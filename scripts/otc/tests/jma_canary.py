"""A-JMA-a proof: JMA constants (live) against our GESLA fits of the Japanese gauges.

    uv run python tests/jma_canary.py --fits DIR [--phase-reference jst|greenwich_utc] [--cache-dir DIR] [CODE ...]

DIR holds gesla-fit documents (`otc_pipeline.gesla_sets` fit + write_fits) of the GESLA JPN records
near JMA stations. Pairing is the canary's own (`canary.pair_sets`: nearest GESLA record within
5 km, same country). Prints the canary result plus per-constituent summaries as JSON and exits 1
when the canary fails. The default phase reference is the adapter's declared one (local meridian).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

from otc_pipeline.adapters.jma import JmaAdapter
from otc_pipeline.canary import canary
from otc_pipeline.gesla_sets import load_comparator_sets


def run(fits_dir: Path, phase_reference: str, codes, cache_dir: Path):
    adapter = JmaAdapter(phase_reference)
    fetched = adapter.fetch(cache_dir, only=list(codes) or None)
    sets = list(adapter.comparator_sets(fetched))   # every owner: the canary is a comparison inside the pipeline
    return sets, canary("jma", sets, load_comparator_sets(in_dir=fits_dir))


def summary(sets, res) -> dict:
    owner = {s.constant_set["source_record_id"]: s.constant_set["provenance"]["owner"] for s in sets}
    out = {}
    for name in ("M2", "S2", "K1", "O1"):
        da = [abs(p.g9.da_m[name]) * 100 for p in res.pairs if name in p.g9.da_m]
        dg = [abs(p.g9.dg_deg[name]) for p in res.pairs if name in p.g9.dg_deg]
        out[name] = {"median_da_cm": round(statistics.median(da), 3), "max_da_cm": round(max(da), 3),
                     "median_dg_deg": round(statistics.median(dg), 3), "max_dg_deg": round(max(dg), 3)}
    by_owner = {}
    for o in sorted(set(owner.values())):
        ps = [p for p in res.pairs if owner[p.source_record_id] == o]
        dg = [abs(v) for p in ps for v in p.g9.dg_deg.values()]
        by_owner[o] = {"pairs": len(ps), "median_dg_deg": round(statistics.median(dg), 3) if dg else None}
    return {"per_constituent": out, "by_owner": by_owner}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fits", type=Path, required=True, help="directory of gesla-fit documents (MANIFEST.json)")
    ap.add_argument("--phase-reference", default="local_meridian", choices=("local_meridian", "jst", "greenwich_utc"))
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("codes", nargs="*")
    a = ap.parse_args(argv)
    cache = a.cache_dir or Path(tempfile.mkdtemp(prefix="a-jma-"))
    sets, res = run(a.fits, a.phase_reference, a.codes, cache)
    print(json.dumps({"phase_reference": a.phase_reference, "convention_canary": res.convention_canary(),
                      **summary(sets, res), **res.to_dict()}, sort_keys=True, indent=1, ensure_ascii=False))
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
