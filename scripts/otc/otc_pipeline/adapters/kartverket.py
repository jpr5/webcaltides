"""Kartverket (Norwegian Hydrographic Service) harmonic constants (manifest A-KV, PP P3).

Per permanent station, `tideapi.php?tide_request=constituents&stationcode=<CODE>` serves the
station's harmonic constants with amplitudes in cm and phase lags referred to the time zone
`utcoffset="+01:00"` (Norwegian standard time). The adapter converts amplitudes to metres and
phases to Greenwich with `canary.to_greenwich` under the convention it declares
(`phase_reference "local"`, `utc_offset_hours 1`: g_greenwich = g_local - speed x 1 h). The
per-source canary against our GESLA fits of the NHS records (Bergen, Trondheim, Stavanger, ...)
is what fixes that sign: read as Greenwich, every phase is off by speed x 1 h.

Datums (datums spec §4.1 item 3, slot DT5b). `tideapi.php?tide_request=stationlevels&stationcode=
<CODE>` gives the station's levels in cm above chart datum (`reflevel="CD"`). The adapter writes
HAT, MHWS, MHW, MHWN, MSL, MLWN, MLW, MLWS, LAT, CD and NN2000 (keys `hat` ... `nn2000`), in
metres above CD: the set's `zero` is `chart_datum`, `zero_name` CD, `chart_datum` `cd` (CD is
Kartverket's published chart datum; it is not always LAT: south of Utsira it is below LAT).
Each basis is kind `published`, method `source`, Kartverket's code; only MSL states an epoch
(its `epoch` attribute, 1996-2014, or where that is absent the same years in the level's
name, "Mean Sea Level (1996-2014)"). The storm-surge return levels and the observed extremes
(type RETURN and OBS) are not tidal datums and are not taken. `msl_offset_m` (Z0 above CD) is
Kartverket's MSL: the constants carry no mean level, and with Z0 = MSL the constants reproduce
Kartverket's own tide tables over a year (monthly mean bias within 0.1 cm at Bodo and Bergen,
ADPORT sa-fix evidence).

SA and S1 are served with IOS's arguments (h - p1 and T + p1 - 90, XDO ZZAZZYZ and AAYZZAA), not
the TCD's (h, T): their phases are converted (`ios_definitions`), after the UTC+1 correction. A
served speed is checked against its astro row to Kartverket's precision (SPEED_PRECISION_DEG_PER_HOUR,
after the IOS p1 term), so another p1-type definition difference cannot pass unseen.

`phase_reference="greenwich_utc"` is the test flag that reads the served phases as Greenwich
(the RED of the proof). Only the default convention ships.

Raw responses are cached by PoliteFetcher in the cache directory with a MANIFEST.json; the
cache lives in the work bucket under cache/kartverket/, never in git.

    uv run python -m otc_pipeline.adapters.kartverket --cache-dir DIR [CODE ...]

fetches (all permanent stations, or the named ones) and prints one line per set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from otc_pipeline.adapter import FetchResult, SourceSet
from otc_pipeline.canary import to_greenwich
from otc_pipeline.constituent_speeds import TableSpeeds, dropped_no_row
from otc_pipeline.datums import period, published
from otc_pipeline.ios_definitions import IOS_TO_TCD, KARTVERKET_XDO, P1_RATE_DEG_PER_HOUR, to_tcd_phase
from otc_pipeline.http import PoliteFetcher

URL = "https://vannstand.kartverket.no/tideapi.php"
STATIONLIST_PARAMS = {"tide_request": "stationlist", "type": "perm", "lang": "en"}
SERVED_UTC_OFFSET = "+01:00"     # what the constituents response must declare
SERVED_UNIT = "cm"
SPDX = "CC-BY-4.0"
# The licence pointer each constituents response carries (<meta licenseurl>): the licence() claim (CC BY
# 4.0) rests on it, so a response that points anywhere else is refused.
SERVED_LICENCE_URLS = ("http://kartverket.no/data/lisens/",)
SVALBARD_MIN_LAT = 74.0          # Ny-Ålesund (NYA) is in Svalbard: country SJM
# stationlevels code -> OTC datum key (datums spec §4.1 item 3, rule 10)
LEVEL_KEYS = {"HAT": "hat", "MHWS": "mhws", "MHW": "mhw", "MHWN": "mhwn", "MSL": "msl", "MLWN": "mlwn",
              "MLW": "mlw", "MLWS": "mlws", "LAT": "lat", "CD": "cd", "NN2000": "nn2000"}
LEVELS_REFLEVEL = "CD"


def _iso_utc(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stationlist(data: bytes) -> dict[str, dict]:
    """code -> {name, lat, lon} for every location in a stationlist response."""
    out = {}
    for loc in ET.fromstring(data).iter("location"):
        out[loc.get("code")] = {"name": loc.get("name"), "lat": float(loc.get("latitude")),
                                "lon": float(loc.get("longitude"))}
    return out


def parse_constituents(data: bytes, code: str) -> dict:
    """A constituents response as {unit, utcoffset, location, observations, constituents}.
    Raises ValueError when it is not the station's constants in cm at UTC+1."""
    root = ET.fromstring(data)
    meta = root.find("meta")
    pointer = None if meta is None else meta.get("licenseurl")
    if pointer not in SERVED_LICENCE_URLS:
        raise ValueError(f"{code}: the response's licence pointer is {pointer!r}, not one of {SERVED_LICENCE_URLS} "
                         "(the CC BY 4.0 the adapter claims)")
    cs = root.find("constituents")
    if cs is None:
        raise ValueError(f"{code}: no <constituents> in the response")
    unit, off = cs.get("unit"), cs.get("utcoffset")
    if unit != SERVED_UNIT:
        raise ValueError(f"{code}: constituents unit {unit!r}, expected {SERVED_UNIT!r}")
    if off != SERVED_UTC_OFFSET:
        raise ValueError(f"{code}: constituents utcoffset {off!r}, expected {SERVED_UTC_OFFSET!r}")
    loc = cs.find("location")
    if loc is None or loc.get("code") != code:
        raise ValueError(f"{code}: response is for {None if loc is None else loc.get('code')!r}")
    obs = cs.find("observations")
    cons = [{"name": c.get("name"), "amplitude_cm": float(c.get("amplitude")),
             "phase_deg": float(c.get("phaseangle")), "speed": float(c.get("speed")), "xdo": c.get("doodson")}
            for c in cs.iter("constituent")]
    if not cons:
        raise ValueError(f"{code}: no constituents")
    return {"unit": unit, "utcoffset": off,
            "location": {"name": loc.get("name"), "lat": float(loc.get("latitude")),
                         "lon": float(loc.get("longitude"))},
            "observations": None if obs is None else {"start": obs.get("start"), "end": obs.get("end")},
            "constituents": cons}


