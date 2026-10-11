"""R7: the Rayleigh walk keeps Foreman's hierarchy (a minor constituent is not kept while the major one it is
compared with cannot be resolved), and names the nearest kept constituent as the partner under 30 days.
Foreman (1977, rev. 2004) comparison table as in T_TIDE / UTide ut_constants (kmpr, df)."""
from __future__ import annotations

import numpy as np
import pytest

from gesla.read_gesla import otc_work
from gesla.select_constituents import CANDIDATES, CONFLICT, FOREMAN_COMPARISON, rayleigh_drops, rayleigh_select
from gesla.tcd_table import TcdTable

REAL_TCD = otc_work() / "tcd_table.json"
real = pytest.mark.skipif(not REAL_TCD.exists(), reason=f"no real TCD dump at {REAL_TCD}")


def old_walk(table, span_hours, r=1.0):
    kept, speeds = [], []
    for raw in CANDIDATES:
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


@pytest.fixture(scope="module")
def tab():
    if not REAL_TCD.exists():
        pytest.skip(f"no real TCD dump at {REAL_TCD}")
    return TcdTable.load(REAL_TCD)


@real
def test_under_27_6_days_mu2_2q1_oo1_are_not_kept_without_n2_q1_j1(tab):
    for d in (15.3, 20, 25, 27.0):
        k = rayleigh_select(tab, d * 24)
        assert "N2" not in k and "Q1" not in k
        assert "MU2" not in k and "2Q1" not in k and "OO1" not in k, (d, k)


@real
def test_between_27_6_and_31_8_days_n2_is_kept_and_2n2_is_not(tab):
    for d in (28, 30, 31.7):
        k = rayleigh_select(tab, d * 24)
        assert "N2" in k and "Q1" in k and "MU2" not in k and "2N2" not in k, (d, k)


@real
def test_unchanged_from_31_85_days_on(tab):
    for d in np.arange(31.85, 800, 0.25):
        assert rayleigh_select(tab, d * 24) == old_walk(tab, d * 24), d


@real
def test_partner_is_the_nearest_kept_under_30_days(tab):
    d = {x.name: x for x in rayleigh_drops(tab, 25 * 24)}
    kept = rayleigh_select(tab, 25 * 24)
    for x in d.values():
        if x.kind != CONFLICT:
            continue
        sp = tab.speed(x.name)
        best = min(kept, key=lambda k: (abs(tab.speed(k) - sp), k))
        assert x.partner == best, (x, best)
    assert d["MU2"].partner == "M2" and d["N2"].partner == "M2" and d["2Q1"].partner == "O1"


@real
def test_partner_from_30_days_is_the_rule_partner_when_kept(tab):
    d = {x.name: x for x in rayleigh_drops(tab, 40 * 24)}
    assert d["P1"].partner == "K1" and d["K2"].partner == "S2" and d["MKS2"].partner == "M2" and d["2N2"].partner == "MU2"


def test_foreman_table_names_only_known_comparisons():
    for n, (ref, df) in FOREMAN_COMPARISON.items():
        assert n in CANDIDATES and df > 0 and (ref == "Z0" or ref in CANDIDATES or ref == "3MK7")
