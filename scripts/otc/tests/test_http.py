"""PoliteFetcher against a real local HTTP server (scripted responses)."""
from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otc_pipeline.http import HttpError, PoliteFetcher, retry_after_seconds

BODY = bytes(range(256)) * 40  # 10,240 bytes


class Server:
    """Each path has a list of scripted responses, used in order (the last one repeats)."""

    def __init__(self):
        self.script: dict[str, list] = {}
        self.log: list[tuple[str, str, dict]] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _serve(self):
                self.server_version = "test"
                srv.log.append((self.command, self.path, dict(self.headers)))
                steps = srv.script.get(self.path.split("?")[0], [("status", 404, {}, b"")])
                step = steps.pop(0) if len(steps) > 1 else steps[0]
                kind = step[0]
                if kind == "status":
                    _, code, headers, body = step
                    self.send_response(code)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif kind == "truncate":   # promise the whole body, send only n bytes, hang up
                    n, headers, body = step[1], (step[2] if len(step) > 2 else {}), \
                        (step[3] if len(step) > 3 else BODY)
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Accept-Ranges", "bytes")
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(body[:n])
                    self.wfile.flush()
                    self.close_connection = True
                elif kind == "over":       # promise more than the body, send it all, hang up
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(BODY) + 100))
                    self.send_header("ETag", '"v1"')
                    self.end_headers()
                    self.wfile.write(BODY)
                    self.wfile.flush()
                    self.close_connection = True
                elif kind == "badrange":   # answer a Range with 206 but the whole body from 0
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes 0-{len(BODY) - 1}/{len(BODY)}")
                    self.send_header("Content-Length", str(len(BODY)))
                    self.end_headers()
                    self.wfile.write(BODY)
                elif kind == "range":      # honour a Range header (and If-Range against ETag)
                    headers, body = (step[1] if len(step) > 1 else {}), \
                        (step[2] if len(step) > 2 else BODY)
                    rng = self.headers.get("Range")
                    if_range = self.headers.get("If-Range")
                    if rng and if_range is not None and if_range != headers.get("ETag"):
                        rng = None
                    start = int(rng.split("=")[1].rstrip("-")) if rng else 0
                    self.send_response(206 if rng else 200)
                    if rng:
                        self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body) - start))
                    self.end_headers()
                    self.wfile.write(body[start:])

            do_GET = _serve
            do_POST = _serve
            do_PUT = _serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def fetcher(tmp_path, t=None, **kw):
    t = t or FakeTime()
    return PoliteFetcher(tmp_path / "cache", sleep=t.sleep, clock=t.clock, **kw), t


def test_fetches_caches_and_reuses(server, tmp_path):
    server.script["/a"] = [("status", 200, {}, BODY)]
    f, _ = fetcher(tmp_path)
    got = f.get(server.base + "/a", cache_name="a.bin")
    assert got.bytes() == BODY and not got.from_cache
    assert got.sha256 == hashlib.sha256(BODY).hexdigest() and got.size == len(BODY)
    again = f.get(server.base + "/a", cache_name="a.bin")
    assert again.from_cache and again.sha256 == got.sha256
    assert len(server.log) == 1
    assert got.input_file().path == "a.bin"


def test_a_corrupted_cache_is_fetched_again(server, tmp_path):
    server.script["/a"] = [("status", 200, {}, BODY)]
    f, _ = fetcher(tmp_path)
    got = f.get(server.base + "/a", cache_name="a.bin")
    got.path.write_bytes(b"tampered")
    assert not f.get(server.base + "/a", cache_name="a.bin").from_cache
    assert len(server.log) == 2


def test_sends_user_agent_and_encodes_params(server, tmp_path):
    server.script["/q"] = [("status", 200, {}, b"ok")]
    f, _ = fetcher(tmp_path)
    got = f.get(server.base + "/q", params={"time>=": "2026-01-01T00:00:00Z", "x": "a b"})
    assert "time%3E%3D=2026-01-01T00%3A00%3A00Z&x=a%20b" in got.url
    assert server.log[0][2]["User-Agent"].startswith("OpenTideConstants")


