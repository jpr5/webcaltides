"""Least-squares harmonic fit in the webcaltides engine's conventions (BP §5).

Model per sample (t in UTC):

    h(t) = Z0 + c * (t - t_mean) / 8766 h
           + sum_k f_k[year] * (a_k cos X_k + b_k sin X_k)
    X_k  = speed_k * (hours since 1 Jan 00:00 UTC of that year) + (V0+u)_k[year]

speed, V0+u and f per year come from the TCD dump (tcd_table.TcdTable, G03a), so the result
is in the engine's convention: A = hypot(a, b), g = atan2(b, a) mod 360, and the engine's
term f * A * cos(speed * t + V0+u - g) is the fitted term. c is the linear trend in m/yr.

The constituent list is an input here; selection (Rayleigh, noise) is G03c.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .qc import Series, despike_mask, unassessed_count
from .tcd_table import TcdTable

HOURS_PER_YEAR = 8766.0
AMP_DECIMALS = 5     # metres: 0.01 mm
PHASE_DECIMALS = 3   # degrees: 0.001


def year_and_hours(times_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Calendar year (UTC) of each stamp, and hours since 1 Jan 00:00 UTC of that year."""
    t = np.asarray(times_s, dtype=np.int64).astype("datetime64[s]")
    years = t.astype("datetime64[Y]")
    hours = (t - years.astype("datetime64[s]")).astype(np.int64) / 3600.0
    return years.astype(np.int64) + 1970, hours


def _yearly(table: TcdTable, name: str, years: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = int(years.min()), int(years.max())
    if lo < table.first_year or hi > table.last_year:
        raise ValueError(f"years {lo}-{hi} outside the TCD dump {table.first_year}-{table.last_year}")
    c = table[name]
    i = years - table.first_year
    return np.asarray(c.v0u, dtype=np.float64)[i], np.asarray(c.f, dtype=np.float64)[i]


def design(table: TcdTable, names: Sequence[str], times_s: np.ndarray) -> np.ndarray:
    """The harmonic columns: f cos X, f sin X for each constituent, in the order of names."""
    years, hours = year_and_hours(times_s)
    cols = np.empty((len(hours), 2 * len(names)), dtype=np.float64)
    for j, n in enumerate(names):
        v0u, f = _yearly(table, n, years)
        x = np.radians(table.speed(n) * hours + v0u)
        cols[:, 2 * j] = f * np.cos(x)
        cols[:, 2 * j + 1] = f * np.sin(x)
    return cols


@dataclass(frozen=True)
class FitResult:
    names: tuple[str, ...]          # TCD names, in input order
    z0: float                       # m, at t_mean
    trend_m_per_yr: float
    t_mean_s: float                 # Unix seconds
    amplitude_m: dict[str, float]
    phase_deg: dict[str, float]     # [0, 360)
    residual: np.ndarray
    rms_m: float
    n: int
    stats: dict

    def to_dict(self) -> dict:
        """Rounded output: amplitude to 0.01 mm, phase to 0.001 deg."""
        return {
            "z0_m": round(self.z0, AMP_DECIMALS),
            "trend_m_per_yr": round(self.trend_m_per_yr, 7),
            "rms_m": round(self.rms_m, AMP_DECIMALS),
            "n": self.n,
            "stats": dict(sorted(self.stats.items())),
            "constituents": {
                n: {"amp_m": round(self.amplitude_m[n], AMP_DECIMALS),
                    "phase_deg": round(self.phase_deg[n], PHASE_DECIMALS) % 360.0}
                for n in sorted(self.names)
            },
        }

    def to_json(self) -> str:
        """Stable JSON: sorted keys, fixed separators, newline at the end."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=1, separators=(",", ": ")) + "\n"


def lsq(table: TcdTable, names: Sequence[str], times_s: np.ndarray, heights_m: np.ndarray, *,
        t_mean_s: float | None = None, stats: dict | None = None) -> FitResult:
    """One least-squares fit of Z0, trend and the named constituents."""
    names = tuple(table.name(n) for n in names)
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate constituents in {names}")
    t = np.asarray(times_s, dtype=np.int64)
    h = np.asarray(heights_m, dtype=np.float64)
    if len(t) < 2 + 2 * len(names):
        raise ValueError(f"{len(t)} samples for {2 + 2 * len(names)} unknowns")
    if t_mean_s is None:
        t_mean_s = float(t.mean())
    tt = (t - t_mean_s) / 3600.0 / HOURS_PER_YEAR
    m = np.column_stack([np.ones(len(t)), tt, design(table, names, t)])
    coef, *_ = np.linalg.lstsq(m, h, rcond=None)
    res = h - m @ coef
    amp, pha = {}, {}
    for j, n in enumerate(names):
        a, b = coef[2 + 2 * j], coef[3 + 2 * j]
        amp[n] = math.hypot(a, b)
        pha[n] = math.degrees(math.atan2(b, a)) % 360.0
    return FitResult(names, float(coef[0]), float(coef[1]), t_mean_s, amp, pha, res,
                     float(np.sqrt(np.mean(res ** 2))), len(t), dict(stats or {}))


def fit_despiked(table: TcdTable, names: Sequence[str], series: Series, *,
                 keep_unassessed: bool = True) -> FitResult:
    """QC step 6 with the fit: fit, drop spikes (qc.despike_mask), refit on the kept samples.
    t_mean stays that of the full series, so Z0 and the trend refer to the same epoch.
    keep_unassessed=False is the PoC's despike (see qc.despike_mask), for the PoC comparison only."""
    t, h = series.times_s, series.heights_m
    t_mean = float(t.mean())
    first = lsq(table, names, t, h, t_mean_s=t_mean)
    keep, mad = despike_mask(t, first.residual, keep_unassessed=keep_unassessed)
    stats = dict(series.stats, spikes_dropped=int((~keep).sum()),
                 spikes_unassessed=unassessed_count(t, first.residual),
                 spike_mad_m=round(mad, 6) if math.isfinite(mad) else None)
    return lsq(table, names, t[keep], h[keep], t_mean_s=t_mean, stats=stats)
