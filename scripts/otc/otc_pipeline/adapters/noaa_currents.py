"""NOAA CO-OPS tidal-current constants and subordinate current offsets (manifest A-NOAAC).

Upstream (metadata API, no key; NOAA rate-limits, so every request goes through PoliteFetcher):

- `stations.json?type=currentpredictions&units=metric`: one row per station bin. `type` is
  H (harmonic), S (subordinate) or W (weak and variable; NOAA publishes no predictions for it),
  `currbin` the bin, `depth` metres, `depthType` S (below surface), B (below chart datum) or
  U (unknown). These are NOAA's definitions (MDAPI documentation, "Current Prediction").
- `stations/<id>/harcon.json?units=metric` per station with an H bin: the ellipse constants of
  every bin (`binNbr`): major- and minor-axis amplitude (cm/s) and Greenwich phase
  (`majorPhaseGMT`, `minorPhaseGMT`), the major-axis azimuth `azi` (degrees true), and the bin's
  mean current along each axis (`majorMeanSpeed`, `minorMeanSpeed`, cm/s; repeated on every row).
- `stations/<id>_<bin>/currentpredictionoffsets.json` per bin: for an H bin the mean flood and
  ebb directions; for an S bin the reference station and bin, four time adjustments (minutes)
  and the flood and ebb amplitude ratios.

What a set holds. One `constant_set` (quantity `current`, `constituents` []) per station with an
H bin; one `current_bins` entry per H bin: NOAA's numbers with amplitudes divided by 100 (m/s)
and Greenwich phases (a phase of 360 is written as 0). The depth is the station list's (it is the
depth NOAA's predictions report; harcon's `binDepth` can differ, e.g. ACT1616 2.13 m vs 4.57 m).
The bin's mean current (`majorMeanSpeed`, `minorMeanSpeed`, repeated on every harcon row) is
written, signed, as `mean_major_ms` and `mean_minor_ms` (since schema 0.5; format 1.0 too). It is not optional:
NOAA's predictions include it (at ACT1616, 7.7 cm/s moves the slacks by about 10 minutes).

`default_current_bin` is the first bin NOAA lists for the station, which is its shallowest.

Subordinate stations (S bins) get `current_offsets`, one per bin, naming the reference by its
NOAA id (`reference_source_record_id`; the build maps it to the OTC id). W bins make nothing;
stations and bins that cannot make a valid set are listed by `skipped()` with the reason.

    uv run python -m otc_pipeline.adapters.noaa_currents fetch --cache DIR [--only ID ...] [--manifest]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from functools import lru_cache
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Callable, Iterable

from otc_pipeline.adapter import FetchResult, InputFile, SourceSet
from otc_pipeline.adapters.noaa import NOAA_LICENCE
from otc_pipeline.constituent_speeds import TableSpeeds, dropped_no_row
from otc_pipeline.http import PoliteFetcher

API = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
LIST_URL = f"{API}/stations.json"
LIST_PARAMS = {"type": "currentpredictions", "units": "metric"}
LIST_NAME = "stations.json"
UNITS = {"units": "metric"}
HARCON_UNITS = "meters, centimeters/second"
MIN_INTERVAL_S = 1.0          # NOAA rate-limits; one request a second per host

# Stable fields of a station-list row (the rest are URLs or always null).
STATION_FIELDS = ("id", "name", "lat", "lng", "type", "currbin", "depth", "depthType", "timezone_offset")
HARCON_FIELDS = ("binNbr", "binDepth", "constNum", "constituentName", "majorAmplitude", "majorPhaseGMT",
                 "minorAmplitude", "minorPhaseGMT", "majorMeanSpeed", "minorMeanSpeed", "azi")
OFFSET_FIELDS = ("refStationId", "refStationBin", "meanFloodDir", "meanEbbDir", "mfcTimeAdjMin",
                 "sbeTimeAdjMin", "mecTimeAdjMin", "sbfTimeAdjMin", "mfcAmpAdj", "mecAmpAdj")
# NOAA depthType -> schema current_depth_type. NOAA's B is "below chart datum" (MDAPI docs).
DEPTH_TYPES = {"S": "below_surface", "B": "below_chart_datum", "U": "unknown"}

# NOAA's 37 constituents in harcon order (constNum), with the speeds NOAA's tide harcon gives
# (deg/h; the current harcon gives none).
NOAA_SPEEDS = {
    "M2": 28.984104, "S2": 30.0, "N2": 28.43973, "K1": 15.041069, "M4": 57.96821, "O1": 13.943035,
    "M6": 86.95232, "MK3": 44.025173, "S4": 60.0, "MN4": 57.423832, "NU2": 28.512583, "S6": 90.0,
    "MU2": 27.968208, "2N2": 27.895355, "OO1": 16.139101, "LAM2": 29.455626, "S1": 15.0,
    "M1": 14.496694, "J1": 15.5854435, "MM": 0.5443747, "SSA": 0.0821373, "SA": 0.0410686,
    "MSF": 1.0158958, "MF": 1.0980331, "RHO": 13.471515, "Q1": 13.398661, "T2": 29.958933,
    "R2": 30.041067, "2Q1": 12.854286, "P1": 14.958931, "2SM2": 31.015896, "M3": 43.47616,
    "L2": 29.528479, "2MK3": 42.92714, "K2": 30.082138, "M8": 115.93642, "MS4": 58.984104,
}
NOAA_ORDER = tuple(NOAA_SPEEDS)
DROP_FLAGS = (None, "bins", "azimuth", "mean")   # proof RED flags (NoaaCurrentsAdapter(drop=...))
CONSTITUENT_TABLE_VERSION = "noaa-37"

# NOAA id -> (alpha-3, IANA zone, why), where NOAA's position gives the wrong place.
PLACE_OVERRIDES: dict[str, tuple[str, str, str]] = {
    "SAV2304": ("USA", "America/New_York", "Savannah River Approach: NOAA longitude has the wrong sign (lands in China)"),
    "SAV2305": ("USA", "America/New_York", "Cockspur Island: NOAA longitude has the wrong sign (lands in China)"),
    "SAV2326": ("USA", "America/New_York", "Skidaway River: NOAA longitude has the wrong sign (lands in China)"),
}

# ISO 3166-1 alpha-2 -> alpha-3 for the countries NOAA current stations are in.
ALPHA3 = {"US": "USA", "PR": "PRI", "VI": "VIR", "CA": "CAN", "GU": "GUM", "MP": "MNP", "AS": "ASM",
          "UM": "UMI", "MX": "MEX", "BS": "BHS"}
LAND_SEARCH_MAX_DEG = 1.5


def canonical_sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


# -- place (time zone and country from the position) ------------------------------------------
# The same rule as the A-NOAA adapter (tzdata zone.tab country of the timezonefinder zone, the
# nearest land zone for an ocean zone); fold the two into one helper when both are merged.

@lru_cache(maxsize=1)
def _zone_countries() -> dict[str, str]:
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
    tf = _finder()
    for i in range(1, int(round(LAND_SEARCH_MAX_DEG / 0.05)) + 1):
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
                             f"{LAND_SEARCH_MAX_DEG} deg")
    a2 = _zone_countries().get(zone)
    if a2 not in ALPHA3:
        raise ValueError(f"NOAA {station_id} ({lat}, {lon}): zone {zone} has country {a2!r}, "
                         "which is not in ALPHA3; add the row")
    return ALPHA3[a2], zone


# -- conversion -------------------------------------------------------------------------------

def _ms(cm_s) -> float:
    """cm/s to m/s. NOAA gives cm/s to 2 decimals, so 6 decimals keep every digit (and no
    float noise such as 7.72 / 100 = 0.07719999999999999)."""
    return round(float(cm_s) / 100.0, 6)


def _phase(value) -> float:
    p = float(value) % 360.0
    return 0.0 if p >= 360.0 else p


def bin_errors(rows: list[dict]) -> list[str]:
    """Why a bin's harcon rows cannot make a current_bin (empty list = they can)."""
    errs = []
    names = [r.get("constituentName") for r in rows]
    for r in rows:
        n, k = r.get("constituentName"), r.get("constNum")
        if n not in NOAA_SPEEDS:
            errs.append(f"constituent {n!r} has no NOAA speed")
        elif not isinstance(k, int) or k < 1 or k > len(NOAA_ORDER) or NOAA_ORDER[k - 1] != n:
            errs.append(f"constituent {n!r} has constNum {k!r}, not NOAA's {NOAA_ORDER.index(n) + 1}")
        for f in ("majorAmplitude", "majorPhaseGMT", "minorAmplitude", "minorPhaseGMT", "azi"):
            if not isinstance(r.get(f), (int, float)):
                errs.append(f"constituent {n!r}: {f} is {r.get(f)!r}")
    if len(set(names)) != len(names):
        errs.append("a constituent is listed twice")
    for f in ("azi", "majorMeanSpeed", "minorMeanSpeed"):
        if len({r.get(f) for r in rows}) > 1:
            errs.append(f"{f} differs between the bin's rows")
    return errs


