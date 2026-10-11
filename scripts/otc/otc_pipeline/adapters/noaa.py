"""NOAA CO-OPS tide-prediction constants and subordinate offsets (manifest A-NOAA, PP §3).

Upstream (metadata API, no key; NOAA rate-limits, so every request goes through PoliteFetcher):

- `stations.json?type=tidepredictions`: every tide-prediction station, `type` R (harmonic
  reference) or S (subordinate, with `reference_id`).
- `stations/<id>/harcon.json?units=metric` per R station: the harmonic constituents. We publish
  `phase_GMT` (Greenwich), never `phase_local` (local standard time); the canary checks this.
- `stations/<id>/tidepredoffsets.json` per S station: the high/low time and height offsets from
  its reference station, which become `subordinate_offsets`. Fetched in NOAA's native units:
  `heightAdjustedType` R is a ratio (unitless); F is an additive offset in feet, which we convert
  exactly to metres (x 0.3048) and publish as type A. (`units=metric` would round the converted
  value to 0.01 m, e.g. 0.1 ft -> 0.03 m.)
- `stations/<id>/datums.json?units=metric` per station (R and S): NOAA's published tidal datums
  (datums spec §4.1 item 2, slot DT5a). Every level is given above the station datum STND; we
  re-express each above the station's MLLW, NOAA's chart datum: the set's (or the subordinate's
  `subordinate_offsets.datum`) `zero` is `chart_datum` with `zero_name` MLLW, `chart_datum` is
  `mllw`, and a reference set's `msl_offset_m` is MSL - MLLW. Each level's basis is kind
  `published`, method `source`, NOAA's name; the tidal levels carry NOAA's epoch (`epoch`, or the
  single DatumAnalysisPeriod when `epoch` is not a year range) and NOAA's control station
  (`ctrlStation`, id only); LAT and HAT carry neither (NOAA computes them from predictions over
  its own 40-year window, not over the epoch, and not by comparison with the control); STND and
  the orthometric datums (NAVD88, PRVD02, ...) are geodetic ties and carry neither. The ranges
  (GT, MN, DHQ, DLQ) are differences of stored levels and HWI/LWI are times: not taken. A station
  with no MLLW gets no datum (no chart datum to refer the levels to).

The data is a U.S. Government work (public domain). The constants are NOAA's own amplitudes and
Greenwich phases (a phase of 360 is written as 0); each speed is the release's astro-table row for
the constituent's name (constituent_speeds: within 1e-4 deg/h of NOAA's, else refused), and a
constituent with no row is left out and recorded in dropped_constituents. The offsets are NOAA's
numbers (additive heights converted from feet to metres). It is not official NOAA material.

Station hints: name, lat and lon are NOAA's as served (some NOAA positions are wrong; see
`PLACE_OVERRIDES`). The IANA time zone comes from the position (timezonefinder, pinned), the
ISO alpha-3 country from that zone's tzdata `zone.tab` entry. NOAA's `state` and `timezonecorr`
are not used for either: both are wrong for some stations.

Stations that cannot make a valid set are left out and listed by `skipped()` with the reason: an
R station with no constituents, an S station whose reference is itself or is not an R station
with constants, or whose offsets are not R (ratio) or A (additive) offsets.

    uv run python -m otc_pipeline.adapters.noaa fetch --cache DIR [--only ID ...]
    uv run python -m otc_pipeline.adapters.noaa canary --datestamp D [--phase-field phase_local]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sys
import tempfile
from functools import lru_cache
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Callable, Iterable

from otc_pipeline.adapter import FetchResult, InputFile, SourceSet
from otc_pipeline.constituent_speeds import TableSpeeds, dropped_no_row
from otc_pipeline.datums import period, published
from otc_pipeline.http import PoliteFetcher

log = logging.getLogger(__name__)

API = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
LIST_URL = f"{API}/stations.json"
LIST_PARAMS = {"type": "tidepredictions"}
LIST_NAME = "stations.json"
UNITS = {"units": "metric"}
MIN_INTERVAL_S = 1.0          # NOAA rate-limits; one request a second per host

PHASE_FIELDS = ("phase_GMT", "phase_local")
# Stable fields of a station-list entry (the rest are URLs or always null).
STATION_FIELDS = ("id", "name", "lat", "lng", "type", "reference_id", "state", "timezonecorr", "timemeridian")
FT_TO_M = 0.3048
HEIGHT_TYPES = {"R": "R", "F": "A"}   # NOAA heightAdjustedType -> schema height_adjusted_type
# NOAA datum name -> OTC key (datums spec rule 10: canonical where NOAA's definition is the
# canonical one, otherwise NOAA's name in lower case).
TIDAL_DATUMS = {"MHHW": "mhhw", "MHW": "mhw", "DTL": "dtl", "MTL": "mtl", "MSL": "msl", "MLW": "mlw",
                "MLLW": "mllw"}
GEODETIC_DATUMS = {"STND": "stnd", "NAVD88": "navd88", "GUVD04": "guvd04", "NMVD03": "nmvd03",
                   "PRVD02": "prvd02", "VIVD09": "vivd09"}
# Not levels, or not datums of the station: ranges (differences of stored levels), intervals
# (hours), and the Columbia River Datum offset (CRD_OFFSET, a river-gauge correction).
DATUMS_NOT_TAKEN = frozenset({"GT", "MN", "DHQ", "DLQ", "HWI", "LWI", "CRD_OFFSET"})
_EPOCH_RE = re.compile(r"(\d{4})-(\d{4})")
_PERIOD_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4}) - (\d{2})/(\d{2})/(\d{4})")

# NOAA id -> (alpha-3, IANA zone), where NOAA's position gives no land zone or the wrong country.
# The one NOAA licence object: noaa and noaa-currents publish under it, and a release holds one object per
# licence_id, so both adapters return this one.
NOAA_LICENCE = {"licence_id": "noaa-public-domain", "spdx": "LicenseRef-PublicDomain-USGov",
                "provider": "NOAA Center for Operational Oceanographic Products and Services (CO-OPS)",
                "attribution": "NOAA CO-OPS data (a U.S. Government work, not subject to copyright). Units "
                               "converted to SI (feet to metres, cm/s to m/s) and constituent speeds set to the "
                               "release's astro table by OpenTideConstants; not official NOAA material.",
                "url": "https://tidesandcurrents.noaa.gov/disclaimers.html",
                # a U.S. Government work is public domain (17 U.S.C. 105): commercial use is allowed
                "commercial_use": True}

PLACE_OVERRIDES: dict[str, tuple[str, str, str]] = {
    "TPT2737": ("UMI", "Etc/GMT+12", "Howland Island: no land zone at NOAA's position"),
    "TPT2739": ("UMI", "Etc/GMT+11", "Palmyra Island: no land zone; nearest land zone is Kiribati"),
    "TPT2891": ("NIU", "Pacific/Niue", "Niue: NOAA longitude has the wrong sign (lands in Vanuatu)"),
    "TPT2893": ("TON", "Pacific/Tongatapu", "Neiafu, Tonga: NOAA longitude is wrong (6.0 E)"),
    "TPT2897": ("TON", "Pacific/Tongatapu", "Nomuka, Tonga: NOAA longitude has the wrong sign (lands near Fiji)"),
    "TWC0279": ("ECU", "America/Guayaquil", "San Lorenzo, Ecuador: NOAA longitude has the wrong sign"),
}
# Ocean (Etc/*) zone with no override: the nearest land zone within this many degrees.
LAND_SEARCH_MAX_DEG = 1.5

# ISO 3166-1 alpha-2 -> alpha-3 for the countries NOAA's stations are in (and their neighbours).
# A station in a country not listed here fails loudly; add the row.
ALPHA3 = {
    "AG": "ATG", "AI": "AIA", "AS": "ASM", "AW": "ABW", "BB": "BRB", "BL": "BLM", "BM": "BMU",
    "BQ": "BES", "BS": "BHS", "BZ": "BLZ", "CA": "CAN", "CK": "COK", "CL": "CHL", "CO": "COL",
    "CR": "CRI", "CU": "CUB", "CW": "CUW", "DM": "DMA", "DO": "DOM", "EC": "ECU", "FJ": "FJI",
    "FM": "FSM", "GD": "GRD", "GP": "GLP", "GT": "GTM", "GU": "GUM", "HN": "HND", "HT": "HTI",
    "ID": "IDN", "IO": "IOT", "JM": "JAM", "JP": "JPN", "KI": "KIR", "KN": "KNA", "KY": "CYM",
    "LC": "LCA", "MF": "MAF", "MH": "MHL", "MP": "MNP", "MQ": "MTQ", "MS": "MSR", "MX": "MEX",
    "NC": "NCL", "NI": "NIC", "NR": "NRU", "NU": "NIU", "NZ": "NZL", "PA": "PAN", "PE": "PER",
    "PF": "PYF", "PG": "PNG", "PH": "PHL", "PN": "PCN", "PR": "PRI", "PW": "PLW", "SB": "SLB",
    "SV": "SLV", "SX": "SXM", "TC": "TCA", "TK": "TKL", "TO": "TON", "TT": "TTO", "TV": "TUV",
    "UM": "UMI", "US": "USA", "VC": "VCT", "VE": "VEN", "VG": "VGB", "VI": "VIR", "VU": "VUT",
    "WF": "WLF", "WS": "WSM",
}


def canonical_sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def _zone_countries() -> dict[str, str]:
    """IANA zone -> ISO alpha-2, from the pinned tzdata's zone.tab."""
    out = {}
    for line in (resource_files("tzdata") / "zoneinfo" / "zone.tab").read_text().splitlines():
        if line and not line.startswith("#"):
            code, _coords, zone = line.split("\t")[:3]
            out[zone] = code
    return out


