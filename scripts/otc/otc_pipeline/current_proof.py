"""A-NOAAC proof: OTC current constants reproduce NOAA's published current predictions.

For one station bin, the set's ellipse constants go through the webcaltides harmonics engine
(`scripts/otc/tools/engine_current_events.rb`), and its max flood, max ebb and slack events are
compared with NOAA's `currents_predictions` (interval MAX_SLACK) for the same bin.

The engine predicts a current the way it predicts an XTide current: one scalar series, flood
positive, `datum_offset` + sum f*A*cos(V0 + u + speed*t - g). It has no ellipse, azimuth or bin.
NOAA's `Velocity_Major` is the major-axis component, signed so flood is positive. So the bin's
major-axis constants go to the engine with the sign that makes flood positive: +1 when the
azimuth is within 90 degrees of the bin's mean flood direction, else -1 (the minor axis does not
enter NOAA's speed). The bin's mean current along the major axis (`mean_major_ms`, signed
toward the azimuth) becomes `datum_offset`, with the same sign.

    uv run python -m otc_pipeline.current_proof --cache DIR [--drop bins|azimuth|mean] \
        --start 2026-10-08 --days 7 ACT1616:1 BOS1101:1 ...
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from otc_pipeline.adapters.noaa_currents import NoaaCurrentsAdapter
from otc_pipeline.http import PoliteFetcher

PRED_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
WEBCALTIDES = Path(__file__).resolve().parents[3]
DRIVER = Path(__file__).resolve().parents[1] / "tools" / "engine_current_events.rb"
# Engine names for NOAA names the TCD spells differently.
ENGINE_NAMES = {"LAM2": "LDA2", "RHO": "RHO1"}

# Tolerances, fixed before the comparison: NOAA gives times to the minute and speeds to 0.1 cm/s;
# the engine's nodal tables (TCD, per year) differ slightly from NOAA's.
TOL_SPEED_MS = 0.03          # max flood / max ebb speed
TOL_SLACK_MIN = 5.0          # slack time
TOL_MAX_TIME_MIN = 10.0      # max flood / max ebb time (the speed is flat near the peak)
MATCH_WINDOW_MIN = 90.0      # an engine event within this of a NOAA event of the same type pairs


@dataclass
class Comparison:
    station: str
    bin: int
    noaa_events: int
    model_events: int
    paired: int
    missed: int                 # NOAA events with no engine event of the same type
    extra: int                  # engine events with no NOAA event
    max_dt_slack_min: float
    max_dt_peak_min: float
    max_dv_ms: float
    max_flood_noaa_ms: float
    max_flood_model_ms: float
    max_ebb_noaa_ms: float
    max_ebb_model_ms: float
    # every paired event's |dt| (min) and every paired peak's |dv| (m/s): the canary's medians (not in to_dict)
    dt_min: tuple = ()
    dv_ms: tuple = ()
    noaa_peaks: int = 0         # NOAA max flood and max ebb events in the window (paired ones: len(dv_ms))

    @property
    def passed(self) -> bool:
        return (self.missed == 0 and self.extra == 0 and self.max_dt_slack_min <= TOL_SLACK_MIN
                and self.max_dt_peak_min <= TOL_MAX_TIME_MIN and self.max_dv_ms <= TOL_SPEED_MS)

    def to_dict(self) -> dict:
        return {**{k: v for k, v in self.__dict__.items() if k not in ("dt_min", "dv_ms")},
                "paired_peaks": len(self.dv_ms), "passed": self.passed}


def engine_input(current_bin: dict, start: datetime, end: datetime) -> dict:
    """The engine's scalar series for one current bin: major axis, signed so flood is positive."""
    flood = current_bin.get("mean_flood_dir_deg")
    # no mean flood direction: the azimuth is taken as the flood direction
    d = math.radians(current_bin["azimuth_deg"] - (current_bin["azimuth_deg"] if flood is None else flood))
    sign = 1.0 if math.cos(d) >= 0 else -1.0
    mean = current_bin.get("mean_major_ms", 0.0)   # absent: the reader adds 0 (schema 0.5 and format 1.0)
    cons = []
    for c in current_bin["constituents"]:
        cons.append({"name": ENGINE_NAMES.get(c["name"], c["name"]), "amp": sign * c["major_amplitude_ms"],
                     "phase": c["major_phase_deg"]})
    return {"start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "datum_offset": sign * mean, "constituents": cons, "step_seconds": 60}


def run_engine(inp: dict, workdir: Path) -> dict:
    i, o = workdir / "engine_in.json", workdir / "engine_out.json"
    i.write_text(json.dumps(inp))
    subprocess.run(["bundle", "exec", "ruby", str(DRIVER), str(i), str(o)], cwd=WEBCALTIDES,
                   check=True, capture_output=True, text=True)
    return json.loads(o.read_text())


def noaa_predictions(fetcher: PoliteFetcher, station: str, bin_: int, start: datetime, days: int) -> dict:
    end = start + timedelta(days=days)
    params = {"product": "currents_predictions", "station": station, "bin": str(bin_),
              "begin_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"),
              "interval": "MAX_SLACK", "units": "metric", "time_zone": "gmt", "format": "json"}
    got = fetcher.get(PRED_URL, params=params,
                      cache_name=f"predictions/{station}_{bin_}_{params['begin_date']}_{days}d.json")
    return json.loads(got.bytes())


