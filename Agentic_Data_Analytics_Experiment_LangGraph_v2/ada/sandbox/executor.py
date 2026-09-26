"""Run agent-written Python in isolation.

Modes (config `sandbox.mode`, `auto` picks the first that works):
  docker      `docker run --network none` with only the run workspace mounted
  remote      POST to the sandbox service (docker compose) that shares only the runs volume
  subprocess  local fallback: executed as a separate OS user (`sandbox.run_as_user`) through
              `sudo -n setpriv`, with rlimits and a hard timeout. The vault (0700, owned by the
              backend user) is unreadable for that user; the API key is never in its environment.

In every mode the child gets a scrubbed environment (no secrets), cwd = workspace,
and the `kit/` directory on PYTHONPATH (sitecustomize guard + ada_kit helpers).
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ada.config import PROJECT_ROOT, Config
from ada.paths import vault_root

KIT_DIR = Path(__file__).resolve().parent / "kit"
_PASSTHROUGH_ENV = ("LANG", "LC_ALL", "TZ")


class SandboxUnavailable(RuntimeError):
    pass


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool
    mode: str
    changed_files: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _can_switch_user(name: str) -> bool:
    if not name or not _user_exists(name) or not shutil.which("sudo") or not shutil.which("setpriv"):
        return False
    probe = subprocess.run(["sudo", "-n", "setpriv", f"--reuid={name}", f"--regid={name}", "--clear-groups",
                            "--", "true"], capture_output=True, timeout=20)
    return probe.returncode == 0


def _docker_ready(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=20)
    return probe.returncode == 0


class Sandbox:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.timeout = int(cfg.get("sandbox.timeout_seconds", 300))
        self.user = cfg.get("sandbox.run_as_user")
        self.mode = self._pick_mode(str(cfg.get("sandbox.mode", "auto")))

    def _pick_mode(self, wanted: str) -> str:
        if wanted in ("docker", "remote"):
            return wanted
        if wanted == "auto" and _docker_ready(self.cfg.get("sandbox.docker_image", "ada-sandbox:latest")):
            return "docker"
        if wanted == "auto" and os.environ.get("ADA_SANDBOX_URL"):
            return "remote"
        if _can_switch_user(self.user):
            return "subprocess-user"
        if self.cfg.get("sandbox.require_isolation", True):
            raise SandboxUnavailable(
                "No isolated sandbox available: need docker, the sandbox service, or the OS user "
                f"{self.user!r} with passwordless `sudo setpriv` (see README). "
                "Set sandbox.require_isolation: false only for throwaway experiments.")
        return "subprocess-same-user"

    @property
    def isolated(self) -> bool:
        return self.mode != "subprocess-same-user"

    # ------------------------------------------------------------------
    def _env(self, workspace: Path, allow_network: bool) -> dict[str, str]:
        env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ}
        home = workspace / ".home"
        env.update({
            "PATH": f"{Path(sys.executable).parent}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(home),
            "MPLCONFIGDIR": str(home / "mpl"),
            "MPLBACKEND": "Agg",
            "PYTHONPATH": str(KIT_DIR),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "OMP_NUM_THREADS": "2",
            "ADA_WORKSPACE": str(workspace),
            "ADA_BLOCKED_PATHS": os.pathsep.join([str(vault_root()), str(PROJECT_ROOT / ".env"),
                                                  str(PROJECT_ROOT.parent / ".env")]),
            "ADA_ALLOW_NETWORK": "1" if allow_network else "0",
            "ADA_MEMORY_MB": str(self.cfg.get("sandbox.memory_mb", 4096)),
            "ADA_CPU_SECONDS": str(self.cfg.get("sandbox.cpu_seconds", 600)),
            "ADA_MAX_FILE_MB": str(self.cfg.get("sandbox.max_file_mb", 1024)),
        })
        return env

    def run_script(self, workspace: Path, script: Path, *, timeout: int | None = None,
                   allow_network: bool = False, args: list[str] | None = None) -> ExecResult:
        """Execute `script` (inside the workspace, or a trusted harness file) with cwd=workspace."""
        workspace = Path(workspace).resolve()
        timeout = int(timeout or self.timeout)
        (workspace / ".home").mkdir(exist_ok=True)
        os.chmod(workspace / ".home", 0o777)
        before = _mtimes(workspace)
        start = time.monotonic()
        if self.mode == "docker":
            result = self._run_docker(workspace, script, timeout, allow_network, args or [])
        elif self.mode == "remote":
            result = self._run_remote(workspace, script, timeout, allow_network, args or [])
        else:
            result = self._run_local(workspace, script, timeout, allow_network, args or [])
        result.duration = time.monotonic() - start
        after = _mtimes(workspace)
        result.changed_files = sorted(p for p, m in after.items() if before.get(p) != m)
        return result

    def _run_local(self, workspace: Path, script: Path, timeout: int, allow_network: bool,
                   args: list[str]) -> ExecResult:
        env = self._env(workspace, allow_network)
        inner = ["timeout", "-s", "KILL", str(timeout), "env", "-i",
                 *[f"{k}={v}" for k, v in env.items()], sys.executable, str(script), *args]
        if self.mode == "subprocess-user":
            cmd = ["sudo", "-n", "setpriv", f"--reuid={self.user}", f"--regid={self.user}", "--clear-groups",
                   "--", *inner]
        else:
            cmd = inner
        try:
            proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, errors="replace",
                                  timeout=timeout + 30, stdin=subprocess.DEVNULL)
            timed_out = proc.returncode in (124, 137, -9)
            return ExecResult(proc.returncode, proc.stdout, proc.stderr, 0.0,
                              timed_out and proc.returncode != 0, self.mode)
        except subprocess.TimeoutExpired as exc:
            return ExecResult(-9, _s(exc.stdout), _s(exc.stderr) + "\n[killed: timeout]", 0.0, True, self.mode)

    def _run_docker(self, workspace: Path, script: Path, timeout: int, allow_network: bool,
                    args: list[str]) -> ExecResult:
        env = self._env(Path("/work"), allow_network)
        env["PYTHONPATH"] = "/kit"
        env["ADA_BLOCKED_PATHS"] = ""
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
        script_in = f"/work/{script.relative_to(workspace)}" if workspace in script.parents else f"/harness/{script.name}"
        cmd = ["docker", "run", "--rm", "--network", "bridge" if allow_network else "none",
               "--memory", f"{self.cfg.get('sandbox.memory_mb', 4096)}m", "--cpus", "2", "--pids-limit", "256",
               "-v", f"{workspace}:/work", "-v", f"{KIT_DIR}:/kit:ro",
               "-v", f"{PROJECT_ROOT / 'ada' / 'evaluation'}:/harness:ro", "-w", "/work",
               *sum((["-e", f"{k}={v}"] for k, v in env.items()), []),
               self.cfg.get("sandbox.docker_image", "ada-sandbox:latest"),
               "timeout", "-s", "KILL", str(timeout), "python", script_in, *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout + 60)
            return ExecResult(proc.returncode, proc.stdout, proc.stderr, 0.0, proc.returncode in (124, 137), "docker")
        except subprocess.TimeoutExpired as exc:
            return ExecResult(-9, _s(exc.stdout), _s(exc.stderr), 0.0, True, "docker")

    def _run_remote(self, workspace: Path, script: Path, timeout: int, allow_network: bool,
                    args: list[str]) -> ExecResult:
        url = os.environ.get("ADA_SANDBOX_URL") or self.cfg.get("sandbox.remote_url")
        body = {"run_dir": workspace.name,
                "script": str(script.relative_to(workspace)) if workspace in script.parents else f"harness:{script.name}",
                "timeout": timeout, "args": args}
        try:
            resp = httpx.post(f"{url}/run", json=body, timeout=timeout + 60)
            resp.raise_for_status()
            data = resp.json()
            return ExecResult(data["returncode"], data["stdout"], data["stderr"], 0.0, data["timed_out"], "remote")
        except httpx.HTTPError as exc:
            return ExecResult(-1, "", f"sandbox service error: {exc}", 0.0, False, "remote")


def _mtimes(root: Path) -> dict[str, float]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                out[str(p.relative_to(root))] = p.stat().st_mtime
            except OSError:
                pass
    return out


def _s(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
