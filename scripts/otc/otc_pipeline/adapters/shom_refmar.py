"""SHOM REFMAR tide-gauge observations (slot A-SHOM-a): fetch, convert to GaugeRecord, guard.

Source: the keyless REFMAR observation service on data.shom.fr (Etalab Licence Ouverte 2.0).

- Gauge list: GET /maregraphie/service/tidegauges (shom_id, name, lat/lon, `state`, `reseau`:
  RONIM = gauges SHOM operates, REFMAR = partner gauges).
- Observations: GET /maregraphie/observation/json/{shom_id}?sources=4&dtStart=...&dtEnd=...
  (sources=4: hourly series). The service serves at most 31 days per request and silently
  truncates a longer one, so the fetch asks for windows of at most 31 days, each ending one
  second before the next starts (dtEnd is inclusive), and the converter fails on any stamp
  outside the window it asked for.
- Raw responses are cached as served under `obs/<shom_id>/<dtStart>_<dtEnd>.json` plus
  `tidegauges.json`, with a MANIFEST.json (manifest §0.3). The cache directory is the local
  copy of `cache/shom-refmar/` in the work bucket; it is never committed.
- Stamps are kept as served (read as UTC wall-clock); `declared_time_base = "utc_instant"` is a
  declaration the time-base audit checks in A-SHOM-b, not a fact this adapter trusts.

GUARD. SHOM's tidal constants and predictions are paid-licence (Document RIP 2026, "Licence pour
les predictions de marees") and must never be fetched, cached, compared with or shipped. Every
URL this adapter's fetcher is asked for, and every redirect target while it fetches, goes
through `guard_url` first. Only https on the REFMAR service host, with exactly the two paths
above and their known query keys, passes; anything else (prediction, harmonic-constant, hdm,
WMS/WFS tide products, the diffusion site, other hosts) raises ForbiddenSourceError before a
request leaves the process and before the cache is touched.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult, sha256_file
from otc_pipeline.http import PoliteFetcher

__all__ = ["ForbiddenSourceError", "GuardedRedirectHandler", "ShomRefmarAdapter", "guard_url"]

log = logging.getLogger(__name__)

UTC = timezone.utc
SHOM_HOST = "services.data.shom.fr"      # no scheme here: the URL scan in the guard test reads this file
GAUGES_PATH = "/maregraphie/service/tidegauges"
OBS_PATH_RE = re.compile(r"/maregraphie/observation/json/(?P<sid>[0-9]{1,6})")
STAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
OBS_QUERY_KEYS = frozenset({"sources", "dtStart", "dtEnd"})
# REFMAR source id -> (GaugeRecord sampling, interval_min). Only the hourly series is used.
SOURCE_IDS = {4: ("instantaneous", 60)}
SOURCE_ID = 4
MAX_WINDOW = timedelta(days=31)          # the service limit per request
SERVED_STAMP = "%Y/%m/%d %H:%M:%S"
QUERY_STAMP = "%Y-%m-%dT%H:%M:%SZ"

SPDX = "etalab-2.0"
CITATION = {"RONIM": "REFMAR. http://dx.doi.org/10.17183/REFMAR#RONIM",
            "PARTENAIRES": "REFMAR. http://dx.doi.org/10.17183/REFMAR#PARTENAIRES"}
DATASET = "SHOM REFMAR tide gauge observations"


class ForbiddenSourceError(RuntimeError):
    """A URL the SHOM adapter must never request (paid-licence SHOM products, or off-allowlist)."""


def _forbid(url: str, why: str) -> None:
    raise ForbiddenSourceError(
        f"refusing {url!r}: {why}. The SHOM adapter may request only the REFMAR gauge list and "
        "hourly observations; SHOM constants and predictions are paid-licence and are never fetched.")


def guard_url(url: str) -> None:
    """Return if `url` is on the REFMAR allowlist; raise ForbiddenSourceError otherwise."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as e:
        _forbid(url, f"unparseable URL ({e})")
    if parts.scheme != "https":
        _forbid(url, "not https")
    if parts.netloc != SHOM_HOST:
        _forbid(url, f"host is not exactly {SHOM_HOST}")
    if parts.fragment:
        _forbid(url, "has a fragment")
    if parts.path == GAUGES_PATH:
        if parts.query:
            _forbid(url, "the gauge list takes no query")
        return
    if not OBS_PATH_RE.fullmatch(parts.path):
        _forbid(url, "path is not on the REFMAR allowlist")
    try:
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        _forbid(url, "malformed query")
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)) or not set(keys) <= OBS_QUERY_KEYS or "sources" not in keys:
        _forbid(url, f"observation query keys {sorted(keys)} are not {sorted(OBS_QUERY_KEYS)}")
    q = dict(pairs)
    if q["sources"] not in {str(s) for s in SOURCE_IDS}:
        _forbid(url, f"sources={q['sources']!r} is not an allowlisted observation source")
    for k in ("dtStart", "dtEnd"):
        if k in q and not STAMP_RE.fullmatch(q[k]):
            _forbid(url, f"{k}={q[k]!r} is not YYYY-MM-DDTHH:MM:SSZ")


class GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Runs the guard on a redirect target before urllib follows it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        guard_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@contextmanager
def _guarded_redirects() -> Iterator[None]:
    """PoliteFetcher calls urllib.request.urlopen, which uses the installed opener; install one
    that guards redirects for the duration of a fetch and put the previous one back."""
    previous = urllib.request._opener
    urllib.request.install_opener(urllib.request.build_opener(GuardedRedirectHandler()))
    try:
        yield
    finally:
        urllib.request.install_opener(previous)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"{dt!r} must be timezone-aware (UTC)")
    return dt.astimezone(UTC).replace(microsecond=0)


def windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """[start, end) as inclusive (dtStart, dtEnd) windows of at most 31 days, dtEnd one second
    before the next dtStart."""
    start, end = _utc(start), _utc(end)
    if not start < end:
        raise ValueError(f"start {start} must be before end {end}")
    out = []
    t = start
    while t < end:
        nxt = min(t + MAX_WINDOW, end)
        out.append((t, nxt - timedelta(seconds=1)))
        t = nxt
    return out


def _compact(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _served_seconds(stamp: str) -> int:
    return int(datetime.strptime(stamp, SERVED_STAMP).replace(tzinfo=UTC).timestamp())


class ShomRefmarAdapter:
    source = "shom-refmar"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "1.0.0"
    contract_sample = ("3", "13", "410")   # Brest, Cherbourg, Saint-Malo
    # The live contract window: validated hourly data lags months, so it is a fixed past month.
    DEFAULT_START = datetime(2025, 1, 1, tzinfo=UTC)
    DEFAULT_END = datetime(2025, 2, 1, tzinfo=UTC)

    def __init__(self, start: datetime | None = None, end: datetime | None = None, *,
                 min_interval_s: float = 1.0) -> None:
        self.start = _utc(start or self.DEFAULT_START)
        self.end = _utc(end or self.DEFAULT_END)
        windows(self.start, self.end)    # validates the range
        self.min_interval_s = min_interval_s
        self._last_update: str | None = None

    # -- fetch ----------------------------------------------------------------------------
    def fetcher(self, cache_dir: Path) -> PoliteFetcher:
        """The only way this adapter reaches the network: every URL is guarded first."""
        return PoliteFetcher(cache_dir, min_interval_s=self.min_interval_s, guard=guard_url)

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        fetcher = self.fetcher(cache_dir)
        got: list = []
        items: dict[str, dict] = {}
        with _guarded_redirects():
            gl = fetcher.get(f"https://{SHOM_HOST}{GAUGES_PATH}", cache_name="tidegauges.json",
                             refresh=True)
            got.append(gl)
            gauges = {str(g["shom_id"]): g for g in json.loads(gl.bytes())}
            ids = sorted(gauges, key=int) if only is None else [str(s) for s in only]
            unknown = [s for s in ids if s not in gauges]
            if unknown:
                raise ValueError(f"shom_id {unknown} not in the SHOM gauge list ({len(gauges)} gauges)")
            for sid in ids:
                wins = []
                for a, b in windows(self.start, self.end):
                    params = {"sources": str(SOURCE_ID), "dtStart": a.strftime(QUERY_STAMP),
                              "dtEnd": b.strftime(QUERY_STAMP)}
                    f = fetcher.get(f"https://{SHOM_HOST}/maregraphie/observation/json/{sid}",
                                    params=params, cache_name=f"obs/{sid}/{_compact(a)}_{_compact(b)}.json")
                    got.append(f)
                    wins.append({"path": f.name, "sha256": f.sha256, "dtStart": params["dtStart"],
                                 "dtEnd": params["dtEnd"]})
                items[sid] = {"gauge": gauges[sid], "source_id": SOURCE_ID, "windows": wins}
        files = tuple(f.input_file() for f in got)
        version = gl.fetched_at[:10].replace("-", "")
        self._last_update = gl.fetched_at[:10]
        fetched = FetchResult(source=self.source, source_version=version, cache_dir=cache_dir,
                              files=files, items=items)
        self.write_manifest(fetched)
        return fetched

    def write_manifest(self, fetched: FetchResult) -> Path:
        """MANIFEST.json for the cache: this fetch's files merged with earlier entries whose file
        is still in the cache with the same bytes (the cache accumulates windows across runs)."""
        path = Path(fetched.cache_dir) / "MANIFEST.json"
        entries: dict[str, dict] = {}
        if path.is_file():
            try:
                old = json.loads(path.read_text()).get("files", [])
            except (OSError, ValueError, AttributeError):
                log.warning("%s is unreadable; rewriting it from this fetch", path)
                old = []
            for e in old:
                p = Path(fetched.cache_dir) / str(e.get("path", ""))
                if isinstance(e, dict) and p.is_file() and sha256_file(p) == e.get("sha256"):
                    entries[e["path"]] = e
        for f in fetched.files:
            entries[f.path] = f.as_dict()
        doc = {"dataset": DATASET, "licence": SPDX, "citation": CITATION["RONIM"],
               "citation_partner_gauges": CITATION["PARTENAIRES"], "archive": "cache/shom-refmar/",
               "adapter_version": self.adapter_version, "source_version": fetched.source_version,
               "files": [entries[k] for k in sorted(entries)]}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        tmp.replace(path)
        return path

    # -- convert --------------------------------------------------------------------------
    def _rows(self, fetched: FetchResult, sid: str) -> tuple[list[tuple[int, float | None]], str]:
        """(stamp seconds, value) rows of a gauge in time order, checked against the windows
        asked for, and the series' member SHA-256."""
        item = fetched.items[sid]
        rows: list[tuple[int, float | None]] = []
        h = hashlib.sha256()
        for w in item["windows"]:
            raw = (Path(fetched.cache_dir) / w["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != w["sha256"]:
                raise ValueError(f"{w['path']}: bytes differ from the fetch's SHA-256")
            h.update(raw)
            lo = int(datetime.strptime(w["dtStart"], QUERY_STAMP).replace(tzinfo=UTC).timestamp())
            hi = int(datetime.strptime(w["dtEnd"], QUERY_STAMP).replace(tzinfo=UTC).timestamp())
            for r in json.loads(raw)["data"]:
                if str(r.get("idstation")) != sid or r.get("idsource") != item["source_id"]:
                    raise ValueError(f"{w['path']}: row for station {r.get('idstation')!r} source "
                                     f"{r.get('idsource')!r}, expected {sid} source {item['source_id']}")
                t = _served_seconds(r["timestamp"])
                if not lo <= t <= hi:
                    raise ValueError(f"{w['path']}: stamp {r['timestamp']} is outside the requested "
                                     f"window {w['dtStart']}..{w['dtEnd']}")
                v = r.get("value")
                if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                    raise ValueError(f"{w['path']}: value {v!r} at {r['timestamp']} is not a number")
                rows.append((t, v))
        rows.sort(key=lambda tv: tv[0])
        for (t0, _), (t1, _) in zip(rows, rows[1:]):
            if t0 == t1:
                raise ValueError(f"station {sid}: duplicate stamp "
                                 f"{datetime.fromtimestamp(t0, UTC).strftime(SERVED_STAMP)}")
        # One window: that file's SHA-256. Several: SHA-256 of the window files' bytes
        # concatenated in time order (reproducible from the cache).
        member = item["windows"][0]["sha256"] if len(item["windows"]) == 1 else h.hexdigest()
        return rows, member

    def records(self, fetched: FetchResult) -> Iterable[GaugeRecord]:
        for sid in sorted(fetched.items, key=int):
            item = fetched.items[sid]
            rows, member = self._rows(fetched, sid)
            if not rows:
                log.info("shom-refmar %s: no samples in %s..%s; no record", sid, self.start, self.end)
                continue
            g = item["gauge"]
            ronim = g.get("reseau") == "RONIM"
            sampling, interval = SOURCE_IDS[item["source_id"]]
            heights = np.array([np.nan if v is None else float(v) for _, v in rows], dtype=np.float64)
            yield GaugeRecord(
                record_id=f"{self.source}:{sid}", source=self.source,
                source_version=fetched.source_version,
                contributor="SHOM" if ronim else "REFMAR partner", originator=ronim,
                licence_class=SPDX, lat=float(g["latitude"]), lon=float(g["longitude"]),
                gauge_type="Coastal", sampling=sampling, interval_min=interval,
                declared_time_base="utc_instant", member_sha256=member,
                times_s=np.array([t for t, _ in rows], dtype=np.int64), heights_m=heights,
                good=np.isfinite(heights))

    def sets(self, fetched: FetchResult):
        return ()

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """shom_id -> SHA-256 of the normalised gauge metadata and served series."""
        keys = {}
        for sid in fetched.items:
            g = fetched.items[sid]["gauge"]
            rows, _ = self._rows(fetched, sid)
            doc = {"gauge": {k: g.get(k) for k in ("shom_id", "name", "latitude", "longitude", "reseau")},
                   "source_id": fetched.items[sid]["source_id"], "series": rows}
            keys[sid] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"))
                                       .encode()).hexdigest()
        return keys

    # -- convention and licence -----------------------------------------------------------
    def convention(self) -> dict:
        # Gauge records carry no constants; this is the convention the fit pipeline publishes in.
        return {"convention_id": "otc-gauge-fit-greenwich", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "tcd"}

    @staticmethod
    def citation_for(reseau: str | None) -> str:
        """The citation the REFMAR licence requires: #RONIM for SHOM gauges, #PARTENAIRES else."""
        return CITATION["RONIM"] if reseau == "RONIM" else CITATION["PARTENAIRES"]

    def licence(self) -> dict:
        updated = (f"Data last updated {self._last_update} (fetch date)." if self._last_update
                   else "Data last updated: the fetch date in source_version.")
        return {
            "licence_id": "shom-refmar-etalab-2.0", "spdx": SPDX, "provider": "SHOM (REFMAR)",
            "citation": CITATION["RONIM"],
            "attribution": (f"{CITATION['RONIM']} (SHOM RONIM gauges); {CITATION['PARTENAIRES']} "
                            "(REFMAR partner gauges). Licence Ouverte / Open Licence 2.0 (Etalab). "
                            f"{updated}"),
            "url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            "commercial_use": True,
        }