def parse_levels(data: bytes, code: str) -> dict:
    """A stationlevels response as {code: {"value_m", "epoch"}} for the tidal levels and CD/NN2000.
    Raises ValueError unless it is the station's levels in cm above CD under the licence the
    adapter claims, with CD among them."""
    root = ET.fromstring(data)
    meta = root.find("meta")
    pointer = None if meta is None else meta.get("licenseurl")
    if pointer not in SERVED_LICENCE_URLS:
        raise ValueError(f"{code}: stationlevels licence pointer is {pointer!r}, not one of {SERVED_LICENCE_URLS}")
    ll = root.find("locationlevel")
    if ll is None:
        raise ValueError(f"{code}: no <locationlevel> in the stationlevels response")
    if ll.get("unit") != SERVED_UNIT or ll.get("reflevel") != LEVELS_REFLEVEL:
        raise ValueError(f"{code}: stationlevels unit {ll.get('unit')!r} / reflevel {ll.get('reflevel')!r}, "
                         f"expected {SERVED_UNIT!r} / {LEVELS_REFLEVEL!r}")
    loc = ll.find("location")
    if loc is None or loc.get("code") != code:
        raise ValueError(f"{code}: stationlevels response is for {None if loc is None else loc.get('code')!r}")
    out = {}
    for r in ll.iter("reflevel"):
        c = r.get("code")
        if c not in LEVEL_KEYS:
            continue
        if c in out:
            raise ValueError(f"{code}: level {c} is listed twice")
        # the epoch Kartverket states: the `epoch` attribute, or else "(YYYY-YYYY)" in the level's name
        # (some stations give it only there, e.g. BOH "Mean Sea Level (1996-2014)")
        named_epoch = re.search(r"\((\d{4}-\d{4})\)", r.get("name") or "")
        out[c] = {"value_m": round(float(r.get("value")) / 100.0, 6),
                  "epoch": r.get("epoch") or (named_epoch[1] if named_epoch else None)}
    if "CD" not in out:
        raise ValueError(f"{code}: stationlevels has no CD")
    if out["CD"]["value_m"] != 0.0:
        raise ValueError(f"{code}: CD is {out['CD']['value_m']} m above the reference level CD")
    return out