class NoaaCurrentsAdapter:
    source = "noaa-currents"
    source_type = "official"
    quantity = "current"
    adapter_version = "1.0.0"
    alias_system = "noaa"             # station_hint.aliases system (schema has no noaa-currents)
    # Pollock Rip Channel (one bin), Boston Harbor Deer Island Light (three bins), and Eastport
    # Friar Roads (subordinate to EPT0003 bin 11).
    contract_sample = ("ACT1616", "BOS1101", "ACT0091")

    def __init__(self, drop: str | None = None,
                 fetcher_factory: Callable[[Path], PoliteFetcher] | None = None, speeds: TableSpeeds | None = None):
        """`drop` is the proof's RED test flag only: "bins" merges every bin into the first
        (the bin number and depth are lost), "azimuth" writes azimuth 0 for every bin, "mean"
        leaves out the mean current (mean_major_ms, mean_minor_ms)."""
        if drop not in DROP_FLAGS:
            raise ValueError(f"drop {drop!r} is not one of {DROP_FLAGS}")
        self.drop = drop
        self._fetcher_factory = fetcher_factory or (
            lambda d: PoliteFetcher(d, min_interval_s=MIN_INTERVAL_S))
        self._speeds = speeds   # the astro rows' speeds (constituent_speeds; default: from the TCD dump)

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
        rows: dict[str, list[dict]] = {}
        for r in json.loads(lst.bytes())["stations"]:
            rows.setdefault(r["id"], []).append(r)
        wanted = list(rows) if only is None else list(dict.fromkeys(only))
        unknown = [sid for sid in wanted if sid not in rows]
        if unknown:
            raise ValueError(f"not NOAA current-prediction stations: {', '.join(unknown)}")
        todo = list(wanted)
        files: list[InputFile] = [lst.input_file()]
        items: dict[str, dict] = {}
        n = 0
        while n < len(todo):   # a subordinate's references join the fetch
            sid = todo[n]
            n += 1
            st_rows = rows[sid]
            it = {"rows": st_rows, "offsets": {}, "input_sha256": [lst.sha256]}
            if any(r["type"] == "H" for r in st_rows):
                got = f.get(f"{API}/stations/{sid}/harcon.json", params=UNITS, cache_name=f"harcon/{sid}.json")
                files.append(got.input_file())
                it["harcon"] = json.loads(got.bytes())
                it["input_sha256"].append(got.sha256)
            for r in st_rows:
                if r["type"] == "W":
                    continue
                key = f"{sid}_{r['currbin']}"
                got = f.get(f"{API}/stations/{key}/currentpredictionoffsets.json",
                            cache_name=f"offsets/{key}.json")
                files.append(got.input_file())
                off = json.loads(got.bytes())
                it["offsets"][r["currbin"]] = off
                it["input_sha256"].append(got.sha256)
                ref = off.get("refStationId")
                if r["type"] == "S" and ref in rows and ref not in todo:
                    todo.append(ref)
            items[sid] = it
            if progress is not None:
                progress(n, len(todo))
        version = min(fl.fetched_at for fl in files)[:10].replace("-", "")
        return FetchResult(source=self.source, source_version=version, cache_dir=cache_dir,
                           files=tuple(files), items=items)

    # -- metadata -------------------------------------------------------------------------
    def convention(self) -> dict:
        return {"convention_id": "noaa-currents-harcon-gmt", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": CONSTITUENT_TABLE_VERSION}

    def licence(self) -> dict:
        return dict(NOAA_LICENCE)   # one object for both NOAA sources (one licence_id per release)

    def _speed(self, name: str) -> float | None:
        """The astro row's speed for a NOAA constituent (NOAA's within 1e-4 deg/h, else SpeedDeviation),
        or None when the release has no row for it."""
        return self.speeds.speed(name, NOAA_SPEEDS[name])

    def constituent_table(self) -> list[dict]:
        """The release constituent table rows this source's sets use (NOAA's 37, with an astro row),
        at the astro rows' speeds."""
        return [{"constituent_table_version": CONSTITUENT_TABLE_VERSION, "name": n, "speed_deg_per_hour": s}
                for n, s in ((n, self._speed(n)) for n in sorted(NOAA_SPEEDS)) if s is not None]

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """Per station bin `<id>_<bin>`: SHA-256 of the normalised JSON of its list row, its
        harcon rows and its offsets (stable fields, sorted keys). Per station `<id>`: SHA-256 of
        its bins' keys, so a set's upstream id has a key too."""
        out = {}
        for sid, it in fetched.items.items():
            harcon = (it.get("harcon") or {}).get("HarmonicConstituents") or []
            bins = {}
            for r in it["rows"]:
                b = r["currbin"]
                off = it["offsets"].get(b)
                bins[f"{sid}_{b}"] = canonical_sha256({
                    "station": {k: r.get(k) for k in STATION_FIELDS},
                    "harcon": [{k: h.get(k) for k in HARCON_FIELDS} for h in harcon if h.get("binNbr") == b],
                    "offsets": {k: off.get(k) for k in OFFSET_FIELDS} if isinstance(off, dict) else None})
            out.update(bins)
            out[sid] = canonical_sha256(dict(sorted(bins.items())))
        return out

    def records(self, fetched: FetchResult) -> Iterable:
        return ()

    # -- sets -----------------------------------------------------------------------------
    def _h_bins(self, it: dict) -> dict[int, list[dict]]:
        harcon = (it.get("harcon") or {}).get("HarmonicConstituents") or []
        out: dict[int, list[dict]] = {}
        for r in it["rows"]:
            if r["type"] == "H":
                out[r["currbin"]] = [h for h in harcon if h.get("binNbr") == r["currbin"]]
        return out

    def skipped(self, fetched: FetchResult) -> dict[str, str]:
        """`<id>` (whole station) or `<id>_<bin>` -> why it makes nothing."""
        out: dict[str, str] = {}
        refs_ok: dict[str, set] = {}
        for sid, it in sorted(fetched.items.items()):
            types = {r["type"] for r in it["rows"]}
            for r in it["rows"]:
                if r["type"] == "W":
                    out[f"{sid}_{r['currbin']}"] = "W: weak and variable (NOAA publishes no predictions)"
            if "H" in types:
                units = (it.get("harcon") or {}).get("units")
                good = set()
                for b, hrows in self._h_bins(it).items():
                    if units != HARCON_UNITS:
                        out[f"{sid}_{b}"] = f"harcon units {units!r}, not {HARCON_UNITS!r}"
                    elif not hrows:
                        out[f"{sid}_{b}"] = "H bin with no harmonic constituents"
                    elif errs := bin_errors(hrows):
                        out[f"{sid}_{b}"] = "; ".join(errs)
                    else:
                        good.add(b)
                if good:
                    refs_ok[sid] = good
                else:
                    out[sid] = "no H bin with usable constants"
                if "S" in types:
                    for r in it["rows"]:
                        if r["type"] == "S":
                            out[f"{sid}_{r['currbin']}"] = "S bin of a station with H bins (one station " \
                                                           "is a reference or a subordinate, not both)"
            elif "S" not in types:
                out[sid] = "W only: weak and variable (NOAA publishes no predictions)"
        for sid, it in sorted(fetched.items.items()):
            if any(r["type"] == "H" for r in it["rows"]) or not any(r["type"] == "S" for r in it["rows"]):
                continue
            good = 0
            for r in it["rows"]:
                if r["type"] != "S":
                    continue
                key = f"{sid}_{r['currbin']}"
                off = it["offsets"].get(r["currbin"]) or {}
                ref, rbin = off.get("refStationId"), off.get("refStationBin")
                if not ref:
                    out[key] = "S bin with no reference station"
                elif ref == sid:
                    out[key] = "subordinate to itself"
                elif ref not in refs_ok:
                    out[key] = f"reference {ref!r} is not a fetched station with usable H bins"
                elif rbin not in refs_ok[ref]:
                    out[key] = f"reference bin {ref}_{rbin} is not a usable H bin"
                else:
                    good += 1
            if not good:
                out[sid] = "no S bin with a usable reference"
        return out

    def _hint(self, sid: str, rows: list[dict], kind: str) -> dict:
        r = rows[0]
        lat, lon = float(r["lat"]), float(r["lng"])
        country, zone = place(sid, lat, lon)
        return {"name": r["name"].strip(), "country": country, "lat": lat, "lon": lon, "type": kind,
                "timezone": zone, "aliases": {"noaa": sid}}

    def _bin(self, row: dict, hrows: list[dict], off: dict | None) -> dict:
        h0 = hrows[0]
        out = {"bin": row["currbin"], "depth_m": row["depth"], "depth_type": DEPTH_TYPES[row["depthType"]],
               "azimuth_deg": 0.0 if self.drop == "azimuth" else _phase(h0["azi"])}
        for k, f in (("mean_flood_dir_deg", "meanFloodDir"), ("mean_ebb_dir_deg", "meanEbbDir")):
            if isinstance(off, dict) and isinstance(off.get(f), (int, float)):
                out[k] = _phase(off[f])
        if self.drop != "mean":
            for k, f in (("mean_major_ms", "majorMeanSpeed"), ("mean_minor_ms", "minorMeanSpeed")):
                if isinstance(h0.get(f), (int, float)):
                    out[k] = _ms(h0[f])
        cons = [{"name": h["constituentName"], "source_name": h["constituentName"],
                 "speed_deg_per_hour": self._speed(h["constituentName"]),
                 "major_amplitude_ms": _ms(h["majorAmplitude"]),
                 "major_phase_deg": _phase(h["majorPhaseGMT"]),
                 "minor_amplitude_ms": _ms(h["minorAmplitude"]),
                 "minor_phase_deg": _phase(h["minorPhaseGMT"])}
                for h in sorted(hrows, key=lambda h: h["constNum"])]
        out["constituents"] = [c for c in cons if c["speed_deg_per_hour"] is not None]   # no astro row: dropped
        return out

    def _offset(self, row: dict, off: dict, lic: str) -> dict:
        out = {"bin": row["currbin"], "depth_m": row["depth"], "depth_type": DEPTH_TYPES[row["depthType"]],
               "reference_source_record_id": off["refStationId"], "reference_bin": off["refStationBin"],
               "time_adj_max_flood_min": off.get("mfcTimeAdjMin"),
               "time_adj_slack_before_ebb_min": off.get("sbeTimeAdjMin"),
               "time_adj_max_ebb_min": off.get("mecTimeAdjMin"),
               "time_adj_slack_before_flood_min": off.get("sbfTimeAdjMin"),
               "flood_amp_ratio": off.get("mfcAmpAdj"), "ebb_amp_ratio": off.get("mecAmpAdj"),
               "licence_id": lic}
        for k, f in (("mean_flood_dir_deg", "meanFloodDir"), ("mean_ebb_dir_deg", "meanEbbDir")):
            if isinstance(off.get(f), (int, float)):
                out[k] = _phase(off[f])
        return out

    def sets(self, fetched: FetchResult) -> Iterable[SourceSet]:
        skipped = self.skipped(fetched)
        lic, conv = self.licence()["licence_id"], self.convention()["convention_id"]
        for sid, it in sorted(fetched.items.items()):
            if sid in skipped:
                continue
            rows = it["rows"]
            if any(r["type"] == "H" for r in rows):
                hb = self._h_bins(it)
                bins = [self._bin(r, hb[r["currbin"]], it["offsets"].get(r["currbin"]))
                        for r in rows if r["type"] == "H" and f"{sid}_{r['currbin']}" not in skipped]
                if self.drop == "bins":   # RED flag: every bin's constants become the first bin's
                    first = bins[0]
                    bins = [{**first, "bin": b["bin"], "depth_m": None, "depth_type": "unknown"} for b in bins]
                no_row = sorted({h["constituentName"] for r in rows
                                 if r["type"] == "H" and f"{sid}_{r['currbin']}" not in skipped
                                 for h in hb[r["currbin"]] if self._speed(h["constituentName"]) is None})
                hint = self._hint(sid, rows, "reference")
                hint["default_current_bin"] = bins[0]["bin"]
                yield SourceSet(
                    station_hint=hint,
                    constant_set={
                        "source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                        "source_record_id": sid, "source_version": fetched.source_version,
                        "convention_id": conv, "licence_id": lic, "qc_status": "ok",
                        "constituents": [], "current_bins": bins,
                        **({"dropped_constituents": [dropped_no_row(n) for n in no_row]} if no_row else {}),
                        "provenance": {"adapter_version": self.adapter_version,
                                       "input_sha256": list(it["input_sha256"]),
                                       "selection_reason": "NOAA CO-OPS current harmonic constants; speeds as "
                                                           "the release's astro table"},
                    })
            else:
                offs = [self._offset(r, it["offsets"][r["currbin"]], lic) for r in rows
                        if r["type"] == "S" and f"{sid}_{r['currbin']}" not in skipped]
                hint = self._hint(sid, rows, "subordinate")
                hint["default_current_bin"] = offs[0]["bin"]
                yield SourceSet(station_hint=hint, constant_set=None,
                                subordinate_offsets={"current_offsets": offs})


# -- CLI ----------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m otc_pipeline.adapters.noaa_currents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fetch", help="fetch (or refresh the cache of) stations; print a summary")
    fp.add_argument("--cache", type=Path, required=True)
    fp.add_argument("--only", nargs="+", metavar="ID")
    fp.add_argument("--manifest", action="store_true", help="write MANIFEST.json into the cache")
    fp.add_argument("--skipped", type=Path, help="write the skipped ids and reasons as JSON here")
    a = ap.parse_args(argv)
    adapter = NoaaCurrentsAdapter()

    def progress(n: int, total: int) -> None:
        if n % 100 == 0 or n == total:
            print(f"  {n}/{total}", file=sys.stderr, flush=True)
    fetched = adapter.fetch(a.cache, only=a.only, progress=progress)
    sets = list(adapter.sets(fetched))
    skipped = adapter.skipped(fetched)
    kinds = {"H": 0, "S": 0, "W": 0}
    for it in fetched.items.values():
        for r in it["rows"]:
            kinds[r["type"]] += 1
    reasons: dict[str, int] = {}
    for why in skipped.values():
        k = why.split(":")[0] if why.startswith(("W", "harcon units")) else why.split(" ")[0:4]
        k = k if isinstance(k, str) else " ".join(k)
        reasons[k] = reasons.get(k, 0) + 1
    print(json.dumps({"source_version": fetched.source_version, "files": len(fetched.files),
                      "stations": len(fetched.items), "rows": kinds,
                      "sets_reference": sum(1 for s in sets if s.constant_set is not None),
                      "bins_reference": sum(len(s.constant_set["current_bins"]) for s in sets if s.constant_set),
                      "sets_subordinate": sum(1 for s in sets if s.constant_set is None),
                      "bins_subordinate": sum(len(s.subordinate_offsets["current_offsets"]) for s in sets
                                              if s.constant_set is None),
                      "skipped": len(skipped), "skipped_by_reason": reasons}, sort_keys=True, indent=1))
    if a.skipped:
        a.skipped.write_text(json.dumps(skipped, sort_keys=True, indent=1) + "\n")
    if a.manifest:
        man = fetched.manifest(dataset="NOAA CO-OPS tidal-current harmonic constants and offsets",
                               licence="LicenseRef-PublicDomain-USGov", source_version=fetched.source_version,
                               citation="NOAA CO-OPS metadata API, https://api.tidesandcurrents.noaa.gov/mdapi/prod/",
                               archive=None, adapter_version=adapter.adapter_version)
        (Path(a.cache) / "MANIFEST.json").write_text(json.dumps(man, sort_keys=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    from otc_pipeline.adapters.noaa_currents import main as _main
    sys.exit(_main())
