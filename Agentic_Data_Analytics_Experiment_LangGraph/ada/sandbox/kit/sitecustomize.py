"""Loaded automatically by every Python process in the sandbox (via PYTHONPATH).

Defense in depth on top of OS isolation (separate user / container):
  * resource limits (memory, CPU, file size) that the code cannot raise again,
  * no network unless ADA_ALLOW_NETWORK=1,
  * no reads from ADA_BLOCKED_PATHS (the holdout vault),
  * no writes/deletes outside the workspace (and /tmp),
  * no spawning of non-Python programs.
Audit hooks cannot be removed once installed.
"""
import os
import sys

_WS = os.path.realpath(os.environ.get("ADA_WORKSPACE", os.getcwd()))
_BLOCKED = [os.path.realpath(p) for p in os.environ.get("ADA_BLOCKED_PATHS", "").split(os.pathsep) if p]
_ALLOW_NET = os.environ.get("ADA_ALLOW_NETWORK") == "1"
_WRITABLE = [_WS, "/tmp", "/dev/null", "/dev/shm", os.path.realpath("/tmp")]
_PY = os.path.realpath(sys.executable)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

os.umask(0)  # files stay readable/writable for the orchestrator

try:
    import resource

    def _limit(kind, value):
        if value > 0:
            resource.setrlimit(kind, (value, value))

    _limit(resource.RLIMIT_AS, int(os.environ.get("ADA_MEMORY_MB", "0")) * 1024 * 1024)
    _limit(resource.RLIMIT_CPU, int(os.environ.get("ADA_CPU_SECONDS", "0")))
    _limit(resource.RLIMIT_FSIZE, int(os.environ.get("ADA_MAX_FILE_MB", "0")) * 1024 * 1024)
except Exception:  # pragma: no cover - platform without resource module
    pass


def _inside(path, roots):
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


_busy = False


def _real(p):
    if isinstance(p, int):
        return None
    try:
        return os.path.realpath(os.fsdecode(p))
    except Exception:
        return None


def _hook(event, args):
    global _busy
    if _busy:
        return
    _busy = True
    try:
        if event == "open":
            path = _real(args[0])
            if path is None:
                return
            if _inside(path, _BLOCKED):
                raise PermissionError(f"sandbox: access to {path} is not allowed")
            mode, flags = args[1], args[2] if len(args) > 2 else 0
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                      (isinstance(flags, int) and flags & _WRITE_FLAGS)
            if writing and not _inside(path, _WRITABLE):
                raise PermissionError(f"sandbox: writing outside the workspace is not allowed ({path})")
        elif event in ("os.remove", "os.rmdir", "os.rename", "os.chmod", "os.chown", "shutil.rmtree",
                       "os.truncate", "os.link", "os.symlink", "shutil.move"):
            path = _real(args[0])
            if path is not None and not _inside(path, _WRITABLE):
                raise PermissionError(f"sandbox: {event} outside the workspace is not allowed ({path})")
        elif event in ("os.listdir", "os.scandir", "glob.glob"):
            path = _real(args[0]) if args and args[0] is not None else None
            if path is not None and _inside(path, _BLOCKED):
                raise PermissionError("sandbox: listing this directory is not allowed")
        elif event.startswith("socket.") and event in ("socket.connect", "socket.getaddrinfo", "socket.sendto",
                                                       "socket.sendmsg", "socket.bind"):
            if not _ALLOW_NET:
                raise PermissionError("sandbox: network access is disabled")
        elif event == "subprocess.Popen":
            exe = _real(args[0]) if args[0] else None
            if exe != _PY:
                raise PermissionError("sandbox: starting external programs is not allowed")
        elif event in ("os.system", "os.exec", "os.posix_spawn", "os.spawn", "pty.spawn"):
            exe = _real(args[0]) if args and args[0] is not None and not isinstance(args[0], (list, tuple)) else None
            if exe != _PY:
                raise PermissionError("sandbox: starting external programs is not allowed")
    finally:
        _busy = False


sys.addaudithook(_hook)
