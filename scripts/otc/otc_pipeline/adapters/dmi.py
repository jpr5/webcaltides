"""DMI (Danish Meteorological Institute) tide-gauge observations -> GaugeRecord (slot A-DMI-a).

Endpoint: the keyless OGC API Features service `https://opendataapi.dmi.dk/v2/oceanObs`
(API keys were dropped on 2025-12-02; the old host dmigw.govcloud.dk no longer resolves).

- `collections/station/items`: the station list. A station has one feature per version
  (validFrom/validTo); the adapter uses the version valid at the end of the window.
- `collections/observation/items?stationId=…&parameterId=sealev_dvr&datetime=a/b`: sea level
  in cm relative to DVR90, which DMI recommends for its own stations after 1997-01-01. One
  request per station and calendar year of the window (a year of 10-min values is ~52,600
  features; the service allows `limit` up to 300,000 and pages with `offset`).

Time base, from the DMI docs ("Oceanographic Observation and Tidewater API"): the `observed`
stamps are UTC. Since mid-2001 sea level is recorded every 10 minutes (before that 15, 30 and
60 min, read here from the series). DMI does not say whether a value is an instantaneous
reading or an average over the interval; the adapter declares `utc_instant` and
`sampling="instantaneous"`, and the time-base audit in A-DMI-b checks that declaration
against comparators instead of trusting it.

QC: each value has `qcStatus` "none" (no QC yet), "auto" (automatic real-time QC) or "manual".
DMI publishes no per-value flag beyond that, so `good` = a finite value with one of those three
statuses (like GESLA, where flag 0 "no QC" is usable). Any other status is not good.

Fair use: DMI sets no numeric limit but may throttle an IP that strains the service. The
fetcher keeps one request at a time, at least 2 s apart, and backs off on 429/5xx with the
server's Retry-After (otc_pipeline.http). Past windows are served from the cache unless
`refresh=True`.

Licence: CC BY 4.0 (DMI terms of use for free data).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult
from otc_pipeline.http import PoliteFetcher

BASE = "https://opendataapi.dmi.dk/v2/oceanObs"
PARAMETER = "sealev_dvr"
PAGE_LIMIT = 300_000
GOOD_QC = frozenset({"none", "auto", "manual"})
SPDX = "CC-BY-4.0"


def _utc(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp())


class DmiAdapter:
    source = "dmi"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "dmi-1"
    # Esbjerg Havn I, Hirtshals Havn I, Frederikshavn I (the manifest's three named stations)
    contract_sample = ("25149", "20047", "20101")

    def __init__(self, start: str | date | None = None, end: str | date | None = None, *,
                 refresh: bool = False, min_interval_s: float = 2.0, fetcher_kw: dict | None = None):
        """Window [start, end) in whole UTC days; default: the previous calendar year."""
        today = datetime.now(timezone.utc).date()
        self.start = date.fromisoformat(start) if isinstance(start, str) else (start or date(today.year - 1, 1, 1))
        self.end = date.fromisoformat(end) if isinstance(end, str) else (end or date(today.year, 1, 1))
        if not self.start < self.end:
            raise ValueError(f"DMI window start {self.start} must be before end {self.end}")
        self.refresh = refresh
        self.min_interval_s = min_interval_s
        self.fetcher_kw = dict(fetcher_kw or {})

    # -- fetch ------------------------------------------------------------------------------
    def _chunks(self) -> list[tuple[date, date]]:
        """[start, end) split at calendar-year boundaries."""
        out, a = [], self.start
        while a < self.end:
            b = min(date(a.year + 1, 1, 1), self.end)
            out.append((a, b))
            a = b
        return out

    def _station(self, features: list[dict], sid: str) -> dict:
        """The station version valid at the window end that serves sea level vs DVR90."""
        end = _utc(self.end)
        versions = [f for f in features if f["properties"].get("stationId") == sid
                    and PARAMETER in (f["properties"].get("parameterId") or [])]
        if not versions:
            raise ValueError(f"DMI station {sid} has no {PARAMETER} series in the station list")
        valid = [f for f in versions if f["properties"]["validFrom"] < end
                 and (f["properties"].get("validTo") is None or f["properties"]["validTo"] >= end)]
        best = max(valid or versions, key=lambda f: f["properties"]["validFrom"])
        p = best["properties"]
        lon, lat = best["geometry"]["coordinates"][:2]
        return {"name": p["name"], "owner": p.get("owner"), "type": p.get("type"),
                "country": p.get("country"), "lat": float(lat), "lon": float(lon)}

    @staticmethod
    def _parse_features(features: list[dict]) -> list[tuple[str, float | None, str]]:
        """(observed, value_cm, qcStatus) per feature, as served."""
        return [(f["properties"]["observed"], f["properties"].get("value"),
                 f["properties"].get("qcStatus")) for f in features]

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        fetcher = PoliteFetcher(cache_dir, min_interval_s=self.min_interval_s, **self.fetcher_kw)
        stations = fetcher.get(f"{BASE}/collections/station/items", params={"limit": "10000"},
                               cache_name="stations.json", refresh=self.refresh)
        st_features = json.loads(stations.bytes())["features"]
        if only is None:
            only = sorted({f["properties"]["stationId"] for f in st_features
                           if PARAMETER in (f["properties"].get("parameterId") or [])
                           and f["properties"].get("owner") == "DMI"})
        files = [stations.input_file()]
        items: dict[str, dict] = {}
        for sid in only:
            meta = self._station(st_features, sid)
            obs: list[tuple] = []
            names: list[str] = []
            for a, b in self._chunks():
                offset = 0
                while True:
                    params = {"stationId": sid, "parameterId": PARAMETER,
                              "datetime": f"{_utc(a)}/{(b - timedelta(days=1)).isoformat()}T23:59:59Z",
                              "limit": str(PAGE_LIMIT)}
                    name = f"obs/{sid}/{a.isoformat()}_{b.isoformat()}.json"
                    if offset:
                        params["offset"] = str(offset)
                        name = name[:-5] + f".offset{offset}.json"
                    got = fetcher.get(f"{BASE}/collections/observation/items", params=params,
                                      cache_name=name, refresh=self.refresh)
                    body = json.loads(got.bytes())
                    feats = body.get("features") or []
                    if body.get("numberReturned", len(feats)) != len(feats):
                        raise ValueError(f"{got.url}: numberReturned {body.get('numberReturned')} "
                                         f"!= {len(feats)} features")
                    files.append(got.input_file())
                    names.append(got.name)
                    obs += self._parse_features(feats)
                    if len(feats) < PAGE_LIMIT:
                        break
                    offset += PAGE_LIMIT
            items[sid] = {"station": meta, "obs": obs, "files": names}
        fetched = FetchResult(source=self.source,
                              source_version=datetime.now(timezone.utc).strftime("%Y%m%d"),
                              cache_dir=cache_dir, files=tuple(files), items=items)
        manifest = fetched.manifest(
            dataset=f"DMI oceanObs {PARAMETER} {self.start}..{self.end} (end exclusive)",
            licence=SPDX, citation="Danish Meteorological Institute (DMI), oceanObs API, "
            "https://opendataapi.dmi.dk/v2/oceanObs", archive=f"cache/{self.source}/")
        (cache_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        return fetched

    # -- convert ----------------------------------------------------------------------------
    @staticmethod
    def _arrays(obs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Times (s), heights (m) and good, sorted by time (stable: duplicates keep served order)."""
        t = np.array([_epoch(o[0]) for o in obs], dtype=np.int64)
        h = np.array([np.nan if o[1] is None else float(o[1]) / 100.0 for o in obs], dtype=np.float64)
        good = np.array([o[2] in GOOD_QC for o in obs], dtype=bool) & np.isfinite(h)
        order = np.argsort(t, kind="stable")
        return t[order], h[order], good[order]

    @staticmethod
    def _interval_min(t: np.ndarray) -> int:
        """The most common positive step in whole minutes (10 since mid-2001)."""
        steps = np.diff(np.unique(t))
        if not len(steps):
            return 10
        vals, counts = np.unique(steps, return_counts=True)
        return max(1, int(round(vals[np.argmax(counts)] / 60)))

    @staticmethod
    def _member_sha256(fetched: FetchResult, names: list[str]) -> str:
        """The raw file's SHA-256; for a series served in several files, the SHA-256 of their
        `sha256  path` lines (sha256sum format, sorted by path)."""
        by_path = {f.path: f.sha256 for f in fetched.files}
        if len(names) == 1:
            return by_path[names[0]]
        lines = "".join(f"{by_path[n]}  {n}\n" for n in sorted(names))
        return hashlib.sha256(lines.encode()).hexdigest()

    def records(self, fetched: FetchResult) -> Iterable[GaugeRecord]:
        for sid in sorted(fetched.items):
            item = fetched.items[sid]
            if not item["obs"]:
                continue   # no samples in the window: no record (the watch key still exists)
            t, h, good = self._arrays(item["obs"])
            st = item["station"]
            yield GaugeRecord(
                record_id=f"{self.source}:{sid}", source=self.source,
                source_version=fetched.source_version, contributor="DMI",
                originator=st["owner"] == "DMI", licence_class=SPDX,
                lat=st["lat"], lon=st["lon"], gauge_type="Coastal",
                sampling="instantaneous", interval_min=self._interval_min(t),
                declared_time_base="utc_instant",
                member_sha256=self._member_sha256(fetched, item["files"]),
                times_s=t, heights_m=h, good=good)

    def sets(self, fetched: FetchResult):
        return ()

    # -- metadata ---------------------------------------------------------------------------
    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """Station id -> SHA-256 of its sorted (observed, value, qcStatus) triples. The server's
        `created` stamp and feature id are left out: they change when DMI re-serves a value."""
        return {sid: hashlib.sha256(json.dumps(sorted(item["obs"], key=lambda o: (o[0], str(o[1]), str(o[2]))),
                                               separators=(",", ":")).encode()).hexdigest()
                for sid, item in fetched.items.items()}

    def convention(self) -> dict:
        # Constants for this source come from the OTC fit (A-DMI-b): Greenwich phases, UTC.
        return {"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "otc-fit"}

    def licence(self) -> dict:
        return {"licence_id": "dmi-cc-by-4.0", "spdx": SPDX,
                "provider": "Danish Meteorological Institute (DMI)",
                "attribution": "Contains data from the Danish Meteorological Institute (DMI), "
                               "licensed under CC BY 4.0.",
                "url": "https://www.dmi.dk/friedata/dokumentation/terms-of-use",
                "commercial_use": True}