def test_retries_honour_retry_after(server, tmp_path):
    server.script["/r"] = [("status", 503, {"Retry-After": "7"}, b""),
                           ("status", 429, {"Retry-After": "3"}, b""),
                           ("status", 200, {}, BODY)]
    f, t = fetcher(tmp_path, min_interval_s=0)
    assert f.get(server.base + "/r").bytes() == BODY
    assert t.slept == [7.0, 3.0]
    assert len(server.log) == 3


def test_backoff_without_retry_after_and_cap(server, tmp_path):
    server.script["/b"] = [("status", 500, {}, b""), ("status", 502, {}, b""),
                           ("status", 503, {"Retry-After": "99999"}, b""), ("status", 200, {}, b"ok")]
    f, t = fetcher(tmp_path, min_interval_s=0, backoff_s=2.0, max_wait_s=60)
    f.get(server.base + "/b")
    assert t.slept == [2.0, 4.0, 60]


def test_gives_up_after_max_retries(server, tmp_path):
    server.script["/down"] = [("status", 503, {}, b"")]
    f, _ = fetcher(tmp_path, min_interval_s=0, max_retries=2)
    with pytest.raises(HttpError) as e:
        f.get(server.base + "/down")
    assert e.value.status == 503 and len(server.log) == 3


def test_client_errors_are_not_retried(server, tmp_path):
    f, _ = fetcher(tmp_path)
    with pytest.raises(HttpError) as e:
        f.get(server.base + "/missing")
    assert e.value.status == 404 and len(server.log) == 1


def test_per_host_rate_limit(server, tmp_path):
    server.script["/a"] = [("status", 200, {}, b"1")]
    f, t = fetcher(tmp_path, min_interval_s=2.5)
    for i in range(3):
        f.get(server.base + "/a", cache_name=f"a{i}")
    assert t.slept == [2.5, 2.5]


def test_host_interval_override(server, tmp_path):
    server.script["/a"] = [("status", 200, {}, b"1")]
    f, t = fetcher(tmp_path, min_interval_s=1, host_intervals={"127.0.0.1": 10})
    f.get(server.base + "/a", cache_name="x")
    f.get(server.base + "/a", cache_name="y")
    assert t.slept == [10]


def test_resumes_a_broken_download_with_range(server, tmp_path):
    server.script["/big"] = [("truncate", 4000, {"ETag": '"v1"'}), ("range", {"ETag": '"v1"'})]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    got = f.get(server.base + "/big", cache_name="big.bin")
    assert got.bytes() == BODY
    assert "Range" not in server.log[0][2]
    assert server.log[1][2]["Range"] == "bytes=4000-"


def test_guard_runs_before_any_request_or_cache(server, tmp_path):
    class Forbidden(Exception):
        pass

    def guard(url):
        if "/forbidden" in url:
            raise Forbidden(url)

    server.script["/forbidden"] = [("status", 200, {}, b"secret")]
    open_f, _ = fetcher(tmp_path)
    assert open_f.get(server.base + "/forbidden", cache_name="f").bytes() == b"secret"
    assert len(server.log) == 1
    f, _ = fetcher(tmp_path, guard=guard)
    with pytest.raises(Forbidden):          # the cached copy is not served either
        f.get(server.base + "/forbidden", cache_name="f")
    assert len(server.log) == 1 and f.requests == 0


def test_cache_name_must_stay_inside_the_cache(tmp_path):
    f, _ = fetcher(tmp_path)
    with pytest.raises(ValueError):
        f.get("http://127.0.0.1:9/x", cache_name="../escape")


