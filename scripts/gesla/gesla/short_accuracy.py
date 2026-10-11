"""expected_accuracy of a short record (format 1.0 `expected_accuracy`), and the fields a v3 fit document adds.

A short record (one the Rayleigh rule cannot resolve every candidate on: under a year of good data) is
published with how accurate its constants are expected to be. Format 1.0 defines that number as a
measurement: long records cut down to the same length (cut_to_days), their fits compared with the
full-length fits. This module makes that measurement, per short record (the target):

- Pool (R1): calibration stations of the target's tidal regime. A station is a co-site cluster (records
  within COSITE_KM, e.g. one gauge from two contributors) of long records (>= POOL_MIN_GOOD_DAYS good days,
  time base utc_instant with no months dropped, not disputed, not refused as implausible), represented by
  its longest record. A station qualifies when its form-factor class (semidiurnal / mixed / diurnal by
  F = (K1 + O1) / (M2 + S2)) equals the target's and its amplitude of the target's reference constituent
  (M2, else the largest main one) is within M2_RATIO of the target's. The target's own co-site stations
  never qualify. The POOL_STATIONS nearest stations with a usable cut make the pool; with fewer the
  target is not calibrated (pool_too_small) and is listed.
- Cuts (R4): the target's own good-hour mask (its hourly stamps, so its calendar months, season and gaps)
  is copied onto the station's record, the same calendar dates in another year (k years later or
  earlier). A shift is usable when the station has >= MIN_COVER of those hours; up to WINDOWS usable
  shifts are drawn with a seed from the target and station ids. Each cut is refitted as production would
  fit a record of it: the candidate list fit_run would choose for it (select_candidate_list on the cut with the
  station's country: base outside the DEU/NOR scope, and base whenever the cut cannot resolve the extended list
  or has too little window-A data), then the primary analysis (select_constituents.analyse; the pool's time
  base is utc_instant, whose sampling response is 1). It is compared with the station's full fit.
- TCD: the dump the fit runs were made with (fit_runs_tcd: their manifests' inputs.tcd_table), for the Rayleigh
  replay and the cuts. A fit whose selection is not rebuilt on it stops the run (SelectionMismatch).
- Metric (R2, R3, R5): per cut and main constituent c (M2, S2, K1, O1) of the station's full fit: the
  relative amplitude error |dA| / A_full (where A_full >= PHASE_MIN_AMP_M), the absolute |dA|, and the
  phase error |dg| (where A_full >= PHASE_MIN_AMP_M). A constituent the cut lost counts |dA| = A_full and
  |dg| = LOST_PHASE_DEG. Per station: the median over its cuts; per target: the median over its stations.
  - amplitude of c: the relative median x the target's own amplitude of c, at least AMP_FLOOR_M.
  - phase of c: claimed only when the target's own amplitude of c is >= PHASE_MIN_AMP_M.
  - expected_accuracy (format 1.0 has one pair of numbers for "the main constituents"): the worst of the
    published main constituents' values, never a pooled figure; the per-constituent values travel with
    the target (accuracy_by_constituent) for the short_record flag's `values`.
  - microtidal: every published main constituent under PHASE_MIN_AMP_M. The phase is then the worst of
    the no-floor phase medians (the lost-constituent penalty included) and the target is flagged.
- Sanity (R8): a fitted record whose M2 is above SANITY_M2_MAX_M, or more than SANITY_RATIO x the largest
  M2 of its SANITY_NEIGHBOURS nearest long records (co-sites excluded), is refused as implausible_fit.
- Hold-out: HOLDOUT_N stations, kept out of every pool, each cut with a real target's mask and judged
  against its own pool like a target; the share of its cuts over the stated value is reported per
  constituent, with every skipped hold-out counted.

The run writes masks.npz (target masks), cuts.jsonl (one row per station and round: every cut, with the
candidate list it was fitted with, and the row's stamp: commit, TCD dump sha256, seed, cut list rule),
short_accuracy.json (targets, refusals, pools, hold-out, inputs: the fit runs' identities and the TCD dump) and
MANIFEST.json (sha256 and size of the three, written last). read_calibration checks them. Into an out dir that
already has cuts.jsonl the run resumes: a row is reused only when its stamp is this run's, its full fit is the
station's and it holds the targets the round asks; every other row is cut again. A build uses a calibration
only with the fit runs and TCD dump it was made from (check_bound).

    uv run python -m gesla.short_accuracy --fit-run DIR [--overlay-run DIR] --out DIR [--workers 4]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from .qc import Series
from .select_constituents import (CANDIDATE_LISTS, CANDIDATES, CONFLICT, FIT_SPAN_YEARS, PERIOD_EXCEEDS_RECORD, RayleighDrop, analyse,
                                  in_tcd_years, last_years, rayleigh_drops, rayleigh_select, select_candidate_list)
from .tcd_table import TcdTable

CAL_VERSION = 2
FIT_DOC_VERSION = 3              # the fit document version that carries the fields of fit_document_v3_fields
MAIN = ("M2", "S2", "K1", "O1")
# Constituents with no stated accuracy (owner decision on the R6 gate, 2026-10-10): against same-gauge and NOAA
# one-year comparators the M2 real error exceeds the cut-measured value 66-68 % of the time; a widening factor fitted
# on a random half of the comparators (amp x1.32, phase x2.32) failed on the other half (fix-r1/m2_split.txt). M2
# is published, but leaves the per-constituent values and the worst-of-four, and the short_record flag says so.
UNCLAIMED = ("M2",)
UNCLAIMED_REASON = "no accuracy is stated for M2: the real-record check did not support one"
WINDOWS = 4
MIN_COVER = 0.9
PHASE_MIN_AMP_M = 0.005          # R5: no phase claim for a constituent under 5 mm
AMP_FLOOR_M = 0.002              # R2: no stated amplitude accuracy under 2 mm
LOST_PHASE_DEG = 90.0            # the expected |dg| of a phase that is not there at all
POOL_STATIONS = 15
POOL_CANDIDATES = 20             # stations cut per target per round; a target short of 15 usable gets the next 20
POOL_SEARCH = 200                # the farthest a pool widens: the 200 nearest stations of the regime
M2_RATIO = (0.5, 2.0)
FORM_LIMITS = (0.25, 3.0)        # F < 0.25 semidiurnal, F > 3 diurnal, else mixed
COSITE_KM = 2.0
POOL_MIN_GOOD_DAYS = 730.0
SANITY_M2_MAX_M = 10.0
SANITY_RATIO = 3.0
SANITY_NEIGHBOURS = 25
HOLDOUT_N = 50
EARTH_KM = 6371.0
AMP_ND, PHASE_ND = 5, 3
CAL_FILE, CUTS_FILE, MASKS_FILE, MANIFEST = "short_accuracy.json", "cuts.jsonl", "masks.npz", "MANIFEST.json"
EXPECTED_ACCURACY_KEYS = ("cut_to_days", "calibration_records", "amp_uncertainty_m", "phase_uncertainty_deg")
UNCALIBRATED = "short_record_uncalibrated"
IMPLAUSIBLE = "implausible_fit"
INCONSISTENT = "inconsistent_record"


class SelectionMismatch(ValueError):
    """Fits whose Rayleigh selection this code does not rebuild on the TCD dump given (drops_of_fit): the
    calibration and R8 stop, naming every one, rather than leave them out of the pools and the long set."""

    def __init__(self, mismatch: Mapping[str, str]):
        self.mismatch = dict(mismatch)
        head = "; ".join(f"{r}: {w[:200]}" for r, w in sorted(mismatch.items())[:3])
        super().__init__(f"{len(mismatch)} fits' Rayleigh selection is not rebuilt on this TCD dump (wrong dump, or "
                         f"another rule): {head}{' ...' if len(mismatch) > 3 else ''}")


# --- regime -------------------------------------------------------------------------------------------

def main_amps(fit: Mapping) -> dict[str, float]:
    """The published main constituents' amplitudes of a fit (RecordFit.to_dict())."""
    return {c: float(fit["constituents"][c]["amp_m"]) for c in MAIN if c in fit["constituents"]}


def form_class(amps: Mapping[str, float]) -> str:
    semi = amps.get("M2", 0.0) + amps.get("S2", 0.0)
    di = amps.get("K1", 0.0) + amps.get("O1", 0.0)
    f = math.inf if semi == 0 else di / semi
    return "semidiurnal" if f < FORM_LIMITS[0] else ("diurnal" if f > FORM_LIMITS[1] else "mixed")


def reference(amps: Mapping[str, float]) -> tuple[str, float]:
    """The constituent the pool's amplitude ratio is taken on: M2, else the largest published main one."""
    if "M2" in amps:
        return "M2", amps["M2"]
    c = max(amps, key=lambda k: (amps[k], k))
    return c, amps[c]


def same_regime(target: Mapping[str, float], station: Mapping[str, float]) -> bool:
    if form_class(target) != form_class(station):
        return False
    c, a = reference(target)
    b = station.get(c, 0.0)
    return a > 0 and M2_RATIO[0] <= b / a <= M2_RATIO[1]


