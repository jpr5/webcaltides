"""SMHI (Swedish Meteorological and Hydrological Institute) sea level -> GaugeRecord (slot A-SMHI-a).

Endpoint: SMHI Open Data Oceanographic Observations (ocobs), documented at
https://opendata.smhi.se/ocobs/introduction, host `opendata-download-ocobs.smhi.se`.

- `parameter/6.json`: the station list for parameter 6, "Havsvattenstånd" (sea level, cm).
- `parameter/6/station/{id}/period/corrected-archive/data.csv`: the whole series of one
  station up to 00 UTC today. Semicolon-separated, UTF-8 with BOM: a station block (name, id,
  lat, lon), a parameter block (name, unit), then `Datum Tid (UTC);Havsvattenstånd;Kvalitet;
  Mätdjup (m)` rows. The first rows carry a free-text side column (period, height system
  RH2000, the quality-code legend); it is not data and is ignored.

Time base and sampling, from SMHI's dataset metadata (ISO 19139 record
https://opendata-catalog.smhi.se/md/2205ecf4-647b-4ca0-b7b4-50e3199932b9, "Havsvattenstånd
RH2000, timvärde"): "Ett momentant mätvärde per timme" (one instantaneous reading per hour);
the CSV time column is headed "(UTC)". On 2026-10-08 the latest-day hourly values at Smögen
equalled the parameter-13 minute value at the same stamp (RMS 0.0 cm over 25 hours). The
adapter declares `utc_instant` and `sampling="instantaneous"`; the A-SMHI-b time-base audit
checks the declaration against comparators (the early archive was digitised from paper charts).

Heights: cm in RH2000 (the national height system), converted to metres. The datum does not
matter for harmonic constants but is not mean sea level.

QC ("Kvalitet"): G = checked and approved; Y = suspect or aggregated values, roughly checked
archive data (SMHI also fills gaps; filled values carry Y); O = not checked. As with GESLA
flags 1 and 0, G and O are usable; Y, any other code and an empty value are not.

Fair use (https://www.smhi.se/data/om-smhis-data/villkor-for-anvandning): do not load SMHI
services needlessly; avoid mass downloads of many places at once and fetching the same data
again. The adapter fetches one station at a time, at least 5 s apart, through
otc_pipeline.http (Retry-After, backoff), and serves repeats from the cache unless
`refresh=True`.

Licence: Creative Commons Attribution 4.0 (SMHI open-data terms); name SMHI as the source.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult
from otc_pipeline.http import PoliteFetcher

BASE = "https://opendata-download-ocobs.smhi.se/api/version/latest/parameter/6"
PERIOD = "corrected-archive"
TIME_HEADER = "Datum Tid (UTC)"
GOOD_QC = frozenset({"G", "O"})
SPDX = "CC-BY-4.0"


@dataclass(frozen=True)
class ParsedCsv:
    station_name: str
    station_id: str
    lat: float
    lon: float
    unit: str
    rows: list[tuple[str, float | None, str]]   # (stamp "YYYY-MM-DD HH:MM:SS" UTC, cm, quality)


def parse_csv(text: str, expect_id: str | None = None) -> ParsedCsv:
    """One corrected-archive CSV. Raises on a layout, unit, time-zone or station-id surprise."""
    lines = text.lstrip("﻿").splitlines()
    try:
        st = lines.index("Stationsnamn;Stationsid;Stationens latitud;Stationens longitud")
        name, sid, lat, lon = lines[st + 1].split(";")[:4]
        pm = next(i for i, l in enumerate(lines) if l.startswith("Parameternamn;Enhet"))
        unit = lines[pm + 1].split(";")[1]
        head = next(i for i, l in enumerate(lines) if l.startswith("Datum Tid"))
    except (ValueError, StopIteration, IndexError) as e:
        raise ValueError(f"SMHI CSV layout not recognised: {e}") from None
    if unit != "cm":
        raise ValueError(f"SMHI CSV unit {unit!r} is not 'cm'")
    cols = lines[head].split(";")
    if cols[0] != TIME_HEADER or cols[2:3] != ["Kvalitet"]:
        raise ValueError(f"SMHI CSV time column {cols[0]!r} is not {TIME_HEADER!r} (UTC expected), "
                         f"columns {cols[:4]}")
    if expect_id is not None and sid != expect_id:
        raise ValueError(f"SMHI CSV is for station {sid}, not the requested {expect_id}")
    rows = []
    for l in lines[head + 1:]:
        if not l.strip():
            continue
        f = l.split(";")
        rows.append((f[0], float(f[1]) if f[1].strip() else None, f[2]))
    return ParsedCsv(name, sid, float(lat), float(lon), unit, rows)


def _epoch(stamp: str) -> int:
    return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


class SmhiAdapter:
    source = "smhi"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "smhi-1"
    # Göteborg-Torshamnen (manifest), Smögen and Kungsvik (west coast, SMHI-operated, long records)
    contract_sample = ("2109", "2111", "2130")

    def __init__(self, *, refresh: bool = False, min_interval_s: float = 5.0,
                 fetcher_kw: dict | None = None):
        self.refresh = refresh
        self.min_interval_s = min_interval_s
        self.fetcher_kw = dict(fetcher_kw or {})

    # -- fetch ------------------------------------------------------------------------------
    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        """`only` = station ids; None = every station in the parameter-6 list, still fetched
        one at a time (a full run is ~75 requests; run it rarely, the cache keeps the rest)."""
        cache_dir = Path(cache_dir)
        fetcher = PoliteFetcher(cache_dir, min_interval_s=self.min_interval_s, **self.fetcher_kw)
        stations = fetcher.get(f"{BASE}.json", cache_name="stations.json", refresh=self.refresh)
        listed = {str(s["key"]): s for s in json.loads(stations.bytes())["station"]}
        if only is None:
            only = sorted(listed, key=int)
        files = [stations.input_file()]
        items: dict[str, dict] = {}
        for sid in only:
            if sid not in listed:
                raise ValueError(f"SMHI station {sid} is not in the parameter-6 station list")
            got = fetcher.get(f"{BASE}/station/{sid}/period/{PERIOD}/data.csv",
                              cache_name=f"data/{sid}/{PERIOD}.csv", refresh=self.refresh)
            p = parse_csv(got.bytes().decode("utf-8"), expect_id=sid)
            files.append(got.input_file())
            items[sid] = {"station": {"name": p.station_name, "owner": listed[sid].get("owner"),
                                      "lat": p.lat, "lon": p.lon},
                          "rows": p.rows, "file": got.name}
        fetched = FetchResult(source=self.source,
                              source_version=datetime.now(timezone.utc).strftime("%Y%m%d"),
                              cache_dir=cache_dir, files=tuple(files), items=items)
        manifest = fetched.manifest(
            dataset=f"SMHI ocobs parameter 6 (sea level, hourly, RH2000) {PERIOD}",
            licence=SPDX, citation="Swedish Meteorological and Hydrological Institute (SMHI), "
            "Open Data Oceanographic Observations, https://opendata-download-ocobs.smhi.se",
            archive=f"cache/{self.source}/")
        (cache_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        return fetched

    # -- convert ----------------------------------------------------------------------------
    @staticmethod
    def _arrays(rows) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Times (s), heights (m) and good, sorted by time (stable)."""
        t = np.array([_epoch(r[0]) for r in rows], dtype=np.int64)
        h = np.array([np.nan if r[1] is None else r[1] / 100.0 for r in rows], dtype=np.float64)
        good = np.array([r[2] in GOOD_QC for r in rows], dtype=bool) & np.isfinite(h)
        order = np.argsort(t, kind="stable")
        return t[order], h[order], good[order]

    @staticmethod
    def _interval_min(t: np.ndarray) -> int:
        """The most common positive step in whole minutes (60 for parameter 6)."""
        steps = np.diff(np.unique(t))
        if not len(steps):
            return 60
        vals, counts = np.unique(steps, return_counts=True)
        return max(1, int(round(vals[np.argmax(counts)] / 60)))

    def records(self, fetched: FetchResult) -> Iterable[GaugeRecord]:
        by_path = {f.path: f.sha256 for f in fetched.files}
        for sid in sorted(fetched.items, key=int):
            item = fetched.items[sid]
            if not item["rows"]:
                continue   # no samples: no record (the watch key still exists)
            t, h, good = self._arrays(item["rows"])
            st = item["station"]
            owner = st["owner"] or "SMHI"
            yield GaugeRecord(
                record_id=f"{self.source}:{sid}", source=self.source,
                source_version=fetched.source_version, contributor=owner,
                originator=owner == "SMHI", licence_class=SPDX,
                lat=st["lat"], lon=st["lon"], gauge_type="Coastal",
                sampling="instantaneous", interval_min=self._interval_min(t),
                declared_time_base="utc_instant", member_sha256=by_path[item["file"]],
                times_s=t, heights_m=h, good=good)

    def sets(self, fetched: FetchResult):
        return ()

    # -- metadata ---------------------------------------------------------------------------
    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """Station id -> SHA-256 of its (stamp, cm, quality) rows. The header's side column
        (period text, legend) is left out."""
        return {sid: hashlib.sha256(json.dumps([list(r) for r in item["rows"]],
                                               separators=(",", ":")).encode()).hexdigest()
                for sid, item in fetched.items.items()}

    def convention(self) -> dict:
        # Constants for this source come from the OTC fit (A-SMHI-b): Greenwich phases, UTC.
        return {"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "otc-fit"}

    def licence(self) -> dict:
        return {"licence_id": "smhi-cc-by-4.0", "spdx": SPDX,
                "provider": "Swedish Meteorological and Hydrological Institute (SMHI)",
                "attribution": "Contains data from the Swedish Meteorological and Hydrological "
                               "Institute (SMHI), licensed under CC BY 4.0.",
                "url": "https://www.smhi.se/data/om-smhis-data/villkor-for-anvandning",
                "commercial_use": True}
