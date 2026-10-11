"""Constituent selection, the fit span, the per-record fit with uncertainty, and the G9 canary
(BP §5 "Constituent selection", §4 record span, §8 G9).

Per record (fit_record):
1. Fit span: the last FIT_SPAN_YEARS years of the cleaned series (a shorter record is used
   whole), less the samples in years outside the TCD dump (as the PoC). The linear trend is
   fitted alongside (fit.lsq). The per-station span choice is G06a.
2. Rayleigh rule: walk CANDIDATES in priority order; keep a candidate when speed * T >= 360 R
   and |speed - speed_k| * T >= 360 R for every constituent already kept. T is the record
   length in hours: the data the series holds (record_hours), not its span. A record of a few
   one-month blocks years apart spans years, but inside each block SA and SSA are nearly
   constant, so on the span they were collinear with Z0 and the trend (fits of 74-296 m SA).
3. Fit and despike (fit.fit_despiked_mask), then the noise rule: keep a constituent when its
   amplitude >= SNR_MIN x the noise of its band (noise.band_noise), then refit.
4. The same analysis on the two halves of the span the primary fit used; u per constituent
   is noise.uncertainty_detail, with its basis per constituent. A half that cannot be fitted
   (FitError only; any other error propagates) is reported in half_errors.
"""

from __future__ import annotations

import dataclasses
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .fit import AMP_DECIMALS, PHASE_DECIMALS, FitError, FitResult, fit_despiked_mask, lsq, year_and_hours
from .noise import band_noise_detail, band_of, uncertainty_detail, vector
from .qc import Series
from .tcd_table import TcdTable

# NOAA's 37, then the TICON-4 extras the TCD defines. Rayleigh conflicts go to the earlier one.
CANDIDATES: tuple[str, ...] = (
    "M2", "S2", "K1", "O1", "N2", "P1", "K2", "Q1", "M4", "SA", "SSA", "NU2", "MU2", "L2", "2N2",
    "MS4", "MN4", "M6", "J1", "OO1", "T2", "LDA2", "S1", "M3", "MK3", "2MK3", "S4", "M8", "2Q1",
    "RHO1", "MM", "MF", "MSF", "R2", "2SM2", "S6", "M1",
    "EPS2", "MKS2", "SIG1", "N4", "S3", "2MK5", "2MO5", "2MS6",
)
RAYLEIGH_R = 1.0
SNR_MIN = 2.0
FIT_SPAN_YEARS = 19.0
SECONDS_PER_YEAR = 365.25 * 86400


def tcd_missing(table: TcdTable, candidates: Sequence[str] = CANDIDATES) -> tuple[str, ...]:
    """Candidates the TCD lacks (rayleigh_select skips them; Analysis records them)."""
    return tuple(n for n in candidates if n not in table)


def record_hours(times_s: np.ndarray) -> float:
    """The record length for the Rayleigh rule: samples x sampling step (the median step),
    at most the span. Equals the span for a record without gaps."""
    t = np.sort(np.asarray(times_s, dtype=np.int64))
    if len(t) < 2:
        return 0.0
    step = float(np.median(np.diff(t)))
    return min(float(t[-1] - t[0]), len(t) * step) / 3600.0


def rayleigh_select(table: TcdTable, span_hours: float, candidates: Sequence[str] = CANDIDATES,
                    r: float = RAYLEIGH_R) -> list[str]:
    """Candidates the span resolves, in priority order. Candidates the TCD lacks are skipped
    (see tcd_missing)."""
    kept: list[str] = []
    speeds: list[float] = []
    for raw in candidates:
        if raw not in table:
            continue
        n = table.name(raw)
        sp = table.speed(n)
        if sp == 0 or sp * span_hours < 360 * r:
            continue
        if all(abs(sp - k) * span_hours >= 360 * r for k in speeds):
            kept.append(n)
            speeds.append(sp)
    return kept


def _subset(series: Series, mask: np.ndarray, **stats) -> Series:
    return Series(series.record_id, series.times_s[mask], series.heights_m[mask], dict(series.stats, **stats))


def last_years(series: Series, years: float = FIT_SPAN_YEARS) -> Series:
    """The samples within `years` of the last one."""
    if len(series) == 0:
        return series
    m = series.times_s >= series.times_s.max() - years * SECONDS_PER_YEAR
    return _subset(series, m, in_window=int(m.sum()))


def in_tcd_years(table: TcdTable, series: Series) -> Series:
    """The samples whose UTC year is in the TCD dump's range (the PoC's filter, after the span
    window). stats outside_tcd_years: how many were dropped."""
    years, _ = year_and_hours(series.times_s)
    m = (years >= table.first_year) & (years <= table.last_year)
    return _subset(series, m, outside_tcd_years=int((~m).sum()))


