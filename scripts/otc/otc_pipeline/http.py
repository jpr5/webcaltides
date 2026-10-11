"""Polite HTTP fetch for adapters: cache, retries, Retry-After, per-host rate limit, resume.

    fetcher = PoliteFetcher(cache_dir, min_interval_s=1.0)
    got = fetcher.get("https://host/path", params={"a": "b"}, cache_name="stations.json")
    got.path, got.sha256, got.input_file()   # InputFile for FetchResult / MANIFEST.json

- Cache: the body is stored at `cache_dir/<cache_name>` with `<cache_name>.meta.json`
  (url, request fingerprint, status, sha256, size, fetched_at), written atomically. A later
  `get` of the same name returns the cached file with no request only when the URL, method,
  request body and caller headers all match, unless `refresh=True` or the file no longer
  matches its SHA-256. A malformed meta file is a cache miss (logged).
- Retries: 408, 429, 500, 502, 503, 504 and network errors, up to `max_retries`; the wait is
  the server's Retry-After (seconds or HTTP date) when given, else exponential backoff; every
  wait is capped at `max_wait_s`. Any other 4xx raises HttpError at once.
- Rate limit: at least `min_interval_s` between two requests to the same host (per fetcher;
  `host_intervals` overrides it per host).
- Resume: a GET that breaks off keeps its `.part` file and a `.part.json` sidecar (request
  fingerprint, ETag, Last-Modified). A retry, or a later `get`, resumes only when the sidecar
  matches this request and names a validator: it sends Range plus If-Range and appends only a
  206 whose Content-Range starts at the bytes it has. Anything else (a 200, a changed
  upstream, a wrong Content-Range, an unknown `.part`) starts over. A 416 whose
  Content-Range total equals the `.part` size completes the `.part`. A finished resume is
  recorded as status 200.
- Guard: `guard(url)` runs before the cache and before any request; an adapter raises from it
  to forbid a URL (SHOM REFMAR does).
"""
from __future__ import annotations

import email.utils
import hashlib
import http.client
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from otc_pipeline.adapter import InputFile, is_relative_inside, sha256_file

__all__ = ["Fetched", "HttpError", "PoliteFetcher", "DEFAULT_USER_AGENT"]

DEFAULT_USER_AGENT = "OpenTideConstants-pipeline/0.1 (+https://opentideconstants.org)"
RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
ERROR_BODY_BYTES = 2048
CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)")
UNSATISFIED_RANGE_RE = re.compile(r"bytes\s+\*/(\d+)")

log = logging.getLogger(__name__)


class HttpError(Exception):
    def __init__(self, url: str, status: int | None, message: str):
        self.url, self.status, self.reason = url, status, message
        super().__init__(f"{url}: {message}")


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    path: Path
    name: str          # path relative to the cache directory
    sha256: str
    size: int
    fetched_at: str
    from_cache: bool

    def input_file(self) -> InputFile:
        return InputFile(path=self.name, sha256=self.sha256, size=self.size,
                         source_url=self.url, fetched_at=self.fetched_at)

    def bytes(self) -> bytes:
        return self.path.read_bytes()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_sha256_file = sha256_file


