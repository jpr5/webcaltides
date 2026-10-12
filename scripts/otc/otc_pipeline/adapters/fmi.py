"""FMI (Finnish Meteorological Institute) mareograph observations -> GaugeRecord (slot A-FMI-a).

Endpoint: the keyless FMI open-data WFS 2.0 service `https://opendata.fmi.fi/wfs`.

- Station list: stored query `fmi::ef::stations` with `networkid=133` ("Mareografiasema", the
  14 Finnish tide gauges). Name, position (EPSG:4258) and region come from it.
- Observations: stored query `fmi::observations::mareograph::simple` with `fmisid`,
  `starttime`, `endtime`, `timestep` and `parameters=WLEVN2K_PT1S_INSTANT`. The query also
  serves `WATLEV` (sea level relative to the theoretical mean water level, which FMI moves
  every year, so the series steps at year ends) and `TW_PT1H_AVG` (water temperature). The
  adapter reads only `WLEVN2K_PT1S_INSTANT`: the instantaneous (1 s) sea level in mm relative
  to the fixed national height system N2000. Values are `NaN` where FMI has none.

Limits, measured on 2026-10-08: one request may span at most 168 h ("Too long time interval
requested! No more than 168.000000 hours allowed.", HTTP 400) at any `timestep`; `endtime` is
inclusive; the response has no paging (numberMatched == numberReturned). The adapter therefore
splits the window into chunks of `chunk_hours` (default 168) that end one time step before the
next chunk starts. FMI's open-data fair-use limits are 20,000 requests a day and 600 per 5
minutes; the fetcher sends one request at a time, at least `min_interval_s` apart. A year at
the default 10-min step is 53 requests (about 33 MB of XML) per station.

Time base: every `Time` is ISO 8601 with `Z` (UTC). `timestep` picks the 1 s instantaneous
value at each step from the hour (FMI holds 1-min values; `timestep=1` returns them). The
adapter declares `utc_instant` and `sampling="instantaneous"`; the time-base audit in A-FMI-b
checks the declaration against comparators.

QC: the simple-feature format carries no per-value quality flag, so `good` = a finite value.

Tide: the Baltic coast of Finland is microtidal (M2 amplitude of a few mm to about 2 cm; the
variation is mostly wind, pressure and seiches), so the fit is expected to get the
`microtidal` flag. FMI publishes no astronomical tide prediction; `fmi::forecast::sealevel::*`
is a 48 h total-water-level model forecast with no history, not a reference for §10 item 5.

Licence: CC BY 4.0 (FMI open-data licence).
"""
from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult
from otc_pipeline.http import PoliteFetcher

BASE = "https://opendata.fmi.fi/wfs"
OBS_QUERY = "fmi::observations::mareograph::simple"
STATION_QUERY = "fmi::ef::stations"
NETWORK_ID = "133"              # Mareografiasema (tide gauges)
PARAMETER = "WLEVN2K_PT1S_INSTANT"
MAX_CHUNK_HOURS = 168
SPDX = "CC-BY-4.0"

NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "BsWfs": "http://xml.fmi.fi/schema/wfs/2.0",
    "ef": "http://inspire.ec.europa.eu/schemas/ef/4.0",
}
FMISID_SPACE = "http://xml.fmi.fi/namespace/stationcode/fmisid"
REGION_SPACE = "http://xml.fmi.fi/namespace/location/region"


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp())


def _day(d: str | date | None, default: date) -> datetime:
    d = date.fromisoformat(d) if isinstance(d, str) else (d or default)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