def between(series: Series, start_s: float, end_s: float) -> Series:
    """The samples with start_s <= t < end_s."""
    m = (series.times_s >= start_s) & (series.times_s < end_s)
    return _subset(series, m, in_window=int(m.sum()))


@dataclass(frozen=True, eq=False)
class Analysis:
    """One selected fit: the final FitResult and how its constituents were chosen."""
    fit: FitResult
    span_hours: float                   # last - first of the series analysed (before despiking)
    first_s: int                        # first and last stamp the fit used (after despiking)
    last_s: int
    rayleigh_kept: tuple[str, ...]
    snr_dropped: tuple[str, ...]
    noise: dict[str, float]             # band -> m (NaN: no noise frequency in the band)
    constituent_noise: dict[str, float]  # kept constituent -> its band's noise, m
    noise_n: dict[str, int]             # band -> test frequencies used (noise.NOISE_FREQUENCIES normally)
    noise_basis: dict[str, str]         # band -> noise.TIERS name used ("" for none)
    constituent_band: dict[str, str] = dataclasses.field(default_factory=dict)  # kept constituent -> band
    record_id: str = ""
    tcd_missing: tuple[str, ...] = ()   # candidates the TCD lacks (skipped by the Rayleigh rule)
    rayleigh_hours: float = math.nan    # T of the Rayleigh rule (record_hours, before despiking)

    def consts(self) -> dict[str, tuple[float, float]]:
        return {n: (self.fit.amplitude_m[n], self.fit.phase_deg[n]) for n in self.fit.names}


def analyse(table: TcdTable, series: Series, *, candidates: Sequence[str] = CANDIDATES,
            snr: float = SNR_MIN, keep_unassessed: bool = True, seed: int | None = None,
            rayleigh_on_span: bool = False) -> Analysis:
    """Rayleigh rule, fit and despike, noise rule, refit (module docstring steps 2-3).
    rayleigh_on_span=True is the PoC's Rayleigh length (the span), for the PoC comparison only."""
    if len(series) < 2:
        raise FitError(f"{series.record_id}: {len(series)} samples")
    t, h = series.times_s, series.heights_m
    span = float(t.max() - t.min()) / 3600.0
    rayleigh_t = span if rayleigh_on_span else record_hours(t)
    names = rayleigh_select(table, rayleigh_t, candidates)
    second, keep = fit_despiked_mask(table, names, series, keep_unassessed=keep_unassessed)
    tk, hk = t[keep], h[keep]
    kw = {} if seed is None else {"seed": seed}
    bn = band_noise_detail(table, second.residual, tk, span, fitted_speeds=[table.speed(n) for n in names], **kw)
    noise = bn.noise
    band = {n: band_of(table.speed(n)) for n in names}
    unmeasured = sorted({band[n] for n in names if not math.isfinite(noise[band[n]])})
    if unmeasured:
        raise FitError(f"{series.record_id}: band {', '.join(unmeasured)} has no noise test frequency "
                       f"for a span of {span:.0f} h; its constituents cannot be assessed")
    kept = [n for n in names if second.amplitude_m[n] >= snr * noise[band[n]]]
    final = lsq(table, kept, tk, hk, t_mean_s=float(t.mean()), stats=second.stats, record_id=series.record_id)
    return Analysis(final, span, int(tk.min()), int(tk.max()), tuple(names),
                    tuple(n for n in names if n not in kept), noise, {n: noise[band[n]] for n in kept},
                    bn.n, bn.basis, {n: band[n] for n in kept}, series.record_id, tcd_missing(table, candidates),
                    rayleigh_t)


def _round_or_none(x: float, nd: int) -> float | None:
    return round(x, nd) if math.isfinite(x) else None


