"""FIX-CONST (owner decision 2026-10-11): the extended candidate list (CANDIDATES + MA2, MB2 and shallow-water
compounds) for German and Norwegian records, kept only where window A says it is better; the choice is recorded
in the fit, and the Rayleigh replay walks the fit's own list.

The rule tests use a small synthetic table (V0+u 0, f 1) with the lists cut to a few constituents, so they run
anywhere. The last tests read the OTC TCD's fit dump ($TCD_TABLE) and skip without it."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import numpy as np
import pytest

from gesla import select_constituents as sc
from gesla.qc import Series
from gesla.short_accuracy import drops_of_fit
from gesla.tcd_table import TcdTable

SPEEDS = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356, "SA": 0.0410686,
          "MA2": 28.9430356, "MB2": 29.0251728}
BASE = ("M2", "S2", "K1", "O1")
EXT = BASE + ("MA2", "MB2")


def _ts(y, m=1, d=1):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp())


@pytest.fixture
def table():
    n = 2030 - 2000 + 1
    return TcdTable({"years": [2000, 2030], "tcd_sha256": "s" * 64, "aliases": {},
                     "constituents": {k: {"speed": v, "v0u": [0.0] * n, "f": [1.0] * n} for k, v in SPEEDS.items()}})


@pytest.fixture
def small_lists(monkeypatch):
    monkeypatch.setattr(sc, "CANDIDATE_LISTS", {"base": BASE, "extended": EXT})
    monkeypatch.setattr(sc, "EXTENDED_CANDIDATES", EXT)


def series(table, *, start=(2008, 1, 1), end=(2025, 8, 1), ma2=0.0, noise=0.01, seed=1, rid="r"):
    t = np.arange(_ts(*start), _ts(*end), 3600, dtype=np.int64)
    years, hours = sc.year_and_hours(t)
    amp = {"M2": 1.0, "S2": 0.3, "K1": 0.2, "O1": 0.15, "MA2": ma2}
    h = sum(a * np.cos(np.radians(SPEEDS[n] * hours - 40.0 * i)) for i, (n, a) in enumerate(amp.items()))
    h = h + np.random.default_rng(seed).normal(0.0, noise, len(t))
    return Series(rid, t, h, {})


# --- the lists ----------------------------------------------------------------------------------------------

def test_the_extended_list_is_the_45_then_ma2_mb2_and_the_compounds_without_mo3():
    assert sc.EXTENDED_CANDIDATES[:len(sc.CANDIDATES)] == sc.CANDIDATES      # Rayleigh conflicts go to the 45
    assert sc.EXTENDED_ADDITIONS[:2] == ("MA2", "MB2")
    decided = {"2MN6", "2MK6", "2SM6", "MSK6", "3MK7", "MO3", "SO3", "MK4", "SN4", "SK3", "2SK5", "MSN2"}
    assert set(sc.EXTENDED_ADDITIONS[2:]) == decided - set(sc.EXTENDED_LEFT_OUT) and set(sc.EXTENDED_LEFT_OUT) == {"MO3"}
    assert not set(sc.EXTENDED_ADDITIONS) & set(sc.CANDIDATES)
    assert sc.CANDIDATE_LISTS == {"base": sc.CANDIDATES, "extended": sc.EXTENDED_CANDIDATES}


@pytest.mark.parametrize("country,want", [("DEU", True), ("NOR", True), (" nor ", True), ("USA", False),
                                          ("NLD", False), (None, False), ("", False)])
def test_the_scope_is_german_and_norwegian_records(country, want):
    assert sc.in_fix_const_scope(country) is want


# --- the window-A rule -----------------------------------------------------------------------------------------

def test_out_of_scope_is_the_45_with_no_record(table, small_lists):
    assert sc.select_candidate_list(table, series(table, ma2=0.05), country="USA") == ("base", None)


def test_the_extended_list_is_chosen_where_window_a_rms_is_lower(table, small_lists):
    clist, rec = sc.select_candidate_list(table, series(table, ma2=0.05), country="DEU")
    assert clist == "extended" and rec["chosen"] == "extended"
    assert rec["rms_m"]["extended"] < rec["rms_m"]["base"]
    assert rec["window_a_hours"] == (_ts(2025, 8, 1) - _ts(2024, 8, 1)) // 3600
    assert rec["selection_last_s"] < sc.WINDOW_A[0]         # the selection copies never saw window A


def test_no_gain_on_window_a_keeps_the_45(table, small_lists):
    # no MA2 in the water: the noise rule drops MA2/MB2 from the extended copy, the RMS is equal, not lower
    clist, rec = sc.select_candidate_list(table, series(table, ma2=0.0, noise=0.05), country="NOR")
    assert clist == "base" and rec["chosen"] == "base"
    assert rec["rms_m"]["extended"] >= rec["rms_m"]["base"]


def test_window_a_data_after_the_series_is_never_used(table, small_lists):
    # the same water to 2025-08-01 and to 2026-07-01: window B (after A) changes nothing in the choice
    a = sc.select_candidate_list(table, series(table, ma2=0.05), country="DEU")
    b = sc.select_candidate_list(table, series(table, ma2=0.05, end=(2026, 7, 1)), country="DEU")
    assert a == b


def test_too_little_window_a_data_keeps_the_45(table, small_lists):
    clist, rec = sc.select_candidate_list(table, series(table, ma2=0.05, end=(2024, 10, 1)), country="DEU")
    assert clist == "base" and rec["reason"] == "window A has 1464 hourly samples"   # < 60% of 180 days
    clist, rec = sc.select_candidate_list(table, series(table, ma2=0.05, end=(2024, 1, 1)), country="DEU")
    assert clist == "base" and rec["reason"] == "window A has 0 hourly samples"


def test_a_selection_span_that_cannot_resolve_ma2_keeps_the_45(table, small_lists):
    # 300 days before window A: MA2/MB2 need a year (M2 -/+ SA): never a short record from the extended list
    clist, rec = sc.select_candidate_list(table, series(table, ma2=0.05, start=(2023, 10, 6)), country="DEU")
    assert clist == "base" and "selection span leaves" in rec["reason"] and "MA2" in rec["reason"]


# --- the fit records the choice; the replay walks it -----------------------------------------------------------

def test_a_base_fit_file_is_unchanged_and_an_extended_one_records_its_list(table, small_lists):
    s = series(table, ma2=0.05)
    base = sc.fit_record(table, s)
    assert "candidate_list" not in base.to_dict() and "candidate_selection" not in base.to_dict()
    assert base.to_dict() == sc.fit_record(table, s, candidate_list="base").to_dict()
    clist, rec = sc.select_candidate_list(table, s, country="DEU")
    ext = sc.fit_record(table, s, candidate_list=clist, selection=rec)
    d = ext.to_dict()
    assert d["candidate_list"] == "extended" and d["candidate_selection"] == rec
    assert "MA2" in d["constituents"] and "MA2" not in base.to_dict()["constituents"]
    assert d["rayleigh_kept"] == list(EXT) and base.to_dict()["rayleigh_kept"] == list(BASE)
    # a base choice inside the scope is recorded too (its selection numbers)
    _, rec0 = sc.select_candidate_list(table, series(table, ma2=0.0, noise=0.05), country="DEU")
    d0 = sc.fit_record(table, s, candidate_list="base", selection=rec0).to_dict()
    assert d0["candidate_list"] == "base" and d0["candidate_selection"] == rec0


def test_the_sampling_correction_keeps_the_candidate_list(table, small_lists):
    s = series(table, ma2=0.05)
    rf = sc.fit_record(table, s, candidate_list="extended", selection={"chosen": "extended"})
    scaled = rf.scaled({"M2": 1.011})
    assert scaled.candidate_list == "extended" and scaled.selection == {"chosen": "extended"}
    assert scaled.primary.fit.amplitude_m["M2"] == pytest.approx(rf.primary.fit.amplitude_m["M2"] * 1.011)


def test_the_rayleigh_replay_walks_the_fits_own_list(table, small_lists, monkeypatch):
    import gesla.short_accuracy as sa
    monkeypatch.setattr(sa, "CANDIDATE_LISTS", {"base": BASE, "extended": EXT})
    d = json.loads(sc.fit_record(table, series(table, ma2=0.05), candidate_list="extended").to_json())
    assert drops_of_fit(table, d) == []
    d.pop("candidate_list")                    # read as a 45-list fit, its kept list is not the rule's
    with pytest.raises(ValueError, match="Rayleigh replay"):
        drops_of_fit(table, d)
    d["candidate_list"] = "other"
    with pytest.raises(ValueError, match="candidate_list 'other'"):
        drops_of_fit(table, d)


# --- the OTC TCD's fit dump ------------------------------------------------------------------------------------

def _otc_dump() -> TcdTable:
    p = Path(os.environ.get("TCD_TABLE") or "/nonexistent")
    if not p.is_file():
        pytest.skip("needs $TCD_TABLE (the OTC TCD's tcd-1900-2100.json)")
    t = TcdTable.load(p)
    if "MA2" not in t:
        pytest.skip(f"{p} is not the OTC TCD's dump")
    return t


def test_every_extended_candidate_is_in_the_otc_tcd_and_mo3_is_2mk3s_speed():
    t = _otc_dump()
    assert sc.tcd_missing(t, sc.EXTENDED_CANDIDATES) == ()
    assert t.speed("MO3") == t.speed("2MK3")


def test_a_year_of_data_resolves_the_whole_extended_list():
    t = _otc_dump()
    assert sc.rayleigh_drops(t, 366 * 24, sc.EXTENDED_CANDIDATES) == []
    left = {d.name for d in sc.rayleigh_drops(t, 300 * 24, sc.EXTENDED_CANDIDATES)}
    assert {"MA2", "MB2"} <= left
