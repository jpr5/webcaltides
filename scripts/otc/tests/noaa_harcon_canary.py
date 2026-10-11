"""F3 proof: NOAA harcon (live) against our GESLA fits through the per-source canary.

`NoaaHarconProof` is a minimal NOAA adapter (A-NOAA builds the real one): it fetches
`stations/<id>.json` and `stations/<id>/harcon.json?units=metric` live through PoliteFetcher and
emits one set per station, converting the served phase to Greenwich with `canary.to_greenwich`
under the convention it declares. NOAA's `phase_GMT` is Greenwich, so the true convention is
`greenwich_utc`; `phase_reference="local"` is the test flag that mislabels it as local time at the
station's own UTC offset (`timezonecorr`), which shifts every phase by speed x offset.

    uv run python tests/noaa_harcon_canary.py --datestamp D [--phase-reference local] [STATION ...]

prints the canary result as JSON and exits 1 when the canary fails. It is not registered in
SOURCES and never ships.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from otc_pipeline.adapter import FetchResult, SourceSet
from otc_pipeline.adapters.noaa import NoaaAdapter
from otc_pipeline.canary import canary, to_greenwich
from otc_pipeline.gesla_sets import load_comparator_sets
from otc_pipeline.http import PoliteFetcher

API = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations"

# NOAA reference station -> its NOAA record in GESLA 4.1 (the G9 stations of G03c).
G9_GESLA = {
    "8443970": "boston-8443970-usa-noaa",
    "9447130": "seattle-9447130-usa-noaa",
    "9414290": "san_francisco-9414290-usa-noaa",
    "8724580": "key_west-8724580-usa-noaa",
}
PROOF_STATIONS = ("8443970", "9447130", "9414290")   # manifest F3: Boston, Seattle, San Francisco
TIMEZONES = {"8443970": "America/New_York", "9447130": "America/Los_Angeles",
             "9414290": "America/Los_Angeles", "8724580": "America/New_York"}


class NoaaHarconProof:
    source = "noaa"
    source_type = "official"
    quantity = "water_level"
    adapter_version = "f3-canary-proof"
    contract_sample = PROOF_STATIONS

    def __init__(self, phase_reference: str = "greenwich_utc"):
        if phase_reference not in ("greenwich_utc", "local"):
            raise ValueError(f"phase_reference {phase_reference!r}")
        self.phase_reference = phase_reference

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        f = PoliteFetcher(cache_dir)
        files, items = [], {}
        for sid in only or PROOF_STATIONS:
            st = f.get(f"{API}/{sid}.json", cache_name=f"station_{sid}.json")
            hc = f.get(f"{API}/{sid}/harcon.json", params={"units": "metric"}, cache_name=f"harcon_{sid}.json")
            files += [st.input_file(), hc.input_file()]
            items[sid] = {"station": json.loads(st.bytes())["stations"][0], "harcon": json.loads(hc.bytes()),
                          "input_sha256": [st.input_file().sha256, hc.input_file().sha256]}
        version = min(fl.fetched_at for fl in files)[:10].replace("-", "")
        return FetchResult(source=self.source, source_version=version, cache_dir=Path(cache_dir),
                           files=tuple(files), items=items)

    def convention_for(self, station: dict) -> dict:
        if self.phase_reference == "local":   # the deliberate mislabel
            return {"convention_id": "noaa-mislabelled-local", "phase_reference": "local",
                    "utc_offset_hours": float(station["timezonecorr"]), "v0_model": "schureman_tcd",
                    "nodal_handling": "f_u_at_prediction", "constituent_table_version": "noaa-37"}
        return self.convention()

    def convention(self) -> dict:
        return {"convention_id": "noaa-harcon-gmt", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "noaa-37"}

    def licence(self) -> dict:
        # the production NOAA adapter's licence (same data, same terms), with an attribution that says
        # what this proof adapter does: NOAA's served speeds, GMT phases re-referenced by the convention
        # under test, zero-amplitude constituents left out
        return {**NoaaAdapter().licence(),
                "attribution": "NOAA CO-OPS data (a U.S. Government work, not subject to copyright), with NOAA's "
                               "own speeds; zero-amplitude constituents left out by the OpenTideConstants canary "
                               "proof; not official NOAA material."}

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        return {sid: hashlib.sha256(json.dumps(it["harcon"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                for sid, it in fetched.items.items()}

    def records(self, fetched: FetchResult):
        return ()

    def sets(self, fetched: FetchResult):
        for sid, it in sorted(fetched.items.items()):
            st, hc = it["station"], it["harcon"]
            if hc.get("units") != "meters":
                raise ValueError(f"{sid}: harcon units {hc.get('units')!r}")
            conv = self.convention_for(st)
            cons = [{"name": c["name"], "speed_deg_per_hour": float(c["speed"]), "amplitude_m": float(c["amplitude"]),
                     "phase_deg": round(to_greenwich(float(c["phase_GMT"]), float(c["speed"]), conv), 3)}
                    for c in hc["HarmonicConstituents"] if float(c["amplitude"]) > 0]
            yield SourceSet(
                station_hint={"name": st["name"], "country": "USA", "lat": float(st["lat"]), "lon": float(st["lng"]),
                              "type": "reference", "timezone": TIMEZONES[sid], "aliases": {"noaa": sid}},
                constant_set={"source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                              "source_record_id": sid, "source_version": fetched.source_version,
                              "convention_id": self.convention()["convention_id"],
                              "licence_id": self.licence()["licence_id"], "qc_status": "ok",
                              "constituents": cons,
                              "provenance": {"adapter_version": self.adapter_version,
                                             "input_sha256": it["input_sha256"]}})


def run(datestamp: str, phase_reference: str, stations, cache_dir: Path):
    adapter = NoaaHarconProof(phase_reference)
    fetched = adapter.fetch(cache_dir, only=list(stations))
    noaa = list(adapter.sets(fetched))
    gesla = load_comparator_sets(datestamp, only=[G9_GESLA[s] for s in stations])
    return canary("noaa", noaa, gesla)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datestamp", required=True, help="build whose gesla fits to read (builds/<D>/gesla/fits/)")
    ap.add_argument("--phase-reference", default="greenwich_utc", choices=("greenwich_utc", "local"))
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("stations", nargs="*", default=list(PROOF_STATIONS))
    a = ap.parse_args(argv)
    cache = a.cache_dir or Path(tempfile.mkdtemp(prefix="f3-noaa-"))
    res = run(a.datestamp, a.phase_reference, a.stations, cache)
    print(json.dumps({"phase_reference": a.phase_reference, "convention_canary": res.convention_canary(),
                      **res.to_dict()}, sort_keys=True, indent=1))
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