def _t(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s if "T" in s or "+" in s else s.replace(" ", "T") + "+00:00")


def compare(station: str, bin_: int, noaa: dict, model: dict, start: datetime, end: datetime) -> Comparison:
    """Pairs each NOAA event with the nearest engine event of the same type (within
    MATCH_WINDOW_MIN) in [start, end)."""
    def inside(t):
        return start <= t < end
    ref = [{"type": e["Type"], "t": _t(e["Time"]), "v": float(e["Velocity_Major"]) / 100.0}
           for e in noaa["current_predictions"]["cp"]]
    ref = [e for e in ref if inside(e["t"])]
    mod = [{"type": e["type"], "t": _t(e["time"]), "v": float(e["velocity"])} for e in model["events"]]
    used: set[int] = set()
    dt_slack, dt_peak, dv = [0.0], [0.0], [0.0]
    dts, dvs = [], []
    missed = 0
    for e in ref:
        best = None
        for j, m in enumerate(mod):
            if j in used or m["type"] != e["type"]:
                continue
            d = abs((m["t"] - e["t"]).total_seconds()) / 60.0
            if d <= MATCH_WINDOW_MIN and (best is None or d < best[0]):
                best = (d, j)
        if best is None:
            missed += 1
            continue
        used.add(best[1])
        dts.append(best[0])
        if e["type"] == "slack":
            dt_slack.append(best[0])
        else:
            dt_peak.append(best[0])
            dv.append(abs(mod[best[1]]["v"] - e["v"]))
            dvs.append(dv[-1])
    # an engine event just outside the window can pair with nothing; count only those inside
    extra = sum(1 for j, m in enumerate(mod) if j not in used and inside(m["t"])
                and start + timedelta(minutes=MATCH_WINDOW_MIN) <= m["t"] < end - timedelta(minutes=MATCH_WINDOW_MIN))

    def peak(events, kind, f):
        vs = [e["v"] for e in events if e["type"] == kind and inside(e["t"])]
        return round(f(vs), 4) if vs else float("nan")
    return Comparison(station, bin_, len(ref), sum(1 for m in mod if inside(m["t"])), len(used), missed, extra,
                      round(max(dt_slack), 2), round(max(dt_peak), 2), round(max(dv), 4),
                      peak(ref, "flood", max), peak(mod, "flood", max), peak(ref, "ebb", min), peak(mod, "ebb", min),
                      tuple(dts), tuple(dvs), sum(1 for e in ref if e["type"] != "slack"))


def prove(cache: Path, targets: list[tuple[str, int]], start: datetime, days: int,
          drop: str | None = None) -> list[Comparison]:
    adapter = NoaaCurrentsAdapter(drop=drop)
    fetched = adapter.fetch(cache, only=sorted({s for s, _ in targets}))
    sets = {ss.constant_set["source_record_id"]: ss.constant_set for ss in adapter.sets(fetched)
            if ss.constant_set is not None}
    fetcher = PoliteFetcher(cache, min_interval_s=1.0)
    end = start + timedelta(days=days)
    out = []
    with tempfile.TemporaryDirectory(prefix="otc-current-proof-") as tmp:
        for station, bin_ in targets:
            cb = next(b for b in sets[station]["current_bins"] if b["bin"] == bin_)
            # 6 h either side, so an event at the window's edge is not cut
            model = run_engine(engine_input(cb, start - timedelta(hours=6), end + timedelta(hours=6)), Path(tmp))
            noaa = noaa_predictions(fetcher, station, bin_, start, days)
            out.append(compare(station, bin_, noaa, model, start, end))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m otc_pipeline.current_proof")
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--drop", choices=("bins", "azimuth", "mean"), default=None, help="RED test flag")
    ap.add_argument("--start", default="2026-10-08")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("targets", nargs="+", metavar="STATION:BIN")
    a = ap.parse_args(argv)
    start = datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    targets = [(t.split(":")[0], int(t.split(":")[1])) for t in a.targets]
    res = prove(a.cache, targets, start, a.days, drop=a.drop)
    print(json.dumps({"drop": a.drop, "start": a.start, "days": a.days,
                      "tolerances": {"speed_ms": TOL_SPEED_MS, "slack_min": TOL_SLACK_MIN,
                                     "max_time_min": TOL_MAX_TIME_MIN, "match_window_min": MATCH_WINDOW_MIN},
                      "results": [r.to_dict() for r in res], "passed": all(r.passed for r in res)},
                     indent=1))
    return 0 if all(r.passed for r in res) else 1


if __name__ == "__main__":
    from otc_pipeline.current_proof import main as _main
    sys.exit(_main())