@dataclass(frozen=True, eq=False)
class RecordFit:
    """The fit of one record: the primary analysis, the two half analyses and u."""
    primary: Analysis
    half1: Analysis | None
    half2: Analysis | None
    half_errors: tuple[str, ...]
    u: dict[str, float]
    u_basis: dict[str, str]             # constituent -> noise.uncertainty_detail basis

    def considered(self) -> set[str]:
        """Every constituent any of the analyses considered (its Rayleigh-kept list)."""
        return {n for a in (self.primary, self.half1, self.half2) if a is not None for n in a.rayleigh_kept}

    def scaled(self, factors: Mapping[str, float]) -> "RecordFit":
        """Amplitudes multiplied per constituent (a sampling response divided out), u recomputed.
        The band noise is a property of the residual and is not scaled. Keys are TCD names; a key
        no analysis considered (another spelling, or a constituent the span cannot resolve) raises."""
        unknown = sorted(set(factors) - self.considered())
        if unknown:
            raise ValueError(f"{self.primary.record_id}: scale factors for constituents the fit never "
                             f"considered: {unknown} (keys are TCD names)")

        def sc(a: Analysis | None) -> Analysis | None:
            if a is None:
                return None
            amp = {n: v * factors.get(n, 1.0) for n, v in a.fit.amplitude_m.items()}
            return dataclasses.replace(a, fit=dataclasses.replace(a.fit, amplitude_m=amp))
        p, h1, h2 = sc(self.primary), sc(self.half1), sc(self.half2)
        return RecordFit(p, h1, h2, self.half_errors, *_u(p, h1, h2))

    def to_dict(self) -> dict:
        p = self.primary
        f = p.fit.to_dict()
        for n, c in f["constituents"].items():
            c["noise_m"] = round(p.constituent_noise[n], AMP_DECIMALS)
            c["u_m"] = round(self.u[n], AMP_DECIMALS)
            c["u_basis"] = self.u_basis[n]
        halves = {}
        for k, a in (("half1", self.half1), ("half2", self.half2)):
            halves[k] = None if a is None else {
                "first_s": a.first_s, "last_s": a.last_s, "n": a.fit.n,
                "constituents": {n: {"amp_m": round(a.fit.amplitude_m[n], AMP_DECIMALS),
                                     "phase_deg": round(a.fit.phase_deg[n], PHASE_DECIMALS) % 360.0}
                                 for n in sorted(a.fit.names)}}
        return dict(f, first_s=p.first_s, last_s=p.last_s, span_hours=round(p.span_hours, 3),
                    rayleigh_hours=round(p.rayleigh_hours, 3),
                    rayleigh_kept=list(p.rayleigh_kept), snr_dropped=list(p.snr_dropped),
                    candidates_missing_from_tcd=list(p.tcd_missing),
                    band_noise_m={b: _round_or_none(v, AMP_DECIMALS) for b, v in sorted(p.noise.items())},
                    band_noise_n=dict(sorted(p.noise_n.items())), band_noise_basis=dict(sorted(p.noise_basis.items())),
                    halves=halves, half_errors=list(self.half_errors))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=1, separators=(",", ": "), allow_nan=False) + "\n"


def _u(p: Analysis, h1: Analysis | None, h2: Analysis | None) -> tuple[dict[str, float], dict[str, str]]:
    halves = [h for h in (h1, h2) if h is not None]
    half_noise = {}
    for n in p.constituent_noise:
        b = p.constituent_band[n]
        vals = [h.noise[b] for h in halves if math.isfinite(h.noise.get(b, math.nan))]
        if vals:
            half_noise[n] = max(vals)
    return uncertainty_detail(p.constituent_noise, h1.consts() if h1 else {}, h2.consts() if h2 else {},
                              primary=p.consts(), half_noise=half_noise)


def sampling_corrected(rf: RecordFit, table: TcdTable, correction, span: tuple[int, int]) -> RecordFit:
    """rf with the sampling response of the record's time base divided out of every amplitude (BP §2.3):
    each constituent the fit considered is multiplied by correction.amplitude_factor(its speed, span)
    (utc_mean_start60: 1 / boxcar response, M2 x1.011; a step: the duration-weighted mean over span; an
    instant base: 1). Phases are not touched (the time shift is applied to the series by qc.clean).

    The correction must be the one the series was cleaned with (stats["timebase"], "utc_instant" when none),
    and rf must not be scaled already: either mismatch raises ValueError. The factors applied are stamped in
    the primary fit's stats["sampling_corrected"] = {"kind", "label", "factors"} (in the fit's JSON), also
    when they are all 1, so a consumer can tell a corrected fit from one never corrected."""
    stats = rf.primary.fit.stats
    rid = rf.primary.record_id or "?"
    if "sampling_corrected" in stats:
        raise ValueError(f"{rid}: sampling response already divided out ({stats['sampling_corrected']['label']})")
    cleaned = stats.get("timebase", "utc_instant")
    if cleaned != correction.label:
        raise ValueError(f"{rid}: series cleaned as {cleaned}, sampling correction asked for {correction.label}")
    factors = {n: correction.amplitude_factor(table.speed(n), span) for n in sorted(rf.considered())}
    factors = {n: f for n, f in factors.items() if f != 1.0}
    out = rf.scaled(factors) if factors else rf
    mark = {"kind": correction.kind, "label": correction.label, "factors": {n: round(f, 9) for n, f in factors.items()}}
    fit = dataclasses.replace(out.primary.fit, stats={**out.primary.fit.stats, "sampling_corrected": mark})
    return dataclasses.replace(out, primary=dataclasses.replace(out.primary, fit=fit))


