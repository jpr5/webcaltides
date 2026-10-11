"""R6: the real-record gate (stated values against real errors, per group and per constituent)."""
from __future__ import annotations

import json

import pytest

from gesla import short_accuracy_gate as g


def row(rec, groups, per):
    return {"record": rec, "comparator": "x", "kind": "same_gauge", "groups": ["all", *groups], "per": per}


def pc(dA, sa, dg=None, sg=None):
    return {"dA": dA, "stated_amp": sa, "dg": dg, "stated_phase": sg}


def test_a_cell_needs_min_records_and_the_band():
    rows = [row(f"r{i}", ["coastal"], {"M2": pc(0.02 if i % 2 else 0.005, 0.01, 2.0 if i % 2 else 0.5, 1.0)})
            for i in range(30)]
    res = g.gate(rows)
    assert res["groups"]["all"]["M2"]["amp_share_over"] == 0.5 and res["groups"]["all"]["M2"]["verdict"] == "pass"
    assert res["groups"]["gappy"]["pooled"]["verdict"] == "too few" and res["passed"]
    res = g.gate(rows[:19])
    assert res["groups"]["all"]["pooled"]["verdict"] == "too few" and res["passed"]


def test_opposite_errors_fail_per_constituent_though_the_pool_passes():
    rows = [row(f"r{i}", [], {"M2": pc(0.001, 0.01, 0.1, 1.0), "K1": pc(0.05, 0.01, 9.0, 1.0)}) for i in range(25)]
    res = g.gate(rows)
    assert res["groups"]["all"]["pooled"]["verdict"] == "pass"
    assert res["groups"]["all"]["M2"]["verdict"] == "FAIL" and res["groups"]["all"]["K1"]["verdict"] == "FAIL"
    assert not res["passed"] and "all/M2" in res["failed_cells"]


def test_no_phase_claim_is_not_counted():
    rows = [row(f"r{i}", [], {"M2": pc(0.02 if i % 2 else 0.005, 0.01)}) for i in range(20)]
    c = g.gate(rows)["groups"]["all"]["M2"]
    assert c["phase_pairs"] == 0 and c["phase_share_over"] is None and c["verdict"] == "pass"


def test_power_and_groups():
    assert g.power(0) is None and g.power(20) > 0.6 and g.power(100) > g.power(20)
    fit = {"first_s": 0, "last_s": 100 * 3600 - 3600}
    assert g.groups_of({"gauge_type": "Coastal", "data_hours": 70.0}, fit, {"M2": 0.003}) == \
        ["all", "coastal", "gappy", "microtidal", "<=30"]
    assert g.groups_of({"gauge_type": "River", "data_hours": 100 * 24.0},
                       {"first_s": 0, "last_s": 100 * 86400 - 3600}, {"M2": 1.0}) == ["all", "91-182"]


def test_noaa_one_year_only_from_other_years(tmp_path):
    def put(com):
        (tmp_path / "8517942.json").write_text(json.dumps({"HarmonicConstituents": [
            {"name": c, "amplitude": 1.0, "phase_GMT": 10.0, "comments": com} for c in ("M2", "K1")]}))
    first, last = 1356998400, 1388534399                 # 2013
    put("Mean of 3 NOS one year analyses (1974-1976).")
    assert g.noaa_one_year("x-8517942-usa-noaa", first, last, tmp_path) == {"M2": (1.0, 10.0), "K1": (1.0, 10.0)}
    put("Mean of 3 NOS one year analyses (2012-2014).")
    assert g.noaa_one_year("x-8517942-usa-noaa", first, last, tmp_path) is None
    put("29-day analysis 2013")
    assert g.noaa_one_year("x-8517942-usa-noaa", first, last, tmp_path) is None
    assert g.noaa_one_year("x-123-can-meds", first, last, tmp_path) is None


def test_stated_values_v1_one_pair_v2_per_constituent():
    v1 = {"targets": {"a": {"amp_uncertainty_m": 0.01, "phase_uncertainty_deg": 2.0}}}
    assert g.stated_of(v1, "a") == {c: (0.01, 2.0) for c in ("M2", "S2", "K1", "O1")}
    v2 = {"targets": {"a": {"by_constituent": {"M2": {"amp_m": 0.01, "phase_deg": None}}}, "b": {"error": "x"}}}
    assert g.stated_of(v2, "a") == {"M2": (0.01, None)} and g.stated_of(v2, "b") is None


def test_a_claim_limited_constituent_is_not_gated():
    rows = [row(f"r{i}", [], {"S2": pc(0.02, None, 2.0 if i % 2 else 0.5, 1.0)}) for i in range(20)]
    c = g.gate(rows)["groups"]["all"]["S2"]
    assert c["amp_pairs"] == 0 and c["phase_share_over"] == 0.5 and c["verdict"] == "pass"
