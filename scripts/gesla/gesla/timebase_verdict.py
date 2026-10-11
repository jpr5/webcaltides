"""Time-base verdict classes, corrections, the pass rule and the positive controls (BP §2 steps 3-4).

G04a measures; this module decides. The audit order, the comparator choice and provider
inheritance are G04c: here a record is judged against ONE comparator series that the caller
supplies already on true UTC (a sibling after its own correction, or an official series).

Classes (BP §2.3), each with a fixed correction:

| verdict                  | signature (all on the monthly lag series in the span)                | correction |
|--------------------------|----------------------------------------------------------------------|------------|
| utc_instant              | every month within 0 +- 3 min; M2 amplitude ratio within 0.01 of 1   | none |
| utc_mean_start60         | every month within -30 +- 3 min; sampling test: the comparator's     | stamps +30 min; amplitudes / boxcar response |
|                          | 60-min mean fits better than its instant at t and at t+30 min; M2    | |
|                          | ratio within 0.01 of the boxcar response (0.989). M4/M6 recorded only | |
| utc_plus_<n> / utc_minus_<n> | every month within 60 n +- 3 min                                 | stamps -n h |
| local_dst:<zone>         | |July median - January median| = 60 +- 5 min; spring gap present    | wall clock in <zone> -> UTC |
| step:<date>              | the monthly series is one class before <date> and another after     | each part its own correction; amplitudes / the duration-weighted response |
| months_bad:<list>        | isolated months off, the rest in one class                           | drop those months, then the class |
| no_comparator            | no audited month (no overlap with the comparator)                    | none; G04c decides (rule 5) |
| unexplained              | none of the above, or the pass rule fails                            | not usable (decision model) |

"Every month within the band" is tested on the corrected series: the residual lag after the
class's correction must be within +-3 min (BAND_MIN), which for a pure shift is the same test.

Isolated months (months_bad), fixed before any real record was classified: a month is off when
its residual is outside +-3 min; the off months are isolated when no two of them are adjacent
calendar months (MAX_BAD_RUN = 1) and they are at most 10 % of the audited months
(MAX_BAD_FRACTION). A run of two or more adjacent off months is not isolated: the record is
`unexplained` and goes to the decision model (BP §7.1) unless the span the fit uses avoids it.

Pass rule (BP §2.4): after the full correction (months dropped included), the residual lag must
be within +-3 min for EVERY audited month in the span. Otherwise the record is `unexplained`.

Positive controls (BP §2, every build): run_positive_controls(); a wrong control raises
PositiveControlError, and `python -m gesla.timebase_verdict controls` exits non-zero.
OTC_TB_TEST_SWAP_CLASSES="a,b" swaps two class names on output: a deliberately broken
classifier, for the control test only.
"""

from __future__ import annotations

import math
import os
import statistics
import sys
from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np
import pandas as pd

from .record import GaugeRecord
from .tcd_table import TcdTable, dump_path
from .timebase_measure import (MonthLag, Samples, constituent_lags, monthly_lags, read_fixture, samples,
                               sampling_test, spring_gap)

BAND_MIN = 3.0                 # pass rule and every class band, minutes
MEAN60_LAG = -30.0
RATIO_TOL = 0.01               # M2 amplitude ratio tolerance (instant: vs 1; mean60: vs the boxcar response)
DST_DIFF, DST_TOL = 60.0, 5.0  # local_dst: |Jul - Jan| = 60 +- 5 min
SPRING_GAP_MIN_FRACTION = 0.5  # spring gap "present": the hour is missing in at least half the years
SPRING_GAP_MAX_CONTROL = 0.1   # ... and in at most 10 % of the control days
PLUS_N = (1, 2, -1, -2)        # the lag search covers +-180 min; +-3 h would sit on its edge
MAX_BAD_RUN = 1
MAX_BAD_FRACTION = 0.10
SAMPLING_DAYS = 365            # the sampling test runs over the last year of the overlap
CONSTITUENT_YEARS = 12         # the M2 ratio comes from the last 12 years of the overlap (as the PoC)
SWAP_ENV = "OTC_TB_TEST_SWAP_CLASSES"
M2_SPEED = 28.9841042          # deg/h, only for the evidence line when no table is at hand

Month = tuple[int, int]


class PositiveControlError(RuntimeError):
    """A positive control came out wrong: a code fault. The build stops."""


