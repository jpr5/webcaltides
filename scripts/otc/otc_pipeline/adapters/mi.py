"""Marine Institute (Ireland): Irish National Tide Gauge Network observations from ERDDAP.

Dataset `IrishNationalTideGaugeNetwork` on https://erddap.marine.ie/erddap (keyless, CC BY 4.0
per the dataset's `license` attribute). One tabledap .csv request per station per window, every
constraint percent-encoded:

    tabledap/IrishNationalTideGaugeNetwork.csv?time,station_id,latitude,longitude,
        Water_Level_LAT,Water_Level_OD_Malin,QC_Flag&station_id="<id>"&time>=<start>&time<<end>

The station list is the dataset's subset table (`station_id,longitude,latitude&distinct()`
with no time constraint); a distinct() over a time window times out upstream (HTTP 504).

Conversion to GaugeRecord:
- times: the `time` column, ISO 8601 UTC (`units` "seconds since 1970-01-01T00:00:00Z"), as stamped.
- heights: metres above OD Malin. A station with no finite OD Malin value in the window uses
  the LAT column for the whole record instead (never mixed within a record).
- good: `QC_Flag` in {0 unknown, 1 good} (flag 9 = missing_value) and a finite height; the
  dataset's _FillValue -9999 counts as missing. Every row is kept, so the record's sample count
  equals the response's row count.

Declared time base: `utc_instant`, from the dataset metadata. `time` is UTC; the water-level
variables say `processing_method` "instantaneous values" and `time_sampling` "5 mins"
(`time_coverage_resolution` PT5M). They also carry `cell_methods` "mean" with no axis or
interval. Read either way the stamp is within 2.5 min of the sample, inside the ±3 min band
of `utc_instant` (BP §2); the -b slot's time-base audit checks the declaration against the
IMI predictions.

Published datum (datums spec §4.1 item 7, slot DT5e): every row carries the level in both LAT
and OD Malin, so their difference is the height of the Marine Institute's LAT above OD Malin.
`published_datums` gives it, per OD Malin record, as `lat` (kind `published`, `source_name`
LAT) with `zero` `vertical_datum`, `zero_name` OD Malin, for the fit (A-mi-b) to put beside its
own `msl_offset_m`; only when the difference is one constant (to the data's 1 mm) over every
row with both values. No `chart_datum`: no Marine Institute source read here says LAT is the
chart datum (the dataset calls it "Lowest Astronomical Tide", nothing more). A record kept in
LAT (no OD Malin) gets no published datum.

A station with no rows in the window (ERDDAP answers 404 "no matching results") is skipped by
`fetch`; asking `load` for it is an error.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult, InputFile, SourceSet, sha256_file
from otc_pipeline.datums import published
from otc_pipeline.http import HttpError, PoliteFetcher

__all__ = ["MarineInstituteAdapter", "ParsedObs", "obs_cache_name", "obs_url", "parse_obs_csv"]

log = logging.getLogger(__name__)

ERDDAP = "https://erddap.marine.ie/erddap"
DATASET = "IrishNationalTideGaugeNetwork"
INFO_URL = f"{ERDDAP}/info/{DATASET}/index.csv"
# The dataset's own licence statement (info.csv, NC_GLOBAL license): the licence() claim rests on it,
# so a fetch whose info.csv says anything else is refused.
SERVED_LICENCE = "Creative Commons Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/)"
STATIONS_URL = f"{ERDDAP}/tabledap/{DATASET}.csv?station_id%2Clongitude%2Clatitude&distinct()"
COLUMNS = ("time", "station_id", "latitude", "longitude", "Water_Level_LAT", "Water_Level_OD_Malin", "QC_Flag")
UNITS = ("UTC", "", "degrees_north", "degrees_east", "metres", "metres", "")
GOOD_QC = frozenset({"0", "1"})     # flag_values 0, 1, 9 = unknown, good, missing_value
FILL_VALUE = -9999.0
NO_ROWS = "Your query produced no matching results"
DEFAULT_INTERVAL_MIN = 5            # time_coverage_resolution PT5M


def _q(constraint: str) -> str:
    return urllib.parse.quote(constraint, safe="=")


def obs_url(station_id: str, start: date, end: date) -> str:
    """The tabledap request for one station's rows with start <= time < end (UTC dates)."""
    cons = (f'station_id="{station_id}"', f"time>={start.isoformat()}T00:00:00Z",
            f"time<{end.isoformat()}T00:00:00Z")
    return (f"{ERDDAP}/tabledap/{DATASET}.csv?" + urllib.parse.quote(",".join(COLUMNS), safe="")
            + "".join("&" + _q(c) for c in cons))