def test_post_body_is_part_of_the_cache_key(server, tmp_path):
    server.script["/p"] = [("status", 200, {}, b"one"), ("status", 200, {}, b"two")]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    a = f.get(server.base + "/p", data=b'{"a":1}')
    b = f.get(server.base + "/p", data=b'{"a":2}')
    assert a.path != b.path and server.log[0][0] == "POST"
    assert a.bytes() == b"one" and b.bytes() == b"two" and len(server.log) == 2


def test_retry_after_parsing():
    assert retry_after_seconds("120") == 120.0
    assert retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480.0 - 60) == 60.0
    assert retry_after_seconds("soon") is None
    assert retry_after_seconds(None) is None


# --- integrity (F2 CR) ---------------------------------------------------------------------

import json
import logging
import os
import ssl
import urllib.error
import urllib.request

NEW = bytes(reversed(BODY))


def test_a_resume_records_status_200(server, tmp_path):
    server.script["/big"] = [("truncate", 4000, {"ETag": '"v1"'}), ("range", {"ETag": '"v1"'})]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    got = f.get(server.base + "/big", cache_name="big.bin")
    assert got.bytes() == BODY and got.status == 200
    meta = json.loads((tmp_path / "cache" / "big.bin.meta.json").read_text())
    assert meta["status"] == 200
    assert f.get(server.base + "/big", cache_name="big.bin").status == 200


def test_resume_sends_if_range_with_the_etag(server, tmp_path):
    server.script["/e"] = [("truncate", 4000, {"ETag": '"v1"'}), ("range", {"ETag": '"v1"'})]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    got = f.get(server.base + "/e", cache_name="e.bin")
    assert got.bytes() == BODY
    assert server.log[1][2]["Range"] == "bytes=4000-" and server.log[1][2]["If-Range"] == '"v1"'


def test_resume_sends_if_range_with_last_modified(server, tmp_path):
    lm = "Wed, 21 Oct 2015 07:28:00 GMT"
    server.script["/l"] = [("truncate", 4000, {"Last-Modified": lm}), ("range",)]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    f.get(server.base + "/l", cache_name="l.bin")
    assert server.log[1][2]["If-Range"] == lm


def test_resume_restarts_when_the_upstream_changed(server, tmp_path):
    server.script["/c"] = [("truncate", 4000, {"ETag": '"v1"'}), ("range", {"ETag": '"v2"'}, NEW)]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    got = f.get(server.base + "/c", cache_name="c.bin")
    assert got.bytes() == NEW, "a changed upstream must not be spliced onto the old prefix"


def test_resume_without_a_validator_starts_over(server, tmp_path):
    # no ETag / Last-Modified: nothing pins the body, so a retry downloads it whole
    server.script["/n"] = [("truncate", 4000), ("range", {}, NEW)]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    got = f.get(server.base + "/n", cache_name="n.bin")
    assert got.bytes() == NEW


def test_a_206_with_the_wrong_content_range_is_not_appended(server, tmp_path):
    server.script["/w"] = [("truncate", 4000, {"ETag": '"v1"'}), ("badrange",), ("status", 200, {}, BODY)]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    got = f.get(server.base + "/w", cache_name="w.bin")
    assert got.bytes() == BODY
    assert "Range" not in server.log[2][2]


def test_a_leftover_part_without_its_url_is_not_resumed(server, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "x.bin.part").write_bytes(b"OLDVERSION")
    server.script["/x"] = [("range", {"ETag": '"v1"'})]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    got = f.get(server.base + "/x", cache_name="x.bin")
    assert got.bytes() == BODY and "Range" not in server.log[0][2]


def test_a_leftover_part_for_another_url_is_not_resumed(server, tmp_path):
    server.script["/one"] = [("truncate", 4000, {"ETag": '"v1"'})]
    server.script["/two"] = [("range", {"ETag": '"v1"'}, NEW)]
    f, _ = fetcher(tmp_path, min_interval_s=0, max_retries=0)
    with pytest.raises(HttpError):
        f.get(server.base + "/one", cache_name="same.bin")
    got = f.get(server.base + "/two", cache_name="same.bin")
    assert got.bytes() == NEW and "Range" not in server.log[-1][2]


