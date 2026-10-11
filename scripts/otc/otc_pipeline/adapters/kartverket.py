"""Kartverket (Norwegian Hydrographic Service) harmonic constants (manifest A-KV, PP P3).

Per permanent station, `tideapi.php?tide_request=constituents&stationcode=<CODE>` serves the
station's harmonic constants with amplitudes in cm and phase lags referred to the time zone
`utcoffset="+01:00"` (Norwegian standard time). The adapter converts amplitudes to metres and
phases to Greenwich with `canary.to_greenwich` under the convention it declares
(`phase_reference "local"`, `utc_offset_hours 1`: g_greenwich = g_local - speed x 1 h). The
per-source canary against our GESLA fits of the NHS records (Bergen, Trondheim, Stavanger, ...)
is what fixes that sign: read as Greenwich, every phase is off by speed x 1 h.

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
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from otc_pipeline.adapter import FetchResult, SourceSet
from otc_pipeline.canary import to_greenwich
from otc_pipeline.constituent_speeds import TableSpeeds, dropped_no_row
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
             "phase_deg": float(c.get("phaseangle")), "speed": float(c.get("speed"))}
            for c in cs.iter("constituent")]
    if not cons:
        raise ValueError(f"{code}: no constituents")
    return {"unit": unit, "utcoffset": off,
            "location": {"name": loc.get("name"), "lat": float(loc.get("latitude")),
                         "lon": float(loc.get("longitude"))},
            "observations": None if obs is None else {"start": obs.get("start"), "end": obs.get("end")},
            "constituents": cons}


class KartverketAdapter:
    source = "kartverket"
    source_type = "official"
    quantity = "water_level"
    adapter_version = "1"
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
            files.append(got.input_file())
            items[code] = {"station": stations[code], **parse_constituents(got.bytes(), code),
                           "input_sha256": [sl.input_file().sha256, got.sha256]}
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
            norm = {k: it[k] for k in ("unit", "utcoffset", "location", "observations", "constituents")}
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
                # the served phase is referred to UTC+1 with the served speed
                cons.append({"name": c["name"], "speed_deg_per_hour": speed,
                             "amplitude_m": round(c["amplitude_cm"] / 100.0, 6),
                             "phase_deg": round(to_greenwich(c["phase_deg"], c["speed"], conv), 3)})
            cs = {"source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                  "source_record_id": code, "source_version": fetched.source_version,
                  "convention_id": conv["convention_id"], "licence_id": self.licence()["licence_id"],
                  "qc_status": "ok", "constituents": cons,
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
