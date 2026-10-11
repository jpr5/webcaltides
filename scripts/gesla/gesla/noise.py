"""Residual noise per band and the uncertainty u of each constituent (BP §5, selection steps 3-4).

Band noise: the RMS amplitude of single-sinusoid least-squares fits to the residual at
NOISE_FREQUENCIES random frequencies per band. The frequencies come from a fixed seed, so a
build is deterministic. A test frequency must stay clear of tidal lines; the rule is tried in
this order, per band, and the first that yields NOISE_FREQUENCIES frequencies within
NOISE_MAX_TRIES draws is used (the basis is reported per band):

1. "tcd_lines": at least NOISE_MIN_RAYLEIGH Rayleigh resolutions (360 deg / T) from every TCD
   line. This is the build plan's rule; every record of a year or more (all PoC records) uses it.
2. "fitted_lines": at least NOISE_MIN_RAYLEIGH Rayleigh from every fitted (Rayleigh-kept) line.
   Under about 45 days the 176 TCD lines cover D1 and D2 entirely at 3 Rayleigh, while the
   span still resolves M2, S2, K1, O1. A line the span cannot resolve has no separate energy:
   it is merged into the fitted line near it, so only the fitted lines need the guard.
3. "fitted_lines_1R": at least 1 Rayleigh from every fitted line, where a test sinusoid is
   close to orthogonal to the fitted ones (the last tier keeps what it finds, even below
   NOISE_FREQUENCIES).
Test frequencies near unfitted lines can see some unresolved tidal energy, so tiers 2-3 can
only raise the noise: the noise rule then keeps fewer constituents and u is not understated.
A band where no tier finds any frequency has noise NaN and count 0; the selection raises
FitError when a constituent it would keep is in such a band (it never drops it silently).

Accepted limit, SA and SSA: the LP band samples 0.02-0.5 cpd (the build plan's LP band); SA
(0.0027 cpd) and SSA (0.0055 cpd) lie below it. Sea-level spectra are red, so their band noise is
likely lower than the true noise at the annual frequency: SA and SSA pass the noise rule more
easily and their u leans low. The served set handles them separately (build plan §5 long-period
rule: dropped at River/Lake gauges and when SA >= 0.25 m), and the halves term of u still
measures their year-to-year change. Changing the band would change every PoC record's SA/SSA.

Uncertainty: see uncertainty_detail(). A constituent without a half comparison is flagged
(its basis) and gets the noise of a half-length fit, so its u is not understated.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .tcd_table import TcdTable

# Bands in cycles per day. The order is part of the seeded draw sequence: do not reorder.
BANDS: dict[str, tuple[float, float]] = {
    "LP": (0.02, 0.5), "D1": (0.8, 1.2), "D2": (1.75, 2.15), "D3": (2.8, 3.2), "D4": (3.7, 4.2),
    "D5": (4.7, 5.2), "D6": (5.7, 6.2), "D8": (7.7, 8.2),
}
LONG_PERIOD_MAX_CPD = 0.6
NOISE_FREQUENCIES = 40
NOISE_SEED = 0
NOISE_MIN_RAYLEIGH = 3.0
NOISE_MAX_TRIES = 4000


def band_of(speed_deg_h: float) -> str:
    """The noise band of a constituent: LP below 0.6 cpd, else the D band with the nearest centre."""
    cpd = speed_deg_h * 24 / 360.0
    if cpd < LONG_PERIOD_MAX_CPD:
        return "LP"
    return min((abs(cpd - (lo + hi) / 2), b) for b, (lo, hi) in BANDS.items() if b != "LP")[1]


TIERS: tuple[tuple[str, bool, float], ...] = (
    ("tcd_lines", False, NOISE_MIN_RAYLEIGH),
    ("fitted_lines", True, NOISE_MIN_RAYLEIGH),
    ("fitted_lines_1R", True, 1.0),
)


@dataclass(frozen=True)
class BandNoise:
    noise: dict[str, float]     # band -> m (NaN: no test frequency found)
    n: dict[str, int]           # band -> test frequencies used
    basis: dict[str, str]       # band -> the TIERS name used ("" when none found any)


def band_noise_detail(table: TcdTable, residual: np.ndarray, times_s: np.ndarray, span_hours: float, *,
                      fitted_speeds: Sequence[float] = (), n_freq: int = NOISE_FREQUENCIES,
                      seed: int = NOISE_SEED) -> BandNoise:
    """Noise amplitude (m) per band of a fit residual, with the count and the basis per band;
    see the module docstring. fitted_speeds: the speeds (deg/h) of the fitted constituents."""
    r = np.asarray(residual, dtype=np.float64)
    t_h = np.asarray(times_s, dtype=np.int64) / 3600.0
    if len(r) != len(t_h):
        raise ValueError(f"{len(r)} residuals for {len(t_h)} times")
    t_h = t_h - t_h.mean()
    all_lines = np.array([c.speed for c in table.constituents.values() if c.speed > 0])
    if len(all_lines) == 0:
        raise ValueError("the TCD table has no positive-speed constituent: no tidal line to keep the "
                         "noise frequencies away from")
    fitted = np.array([sp for sp in fitted_speeds if sp > 0], dtype=np.float64)
    rng = np.random.default_rng(seed)
    noise: dict[str, float] = {}
    count: dict[str, int] = {}
    basis: dict[str, str] = {}
    for band, (lo, hi) in BANDS.items():
        amps: list[float] = []
        used = ""
        for name, only_fitted, guard in TIERS:
            lines = fitted if only_fitted else all_lines
            amps = []
            tries = 0
            while len(amps) < n_freq and tries < NOISE_MAX_TRIES:
                tries += 1
                speed = rng.uniform(lo, hi) * 360 / 24
                if len(lines) and np.min(np.abs(lines - speed)) * span_hours < 360 * guard:
                    continue
                x = np.radians(speed * t_h)
                c, *_ = np.linalg.lstsq(np.column_stack([np.cos(x), np.sin(x)]), r, rcond=None)
                amps.append(math.hypot(c[0], c[1]))
            used = name if amps else ""
            if len(amps) >= n_freq:
                break
        noise[band] = float(np.sqrt(np.mean(np.square(amps)))) if amps else float("nan")
        count[band] = len(amps)
        basis[band] = used
    return BandNoise(noise, count, basis)


def band_noise(table: TcdTable, residual: np.ndarray, times_s: np.ndarray, span_hours: float, *,
               fitted_speeds: Sequence[float] = (), n_freq: int = NOISE_FREQUENCIES,
               seed: int = NOISE_SEED) -> dict[str, float]:
    """Noise amplitude (m) per band of a fit residual; see band_noise_detail."""
    return band_noise_detail(table, residual, times_s, span_hours, fitted_speeds=fitted_speeds,
                             n_freq=n_freq, seed=seed).noise


def vector(amp_m: float, phase_deg: float) -> complex:
    return cmath.rect(amp_m, -math.radians(phase_deg))


def uncertainty_detail(noise: Mapping[str, float], half1: Mapping[str, tuple[float, float]],
                       half2: Mapping[str, tuple[float, float]], *,
                       primary: Mapping[str, tuple[float, float]] | None = None,
                       half_noise: Mapping[str, float] | None = None) -> tuple[dict[str, float], dict[str, str]]:
    """u per constituent of `noise` (its band noise in the primary fit, m), and the basis used.
    half1/half2/primary: name -> (amp m, phase deg). half_noise: name -> the largest band noise
    of that band in the half fits that exist (m).

    - "halves" (in both halves): u = max(band noise, |z_half1 - z_half2| / 2), z = A exp(-i g).
    - "one_half" (one half failed or lacks it): u = max(band noise, half noise, |z_half - z_primary|);
      z_primary is about (z_half1 + z_half2) / 2, so the last term is on the scale of the first rule's.
    - "band_noise" (in no half): u = max(band noise, half noise).
    The half noise is the noise of a fit to half the data, the floor of the halves rule: from the
    half fits where they exist, else sqrt(2) x the band noise (noise amplitude ~ 1 / sqrt(N)).
    So u without a half comparison is never below what the halves rule would give for pure noise.
    """
    primary = primary or {}
    half_noise = half_noise or {}
    u: dict[str, float] = {}
    basis: dict[str, str] = {}
    for n, nz in noise.items():
        if n in half1 and n in half2:
            u[n] = max(nz, abs(vector(*half1[n]) - vector(*half2[n])) / 2)
            basis[n] = "halves"
            continue
        hn = half_noise.get(n, math.nan)
        hn = hn if math.isfinite(hn) else math.sqrt(2) * nz
        u[n] = max(nz, hn)
        basis[n] = "band_noise"
        for h in (half1, half2):
            if n in h:
                basis[n] = "one_half"
                if n in primary:
                    u[n] = max(u[n], abs(vector(*h[n]) - vector(*primary[n])))
    return u, basis


def uncertainty(noise: Mapping[str, float], half1: Mapping[str, tuple[float, float]],
                half2: Mapping[str, tuple[float, float]], **kw) -> dict[str, float]:
    """u per constituent; see uncertainty_detail."""
    return uncertainty_detail(noise, half1, half2, **kw)[0]
