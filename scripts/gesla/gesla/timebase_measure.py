"""Time-base measurements between a record and a comparator (BP §2 step 2).

These are measurements only; the verdict classes and corrections are G04b, the audit order
and comparators G04c. Records are compared AS STAMPED (GaugeRecord.times_s).

- lag_minutes / monthly_lags / summary: cross-correlation lag at 1-min resolution per calendar
  month, as a series and as January and July summaries. Ported from the T4 check
  (verify/glib.py lag_minutes, xcorr_months.py, ctrl41.py). Sign: + = the record is late
  against the comparator, i.e. record(t) ~ comparator(t - lag).
- constituent_lags: per-constituent lag (min) and amplitude ratio (M2, S2, K1, O1, M4, M6) from
  the G03c analysis of both records over the same window (the PoC's tools/timebase.py).
- sampling_test: the direct sampling test (T4 verify/avg41.py): regress the record on the
  comparator's instantaneous value at t, at t+30 min, and on its mean over [t, t+60 min).
- spring_gap: the spring-forward gap test (T4 verify/dst_gap.py): is the local hour that does
  not exist on DST-change days (02:00 in Europe/Berlin) missing from the record, and present
  on control days one week before and one and two weeks after.
"""

from __future__ import annotations

import datetime as dt
import statistics
import warnings
from dataclasses import dataclass, field
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .qc import clean
from .read_gesla import GeslaRelease, ShaMismatch, parse_header, parse_record, sha256_bytes
from .record import GaugeRecord
from .select_constituents import analyse, between
from .tcd_table import TcdTable

CONSTITUENTS: tuple[str, ...] = ("M2", "S2", "K1", "O1", "M4", "M6")
LAGS_MIN = np.arange(-180, 181, 1)
MIN_SAMPLES = 200               # per month, in the record and in the comparator window; and per lag, matched
MIN_LAGS = 3                    # lags with a correlation needed for a month's lag (a parabola needs 3)
MAX_INTERP_GAP_S = 70 * 60      # the comparator is not interpolated across a longer gap
COMPARATOR_PAD_S = 4 * 3600     # comparator window = record window +- 4 h
INTERP_LIMIT_MIN = 12           # sampling test: fill at most 12 missing minutes of the 1-min grid
CONTROL_DAYS = (-7, 7, 14)

Samples = tuple[np.ndarray, np.ndarray]   # (int64 Unix seconds, float64 heights), sorted, unique


def samples(record: GaugeRecord) -> Samples:
    """The record's good samples, sorted by stamp, duplicate stamps dropped (first in file order)."""
    t = record.times_s[record.good]
    h = record.heights_m[record.good]
    order = np.argsort(t, kind="stable")
    t, h = t[order], h[order]
    first = np.ones(len(t), dtype=bool)
    first[1:] = t[1:] != t[:-1]
    return t[first].astype(np.int64), h[first].astype(np.float64)


def read_fixture(release: GeslaRelease, name: str, version: str = "4.0") -> GaugeRecord:
    """A pinned GESLA-<version> WSV fixture (BP §1.2), from the release's cache directory, checked
    against the manifest's SHA-256 and size. Its contributor comes from the release's metadata row
    of the same file name when there is one; null value and gauge type from its own header."""
    key = f"fixtures/gesla-{version}-wsv/{name}"
    entry = release.files.get(key)
    if entry is None:
        raise KeyError(f"{name!r} is not a pinned GESLA {version} fixture ({key} not in the manifest)")
    data = (release.root / key).read_bytes()
    got = sha256_bytes(data)
    if got != entry["sha256"] or len(data) != entry["size"]:
        raise ShaMismatch(f"fixture {name}: sha256 {got} size {len(data)}, "
                          f"manifest has {entry['sha256']} size {entry['size']}")
    head = parse_header(data)
    row = release.metadata().get(name)
    meta = {"GAUGE TYPE": head["GAUGE TYPE"], "NULL VALUE": head["NULL VALUE"],
            "CONTRIBUTOR (ABBREVIATED)": row["CONTRIBUTOR (ABBREVIATED)"] if row else head.get("CONTRIBUTOR", "")}
    return parse_record(name, data, meta, roles={}, source_version=version, expected_sha256=entry["sha256"])