@lru_cache(maxsize=1)
def _finder():
    from timezonefinder import TimezoneFinder
    return TimezoneFinder()


def _land_zone_near(lat: float, lon: float) -> str | None:
    """The land zone nearest (lat, lon) within LAND_SEARCH_MAX_DEG, on rings 0.05 deg apart."""
    tf = _finder()
    steps = int(round(LAND_SEARCH_MAX_DEG / 0.05))
    for i in range(1, steps + 1):
        r = 0.05 * i
        for k in range(24):
            a = 2 * math.pi * k / 24
            la = max(-90.0, min(90.0, lat + r * math.sin(a)))
            lo = (lon + r * math.cos(a) / max(0.2, math.cos(math.radians(lat))) + 180.0) % 360.0 - 180.0
            z = tf.timezone_at_land(lng=lo, lat=la)
            if z:
                return z
    return None


@lru_cache(maxsize=None)
def place(station_id: str, lat: float, lon: float) -> tuple[str, str]:
    """(alpha-3 country, IANA zone) of a station, from its position (see PLACE_OVERRIDES)."""
    if station_id in PLACE_OVERRIDES:
        country, zone, _why = PLACE_OVERRIDES[station_id]
        return country, zone
    zone = _finder().timezone_at(lng=lon, lat=lat)
    if zone is None or zone.startswith("Etc/"):
        zone = _land_zone_near(lat, lon)
        if zone is None:
            raise ValueError(f"NOAA {station_id} ({lat}, {lon}): no land time zone within "
                             f"{LAND_SEARCH_MAX_DEG} deg; add it to PLACE_OVERRIDES")
    a2 = _zone_countries().get(zone)
    if a2 is None or a2 not in ALPHA3:
        raise ValueError(f"NOAA {station_id} ({lat}, {lon}): zone {zone} has country {a2!r}, "
                         "which is not in ALPHA3; add the row")
    return ALPHA3[a2], zone