def test_a_leftover_part_for_the_same_url_is_resumed(server, tmp_path):
    server.script["/s"] = [("truncate", 4000, {"ETag": '"v1"'}), ("range", {"ETag": '"v1"'})]
    f, _ = fetcher(tmp_path, min_interval_s=0, max_retries=0)
    with pytest.raises(HttpError):
        f.get(server.base + "/s", cache_name="s.bin")
    got = f.get(server.base + "/s", cache_name="s.bin")
    assert got.bytes() == BODY and server.log[-1][2]["Range"] == "bytes=4000-"


def test_416_keeps_a_complete_part(server, tmp_path):
    server.script["/full"] = [("over",), ("status", 416, {"Content-Range": f"bytes */{len(BODY)}"}, b"")]
    f, _ = fetcher(tmp_path, min_interval_s=0, max_retries=1)
    got = f.get(server.base + "/full", cache_name="full.bin")
    assert got.bytes() == BODY and got.status == 200
    assert len(server.log) == 2


def test_416_with_a_wrong_total_starts_over(server, tmp_path):
    server.script["/bad"] = [("over",), ("status", 416, {"Content-Range": "bytes */99"}, b""),
                             ("status", 200, {}, NEW)]
    f, _ = fetcher(tmp_path, min_interval_s=0, max_retries=3)
    got = f.get(server.base + "/bad", cache_name="bad.bin")
    assert got.bytes() == NEW and "Range" not in server.log[-1][2]


@pytest.mark.parametrize("name", ["", ".", "./", "a/./b", "a//b", "../x", "a/../b", "/abs", "a/.."])
def test_bad_cache_names_are_rejected(tmp_path, name):
    f, _ = fetcher(tmp_path)
    with pytest.raises(ValueError):
        f.get("http://127.0.0.1:9/x", cache_name=name)
    assert not (tmp_path / "cache.part").exists()


def test_cache_key_includes_method_body_and_headers(server, tmp_path):
    server.script["/k"] = [("status", 200, {}, b"one"), ("status", 200, {}, b"two"),
                           ("status", 200, {}, b"three"), ("status", 200, {}, b"four")]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    assert f.get(server.base + "/k", cache_name="k", data=b"1").bytes() == b"one"
    assert f.get(server.base + "/k", cache_name="k", data=b"2").bytes() == b"two"
    assert f.get(server.base + "/k", cache_name="k", data=b"2", method="PUT").bytes() == b"three"
    assert f.get(server.base + "/k", cache_name="k", data=b"2", method="PUT",
                 headers={"Accept": "text/csv"}).bytes() == b"four"
    assert f.get(server.base + "/k", cache_name="k", data=b"2", method="PUT",
                 headers={"Accept": "text/csv"}).from_cache
    assert len(server.log) == 4


class _BreakingResponse:
    """Wraps a real response; the first read after `after` bytes raises the given error."""

    def __init__(self, resp, after, exc):
        self._resp, self._left, self._exc = resp, after, exc

    def __getattr__(self, k):
        return getattr(self._resp, k)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._resp.close()

    def read(self, n=-1):
        if self._left <= 0:
            raise self._exc
        data = self._resp.read(min(n, self._left))
        self._left -= len(data)
        return data


def test_an_ssl_error_mid_body_is_retried_and_resumed(server, tmp_path, monkeypatch):
    server.script["/tls"] = [("range", {"ETag": '"v1"'})]
    real = urllib.request.urlopen
    calls = []

    def urlopen(req, timeout=None):
        resp = real(req, timeout=timeout)
        calls.append(1)
        if len(calls) == 1:
            return _BreakingResponse(resp, 4096, ssl.SSLError("EOF occurred in violation of protocol"))
        return resp
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    f, _ = fetcher(tmp_path, min_interval_s=0)
    got = f.get(server.base + "/tls", cache_name="tls.bin")
    assert got.bytes() == BODY and server.log[1][2]["Range"] == "bytes=4096-"