class UnexplainedRecord(ValueError):
    """A record without a usable verdict was about to be cleaned for a fit."""


def month_key(m: Month) -> str:
    return f"{m[0]:04d}-{m[1]:02d}"


def boxcar_response(speed_deg_h: float, minutes: float = 60.0) -> float:
    """Amplitude response of a mean over `minutes` at a constituent speed: sin(x)/x, x = w T / 2."""
    x = math.radians(speed_deg_h) * (minutes / 60.0) / 2.0
    return 1.0 if x == 0 else math.sin(x) / x


def isolated(bad: Sequence[Month], audited: Sequence[Month]) -> bool:
    """Off months are isolated: none adjacent to another, and at most 10 % of the audited months."""
    if not bad:
        return True
    if len(bad) > MAX_BAD_FRACTION * len(audited):
        return False
    idx = sorted(y * 12 + (m - 1) for y, m in bad)
    run = 1
    for a, b in zip(idx, idx[1:]):
        run = run + 1 if b - a == 1 else 1
        if run > MAX_BAD_RUN:
            return False
    return True


def _month_of(times_s: np.ndarray) -> np.ndarray:
    """Calendar month index y * 12 + (m - 1) of each stamp."""
    m = np.asarray(times_s, dtype=np.int64).astype("datetime64[s]").astype("datetime64[M]").astype(np.int64)
    return m + 1970 * 12


