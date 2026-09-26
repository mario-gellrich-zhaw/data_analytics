"""Web tools for the DataCollectorAgent (the only agent with network access).

Rules enforced here: robots.txt is honoured, requests are rate limited per
domain, responses that demand a login (401/403/paywall redirects) are not worked
around, and every downloaded file gets a provenance record in raw/sources.json.
"""
from __future__ import annotations

import io
import re
import threading
import zipfile
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field

from agents.tools.registry import ToolContext, tool
from ada.store import sha256_file

_robots_cache: dict[str, RobotFileParser | None] = {}
_last_request: dict[str, float] = {}
_lock = threading.Lock()
LOGIN_HINTS = ("login", "signin", "sign-in", "auth", "account/", "paywall", "subscribe")


def _ua(tc: ToolContext) -> str:
    return tc.ctx.cfg.get("web.user_agent", "ADA-research-bot/0.1")


def _robots_allowed(tc: ToolContext, url: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    with _lock:
        cached = _robots_cache.get(base, "missing")
    if cached == "missing":
        parser: RobotFileParser | None = RobotFileParser()
        try:
            resp = httpx.get(f"{base}/robots.txt", headers={"User-Agent": _ua(tc)}, timeout=15, follow_redirects=True)
            if resp.status_code >= 400:
                parser = None          # no robots.txt -> allowed
            else:
                parser.parse(resp.text.splitlines())
        except httpx.HTTPError:
            parser = None
        with _lock:
            _robots_cache[base] = parser
        cached = parser
    return True if cached is None else cached.can_fetch(_ua(tc), url)


def _throttle(tc: ToolContext, url: str) -> None:
    host = urlparse(url).netloc
    delay = float(tc.ctx.cfg.get("web.request_delay_seconds", 1.0))
    with _lock:
        wait = _last_request.get(host, 0) + delay - time.monotonic()
        _last_request[host] = time.monotonic() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


def _get(tc: ToolContext, url: str, *, stream: bool = False) -> httpx.Response:
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs are allowed")
    if not _robots_allowed(tc, url):
        raise PermissionError(f"robots.txt disallows fetching {url}")
    _throttle(tc, url)
    client = httpx.Client(headers={"User-Agent": _ua(tc)}, timeout=httpx.Timeout(60, connect=15),
                          follow_redirects=True)
    req = client.build_request("GET", url)
    resp = client.send(req, stream=stream)
    final = str(resp.url).lower()
    if resp.status_code in (401, 402, 403) or any(h in urlparse(final).path for h in LOGIN_HINTS) and final != url.lower():
        resp.close()
        raise PermissionError(f"{url} requires authentication or is blocked (HTTP {resp.status_code}); "
                              "do not try to bypass it — pick another source")
    resp.raise_for_status()
    return resp


class SearchArgs(BaseModel):
    query: str = Field(description="what to look for, e.g. 'open data rental listings Zurich csv download license'")


@tool("web_search", "Search the web. Returns an answer with cited source URLs.", SearchArgs, network=True)
def web_search(tc: ToolContext, a: SearchArgs) -> Any:
    return tc.ctx.llm.web_search(a.query, agent=tc.agent, node=tc.node)


class FetchArgs(BaseModel):
    url: str
    max_chars: int = Field(6000, ge=500, le=30000)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    links = re.findall(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    link_lines = [f"- {re.sub(r'<[^>]+>', '', t).strip()[:80]} -> {h}" for h, t in links
                  if h.startswith("http") or h.startswith("/")][:120]
    return text.strip() + "\n\nLINKS:\n" + "\n".join(link_lines)


@tool("fetch_url", "Fetch a web page or small API response as text (respects robots.txt). Use it to read "
      "dataset landing pages, licenses and API docs. For data files use download_file.", FetchArgs, network=True)
def fetch_url(tc: ToolContext, a: FetchArgs) -> Any:
    resp = _get(tc, a.url)
    ctype = resp.headers.get("content-type", "")
    body = resp.text
    text = _html_to_text(body) if "html" in ctype else body
    return {"url": str(resp.url), "status": resp.status_code, "content_type": ctype, "text": text[:a.max_chars],
            "truncated": len(text) > a.max_chars}


class DownloadArgs(BaseModel):
    url: str
    filename: str = Field(description="target file name inside raw/, e.g. listings_2024.csv")
    source_name: str = Field(description="publisher / dataset name")
    license: str = Field(description="license as stated by the publisher (e.g. CC BY 4.0, ODbL); 'unknown' if not found")
    license_url: str = ""
    description: str = ""


def _record_source(tc: ToolContext, rel: str, meta: dict[str, Any]) -> None:
    store = tc.ctx.store
    sources = store.read_json("raw/sources.json", default=[]) or []
    sources = [s for s in sources if s.get("path") != rel]
    sources.append({"path": rel, **meta, "sha256": sha256_file(store.resolve(rel))})
    store.write_json("raw/sources.json", sources)


@tool("download_file", "Download a data file (CSV, JSON, XLSX, ZIP, GeoJSON, API response) into raw/ and record "
      "its provenance (URL, license, retrieval time, sha256).", DownloadArgs, network=True)
def download_file(tc: ToolContext, a: DownloadArgs) -> Any:
    store = tc.ctx.store
    existing = store.read_json("raw/sources.json", default=[]) or []
    if len(existing) >= int(tc.ctx.cfg.get("web.max_downloads_per_run", 60)):
        return "refused: download limit for this run reached"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", a.filename).strip("._") or "download.bin"
    rel = f"raw/{name}"
    limit = int(tc.ctx.cfg.get("web.max_download_mb", 300)) * 1024 * 1024
    resp = _get(tc, a.url, stream=True)
    size = 0
    path = store.resolve(rel)
    store.ensure_dir("raw")
    try:
        with open(path, "wb") as fh:
            for chunk in resp.iter_bytes(1 << 16):
                size += len(chunk)
                if size > limit:
                    hint = (" — it looks like a ZIP archive: use list_remote_zip + download_zip_member to fetch only "
                            "the data files inside it") if a.url.lower().split("?")[0].endswith(".zip") else ""
                    raise ValueError(f"file exceeds {limit // (1024 * 1024)} MB limit{hint}")
                fh.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        resp.close()
    path.chmod(0o666)
    _record_source(tc, rel, {
        "url": a.url, "final_url": str(resp.url), "source_name": a.source_name, "license": a.license,
        "license_url": a.license_url, "description": a.description, "bytes": size,
        "content_type": resp.headers.get("content-type", ""),
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    tc.ctx.emit("artifact", f"downloaded {rel} ({size / 1024:.0f} KB) from {urlparse(a.url).netloc}",
                node=tc.node, agent=tc.agent, payload={"path": rel, "url": a.url, "license": a.license})
    return {"saved": rel, "bytes": size, "content_type": resp.headers.get("content-type", "")}


# ---------------------------------------------------------------------------- remote ZIP archives
class _RangeReader(io.RawIOBase):
    """Seekable read-only view of a remote file via HTTP range requests (zip central directory is at the end)."""

    def __init__(self, tc: ToolContext, url: str):
        self.client = httpx.Client(headers={"User-Agent": _ua(tc)}, timeout=httpx.Timeout(120, connect=15),
                                   follow_redirects=True)
        head = self.client.head(url)
        head.raise_for_status()
        if head.headers.get("accept-ranges") != "bytes":
            raise ValueError("server does not support range requests — download the whole file instead")
        self.url, self.size, self.pos = str(head.url), int(head.headers["content-length"]), 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def readinto(self, buf: Any) -> int:
        if self.pos >= self.size:
            return 0
        end = min(self.size, self.pos + len(buf)) - 1
        data = self.client.get(self.url, headers={"Range": f"bytes={self.pos}-{end}"}).content
        buf[:len(data)] = data
        self.pos += len(data)
        return len(data)


def _open_remote_zip(tc: ToolContext, url: str) -> zipfile.ZipFile:
    if not _robots_allowed(tc, url):
        raise PermissionError(f"robots.txt disallows fetching {url}")
    return zipfile.ZipFile(io.BufferedReader(_RangeReader(tc, url), buffer_size=1 << 20))


class ZipListArgs(BaseModel):
    url: str = Field(description="URL of a .zip archive (e.g. a GitHub release asset or Zenodo file)")
    pattern: str = Field("", description="optional substring filter on member names, e.g. '.csv'")


@tool("list_remote_zip", "List the files inside a remote ZIP archive without downloading it (works for archives "
      "larger than the download limit). Media files are summarised, data files listed with sizes.", ZipListArgs,
      network=True)
def list_remote_zip(tc: ToolContext, a: ZipListArgs) -> Any:
    with _open_remote_zip(tc, a.url) as z:
        infos = [i for i in z.infolist() if not i.is_dir() and a.pattern.lower() in i.filename.lower()]
    media = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".npy")
    data = [{"member": i.filename, "mb": round(i.file_size / 1e6, 2)} for i in infos if not i.filename.lower().endswith(media)]
    n_media = sum(1 for i in infos if i.filename.lower().endswith(media))
    return {"members": data[:200], "data_files": len(data), "media_files_not_listed": n_media}


class ZipMemberArgs(BaseModel):
    url: str
    member: str = Field(description="exact member path from list_remote_zip")
    filename: str = Field(description="target file name inside raw/")
    source_name: str
    license: str = Field(description="license as stated by the publisher; 'unknown' if not found")
    license_url: str = ""
    description: str = ""


@tool("download_zip_member", "Extract one file from a remote ZIP archive into raw/ (only that file is transferred) "
      "and record its provenance.", ZipMemberArgs, network=True)
def download_zip_member(tc: ToolContext, a: ZipMemberArgs) -> Any:
    store = tc.ctx.store
    limit = int(tc.ctx.cfg.get("web.max_download_mb", 300)) * 1024 * 1024
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", a.filename).strip("._") or "member.bin"
    rel = f"raw/{name}"
    with _open_remote_zip(tc, a.url) as z:
        info = z.getinfo(a.member)
        if info.file_size > limit:
            raise ValueError(f"member is {info.file_size / 1e6:.0f} MB, above the {limit // (1024 * 1024)} MB limit")
        store.ensure_dir("raw")
        path = store.resolve(rel)
        with z.open(info) as src, open(path, "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    path.chmod(0o666)
    _record_source(tc, rel, {
        "url": f"{a.url}#{a.member}", "archive_url": a.url, "archive_member": a.member, "source_name": a.source_name,
        "license": a.license, "license_url": a.license_url, "description": a.description, "bytes": info.file_size,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    tc.ctx.emit("artifact", f"extracted {a.member} -> {rel} ({info.file_size / 1024:.0f} KB)", node=tc.node,
                agent=tc.agent, payload={"path": rel, "url": a.url, "license": a.license})
    return {"saved": rel, "bytes": info.file_size}