class FmiAdapter:
    source = "fmi"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "fmi-1"
    # Helsinki Kaivopuisto, Hanko Pikku Kolalahti, Kemi Ajos (the manifest's three named stations)
    contract_sample = ("132310", "134253", "100539")

    def __init__(self, start: str | date | None = None, end: str | date | None = None, *,
                 timestep_min: int = 10, chunk_hours: int = MAX_CHUNK_HOURS, refresh: bool = False,
                 min_interval_s: float = 1.0, fetcher_kw: dict | None = None):
        """Window [start, end) in whole UTC days; default: the previous calendar year."""
        today = datetime.now(timezone.utc).date()
        self.start = _day(start, date(today.year - 1, 1, 1))
        self.end = _day(end, date(today.year, 1, 1))
        if not self.start < self.end:
            raise ValueError(f"FMI window start {self.start:%Y-%m-%d} must be before end {self.end:%Y-%m-%d}")
        if not 1 <= chunk_hours <= MAX_CHUNK_HOURS:
            raise ValueError(f"chunk_hours {chunk_hours} must be 1..{MAX_CHUNK_HOURS} (FMI's per-request limit)")
        if timestep_min < 1 or 60 % timestep_min:
            raise ValueError(f"timestep_min {timestep_min} must divide 60 (FMI steps from the hour)")
        self.timestep_min = timestep_min
        self.chunk_hours = chunk_hours
        self.refresh = refresh
        self.min_interval_s = min_interval_s
        self.fetcher_kw = dict(fetcher_kw or {})

    # -- fetch ------------------------------------------------------------------------------
    def _chunks(self) -> list[tuple[datetime, datetime]]:
        """[start, end) as (first, last) stamps per request; FMI's endtime is inclusive, so each
        chunk ends one time step before the next one starts."""
        step = timedelta(minutes=self.timestep_min)
        size = timedelta(hours=self.chunk_hours)
        out, a = [], self.start
        while a < self.end:
            b = min(a + size, self.end)
            out.append((a, b - step))
            a = b
        return out

    @staticmethod
    def _parse_stations(body: bytes) -> dict[str, dict]:
        """fmisid -> name, region, lat, lon from the station list."""
        out = {}
        for fac in ET.fromstring(body).iter(f"{{{NS['ef']}}}EnvironmentalMonitoringFacility"):
            sid = next((i.text for i in fac.findall("gml:identifier", NS)
                        if i.get("codeSpace") == FMISID_SPACE), None)
            pos = fac.findtext("ef:representativePoint/gml:Point/gml:pos", namespaces=NS)
            if not sid or not pos:
                continue
            lat, lon = (float(x) for x in pos.split()[:2])
            region = next((n.text for n in fac.findall("gml:name", NS)
                           if n.get("codeSpace") == REGION_SPACE), None)
            out[sid] = {"name": fac.findtext("ef:name", namespaces=NS), "region": region,
                        "lat": lat, "lon": lon}
        return out

    @staticmethod
    def _parse_obs(body: bytes) -> list[tuple[str, str]]:
        """(Time, ParameterValue) of every WLEVN2K_PT1S_INSTANT element, as served."""
        root = ET.fromstring(body)
        els = list(root.iter(f"{{{NS['BsWfs']}}}BsWfsElement"))
        returned, matched = root.get("numberReturned"), root.get("numberMatched")
        if returned is None or int(returned) != len(els) or (matched is not None and int(matched) != len(els)):
            raise ValueError(f"FMI response: numberReturned {returned} / numberMatched {matched} "
                             f"!= {len(els)} elements")
        out = []
        for el in els:
            if el.findtext("BsWfs:ParameterName", namespaces=NS) != PARAMETER:
                continue
            out.append((el.findtext("BsWfs:Time", namespaces=NS),
                        el.findtext("BsWfs:ParameterValue", namespaces=NS).strip()))
        return out

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        fetcher = PoliteFetcher(cache_dir, min_interval_s=self.min_interval_s, **self.fetcher_kw)
        stations = fetcher.get(BASE, params={"service": "WFS", "version": "2.0.0", "request": "getFeature",
                                             "storedquery_id": STATION_QUERY, "networkid": NETWORK_ID},
                               cache_name="stations.xml", refresh=self.refresh)
        known = self._parse_stations(stations.bytes())
        if only is None:
            only = sorted(known)
        unknown = [s for s in only if s not in known]
        if unknown:
            raise ValueError(f"FMI fmisid {', '.join(unknown)} is not in the mareograph network "
                             f"(networkid={NETWORK_ID}; known: {', '.join(sorted(known))})")
        files = [stations.input_file()]
        items: dict[str, dict] = {}
        for sid in only:
            obs: list[tuple[str, str]] = []
            names: list[str] = []
            for a, b in self._chunks():
                got = fetcher.get(BASE, params={
                    "service": "WFS", "version": "2.0.0", "request": "getFeature",
                    "storedquery_id": OBS_QUERY, "fmisid": sid, "parameters": PARAMETER,
                    "starttime": _stamp(a), "endtime": _stamp(b), "timestep": str(self.timestep_min)},
                    cache_name=f"obs/{sid}/{a:%Y%m%dT%H%M}.xml", refresh=self.refresh)
                files.append(got.input_file())
                names.append(got.name)
                obs += self._parse_obs(got.bytes())
            items[sid] = {"station": known[sid], "obs": obs, "files": names}
        fetched = FetchResult(source=self.source,
                              source_version=datetime.now(timezone.utc).strftime("%Y%m%d"),
                              cache_dir=cache_dir, files=tuple(files), items=items)
        manifest = fetched.manifest(
            dataset=f"FMI {OBS_QUERY} {PARAMETER} (mm, N2000) timestep {self.timestep_min} min "
                    f"{self.start:%Y-%m-%d}..{self.end:%Y-%m-%d} (end exclusive)",
            licence=SPDX, citation="Finnish Meteorological Institute (FMI), open data WFS, "
            "https://opendata.fmi.fi/wfs", archive=f"cache/{self.source}/")
        (cache_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        return fetched

    # -- convert ----------------------------------------------------------------------------
    @staticmethod
    def _arrays(obs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Times (s), heights (m) and good, sorted by time (stable: duplicates keep served order)."""
        t = np.array([_epoch(o[0]) for o in obs], dtype=np.int64)
        h = np.array([float(o[1]) / 1000.0 for o in obs], dtype=np.float64)   # "NaN" -> nan
        good = np.isfinite(h)
        order = np.argsort(t, kind="stable")
        return t[order], h[order], good[order]

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
                source_version=fetched.source_version, contributor="FMI",
                originator=True, licence_class=SPDX,
                lat=st["lat"], lon=st["lon"], gauge_type="Coastal",
                sampling="instantaneous", interval_min=self.timestep_min,
                declared_time_base="utc_instant",
                member_sha256=self._member_sha256(fetched, item["files"]),
                times_s=t, heights_m=h, good=good)

    def sets(self, fetched: FetchResult):
        return ()

    # -- metadata ---------------------------------------------------------------------------
    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """fmisid -> SHA-256 of its sorted (Time, value) pairs. The response `timeStamp` and the
        gml:ids are left out: they change on every request."""
        def norm(v: str) -> str:
            x = float(v)
            return "NaN" if math.isnan(x) else repr(x)
        return {sid: hashlib.sha256(json.dumps(sorted((o[0], norm(o[1])) for o in item["obs"]),
                                               separators=(",", ":")).encode()).hexdigest()
                for sid, item in fetched.items.items()}

    def convention(self) -> dict:
        # Constants for this source come from the OTC fit (A-FMI-b): Greenwich phases, UTC.
        return {"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "otc-fit"}

    def licence(self) -> dict:
        return {"licence_id": "fmi-cc-by-4.0", "spdx": SPDX,
                "provider": "Finnish Meteorological Institute (FMI)",
                "attribution": "Contains data from the Finnish Meteorological Institute (FMI), "
                               "licensed under CC BY 4.0.",
                "url": "https://en.ilmatieteenlaitos.fi/open-data-licence"}
