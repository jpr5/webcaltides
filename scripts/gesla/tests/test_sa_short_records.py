"""G03c-sa: SA/SSA on short or gappy records.

A record of a few one-month occupations spread over years (NOAA's short-term Alaska stations)
spans more than a year, but holds a few months of data. With the Rayleigh rule on the span
(last - first), SA and SSA were fitted: inside a one-month block SA is nearly a constant, so it
was collinear with Z0 and the trend, and real fits reported SA of 74 to 296 m (G06b sa_study).
The Rayleigh rule's record length is the data the record holds, not its span.
"""

from __future__ import annotations

import numpy as np
import pytest
from test_poc_reproduction import (  # noqa: F401 (poc: fixture)
    T2019,
    fake_table,
    poc,
    synth,
)

from gesla import select_constituents as sc
from gesla.qc import Series, clean
from gesla.select_constituents import analyse

TRUTH = {"M2": (1.0, 40.0), "S2": (0.3, 70.0), "K1": (0.4, 200.0), "O1": (0.3, 150.0), "SA": (0.05, 100.0)}
HOUR = 3600
YEAR_H = 8766


def red_noise(n: int, seed: int, tau_h: float = 72.0, sd: float = 0.10) -> np.ndarray:
    """AR(1) hourly noise, correlation time tau_h: weather-like non-tidal level."""
    rng = np.random.default_rng(seed)
    phi = np.exp(-1.0 / tau_h)
    e = rng.normal(0.0, sd * np.sqrt(1 - phi ** 2), n)
    x = np.empty(n)
    x[0] = rng.normal(0.0, sd)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def blocks(starts_yr: tuple[float, ...], days: int = 30) -> np.ndarray:
    return np.concatenate([T2019 + int(s * YEAR_H * HOUR) + np.arange(days * 24) * HOUR for s in starts_yr])


def test_record_length_is_the_data_held_not_the_span():
    t = blocks((0.1, 0.9, 1.45))
    assert sc.record_hours(t) == pytest.approx(3 * 30 * 24)
    cont = T2019 + np.arange(1000) * HOUR
    assert sc.record_hours(cont) == pytest.approx(999.0)    # never more than the span
    three = T2019 + np.arange(0, 3000, 3) * HOUR            # 3-hourly: the step is 3 h
    assert sc.record_hours(three) == pytest.approx(2997.0)


def test_gappy_18_month_record_never_fits_sa_at_metres():
    """Three one-month blocks over 1.5 years, a trend and a 5 cm SA, with weather-like noise.
    SA is either resolved (within 2 cm) or not fitted. At 2f2c7f4 it was fitted at 38 cm."""
    tab = fake_table()
    t = blocks((0.1, 0.9, 1.45))
    h = synth(tab, TRUTH, t) + red_noise(len(t), seed=2)
    a = analyse(tab, Series("gappy", t, h, {}), seed=1)
    assert a.span_hours > YEAR_H                          # the span alone passes SA's Rayleigh test
    sa = a.fit.amplitude_m.get("SA")
    assert sa is None or abs(sa - 0.05) <= 0.02, f"SA {sa} m"


def test_continuous_18_month_record_resolves_sa_with_the_trend():
    tab = fake_table()
    t = T2019 + np.arange(int(1.5 * YEAR_H)) * HOUR
    h = synth(tab, TRUTH, t) + red_noise(len(t), seed=1)
    a = analyse(tab, Series("cont", t, h, {}), seed=1)
    assert "SA" in a.fit.names
    assert abs(a.fit.amplitude_m["SA"] - 0.05) <= 0.02
    assert abs(a.fit.trend_m_per_yr - 0.003) <= 0.03


# Worst SA of G06b's sa_study (fitted at 2f2c7f4: SA 296, 233, 160 m; 0.09-0.24 years of data).
WORST = ("point_spencer-9469237-usa-noaa", "cape_hichinbrook-9454329-usa-noaa", "saginaw_bay-9451497-usa-noaa")


@pytest.mark.parametrize("fid", WORST)
def test_real_short_occupation_records_do_not_fit_sa_or_ssa(poc, fid):  # noqa: F811
    table, release, _ = poc
    s = sc.in_tcd_years(table, sc.last_years(clean(release.record(fid, roles={}))))
    a = analyse(table, s)
    big = {n: round(v, 3) for n, v in a.fit.amplitude_m.items() if v > 5.0}
    assert not big and abs(a.fit.z0) < 10.0, f"z0 {a.fit.z0:.3f} m, amplitudes over 5 m: {big}"
    assert "SA" not in a.rayleigh_kept and "SSA" not in a.rayleigh_kept
    assert sc.record_hours(s.times_s) < YEAR_H


def test_woodlawn_half2_is_the_only_poc_fit_the_record_length_changes(poc):  # noqa: F811
    """Woodlawn's second half spans 4821 h but holds 4249 h of data, under the half year that
    K2/S2 and K1/P1 need: with the data length it no longer fits K2, P1, SSA (PoC mode keeps the
    PoC's span length, so the PoC reproduction is unchanged)."""
    from test_poc_reproduction import run
    table = poc[0]
    half_year = 360.0 / abs(table.speed("K2") - table.speed("S2"))     # about 4383 h
    ours = run(poc, "woodlawn", keep_unassessed=True)
    h2 = ours.half2
    assert h2.rayleigh_hours < half_year <= h2.span_hours
    assert not {"K2", "P1", "SSA"} & set(h2.rayleigh_kept)
    assert {"K2", "P1", "SSA"} <= set(ours.primary.rayleigh_kept)
