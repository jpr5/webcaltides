"""Reference implementation of the monthly lag computation, as it was before the performance work
(gesla.timebase_measure.lag_minutes / monthly_lags and gesla.timebase_verdict's per-call row
computation at 9dda78a, the base of the TB-PERF-2 change). Kept in the tests, not the library: the optimised code must give the
same numbers BIT FOR BIT (tests/test_timebase_perf_equivalence.py).

Do not optimise this file. It is the specification the fast path is checked against.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from gesla.timebase_measure import (COMPARATOR_PAD_S, LAGS_MIN, MAX_INTERP_GAP_S, MIN_LAGS, MIN_SAMPLES, MonthLag,
                                    Samples, _month_bounds)


def correlations(at: np.ndarray, av: np.ndarray, bt: np.ndarray, bv: np.ndarray,
                 lags: np.ndarray = LAGS_MIN) -> tuple[np.ndarray, np.ndarray]:
    """The per-lag Pearson correlation and matched count of the month's samples: one lag at a time."""
    res = np.full(len(lags), np.nan)
    matched = np.zeros(len(lags), dtype=np.int64)
    for k, lag in enumerate(lags):
        tt = at - int(lag) * 60
        i = np.searchsorted(bt, tt)
        ok = (i > 0) & (i < len(bt))
        i2 = np.clip(i, 1, len(bt) - 1)
        gap = bt[i2] - bt[i2 - 1]
        ok &= gap <= MAX_INTERP_GAP_S
        matched[k] = ok.sum()
        if matched[k] < MIN_SAMPLES:
            continue
        w = (tt - bt[i2 - 1]) / np.maximum(gap, 1)
        v = bv[i2 - 1] + (bv[i2] - bv[i2 - 1]) * w
        x = av[ok] - av[ok].mean()
        y = v[ok] - v[ok].mean()
        den = np.sqrt((x * x).sum() * (y * y).sum())
        if den > 0:
            res[k] = (x * y).sum() / den
    return res, matched


def lag_minutes(rec: Samples, ref: Samples, start_s: int, end_s: int,
                lags: np.ndarray = LAGS_MIN) -> tuple[float | None, float | None, int, int]:
    at, av = rec
    bt, bv = ref
    ma = (at >= start_s) & (at <= end_s)
    mb = (bt >= start_s - COMPARATOR_PAD_S) & (bt <= end_s + COMPARATOR_PAD_S)
    at, av, bt, bv = at[ma], av[ma], bt[mb], bv[mb]
    if len(at) < MIN_SAMPLES or len(bt) < MIN_SAMPLES:
        return None, None, int(len(at)), 0
    res, matched = correlations(at, av, bt, bv, lags)
    if np.isfinite(res).sum() < MIN_LAGS:
        return None, None, int(len(at)), 0
    k = int(np.nanargmax(res))
    curv = res[k - 1] - 2 * res[k] + res[k + 1] if 0 < k < len(res) - 1 else np.nan
    if np.isfinite(curv) and curv != 0:
        d = (res[k - 1] - res[k + 1]) / (2 * curv)
        best = float(lags[k] + d * (lags[1] - lags[0]))
    else:
        best = float(lags[k])
    return best, float(res[k]), int(len(at)), int(matched[k])


def monthly_lags(rec: Samples, ref: Samples, *, years: Iterable[int] | None = None,
                 months: Iterable[int] | None = None) -> list[MonthLag]:
    if years is None:
        if len(rec[0]) == 0:
            return []
        y = rec[0][[0, -1]].astype("datetime64[s]").astype("datetime64[Y]").astype(np.int64) + 1970
        years = range(int(y[0]), int(y[1]) + 1)
    months = tuple(range(1, 13)) if months is None else tuple(months)
    rows = []
    for y in years:
        for m in months:
            start, end = _month_bounds(y, m)
            lag, r, n, matched = lag_minutes(rec, ref, start, end)
            if lag is not None and r is not None:      # lag_minutes returns both or neither
                rows.append(MonthLag(int(y), int(m), lag, r, n, matched))
    return rows


def corrected_rows(corr, ev) -> list[MonthLag]:
    """timebase_verdict at 9dda78a: the corrected series' monthly lags, recomputed on every call."""
    from gesla.timebase_verdict import _apply, _clip, _rows
    return _rows(_clip(_apply(corr, ev.rec), *ev.span), ev.comp)