def _datum_epoch(body: dict) -> dict | None:
    """The epoch NOAA states for the tidal levels: `epoch` when it is a year range (1983-2001 is the
    NTDE; 2012-2016 and 2002-2006 are NOAA's modified 5-year epochs), else the one
    DatumAnalysisPeriod (e.g. "Special" datums from a single series); None when neither is stated
    (an empty or "0000-0000" epoch with several analysis periods)."""
    m = _EPOCH_RE.fullmatch(str(body.get("epoch") or "").strip())
    if m and m[1] != "0000" and int(m[1]) <= int(m[2]):
        name = "NTDE 1983-2001" if (m[1], m[2]) == ("1983", "2001") else f"NOAA epoch {m[1]}-{m[2]}"
        return period(f"{m[1]}-01-01", f"{m[2]}-12-31", name)
    periods = body.get("DatumAnalysisPeriod") or []
    if len(periods) == 1:
        p = _PERIOD_RE.fullmatch(str(periods[0]).strip())
        if p:
            return period(f"{p[3]}-{p[1]}-{p[2]}", f"{p[6]}-{p[4]}-{p[5]}", "NOAA datum analysis period")
    return None


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def noaa_datum(body: dict, *, with_msl_offset: bool) -> tuple[dict | None, list[str]]:
    """NOAA's datums.json (units=metric) as an OTC datum above the station's MLLW, and the names
    NOAA gives that are not taken. (None, ...) when NOAA gives no MLLW or the units are not metres."""
    if not isinstance(body, dict) or body.get("units") != "meters":
        return None, []
    levels = {d["name"]: float(d["value"]) for d in body.get("datums") or []
              if isinstance(d, dict) and _number(d.get("value"))}
    if "MLLW" not in levels:
        return None, []
    mllw = levels["MLLW"]
    epoch = _datum_epoch(body)
    ctrl = str(body.get("ctrlStation") or "").strip().split(" ", 1)[0] or None
    named, basis, not_taken = {}, {}, []
    for src, v in levels.items():
        if src in TIDAL_DATUMS:
            key, b = TIDAL_DATUMS[src], published(src, epoch=epoch, control_source_id=ctrl)
        elif src in GEODETIC_DATUMS:
            key, b = GEODETIC_DATUMS[src], published(src)
        else:
            if src not in DATUMS_NOT_TAKEN:
                not_taken.append(src)
            continue
        named[key], basis[key] = round(v - mllw, 6), b
    for src, key in (("LAT", "lat"), ("HAT", "hat")):
        if _number(body.get(src)):
            named[key], basis[key] = round(float(body[src]) - mllw, 6), published(src)
    datum = {"zero": "chart_datum", "zero_name": "MLLW", "chart_datum": "mllw", "named": named, "basis": basis}
    if with_msl_offset and "msl" in named:
        datum = {"msl_offset_m": named["msl"], **datum}
    return datum, sorted(not_taken)