@dataclass(frozen=True)
class Correction:
    """A verdict's fixed correction, callable as a qc.TimeBaseCorrection on the stamped series.

    kind: utc_instant | utc_mean_start60 | utc_plus_n | local_dst | step | none (not usable).
    A step applies `first` before the first part's start and each part's correction from its start
    on (part starts are stamps as recorded). months_drop are UTC calendar months (the frame the pass
    rule measures them in), so they are dropped AFTER the shift, on the corrected stamps."""
    kind: str
    n: int = 0
    zone: str | None = None
    months_drop: tuple[Month, ...] = ()
    first: "Correction | None" = None
    parts: tuple[tuple[int, "Correction"], ...] = ()

    @property
    def usable(self) -> bool:
        return self.kind != "none"

    @property
    def label(self) -> str:
        if self.kind == "utc_plus_n":
            return f"utc_plus_{self.n}" if self.n > 0 else f"utc_minus_{-self.n}"
        if self.kind == "local_dst":
            return f"local_dst:{self.zone}"
        if self.kind == "step":
            return "step:" + ",".join(_iso(s) for s, _ in self.parts)
        return self.kind

    def amplitude_factor(self, speed_deg_h: float, span: tuple[int, int] | None = None) -> float:
        """Multiply a fitted amplitude by this (BP §2.3: divide by the boxcar response).

        A step whose parts differ in response (a utc_mean_start60 part next to an instant one): the
        fitted amplitude is the duration-weighted mean of the parts' responses over the fit span, so
        the factor is 1 / that mean; it needs `span` ([start, end) of the fit) and raises without it."""
        if self.kind == "utc_mean_start60":
            return 1.0 / boxcar_response(speed_deg_h)
        if self.kind != "step":
            return 1.0
        parts = [(None, self.first or Correction("utc_instant"))] + list(self.parts)
        factors = [c.amplitude_factor(speed_deg_h) for _, c in parts]
        if len(set(factors)) == 1:
            return factors[0]
        if span is None:
            raise ValueError(f"{self.label}: the parts differ in amplitude response; the factor needs the fit span")
        lo, hi = span
        edges = [lo] + [min(max(int(s), lo), hi) for s, _ in parts[1:]] + [hi]
        w = [b - a for a, b in zip(edges, edges[1:])]
        if sum(w) <= 0:
            raise ValueError(f"{self.label}: empty fit span {span}")
        return sum(w) / sum(wi / f for wi, f in zip(w, factors))

    def __call__(self, times_s: np.ndarray, heights_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if not self.usable:
            raise UnexplainedRecord("no usable time-base verdict: the record cannot be corrected")
        t = np.asarray(times_s, dtype=np.int64)
        h = np.asarray(heights_m, dtype=np.float64)
        t, h = self._correct(t, h)
        if self.months_drop:
            keep = ~np.isin(_month_of(t), [y * 12 + m - 1 for y, m in self.months_drop])
            t, h = t[keep], h[keep]
        return t, h

    def _correct(self, t: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.kind == "step":
            bounds = [s for s, _ in self.parts]
            corrs = [self.first or Correction("utc_instant")] + [c for _, c in self.parts]
            which = np.searchsorted(np.asarray(bounds, dtype=np.int64), t, side="right")
            out_t, out_h = [], []
            for k, c in enumerate(corrs):
                sel = which == k
                ct, ch = c(t[sel], h[sel])
                out_t.append(ct)
                out_h.append(ch)
            t, h = np.concatenate(out_t), np.concatenate(out_h)
            order = np.argsort(t, kind="stable")
            return t[order], h[order]
        return self._shift(t, h)

    def _shift(self, t: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.kind == "utc_instant":
            return t, h
        if self.kind == "utc_mean_start60":
            return t + 1800, h
        if self.kind == "utc_plus_n":
            return t - self.n * 3600, h
        if self.kind == "local_dst":
            idx = pd.DatetimeIndex(t.astype("datetime64[s]"))
            loc = idx.tz_localize(self.zone, ambiguous=_dst_flags(idx, t, self.zone), nonexistent="NaT")
            ok = ~loc.isna()
            utc = loc[ok].tz_convert("UTC").tz_localize(None).values.astype("datetime64[s]").astype(np.int64)
            return utc, h[np.asarray(ok)]
        raise ValueError(f"unknown correction kind {self.kind!r}")


NOT_USABLE = Correction("none")


def _dst_flags(idx: pd.DatetimeIndex, t: np.ndarray, zone: str) -> np.ndarray:
    """The autumn wall-clock hour occurs twice: its first copy is summer time, the repeat standard
    time. A stamp in that hour is the repeat when an equal or later stamp of the hour came before
    it (in the order given; sorted duplicates work too). True = DST."""
    a = idx.tz_localize(zone, ambiguous=np.ones(len(t), dtype=bool), nonexistent="NaT")
    b = idx.tz_localize(zone, ambiguous=np.zeros(len(t), dtype=bool), nonexistent="NaT")
    amb = np.asarray((a != b) & ~a.isna())
    flags = np.ones(len(t), dtype=bool)
    if amb.any():
        ta = t[amb]
        prev = np.maximum.accumulate(np.concatenate([[np.iinfo(np.int64).min], ta[:-1]]))
        flags[np.nonzero(amb)[0]] = ta > prev
    return flags


def _iso(s: int) -> str:
    return str(np.datetime64(int(s), "s").astype("datetime64[D]"))


@dataclass
class Verdict:
    record_id: str
    comparator_id: str
    verdict: str                     # the BP §2.3 label, or "unexplained" / "no_comparator"
    base: str                        # the class under months_bad / the tried class when unexplained
    correction: Correction
    passed: bool                     # the pass rule held (always False for unexplained / no_comparator)
    months_bad: list[str] = field(default_factory=list)
    span: tuple[int, int] | None = None
    residual: dict = field(default_factory=dict)   # n_months, max_abs, off (month -> residual)
    evidence: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def line(self) -> str:
        r = self.residual
        span = f"{_iso(self.span[0])}..{_iso(self.span[1])}" if self.span else "-"
        bad = f" months_bad {','.join(self.months_bad)}" if self.months_bad else ""
        res = (f"residual {r['n_months']} months max|lag| {r['max_abs']:.1f} min" if r.get("n_months")
               else "no residual")
        return (f"{self.record_id} vs {self.comparator_id} [{span}]: {self.verdict} (base {self.base},"
                f" passed {self.passed}){bad}; {res}")


# --- evidence ----------------------------------------------------------------------------------

def _clip(s: Samples, lo: int, hi: int) -> Samples:
    m = (s[0] >= lo) & (s[0] < hi)
    return s[0][m], s[1][m]


def _rows(rec: Samples, comp: Samples) -> list[MonthLag]:
    return monthly_lags(rec, comp) if len(rec[0]) else []


def _residual(rows: Sequence[MonthLag]) -> dict:
    if not rows:
        return {"n_months": 0, "max_abs": None, "off": {}}
    return {"n_months": len(rows), "max_abs": max(abs(r.lag_min) for r in rows),
            "off": {month_key((r.year, r.month)): round(r.lag_min, 1) for r in rows if abs(r.lag_min) > BAND_MIN}}


class _Evidence:
    """Lazy measurements of one record against one comparator over one span."""

    def __init__(self, record: GaugeRecord, comp: Samples, table: TcdTable | None, span: tuple[int, int],
                 zone: str | None, comp_m2: float = 1.0):
        self.record, self.comp, self.table, self.span, self.zone = record, comp, table, span, zone
        self.comp_m2 = comp_m2
        self.rec = _clip(samples(record), *span)
        self.raw = _rows(self.rec, comp)
        self.rows_memo: dict[Correction, list[MonthLag]] = {}   # _corrected_rows, by correction without drops
        self.data: dict = {}
        if self.raw:
            lags = [r.lag_min for r in self.raw]
            jan = [r.lag_min for r in self.raw if r.month == 1]
            jul = [r.lag_min for r in self.raw if r.month == 7]
            self.data["lags"] = {"n_months": len(lags), "median": round(statistics.median(lags), 2),
                                 "min": round(min(lags), 2), "max": round(max(lags), 2),
                                 "jan_median": round(statistics.median(jan), 2) if jan else None,
                                 "jul_median": round(statistics.median(jul), 2) if jul else None}
            # The raw lag of every audited month (G04c: month votes across several comparators).
            self.data["monthly"] = {month_key((r.year, r.month)): round(r.lag_min, 2) for r in self.raw}

    def overlap_end(self) -> int:
        return int(min(self.rec[0][-1], self.comp[0][-1], self.span[1]))

    def overlap_start(self) -> int:
        return int(max(self.rec[0][0], self.comp[0][0], self.span[0]))

    def sampling(self) -> dict | None:
        if "sampling" not in self.data:
            end = self.overlap_end()
            start = max(self.overlap_start(), end - SAMPLING_DAYS * 86400)
            try:
                res = sampling_test(self.rec, self.comp, start, end)
                self.data["sampling"] = {"window": f"{_iso(start)}..{_iso(end)}",
                                         "mean60_cm": round(res["mean60"]["resid_cm"], 3),
                                         "inst_t30_cm": round(res["inst_t30"]["resid_cm"], 3),
                                         "inst_t_cm": round(res["inst_t"]["resid_cm"], 3),
                                         "n": res["mean60"]["n"], "ref_interval_min": res["ref_interval_min"]}
            except (ValueError, ZeroDivisionError, IndexError) as e:
                self.data["sampling"] = None
                self.data["sampling_error"] = str(e)
        return self.data["sampling"]

    def constituents(self) -> dict | None:
        if "constituents" not in self.data:
            if self.table is None:
                self.data["constituents"] = None
                return None
            end = self.overlap_end()
            start = max(self.overlap_start(), end - CONSTITUENT_YEARS * 365 * 86400)
            comp_rec = _as_record(self.comp, self.record, "comparator")
            try:
                out = constituent_lags(self.table, self.record, comp_rec, start, end + 1)
                self.data["constituents"] = {k: (None if v is None else
                                                 {"lag_min": round(v["lag_min"], 2),
                                                  "amp_ratio": round(v["amp_ratio"], 4)})
                                             for k, v in out.items()}
                self.data["constituents_window"] = f"{_iso(start)}..{_iso(end)}"
            except (ValueError, np.linalg.LinAlgError, KeyError) as e:
                self.data["constituents"] = None
                self.data["constituents_error"] = str(e)
        return self.data["constituents"]

    def gap(self) -> dict | None:
        if "spring_gap" not in self.data:
            if not self.zone:
                self.data["spring_gap"] = None
            else:
                y0 = int(np.datetime64(self.span[0], "s").astype("datetime64[Y]").astype(int)) + 1970
                y1 = int(np.datetime64(self.span[1] - 1, "s").astype("datetime64[Y]").astype(int)) + 1970
                g = spring_gap(self.record, self.zone, years=range(y0, y1 + 1))
                self.data["spring_gap"] = {"zone": self.zone, "hits": g.hits, "years": g.years,
                                           "control_hits": g.control_hits, "controls": g.controls}
        return self.data["spring_gap"]

    def m2_ratio(self) -> float | None:
        c = self.constituents()
        return None if not c or not c.get("M2") else c["M2"]["amp_ratio"]


def _as_record(s: Samples, like: GaugeRecord, name: str) -> GaugeRecord:
    return GaugeRecord(record_id=f"{like.record_id}~{name}", source="gesla", source_version="audit",
                       contributor="comparator", originator=False, licence_class="audit", lat=like.lat,
                       lon=like.lon, gauge_type=like.gauge_type, sampling="instantaneous", interval_min=1,
                       declared_time_base=None, member_sha256="0" * 64, times_s=s[0].astype(np.int64),
                       heights_m=s[1].astype(np.float64), good=np.ones(len(s[0]), dtype=bool))


# --- classification ----------------------------------------------------------------------------

def _signature(kind: str, ev: _Evidence, *, n: int = 0, rows: Sequence[MonthLag] | None = None) -> list[str]:
    """Why `kind` does NOT fit the evidence (empty = the signature holds). The month-by-month
    band is not tested here: it is the residual test on the corrected series."""
    rows = ev.raw if rows is None else rows
    if not rows:
        return ["no audited month"]
    med = statistics.median(r.lag_min for r in rows)
    why = []
    if kind == "utc_instant":
        if abs(med) > BAND_MIN:
            why.append(f"median lag {med:+.1f} not within 0 +- {BAND_MIN:g}")
        else:
            r, want = ev.m2_ratio(), 1.0 / ev.comp_m2
            if r is None or abs(r - want) > RATIO_TOL:
                why.append(f"M2 amplitude ratio {r} not within {RATIO_TOL} of {want:.4f}")
    elif kind == "utc_mean_start60":
        if abs(med - MEAN60_LAG) > BAND_MIN:
            why.append(f"median lag {med:+.1f} not within {MEAN60_LAG:+.0f} +- {BAND_MIN:g}")
        else:
            s = ev.sampling()
            if s is None or not all(math.isfinite(s[k]) for k in ("mean60_cm", "inst_t30_cm", "inst_t_cm")):
                why.append(f"sampling test not possible (comparator interval "
                           f"{s and s.get('ref_interval_min')} min; the hour mean needs a sub-hourly comparator): {s}")
            elif not (s["mean60_cm"] < s["inst_t_cm"] and s["mean60_cm"] < s["inst_t30_cm"]):
                why.append(f"sampling test does not favour the 60-min mean: {s}")
            want = boxcar_response(ev.table.speed(ev.table.name("M2")) if ev.table else M2_SPEED) / ev.comp_m2
            r = ev.m2_ratio()
            if r is None or abs(r - want) > RATIO_TOL:
                why.append(f"M2 amplitude ratio {r} not within {RATIO_TOL} of the boxcar response {want:.4f}")
    elif kind == "utc_plus_n":
        if abs(med - 60 * n) > BAND_MIN:
            why.append(f"median lag {med:+.1f} not within {60 * n:+d} +- {BAND_MIN:g}")
    elif kind == "local_dst":
        jan = [r.lag_min for r in rows if r.month == 1]
        jul = [r.lag_min for r in rows if r.month == 7]
        if not ev.zone:
            why.append("no zone to test")
        elif not jan or not jul:
            why.append("no January or no July month")
        else:
            d = abs(statistics.median(jul) - statistics.median(jan))
            if abs(d - DST_DIFF) > DST_TOL:
                why.append(f"|Jul - Jan| {d:.1f} not within {DST_DIFF:g} +- {DST_TOL:g}")
            g = ev.gap()
            if not g or g["years"] == 0 or g["hits"] < SPRING_GAP_MIN_FRACTION * g["years"] \
                    or g["control_hits"] > SPRING_GAP_MAX_CONTROL * max(g["controls"], 1):
                why.append(f"spring gap not present: {g}")
    return why


def _base(kind: str, n: int = 0, zone: str | None = None) -> Correction:
    return Correction(kind, n=n, zone=zone)


def _candidates(ev: _Evidence) -> list[Correction]:
    out = [_base("utc_instant"), _base("utc_mean_start60")]
    out += [_base("utc_plus_n", n=n) for n in PLUS_N]
    if ev.zone:
        out.append(_base("local_dst", zone=ev.zone))
    return out


def _corrected_rows(corr: Correction, ev: _Evidence) -> list[MonthLag]:
    """The monthly lags of ev.rec after `corr` (clipped to the span), against ev.comp; for a shift
    class (not a step), computed once per evaluation and reused (the class test and the pass rule
    used to compute the same rows two or three times).

    Exact, not approximate: utc_instant without dropped months is the identity, so its rows are
    ev.raw (the same function of the same arrays). A correction with dropped months gives the rows
    of the same correction without them, minus the dropped months: a month's lag reads only the
    record samples stamped in that month (and the comparator), so removing other months' samples
    cannot change it, and a dropped month has no samples left, so no row.

    A step is refused (ValueError): its parts are recomputed by the caller, never memoised."""
    if corr.kind == "step":
        raise ValueError(f"_corrected_rows: {corr.label} is a step; the row memo is for shift classes only")
    key = replace(corr, months_drop=())
    if key not in ev.rows_memo:
        ev.rows_memo[key] = (ev.raw if key == Correction("utc_instant")
                             else _rows(_clip(_apply(key, ev.rec), *ev.span), ev.comp))
    rows = ev.rows_memo[key]
    if corr.months_drop:
        drop = set(corr.months_drop)
        rows = [r for r in rows if (r.year, r.month) not in drop]
    return rows


def _try(corr: Correction, ev: _Evidence, reasons: list[str]) -> tuple[Correction, list[Month]] | None:
    """Correct, measure the residual, and accept the class clean or with isolated off months."""
    rows = _corrected_rows(corr, ev)
    bad = [(r.year, r.month) for r in rows if abs(r.lag_min) > BAND_MIN]
    if not bad:
        return corr, []
    audited = [(r.year, r.month) for r in rows]
    if isolated(bad, audited):
        return replace(corr, months_drop=tuple(bad)), bad
    reasons.append(f"{corr.label}: {len(bad)} of {len(rows)} months off and not isolated: "
                   + ", ".join(f"{month_key((r.year, r.month))} {r.lag_min:+.1f}" for r in rows
                               if abs(r.lag_min) > BAND_MIN))
    return None


def _apply(corr: Correction, s: Samples) -> Samples:
    t, h = corr(s[0], s[1])
    return np.asarray(t, dtype=np.int64), np.asarray(h, dtype=np.float64)


LAG_CLASSES = [(0.0, "utc_instant", 0), (MEAN60_LAG, "utc_mean_start60", 0)] + [
    (60.0 * n, "utc_plus_n", n) for n in PLUS_N]


def _month_label(lag: float) -> tuple[str, int] | None:
    for centre, kind, n in LAG_CLASSES:
        if abs(lag - centre) <= BAND_MIN:
            return kind, n
    return None


def _step(ev: _Evidence, reasons: list[str]) -> tuple[Correction, list[Month], dict] | None:
    """One jump between two lag classes: every month before the step date is one class and every
    month from it on another, each part allowing isolated off months; each part's class signature
    must hold on its own months."""
    rows = ev.raw
    labels = [_month_label(r.lag_min) for r in rows]
    for k in range(1, len(rows)):
        if labels[k] is None or labels[k] == labels[k - 1]:
            continue
        before = [l for l in labels[:k] if l is not None]
        after = [l for l in labels[k:] if l is not None]
        if not before or not after:
            continue
        a = statistics.mode(before)
        b = statistics.mode(after)
        if a == b:
            continue
        start = int(np.datetime64(f"{rows[k].year:04d}-{rows[k].month:02d}", "M").astype("datetime64[s]").astype(np.int64))
        ca, cb = _base(a[0], n=a[1]), _base(b[0], n=b[1])
        sig = _signature(ca.kind, ev, n=ca.n, rows=rows[:k]) + _signature(cb.kind, ev, n=cb.n, rows=rows[k:])
        # The sampling test and the M2 ratio are measured on the whole record, which straddles the
        # step: for a step only the lag part of each class signature is tested.
        sig = [w for w in sig if not w.startswith(("sampling", "M2"))]
        if sig:
            continue
        step = Correction("step", first=ca, parts=((start, cb),))
        rows_c = _rows(_clip(_apply(step, ev.rec), *ev.span), ev.comp)
        bad = [(r.year, r.month) for r in rows_c if abs(r.lag_min) > BAND_MIN]
        if isolated(bad, [(r.year, r.month) for r in rows_c]):
            return replace(step, months_drop=tuple(bad)), bad, {"before": ca.label, "after": cb.label}
        reasons.append(f"step at {_iso(start)} ({ca.label} -> {cb.label}): off months not isolated")
    return None


def _swap(label: str) -> str:
    pair = [p.strip() for p in os.environ.get(SWAP_ENV, "").split(",") if p.strip()]
    if len(pair) != 2:
        return label
    a, b = pair
    return b if label == a else a if label == b else label


def evaluate(record: GaugeRecord, comparator: Samples, *, comparator_id: str, table: TcdTable | None,
             zone: str | None = None, span: tuple[int, int] | None = None,
             comparator_m2_response: float = 1.0) -> Verdict:
    """Classify `record` against one comparator (true-UTC samples), correct it, apply the pass rule.

    span = [start_s, end_s): the months the fit would use; default the whole overlap.
    comparator_m2_response: the M2 amplitude response still in the comparator series (a sibling
    corrected from utc_mean_start60 keeps its boxcar response, 0.989: the amplitude part of its
    correction acts on fitted constituents, not on the series). The M2 ratio tests divide by it."""
    t = samples(record)[0]
    if span is None:
        lo = int(max(t[0], comparator[0][0])) if len(t) and len(comparator[0]) else 0
        hi = int(min(t[-1], comparator[0][-1])) + 1 if len(t) and len(comparator[0]) else 0
        span = (lo, hi)
    ev = _Evidence(record, comparator, table, span, zone, comparator_m2_response)
    if not ev.raw:
        return Verdict(record.record_id, comparator_id, "no_comparator", "no_comparator", NOT_USABLE, False,
                       span=span, evidence=ev.data, reasons=["no month with enough data on both sides"])
    reasons: list[str] = []
    found = None
    for corr in _candidates(ev):
        why = _signature(corr.kind, ev, n=corr.n)
        if why:
            reasons.append(f"{corr.label}: " + "; ".join(why))
            continue
        got = _try(corr, ev, reasons)
        if got:
            found = (got[0], got[1], None)
            break
    if found is None:
        st = _step(ev, reasons)
        if st:
            found = st
    if found is None:
        return Verdict(record.record_id, comparator_id, "unexplained", "unexplained", NOT_USABLE, False,
                       span=span, evidence=ev.data, reasons=reasons)
    corr, bad, step_info = found
    if step_info:
        ev.data["step"] = step_info
    base = _swap(replace(corr, months_drop=()).label)
    label = f"months_bad:{','.join(month_key(m) for m in bad)}" if bad else base
    # Pass rule: the full correction (dropped months included), every audited month within +-3 min.
    # A step's parts are recomputed (not memoised); every other class reuses its rows (_corrected_rows).
    final = (_rows(_clip(_apply(corr, ev.rec), *span), ev.comp) if corr.kind == "step"
             else _corrected_rows(corr, ev))
    res = _residual(final)
    if not final or res["off"]:
        reasons.append(f"pass rule: residual outside +-{BAND_MIN:g} min in {res['off'] or 'every month (none audited)'}")
        return Verdict(record.record_id, comparator_id, "unexplained", base, NOT_USABLE, False,
                       [month_key(m) for m in bad], span, res, ev.data, reasons)
    return Verdict(record.record_id, comparator_id, label, base, corr, True, [month_key(m) for m in bad], span,
                   res, ev.data, reasons)


# --- positive controls -------------------------------------------------------------------------

def _unix(y: int, m: int = 1) -> int:
    return int(np.datetime64(f"{y:04d}-{m:02d}", "M").astype("datetime64[s]").astype(np.int64))


@dataclass(frozen=True)
class Control:
    name: str
    expect: str


@dataclass
class ControlResult:
    name: str
    expect: str
    verdict: Verdict
    member_sha256: str

    def line(self) -> str:
        ok = "OK" if self.verdict.verdict == self.expect and self.verdict.passed else "WRONG"
        return f"[{ok}] {self.name}: expect {self.expect}; {self.verdict.line()}; member sha256 {self.member_sha256}"


BOXCAR_SOURCE = "bremerhaventg-bre-deu-cmems"            # 10-min CMEMS, UTC instants
NOAA_SOURCE = "the_battery-8518750-usa-noaa"
WSV40 = (("helgolandbinnenhafen-9510070-deu-wsv", "helgolandtg-hel-deu-cmems"),
         ("bhvalterleuchtturm-4990010-deu-wsv", "bremerhaventg-bre-deu-cmems"),
         ("cuxhavensteubenhft-5990020-deu-wsv", "cuxhaventg-cux-deu-cmems"))
# Control spans are fixed: a control tests the code, so its span avoids the comparator's own
# known off months (Helgoland CMEMS 2013-06/07, 2014-03/04, 2015-05 against WSV; G04b notes).
WSV40_SPAN = (_unix(2015, 6), _unix(2025, 7))
NOAA_SPAN = (_unix(2016), _unix(2026))
BOXCAR_SPAN = (_unix(2016), _unix(2026))

CONTROLS = (
    [Control(f"boxcar:{BOXCAR_SOURCE}", "utc_mean_start60"), Control(f"noaa+60:{NOAA_SOURCE}", "utc_plus_1")]
    + [Control(f"wsv40:{a}", "local_dst:Europe/Berlin") for a, _ in WSV40]
)


def boxcar_copy(s: Samples, start: int, end: int) -> Samples:
    """Hourly stamps t on the hour, each the mean over [t, t + 60 min) of the series put on a 1-min
    grid by linear interpolation (at most 12 missing minutes), as WSV-4.1 and the sampling test."""
    m = (s[0] >= start - 3600) & (s[0] < end + 7200)
    ser = pd.Series(s[1][m], index=pd.to_datetime(s[0][m], unit="s"))
    g = ser.resample("1min").mean().interpolate(limit=12, limit_area="inside")
    mean = g.rolling(60, min_periods=60).mean().shift(-59)
    hours = pd.date_range(pd.to_datetime(start, unit="s").ceil("h"), pd.to_datetime(end, unit="s"), freq="h",
                          inclusive="left")
    v = mean.reindex(hours)
    ok = v.notna().to_numpy()
    t = hours.values.astype("datetime64[s]").astype(np.int64)
    return t[ok], v.to_numpy()[ok]


def _copy(rec: GaugeRecord, s: Samples, suffix: str) -> GaugeRecord:
    return GaugeRecord(record_id=f"{rec.record_id}{suffix}", source=rec.source, source_version=rec.source_version,
                       contributor=rec.contributor, originator=rec.originator, licence_class=rec.licence_class,
                       lat=rec.lat, lon=rec.lon, gauge_type=rec.gauge_type, sampling=rec.sampling,
                       interval_min=60, declared_time_base=rec.declared_time_base, member_sha256=rec.member_sha256,
                       times_s=s[0].astype(np.int64), heights_m=s[1].astype(np.float64),
                       good=np.ones(len(s[0]), dtype=bool))


def run_positive_controls(release, table: TcdTable, *, log=None) -> list[ControlResult]:
    """Run every control; the first wrong one raises PositiveControlError (the build stops)."""
    results = []

    def check(control: Control, v: Verdict, sha: str) -> None:
        r = ControlResult(control.name, control.expect, v, sha)
        if log:
            log(r.line())
        if v.verdict != control.expect or not v.passed:
            raise PositiveControlError(f"positive control {control.name}: expected {control.expect}, got "
                                       f"{v.verdict} (passed {v.passed}); reasons: {v.reasons}")
        results.append(r)

    src = release.record(BOXCAR_SOURCE, roles={})
    s = samples(src)
    copy = _copy(src, boxcar_copy(s, *BOXCAR_SPAN), "~boxcar60")
    check(CONTROLS[0], evaluate(copy, s, comparator_id=BOXCAR_SOURCE, table=table, span=BOXCAR_SPAN),
          src.member_sha256)

    src = release.record(NOAA_SOURCE, roles={})
    s = samples(src)
    copy = _copy(src, (s[0] + 3600, s[1]), "~plus60")
    check(CONTROLS[1], evaluate(copy, s, comparator_id=NOAA_SOURCE, table=table, span=NOAA_SPAN),
          src.member_sha256)

    for control, (name, comp_name) in zip(CONTROLS[2:], WSV40):
        fx = read_fixture(release, name)
        comp = samples(release.record(comp_name, roles={}))
        check(control, evaluate(fx, comp, comparator_id=comp_name, table=table, zone="Europe/Berlin",
                                span=WSV40_SPAN), fx.member_sha256)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from .read_gesla import GeslaRelease, otc_work

    ap = argparse.ArgumentParser(prog="python -m gesla.timebase_verdict")
    ap.add_argument("command", choices=["controls"])
    ap.parse_args(argv)
    release = GeslaRelease(otc_work() / "inputs" / "gesla" / "4.1")
    table = TcdTable.load(dump_path())   # the one table (tcd_table.dump_path)
    run_positive_controls(release, table, log=print)   # PositiveControlError propagates: exit 1
    print("positive controls: all right")
    return 0


if __name__ == "__main__":
    sys.exit(main())
