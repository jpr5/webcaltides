"""A-KV proof: Kartverket constants (live) against our GESLA fits of the NHS records.

    uv run python tests/kartverket_canary.py --datestamp D [--phase-reference greenwich_utc] [CODE ...]

prints the canary result as JSON and exits 1 when the canary fails. `--phase-reference
greenwich_utc` reads the served phases as Greenwich (the RED); the default is the adapter's
declared convention (UTC+1 -> Greenwich).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from otc_pipeline.adapters.kartverket import KartverketAdapter
from otc_pipeline.canary import canary
from otc_pipeline.gesla_sets import load_comparator_sets

# Kartverket station code -> the NHS record of the same gauge in GESLA 4.1.
NHS_GESLA = {
    "BGO": "bergen-bgo-nor-nhs",
    "TRD": "trondheim-trd-nor-nhs",
    "SVG": "stavanger-svg-nor-nhs",
    "TOS": "tromso-tos-nor-nhs",
    "NVK": "narvik-nvk-nor-nhs",
}
PROOF_STATIONS = ("BGO", "TRD", "SVG")   # manifest A-KV: Bergen, Trondheim and one more


def run(datestamp: str, phase_reference: str, stations, cache_dir: Path):
    adapter = KartverketAdapter(phase_reference)
    fetched = adapter.fetch(cache_dir, only=list(stations))
    kv = list(adapter.sets(fetched))
    gesla = load_comparator_sets(datestamp, only=[NHS_GESLA[s] for s in stations])
    return canary("kartverket", kv, gesla)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datestamp", required=True, help="build whose gesla fits to read (builds/<D>/gesla/fits/)")
    ap.add_argument("--phase-reference", default="local", choices=("local", "greenwich_utc"))
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("stations", nargs="*", default=list(PROOF_STATIONS))
    a = ap.parse_args(argv)
    cache = a.cache_dir or Path(tempfile.mkdtemp(prefix="a-kv-"))
    res = run(a.datestamp, a.phase_reference, a.stations, cache)
    print(json.dumps({"phase_reference": a.phase_reference, "convention_canary": res.convention_canary(),
                      **res.to_dict()}, sort_keys=True, indent=1))
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