def levels_datum(levels: dict) -> dict:
    """The set's datum from parse_levels: every level above CD, Kartverket's code as source_name."""
    named, basis = {}, {}
    for c, lv in levels.items():
        epoch = None
        if lv["epoch"]:
            a, _, b = lv["epoch"].partition("-")
            if not (len(a) == len(b) == 4 and a.isdigit() and b.isdigit()):
                raise ValueError(f"level {c}: epoch {lv['epoch']!r} is not YYYY-YYYY")
            epoch = period(f"{a}-01-01", f"{b}-12-31", f"Kartverket {c} {a}-{b}")
        named[LEVEL_KEYS[c]], basis[LEVEL_KEYS[c]] = lv["value_m"], published(c, epoch=epoch)
    return {"zero": "chart_datum", "zero_name": "CD", "chart_datum": "cd", "named": named, "basis": basis}


# Kartverket gives speeds to 1e-8 deg/h; its own rounding of the TCD-equal definitions stays below 3e-7
SPEED_PRECISION_DEG_PER_HOUR = 1e-6


def _with_z0(datum: dict | None) -> dict | None:
    """Z0 above CD (msl_offset_m) is Kartverket's MSL above CD."""
    if datum and "msl" in (datum.get("named") or {}):
        return {**datum, "msl_offset_m": datum["named"]["msl"]}
    return datum