def km(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    p, q = np.radians(lats), np.radians(lons)
    la, lo = math.radians(lat), math.radians(lon)
    a = np.sin((p - la) / 2) ** 2 + math.cos(la) * np.cos(p) * np.sin((q - lo) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def cosite_clusters(ids: Sequence[str], lats: Sequence[float], lons: Sequence[float],
                    limit_km: float = COSITE_KM) -> dict[str, str]:
    """record id -> cluster id (the smallest record id of its cluster): records within limit_km, chained."""
    parent = list(range(len(ids)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    la, lo = np.asarray(lats, float), np.asarray(lons, float)
    for i in range(len(ids)):
        for j in np.flatnonzero(km(la[i], lo[i], la[i + 1:], lo[i + 1:]) <= limit_km) + i + 1:
            a, b = find(i), find(int(j))
            if a != b:
                parent[max(a, b)] = min(a, b)
    root_ids: dict[int, list[str]] = {}
    for i in range(len(ids)):
        root_ids.setdefault(find(i), []).append(ids[i])
    return {r: min(v) for v in root_ids.values() for r in v}


# --- masks and cuts ------------------------------------------------------------------------------------

def shift_years(hours: np.ndarray, k: int) -> np.ndarray:
    """Hour stamps (hours since 1970, UTC) moved to the same calendar date and hour k years later (k < 0:
    earlier). A 29 February that does not exist in the new year is dropped. Sorted, unique."""
    t = np.asarray(hours, dtype=np.int64).astype("datetime64[h]")
    y = t.astype("datetime64[Y]").astype(np.int64) + 1970
    mon = (t.astype("datetime64[M]") - t.astype("datetime64[Y]").astype("datetime64[M]")).astype(np.int64)
    day = (t.astype("datetime64[D]") - t.astype("datetime64[M]").astype("datetime64[D]")).astype(np.int64)
    hr = (t - t.astype("datetime64[D]").astype("datetime64[h]")).astype(np.int64)
    ny = y + k
    leap = (ny % 4 == 0) & ((ny % 100 != 0) | (ny % 400 == 0))
    ok = ~((mon == 1) & (day == 28) & ~leap)
    m_start = ((ny - 1970) * 12 + mon).astype("datetime64[M]")
    out = (m_start.astype("datetime64[D]") + day).astype("datetime64[h]") + hr
    return np.unique(out[ok].astype(np.int64))


def record_seed(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256(":".join(map(str, parts)).encode()).digest()[:8], "big")


def cut_shifts(station_hours: np.ndarray, target_hours: np.ndarray, *, seed: int, n: int = WINDOWS,
               min_cover: float = MIN_COVER) -> list[tuple[int, np.ndarray]]:
    """Up to n (k, hours) cuts: the target's hours shifted k whole years (k may be 0) onto the station's
    record, kept where the station has >= min_cover of them; hours = the shifted hours the station has.
    The usable shifts are drawn with the seed, then sorted by k."""
    sh = np.asarray(station_hours, dtype=np.int64)
    th = np.asarray(target_hours, dtype=np.int64)
    if len(sh) == 0 or len(th) == 0:
        return []
    y = lambda h: int(np.datetime64(int(h), "h").astype("datetime64[Y]").astype(np.int64)) + 1970  # noqa: E731
    lo_k, hi_k = y(sh[0]) - y(th[0]), y(sh[-1]) - y(th[-1])
    usable = []
    for k in range(lo_k, hi_k + 1):
        s = shift_years(th, k)
        have = s[np.isin(s, sh, assume_unique=True)]
        if len(have) >= min_cover * len(th):
            usable.append((k, have))
    if len(usable) > n:
        pick = np.random.default_rng(seed).choice(len(usable), size=n, replace=False)
        usable = [usable[i] for i in sorted(pick)]
    return usable


def series_hours(series: Series) -> np.ndarray:
    return (np.asarray(series.times_s, dtype=np.int64) // 3600).astype(np.int64)


def subset_hours(series: Series, hours: np.ndarray) -> Series:
    m = np.isin(series_hours(series), hours)
    return Series(series.record_id, series.times_s[m], series.heights_m[m], dict(series.stats))


# --- the metric -----------------------------------------------------------------------------------------

def _wrap(d: float) -> float:
    return abs((d + 180.0) % 360.0 - 180.0)


def cut_errors(full: Mapping[str, Sequence[float]], cut: Mapping[str, Sequence[float]]) -> dict[str, dict]:
    """Per main constituent of the full fit (name -> (amp m, phase deg)): dA (|dA| m), rel (|dA| / A_full,
    only where A_full >= PHASE_MIN_AMP_M), dg (|dg| deg, same condition) and dg_all (no amplitude floor).
    A constituent the cut lost: dA = A_full, rel = 1, dg = dg_all = LOST_PHASE_DEG, lost True."""
    out = {}
    for c in MAIN:
        if c not in full:
            continue
        a, g = float(full[c][0]), float(full[c][1])
        if c in cut:
            da, dg, lost = abs(float(cut[c][0]) - a), _wrap(float(cut[c][1]) - g), False
        else:
            da, dg, lost = a, LOST_PHASE_DEG, True
        big = a >= PHASE_MIN_AMP_M
        out[c] = {"dA": da, "rel": (da / a) if big else None, "dg": dg if big else None, "dg_all": dg, "lost": lost}
    return out


def _median(xs: Iterable[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return statistics.median(v) if v else None


def station_value(cuts: Sequence[Mapping]) -> dict[str, dict] | None:
    """Per constituent, the median over a station's usable cuts of rel, dA, dg and dg_all. None: no usable cut."""
    errs = [w["errors"] for w in cuts if w.get("errors") is not None]
    if not errs:
        return None
    names = sorted({c for e in errs for c in e}, key=MAIN.index)
    return {c: {k: _median(e[c][k] for e in errs if c in e) for k in ("rel", "dA", "dg", "dg_all")} for c in names}


def target_accuracy(target: Mapping[str, float], stations: Sequence[Mapping[str, Mapping]]) -> dict:
    """The stated accuracy of a target (its published main amplitudes) from its stations' values.
    Returns {"by_constituent": {c: {amp_m, phase_deg|None, basis}}, "amp_uncertainty_m", "phase_uncertainty_deg",
    "microtidal", "worst": {"amp": c, "phase": c}}, or {"error": why}."""
    by, unclaimed = {}, {}
    for c, a_t in target.items():
        rels = [s[c]["rel"] for s in stations if c in s and s[c]["rel"] is not None]
        absd = [s[c]["dA"] for s in stations if c in s and s[c]["dA"] is not None]
        if rels:
            amp, basis = max(statistics.median(rels) * a_t, AMP_FLOOR_M), "relative"
        elif absd:
            amp, basis = max(statistics.median(absd), AMP_FLOOR_M), "absolute"
        else:
            continue
        ph = [s[c]["dg"] for s in stations if c in s and s[c]["dg"] is not None]
        phase = statistics.median(ph) if (a_t >= PHASE_MIN_AMP_M and ph) else None
        ph_all = [s[c]["dg_all"] for s in stations if c in s and s[c]["dg_all"] is not None]
        v = {"amp_m": round(amp, AMP_ND), "phase_deg": None if phase is None else round(phase, PHASE_ND),
             "phase_all_deg": round(statistics.median(ph_all), PHASE_ND) if ph_all else None, "basis": basis}
        (unclaimed if c in UNCLAIMED else by)[c] = v
    if not by:
        return {"error": "no main constituent of the target has a calibrated amplitude"}
    microtidal = all(a < PHASE_MIN_AMP_M for a in target.values())
    if microtidal:
        phases = {c: v["phase_all_deg"] for c, v in by.items() if v["phase_all_deg"] is not None}
    else:
        phases = {c: v["phase_deg"] for c, v in by.items() if v["phase_deg"] is not None}
    if not phases:
        return {"error": "no main constituent of the target has a calibrated phase"}
    wa = max(by, key=lambda c: (by[c]["amp_m"], c))
    wp = max(phases, key=lambda c: (phases[c], c))
    return {"by_constituent": by, "unclaimed": unclaimed, "amp_uncertainty_m": by[wa]["amp_m"],
            "phase_uncertainty_deg": phases[wp], "microtidal": microtidal, "worst": {"amp": wa, "phase": wp}}


VALIDATION_NOTE = "accuracy calibrated on cut long records; not validated against real records of this length"
DISPUTED_NOTE = "no phase accuracy is stated: the record's time base is disputed (phase_uncertainty_deg is 90, an unknown phase)"
MICROTIDAL_NOTE = ("no phase accuracy is stated: every main constituent is under 5 mm (phase_uncertainty_deg is 90, "
                   "an unknown phase)")


def restate(t: Mapping, *, limits: Sequence[Mapping] = (), validation: Mapping[str, str] | None = None) -> dict:
    """A target's published claim from its measured values (t["measured"]: c -> {amp_m, phase_deg}), its groups and
    flags: UNCLAIMED constituents and claim limits (R6: {constituent, quantity amp|phase, group}) leave the values; a
    disputed time base or a microtidal record states no phase (90 deg: the median error of an unknown phase, since
    format 1.0 needs a number); a length band the real-record gate could not judge carries VALIDATION_NOTE.
    Returns {by_constituent, amp_uncertainty_m, phase_uncertainty_deg, worst, notes} or {"error"}."""
    groups = set(t.get("groups", ()))
    cut = {(lim["constituent"], lim["quantity"]) for lim in limits if lim["group"] in groups}
    disputed = t.get("time_base_status") == "disputed"
    notes = []
    if "M2" in UNCLAIMED and t.get("publishes_m2"):
        notes.append(UNCLAIMED_REASON)
    by = {}
    for c, v in t["measured"].items():
        if c in UNCLAIMED:
            continue
        amp = None if (c, "amp") in cut else v["amp_m"]
        ph = None if ((c, "phase") in cut or disputed or t.get("microtidal")) else v["phase_deg"]
        if amp is None and ph is None:
            continue
        by[c] = {"amp_m": amp, "phase_deg": ph}
    for c, q in sorted(cut):
        if c in t["measured"]:
            notes.append(f"no {'amplitude' if q == 'amp' else 'phase'} accuracy is stated for {c} on records like this "
                         "one: the real-record check did not support one")
    if disputed:
        notes.append(DISPUTED_NOTE)
    if t.get("microtidal"):
        notes.append(MICROTIDAL_NOTE)
    if validation is not None and validation.get(t.get("band")) != "validated":
        notes.append(VALIDATION_NOTE)
    amps = {c: v["amp_m"] for c, v in by.items() if v["amp_m"] is not None}
    if not amps:
        return {"error": "no main constituent of the target has a claimed amplitude"}
    phases = {c: v["phase_deg"] for c, v in by.items() if v["phase_deg"] is not None}
    wa = max(amps, key=lambda c: (amps[c], c))
    if disputed or t.get("microtidal"):
        phase, wp = LOST_PHASE_DEG, None
    elif phases:
        wp = max(phases, key=lambda c: (phases[c], c))
        phase = phases[wp]
    else:
        return {"error": "no main constituent of the target has a claimed phase"}
    return {"by_constituent": by, "amp_uncertainty_m": amps[wa], "phase_uncertainty_deg": phase,
            "worst": {"amp": wa, "phase": wp}, "notes": notes}


def apply_gate(cal_dir: Path, res: Mapping, *, commit: str | None = None,
               fit_runs: Sequence[Path] | None = None) -> dict:
    """Write an R6 gate result into a calibration: every failed cell becomes a claim limit (its constituent's
    amplitude or phase, or every constituent's for a failed pooled cell, on the records of its group), every length
    band whose pooled cell passed is validated, and each target is restated (restate). The limits and validation
    are recorded in config with the gate's own summary (and, when given, the commit that applied it and the
    identities of the fit runs it was judged on, run_identity); MANIFEST.json is rewritten."""
    cal = read_calibration(cal_dir)
    cal.pop("_sha256", None)
    limits = list(cal["config"].get("claim_limits", []))
    for g, cells in res["groups"].items():
        for c, v in cells.items():
            if v["verdict"] != "FAIL":
                continue
            for q, share in (("amp", v["amp_share_over"]), ("phase", v["phase_share_over"])):
                if share is not None and not (res["band"][0] <= share <= res["band"][1]):
                    for k in (MAIN if c == "pooled" else (c,)):
                        lim = {"constituent": k, "quantity": q, "group": g}
                        if lim not in limits:
                            limits.append(lim)
    bands = ("<=30", "31-90", "91-182", "183-364")
    validation = {b: ("validated" if res["groups"][b]["pooled"]["verdict"] == "pass" else
                      res["groups"][b]["pooled"]["verdict"]) for b in bands}
    for t, v in cal["targets"].items():
        if "measured" not in v:
            continue
        for k in ("by_constituent", "amp_uncertainty_m", "phase_uncertainty_deg", "worst", "notes", "error"):
            v.pop(k, None)
        v.update(restate(v, limits=limits, validation=validation))
    cal["config"]["claim_limits"] = limits
    cal["config"]["validation"] = validation
    entry = {k: res[k] for k in ("failed_cells", "passed", "records", "comparators")}
    if commit is not None:
        entry["commit"] = commit
    if fit_runs is not None:
        entry["fit_runs"] = [run_identity(Path(d)) for d in fit_runs]
    cal["config"].setdefault("gates", []).append(entry)
    write_calibration(cal, cal_dir)
    return cal


# --- sanity (R8) ----------------------------------------------------------------------------------------

def implausible(records: Sequence[Mapping], long_ids: set[str], clusters: Mapping[str, str]) -> dict[str, dict]:
    """record id -> {"reason": implausible_fit, "detail"} for each record whose M2 is over SANITY_M2_MAX_M, or
    over SANITY_RATIO x the largest M2 of its SANITY_NEIGHBOURS nearest long records (its co-sites excluded).
    records: {"record", "lat", "lon", "amps"}."""
    longs = [r for r in records if r["record"] in long_ids and "M2" in r["amps"]]
    la = np.array([r["lat"] for r in longs]); lo = np.array([r["lon"] for r in longs])
    m2 = np.array([r["amps"]["M2"] for r in longs])
    lc = np.array([clusters[r["record"]] for r in longs])
    out = {}
    for r in records:
        a = r["amps"].get("M2")
        if a is None:
            continue
        if a > SANITY_M2_MAX_M:
            out[r["record"]] = {"reason": IMPLAUSIBLE, "detail": f"M2 {a:.2f} m is over {SANITY_M2_MAX_M:.0f} m"}
            continue
        d = km(r["lat"], r["lon"], la, lo)
        keep = lc != clusters[r["record"]]
        order = np.argsort(d, kind="stable")
        idx = order[keep[order]][:SANITY_NEIGHBOURS]
        if len(idx) == 0:
            continue
        top = float(m2[idx].max())
        if a > SANITY_RATIO * top:
            out[r["record"]] = {"reason": IMPLAUSIBLE,
                                "detail": f"M2 {a:.2f} m is over {SANITY_RATIO:g} x the largest M2 ({top:.2f} m) of "
                                          f"its {len(idx)} nearest long records (to {float(d[idx].max()):.0f} km)"}
    return out


# --- sign inversion (implausible_fit) -----------------------------------------------------------------

# A record whose heights are sign-inverted has every constituent 180 deg from the truth, the same in both bands (a
# clock offset shifts the semidiurnal band twice as far as the diurnal one). Against EOT20 at the record's cell (the
# time-base audit's weak inputs), over the fitted records of the release run (fit-5c + overlay): of 3983 records
# where EOT20 can judge all four main constituents (below), 6 have every one within 23 deg of 180 deg (band means
# within 8 deg of each other); the next record is 61 deg from it, and 3201 have all four within 30 deg of EOT20.
INVERSION_KM = 15.0              # EOT20 cell within this distance (timebase_weak.E_MAX_KM)
INVERSION_MIN_AMP_M = PHASE_MIN_AMP_M   # each main constituent at least 5 mm in the fit and in EOT20 (R5)
INVERSION_AMP_RATIO = (0.5, 2.0)        # fit / EOT20 amplitude of each (an inversion leaves amplitudes alone)
INVERSION_WINDOW_DEG = 30.0      # every main constituent within 180 +- this of EOT20
INVERSION_BAND_DEG = 30.0        # the semidiurnal and diurnal mean shifts within this of each other


def _circ_mean(degs: Sequence[float]) -> float:
    z = sum(complex(math.cos(math.radians(d)), math.sin(math.radians(d))) for d in degs)
    return math.degrees(math.atan2(z.imag, z.real)) % 360.0


def inverted_against_eot20(fit: Mapping, eot: Mapping | None) -> str | None:
    """Why the fit (RecordFit.to_dict()) is sign-inverted against EOT20, or None. eot: the weak-inputs record's
    "eot" ({constituent: [amp m, phase deg], "dist_km"}), None where there is none. EOT20 judges only when its cell
    is within INVERSION_KM and every main constituent is at least INVERSION_MIN_AMP_M in both, with amplitude ratio
    within INVERSION_AMP_RATIO; then the fit is inverted when every main phase is within INVERSION_WINDOW_DEG of
    180 deg from EOT20's and the two bands' mean shifts agree within INVERSION_BAND_DEG."""
    if not eot or eot.get("dist_km") is None or float(eot["dist_km"]) > INVERSION_KM:
        return None
    cons = fit["constituents"]
    shift = {}
    for c in MAIN:
        if c not in cons or c not in eot:
            return None
        a, (b, g) = float(cons[c]["amp_m"]), (float(eot[c][0]), float(eot[c][1]))
        if a < INVERSION_MIN_AMP_M or b < INVERSION_MIN_AMP_M or not INVERSION_AMP_RATIO[0] <= a / b <= INVERSION_AMP_RATIO[1]:
            return None
        shift[c] = (float(cons[c]["phase_deg"]) - g) % 360.0
    if any(abs(s - 180.0) > INVERSION_WINDOW_DEG for s in shift.values()):
        return None
    semi, di = _circ_mean([shift["M2"], shift["S2"]]), _circ_mean([shift["K1"], shift["O1"]])
    if abs(_wrap(semi - di)) > INVERSION_BAND_DEG:
        return None
    return ("sign-inverted: every main constituent is 180 deg from EOT20 (" +
            ", ".join(f"{c} {shift[c]:.0f}" for c in MAIN) + f" deg; EOT20 cell {float(eot['dist_km']):.1f} km)")


# --- long-period lines of a short record --------------------------------------------------------------

# Under a year the long-period lines are mostly weather, river flow or datum drift, not tide: on the release run,
# 145 short sets published one of them at 10 cm or more, and 76 of the 102 with a long record nearby were over 3 x
# its value. The fit's own uncertainty does not catch them (u_m is underestimated there: waal_km_922 MF 0.413 m passed
# a 2 u_m test on a river reach with no M2). So a short record publishes MM, MF or MSF only when a long record of the
# same water within 3 km confirms the value, and only over 3 cycles; SA and SSA never.
LONG_PERIOD = ("SA", "SSA", "MM", "MF", "MSF")
LONG_PERIOD_NEVER_SHORT = ("SA", "SSA")
LONG_PERIOD_MIN_CYCLES = 3.0     # the record covers at least 3 periods of the constituent
LONG_PERIOD_REF_KM = 3.0         # a long record within this distance, in the same water (below), is the reference
LONG_PERIOD_CONFIRM_M = 0.03     # confirmed: |z_short - z_long| at most this ...
LONG_PERIOD_CONFIRM_SHARE = 0.5  # ... or at most this x the long record's amplitude, whichever is larger
LONG_PERIOD_SAME_WATER_M2_RATIO = (0.5, 2.0)    # same water: the reference's M2 amplitude over the record's
LONG_PERIOD_SAME_WATER_M2_DEG = 45.0            # ... and M2 phases within this
LONG_PERIOD_RULE = "long_period_rule"           # format 1.0 dropped_reason


def long_period_reference(fit: Mapping, lat: float, lon: float, longs: Sequence[Mapping]) -> dict | None:
    """The nearest long record within LONG_PERIOD_REF_KM in the same water (M2 amplitude ratio within
    LONG_PERIOD_SAME_WATER_M2_RATIO, M2 phase within LONG_PERIOD_SAME_WATER_M2_DEG), or None. longs: {"record", "lat",
    "lon", "fit"} of the run's long records (no Rayleigh drop, not refused). Returns {"record", "km", "lines"}: lines
    is [amp m, phase deg] of each of MM, MF, MSF the reference publishes, None for one it does not (under its noise:
    amplitude 0 for the confirmation)."""
    m2 = fit["constituents"].get("M2")
    if m2 is None or not longs:
        return None
    d = km(lat, lon, np.array([float(r["lat"]) for r in longs]), np.array([float(r["lon"]) for r in longs]))
    for i in np.argsort(d, kind="stable"):
        if d[i] > LONG_PERIOD_REF_KM:
            break
        rc = longs[i]["fit"]["constituents"]
        r2 = rc.get("M2")
        if r2 is None or not (LONG_PERIOD_SAME_WATER_M2_RATIO[0] <= r2["amp_m"] / m2["amp_m"] <= LONG_PERIOD_SAME_WATER_M2_RATIO[1]) \
                or abs(_wrap(r2["phase_deg"] - m2["phase_deg"])) > LONG_PERIOD_SAME_WATER_M2_DEG:
            continue
        lines = {c: ([float(rc[c]["amp_m"]), float(rc[c]["phase_deg"])] if c in rc else None)
                 for c in LONG_PERIOD if c not in LONG_PERIOD_NEVER_SHORT}
        return {"record": longs[i]["record"], "km": round(float(d[i]), 2), "lines": lines}
    return None


def long_period_drops(fit: Mapping, speeds: Mapping[str, float], reference: Mapping | None) -> list[dict]:
    """The long-period lines a short record's fit (RecordFit.to_dict()) does not publish, as format 1.0
    dropped_constituents items (dropped_reason long_period_rule, detail: every test it failed), sorted by name.
    SA and SSA always; MM, MF and MSF unless the record covers at least LONG_PERIOD_MIN_CYCLES of its periods
    (rayleigh_hours x speed / 360) and the reference (long_period_reference) confirms it: the vector difference is at
    most LONG_PERIOD_CONFIRM_M or LONG_PERIOD_CONFIRM_SHARE x the reference's amplitude, whichever is larger. With no
    reference nothing confirms it. Call it for a short record only (one with Rayleigh drops)."""
    out = []
    hours = float(fit["rayleigh_hours"])
    for name in LONG_PERIOD:
        c = fit["constituents"].get(name)
        if c is None:
            continue
        if name in LONG_PERIOD_NEVER_SHORT:
            out.append({"name": name, "dropped_reason": LONG_PERIOD_RULE,
                        "detail": "a short record never publishes SA or SSA (the seasonal cycle is not tide in under a year)"})
            continue
        why = []
        cycles = hours * float(speeds[name]) / 360.0
        if cycles < LONG_PERIOD_MIN_CYCLES:
            why.append(f"the record covers {cycles:.1f} cycles, under {LONG_PERIOD_MIN_CYCLES:g}")
        a = float(c["amp_m"])
        if reference is None:
            why.append(f"no long record of the same water within {LONG_PERIOD_REF_KM:g} km confirms its {a:.3f} m")
        else:
            line = reference["lines"][name]
            ref_amp = line[0] if line else 0.0
            z = _vec(a, float(c["phase_deg"])) - (_vec(line[0], line[1]) if line else 0j)
            limit = max(LONG_PERIOD_CONFIRM_M, LONG_PERIOD_CONFIRM_SHARE * ref_amp)
            if abs(z) > limit:
                why.append(f"{a:.3f} m differs by {abs(z):.3f} m (over {limit:.3f} m) from the long record "
                           f"{reference['record']} ({reference['km']:.1f} km), which has "
                           + (f"{ref_amp:.3f} m" if line else "it under its noise"))
        if why:
            out.append({"name": name, "dropped_reason": LONG_PERIOD_RULE, "detail": "; ".join(why)})
    return sorted(out, key=lambda o: o["name"])


# --- self-consistency (inconsistent_record) ----------------------------------------------------------

HALVES_MIN_AMP_M = 0.02          # a constituent under 2 cm is too small for the halves to judge
HALVES_MISSING_AMP_M = 0.05      # M2 or the dominant constituent missing from a half is judged from 5 cm
HALVES_M2 = 0.5                  # |z1 - z2| / A over which M2's halves disagree
HALVES_K1_S2 = 1.0               # the same for K1 and S2 (P1 and K2 beat in the halves), when >= 0.6 x dominant
HALVES_K1_S2_SHARE = 0.6
OWN_U_RATIO = 5.0                # the fit's own u_m over the stated amplitude accuracy ...
OWN_U_AMP_SHARE = 1.0            # ... when u_m is also over the constituent's own amplitude (the record cannot fix it)


def _vec(a: float, g: float) -> complex:
    return complex(a * math.cos(math.radians(g)), a * math.sin(math.radians(g)))


def inconsistent_halves(fit: Mapping) -> str | None:
    """Why the two halves of a fit (RecordFit.to_dict()) say it is not one record, or None. Over the published main
    constituents (A = full-fit amplitude, D = |z_half1 - z_half2| / A): M2, or the dominant one, at A >= 5 cm missing
    from a half; M2 at A >= 2 cm with D > HALVES_M2; K1 or S2 at A >= 2 cm and >= 0.6 x the dominant amplitude with D >
    HALVES_K1_S2. A half that could not be fitted says nothing (no refusal from it)."""
    h = fit.get("halves") or {}
    h1, h2 = h.get("half1"), h.get("half2")
    if not h1 or not h2:
        return None
    amps = {c: float(fit["constituents"][c]["amp_m"]) for c in MAIN if c in fit["constituents"]}
    if not amps:
        return None
    dom = max(amps, key=lambda c: (amps[c], c))
    why = []
    for c, a in amps.items():
        c1, c2 = h1["constituents"].get(c), h2["constituents"].get(c)
        if c1 is None or c2 is None:
            if (c == "M2" or c == dom) and a >= HALVES_MISSING_AMP_M:
                why.append(f"{c} ({a:.3f} m) is missing from {'the first' if c1 is None else 'the second'} half")
            continue
        if a < HALVES_MIN_AMP_M:
            continue
        d = abs(_vec(c1["amp_m"], c1["phase_deg"]) - _vec(c2["amp_m"], c2["phase_deg"])) / a
        if c == "M2" and d > HALVES_M2 or c in ("K1", "S2") and a >= HALVES_K1_S2_SHARE * amps[dom] and d > HALVES_K1_S2:
            why.append(f"the halves' {c} differ by {d:.2f} x its amplitude ({c1['amp_m']:.3f} m {c1['phase_deg']:.0f} deg "
                       f"against {c2['amp_m']:.3f} m {c2['phase_deg']:.0f} deg)")
    return "; ".join(why) or None


def own_u_exceeds(fit: Mapping, by_constituent: Mapping[str, Mapping]) -> str | None:
    """A stated amplitude accuracy the record's own fit uncertainty (u_m, from its halves or band noise) exceeds
    OWN_U_RATIO times while u_m is also over OWN_U_AMP_SHARE x the constituent's amplitude, or None. (u_m over 5 x the
    stated value alone is common for K1 and S2, whose halves beat with P1 and K2, on records the comparators confirm.)"""
    for c, v in by_constituent.items():
        k = fit["constituents"].get(c, {})
        u, a = k.get("u_m"), k.get("amp_m")
        if u is not None and a and v.get("amp_m") and u > OWN_U_RATIO * v["amp_m"] and u > OWN_U_AMP_SHARE * a:
            return (f"{c}: the fit's own uncertainty {u:.4f} m is over {OWN_U_RATIO:g} x the stated {v['amp_m']:.4f} m "
                    f"and over its amplitude {a:.4f} m")
    return None


def run_implausible(rows: Mapping[str, Mapping], fits: Mapping[str, Mapping], table: TcdTable) -> dict[str, dict]:
    """R8 on a whole fit run, with no calibration: record id -> {reason, detail} (implausible). rows: the run's
    fitted rows (lat, lon); fits: their RecordFit.to_dict(). Long records are those whose selection keeps every
    candidate. A fit whose selection this code does not rebuild on `table` (another TCD's dump, another rule) is
    an error (ValueError naming every one), never a record quietly left out of the long set."""
    ids = sorted(rows)
    clusters = cosite_clusters(ids, [float(rows[r]["lat"]) for r in ids], [float(rows[r]["lon"]) for r in ids])
    recs, long_ids, mismatch = [], set(), {}
    for r in ids:
        recs.append({"record": r, "lat": float(rows[r]["lat"]), "lon": float(rows[r]["lon"]), "amps": main_amps(fits[r])})
        try:
            if not drops_of_fit(table, fits[r]):
                long_ids.add(r)
        except (ValueError, KeyError, TypeError) as e:
            mismatch[r] = f"{type(e).__name__}: {e}"
    if mismatch:
        raise SelectionMismatch(mismatch)
    return implausible(recs, long_ids, clusters)


# --- calibration file -----------------------------------------------------------------------------------

def _canonical(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=1, separators=(",", ": "), allow_nan=False) + "\n").encode()


def fit_sha256(fit: Mapping) -> str:
    """The calibration's key to the exact fit a target was calibrated for (any refit changes it)."""
    return hashlib.sha256(json.dumps(fit, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_calibration(cal: Mapping, out_dir: Path) -> Path:
    """short_accuracy.json, then MANIFEST.json (written last) with the sha256 and size of it and of
    cuts.jsonl and masks.npz when present."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CAL_FILE).write_bytes(_canonical(cal))
    files = [{"path": n, "sha256": sha256_file(out_dir / n), "size": (out_dir / n).stat().st_size}
             for n in (CAL_FILE, CUTS_FILE, MASKS_FILE) if (out_dir / n).exists()]
    man = {"dataset": "gesla short-record accuracy calibration", "cal_version": cal["cal_version"], "files": files}
    (out_dir / MANIFEST).write_bytes(_canonical(man))
    return out_dir / CAL_FILE


def read_calibration(in_dir: Path) -> dict:
    """The calibration, after every MANIFEST entry's sha256 and size and the cal_version are checked."""
    man = json.loads((in_dir / MANIFEST).read_text())
    entries = {f["path"]: f for f in man["files"]}
    if CAL_FILE not in entries:
        raise ValueError(f"{in_dir}/{MANIFEST}: no entry for {CAL_FILE}")
    for name, e in entries.items():
        p = in_dir / name
        got, size = sha256_file(p), p.stat().st_size
        if got != e["sha256"] or size != e["size"]:
            raise ValueError(f"{p}: sha256 {got} size {size}, manifest has {e['sha256']} size {e['size']}")
    cal = json.loads((in_dir / CAL_FILE).read_bytes())
    if cal.get("cal_version") != CAL_VERSION or man.get("cal_version") != CAL_VERSION:
        raise ValueError(f"{in_dir}: cal_version {cal.get('cal_version')!r}, expected {CAL_VERSION}")
    cal["_sha256"] = entries[CAL_FILE]["sha256"]
    return cal


# --- fit document v3 fields -----------------------------------------------------------------------------

def drops_of_fit(table: TcdTable, fit: Mapping) -> list[RayleighDrop]:
    """The Rayleigh drops of a fit (RecordFit.to_dict()), replayed on its rayleigh_hours. The replay must
    rebuild the fit's rayleigh_kept exactly, else ValueError (a different TCD or rule). The replay walks the
    fit's own candidate list (its candidate_list, "base" when absent)."""
    t = float(fit["rayleigh_hours"])
    name = fit.get("candidate_list", "base")
    if name not in CANDIDATE_LISTS:
        raise ValueError(f"candidate_list {name!r} is not one of {sorted(CANDIDATE_LISTS)}")
    cands = CANDIDATE_LISTS[name]
    kept = rayleigh_select(table, t, cands)
    if kept != list(fit["rayleigh_kept"]):
        raise ValueError(f"Rayleigh replay at {t} h keeps {kept}, the fit kept {fit['rayleigh_kept']}")
    return rayleigh_drops(table, t, cands)


def dropped_constituents(drops: Sequence[Mapping], snr_dropped: Sequence[str]) -> list[dict]:
    """Format 1.0 dropped_constituents items, sorted by name: a conflict is rayleigh + not_separable_from
    (the partner, also when the noise rule later dropped it); a period longer than the record is
    other + detail; a noise drop is noise."""
    out = []
    for d in drops:
        if d["kind"] == CONFLICT:
            out.append({"name": d["name"], "dropped_reason": "rayleigh", "not_separable_from": d["partner"]})
        elif d["kind"] == PERIOD_EXCEEDS_RECORD:
            out.append({"name": d["name"], "dropped_reason": "other",
                        "detail": f"period longer than the record (needs {d['needs_days']:.1f} days)"})
        else:
            raise ValueError(f"{d['name']}: unknown drop kind {d['kind']!r}")
    out += [{"name": n, "dropped_reason": "noise"} for n in snr_dropped]
    names = [o["name"] for o in out]
    if len(set(names)) != len(names):
        raise ValueError(f"a constituent is dropped twice: {sorted(n for n in names if names.count(n) > 1)}")
    return sorted(out, key=lambda o: o["name"])


def short_record_reason(good_hours: float, drops: Sequence[Mapping], limit: int = 800,
                        notes: Sequence[str] = ()) -> str:
    """One line for the short_record flag: the accuracy notes first (restate: the M2 note first), then the good
    days, the pairs (main ones first) and the period drops."""
    pairs = [d for d in drops if d["kind"] == CONFLICT]
    rank = {n: i for i, n in enumerate(CANDIDATES)}
    pairs.sort(key=lambda d: (rank.get(d["partner"], 99), rank.get(d["name"], 99)))
    parts = [f"{d['name']} from {d['partner']}" for d in pairs]
    long_p = [d["name"] for d in drops if d["kind"] == PERIOD_EXCEEDS_RECORD]
    s = "".join(f"{n}; " for n in notes) + f"{good_hours / 24:.1f} days of good data"
    if parts:
        s += f": cannot separate {', '.join(parts)}"
    if long_p:
        s += f"{';' if parts else ':'} {', '.join(long_p)} {'has' if len(long_p) == 1 else 'have'} a period longer than the record"
    return s if len(s) <= limit else s[:limit - 1].rstrip(", ") + "…"


def flag_values(by_constituent: Mapping[str, Mapping]) -> dict[str, float | None]:
    """The short_record flag's `values` (numbers only): each main constituent's stated accuracy."""
    out: dict[str, float | None] = {}
    for c, v in by_constituent.items():
        out[f"{c}_amp_uncertainty_m"] = v["amp_m"]
        out[f"{c}_phase_uncertainty_deg"] = v["phase_deg"]
    return out


def _refusal(reason: str, detail: str) -> dict:
    return {"reason": reason, "detail": detail}


def fit_document_v3_fields(table: TcdTable, fit: Mapping, *, record_id: str, good_hours: float,
                           calibration: Mapping | None) -> dict:
    """What a v3 fit document adds to a v2 one. Never raises for a record-level problem: it reports it.

    rayleigh_drops: RayleighDrop.to_dict per drop ([] for a record that resolves every candidate).
    expected_accuracy: the four format 1.0 keys for a calibrated short record, else None.
    accuracy_by_constituent: {c: {amp_m, phase_deg}} behind it (for the short_record flag's values), or None.
    accuracy_unclaimed: the published main constituents with no stated accuracy (UNCLAIMED), or None.
    accuracy_notes: the lines the short_record flag's reason starts with (restate: the M2 note first), or None.
    microtidal: True / False for a calibrated short record, else None.
    refusal: None, or {"reason", "detail"}: implausible_fit (R8, from the calibration's sanity list, any
    record), or short_record_uncalibrated (a short record with no calibration, one the calibration does not
    cover or could not calibrate, or whose good hours or fit differ from the calibrated ones, or a fit whose
    Rayleigh selection the current rule does not rebuild)."""
    out = {"rayleigh_drops": [], "expected_accuracy": None, "accuracy_by_constituent": None, "microtidal": None,
           "accuracy_unclaimed": None, "accuracy_notes": None, "refusal": None}
    try:
        drops = [d.to_dict() for d in drops_of_fit(table, fit)]
    except (ValueError, KeyError, TypeError) as e:
        out["refusal"] = _refusal(UNCALIBRATED, f"{record_id}: the fit's Rayleigh selection is not this code's ({e})")
        return out
    out["rayleigh_drops"] = drops
    if calibration is not None and record_id in calibration.get("refused", {}):
        r = calibration["refused"][record_id]
        out["refusal"] = _refusal(r["reason"], f"{record_id}: {r['detail']}")
        return out
    if not drops:
        return out
    if calibration is None:
        out["refusal"] = _refusal(UNCALIBRATED, f"{record_id}: short record and no calibration given")
        return out
    t = calibration.get("targets", {}).get(record_id)
    if t is None:
        out["refusal"] = _refusal(UNCALIBRATED, f"{record_id}: not a target of the calibration")
    elif "error" in t:
        out["refusal"] = _refusal(UNCALIBRATED, f"{record_id}: {t['error']}")
    elif round(float(good_hours), 1) != t["good_hours"]:
        out["refusal"] = _refusal(UNCALIBRATED, f"{record_id}: calibrated at {t['good_hours']} good hours, "
                                                f"the document has {round(float(good_hours), 1)}")
    elif fit_sha256(fit) != t["fit_sha256"]:
        out["refusal"] = _refusal(UNCALIBRATED, f"{record_id}: the fit differs from the one calibrated")
    else:
        out["expected_accuracy"] = {k: t[k] for k in EXPECTED_ACCURACY_KEYS}
        out["accuracy_by_constituent"] = {c: {"amp_m": v["amp_m"], "phase_deg": v["phase_deg"]}
                                          for c, v in t["by_constituent"].items()}
        out["microtidal"] = t["microtidal"]
        out["accuracy_unclaimed"] = sorted(t.get("unclaimed", {}), key=MAIN.index)
        out["accuracy_notes"] = list(t.get("notes", []))
    return out


# --- the calibration run --------------------------------------------------------------------------------

_W: dict = {}


def _init_worker(fit_dirs: Sequence[str], masks_path: str | None, tcd_path: str) -> None:
    from .read_gesla import GeslaRelease, load_contributor_roles
    repo = Path(__file__).resolve().parents[3]
    rel = GeslaRelease()
    rel.metadata()
    _W.update(rel=rel, roles=load_contributor_roles(repo / "data/gesla/contributor_roles.csv"),
              table=TcdTable.load(Path(tcd_path)), fit_dirs=[Path(d) for d in fit_dirs])
    if masks_path:
        with np.load(masks_path) as z:
            _W["masks"] = {k: z[k] for k in z.files}


def _fit_path(rid: str) -> Path:
    for d in reversed(_W["fit_dirs"]):          # the overlay run (last) wins
        p = d / "fits" / f"{rid}.json"
        if p.exists():
            return p
    raise FileNotFoundError(f"{rid}: no fit in {[str(d) for d in _W['fit_dirs']]}")


def _fit_window(rid: str) -> Series:
    """A record's cleaned fit span (in_tcd_years(last_years(clean))) with its declared time base, which
    must be utc_instant (a missing TIME ZONE HOURS refuses the record)."""
    from .qc import clean
    from .timebase_audit import declared_correction
    meta = _W["rel"].metadata()[rid]
    tz = (meta.get("TIME ZONE HOURS") or "").strip()
    if not tz:
        raise ValueError(f"{rid}: no TIME ZONE HOURS")
    corr = declared_correction(int(float(tz)))
    if corr.label != "utc_instant":
        raise ValueError(f"{rid}: declared time base {corr.label}; the calibration takes utc_instant records only")
    series = clean(_W["rel"].record(rid, _W["roles"]), corr)
    return in_tcd_years(_W["table"], last_years(series, FIT_SPAN_YEARS))


def _mask_job(rid: str) -> tuple[str, np.ndarray | None, str | None]:
    try:
        return rid, series_hours(_fit_window(rid)), None
    except Exception as e:  # noqa: BLE001 - reported per target, never guessed
        return rid, None, f"{type(e).__name__}: {str(e)[:300]}"


CUT_LIST_RULE = "select_candidate_list on the cut, the station's GESLA COUNTRY"   # in every cuts.jsonl stamp


def cut_candidate_list(table: TcdTable, cut: Series, country: str | None) -> str:
    """The candidate list a cut is refitted with: the one fit_run would choose for a record of that data
    (select_constituents.select_candidate_list on the cut, with the station's country), never the station's own
    full-record list. A TCD dump without the extended candidates is a ValueError in scope, as in fit_run."""
    name, _ = select_candidate_list(table, cut, country=country)
    return name


def cut_stamp(commit: str, tcd_table_sha256: str, seed: int) -> dict:
    """What a cuts.jsonl row must have been made with to be reused on resume: the commit (the code and its
    settings), the TCD dump, the seed and the cut list rule."""
    return {"commit": commit, "tcd_table_sha256": tcd_table_sha256, "seed": seed, "cut_list_rule": CUT_LIST_RULE}


def reusable(row: Mapping, stamp: Mapping, targets: set[str], full_sha256: str) -> bool:
    """A saved cuts.jsonl row is reused only when its stamp is this run's, it was cut from the station's full fit,
    it holds exactly the targets asked, and every cut names its candidate list (a row from before the stamp, or of
    another commit, TCD dump or seed, is cut again)."""
    return (row.get("stamp") == stamp and row.get("full_sha256") == full_sha256 and set(row.get("cuts", {})) == targets
            and all("candidate_list" in c for cs in row["cuts"].values() for c in cs))


def _station_job(station: str, targets: Sequence[str]) -> dict:
    """Every cut of one station for its targets (target id -> cuts)."""
    from .fit import FitError
    t0 = time.time()
    table = _W["table"]
    full_fit = json.loads(_fit_path(station).read_text())
    full = {c: (v["amp_m"], v["phase_deg"]) for c, v in full_fit["constituents"].items() if c in MAIN}
    country = _W["rel"].metadata()[station].get("COUNTRY")
    win = _fit_window(station)
    sh = series_hours(win)
    out = {}
    for tid in targets:
        cuts = []
        for k, hours in cut_shifts(sh, _W["masks"][tid], seed=record_seed(tid, station)):
            s = subset_hours(win, hours)
            row = {"k": k, "n": len(s), "target_n": int(len(_W["masks"][tid]))}
            try:
                row["candidate_list"] = clist = cut_candidate_list(table, s, country)
                a = analyse(table, s, candidates=CANDIDATE_LISTS[clist])
            except FitError as e:
                row.update(errors=None, error=f"FitError: {str(e)[:200]}")
            else:
                cut = a.consts()
                row["cut"] = {c: [round(cut[c][0], 6), round(cut[c][1], 4)] for c in MAIN if c in cut}
                row["errors"] = {c: {k2: (None if v2 is None else (round(v2, 7) if not isinstance(v2, bool) else v2))
                                     for k2, v2 in e.items()} for c, e in cut_errors(full, cut).items()}
            cuts.append(row)
        out[tid] = cuts
    return {"station": station, "full_sha256": fit_sha256(full_fit), "cuts": out, "secs": round(time.time() - t0, 2)}


def fit_runs_tcd(fit_runs: Sequence[Path]) -> Path:
    """The TCD dump the fit runs were made with (each run's timebase_manifest.json inputs.tcd_table): the
    calibration replays their Rayleigh selection and refits their cuts with it. Every run must name one, all
    the same, and the file must still have that sha256, else ValueError (a calibration on another TCD's
    dump would not be the fits' own). A relative path is taken from the (first) run's directory."""
    seen = {}
    for d in fit_runs:
        m = d / "timebase_manifest.json"
        t = json.loads(m.read_text()).get("inputs", {}).get("tcd_table") if m.exists() else None
        if not t or not t.get("path") or not t.get("sha256"):
            raise ValueError(f"{d}: the fit run names no TCD dump (timebase_manifest.json inputs.tcd_table)")
        seen[str(d)] = t
    shas = {t["sha256"] for t in seen.values()}
    if len(shas) != 1:
        raise ValueError(f"the fit runs were made with different TCD dumps: {seen}")
    d, t = next(iter(seen.items()))
    p = Path(t["path"])
    if not p.is_absolute():                     # a relative path is the manifest's, not the caller's cwd's
        p = Path(d) / p
    if not p.exists() or sha256_file(p) != t["sha256"]:
        raise ValueError(f"{p}: not the TCD dump the fit runs name (sha256 {t['sha256']})")
    return p


def run_identity(d: Path) -> dict:
    """A fit run as a calibration names it in inputs.fit_runs."""
    return {"dir": str(d), "results_jsonl_sha256": sha256_file(d / "results.jsonl"),
            "manifest_sha256": sha256_file(d / "timebase_manifest.json")
            if (d / "timebase_manifest.json").exists() else None}


def check_bound(cal: Mapping, fit_runs: Sequence[Path], *, tcd_table_sha256: str | None = None) -> None:
    """A build must use the calibration made from the fit runs it builds (the run, then the overlay): their
    results.jsonl and timebase_manifest.json (which names every fit's sha256) must be the ones the calibration
    names, in order, and (when tcd_table_sha256 is given) the build's TCD dump must be the one the calibration was
    made on, else ValueError naming each difference. Its targets are keyed to those fits (fit_sha256): with any
    other run the short records it would refuse are not uncalibrated but calibrated on other fits, so the build
    stops."""
    inputs = cal.get("inputs", {})
    want = [{k: r.get(k) for k in ("results_jsonl_sha256", "manifest_sha256")} for r in inputs.get("fit_runs", [])]
    have = [{k: r[k] for k in ("results_jsonl_sha256", "manifest_sha256")} for r in map(run_identity, fit_runs)]
    why = []
    if not want or want != have:
        named = [r.get("dir") for r in inputs.get("fit_runs", [])]
        why.append(f"the calibration was made from the fit runs {named} {want}, the build uses "
                   f"{[str(d) for d in fit_runs]} {have}")
    if tcd_table_sha256 is not None and inputs.get("tcd_table_sha256") != tcd_table_sha256:
        why.append(f"the calibration was made on the TCD dump {inputs.get('tcd_table_sha256')}, the build uses "
                   f"{tcd_table_sha256}")
    if why:
        raise ValueError("; ".join(why) + ": re-run gesla.short_accuracy on the build's fit runs")


def load_rows(fit_runs: Sequence[Path]) -> tuple[dict[str, dict], dict[str, Path]]:
    """Fitted rows and their fit dir; a later run (the overlay) replaces an earlier one's record."""
    rows, where = {}, {}
    for d in fit_runs:
        for x in (d / "results.jsonl").read_text().splitlines():
            if x.strip():
                r = json.loads(x)
                if r.get("outcome") == "fitted":
                    rows[r["record"]] = r
                    where[r["record"]] = d
                elif r["record"] in rows and d != fit_runs[0]:
                    raise ValueError(f"{r['record']}: the overlay run did not fit it ({r.get('outcome')})")
    return rows, where


def _git(args: Sequence[str]) -> str:
    repo = Path(__file__).resolve().parents[3]
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def plan(rows: Mapping[str, dict], fits: Mapping[str, dict], table: TcdTable, meta: Mapping[str, Mapping],
         *, seed: int = 0, holdout_n: int = HOLDOUT_N) -> dict:
    """Everything decided before any cut: clusters, refusals (R8), stations, targets, hold-out, pools. A fit whose
    Rayleigh selection is not rebuilt on `table` raises SelectionMismatch naming every one."""
    ids = sorted(rows)
    clusters = cosite_clusters(ids, [rows[r]["lat"] for r in ids], [rows[r]["lon"] for r in ids])
    recs, long_ids, short, unfit = [], set(), {}, {}
    for r in ids:
        f = fits[r]
        amps = main_amps(f)
        recs.append({"record": r, "lat": float(rows[r]["lat"]), "lon": float(rows[r]["lon"]), "amps": amps})
        try:
            drops = drops_of_fit(table, f)
        except (ValueError, KeyError, TypeError) as e:
            unfit[r] = f"{type(e).__name__}: {e}"
            continue
        if drops:
            short[r] = amps
        else:
            long_ids.add(r)
    if unfit:                                   # a fit left out of the pools and the long set would bias both
        raise SelectionMismatch(unfit)
    refused = implausible(recs, long_ids, clusters)
    by_id = {x["record"]: x for x in recs}
    pool_ok = {}
    for r in sorted(long_ids):
        row = rows[r]
        if r in refused or row["data_hours"] < POOL_MIN_GOOD_DAYS * 24 or row["time_base"] != "utc_instant" \
                or row["months_dropped"] != 0 or row.get("time_base_status") == "disputed":
            continue
        if not (meta[r].get("TIME ZONE HOURS") or "").strip():
            continue
        pool_ok[r] = row["data_hours"]
    stations: dict[str, str] = {}                 # cluster -> representative record
    for r, gh in pool_ok.items():
        c = clusters[r]
        if c not in stations or (gh, r) > (pool_ok[stations[c]], stations[c]):
            stations[c] = r
    st_ids = sorted(stations.values())
    rng = np.random.default_rng(record_seed("holdout", seed))
    holdout = sorted(rng.choice(st_ids, size=min(holdout_n, len(st_ids)), replace=False).tolist())
    cal_st = [s for s in st_ids if s not in set(holdout)]
    la = np.array([by_id[s]["lat"] for s in cal_st]); lo = np.array([by_id[s]["lon"] for s in cal_st])
    targets, pools = {}, {}

    def pool_for(rid: str, amps: Mapping[str, float]) -> list[tuple[str, float]]:
        d = km(by_id[rid]["lat"], by_id[rid]["lon"], la, lo)
        own = clusters[rid]
        cand = [(cal_st[i], float(d[i])) for i in np.argsort(d, kind="stable")
                if clusters[cal_st[i]] != own and same_regime(amps, by_id[cal_st[i]]["amps"])]
        return cand[:POOL_SEARCH]
    for r, amps in sorted(short.items()):
        if r in refused:
            continue
        row = rows[r]
        why = None
        if row["time_base"] != "utc_instant" or row["months_dropped"] != 0:
            why = f"time base {row['time_base']} with {row['months_dropped']} months dropped"
        elif not (meta[r].get("TIME ZONE HOURS") or "").strip():
            why = "no TIME ZONE HOURS"
        elif not amps:
            why = "no main constituent published"
        targets[r] = {"why": why, "good_hours": round(float(row["data_hours"]), 1), "regime": form_class(amps),
                      "reference": list(reference(amps)) if amps else None, "fit_sha256": fit_sha256(fits[r])}
        if why is None:
            pools[r] = pool_for(r, amps)
    # the hold-out: each station takes the mask of a target drawn with the seed; its pool as a target's
    target_ids = sorted(t for t in targets if targets[t]["why"] is None)
    hold = {}
    for h in holdout:
        hm = target_ids[record_seed("holdout-mask", h, seed) % len(target_ids)]
        hold[h] = {"mask_of": hm, "pool": pool_for(h, by_id[h]["amps"])[:POOL_CANDIDATES]}
    return {"clusters": clusters, "refused": refused, "stations": stations, "holdout": hold,
            "targets": targets, "pools": pools, "short": short, "long": sorted(long_ids)}


def finish_target(pool: Sequence[tuple[str, float]], cuts: Mapping[str, Mapping], target_amps: Mapping[str, float],
                  key: str) -> dict:
    """A target's (or hold-out's) stated accuracy from the first POOL_STATIONS stations with a usable cut."""
    used, values = [], []
    for s, d in pool:
        v = station_value(cuts.get(s, {}).get("cuts", {}).get(key, []))
        if v is None:
            continue
        used.append((s, round(d, 1)))
        values.append(v)
        if len(used) == POOL_STATIONS:
            break
    if len(used) < POOL_STATIONS:
        return {"error": f"pool_too_small: {len(used)} stations of the regime with a usable cut "
                         f"(of {len(pool)} candidates; {POOL_STATIONS} needed)", "pool": used}
    acc = target_accuracy(target_amps, values)
    if "error" in acc:
        return {**acc, "pool": used}
    return {**acc, "calibration_records": len(used), "pool": used, "max_km": used[-1][1]}


def widen(pools: Mapping[str, Sequence[tuple[str, float]]], reach: dict[str, int],
          cuts: Mapping[str, Mapping]) -> dict[str, set[str]]:
    """The next round of cuts: each target with fewer than POOL_STATIONS usable stations among the reach[t] nearest
    of its regime pool takes the next POOL_CANDIDATES (reach[t] grows in place). station -> targets."""
    more: dict[str, set[str]] = {}
    for t, pool in pools.items():
        usable = sum(1 for s_, _ in pool[:reach[t]]
                     if station_value(cuts.get(s_, {}).get("cuts", {}).get(t, [])) is not None)
        if usable < POOL_STATIONS and reach[t] < len(pool):
            for s_, _ in pool[reach[t]:reach[t] + POOL_CANDIDATES]:
                more.setdefault(s_, set()).add(t)
            reach[t] = min(reach[t] + POOL_CANDIDATES, len(pool))
    return more


def holdout_check(plan_: Mapping, cuts: Mapping[str, Mapping], amps: Mapping[str, Mapping[str, float]],
                  fulls: Mapping[str, Mapping]) -> dict:
    """Per constituent, the share of hold-out cuts whose own error exceeds the stated value; skipped
    hold-outs are counted with why."""
    over = {c: [0, 0] for c in MAIN}
    pover = {c: [0, 0] for c in MAIN}
    skipped = {}
    for h, hv in sorted(plan_["holdout"].items()):
        acc = finish_target(hv["pool"], cuts, amps[h], hv["mask_of"])
        own = cuts.get(h, {}).get("cuts", {}).get(hv["mask_of"], [])
        own = [w for w in own if w.get("errors")]
        if "error" in acc:
            skipped[h] = acc["error"]
            continue
        if not own:
            skipped[h] = "no usable cut of the hold-out itself"
            continue
        for c, v in acc["by_constituent"].items():
            for w in own:
                e = w["errors"].get(c)
                if e is None:
                    continue
                over[c][0] += e["dA"] > v["amp_m"]
                over[c][1] += 1
                if v["phase_deg"] is not None and e["dg"] is not None:
                    pover[c][0] += e["dg"] > v["phase_deg"]
                    pover[c][1] += 1
    sh = lambda a: {"over": a[0], "n": a[1], "share": round(a[0] / a[1], 4) if a[1] else None}  # noqa: E731
    return {"amp": {c: sh(v) for c, v in over.items()}, "phase": {c: sh(v) for c, v in pover.items()},
            "holdouts": len(plan_["holdout"]), "skipped": skipped}


def calibrate(fit_runs: Sequence[Path], out_dir: Path, *, workers: int = 4, seed: int = 0,
              holdout_n: int = HOLDOUT_N) -> dict:
    from .read_gesla import GeslaRelease
    t_start = time.time()
    commit = _git(["rev-parse", "HEAD"])
    if _git(["status", "--porcelain", "--untracked-files=no"]):
        raise RuntimeError("the checkout has uncommitted changes: a calibration names the commit that made it")
    tcd_path = fit_runs_tcd(fit_runs)
    table = TcdTable.load(tcd_path)
    rel = GeslaRelease()
    meta = rel.metadata()
    rows, where = load_rows(fit_runs)
    fits = {r: json.loads((where[r] / "fits" / f"{r}.json").read_text()) for r in rows}
    p = plan(rows, fits, table, meta, seed=seed, holdout_n=holdout_n)
    out_dir.mkdir(parents=True, exist_ok=True)
    live = [t for t, v in p["targets"].items() if v["why"] is None]
    log = lambda s: print(f"{time.strftime('%H:%M:%S')} {s}", flush=True)  # noqa: E731
    log(f"fitted {len(rows)}, short {len(p['short'])}, targets {len(live)} (+{len(p['targets']) - len(live)} not "
        f"calibratable), refused implausible {len(p['refused'])}, "
        f"stations {len(p['stations'])}, hold-out {len(p['holdout'])}, workers {workers}")
    init = (tuple(str(d) for d in fit_runs), None, str(tcd_path))
    masks, mask_err = {}, {}
    mask_ids = sorted(set(live))
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=init) as ex:
        for fu in as_completed([ex.submit(_mask_job, t) for t in mask_ids]):
            rid, hours, err = fu.result()
            if err:
                mask_err[rid] = err
            else:
                masks[rid] = hours
    for rid, err in mask_err.items():
        p["targets"][rid]["why"] = f"mask: {err}"
        p["pools"].pop(rid, None)
    np.savez_compressed(out_dir / MASKS_FILE, **masks)
    log(f"masks {len(masks)} ({len(mask_err)} refused)")
    reach = {t: min(POOL_CANDIDATES, len(pool)) for t, pool in p["pools"].items()}   # stations considered so far
    first: dict[str, set[str]] = {}
    for t, pool in p["pools"].items():
        for s, _ in pool[:reach[t]]:
            first.setdefault(s, set()).add(t)
    for h, hv in p["holdout"].items():
        if hv["mask_of"] in masks:
            first.setdefault(h, set()).add(hv["mask_of"])
            for s, _ in hv["pool"]:
                first.setdefault(s, set()).add(hv["mask_of"])
    stamp = cut_stamp(commit, sha256_file(tcd_path), seed)
    saved: dict[tuple[str, int], dict] = {}
    if (out_dir / CUTS_FILE).exists():          # resume (reusable: a partial last line is dropped)
        for x in (out_dir / CUTS_FILE).read_text().splitlines():
            try:
                d = json.loads(x)
            except json.JSONDecodeError:
                continue
            saved[(d["station"], d.get("round", 0))] = d
    cuts: dict[str, dict] = {}
    errors = []

    def run_round(jobs: Mapping[str, set[str]], rnd: int) -> None:
        todo, stale = {}, 0
        with open(out_dir / CUTS_FILE, "a") as f:
            for s_, ts in jobs.items():
                d = saved.get((s_, rnd))
                if d is not None and reusable(d, stamp, ts, fit_sha256(fits[s_])):
                    merge(d)
                    f.write(json.dumps(d, sort_keys=True) + "\n")
                else:
                    stale += d is not None
                    todo[s_] = ts
        pairs_ = sum(len(v) for v in jobs.values())
        log(f"round {rnd}: stations {len(jobs)}, station-target pairs {pairs_}, reused {len(jobs) - len(todo)}, "
            f"saved rows cut again (stamp, fit or targets differ) {stale}, to cut {len(todo)}")
        if not todo:
            return
        t0 = last = time.time()
        n = 0
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(tuple(str(d) for d in fit_runs), str(out_dir / MASKS_FILE),
                                           str(tcd_path))) as ex, \
                open(out_dir / CUTS_FILE, "a") as f:
            futs = {ex.submit(_station_job, s_, sorted(ts)): s_ for s_, ts in sorted(todo.items())}
            for fu in as_completed(futs):
                s_ = futs[fu]
                try:
                    d = fu.result()
                except Exception as e:  # noqa: BLE001 - a station that cannot be cut is listed, never guessed
                    errors.append({"station": s_, "round": rnd, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                    continue
                if d["full_sha256"] != fit_sha256(fits[s_]):
                    errors.append({"station": s_, "round": rnd, "error": "the worker read another full fit"})
                    continue
                d["round"] = rnd
                d["stamp"] = stamp
                merge(d)
                f.write(json.dumps(d, sort_keys=True) + "\n")
                f.flush()
                n += 1
                if time.time() - last > 60 or n == len(todo):
                    last = time.time()
                    rate = n / (last - t0)
                    log(f"round {rnd}: {n}/{len(todo)} stations cut, {rate * 60:.1f}/min, eta "
                        f"{(len(todo) - n) / rate / 60 if rate else float('nan'):.0f} min, errors {len(errors)}")

    def merge(d: Mapping) -> None:
        c = cuts.setdefault(d["station"], {"station": d["station"], "full_sha256": d["full_sha256"], "cuts": {}})
        c["cuts"].update(d["cuts"])

    if saved:                                   # rewritten from `saved`: the rows reused, then the rows cut now
        (out_dir / CUTS_FILE).write_text("")
    run_round(first, 0)
    rnd = 0
    while True:                                 # widen: a target short of 15 usable stations takes the next 20
        more = widen(p["pools"], reach, cuts)
        if not more:
            break
        rnd += 1
        run_round(more, rnd)
    targets = {}
    for t, v in sorted(p["targets"].items()):
        base = {"good_hours": v["good_hours"], "regime": v["regime"], "reference": v["reference"],
                "fit_sha256": v["fit_sha256"], "lat": float(rows[t]["lat"]), "lon": float(rows[t]["lon"])}
        if v["why"] is not None:
            targets[t] = {**base, "error": f"not calibratable: {v['why']}"}
            continue
        acc = finish_target(p["pools"][t][:reach[t]], cuts, p["short"][t], t)
        if "error" in acc:
            targets[t] = {**base, **acc}
            continue
        from .short_accuracy_gate import groups_of
        row, f = rows[t], fits[t]
        g = groups_of(row, f, p["short"][t])
        meas = {c: {"amp_m": x["amp_m"], "phase_deg": x["phase_deg"]} for c, x in
                {**acc["by_constituent"], **acc.get("unclaimed", {})}.items()}
        info = {"measured": meas, "unclaimed": acc.get("unclaimed", {}), "microtidal": acc["microtidal"],
                "pool": acc["pool"], "max_km": acc["max_km"], "calibration_records": acc["calibration_records"],
                "groups": g, "band": next(x for x in g if x[0] in "<0123456789"),
                "time_base_status": row.get("time_base_status"), "publishes_m2": "M2" in p["short"][t],
                "cut_to_days": round(v["good_hours"] / 24, 1)}
        why = inconsistent_halves(f) or own_u_exceeds(f, acc["by_constituent"])
        if why:
            p["refused"][t] = {"reason": INCONSISTENT, "detail": why}
        claim = restate(info)
        targets[t] = {**base, **info, **claim}
    amps = {r: main_amps(fits[r]) for r in p["holdout"]}
    hc = holdout_check(p, cuts, amps, fits)
    run_inputs = [run_identity(d) for d in fit_runs]
    cal = {"cal_version": CAL_VERSION, "made_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "code": {"commit": commit, "dirty": False},
           "run_seconds": round(time.time() - t_start, 1),
           "config": {"main": list(MAIN), "windows": WINDOWS, "min_cover": MIN_COVER,
                      "phase_min_amp_m": PHASE_MIN_AMP_M, "amp_floor_m": AMP_FLOOR_M, "lost_phase_deg": LOST_PHASE_DEG,
                      "pool_stations": POOL_STATIONS, "pool_candidates": POOL_CANDIDATES, "m2_ratio": list(M2_RATIO),
                      "form_limits": list(FORM_LIMITS), "cosite_km": COSITE_KM, "pool_min_good_days": POOL_MIN_GOOD_DAYS,
                      "sanity_m2_max_m": SANITY_M2_MAX_M, "sanity_ratio": SANITY_RATIO,
                      "sanity_neighbours": SANITY_NEIGHBOURS, "holdout_n": holdout_n, "seed": seed,
                      "unclaimed": list(UNCLAIMED), "unclaimed_reason": UNCLAIMED_REASON},
           "inputs": {"fit_runs": run_inputs, "tcd_table": str(tcd_path), "tcd_table_sha256": sha256_file(tcd_path),
                      "tcd_sha256": table.tcd_sha256,
                      "gesla_version": rel.version, "gesla_manifest_sha256": sha256_file(Path(rel.root) / "MANIFEST.json")},
           "stations": len(p["stations"]), "station_errors": errors, "refused": p["refused"],
           "holdout": {h: v["mask_of"] for h, v in p["holdout"].items()},
           "holdout_check": hc, "targets": targets}
    write_calibration(cal, out_dir)
    log(f"done in {(time.time() - t_start) / 60:.1f} min")
    return cal


def summary(cal: Mapping) -> dict:
    ts = cal["targets"].values()
    ok = [t for t in ts if "error" not in t]
    bands = [("<=30", 0, 30), ("31-90", 30, 90), ("91-182", 90, 182), ("183-364", 182, 366)]
    by = {}
    for name, lo, hi in bands:
        b = [t for t in ok if lo < t["good_hours"] / 24 <= hi]
        if b:
            a = sorted(t["amp_uncertainty_m"] for t in b)
            g = sorted(t["phase_uncertainty_deg"] for t in b)
            by[name] = {"targets": len(b), "microtidal": sum(t["microtidal"] for t in b),
                        "amp_m_median": round(statistics.median(a), 5), "amp_m_p90": a[int(0.9 * (len(a) - 1))],
                        "phase_deg_median": round(statistics.median(g), 3), "phase_deg_p90": g[int(0.9 * (len(g) - 1))]}
    errs: dict[str, int] = {}
    for t in ts:
        if "error" in t:
            k = t["error"].split(":")[0]
            errs[k] = errs.get(k, 0) + 1
    return {"targets": len(cal["targets"]), "calibrated": len(ok), "not_calibrated": errs, "by_band": by,
            "refused_implausible": sorted(cal["refused"]), "holdout_check": cal["holdout_check"],
            "run_seconds": cal["run_seconds"]}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gesla.short_accuracy")
    ap.add_argument("--fit-run", required=True, type=Path, help="a fit run dir (results.jsonl, fits/)")
    ap.add_argument("--overlay-run", type=Path, help="a partial run whose records replace the fit run's")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--holdout", type=int, default=HOLDOUT_N)
    a = ap.parse_args(argv)
    runs = [a.fit_run] + ([a.overlay_run] if a.overlay_run else [])
    cal = calibrate(runs, a.out, workers=a.workers, seed=a.seed, holdout_n=a.holdout)
    print(json.dumps(summary(cal), indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