def test_error_responses_are_closed(server, tmp_path, monkeypatch):
    server.script["/r"] = [("status", 503, {}, b""), ("status", 200, {}, b"ok")]
    real = urllib.request.urlopen
    errors = []

    def urlopen(req, timeout=None):
        try:
            return real(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            errors.append(e)
            raise
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    f, _ = fetcher(tmp_path, min_interval_s=0)
    f.get(server.base + "/r")
    assert errors and all(e.fp is None or e.fp.closed for e in errors)


def test_meta_is_written_atomically(server, tmp_path, monkeypatch):
    import otc_pipeline.http as h
    server.script["/m"] = [("status", 200, {}, b"one"), ("status", 200, {}, b"two")]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    f.get(server.base + "/m", cache_name="m")
    meta = tmp_path / "cache" / "m.meta.json"
    before = meta.read_text()
    real = os.replace

    def crash(src, dst):
        if str(dst).endswith("m.meta.json"):
            raise OSError("crash before the meta is in place")
        return real(src, dst)
    monkeypatch.setattr(h.os, "replace", crash)
    with pytest.raises(OSError):
        f.get(server.base + "/m", cache_name="m", refresh=True)
    assert meta.read_text() == before


@pytest.mark.parametrize("bad", ["[]", "{", '{"url": "x"}', '"str"'])
def test_a_malformed_meta_is_a_cache_miss(server, tmp_path, caplog, bad):
    server.script["/mm"] = [("status", 200, {}, b"body")]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    f.get(server.base + "/mm", cache_name="mm")
    (tmp_path / "cache" / "mm.meta.json").write_text(bad)
    with caplog.at_level(logging.INFO, logger="otc_pipeline.http"):
        again = f.get(server.base + "/mm", cache_name="mm")
    assert not again.from_cache and again.bytes() == b"body" and len(server.log) == 2
    assert any("meta" in r.getMessage() for r in caplog.records), caplog.text


def test_retries_are_logged(server, tmp_path, caplog):
    server.script["/r"] = [("status", 503, {"Retry-After": "7"}, b""), ("status", 200, {}, b"ok")]
    f, _ = fetcher(tmp_path, min_interval_s=0)
    with caplog.at_level(logging.WARNING, logger="otc_pipeline.http"):
        f.get(server.base + "/r")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("503" in m and "retry" in m.lower() and "7" in m for m in msgs), msgs


def test_errors_keep_the_body_and_the_cause(server, tmp_path):
    server.script["/nf"] = [("status", 400, {}, b"station not found")]
    f, _ = fetcher(tmp_path)
    with pytest.raises(HttpError) as e:
        f.get(server.base + "/nf")
    assert "station not found" in str(e.value)
    assert isinstance(e.value.__cause__, urllib.error.HTTPError)


def test_the_final_retry_error_keeps_the_cause_and_body(server, tmp_path):
    server.script["/down"] = [("status", 503, {}, b"maintenance until 12:00")]
    f, _ = fetcher(tmp_path, min_interval_s=0, max_retries=1)
    with pytest.raises(HttpError) as e:
        f.get(server.base + "/down")
    assert "maintenance until 12:00" in str(e.value) and e.value.__cause__ is not None


def test_a_transport_error_has_no_http_status(server, tmp_path):
    server.script["/t"] = [("truncate", 10)]
    f, _ = fetcher(tmp_path, min_interval_s=0, max_retries=0)
    with pytest.raises(HttpError) as e:
        f.get(server.base + "/t")
    assert e.value.status is None


def test_retry_after_rejects_non_ascii_digits():
    assert retry_after_seconds("²") is None
    assert retry_after_seconds("١٢") is None
