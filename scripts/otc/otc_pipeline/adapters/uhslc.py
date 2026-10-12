"""UHSLC Fast Delivery hourly sea level -> GaugeRecord (slot A-UH-a).

Endpoints (University of Hawaii Sea Level Center, legacy data portal):

- `https://uhslc.soest.hawaii.edu/data/meta.geojson`: one feature per station, with `uhslc_id`
  (integer; files and the OTC `uhslc` alias use it as 3 digits, e.g. 56 -> "056"), name, GLOSS
  id, country, `fd_span` (first and last Fast Delivery day, null when the station has none) and
  the point as [lon, lat] with lon in 0..360.
- `https://uhslc.soest.hawaii.edu/data/csv/fast/hourly/h{NNN}.csv`: the whole Fast Delivery
  hourly series of one station. No header; each line is `year,month,day,hour,sea_level`, the
  sea level in millimetres relative to the station zero, `-32767` for a missing hour (the
  `_FillValue` of the UHSLC ERDDAP hourly datasets), times in UTC (ERDDAP `time` units:
  seconds since 1970-01-01T00:00:00Z).

Time base: each value is stamped on the hour, UTC. UHSLC hourly values are either filtered from
the gauge's higher-rate samples with a symmetric filter centred on the hour, or a spot reading
on the hour (the JASL format's decimation codes 1 and 3; UHSLC does not mark which per file).
Neither has a lag, so the adapter declares `utc_instant` and `sampling="instantaneous"`. A
plain hour-mean stamped at the start of its hour (decimation code 2) would be
`utc_mean_start60`; the time-base audit in A-UH-b checks the declaration against comparators
instead of trusting it.

QC: Fast Delivery data have had UHSLC's first-level QC (released within 1-2 months); the CSV
carries no per-value flag, so `good` = not the missing-value code.

Station ids are kept on every record (`record_id = "uhslc-fd:<NNN>"`, `uhslc_id(record)`), so
the combined build can drop UHSLC records already in GESLA (most GESLA "UHSLC" files carry the
same id). The proof stations may be among those deduplicated.

Licence: UHSLC publishes no SPDX licence. Its ERDDAP licence text says the data "may be used and
redistributed for free but is not intended for legal use"; UHSLC asks users to cite the JASL
dataset and to note the date through which Fast Delivery data was obtained.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult
from otc_pipeline.http import PoliteFetcher

BASE = "https://uhslc.soest.hawaii.edu/data"
META_URL = f"{BASE}/meta.geojson"
CSV_URL = BASE + "/csv/fast/hourly/h{id}.csv"
MISSING = -32767
SPDX = "LicenseRef-UHSLC-FreeUse"
CITATION = ("Caldwell, P. C., M. A. Merrifield, P. R. Thompson (2015), Sea level measured by tide "
            "gauges from global oceans - the Joint Archive for Sea Level holdings (NCEI Accession "
            "0019568), Version 5.5, NOAA National Centers for Environmental Information, Dataset, "
            "doi:10.7289/V5V40S7W. Fast Delivery data from the University of Hawaii Sea Level Center.")


def station_id(uhslc_id: int | str) -> str:
    """The 3-digit UHSLC station id used in file names and the OTC `uhslc` alias."""
    n = int(uhslc_id)
    if not 0 <= n <= 999:
        raise ValueError(f"UHSLC id {uhslc_id!r} is not 0..999")
    return f"{n:03d}"


def normalised_sha256(body: bytes) -> str:
    """SHA-256 of the CSV lines with line endings and trailing blank lines normalised."""
    lines = [ln.strip() for ln in body.decode("ascii").splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def parse_hourly_csv(body: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(times_s int64 UTC as stamped, heights_m float64 with NaN for missing, good bool)."""
    rows = []
    for n, line in enumerate(body.decode("ascii").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) != 5:
            raise ValueError(f"line {n}: {len(parts)} fields, want 5 fields (year,month,day,hour,mm)")
        try:
            rows.append([int(p) for p in parts])
        except ValueError:
            raise ValueError(f"line {n}: {line!r} is not 5 integers") from None
    if not rows:
        raise ValueError("no data lines in the UHSLC hourly CSV")
    a = np.asarray(rows, dtype=np.int64)
    y, mo, d, hr, mm = a.T
    for name, v, lo, hi in (("month", mo, 1, 12), ("day", d, 1, 31), ("hour", hr, 0, 23)):
        bad = np.flatnonzero((v < lo) | (v > hi))
        if bad.size:
            raise ValueError(f"line {bad[0] + 1}: {name} {v[bad[0]]} is not {lo}..{hi}")
    month = ((y - 1970) * 12 + (mo - 1)).astype("datetime64[M]")
    day = month.astype("datetime64[D]") + (d - 1).astype("timedelta64[D]")
    over = np.flatnonzero(day.astype("datetime64[M]") != month)
    if over.size:
        raise ValueError(f"line {over[0] + 1}: day {d[over[0]]} is past the end of the month")
    times = day.astype("datetime64[s]").astype(np.int64) + hr * 3600
    good = mm != MISSING
    heights = np.where(good, mm / 1000.0, np.nan).astype(np.float64)
    return times.astype(np.int64), heights, good