def fit_record(table: TcdTable, series: Series, *, years: float = FIT_SPAN_YEARS,
               candidates: Sequence[str] = CANDIDATES, keep_unassessed: bool = True,
               rayleigh_on_span: bool = False) -> RecordFit:
    """Steps 1-4 of the module docstring on a cleaned series (qc.clean). keep_unassessed=False
    and rayleigh_on_span=True together are the PoC's method (the PoC comparison only)."""
    kw = dict(candidates=candidates, keep_unassessed=keep_unassessed, rayleigh_on_span=rayleigh_on_span)
    primary = analyse(table, in_tcd_years(table, last_years(series, years)), **kw)
    s0, s1 = primary.first_s, primary.last_s
    mid = s0 + (s1 - s0) / 2
    halves: list[Analysis | None] = []
    errors = []
    for k, (a, b) in (("half1", (s0, mid)), ("half2", (mid, s1 + 1))):   # half2 ends at s1, inclusive
        try:
            halves.append(analyse(table, between(series, a, b), **kw))
        except FitError as e:
            halves.append(None)
            errors.append(f"{k}: {e}")
    return RecordFit(primary, halves[0], halves[1], tuple(errors), *_u(primary, halves[0], halves[1]))


# --- G9 convention canary (BP §8) ------------------------------------------------------------------

G9_CONSTITUENTS = ("M2", "S2", "K1", "O1")
G9_AMP_TOL_M = 0.01
G9_PHASE_TOL_DEG = 2.0


@dataclass(frozen=True)
class G9Result:
    station: str
    da_m: dict[str, float]
    dg_deg: dict[str, float]
    missing: tuple[str, ...]
    median_da_m: float                  # NaN when no constituent is on both sides
    median_dg_deg: float
    passed: bool

    def to_dict(self) -> dict:
        """Strict-JSON safe: a median that does not exist is None, with an error naming why."""
        d = {"station": self.station, "missing": list(self.missing), "passed": self.passed,
             "median_da_cm": _round_or_none(self.median_da_m * 100, 3),
             "median_dg_deg": _round_or_none(self.median_dg_deg, 3),
             "da_cm": {n: round(v * 100, 3) for n, v in self.da_m.items()},
             "dg_deg": {n: round(v, 3) for n, v in self.dg_deg.items()}}
        if not self.da_m:
            d["error"] = f"no constituent of {', '.join(G9_CONSTITUENTS)} is in both the fit and the reference"
        return d


def g9_canary(station: str, fit: Mapping[str, tuple[float, float]],
              ref: Mapping[str, tuple[float, float]]) -> G9Result:
    """Median |dA| and |dg| over M2, S2, K1, O1 of a fit against the official constants (amp m,
    phase deg, same convention). Fails when a constituent is missing from either side."""
    missing = tuple(n for n in G9_CONSTITUENTS if n not in fit or n not in ref)
    da = {n: fit[n][0] - ref[n][0] for n in G9_CONSTITUENTS if n not in missing}
    dg = {n: (fit[n][1] - ref[n][1] + 180) % 360 - 180 for n in G9_CONSTITUENTS if n not in missing}
    mda = statistics.median(abs(v) for v in da.values()) if da else math.nan
    mdg = statistics.median(abs(v) for v in dg.values()) if dg else math.nan
    ok = not missing and mda <= G9_AMP_TOL_M and mdg <= G9_PHASE_TOL_DEG
    return G9Result(station, da, dg, missing, mda, mdg, ok)


def load_noaa_harcon(path: str | Path) -> dict[str, tuple[float, float]]:
    """NOAA CO-OPS harcon.json (metric) -> name -> (amplitude m, phase_GMT deg).
    Wrong units, a missing key or a duplicate name raise ValueError naming the file."""
    data = json.loads(Path(path).read_text())
    if data.get("units") != "meters":
        raise ValueError(f"{path}: units {data.get('units')!r}, expected 'meters'")
    if "HarmonicConstituents" not in data:
        raise ValueError(f"{path}: no 'HarmonicConstituents'")
    out: dict[str, tuple[float, float]] = {}
    for i, c in enumerate(data["HarmonicConstituents"]):
        miss = [k for k in ("name", "amplitude", "phase_GMT") if k not in c]
        if miss:
            raise ValueError(f"{path}: constituent {i} lacks {', '.join(miss)}")
        if c["name"] in out:
            raise ValueError(f"{path}: duplicate constituent {c['name']!r}")
        out[c["name"]] = (float(c["amplitude"]), float(c["phase_GMT"]))
    return out


__all__ = ["CANDIDATES", "RAYLEIGH_R", "SNR_MIN", "FIT_SPAN_YEARS", "record_hours", "rayleigh_select", "last_years",
           "between", "Analysis", "analyse", "RecordFit", "fit_record", "G9_CONSTITUENTS", "G9_AMP_TOL_M",
           "G9_PHASE_TOL_DEG", "G9Result", "g9_canary", "load_noaa_harcon", "vector"]
