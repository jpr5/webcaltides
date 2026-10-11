"""The real-record gate for short-record expected_accuracy (R6): stated values against real errors.

A short record's published constants are compared with an independent comparator of the same place:
- a same-gauge long record: a fitted record of at least a year within SAME_GAUGE_KM whose fit span does not overlap
  the short record's (another epoch of the gauge: a sensor or contributor of the same time is not independent; the
  longest if several), not refused;
- else, for a NOAA record, NOAA's own harmonic constants when they come from one-year analyses
  ("one year analyses" in the constituent comments) of years that do not overlap the short record's data.
TICON-4 is never a comparator: it fitted the same GESLA file.

For each published main constituent (M2, S2, K1, O1) the comparator also has: the real amplitude error
|A_short - A_cmp| against the stated amplitude value, and, where a phase value is stated, the real phase
error against it. A stated median is exceeded about half the time, so per group the share over the stated
value must be within BAND. Groups: all, coastal (GESLA gauge type), gappy (more than 20 % of the record's
span missing), microtidal, and good-day bands <= 30, 31-90, 91-182, 183-364. Each group is checked pooled and per constituent
(a stated value per constituent must hold per constituent). A cell with fewer than MIN_RECORDS records is
reported, never claimed. power: the chance the gate fails a group whose true share
is 0.2 or 0.8, with the group's record count as n (cuts and constituents of one record are not independent).

The stated values are a calibration's: per constituent (calibration version 2: by_constituent), or the one
published pair for every constituent (version 1), so the gate also measures what was published before.

    uv run python -m gesla.short_accuracy_gate --calibration DIR --fit-run DIR [--overlay-run DIR] [--out FILE]
Exit 0: no claimed group fails; 6: a claimed group fails.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .short_accuracy import MAIN, PHASE_MIN_AMP_M, km

SAME_GAUGE_KM = 1.0
BAND = (0.35, 0.65)
MIN_RECORDS = 20
GAPPY = 0.20
NOAA_ONE_YEAR = re.compile(r"one year analys", re.I)
GROUPS = ("all", "coastal", "gappy", "microtidal", "<=30", "31-90", "91-182", "183-364")


def _wrap(d: float) -> float:
    return abs((d + 180.0) % 360.0 - 180.0)


def binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))


def power(n: int, band: tuple[float, float] = BAND) -> float | None:
    """The chance a group of n records is outside the band when the true share is 0.2 (and, by symmetry, 0.8)."""
    if n == 0:
        return None
    lo = math.ceil(band[0] * n) - 1                   # share < 0.35 means at most this many over
    return round(binom_cdf(lo, n, 0.2), 3)


def groups_of(row: Mapping, fit: Mapping, amps: Mapping[str, float]) -> list[str]:
    g = ["all"]
    if (row.get("gauge_type") or row.get("gauge_type_meta")) == "Coastal":
        g.append("coastal")
    span_h = (int(fit["last_s"]) - int(fit["first_s"])) / 3600 + 1
    if span_h > 0 and 1 - float(row["data_hours"]) / span_h > GAPPY:
        g.append("gappy")
    if amps and all(a < PHASE_MIN_AMP_M for a in amps.values()):
        g.append("microtidal")
    d = float(row["data_hours"]) / 24
    g.append("<=30" if d <= 30 else "31-90" if d <= 90 else "91-182" if d <= 182 else "183-364")
    return g


def noaa_one_year(record_id: str, first_s: int, last_s: int, cache: Path) -> dict | None:
    """NOAA's constants of a NOAA record's station when every main constituent comes from one-year analyses of
    years outside the record's data years; name -> (amp m, phase deg)."""
    m = re.search(r"-(\d+)-usa-noaa$", record_id)
    if not m:
        return None
    p = cache / f"{m.group(1)}.json"
    if not p.exists():
        return None
    hc = {c["name"]: c for c in json.loads(p.read_text()).get("HarmonicConstituents", [])}
    y0 = datetime.fromtimestamp(first_s, timezone.utc).year
    y1 = datetime.fromtimestamp(last_s, timezone.utc).year
    out = {}
    for c in MAIN:
        if c not in hc:
            continue
        com = hc[c].get("comments") or ""
        if not NOAA_ONE_YEAR.search(com):
            return None
        yrs = [int(y) for y in re.findall(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)", com)]
        rng = list(range(min(yrs), max(yrs) + 1)) if yrs else []
        if not rng or any(y0 <= y <= y1 for y in rng):
            return None
        out[c] = (float(hc[c]["amplitude"]), float(hc[c]["phase_GMT"]))
    return out or None


def stated_of(cal: Mapping, rid: str) -> dict[str, tuple[float, float | None]] | None:
    t = cal["targets"].get(rid)
    if t is None or "error" in t:
        return None
    if "by_constituent" in t:
        return {c: (v.get("amp_m"), v.get("phase_deg")) for c, v in t["by_constituent"].items()}
    return {c: (t["amp_uncertainty_m"], t["phase_uncertainty_deg"]) for c in MAIN}   # version 1: one pair


def comparisons(cal: Mapping, rows: Mapping[str, dict], fits: Mapping[str, dict], noaa_cache: Path) -> list[dict]:
    """One row per calibrated short record with a comparator: its groups, and per constituent the real and
    stated values."""
    refused = set(cal.get("refused", {}))
    longs = [r for r in rows if r not in refused and float(rows[r]["data_hours"]) >= 365.26 * 24]
    la = np.array([float(rows[r]["lat"]) for r in longs]); lo = np.array([float(rows[r]["lon"]) for r in longs])
    out = []
    for rid in sorted(cal["targets"]):
        st = stated_of(cal, rid)
        if st is None or rid not in rows or rid in refused:
            continue
        f, row = fits[rid], rows[rid]
        amps = {c: float(f["constituents"][c]["amp_m"]) for c in MAIN if c in f["constituents"]}
        d = km(float(row["lat"]), float(row["lon"]), la, lo)
        near = [(longs[i], float(d[i])) for i in np.flatnonzero(d <= SAME_GAUGE_KM)
                if int(fits[longs[i]]["last_s"]) < int(f["first_s"]) or int(fits[longs[i]]["first_s"]) > int(f["last_s"])]
        cmp, kind, cid = None, None, None
        if near:
            cid = max(near, key=lambda x: (float(rows[x[0]]["data_hours"]), x[0]))[0]
            cf = fits[cid]["constituents"]
            cmp, kind = {c: (cf[c]["amp_m"], cf[c]["phase_deg"]) for c in MAIN if c in cf}, "same_gauge"
        else:
            n = noaa_one_year(rid, int(f["first_s"]), int(f["last_s"]), noaa_cache)
            if n:
                cmp, kind, cid = n, "noaa_one_year", "noaa:" + rid
        if not cmp:
            continue
        per = {}
        for c, a in amps.items():
            if c not in cmp or c not in st:
                continue
            sa, sg = st[c]
            per[c] = {"dA": abs(a - cmp[c][0]), "stated_amp": sa,
                      "dg": _wrap(float(f["constituents"][c]["phase_deg"]) - cmp[c][1]) if sg is not None else None,
                      "stated_phase": sg}
        if per:
            out.append({"record": rid, "comparator": cid, "kind": kind, "groups": groups_of(row, f, amps), "per": per})
    return out


def _cell(rs: Sequence[Mapping], c: str | None) -> dict:
    vals = [v for r in rs for k, v in r["per"].items() if c is None or k == c]
    a = [v["dA"] > v["stated_amp"] for v in vals if v["stated_amp"] is not None]
    p = [v["dg"] > v["stated_phase"] for v in vals if v["stated_phase"] is not None]
    n = len({r["record"] for r in rs if any((c is None or k == c) and (v["stated_amp"] is not None or v["stated_phase"]
                                                                      is not None) for k, v in r["per"].items())})
    sa_ = round(sum(a) / len(a), 3) if a else None
    sp = round(sum(p) / len(p), 3) if p else None
    if n < MIN_RECORDS:
        verdict = "too few"
    else:
        ok = (sa_ is not None or sp is not None) and all(s is None or BAND[0] <= s <= BAND[1] for s in (sa_, sp))
        verdict = "pass" if ok else "FAIL"
    return {"records": n, "amp_pairs": len(a), "amp_share_over": sa_, "phase_pairs": len(p),
            "phase_share_over": sp, "power": power(n), "verdict": verdict}


def gate(rows: Sequence[Mapping]) -> dict:
    """Per group: the pooled cell and one cell per main constituent (a stated value per constituent must hold
    per constituent: M2 and K1 can be wrong in opposite directions). The gate passes when no cell with at
    least MIN_RECORDS records is outside the band."""
    table = {}
    for g in GROUPS:
        rs = [r for r in rows if g in r["groups"]]
        table[g] = {"pooled": _cell(rs, None), **{c: _cell(rs, c) for c in MAIN}}
    fails = [f"{g}/{c}" for g, cells in table.items() for c, v in cells.items() if v["verdict"] == "FAIL"]
    return {"band": list(BAND), "min_records": MIN_RECORDS, "groups": table, "failed_cells": fails,
            "passed": not fails, "records": len(rows),
            "comparators": {k: sum(r["kind"] == k for r in rows) for k in ("same_gauge", "noaa_one_year")}}


def render(res: Mapping) -> str:
    """The gate table as text: one line per group and cell."""
    out = [f"band {res['band']}, cells need {res['min_records']} records; power = P(fail | true share 0.2)"]
    out.append(f"{'group':11s} {'cell':6s} {'recs':>4s} {'amp n':>5s} {'amp>':>6s} {'ph n':>5s} {'ph>':>6s} {'power':>5s}  verdict")
    for g, cells in res["groups"].items():
        for c, v in cells.items():
            f = lambda x: "-" if x is None else f"{x:.3f}"  # noqa: E731
            out.append(f"{g:11s} {c:6s} {v['records']:4d} {v['amp_pairs']:5d} {f(v['amp_share_over']):>6s} "
                       f"{v['phase_pairs']:5d} {f(v['phase_share_over']):>6s} {f(v['power']):>5s}  {v['verdict']}")
    out.append(f"failed cells: {res['failed_cells'] or 'none'}; passed: {res['passed']}; comparators {res['comparators']}")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    from .read_gesla import otc_work
    from .short_accuracy import CAL_FILE, CAL_VERSION, load_rows, read_calibration
    ap = argparse.ArgumentParser(prog="python -m gesla.short_accuracy_gate")
    ap.add_argument("--calibration", required=True, type=Path)
    ap.add_argument("--fit-run", required=True, type=Path)
    ap.add_argument("--overlay-run", type=Path)
    ap.add_argument("--noaa-cache", type=Path, default=otc_work() / "cache/noaa/harcon")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--apply", action="store_true", help="write the failed cells as claim limits and the validated "
                    "bands into the calibration (short_accuracy.apply_gate), then gate again")
    a = ap.parse_args(argv)
    raw = json.loads((a.calibration / CAL_FILE).read_text())
    cal = read_calibration(a.calibration) if raw.get("cal_version") == CAL_VERSION else raw   # v1: the old file
    runs = [a.fit_run] + ([a.overlay_run] if a.overlay_run else [])
    rows, where = load_rows(runs)
    fits = {r: json.loads((where[r] / "fits" / f"{r}.json").read_text()) for r in rows}
    comp = comparisons(cal, rows, fits, a.noaa_cache)
    res = gate(comp)
    res["calibration"] = {"dir": str(a.calibration), "cal_version": raw.get("cal_version"),
                          "commit": (raw.get("code") or {}).get("commit")}
    if a.out:
        a.out.write_text(json.dumps({"gate": res, "rows": comp}, indent=1, sort_keys=True) + "\n")
    print(render(res), flush=True)
    if a.apply:
        from .short_accuracy import apply_gate
        cal = apply_gate(a.calibration, res)
        res = gate(comparisons(cal, rows, fits, a.noaa_cache))
        print(f"applied: claim limits {cal['config']['claim_limits']}, validation {cal['config']['validation']}", flush=True)
        if a.out:
            a.out.with_suffix(".applied.json").write_text(json.dumps({"gate": res, "rows": comparisons(cal, rows, fits, a.noaa_cache)},
                                                                      indent=1, sort_keys=True) + "\n")
        print(render(res), flush=True)
    return 0 if res["passed"] else 6


if __name__ == "__main__":
    sys.exit(main())