# --- cross-correlation lag ---------------------------------------------------------------------

# (lags x record samples) scored at once: ~16 MB per float64 (lags x samples) array, but about a
# dozen such arrays are live together, so one lag_minutes call peaks at ~190 MB on a 1-min or 10-s
# month (measured, review C), a fixed cost per worker that does not grow with record length.
LAG_BLOCK_ELEMENTS = 2_000_000


def _correlations(at: np.ndarray, av: np.ndarray, bt: np.ndarray, bv: np.ndarray,
                  lags: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-lag Pearson correlation of the record samples (at, av) with the comparator (bt, bv)
    linearly interpolated at at - lag, and the number of samples matched at each lag. NaN where a
    lag matches fewer than MIN_SAMPLES samples (a correlation of a handful is close to +-1 whatever
    the lag) or the variance is zero.

    The numbers are those of scoring one lag at a time (tests/reference_monthly_lags.py, compared
    bit for bit by tests/test_timebase_perf_equivalence.py); only the loop structure differs:

    - Interpolation is done for a whole block of lags at once, as 2-D arrays of shape
      (lags in the block, record samples). Every step is elementwise (searchsorted, clip, the gap
      test, the linear weights), so each row holds exactly the values the 1-D code computes.
    - The common case is a lag at which EVERY record sample is matched. Then the 1-D code's x is
      the whole record (av - av.mean(), the same for every such lag) and its y is the whole row, so
      the sums are row reductions of contiguous rows. numpy reduces a contiguous last axis with the
      same pairwise summation as a 1-D array, so mean, sum of squares and cross sum are identical.
    - A lag that matches only some samples (a comparator gap, the month's edge) is scored one at a
      time on its own masked row, exactly as the 1-D code.
    """
    res = np.full(len(lags), np.nan)
    matched = np.zeros(len(lags), dtype=np.int64)
    nb = len(bt)
    block = max(1, LAG_BLOCK_ELEMENTS // max(len(at), 1))
    # The record centred and its sum of squares: the 1-D code's x and (x * x).sum() at any lag
    # that matches every record sample.
    xa = av - av.mean()
    sxx = (xa * xa).sum()
    for c0 in range(0, len(lags), block):
        lg = np.asarray(lags[c0:c0 + block]).astype(np.int64)
        tt = at[None, :] - (lg * 60)[:, None]                 # row k: the stamps at lag lg[k]
        i = np.searchsorted(bt, tt)
        ok = (i > 0) & (i < nb)
        i2 = np.clip(i, 1, nb - 1)
        gap = bt[i2] - bt[i2 - 1]
        ok &= gap <= MAX_INTERP_GAP_S
        mk = ok.sum(axis=1)
        matched[c0:c0 + len(lg)] = mk
        scored = mk >= MIN_SAMPLES      # too few samples to correlate at a lag: NaN, never a +-1 of a few
        if not scored.any():
            continue
        w = (tt - bt[i2 - 1]) / np.maximum(gap, 1)
        v = bv[i2 - 1] + (bv[i2] - bv[i2 - 1]) * w            # the comparator interpolated, per lag
        full = scored & (mk == len(at))
        if full.any():
            vf = v[full]                                       # a contiguous copy of the full rows
            y = vf - vf.mean(axis=1, keepdims=True)
            den = np.sqrt(sxx * (y * y).sum(axis=1))
            num = (xa[None, :] * y).sum(axis=1)
            r = np.full(len(vf), np.nan)
            pos = den > 0
            r[pos] = num[pos] / den[pos]
            res[c0 + np.nonzero(full)[0]] = r
        for k in np.nonzero(scored & ~full)[0]:
            okk = ok[k]
            x = av[okk] - av[okk].mean()
            vk = v[k][okk]
            y = vk - vk.mean()
            den = np.sqrt((x * x).sum() * (y * y).sum())
            if den > 0:
                res[c0 + k] = (x * y).sum() / den
    return res, matched


def lag_minutes(rec: Samples, ref: Samples, start_s: int, end_s: int,
                lags: np.ndarray = LAGS_MIN) -> tuple[float | None, float | None, int, int]:
    """Best lag (min) such that rec(t) ~ ref(t - lag), over rec samples in [start_s, end_s].
    ref is linearly interpolated (not across gaps > 70 min) within [start - 4 h, end + 4 h]. A lag
    is scored only when at least MIN_SAMPLES record samples can be interpolated from ref at it (a
    correlation of a handful of samples is close to +-1 whatever the lag: a comparator that only
    brushes the month or ends before the record starts; Wemeldinge and Bruinisse RWS vs RWS_HIST
    1970-12 had 2 matched samples and r = 1.000 at -2.0 and -17.1 min). The
    correlation peak is refined by a parabola through its neighbours. Returns (lag, r, n, matched):
    n = record samples in the window, matched = those interpolated at the returned lag. Returns
    (None, None, n, 0) when either side has fewer than MIN_SAMPLES samples, or when fewer than
    MIN_LAGS lags can be scored (their samples do not interleave in the window, or every comparator
    gap is > 70 min): the month has no lag, as a month without data.

    Stamps are taken as int64 Unix seconds (Samples): another dtype is cast (truncated) first."""
    at, av = np.asarray(rec[0], dtype=np.int64), rec[1]
    bt, bv = np.asarray(ref[0], dtype=np.int64), ref[1]
    ma = (at >= start_s) & (at <= end_s)
    mb = (bt >= start_s - COMPARATOR_PAD_S) & (bt <= end_s + COMPARATOR_PAD_S)
    at, av, bt, bv = at[ma], av[ma], bt[mb], bv[mb]
    if len(at) < MIN_SAMPLES or len(bt) < MIN_SAMPLES:
        return None, None, int(len(at)), 0
    res, matched = _correlations(at, av, bt, bv, lags)
    if np.isfinite(res).sum() < MIN_LAGS:
        return None, None, int(len(at)), 0
    k = int(np.nanargmax(res))
    curv = res[k - 1] - 2 * res[k] + res[k + 1] if 0 < k < len(res) - 1 else np.nan
    if np.isfinite(curv) and curv != 0:     # a NaN neighbour (or a flat top) is not refined
        d = (res[k - 1] - res[k + 1]) / (2 * curv)
        best = float(lags[k] + d * (lags[1] - lags[0]))
    else:
        best = float(lags[k])
    return best, float(res[k]), int(len(at)), int(matched[k])


@dataclass(frozen=True)
class MonthLag:
    year: int
    month: int
    lag_min: float
    r: float
    n: int              # record samples in the month
    n_matched: int      # of those, interpolated from the comparator at the month's lag


def _month_bounds(year: int, month: int) -> tuple[int, int]:
    """[first day 00:00, last day 23:59] of the month, Unix seconds (as the T4 check)."""
    s = np.datetime64(f"{year:04d}-{month:02d}", "M")
    start = int(s.astype("datetime64[s]").astype(np.int64))
    end = int((s + np.timedelta64(1, "M")).astype("datetime64[s]").astype(np.int64)) - 60
    return start, end


def monthly_lags(rec: Samples, ref: Samples, *, years: Iterable[int] | None = None,
                 months: Iterable[int] | None = None) -> list[MonthLag]:
    """lag_minutes for every calendar month (default: every month of every year the record
    covers), skipping months without enough data on either side."""
    if years is None:
        if len(rec[0]) == 0:
            return []
        y = rec[0][[0, -1]].astype("datetime64[s]").astype("datetime64[Y]").astype(np.int64) + 1970
        years = range(int(y[0]), int(y[1]) + 1)
    months = tuple(range(1, 13)) if months is None else tuple(months)
    rows = []
    # On sorted stamps (Samples are), cut each month out by binary search: the slices hold exactly
    # the samples lag_minutes' masks select, so a month costs its own samples, not the whole record.
    # Unsorted input (outside the Samples contract) keeps the masks over the whole series, and says so.
    rec = (np.asarray(rec[0], dtype=np.int64), rec[1])
    ref = (np.asarray(ref[0], dtype=np.int64), ref[1])
    sorted_ = bool(np.all(np.diff(rec[0]) >= 0)) and bool(np.all(np.diff(ref[0]) >= 0))
    if not sorted_:
        warnings.warn("monthly_lags: stamps not sorted (Samples are): every month masks the whole series "
                      "(same numbers, O(N x months))", RuntimeWarning, stacklevel=2)
    for y in years:
        for m in months:
            start, end = _month_bounds(y, m)
            if sorted_:
                a0, a1 = np.searchsorted(rec[0], start, "left"), np.searchsorted(rec[0], end, "right")
                b0 = np.searchsorted(ref[0], start - COMPARATOR_PAD_S, "left")
                b1 = np.searchsorted(ref[0], end + COMPARATOR_PAD_S, "right")
                lag, r, n, matched = lag_minutes((rec[0][a0:a1], rec[1][a0:a1]), (ref[0][b0:b1], ref[1][b0:b1]),
                                                 start, end)
            else:
                lag, r, n, matched = lag_minutes(rec, ref, start, end)
            if lag is not None:
                rows.append(MonthLag(int(y), int(m), lag, r, n, matched))
    return rows


def _stats(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {"n": len(values), "median": statistics.median(values), "min": min(values), "max": max(values)}


def summary(rows: Sequence[MonthLag]) -> dict:
    """Median of the monthly lags, overall and for January and July separately."""
    lags = [r.lag_min for r in rows]
    return {"n_months": len(rows), "median": statistics.median(lags) if lags else None,
            "jan": _stats([r.lag_min for r in rows if r.month == 1]),
            "jul": _stats([r.lag_min for r in rows if r.month == 7])}


# --- per-constituent lag -----------------------------------------------------------------------

def constituent_lags(table: TcdTable, rec: GaugeRecord, ref: GaugeRecord, start_s: int, end_s: int,
                     names: Sequence[str] = CONSTITUENTS) -> dict[str, dict | None]:
    """Fit both records (QC, Rayleigh and noise rules: select_constituents.analyse) over
    [start_s, end_s) and compare: lag_min = phase difference / speed (+ = rec late) and
    amp_ratio = A_rec / A_ref. A constituent missing from either fit maps to None."""
    fits = [analyse(table, between(clean(r), start_s, end_s)).fit for r in (rec, ref)]
    out: dict[str, dict | None] = {}
    for n in names:
        tn = table.name(n)
        if any(tn not in f.amplitude_m for f in fits):
            out[n] = None
            continue
        dg = (fits[0].phase_deg[tn] - fits[1].phase_deg[tn] + 180.0) % 360.0 - 180.0
        out[n] = {"lag_min": dg / table.speed(tn) * 60.0,
                  "amp_ratio": fits[0].amplitude_m[tn] / fits[1].amplitude_m[tn]}
    return out


# --- direct sampling test ----------------------------------------------------------------------

def _series(s: Samples, start_s: int, end_s: int) -> pd.Series:
    t, h = s
    m = (t >= start_s) & (t <= end_s)
    return pd.Series(h[m], index=pd.to_datetime(t[m], unit="s"))


def _regress(w: pd.Series, ref: pd.Series) -> dict:
    j = pd.concat([w, ref], axis=1, keys=["w", "r"]).dropna()
    x = j.r - j.r.mean()
    y = j.w - j.w.mean()
    slope = float((x * y).sum() / (x * x).sum())
    return {"slope": slope, "resid_cm": float((y - slope * x).std() * 100.0), "n": int(len(j))}


def sampling_test(rec: Samples, ref: Samples, start_s: int, end_s: int) -> dict:
    """Regress the record on the comparator's instantaneous value at t (inst_t), at t + 30 min
    (inst_t30) and on its mean over [t, t + 60 min) (mean60), over [start_s, end_s]. The
    comparator is put on a 1-min grid by linear interpolation (at most 12 missing minutes)."""
    r = _series(ref, start_s, end_s)
    steps = np.diff(r.index.values).astype("timedelta64[m]").astype(np.int64)
    g = r.resample("1min").mean().interpolate(limit=INTERP_LIMIT_MIN)
    w = _series(rec, start_s, end_s)
    candidates = {
        "inst_t": g,
        "inst_t30": g.shift(-30),
        "mean60": g.rolling(60).mean().shift(-59),
    }
    out = {k: _regress(w, v.reindex(w.index)) for k, v in candidates.items()}
    out["ref_interval_min"] = int(pd.Series(steps).mode().iloc[0]) if len(steps) else None
    return out


# --- spring-forward gap ------------------------------------------------------------------------

@dataclass(frozen=True)
class SpringGap:
    zone: str
    hits: int                 # transition days with the missing local hour absent or not good
    years: int                # transition days the record covers
    control_hits: int
    controls: int
    days: list[str] = field(default_factory=list)   # the transition days counted, YYYY-MM-DD


def _spring_forward(zone: ZoneInfo, year: int) -> tuple[dt.date, int] | None:
    """The first day of the year on which the zone's offset increases, and the local hour that
    does not exist on it; None when the zone has no such day that year."""
    day = dt.date(year, 1, 1)
    prev = dt.datetime(year, 1, 1, 12, tzinfo=zone).utcoffset()
    while day.year == year:
        off = dt.datetime(day.year, day.month, day.day, 12, tzinfo=zone).utcoffset()
        if off > prev:
            for hh in range(24):
                local = dt.datetime(day.year, day.month, day.day, hh, tzinfo=zone)
                if local.astimezone(dt.timezone.utc).astimezone(zone).hour != hh:
                    return day, hh
        prev = off
        day += dt.timedelta(days=1)
    return None


def spring_gap(record: GaugeRecord, zone: str, *, years: Iterable[int] | None = None) -> SpringGap:
    """For each year: on the zone's spring-forward day, is the local hour that does not exist
    missing from the record (no good sample stamped in it)? The same hour on CONTROL_DAYS around
    it is the control. A day counts only when the record has a good sample stamped on it."""
    tz = ZoneInfo(zone)
    t, _ = samples(record)
    if years is None:
        if len(t) == 0:
            return SpringGap(zone, 0, 0, 0, 0)
        y = t[[0, -1]].astype("datetime64[s]").astype("datetime64[Y]").astype(np.int64) + 1970
        years = range(int(y[0]), int(y[1]) + 1)

    def stamp(d: dt.date, hh: int = 0) -> int:
        return int((dt.datetime(d.year, d.month, d.day, hh) - dt.datetime(1970, 1, 1)).total_seconds())

    def has(lo: int, hi: int) -> bool:
        return bool(np.searchsorted(t, hi, "left") > np.searchsorted(t, lo, "left"))

    hits = total = chits = ctot = 0
    days = []
    for y in years:
        sf = _spring_forward(tz, int(y))
        if sf is None:
            continue
        day, hh = sf
        if has(stamp(day), stamp(day) + 86400):
            total += 1
            days.append(day.isoformat())
            hits += not has(stamp(day, hh), stamp(day, hh) + 3600)
        for dd in CONTROL_DAYS:
            c = day + dt.timedelta(days=dd)
            if has(stamp(c), stamp(c) + 86400):
                ctot += 1
                chits += not has(stamp(c, hh), stamp(c, hh) + 3600)
    return SpringGap(zone, hits, total, chits, ctot, days)
