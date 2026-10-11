"""A-JMA-a proof: reproduce JMA's own hourly predictions and high/low waters from our constants.

    uv run python tests/jma_predictions.py --year 2026 [--phase-reference jst|greenwich_utc] CODE [CODE ...]

For each JMA-owned station: fetch the adapter's set (Greenwich phases, metres) and JMA's hourly
prediction file for the year (cm above the tide-table datum, JST), predict with the engine's
convention (TCD speeds, yearly V0+u and f: `gesla.fit.design`) plus the set's msl_offset_m, and
compare. Hourly: max/RMS |dh|. High/low waters: JMA's listed times and heights (minutes, JST)
against our extremum nearest to each (1-minute search within +-3 h).

Prints JSON; exits 1 when any station's max hourly |dh| exceeds --max-dh-cm or the 95th percentile
|dt| of the high/low waters exceeds --max-dt-min (the max |dt| is reported: at a flat, double low
water a cm-level difference moves the extremum by much more than at a sharp one).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from gesla.fit import design
from gesla.read_gesla import otc_work
from gesla.tcd_table import TcdTable

from otc_pipeline.adapters.jma import JST, JmaAdapter, fetch_predictions
from otc_pipeline.gesla_sets import REPO

# JMA's prediction line: after the code (cols 78-80), 4 high waters then 4 low waters, each
# hhmm (4 chars) + height cm (3 chars); 9999999 = none.
EVENTS_AT = 80


def parse_events(data: bytes) -> list[tuple[str, datetime, float]]:
    """[(HW|LW, UTC time, m)] listed in a JMA prediction file."""
    out = []
    for line in data.decode("ascii").splitlines():
        if not line.strip():
            continue
        yy, mm, dd = (int(line[72 + 2 * i:74 + 2 * i]) for i in range(3))
        day = datetime(2000 + yy, mm, dd, tzinfo=UTC) - timedelta(hours=JST)
        for k in range(8):
            cell = line[EVENTS_AT + 7 * k:EVENTS_AT + 7 * k + 7]
            if cell == "9999999":
                continue
            hh, mi, h = int(cell[0:2]), int(cell[2:4]), int(cell[4:7])
            out.append(("HW" if k < 4 else "LW", day + timedelta(hours=hh, minutes=mi), h / 100.0))
    return out


def predictor(table: TcdTable, cs: dict):
    cons = cs["constituents"]
    names = [c["name"] for c in cons]
    amp = np.array([c["amplitude_m"] for c in cons])
    g = np.radians([c["phase_deg"] for c in cons])
    z0 = cs["datum"]["msl_offset_m"]

    def h(times_s: np.ndarray) -> np.ndarray:
        cols = design(table, names, np.asarray(times_s, dtype=np.int64))
        return z0 + cols[:, 0::2] @ (amp * np.cos(g)) + cols[:, 1::2] @ (amp * np.sin(g))
    return h


def compare(table: TcdTable, cs: dict, hourly, events) -> dict:
    pred = predictor(table, cs)
    ts = np.array([int(t.timestamp()) for t, _ in hourly])
    dh = pred(ts) - np.array([v for _, v in hourly])
    dts, dhs, unmatched = [], [], 0
    for kind, t, v in events:
        t0 = int(t.timestamp())
        grid = np.arange(t0 - 3 * 3600, t0 + 3 * 3600 + 1, 60)
        y = pred(grid)
        i = np.arange(1, len(y) - 1)
        ext = i[(y[i] >= y[i - 1]) & (y[i] >= y[i + 1])] if kind == "HW" else i[(y[i] <= y[i - 1]) & (y[i] <= y[i + 1])]
        if not len(ext):            # no extremum of that kind within +-3 h: counted as 180 min
            dts.append(180.0)
            unmatched += 1
            continue
        j = ext[np.argmin(np.abs(grid[ext] - t0))]
        dts.append((grid[j] - t0) / 60.0)
        dhs.append(y[j] - v)
    adt, adh = np.abs(dts), np.abs(dhs)
    return {"hours": len(ts), "max_dh_cm": round(float(np.max(np.abs(dh))) * 100, 2),
            "rms_dh_cm": round(float(np.sqrt(np.mean(dh ** 2))) * 100, 2),
            "mean_dh_cm": round(float(np.mean(dh)) * 100, 2),
            "events": len(events), "unmatched_events": unmatched, "max_dt_min": round(float(np.max(adt)), 1),
            "median_dt_min": round(float(np.median(adt)), 1),
            "p95_dt_min": round(float(np.percentile(adt, 95)), 1),
            "max_event_dh_cm": round(float(np.max(adh)) * 100, 2)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--phase-reference", default="local_meridian", choices=("local_meridian", "jst", "greenwich_utc"))
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--max-dh-cm", type=float, default=5.0)
    ap.add_argument("--max-dt-min", type=float, default=10.0, help="limit on the 95th percentile |dt|")
    ap.add_argument("codes", nargs="+")
    a = ap.parse_args(argv)
    cache = a.cache_dir or Path(tempfile.mkdtemp(prefix="a-jma-pred-"))
    tcd = REPO / "data" / "latest-xtide.tcd"
    table = TcdTable.load(otc_work() / "tcd_table.json", tcd_sha256=hashlib.sha256(tcd.read_bytes()).hexdigest())
    adapter = JmaAdapter(a.phase_reference, year=a.year)
    fetched = adapter.fetch(cache, only=a.codes)
    sets = {s.constant_set["source_record_id"]: s.constant_set for s in adapter.sets(fetched)}
    out, ok = {}, True
    for code in a.codes:
        if sets[code]["provenance"]["owner"] != "jma":
            raise SystemExit(f"{code}: not a JMA-owned station (only 4 constituents); pick a JMA station")
        got, hourly = fetch_predictions(cache, code, a.year)
        r = compare(table, sets[code], hourly, parse_events(got.bytes()))
        r["prediction_file_sha256"] = got.sha256
        out[code] = r
        ok &= r["max_dh_cm"] <= a.max_dh_cm and r["p95_dt_min"] <= a.max_dt_min
    print(json.dumps({"phase_reference": a.phase_reference, "year": a.year, "passed": ok,
                      "max_dh_cm_limit": a.max_dh_cm, "max_dt_min_limit": a.max_dt_min, "stations": out},
                     indent=1, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
