"""G04b: verdict classes, corrections and the pass rule (BP §2 steps 3-4), on synthetic records.

Every synthetic record is an analytic tide (fit.design over M2, S2, K1, O1, M4, M6), so a
shift, a 60-min boxcar mean or a wall-clock stamp is exact. The comparator is the same tide on
a 10-min grid. The real-record proof is test_positive_controls.py and the G04b proof script.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from gesla.qc import clean
from gesla.read_gesla import otc_work
from gesla.record import GaugeRecord
from gesla.tcd_table import TcdTable
from gesla.timebase_verdict import (BAND_MIN, Correction, UnexplainedRecord, boxcar_response, evaluate,
                                    isolated, month_key)

HOUR = 3600


def unix(y: int, m: int = 1, d: int = 1, hh: int = 0, mm: int = 0) -> int:
    return int(np.datetime64(f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:{mm:02d}", "s").astype(np.int64))


@pytest.fixture(scope="module")
def table():
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    if not dump.exists():
        pytest.skip(f"TCD dump not in the cache: {dump}")
    return TcdTable.load(dump)


NAMES = ("M2", "S2", "K1", "O1", "M4", "M6")
AMPS = np.array([1.0, 0.3, 0.2, 0.15, 0.05, 0.02])


def tide(table, t) -> np.ndarray:
    from gesla.fit import design
    d = design(table, NAMES, np.asarray(t, dtype=np.int64))
    return d[:, 0::2] @ AMPS + d[:, 1::2] @ (AMPS * 0.5)


def record(name: str, t, h, good=None) -> GaugeRecord:
    t = np.asarray(t, dtype=np.int64)
    h = np.asarray(h, dtype=np.float64)
    return GaugeRecord(record_id=name, source="gesla", source_version="test", contributor="X", originator=True,
                       licence_class="x", lat=0.0, lon=0.0, gauge_type="Coastal", sampling="instantaneous",
                       interval_min=60, declared_time_base=None, member_sha256="0" * 64, times_s=t, heights_m=h,
                       good=np.ones(len(t), dtype=bool) if good is None else np.asarray(good, dtype=bool))


START, END = unix(2015), unix(2017)


@pytest.fixture(scope="module")
def comp(table):
    t = np.arange(START - 2 * HOUR, END + 2 * HOUR, 600, dtype=np.int64)
    return t, tide(table, t)


HOURS = np.arange(START, END, HOUR, dtype=np.int64)


# --- pure functions -------------------------------------------------------------------------

def test_boxcar_response_m2():
    assert boxcar_response(28.9841042) == pytest.approx(0.9894, abs=0.0002)
    assert boxcar_response(28.9841042 * 2) == pytest.approx(0.958, abs=0.001)
    assert boxcar_response(0.0) == 1.0


def test_isolated_months():
    rows = [(2015, m) for m in range(1, 13)] + [(2016, m) for m in range(1, 13)]
    assert isolated([], rows)
    assert isolated([(2015, 3)], rows)
    assert isolated([(2015, 3), (2015, 5)], rows)                   # not adjacent
    assert not isolated([(2015, 3), (2015, 4)], rows)               # adjacent: a run, not isolated
    assert not isolated([(2015, 12), (2016, 1)], rows)              # adjacent across the year
    assert not isolated([(2015, 1), (2015, 3), (2015, 5)], rows)    # 3 of 24 > 10 %


def test_corrections():
    t = np.array([unix(2016, 1, 10), unix(2016, 2, 10), unix(2016, 3, 27, 1), unix(2016, 3, 27, 2, 30),
                  unix(2016, 3, 27, 3), unix(2016, 7, 1)], dtype=np.int64)
    h = np.arange(len(t), dtype=np.float64)
    tt, hh = Correction("utc_mean_start60")(t, h)
    assert np.array_equal(tt, t + 1800)
    tt, _ = Correction("utc_plus_n", n=1)(t, h)
    assert np.array_equal(tt, t - 3600)
    tt, hh = Correction("utc_instant", months_drop=((2016, 2),))(t, h)
    assert len(tt) == len(t) - 1 and unix(2016, 2, 10) not in tt
    # Berlin wall clock -> UTC: CET is -1 h, CEST -2 h; 02:30 on 2016-03-27 does not exist and is dropped.
    tt, hh = Correction("local_dst", zone="Europe/Berlin")(t, h)
    assert list(tt) == [unix(2016, 1, 9, 23), unix(2016, 2, 10) - 3600, unix(2016, 3, 27, 0),
                        unix(2016, 3, 27, 1), unix(2016, 6, 30, 22)]
    assert 3.0 not in hh
    # step: instant before 2016-03-01, +1 h after it.
    step = Correction("step", parts=((unix(2016, 3), Correction("utc_plus_n", n=1)),),
                      first=Correction("utc_instant"))
    tt, _ = step(t, h)
    assert list(tt[:2]) == list(t[:2]) and list(tt[2:]) == list(t[2:] - 3600)
    assert Correction("utc_mean_start60").amplitude_factor(28.9841042) == pytest.approx(1 / 0.9894, abs=0.0003)
    assert Correction("utc_instant").amplitude_factor(28.9841042) == 1.0


def test_month_key():
    assert month_key((2013, 7)) == "2013-07"


# --- classification on synthetic records ------------------------------------------------------

def run(table, comp, rec, **kw):
    return evaluate(rec, comp, comparator_id="comp", table=table, **kw)


def test_instant(table, comp):
    v = run(table, comp, record("inst", HOURS, tide(table, HOURS)))
    assert (v.verdict, v.passed) == ("utc_instant", True), v.reasons
    assert v.residual["max_abs"] <= 0.5 and v.residual["n_months"] == 24


def test_plus_one_hour(table, comp):
    v = run(table, comp, record("plus1", HOURS, tide(table, HOURS - 3600)))
    assert (v.verdict, v.passed) == ("utc_plus_1", True), v.reasons
    assert v.correction.n == 1


def test_mean_start60(table, comp):
    grid = np.arange(START - HOUR, END + 2 * HOUR, 60, dtype=np.int64)
    g = tide(table, grid)
    i0 = np.searchsorted(grid, HOURS)
    h = np.array([g[i:i + 60].mean() for i in i0])
    v = run(table, comp, record("mean60", HOURS, h))
    assert (v.verdict, v.passed) == ("utc_mean_start60", True), v.reasons
    ev = v.evidence
    assert ev["sampling"]["mean60_cm"] < min(ev["sampling"]["inst_t_cm"], ev["sampling"]["inst_t30_cm"])
    assert abs(ev["constituents"]["M2"]["amp_ratio"] - 0.9894) <= 0.01
    assert "M6" in ev["constituents"]                               # recorded, not tested


def test_local_dst(table, comp):
    # Wall-clock Europe/Berlin stamps: the stamp 02:00 on spring-forward day has no reading.
    import pandas as pd
    local = pd.DatetimeIndex(HOURS.astype("datetime64[s]")).tz_localize("UTC").tz_convert("Europe/Berlin")
    wall = local.tz_localize(None).values.astype("datetime64[s]").astype(np.int64)
    keep = np.r_[True, wall[1:] != wall[:-1]]                       # the repeated autumn hour: first kept
    v = run(table, comp, record("dst", wall[keep], tide(table, HOURS[keep])), zone="Europe/Berlin")
    assert (v.verdict, v.passed) == ("local_dst:Europe/Berlin", True), v.reasons
    assert v.evidence["spring_gap"]["hits"] == 2
    # Without a zone to test there is no local_dst class: unexplained.
    v = run(table, comp, record("dst", wall[keep], tide(table, HOURS[keep])))
    assert v.verdict == "unexplained"


def test_step(table, comp):
    cut = unix(2016, 3)
    h = np.where(HOURS < cut, tide(table, HOURS), tide(table, HOURS - 3600))
    v = run(table, comp, record("step", HOURS, h))
    assert (v.verdict, v.passed) == ("step:2016-03-01", True), v.reasons
    assert v.evidence["step"] == {"before": "utc_instant", "after": "utc_plus_1"}


def test_isolated_bad_month_is_dropped(table, comp):
    bad = (HOURS >= unix(2015, 6)) & (HOURS < unix(2015, 7))
    h = np.where(bad, tide(table, HOURS - 17 * 60), tide(table, HOURS))
    v = run(table, comp, record("one-bad", HOURS, h))
    assert (v.verdict, v.base, v.months_bad, v.passed) == ("months_bad:2015-06", "utc_instant", ["2015-06"], True)
    assert v.residual["n_months"] == 23


def test_adjacent_bad_months_are_unexplained(table, comp):
    bad = (HOURS >= unix(2015, 6)) & (HOURS < unix(2015, 8))
    h = np.where(bad, tide(table, HOURS - 17 * 60), tide(table, HOURS))
    v = run(table, comp, record("two-bad", HOURS, h))
    assert (v.verdict, v.passed) == ("unexplained", False)
    assert any("2015-06" in r and "2015-07" in r for r in v.reasons), v.reasons


def test_constant_17_min_is_unexplained(table, comp):
    v = run(table, comp, record("plus17", HOURS, tide(table, HOURS - 17 * 60)))
    assert (v.verdict, v.passed, v.correction.usable) == ("unexplained", False, False)


def test_no_overlap_is_no_comparator(table, comp):
    t = np.arange(unix(2019), unix(2020), HOUR, dtype=np.int64)
    v = run(table, comp, record("late", t, tide(table, t)))
    assert (v.verdict, v.passed) == ("no_comparator", False)


def test_span_limits_the_audit(table, comp):
    bad = (HOURS >= unix(2015, 6)) & (HOURS < unix(2015, 8))
    h = np.where(bad, tide(table, HOURS - 17 * 60), tide(table, HOURS))
    v = run(table, comp, record("two-bad", HOURS, h), span=(unix(2016), END))
    assert (v.verdict, v.passed) == ("utc_instant", True)
    assert v.residual["n_months"] == 12


def test_pass_rule_band_is_three_minutes():
    assert BAND_MIN == 3.0


# --- qc applies the correction ----------------------------------------------------------------

def test_clean_applies_and_refuses(table, comp):
    rec = record("plus1", HOURS, tide(table, HOURS - 3600))
    v = run(table, comp, rec)
    s = clean(rec, v.correction)
    assert s.times_s[0] == HOURS[0] - 3600 and s.stats["timebase"] == "utc_plus_1"
    u = run(table, comp, record("plus17", HOURS, tide(table, HOURS - 17 * 60)))
    with pytest.raises(UnexplainedRecord):
        clean(rec, u.correction)


def test_swap_flag_breaks_the_classifier(table, comp, monkeypatch):
    monkeypatch.setenv("OTC_TB_TEST_SWAP_CLASSES", "utc_instant,utc_plus_1")
    v = run(table, comp, record("inst", HOURS, tide(table, HOURS)))
    assert v.verdict == "utc_plus_1"


def _mean60(table, hours):
    grid = np.arange(hours[0] - HOUR, hours[-1] + 2 * HOUR, 60, dtype=np.int64)
    g = tide(table, grid)
    return np.array([g[i:i + 60].mean() for i in np.searchsorted(grid, hours)])


def test_comparator_that_is_a_corrected_mean_sibling(table):
    # The comparator is a utc_mean_start60 sibling after its correction (+30 min): its amplitudes
    # still carry the boxcar response, so an instantaneous record's M2 ratio is 1 / 0.989.
    sib = (HOURS + 1800, _mean60(table, HOURS))
    rec = record("inst", HOURS, tide(table, HOURS))
    v = evaluate(rec, sib, comparator_id="sib~corrected", table=table)
    assert v.verdict == "unexplained" and any("M2 amplitude ratio" in r for r in v.reasons), v.reasons
    v = evaluate(rec, sib, comparator_id="sib~corrected", table=table,
                 comparator_m2_response=boxcar_response(28.9841042))
    assert (v.verdict, v.passed) == ("utc_instant", True), v.reasons


def test_hourly_comparator_cannot_show_the_hour_mean(table):
    hourly = (HOURS, tide(table, HOURS))
    v = evaluate(record("mean60", HOURS, _mean60(table, HOURS)), hourly, comparator_id="hourly", table=table)
    assert v.verdict == "unexplained"
    assert any("sampling test not possible" in r for r in v.reasons), v.reasons
    assert v.residual == {}                       # no class was applied: no residual to report