def retry_after_seconds(value: str | None, now: float | None = None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP date); None if absent or bad."""
    if not value:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


class _Restart(Exception):
    """The `.part` cannot be resumed; drop it and fetch the whole body."""


def _write_json_atomic(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(obj, sort_keys=True, indent=1) + "\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _error_text(e: urllib.error.HTTPError) -> str:
    try:
        body = e.read(ERROR_BODY_BYTES) if e.fp is not None else b""
    except OSError:
        body = b""
    finally:
        e.close()
    text = body.decode("utf-8", "replace").strip()
    return f"HTTP {e.code} {e.reason}" + (f": {text}" if text else "")


def _validator(etag: str | None, last_modified: str | None) -> str | None:
    """The If-Range value: a strong ETag, else Last-Modified (RFC 9110 §13.1.5)."""
    if etag and not etag.startswith("W/"):
        return etag
    return last_modified or None


class PoliteFetcher:
    def __init__(self, cache_dir: Path, *, user_agent: str = DEFAULT_USER_AGENT,
                 min_interval_s: float = 1.0, host_intervals: Mapping[str, float] | None = None,
                 max_retries: int = 5, backoff_s: float = 2.0, max_wait_s: float = 300.0,
                 timeout_s: float = 60.0, guard: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cache_dir = Path(cache_dir)
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.host_intervals = dict(host_intervals or {})
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.max_wait_s = max_wait_s
        self.timeout_s = timeout_s
        self.guard = guard
        self.sleep = sleep
        self.clock = clock
        self._last: dict[str, float] = {}
        self.requests = 0   # requests sent, for tests and logs

    # -- helpers --------------------------------------------------------------------------
    @staticmethod
    def build_url(url: str, params: Mapping[str, str] | None) -> str:
        if not params:
            return url
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="")
        return f"{url}{'&' if '?' in url else '?'}{query}"

    def _wait_for_host(self, host: str) -> None:
        interval = self.host_intervals.get(host, self.min_interval_s)
        last = self._last.get(host)
        if last is not None:
            gap = self.clock() - last
            if gap < interval:
                self.sleep(interval - gap)
        self._last[host] = self.clock()

    def _cache_path(self, name: str) -> Path:
        if not is_relative_inside(name):
            raise ValueError(f"cache_name {name!r} must be a relative path inside the cache "
                             "(no empty, '.' or '..' segments)")
        return self.cache_dir / Path(*name.split("/"))

    @staticmethod
    def _fingerprint(url: str, method: str, data: bytes | None,
                     headers: Mapping[str, str] | None) -> dict:
        return {"url": url, "method": method,
                "body_sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                "headers": sorted([k.lower(), str(v)] for k, v in (headers or {}).items())}

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            obj = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            log.info("cache meta %s is unreadable (%s); fetching again", path, e)
            return None
        if not isinstance(obj, dict):
            log.info("cache meta %s is not a JSON object; fetching again", path)
            return None
        return obj

    def _cached(self, fp: dict, name: str, path: Path) -> Fetched | None:
        meta_path = path.with_name(path.name + ".meta.json")
        if not (path.is_file() and meta_path.is_file()):
            return None
        meta = self._read_json(meta_path)
        if meta is None:
            return None
        status, fetched_at, sha = meta.get("status"), meta.get("fetched_at"), meta.get("sha256")
        if not (isinstance(status, int) and isinstance(fetched_at, str) and isinstance(sha, str)):
            log.info("cache meta %s lacks status/fetched_at/sha256; fetching again", meta_path)
            return None
        if meta.get("request", {"url": meta.get("url")}) != fp or sha != _sha256_file(path):
            return None
        return Fetched(url=fp["url"], status=status, path=path, name=name, sha256=sha,
                       size=path.stat().st_size, fetched_at=fetched_at, from_cache=True)

    def _resume_point(self, part: Path, side: Path, fp: dict) -> tuple[int, str | None]:
        """(bytes held, If-Range value) for a `.part` that belongs to this request; else (0, None)
        after dropping it."""
        if not part.exists():
            side.unlink(missing_ok=True)
            return 0, None
        info = self._read_json(side) if side.exists() else None
        validator = info.get("validator") if info is not None and info.get("request") == fp else None
        if not isinstance(validator, str) or not validator or not part.stat().st_size:
            part.unlink()
            side.unlink(missing_ok=True)
            return 0, None
        return part.stat().st_size, validator

    # -- the fetch ------------------------------------------------------------------------
    def get(self, url: str, *, params: Mapping[str, str] | None = None, cache_name: str | None = None,
            refresh: bool = False, headers: Mapping[str, str] | None = None,
            data: bytes | None = None, method: str | None = None) -> Fetched:
        full = self.build_url(url, params)
        if self.guard is not None:
            self.guard(full)
        method = (method or ("POST" if data is not None else "GET")).upper()
        fp = self._fingerprint(full, method, data, headers)
        if cache_name is None:
            key = hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:32]
            cache_name = f"http/{key}"
        path = self._cache_path(cache_name)
        if not refresh:
            hit = self._cached(fp, cache_name, path)
            if hit is not None:
                return hit
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        side = path.with_name(path.name + ".part.json")
        if method != "GET":
            part.unlink(missing_ok=True)
            side.unlink(missing_ok=True)
        host = urllib.parse.urlsplit(full).hostname or ""
        attempt = 0
        last_error: HttpError | None = None
        last_cause: BaseException | None = None
        while True:
            have, if_range = self._resume_point(part, side, fp) if method == "GET" else (0, None)
            req = urllib.request.Request(full, data=data, method=method)
            req.add_header("User-Agent", self.user_agent)
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            if have:
                req.add_header("Range", f"bytes={have}-")
                req.add_header("If-Range", if_range)
            self._wait_for_host(host)
            self.requests += 1
            wait: float | None = None
            status = None
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    status = resp.status
                    append = have > 0 and status == 206
                    if append:
                        m = CONTENT_RANGE_RE.fullmatch((resp.headers.get("Content-Range") or "").strip())
                        if m is None or int(m.group(1)) != have:
                            raise _Restart(f"206 Content-Range {resp.headers.get('Content-Range')!r} "
                                           f"does not start at byte {have}")
                    else:
                        if method == "GET":
                            v = _validator(resp.headers.get("ETag"), resp.headers.get("Last-Modified"))
                            _write_json_atomic(side, {"request": fp, "validator": v})
                    expected = resp.headers.get("Content-Length")
                    with part.open("ab" if append else "wb") as out:
                        while chunk := resp.read(1 << 16):
                            out.write(chunk)
                    got = part.stat().st_size - (have if append else 0)
                    if expected is not None and expected.isascii() and expected.isdigit() \
                            and got < int(expected):
                        raise http.client.IncompleteRead(b"", int(expected) - got)
                status = 200 if append else status
                break
            except urllib.error.HTTPError as e:
                status = e.code
                text = _error_text(e)
                if e.code == 416 and have:
                    m = UNSATISFIED_RANGE_RE.fullmatch((e.headers.get("Content-Range") or "").strip()
                                                       if e.headers else "")
                    if m is not None and int(m.group(1)) == have:
                        log.info("%s: 416 for bytes=%d-; the .part already holds all %d bytes",
                                 full, have, have)
                        status = 200
                        break
                    part.unlink(missing_ok=True)
                    side.unlink(missing_ok=True)
                elif e.code not in RETRY_STATUS:
                    raise HttpError(full, e.code, text) from e
                wait = retry_after_seconds(e.headers.get("Retry-After") if e.headers else None)
                last_error, last_cause = HttpError(full, e.code, text), e
            except _Restart as e:
                part.unlink(missing_ok=True)
                side.unlink(missing_ok=True)
                last_error, last_cause = HttpError(full, None, str(e)), e
            except (http.client.HTTPException, OSError) as e:
                # OSError covers URLError, ConnectionError, TimeoutError and ssl.SSLError (a TLS
                # reset in the middle of the body); the .part is kept for a resume.
                last_error, last_cause = HttpError(full, None, f"{type(e).__name__}: {e}"), e
            attempt += 1
            if attempt > self.max_retries:
                raise last_error from last_cause
            if wait is None:
                wait = self.backoff_s * (2 ** (attempt - 1))
            wait = min(wait, self.max_wait_s)
            log.warning("%s: %s; retry %d/%d in %.1f s", full, last_error.reason, attempt,
                        self.max_retries, wait)
            self.sleep(wait)
        os.replace(part, path)
        side.unlink(missing_ok=True)
        sha = _sha256_file(path)
        fetched_at = _now()
        meta = {"url": full, "request": fp, "status": status, "sha256": sha,
                "size": path.stat().st_size, "fetched_at": fetched_at}
        _write_json_atomic(path.with_name(path.name + ".meta.json"), meta)
        return Fetched(url=full, status=status, path=path, name=cache_name, sha256=sha,
                       size=meta["size"], fetched_at=fetched_at, from_cache=False)