class UhslcFdAdapter:
    source = "uhslc-fd"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "uhslc-fd-1"
    # Pago Pago (NOAA 1770000), Guam / Apra Harbor (NOAA 1630000), Kwajalein (NOAA 1820000)
    contract_sample = ("056", "053", "055")

    def __init__(self, min_interval_s: float = 1.0):
        self.min_interval_s = min_interval_s

    # -- fetch ------------------------------------------------------------------------------
    def fetch(self, cache_dir: Path, only: list[str] | None = None, refresh: bool = False) -> FetchResult:
        cache_dir = Path(cache_dir)
        fetcher = PoliteFetcher(cache_dir, min_interval_s=self.min_interval_s)
        meta_got = fetcher.get(META_URL, cache_name="meta.geojson", refresh=refresh)
        stations = {}
        for f in json.loads(meta_got.bytes())["features"]:
            p = f["properties"]
            if (p.get("fd_span") or {}).get("latest"):
                stations[station_id(p["uhslc_id"])] = f
        wanted = sorted(stations) if only is None else sorted({station_id(s) for s in only})
        missing = [s for s in wanted if s not in stations]
        if missing:
            raise ValueError(f"UHSLC ids {missing} have no Fast Delivery data in meta.geojson")
        files = [meta_got.input_file()]
        items = {}
        for sid in wanted:
            got = fetcher.get(CSV_URL.format(id=sid), cache_name=f"hourly/h{sid}.csv", refresh=refresh)
            files.append(got.input_file())
            items[sid] = {"feature": stations[sid], "file": got.input_file()}
        fetched = FetchResult(source=self.source, source_version=meta_got.fetched_at[:10].replace("-", ""),
                              cache_dir=cache_dir, files=tuple(files), items=items)
        manifest = fetched.manifest(
            dataset="UHSLC Fast Delivery hourly sea level (csv/fast/hourly) and station metadata",
            licence=SPDX, citation=CITATION, archive="cache/uhslc/")
        (cache_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        return fetched

    # -- convert ----------------------------------------------------------------------------
    def records(self, fetched: FetchResult) -> Iterable[GaugeRecord]:
        for sid, item in sorted(fetched.items.items()):
            f = item["file"]
            t, h, good = parse_hourly_csv((Path(fetched.cache_dir) / f.path).read_bytes())
            lon, lat = item["feature"]["geometry"]["coordinates"][:2]
            yield GaugeRecord(
                record_id=f"{self.source}:{sid}", source=self.source,
                source_version=fetched.source_version, contributor="UHSLC", originator=False,
                licence_class=SPDX, lat=float(lat), lon=((float(lon) + 180.0) % 360.0) - 180.0,
                gauge_type="Coastal", sampling="instantaneous", interval_min=60,
                declared_time_base="utc_instant", member_sha256=f.sha256,
                times_s=t, heights_m=h, good=good)

    def sets(self, fetched: FetchResult):
        return ()

    def uhslc_id(self, record: GaugeRecord) -> str:
        """The 3-digit UHSLC id of a record (for the dedupe against GESLA)."""
        prefix = f"{self.source}:"
        if not record.record_id.startswith(prefix):
            raise ValueError(f"{record.record_id!r} is not a {self.source} record")
        return station_id(record.record_id[len(prefix):])

    # -- metadata ---------------------------------------------------------------------------
    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        return {sid: normalised_sha256((Path(fetched.cache_dir) / item["file"].path).read_bytes())
                for sid, item in fetched.items.items()}

    def convention(self) -> dict:
        # Constants for this source come from the OTC fit (A-UH-b): Greenwich phases, UTC.
        return {"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "otc-fit"}

    def licence(self) -> dict:
        return {"licence_id": "uhslc-fd", "spdx": SPDX,
                "provider": "University of Hawaii Sea Level Center (UHSLC)",
                "citation": CITATION,
                "attribution": "Contains UHSLC Fast Delivery sea level data (University of Hawaii "
                               "Sea Level Center), free to use and redistribute, not for legal use.",
                "url": "https://uhslc.soest.hawaii.edu/datainfo/"}
