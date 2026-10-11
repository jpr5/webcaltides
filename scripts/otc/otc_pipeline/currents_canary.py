"""The NOAA-currents canary (D8; owner-delegated decision 3, 2026-10-11).

NOAA currents have no GESLA comparator, so the canary compares the source with NOAA's own published current
predictions: a fixed sample of reference stations (SAMPLE stations, the default bin of each), predicted by the
reference engine from the constants the release publishes (current_proof.engine_input / run_engine), against
NOAA's MAX_SLACK predictions for the same bin and week (current_proof.compare pairs the events).

The pass bar is the other sources' canary rule (canary.canary: median |dA| <= 1 cm and median |dg| <= 2 deg over
at least 3 gauges, no gauge missing a constituent), read for events:
- at least MIN_GAUGES stations compared;
- no sampled station without data: each has a cached NOAA prediction and at least one paired event (the rule's
  "no gauge missing a constituent": the comparison is complete);
- median |dt| over every paired event (slack, max flood, max ebb) <= 2 deg of M2 (TOL_DT_MIN, 4.14 min);
- median |dv| over every paired max flood and max ebb <= 1 cm/s (TOL_DV_MS);
- per station (fix round 1): every paired event within STATION_MAX_DT_MIN (10 min) and every paired peak within
  STATION_MAX_DV_MS (3 cm/s), so a sign or bin error at one station fails the canary although the medians hold.
Like the other canaries, the bar is on medians, not on single events. Events one side lists and the other does
not (NOAA leaves out weak events: a max ebb of a few cm/s between two slacks, a 0.1 cm/s reversal) are counted
per station in the result (`unmatched_noaa`, `unmatched_engine`) and do not fail the canary.

    uv run python -m otc_pipeline.currents_canary fetch --agency-cache ROOT --cache DIR   # NOAA, 1 request/s
    uv run python -m otc_pipeline.currents_canary run --agency-cache ROOT --cache DIR     # offline

`fetch` is the only step that reads NOAA's predictions from the network (PoliteFetcher, at most one request per
second, cached under DIR/predictions/). The build (`run_canary`) reads the cache only: a prediction that is not
cached fails the canary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

from otc_pipeline import current_proof as cp
from otc_pipeline.adapter import SourceSet

SAMPLE = 20
MIN_GAUGES = 3
TOL_DT_MIN = 2.0 / 28.9841042 * 60.0     # 2 deg of M2 in minutes (4.14)
TOL_DV_MS = 0.01                          # 1 cm/s
STATION_MAX_DT_MIN = 10.0                 # per station: no paired event more than 10 min off (current_proof's peak bar)
STATION_MAX_DV_MS = 0.03                  # per station: no paired peak more than 3 cm/s off (current_proof's bar)
START = "2026-10-08"
DAYS = 7


def sample(sets: Sequence[SourceSet], n: int = SAMPLE) -> list[tuple[str, int]]:
    """(station, bin) of n reference current sets: the n smallest SHA-256 of the station id (a fixed, spread
    sample that does not depend on the input order), each at its default bin (else its first bin)."""
    refs = [ss for ss in sets if ss.constant_set is not None and ss.constant_set.get("current_bins")]
    refs.sort(key=lambda ss: hashlib.sha256(ss.constant_set["source_record_id"].encode()).hexdigest())
    out = []
    for ss in refs[:n]:
        bins = [b["bin"] for b in ss.constant_set["current_bins"]]
        b = ss.station_hint.get("default_current_bin")
        out.append((ss.constant_set["source_record_id"], b if b in bins else bins[0]))
    return out


def _cache_name(station: str, bin_: int, start: datetime, days: int) -> str:
    return f"predictions/{station}_{bin_}_{start.strftime('%Y%m%d')}_{days}d.json"


def fetch(cache: Path, targets: Sequence[tuple[str, int]], start: datetime, days: int = DAYS) -> int:
    """NOAA's predictions for the targets into the cache (PoliteFetcher, 1 request/s; a cached one is not
    fetched again). Returns how many were fetched or found."""
    from otc_pipeline.http import PoliteFetcher
    f = PoliteFetcher(cache, min_interval_s=1.0)
    for i, (station, bin_) in enumerate(targets, 1):
        cp.noaa_predictions(f, station, bin_, start, days)
        print(f"  [{i}/{len(targets)}] {station} bin {bin_}", flush=True)
    return len(targets)


def _cached(cache: Path, station: str, bin_: int, start: datetime, days: int) -> dict | None:
    p = Path(cache) / _cache_name(station, bin_, start, days)
    return json.loads(p.read_text()) if p.is_file() else None


def judge(comparisons: list[cp.Comparison], unavailable: list[str], *, min_gauges: int = MIN_GAUGES,
          tol_dt_min: float = TOL_DT_MIN, tol_dv_ms: float = TOL_DV_MS) -> dict:
    """The canary result (module text) from the per-station comparisons."""
    dts = [x for c in comparisons for x in c.dt_min]
    dvs = [x for c in comparisons for x in c.dv_ms]
    mdt = statistics.median(dts) if dts else None
    mdv = statistics.median(dvs) if dvs else None
    reasons = []
    if len(comparisons) < min_gauges:
        reasons.append(f"{len(comparisons)} station(s) compared, at least {min_gauges} needed")
    for u in unavailable:
        reasons.append(f"{u}: no cached NOAA prediction")
    for c in comparisons:
        if not c.dt_min:
            reasons.append(f"{c.station} bin {c.bin}: no paired event")
            continue
        if max(c.dt_min) > STATION_MAX_DT_MIN:
            reasons.append(f"{c.station} bin {c.bin}: a paired event {max(c.dt_min):.1f} min off "
                           f"(limit {STATION_MAX_DT_MIN:g} min)")
        if c.dv_ms and max(c.dv_ms) > STATION_MAX_DV_MS:
            reasons.append(f"{c.station} bin {c.bin}: a paired peak {max(c.dv_ms):.3f} m/s off "
                           f"(limit {STATION_MAX_DV_MS} m/s)")
    if mdt is not None and not mdt <= tol_dt_min:
        reasons.append(f"median |dt| {mdt:.2f} min exceeds {tol_dt_min:.2f} min")
    if mdv is not None and not mdv <= tol_dv_ms:
        reasons.append(f"median |dv| {mdv:.4f} m/s exceeds {tol_dv_ms} m/s")
    return {"status": "fail" if reasons else "pass", "gauges": len(comparisons),
            "median_dt_min": None if mdt is None else round(mdt, 3),
            "median_dv_ms": None if mdv is None else round(mdv, 5),
            "tol_dt_min": round(tol_dt_min, 3), "tol_dv_ms": tol_dv_ms, "min_gauges": min_gauges,
            "events_paired": len(dts), "unmatched_noaa": sum(c.missed for c in comparisons),
            "unmatched_engine": sum(c.extra for c in comparisons), "reasons": reasons,
            "stations": [c.to_dict() for c in comparisons]}


def run_canary(sets: Sequence[SourceSet], cache: Path, *, start: str = START, days: int = DAYS, n: int = SAMPLE,
               engine: Callable[[dict, Path], dict] = cp.run_engine) -> dict:
    """The canary on the cached predictions (no network): see the module text."""
    t0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=days)
    by_id = {ss.constant_set["source_record_id"]: ss.constant_set for ss in sets if ss.constant_set is not None}
    comps, unavailable = [], []
    with tempfile.TemporaryDirectory(prefix="otc-currents-canary-") as tmp:
        for station, bin_ in sample(sets, n):
            noaa = _cached(cache, station, bin_, t0, days)
            if noaa is None:
                unavailable.append(f"{station} bin {bin_}")
                continue
            cb = next(b for b in by_id[station]["current_bins"] if b["bin"] == bin_)
            model = engine(cp.engine_input(cb, t0 - timedelta(hours=6), t1 + timedelta(hours=6)), Path(tmp))
            comps.append(cp.compare(station, bin_, noaa, model, t0, t1))
    res = judge(comps, unavailable)
    res.update({"comparator": "NOAA currents_predictions MAX_SLACK", "start": start, "days": days, "sample": n})
    return res


def convention_canary(result: dict) -> dict:
    """The schema `convention.canary` object (status and gauges: the schema has no event-time fields)."""
    return {"status": result["status"], "gauges": result["gauges"]}


def main(argv: list[str] | None = None) -> int:
    from otc_pipeline.combine import load_agency
    ap = argparse.ArgumentParser(prog="otc_pipeline.currents_canary")
    ap.add_argument("cmd", choices=("fetch", "run"))
    ap.add_argument("--agency-cache", type=Path, required=True)
    ap.add_argument("--cache", type=Path, required=True, help="the predictions cache")
    ap.add_argument("--start", default=START)
    ap.add_argument("--days", type=int, default=DAYS)
    a = ap.parse_args(argv)
    sets = load_agency(a.agency_cache, ("noaa-currents",), jma_year=0)["noaa-currents"].sets
    if a.cmd == "fetch":
        t0 = datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        fetch(a.cache, sample(sets), t0, a.days)
        return 0
    res = run_canary(sets, a.cache, start=a.start, days=a.days)
    print(json.dumps(res, indent=1))
    return 0 if res["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