def _slug(station_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", station_id).strip("_")


def obs_cache_name(station_id: str, start: date, end: date) -> str:
    return f"obs/{_slug(station_id)}_{start:%Y%m%d}_{end:%Y%m%d}.csv"


@dataclass(frozen=True)
class ParsedObs:
    station_id: str
    lat: float
    lon: float
    datum: str              # "OD Malin" or "LAT"
    lat_above_od_malin: tuple[float, ...]   # distinct OD Malin - LAT values (1 mm) over rows with both
    times_s: np.ndarray     # int64, as stamped
    heights_m: np.ndarray   # float64, NaN where missing
    qc: tuple[str, ...]
    good: np.ndarray


def _height(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        return math.nan
    return math.nan if v == FILL_VALUE else v


def _stamp(text: str) -> int:
    t = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int(t.timestamp())


def parse_obs_csv(text: str) -> ParsedObs:
    """One station's tabledap .csv response. Refuses a response whose columns, units or station
    differ from what the request asked for (ValueError)."""
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 3:
        raise ValueError("ERDDAP response has no data rows")
    if tuple(rows[0]) != COLUMNS:
        raise ValueError(f"ERDDAP columns {rows[0]!r} are not {list(COLUMNS)!r}")
    if tuple(rows[1]) != UNITS:
        raise ValueError(f"ERDDAP units {rows[1]!r} are not {list(UNITS)!r}")
    data = [r for r in rows[2:] if r]
    if any(len(r) != len(COLUMNS) for r in data):
        raise ValueError("ERDDAP row with the wrong number of fields")
    stations = {r[1] for r in data}
    if len(stations) != 1:
        raise ValueError(f"ERDDAP response holds {len(stations)} stations: {sorted(stations)!r}")
    times = np.array([_stamp(r[0]) for r in data], dtype=np.int64)
    od = np.array([_height(r[5]) for r in data], dtype=np.float64)
    lat_h = np.array([_height(r[4]) for r in data], dtype=np.float64)
    datum, heights = ("OD Malin", od) if np.isfinite(od).any() else ("LAT", lat_h)
    both = np.isfinite(od) & np.isfinite(lat_h)
    # metres at 1 mm resolution: LAT above OD Malin = (height above OD Malin) - (height above LAT)
    offsets = tuple(sorted({round(float(v), 3) for v in (od[both] - lat_h[both])}))
    qc = tuple(r[6] for r in data)
    good = np.array([q in GOOD_QC for q in qc], dtype=bool) & np.isfinite(heights)
    last = data[-1]
    return ParsedObs(station_id=last[1], lat=float(last[2]), lon=float(last[3]), datum=datum,
                     lat_above_od_malin=offsets,
                     times_s=times, heights_m=heights, qc=qc, good=good)


def _stations(text: str) -> dict[str, tuple[float, float]]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(rows[0]) != ("station_id", "longitude", "latitude"):
        raise ValueError(f"ERDDAP station list columns {rows[:1]!r} are not station_id,longitude,latitude")
    return {r[0]: (float(r[2]), float(r[1])) for r in rows[2:] if r and r[0]}


def served_licence(info_csv: str) -> str | None:
    """The NC_GLOBAL `license` attribute of an ERDDAP info .csv, or None when it has none."""
    for row in csv.reader(io.StringIO(info_csv)):
        if len(row) >= 5 and row[:3] == ["attribute", "NC_GLOBAL", "license"]:
            return row[4]
    return None


def _input_file(cache_dir: Path, name: str) -> InputFile:
    """InputFile for a cached response, from the polite fetcher's meta file (url, fetched_at)."""
    path = cache_dir / name
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not in the cache; run fetch first")
    meta = json.loads((cache_dir / f"{name}.meta.json").read_text())
    return InputFile(path=name, sha256=sha256_file(path), size=path.stat().st_size,
                     source_url=meta["url"], fetched_at=meta["fetched_at"])


def _interval_min(times_s: np.ndarray) -> int:
    if len(times_s) < 2:
        return DEFAULT_INTERVAL_MIN
    step = int(round(float(np.median(np.diff(np.sort(times_s)))) / 60.0))
    return step if step > 0 else DEFAULT_INTERVAL_MIN


class MarineInstituteAdapter:
    source = "marine-institute"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "mi-2"   # mi-2: published LAT above OD Malin (DT5e)
    contract_sample = ("Dublin Port", "Galway Port", "Malin Head - Portmore Pier")

    def __init__(self, start: date | None = None, end: date | None = None, *,
                 today: date | None = None) -> None:
        """The window is [start, end) in UTC dates; default: the last complete UTC year."""
        if start is None or end is None:
            year = (today or datetime.now(timezone.utc).date()).year
            start, end = start or date(year - 1, 1, 1), end or date(year, 1, 1)
        if end <= start:
            raise ValueError(f"window end {end} is not after start {start}")
        self.start, self.end = start, end

    # -- fetch and load ----------------------------------------------------------------------
    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        fetcher = PoliteFetcher(cache_dir, min_interval_s=1.0)
        fetcher.get(INFO_URL, cache_name="info.csv", refresh=True)
        listed = fetcher.get(STATIONS_URL, cache_name="stations.csv", refresh=True)
        stations = _stations(listed.path.read_text())
        ids = self._ids(stations, only)
        fetched_ids = []
        for sid in ids:
            try:
                fetcher.get(obs_url(sid, self.start, self.end),
                            cache_name=obs_cache_name(sid, self.start, self.end))
            except HttpError as e:
                if e.status == 404 and NO_ROWS in e.reason:
                    log.warning("marine-institute: %s has no rows in [%s, %s); skipped", sid, self.start, self.end)
                    continue
                raise
            fetched_ids.append(sid)
        return self.load(cache_dir, only=fetched_ids)

    def _ids(self, stations: dict, only: list[str] | None) -> list[str]:
        if only is None:
            return sorted(stations)
        unknown = [s for s in only if s not in stations]
        if unknown:
            raise ValueError(f"{unknown!r} is not a station of {DATASET} ({len(stations)} listed)")
        return list(dict.fromkeys(only))

    def load(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        """FetchResult from responses already in the cache. Without `only`, every listed
        station whose response for this window is cached."""
        cache_dir = Path(cache_dir)
        files = [_input_file(cache_dir, "info.csv"), _input_file(cache_dir, "stations.csv")]
        served = served_licence((cache_dir / "info.csv").read_text())
        if served != SERVED_LICENCE:
            raise ValueError(f"{DATASET} info.csv states licence {served!r}, not {SERVED_LICENCE!r}, which "
                             "the adapter's licence (CC BY 4.0, commercial use) rests on")
        stations = _stations((cache_dir / "stations.csv").read_text())
        ids = self._ids(stations, only)
        if only is None:
            ids = [s for s in ids if (cache_dir / obs_cache_name(s, self.start, self.end)).is_file()]
        items = {}
        for sid in ids:
            f = _input_file(cache_dir, obs_cache_name(sid, self.start, self.end))
            parsed = parse_obs_csv((cache_dir / f.path).read_text())
            if parsed.station_id != sid:
                raise ValueError(f"{f.path} holds station {parsed.station_id!r}, not {sid!r}")
            files.append(f)
            items[sid] = {"obs": parsed, "file": f}
        version = files[0].fetched_at[:10].replace("-", "")
        return FetchResult(source=self.source, source_version=version, cache_dir=cache_dir,
                           files=tuple(files), items=items)

    # -- contract ------------------------------------------------------------------------------
    def convention(self) -> dict:
        # Gauge observations carry no constants; this is the convention the fit (G03b/G03c,
        # engine TCD tables) gives the sets made from them.
        return {"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "tcd"}

    def licence(self) -> dict:
        return {"licence_id": "marine-institute-cc-by-4.0", "spdx": "CC-BY-4.0",
                "provider": "Marine Institute",
                "attribution": "Contains Irish National Tide Gauge Network data from the Marine Institute, "
                               "licensed under CC BY 4.0. Modified by OpenTideConstants: the harmonic constants "
                               "are OpenTideConstants' fit to the water levels, not a Marine Institute product.",
                "citation": "Marine Institute. Irish National Tide Gauge Network Real Time Data "
                            "(ERDDAP dataset IrishNationalTideGaugeNetwork).",
                # the dataset page that states its licence (NC_GLOBAL license, checked on every load)
                "url": f"{ERDDAP}/info/{DATASET}/index.html",
                # CC BY 4.0 allows commercial use with attribution
                "commercial_use": True}

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """station id -> SHA-256 of the station's normalised rows (stamp, both heights, QC flag)."""
        keys = {}
        for sid, item in fetched.items.items():
            o: ParsedObs = item["obs"]
            text = (fetched.cache_dir / item["file"].path).read_text()
            rows = [r for r in csv.reader(io.StringIO(text))][2:]
            norm = json.dumps({"station_id": sid, "lat": o.lat, "lon": o.lon,
                               "rows": [[r[0], r[4], r[5], r[6]] for r in rows if r]},
                              sort_keys=True, separators=(",", ":"))
            keys[sid] = hashlib.sha256(norm.encode()).hexdigest()
        return keys

    def records(self, fetched: FetchResult) -> Iterable[GaugeRecord]:
        for sid, item in sorted(fetched.items.items()):
            o: ParsedObs = item["obs"]
            yield GaugeRecord(
                record_id=f"{self.source}:{sid}", source=self.source,
                source_version=fetched.source_version, contributor="Marine Institute", originator=True,
                licence_class="CC-BY-4.0", lat=o.lat, lon=o.lon, gauge_type="Coastal",
                sampling="instantaneous", interval_min=_interval_min(o.times_s),
                declared_time_base="utc_instant", member_sha256=item["file"].sha256,
                times_s=o.times_s, heights_m=o.heights_m, good=o.good)

    def sets(self, fetched: FetchResult) -> Iterable[SourceSet]:
        return ()

    def published_datums(self, fetched: FetchResult) -> dict[str, dict]:
        """record_id -> {"datum": LAT above OD Malin (no msl_offset_m: the fit's Z0 goes with it),
        "notes": the file it was read from}, for every OD Malin record whose LAT - OD Malin difference is constant."""
        out = {}
        for sid, item in sorted(fetched.items.items()):
            o: ParsedObs = item["obs"]
            if o.datum != "OD Malin" or len(o.lat_above_od_malin) != 1:
                if o.datum == "OD Malin" and len(o.lat_above_od_malin) > 1:
                    log.warning("marine-institute: %s: LAT - OD Malin is not constant (%s); no published datum",
                                sid, o.lat_above_od_malin[:5])
                continue
            out[f"{self.source}:{sid}"] = {
                "datum": {"zero": "vertical_datum", "zero_name": "OD Malin",
                          "named": {"lat": o.lat_above_od_malin[0]}, "basis": {"lat": published("LAT")}},
                "notes": {"lat_from": item["file"].path}}
        return out