def _height(off: dict, key: str) -> float:
    """A height offset: a ratio as given (R), or an additive offset in feet as metres (F)."""
    v = off[key]
    return v if off["heightAdjustedType"] == "R" else round(float(v) * FT_TO_M, 6)


class NoaaAdapter:
    source = "noaa"
    source_type = "official"
    quantity = "water_level"
    adapter_version = "1.1.0"   # 1.1: published datums (DT5a)
    # Boston, Seattle, San Francisco (the G9 stations) and Hull, MA (subordinate to Boston).
    contract_sample = ("8443970", "9447130", "9414290", "8444351")

    def __init__(self, phase_field: str = "phase_GMT",
                 fetcher_factory: Callable[[Path], PoliteFetcher] | None = None, speeds: TableSpeeds | None = None):
        """`phase_field="phase_local"` is the canary's RED test flag only: it publishes NOAA's
        local-time phases as if they were Greenwich, the mistake the canary must catch. `speeds`:
        the astro rows' speeds (constituent_speeds; default: from the TCD dump)."""
        if phase_field not in PHASE_FIELDS:
            raise ValueError(f"phase_field {phase_field!r} is not one of {PHASE_FIELDS}")
        self.phase_field = phase_field
        self._fetcher_factory = fetcher_factory or (
            lambda d: PoliteFetcher(d, min_interval_s=MIN_INTERVAL_S))
        self._speeds = speeds

    @property
    def speeds(self) -> TableSpeeds:
        """The astro rows' speeds (KP0b), from the TCD dump unless given; read when first needed."""
        if self._speeds is None:
            self._speeds = TableSpeeds.from_tcd_dump()
        return self._speeds

    # -- fetch ----------------------------------------------------------------------------
    def fetch(self, cache_dir: Path, only: list[str] | None = None,
              progress: Callable[[int, int], None] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        f = self._fetcher_factory(cache_dir)
        lst = f.get(LIST_URL, params=LIST_PARAMS, cache_name=LIST_NAME)
        stations = {s["id"]: s for s in json.loads(lst.bytes())["stations"]}
        wanted = list(stations) if only is None else list(dict.fromkeys(only))
        unknown = [sid for sid in wanted if sid not in stations]
        if unknown:
            raise ValueError(f"not NOAA tide-prediction stations: {', '.join(unknown)}")
        # A subordinate needs its reference in the same fetch.
        todo = list(wanted)
        for sid in wanted:
            ref = stations[sid].get("reference_id")
            if stations[sid]["type"] == "S" and ref in stations and ref not in todo:
                todo.append(ref)
        files: list[InputFile] = [lst.input_file()]
        items = {}
        for n, sid in enumerate(sorted(todo), 1):
            st = stations[sid]
            if st["type"] == "R":
                got = f.get(f"{API}/stations/{sid}/harcon.json", params=UNITS, cache_name=f"harcon/{sid}.json")
                key = "harcon"
            else:
                got = f.get(f"{API}/stations/{sid}/tidepredoffsets.json", cache_name=f"offsets/{sid}.json")
                key = "offsets"
            dat = f.get(f"{API}/stations/{sid}/datums.json", params=UNITS, cache_name=f"datums/{sid}.json")
            files += [got.input_file(), dat.input_file()]
            items[sid] = {"station": st, key: json.loads(got.bytes()), "datums": json.loads(dat.bytes()),
                          "input_sha256": [lst.sha256, got.sha256, dat.sha256]}
            if progress is not None:
                progress(n, len(todo))
        version = min(fl.fetched_at for fl in files)[:10].replace("-", "")
        return FetchResult(source=self.source, source_version=version, cache_dir=cache_dir,
                           files=tuple(files), items=items)

    # -- metadata -------------------------------------------------------------------------
    def convention(self) -> dict:
        return {"convention_id": "noaa-harcon-gmt", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "noaa-37"}

    def licence(self) -> dict:
        return dict(NOAA_LICENCE)

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """Per station: SHA-256 of the normalised JSON of its list entry and its harcon or offsets
        (stable fields only, sorted keys)."""
        out = {}
        for sid, it in fetched.items.items():
            body = it.get("harcon", it.get("offsets"))
            body = {k: v for k, v in body.items() if k != "self"} if isinstance(body, dict) else body
            dat = it.get("datums")
            dat = {k: v for k, v in dat.items() if k not in ("self", "disclaimers")} if isinstance(dat, dict) else dat
            out[sid] = canonical_sha256({"station": {k: it["station"].get(k) for k in STATION_FIELDS},
                                         "data": body, "datums": dat})
        return out

    def records(self, fetched: FetchResult) -> Iterable:
        return ()

    # -- sets -----------------------------------------------------------------------------
    def skipped(self, fetched: FetchResult) -> dict[str, str]:
        """Station id -> why it makes no set."""
        items = fetched.items
        out: dict[str, str] = {}
        for sid, it in sorted(items.items()):
            if it["station"]["type"] == "R":
                if it["harcon"].get("units") != "meters":
                    out[sid] = f"harcon units {it['harcon'].get('units')!r}, not meters"
                elif not it["harcon"].get("HarmonicConstituents"):
                    out[sid] = "no harmonic constituents"
        for sid, it in sorted(items.items()):
            if it["station"]["type"] != "S":
                continue
            off = it["offsets"]
            ref = off.get("refStationId") or it["station"].get("reference_id")
            if off.get("type") != "S":
                out[sid] = f"listed as S but its offsets say type {off.get('type')!r}"
            elif ref == sid:
                out[sid] = "subordinate to itself"
            elif ref not in items or items[ref]["station"]["type"] != "R":
                out[sid] = f"reference {ref!r} is not a fetched R station"
            elif ref in out:
                out[sid] = f"reference {ref} is skipped ({out[ref]})"
            elif off.get("heightAdjustedType") not in HEIGHT_TYPES:
                out[sid] = f"heightAdjustedType {off.get('heightAdjustedType')!r} is not R or F"
            elif any(not isinstance(off.get(k), (int, float)) for k in
                     ("heightOffsetHighTide", "heightOffsetLowTide", "timeOffsetHighTide", "timeOffsetLowTide")):
                out[sid] = "an offset is missing"
        return out

    def _hint(self, st: dict, kind: str) -> dict:
        lat, lon = float(st["lat"]), float(st["lng"])
        country, zone = place(st["id"], lat, lon)
        return {"name": st["name"].strip(), "country": country, "lat": lat, "lon": lon, "type": kind,
                "timezone": zone, "aliases": {"noaa": st["id"]}}

    def _constituent(self, sid: str, c: dict) -> dict | None:
        """The constituent with its astro row's speed (NOAA's own within 1e-4 deg/h, else SpeedDeviation),
        or None when the release has no astro row for its name."""
        speed = self.speeds.speed(c["name"], float(c["speed"]), where=f"{sid}: ")
        if speed is None:
            return None
        phase = float(c[self.phase_field]) % 360.0
        return {"name": c["name"], "source_name": c["name"], "speed_deg_per_hour": speed,
                "amplitude_m": float(c["amplitude"]), "phase_deg": 0.0 if phase >= 360.0 else phase}

    def sets(self, fetched: FetchResult) -> Iterable[SourceSet]:
        skipped = self.skipped(fetched)
        lic, conv = self.licence()["licence_id"], self.convention()["convention_id"]
        for sid, it in sorted(fetched.items.items()):
            if sid in skipped:
                continue
            st = it["station"]
            if st["type"] == "R":
                cons, dropped = [], []
                for c in it["harcon"]["HarmonicConstituents"]:
                    con = self._constituent(sid, c)
                    cons.append(con) if con is not None else dropped.append(dropped_no_row(c["name"]))
                datum, not_taken = noaa_datum(it.get("datums"), with_msl_offset=True)
                if not_taken:   # provenance is a closed list: a name NOAA adds is logged, not published
                    log.warning("noaa %s: datum names not taken: %s", sid, ", ".join(not_taken))
                yield SourceSet(
                    station_hint=self._hint(st, "reference"),
                    constant_set={
                        "source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                        "source_record_id": sid, "source_version": fetched.source_version,
                        "convention_id": conv, "licence_id": lic, "qc_status": "ok",
                        "constituents": cons, **({"dropped_constituents": dropped} if dropped else {}),
                        **({"datum": datum} if datum else {}),
                        "provenance": {"adapter_version": self.adapter_version,
                                       "input_sha256": list(it["input_sha256"]),
                                       "selection_reason": "NOAA CO-OPS harmonic constants; speeds as the "
                                                           "release's astro table"},
                    })
            else:
                off = it["offsets"]
                datum, _not_taken = noaa_datum(it.get("datums"), with_msl_offset=False)
                yield SourceSet(
                    station_hint=self._hint(st, "subordinate"), constant_set=None,
                    subordinate_offsets={
                        "reference_source_record_id": off.get("refStationId") or st["reference_id"],
                        "time_offset_high_min": off["timeOffsetHighTide"],
                        "time_offset_low_min": off["timeOffsetLowTide"],
                        "height_offset_high": _height(off, "heightOffsetHighTide"),
                        "height_offset_low": _height(off, "heightOffsetLowTide"),
                        "height_adjusted_type": HEIGHT_TYPES[off["heightAdjustedType"]],
                        "licence_id": lic, **({"datum": datum} if datum else {}),
                    })


# -- CLI ----------------------------------------------------------------------------------------

G9_GESLA = {   # NOAA reference station -> its NOAA record in GESLA 4.1 (the G9 stations of G03c)
    "8443970": "boston-8443970-usa-noaa",
    "9447130": "seattle-9447130-usa-noaa",
    "9414290": "san_francisco-9414290-usa-noaa",
    "8724580": "key_west-8724580-usa-noaa",
}
CANARY_STATIONS = ("8443970", "9447130", "9414290")


def run_canary(datestamp: str, phase_field: str, stations, cache_dir: Path):
    from otc_pipeline.canary import canary
    from otc_pipeline import gesla_sets
    adapter = NoaaAdapter(phase_field=phase_field)
    fetched = adapter.fetch(cache_dir, only=list(stations))
    noaa_sets = list(adapter.sets(fetched))
    # the GESLA side is compared, never published: comparator sets, which carry no licence
    gesla = gesla_sets.load_comparator_sets(datestamp, only=[G9_GESLA[s] for s in stations if s in G9_GESLA])
    return canary("noaa", noaa_sets, gesla)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m otc_pipeline.adapters.noaa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fetch", help="fetch (or refresh the cache of) stations; print a summary")
    fp.add_argument("--cache", type=Path, required=True)
    fp.add_argument("--only", nargs="+", metavar="ID")
    fp.add_argument("--manifest", action="store_true", help="write MANIFEST.json into the cache")
    cp = sub.add_parser("canary", help="the convention canary against our GESLA fits")
    cp.add_argument("--datestamp", required=True, help="build whose gesla fits to read")
    cp.add_argument("--phase-field", default="phase_GMT", choices=PHASE_FIELDS)
    cp.add_argument("--cache", type=Path, default=None)
    cp.add_argument("stations", nargs="*", default=list(CANARY_STATIONS))
    a = ap.parse_args(argv)
    if a.cmd == "canary":
        res = run_canary(a.datestamp, a.phase_field, a.stations,
                         a.cache or Path(tempfile.mkdtemp(prefix="noaa-canary-")))
        print(json.dumps({"phase_field": a.phase_field, "convention_canary": res.convention_canary(),
                          **res.to_dict()}, sort_keys=True, indent=1))
        return 0 if res.passed else 1
    adapter = NoaaAdapter()

    def progress(n: int, total: int) -> None:
        if n % 100 == 0 or n == total:
            print(f"  {n}/{total}", file=sys.stderr, flush=True)
    fetched = adapter.fetch(a.cache, only=a.only, progress=progress)
    sets = list(adapter.sets(fetched))
    skipped = adapter.skipped(fetched)
    kinds = {"R": 0, "S": 0}
    for it in fetched.items.values():
        kinds[it["station"]["type"]] += 1
    print(json.dumps({"source_version": fetched.source_version, "files": len(fetched.files),
                      "stations": len(fetched.items), "R": kinds["R"], "S": kinds["S"],
                      "sets_reference": sum(1 for s in sets if s.constant_set is not None),
                      "sets_subordinate": sum(1 for s in sets if s.subordinate_offsets is not None),
                      "skipped": skipped}, sort_keys=True, indent=1))
    if a.manifest:
        man = fetched.manifest(dataset="NOAA CO-OPS tide-prediction harmonic constants and offsets",
                               licence="LicenseRef-PublicDomain-USGov", source_version=fetched.source_version,
                               citation="NOAA CO-OPS metadata API, https://api.tidesandcurrents.noaa.gov/mdapi/prod/",
                               archive=None, adapter_version=adapter.adapter_version)
        (Path(a.cache) / "MANIFEST.json").write_text(json.dumps(man, sort_keys=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    from otc_pipeline.adapters.noaa import main as _main
    sys.exit(_main())