class KartverketAdapter:
    source = "kartverket"
    source_type = "official"
    quantity = "water_level"
    adapter_version = "2"   # 2: published datums (DT5b)
    contract_sample = ("BGO", "TRD", "SVG")

    def __init__(self, phase_reference: str = "local", speeds: TableSpeeds | None = None):
        if phase_reference not in ("local", "greenwich_utc"):
            raise ValueError(f"phase_reference {phase_reference!r}")
        self.phase_reference = phase_reference
        self._speeds = speeds   # the astro rows' speeds (constituent_speeds; default: from the TCD dump)

    @property
    def speeds(self) -> TableSpeeds:
        """The astro rows' speeds (KP0b), from the TCD dump unless given; read when first needed."""
        if self._speeds is None:
            self._speeds = TableSpeeds.from_tcd_dump()
        return self._speeds

    # -- fetch ------------------------------------------------------------------------------
    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        f = PoliteFetcher(cache_dir)
        sl = f.get(URL, params=STATIONLIST_PARAMS, cache_name="stationlist.xml")
        stations = parse_stationlist(sl.bytes())
        codes = sorted(stations) if only is None else list(only)
        unknown = [c for c in codes if c not in stations]
        if unknown:
            raise KeyError(f"not permanent Kartverket stations: {unknown}")
        files, items = [sl.input_file()], {}
        for code in codes:
            got = f.get(URL, params={"tide_request": "constituents", "stationcode": code, "lang": "en"},
                        cache_name=f"constituents/{code}.xml")
            lv = f.get(URL, params={"tide_request": "stationlevels", "stationcode": code, "lang": "en"},
                       cache_name=f"stationlevels/{code}.xml")
            files += [got.input_file(), lv.input_file()]
            items[code] = {"station": stations[code], **parse_constituents(got.bytes(), code),
                           "levels": parse_levels(lv.bytes(), code),
                           "input_sha256": [sl.input_file().sha256, got.sha256, lv.sha256]}
        version = min(fl.fetched_at for fl in files)[:10].replace("-", "")
        fetched = FetchResult(source=self.source, source_version=version, cache_dir=cache_dir,
                              files=tuple(files), items=items)
        manifest = fetched.manifest(
            dataset="Kartverket harmonic constants (tideapi.php constituents, permanent stations)",
            licence=SPDX, citation="Kartverket (Norwegian Mapping Authority), https://vannstand.kartverket.no/",
            archive=f"cache/{self.source}/")
        (cache_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        return fetched

    # -- convention and licence --------------------------------------------------------------
    def convention(self) -> dict:
        if self.phase_reference == "greenwich_utc":     # the RED test flag
            return {"convention_id": "kartverket-read-as-greenwich", "phase_reference": "greenwich_utc",
                    "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                    "constituent_table_version": "tcd"}
        return {"convention_id": "kartverket-utc+1", "phase_reference": "local", "utc_offset_hours": 1,
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "tcd"}

    def licence(self) -> dict:
        return {"licence_id": "kartverket-cc-by-4.0", "spdx": SPDX, "provider": "Kartverket",
                # Kartverket's terms ask for the credit "© Kartverket" and a link; CC BY 4.0 for a note of changes
                "attribution": "© Kartverket (https://kartverket.no). Tide constants from Kartverket's tide API "
                               "(vannstand.kartverket.no), CC BY 4.0. Modified by OpenTideConstants: amplitudes "
                               "converted from cm to metres, phase lags converted from UTC+1 to Greenwich, "
                               "constituent speeds set to the release's astro table, constituents with no "
                               "astro-table row left out (see each set's dropped_constituents).",
                "url": "https://kartverket.no/api-og-data/vilkar-for-bruk",
                # CC BY 4.0 allows commercial use with attribution
                "commercial_use": True}

    # -- outputs ------------------------------------------------------------------------------
    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """code -> SHA-256 of the station's normalised constants (not the raw bytes)."""
        keys = {}
        for code, it in fetched.items.items():
            norm = {k: it[k] for k in ("unit", "utcoffset", "location", "observations", "constituents", "levels")}
            keys[code] = hashlib.sha256(json.dumps(norm, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return keys

    def records(self, fetched: FetchResult):
        return ()

    def sets(self, fetched: FetchResult):
        conv = self.convention()
        for code, it in sorted(fetched.items.items()):
            st = it["station"]
            cons, dropped = [], []
            for c in it["constituents"]:
                # the astro row's speed (Kartverket's within 1e-4 deg/h, else SpeedDeviation); a name
                # with no row (GAM2) is left out and recorded: its f and u cannot be checked
                speed = self.speeds.speed(c["name"], c["speed"], where=f"{code}: ")
                if speed is None:
                    dropped.append(dropped_no_row(c["name"]))
                    continue
                # IOS's SA and S1 (served with their XDO code) get the TCD's argument; every served speed
                # is its row's (plus IOS's p1 term) to Kartverket's precision
                k = 0
                if c["name"] in IOS_TO_TCD:
                    if c.get("xdo") != KARTVERKET_XDO[c["name"]]:
                        raise ValueError(f"{code}: {c['name']} served with XDO {c.get('xdo')!r}, not IOS's "
                                         f"{KARTVERKET_XDO[c['name']]!r}: no phase conversion is known for it")
                    k = IOS_TO_TCD[c["name"]][0]
                want = speed + k * P1_RATE_DEG_PER_HOUR
                if abs(c["speed"] - want) > SPEED_PRECISION_DEG_PER_HOUR:
                    raise ValueError(f"{code}: {c['name']} served at {c['speed']} deg/h, its astro row "
                                     f"{speed} (+ IOS p1 term {k}) is {want:.8f}: a definition the adapter "
                                     "does not know")
                # the served phase is referred to UTC+1 with the served speed
                g = to_tcd_phase(c["name"], to_greenwich(c["phase_deg"], c["speed"], conv))
                cons.append({"name": c["name"], "speed_deg_per_hour": speed,
                             "amplitude_m": round(c["amplitude_cm"] / 100.0, 6),
                             "phase_deg": round(g, 3) % 360.0})
            cs = {"source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                  "source_record_id": code, "source_version": fetched.source_version,
                  "convention_id": conv["convention_id"], "licence_id": self.licence()["licence_id"],
                  "qc_status": "ok", "constituents": cons, "datum": _with_z0(levels_datum(it["levels"])),
                  "provenance": {"adapter_version": self.adapter_version, "input_sha256": it["input_sha256"]}}
            if dropped:
                cs["dropped_constituents"] = dropped
            obs = it["observations"]
            if obs and obs.get("start") and obs.get("end"):
                cs["record_span"] = {"start": _iso_utc(obs["start"]), "end": _iso_utc(obs["end"])}
            svalbard = st["lat"] > SVALBARD_MIN_LAT
            yield SourceSet(
                station_hint={"name": st["name"], "country": "SJM" if svalbard else "NOR",
                              "lat": st["lat"], "lon": st["lon"], "type": "reference",
                              "timezone": "Arctic/Longyearbyen" if svalbard else "Europe/Oslo",
                              "aliases": {"kartverket": code}},
                constant_set=cs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.adapters.kartverket")
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("codes", nargs="*")
    a = ap.parse_args(argv)
    ad = KartverketAdapter()
    fetched = ad.fetch(a.cache_dir, only=a.codes or None)
    for s in ad.sets(fetched):
        m2 = next((c for c in s.constant_set["constituents"] if c["name"] == "M2"), None)
        print(s.constant_set["source_record_id"], s.station_hint["name"], len(s.constant_set["constituents"]),
              "M2", None if m2 is None else (m2["amplitude_m"], m2["phase_deg"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
