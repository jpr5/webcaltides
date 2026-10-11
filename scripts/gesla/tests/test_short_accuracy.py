"""SR-ACC: the Rayleigh drop list with partners, and the short-record expected_accuracy calibration.

Synthetic tests (always run): the drop walk, the format 1.0 items, the regime pool (R1), the relative
amplitude error (R2), the worst of the four constituents (R3), the target's mask copied onto other years
(R4), the phase floor by the target's own amplitudes and the microtidal case (R5), the implausible-fit
refusal (R8), the hold-out count, the calibration file, and the v3 fields' refusals (never a crash).
Real-TCD tests (skipped without $OTC_WORK/tcd_table.json): rayleigh_drops against the old walk.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import numpy as np
import pytest
from test_poc_reproduction import T2019, fake_table, synth
from test_sa_short_records import red_noise

from gesla import short_accuracy as sa
from gesla.qc import Series
from gesla.read_gesla import otc_work
from gesla.select_constituents import (CANDIDATES, CONFLICT, PERIOD_EXCEEDS_RECORD, RayleighDrop, fit_record,
                                       rayleigh_drops, rayleigh_select)
from gesla.tcd_table import TcdTable

HOUR = 3600
DAY_H = 24.0
TRUTH = {"M2": (1.0, 40.0), "S2": (0.3, 70.0), "K1": (0.4, 200.0), "O1": (0.3, 150.0), "N2": (0.2, 20.0)}


def old_rayleigh_select(table, span_hours, candidates=CANDIDATES, r=1.0):
    """rayleigh_select as it was before SR-ACC (c6f2fac), verbatim."""
    kept, speeds = [], []
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


def three_speed_table() -> TcdTable:
    sp = {"A1": 10.0, "B1": 10.6, "C1": 10.45}
    return TcdTable({"years": [2019, 2021], "tcd_sha256": "0" * 64, "aliases": {},
                     "constituents": {k: {"speed": v, "v0u": [0.0] * 3, "f": [1.0] * 3} for k, v in sp.items()}})


# --- the drop walk ------------------------------------------------------------------------------------

def test_drops_name_the_partner_and_the_period_drops():
    tab = fake_table()
    t = 40 * DAY_H
    cands = ("M2", "S2", "K1", "O1", "N2", "P1", "SA")
    assert rayleigh_select(tab, t, cands) == ["M2", "S2", "K1", "O1", "N2"]
    d = rayleigh_drops(tab, t, cands)
    assert d[0] == RayleighDrop("P1", CONFLICT, "K1", None)
    assert d[1].name == "SA" and d[1].kind == PERIOD_EXCEEDS_RECORD and d[1].partner is None
    assert d[1].needs_days == pytest.approx(360 / 0.0410686 / 24)
    assert rayleigh_drops(tab, 400 * DAY_H, cands) == []


def test_the_partner_is_the_first_kept_in_priority_order_from_30_days_and_the_nearest_below():
    """C1 is within 360/T of both A1 (0.45 deg/h away) and B1 (0.15 away)."""
    tab = three_speed_table()
    assert rayleigh_select(tab, 750.0, ("A1", "B1", "C1")) == ["A1", "B1"]
    assert rayleigh_drops(tab, 750.0, ("A1", "B1", "C1")) == [RayleighDrop("C1", CONFLICT, "A1", None)]
    assert rayleigh_drops(tab, 700.0, ("A1", "B1", "C1")) == [RayleighDrop("C1", CONFLICT, "B1", None)]


def test_kept_and_dropped_partition_the_candidates_the_tcd_has():
    tab = fake_table()
    for t in (300.0, 1000.0, 4400.0, 9000.0):
        kept = rayleigh_select(tab, t)
        dropped = [d.name for d in rayleigh_drops(tab, t)]
        assert not set(kept) & set(dropped)
        assert sorted(kept + dropped) == sorted(tab.name(n) for n in CANDIDATES if n in tab)


def test_rayleigh_select_is_unchanged_on_synthetic_speeds():
    tab = fake_table()
    for t in np.linspace(10, 20000, 400):
        assert rayleigh_select(tab, float(t)) == old_rayleigh_select(tab, float(t))


REAL_TCD = otc_work() / "tcd_table.json"
real = pytest.mark.skipif(not REAL_TCD.exists(), reason=f"no real TCD dump at {REAL_TCD}")


@real
def test_real_tcd_rayleigh_select_is_byte_identical_to_the_old_walk_from_31_85_days():
    tab = TcdTable.load(REAL_TCD)
    for t in list(np.linspace(31.85 * 24, 20000, 2000)) + [d * 24 for d in (40, 90, 182.6, 365.25, 366, 730)]:
        assert rayleigh_select(tab, float(t)) == old_rayleigh_select(tab, float(t)), t


@real
def test_real_tcd_drop_sets_at_the_plan_lengths():
    tab = TcdTable.load(REAL_TCD)
    have = [tab.name(n) for n in CANDIDATES if n in tab]
    for days in (15.3, 30, 40, 90, 182.6, 365.25, 366, 730):
        kept, drops = rayleigh_select(tab, days * 24), rayleigh_drops(tab, days * 24)
        assert sorted(kept + [d.name for d in drops]) == sorted(have)
        assert all((d.partner in kept) == (d.kind == CONFLICT) for d in drops)
    at40 = {d.name: d for d in rayleigh_drops(tab, 40 * 24)}
    assert at40["P1"].partner == "K1" and at40["K2"].partner == "S2" and at40["MKS2"].partner == "M2"
    assert at40["SA"].kind == at40["SSA"].kind == PERIOD_EXCEEDS_RECORD
    assert rayleigh_drops(tab, 366 * 24) == []


# --- format 1.0 items --------------------------------------------------------------------------------

def test_dropped_constituents_maps_kinds_and_keeps_a_noise_dropped_partner():
    drops = [RayleighDrop("P1", CONFLICT, "K1", None).to_dict(),
             RayleighDrop("SIG1", CONFLICT, "2Q1", None).to_dict(),
             RayleighDrop("SA", PERIOD_EXCEEDS_RECORD, None, 365.2422).to_dict()]
    out = sa.dropped_constituents(drops, ["2Q1"])
    assert [o["name"] for o in out] == ["2Q1", "P1", "SA", "SIG1"]
    by = {o["name"]: o for o in out}
    assert by["P1"] == {"name": "P1", "dropped_reason": "rayleigh", "not_separable_from": "K1"}
    assert by["SIG1"]["not_separable_from"] == "2Q1" and by["2Q1"]["dropped_reason"] == "noise"   # D3
    assert by["SA"] == {"name": "SA", "dropped_reason": "other",
                        "detail": "period longer than the record (needs 365.2 days)"}              # D2
    with pytest.raises(ValueError, match="twice"):
        sa.dropped_constituents(drops, ["P1"])


def test_short_record_reason_is_one_line_main_pairs_first():
    drops = [RayleighDrop("MKS2", CONFLICT, "M2", None).to_dict(), RayleighDrop("P1", CONFLICT, "K1", None).to_dict(),
             RayleighDrop("K2", CONFLICT, "S2", None).to_dict(),
             RayleighDrop("SA", PERIOD_EXCEEDS_RECORD, None, 365.2).to_dict()]
    s = sa.short_record_reason(40 * 24, drops)
    assert s == ("40.0 days of good data: cannot separate MKS2 from M2, K2 from S2, P1 from K1; "
                 "SA has a period longer than the record")
    assert "\n" not in s and len(sa.short_record_reason(40 * 24, drops * 100, limit=80)) <= 80


@pytest.fixture
def all_claimed(monkeypatch):
    """The metric's mechanics, with every main constituent claimed (UNCLAIMED empty)."""
    monkeypatch.setattr(sa, "UNCLAIMED", ())


# --- R1: regime and co-sites ------------------------------------------------------------------------

def test_form_class_and_reference():
    assert sa.form_class({"M2": 1.0, "S2": 0.3, "K1": 0.1, "O1": 0.1}) == "semidiurnal"
    assert sa.form_class({"M2": 0.3, "S2": 0.1, "K1": 0.3, "O1": 0.2}) == "mixed"
    assert sa.form_class({"M2": 0.02, "K1": 0.3, "O1": 0.2}) == "diurnal"
    assert sa.reference({"M2": 0.4, "K1": 0.9}) == ("M2", 0.4)
    assert sa.reference({"K1": 0.9, "O1": 0.5}) == ("K1", 0.9)


def test_same_regime_needs_the_class_and_the_amplitude_ratio():
    t = {"M2": 1.0, "S2": 0.3, "K1": 0.1, "O1": 0.1}
    assert sa.same_regime(t, {"M2": 0.5, "S2": 0.15, "K1": 0.05, "O1": 0.05})
    assert sa.same_regime(t, {"M2": 2.0, "S2": 0.6, "K1": 0.2, "O1": 0.2})
    assert not sa.same_regime(t, {"M2": 0.49, "S2": 0.15, "K1": 0.05, "O1": 0.05})
    assert not sa.same_regime(t, {"M2": 2.1, "S2": 0.6, "K1": 0.2, "O1": 0.2})
    assert not sa.same_regime(t, {"M2": 1.0, "S2": 0.3, "K1": 0.5, "O1": 0.5})        # mixed
    churchill = {"M2": 1.5, "S2": 0.4, "K1": 0.06, "O1": 0.05}
    lake = {"M2": 0.01, "S2": 0.003, "K1": 0.002, "O1": 0.001}
    assert not sa.same_regime(churchill, lake)


def test_cosite_clusters_chain_within_2_km():
    ids = ["a", "b", "c", "d"]
    lat = [0.0, 0.0, 0.0, 0.0]
    lon = [0.0, 0.015, 0.03, 1.0]                        # a-b 1.7 km, b-c 1.7 km, a-c 3.3 km
    c = sa.cosite_clusters(ids, lat, lon)
    assert c["a"] == c["b"] == c["c"] == "a" and c["d"] == "d"


# --- R4: the target's mask on another year ----------------------------------------------------------

def _hours(y, m, d, n, step=1):
    t0 = np.datetime64(f"{y:04d}-{m:02d}-{d:02d}T00", "h").astype(np.int64)
    return t0 + np.arange(0, n * step, step, dtype=np.int64)


def test_shift_years_keeps_calendar_dates_and_drops_a_missing_29_february():
    h = _hours(2020, 2, 28, 72)                          # 28 Feb, 29 Feb, 1 Mar 2020
    s = sa.shift_years(h, 1)
    days = np.unique(s.astype("datetime64[h]").astype("datetime64[D]")).astype(str).tolist()
    assert days == ["2021-02-28", "2021-03-01"] and len(s) == 48
    assert (sa.shift_years(sa.shift_years(_hours(2019, 6, 1, 100), 3), -3) == _hours(2019, 6, 1, 100)).all()


def test_cut_shifts_copy_the_mask_and_need_the_cover():
    station = np.concatenate([_hours(2010, 1, 1, 24 * 365 * 6)])
    station = np.delete(station, np.arange(24 * 365 * 2 + 3000, 24 * 365 * 2 + 3600))   # a gap in 2012
    target = np.concatenate([_hours(2020, 6, 1, 24 * 10), _hours(2020, 8, 1, 24 * 10)])   # gappy summer
    cuts = sa.cut_shifts(station, target, seed=5)
    same = lambda a, b: [(k, h.tolist()) for k, h in a] == [(k, h.tolist()) for k, h in b]  # noqa: E731
    assert same(cuts, sa.cut_shifts(station, target, seed=5)) and len(cuts) == sa.WINDOWS
    for k, hours in cuts:
        shifted = sa.shift_years(target, k)
        assert set(hours.tolist()) <= set(shifted.tolist()) and len(hours) >= 0.9 * len(target)
        months = np.unique(hours.astype("datetime64[h]").astype("datetime64[M]").astype(str))
        assert [m[5:] for m in months] == ["06", "08"]
    assert not same(cuts, sa.cut_shifts(station, target, seed=6))
    assert sa.cut_shifts(station[::3], target, seed=5) == []                             # 33 % cover


# --- R2, R3, R5: the metric and the stated value -----------------------------------------------------

def test_cut_errors_relative_lost_and_the_5_mm_floor():
    full = {"M2": (1.0, 10.0), "S2": (0.004, 50.0), "K1": (0.2, 350.0)}
    e = sa.cut_errors(full, {"M2": (1.02, 12.0), "S2": (0.005, 80.0)})
    assert e["M2"]["rel"] == pytest.approx(0.02) and e["M2"]["dg"] == pytest.approx(2.0)
    assert e["S2"]["rel"] is None and e["S2"]["dg"] is None and e["S2"]["dg_all"] == pytest.approx(30.0)
    assert e["K1"] == {"dA": 0.2, "rel": 1.0, "dg": 90.0, "dg_all": 90.0, "lost": True}
    assert "O1" not in e


def _st(rel, dA, dg, dg_all=None):
    return {"rel": rel, "dA": dA, "dg": dg, "dg_all": dg if dg_all is None else dg_all}


def test_amplitude_is_relative_times_the_target_amplitude_with_a_floor(all_claimed):
    stations = [{"M2": _st(0.01, 0.001, 1.0), "K1": _st(0.10, 0.001, 8.0)},
                {"M2": _st(0.03, 0.001, 3.0), "K1": _st(0.20, 0.001, 12.0)}]
    acc = sa.target_accuracy({"M2": 2.0, "K1": 0.01}, stations)
    assert acc["by_constituent"]["M2"]["amp_m"] == pytest.approx(0.04)          # 0.02 x 2 m
    assert acc["by_constituent"]["K1"]["amp_m"] == sa.AMP_FLOOR_M                # 0.15 x 1 cm under the floor
    assert acc["amp_uncertainty_m"] == pytest.approx(0.04) and acc["worst"]["amp"] == "M2"


def test_the_published_pair_is_the_worst_constituent_not_a_pooled_figure(all_claimed):
    stations = [{"M2": _st(0.01, 0, 1.0), "S2": _st(0.05, 0, 6.0), "K1": _st(0.10, 0, 12.0), "O1": _st(0.02, 0, 2.0)}]
    acc = sa.target_accuracy({"M2": 1.0, "S2": 0.3, "K1": 0.3, "O1": 0.2}, stations)
    assert acc["phase_uncertainty_deg"] == 12.0 and acc["worst"]["phase"] == "K1"
    assert acc["amp_uncertainty_m"] == pytest.approx(0.03) and acc["worst"]["amp"] == "K1"
    assert acc["phase_uncertainty_deg"] != statistics.mean([1.0, 6.0, 12.0, 2.0])


def test_phase_claims_follow_the_targets_own_amplitudes(all_claimed):
    stations = [{"M2": _st(0.01, 0, 1.0), "K1": _st(0.1, 0, 40.0)}]
    acc = sa.target_accuracy({"M2": 0.5, "K1": 0.004}, stations)               # K1 under 5 mm in the target
    assert acc["by_constituent"]["K1"]["phase_deg"] is None and acc["phase_uncertainty_deg"] == 1.0
    assert acc["microtidal"] is False


def test_a_microtidal_target_takes_the_no_floor_phase_with_the_lost_penalty(all_claimed):
    stations = [{"M2": _st(0.5, 0.002, None, 30.0), "K1": _st(1.0, 0.002, None, 90.0)},
                {"M2": _st(0.4, 0.002, None, 20.0), "K1": _st(1.0, 0.002, None, 70.0)}]
    acc = sa.target_accuracy({"M2": 0.003, "K1": 0.002}, stations)
    assert acc["microtidal"] is True and acc["phase_uncertainty_deg"] == 80.0 and acc["worst"]["phase"] == "K1"


def test_station_value_and_finish_target_need_15_usable_stations(all_claimed):
    good = [{"errors": {"M2": {"dA": 0.01, "rel": 0.01, "dg": 1.0, "dg_all": 1.0, "lost": False}}},
            {"errors": {"M2": {"dA": 0.03, "rel": 0.03, "dg": 3.0, "dg_all": 3.0, "lost": False}}},
            {"errors": None, "error": "FitError"}]
    assert sa.station_value(good)["M2"]["rel"] == pytest.approx(0.02)
    assert sa.station_value([{"errors": None}]) is None
    pool = [(f"s{i}", float(i)) for i in range(20)]
    cuts = {f"s{i}": {"cuts": {"t": good if i != 3 else []}} for i in range(20)}
    acc = sa.finish_target(pool, cuts, {"M2": 1.0}, "t")
    assert acc["calibration_records"] == 15 and "s3" not in [s for s, _ in acc["pool"]] and acc["max_km"] == 15.0
    short = sa.finish_target(pool[:14], cuts, {"M2": 1.0}, "t")
    assert short["error"].startswith("pool_too_small")


def test_a_pool_widens_until_15_usable_stations(all_claimed):
    ok = [{"errors": {"M2": {"dA": 0.01, "rel": 0.01, "dg": 1.0, "dg_all": 1.0, "lost": False}}}]
    pool = [(f"s{i:02d}", float(i)) for i in range(50)]
    cuts = {f"s{i:02d}": {"cuts": {"t": ok if i % 2 else []}} for i in range(50)}   # every other station uncut
    reach = {"t": 20, "u": 20}
    pools = {"t": pool, "u": pool[:20]}
    cuts.update({f"s{i:02d}": {"cuts": {"t": ok if i % 2 else [], "u": ok}} for i in range(20)})
    more = sa.widen(pools, reach, cuts)
    assert set(more) == {f"s{i:02d}" for i in range(20, 40)} and all(v == {"t"} for v in more.values())
    assert reach == {"t": 40, "u": 20}
    assert sa.widen(pools, reach, cuts) == {}                     # 20 usable of 40: enough
    assert sa.finish_target(pool[:reach["t"]], cuts, {"M2": 1.0}, "t")["calibration_records"] == 15
    short = {"t": pool[:30]}
    r2 = {"t": 20}
    assert set(sa.widen(short, r2, cuts)) == {f"s{i:02d}" for i in range(20, 30)} and r2 == {"t": 30}
    assert sa.widen(short, r2, cuts) == {}                        # nothing left to widen to: the target is listed


# --- R8 -----------------------------------------------------------------------------------------------

def test_implausible_fit_is_refused_by_size_and_by_neighbours():
    recs = [{"record": f"L{i}", "lat": 45.0 + i * 0.1, "lon": -64.0, "amps": {"M2": 1.0 + i * 0.02}} for i in range(30)]
    recs += [{"record": "five_islands", "lat": 45.4, "lon": -64.05, "amps": {"M2": 17.67}},
             {"record": "big_ok", "lat": 45.5, "lon": -64.1, "amps": {"M2": 3.5}},
             {"record": "copy", "lat": 46.0, "lon": -64.0, "amps": {"M2": 9.0}}]
    longs = {r["record"] for r in recs if r["record"].startswith("L")} | {"copy"}
    ids = [r["record"] for r in recs]
    cl = sa.cosite_clusters(ids, [r["lat"] for r in recs], [r["lon"] for r in recs])
    out = sa.implausible(recs, longs, cl)
    assert out["five_islands"]["reason"] == "implausible_fit" and "over 10 m" in out["five_islands"]["detail"]
    assert "big_ok" not in out                       # 3.5 m < 3 x the largest of its 25 nearest (about 1.4 m)
    assert "copy" in out and "nearest long" in out["copy"]["detail"]   # 9 m; its co-site L10 does not count
    assert not any(r.startswith("L") for r in out)


# --- hold-out -------------------------------------------------------------------------------------------

def test_holdout_check_counts_over_under_and_skips(all_claimed):
    def cut(dA, dg):
        return {"errors": {"M2": {"dA": dA, "rel": dA, "dg": dg, "dg_all": dg, "lost": False}}}
    st = [cut(0.02, 2.0)]                              # every pool station states 0.02 m / 2 deg for M2 = 1 m
    pool = [(f"s{i}", float(i)) for i in range(15)]
    cuts = {f"s{i}": {"cuts": {"m": st}} for i in range(15)}
    cuts["h"] = {"cuts": {"m": [cut(0.03, 1.0), cut(0.01, 3.0), cut(0.05, 5.0)]}}
    plan = {"holdout": {"h": {"mask_of": "m", "pool": pool}, "h2": {"mask_of": "m", "pool": pool[:3]}}}
    hc = sa.holdout_check(plan, cuts, {"h": {"M2": 1.0}, "h2": {"M2": 1.0}}, {})
    assert hc["amp"]["M2"] == {"over": 2, "n": 3, "share": 0.6667}
    assert hc["phase"]["M2"] == {"over": 2, "n": 3, "share": 0.6667}
    assert list(hc["skipped"]) == ["h2"] and hc["holdouts"] == 2


# --- calibration file and the v3 fields -------------------------------------------------------------

def _fit(tab, days, amps=None):
    t = days * 24
    return {"rayleigh_hours": round(t, 3), "rayleigh_kept": rayleigh_select(tab, t), "snr_dropped": [],
            "constituents": {c: {"amp_m": a, "phase_deg": 10.0} for c, a in (amps or {"M2": 1.0}).items()}}


def _cal(tab, fit):
    return {"cal_version": sa.CAL_VERSION, "refused": {"bad-long": {"reason": "implausible_fit", "detail": "M2 17 m"}},
            "targets": {"short-1": {"good_hours": 972.0, "cut_to_days": 40.5, "calibration_records": 15,
                                    "amp_uncertainty_m": 0.012, "phase_uncertainty_deg": 1.5, "microtidal": False,
                                    "fit_sha256": sa.fit_sha256(fit),
                                    "by_constituent": {"M2": {"amp_m": 0.012, "phase_deg": 1.5, "basis": "relative"}}},
                        "short-2": {"good_hours": 972.0, "error": "pool_too_small: 9 stations"}}}


def test_calibration_file_round_trip_checks_every_file(tmp_path):
    tab = fake_table()
    cal = _cal(tab, _fit(tab, 40.5))
    (tmp_path / sa.CUTS_FILE).write_text("{}\n")
    sa.write_calibration(cal, tmp_path)
    assert sa.read_calibration(tmp_path)["targets"] == cal["targets"]
    (tmp_path / sa.CUTS_FILE).write_text("{} \n")
    with pytest.raises(ValueError, match="sha256"):
        sa.read_calibration(tmp_path)
    sa.write_calibration(dict(cal, cal_version=99), tmp_path)
    with pytest.raises(ValueError, match="cal_version"):
        sa.read_calibration(tmp_path)


def test_v3_fields_refuse_and_never_raise():
    tab = fake_table()
    f = _fit(tab, 40.5)
    cal = _cal(tab, f)
    v = sa.fit_document_v3_fields(tab, f, record_id="short-1", good_hours=972.04, calibration=cal)
    assert v["refusal"] is None and v["expected_accuracy"] == {"cut_to_days": 40.5, "calibration_records": 15,
                                                               "amp_uncertainty_m": 0.012, "phase_uncertainty_deg": 1.5}
    assert v["accuracy_by_constituent"] == {"M2": {"amp_m": 0.012, "phase_deg": 1.5}} and v["microtidal"] is False
    for rid, gh, c, why in (("short-1", 972.0, None, "no calibration"), ("other", 972.0, cal, "not a target"),
                            ("short-2", 972.0, cal, "pool_too_small"), ("short-1", 990.0, cal, "calibrated at")):
        v = sa.fit_document_v3_fields(tab, f, record_id=rid, good_hours=gh, calibration=c)
        assert v["refusal"]["reason"] == "short_record_uncalibrated" and why in v["refusal"]["detail"]
        assert v["expected_accuracy"] is None
    f2 = dict(f, constituents={"M2": {"amp_m": 1.1, "phase_deg": 10.0}})
    assert "differs" in sa.fit_document_v3_fields(tab, f2, record_id="short-1", good_hours=972.0, calibration=cal)["refusal"]["detail"]
    bad = dict(f, rayleigh_kept=f["rayleigh_kept"] + ["P1"])
    assert "Rayleigh selection" in sa.fit_document_v3_fields(tab, bad, record_id="short-1", good_hours=972.0, calibration=cal)["refusal"]["detail"]
    nohours = {k: v for k, v in f.items() if k != "rayleigh_hours"}
    assert "Rayleigh selection" in sa.fit_document_v3_fields(tab, nohours, record_id="short-1", good_hours=972.0, calibration=cal)["refusal"]["detail"]
    long_ = sa.fit_document_v3_fields(tab, _fit(tab, 400), record_id="bad-long", good_hours=9600.0, calibration=cal)
    assert long_["refusal"]["reason"] == "implausible_fit" and long_["rayleigh_drops"] == []
    ok = sa.fit_document_v3_fields(tab, _fit(tab, 400), record_id="long", good_hours=9600.0, calibration=None)
    assert ok == {"rayleigh_drops": [], "expected_accuracy": None, "accuracy_by_constituent": None,
                  "microtidal": None, "accuracy_unclaimed": None, "accuracy_notes": None, "refusal": None}


def test_m2_is_published_without_a_stated_accuracy():
    """Owner decision on the R6 gate: M2 leaves the per-constituent values and the worst-of-four."""
    assert sa.UNCLAIMED == ("M2",)
    stations = [{"M2": _st(0.30, 0, 20.0), "S2": _st(0.05, 0, 6.0), "K1": _st(0.10, 0, 12.0), "O1": _st(0.02, 0, 2.0)}]
    acc = sa.target_accuracy({"M2": 1.0, "S2": 0.3, "K1": 0.3, "O1": 0.2}, stations)
    assert "M2" not in acc["by_constituent"] and acc["unclaimed"]["M2"]["amp_m"] == pytest.approx(0.3)
    assert acc["amp_uncertainty_m"] == pytest.approx(0.03) and acc["phase_uncertainty_deg"] == 12.0
    assert sa.target_accuracy({"M2": 1.0}, [{"M2": _st(0.01, 0, 1.0)}]) == \
        {"error": "no main constituent of the target has a calibrated amplitude"}
    r = sa.short_record_reason(40 * 24, [RayleighDrop("P1", CONFLICT, "K1", None).to_dict()], notes=[sa.UNCLAIMED_REASON])
    assert r == ("no accuracy is stated for M2: the real-record check did not support one; 40.0 days of good data: "
                 "cannot separate P1 from K1")


def test_flag_values_are_numbers_or_null():
    v = sa.flag_values({"M2": {"amp_m": 0.01, "phase_deg": 1.0}, "K1": {"amp_m": 0.002, "phase_deg": None}})
    assert v == {"M2_amp_uncertainty_m": 0.01, "M2_phase_uncertainty_deg": 1.0,
                 "K1_amp_uncertainty_m": 0.002, "K1_phase_uncertainty_deg": None}


# --- a synthetic station: longer and fuller masks give smaller errors ------------------------------

def test_cut_errors_fall_with_the_mask_length_on_a_synthetic_record():
    from gesla.select_constituents import analyse
    tab = fake_table()
    t = T2019 + np.arange(int(2.9 * 8766)) * HOUR
    h = synth(tab, TRUTH, t) + red_noise(len(t), seed=3, sd=0.08)
    s = Series("synthetic-1", t.astype(np.int64), h.astype(np.float64), {})
    full = analyse(tab, s).consts()
    sh = sa.series_hours(s)
    med = {}
    for days in (20, 60, 200):
        mask = _hours(2019, 3, 1, 24 * days)
        rel = []
        for k, hours in sa.cut_shifts(sh, mask, seed=1):
            e = sa.cut_errors(full, analyse(tab, sa.subset_hours(s, hours)).consts())
            rel.append(statistics.mean(e[c]["rel"] for c in ("M2", "S2", "K1", "O1")))
        med[days] = statistics.median(rel)
    assert med[20] > med[60] > med[200]


# --- fix-r2 ---------------------------------------------------------------------------------------------

def _halves_fit(full, h1, h2):
    def cons(d):
        return {c: {"amp_m": a, "phase_deg": g} for c, (a, g) in d.items()}
    return {"constituents": {c: {"amp_m": a, "phase_deg": g, "u_m": 0.001} for c, (a, g) in full.items()},
            "halves": {"half1": None if h1 is None else {"constituents": cons(h1)},
                       "half2": None if h2 is None else {"constituents": cons(h2)}}}


def test_inconsistent_halves_catch_a_break_and_spare_a_sound_record():
    good = _halves_fit({"M2": (1.0, 10.0), "S2": (0.3, 40.0), "K1": (0.2, 200.0)},
                       {"M2": (1.02, 11.0), "S2": (0.25, 30.0), "K1": (0.15, 190.0)},
                       {"M2": (0.98, 9.0), "S2": (0.35, 50.0), "K1": (0.25, 210.0)})
    assert sa.inconsistent_halves(good) is None
    zekes = _halves_fit({"M2": (0.309, 5.8), "S2": (0.045, 356.0)}, {"M2": (0.572, 25.9), "S2": (0.06, 10.0)},
                        {"M2": (0.185, 300.0), "S2": (0.03, 200.0)})
    assert "M2 differ" in sa.inconsistent_halves(zekes)
    lost = _halves_fit({"M2": (0.356, 10.0), "S2": (0.16, 40.0)}, {"M2": (0.673, 10.0), "S2": (0.2, 40.0)},
                       {"S2": (0.12, 40.0)})
    assert "M2 (0.356 m) is missing from the second half" in sa.inconsistent_halves(lost)
    massena = _halves_fit({"K1": (0.068, 100.0), "S2": (0.01, 0.0)}, {"K1": (0.015, 100.0)}, {"K1": (0.192, 140.0)})
    assert "K1 differ" in sa.inconsistent_halves(massena)
    small_k1 = _halves_fit({"M2": (0.99, 10.0), "K1": (0.067, 100.0)}, {"M2": (0.99, 10.0), "K1": (0.04, 60.0)},
                           {"M2": (0.99, 11.0), "K1": (0.09, 160.0)})
    assert sa.inconsistent_halves(small_k1) is None          # K1 under 0.6 x the dominant M2: P1 beat, not a break
    assert sa.inconsistent_halves(_halves_fit({"M2": (1.0, 0.0)}, None, {"M2": (1.0, 0.0)})) is None


def test_own_u_needs_five_times_the_stated_value_and_more_than_the_amplitude():
    f = {"constituents": {"K1": {"amp_m": 0.05, "phase_deg": 0.0, "u_m": 0.06}, "S2": {"amp_m": 0.2, "phase_deg": 0.0, "u_m": 0.06}}}
    assert "K1" in sa.own_u_exceeds(f, {"K1": {"amp_m": 0.01}})
    assert sa.own_u_exceeds(f, {"S2": {"amp_m": 0.01}}) is None   # 6x the stated value but well under its 0.2 m


def _t(**over):
    t = {"measured": {"M2": {"amp_m": 0.03, "phase_deg": 1.0}, "S2": {"amp_m": 0.01, "phase_deg": 6.0},
                      "K1": {"amp_m": 0.02, "phase_deg": 9.0}, "O1": {"amp_m": 0.005, "phase_deg": 3.0}},
         "groups": ["all", "coastal", "91-182"], "band": "91-182", "time_base_status": "verified",
         "publishes_m2": True, "microtidal": False}
    t.update(over)
    return t


def test_restate_m2_note_first_and_the_worst_of_the_claimed_ones():
    r = sa.restate(_t(), validation={"91-182": "validated"})
    assert r["notes"] == [sa.UNCLAIMED_REASON] and "M2" not in r["by_constituent"]
    assert r["amp_uncertainty_m"] == 0.02 and r["phase_uncertainty_deg"] == 9.0 and r["worst"] == {"amp": "K1", "phase": "K1"}


def test_restate_a_failed_cell_drops_that_claim_on_its_group_only():
    lim = [{"constituent": "K1", "quantity": "phase", "group": "183-364"}, {"constituent": "S2", "quantity": "amp", "group": "coastal"}]
    r = sa.restate(_t(), limits=lim, validation={"91-182": "validated"})
    assert r["by_constituent"]["K1"]["phase_deg"] == 9.0                      # 183-364 does not apply here
    assert r["by_constituent"]["S2"] == {"amp_m": None, "phase_deg": 6.0}
    assert "no amplitude accuracy is stated for S2" in r["notes"][1]


def test_restate_disputed_time_base_and_microtidal_state_no_phase():
    r = sa.restate(_t(time_base_status="disputed"), validation={"91-182": "validated"})
    assert all(v["phase_deg"] is None for v in r["by_constituent"].values()) and r["phase_uncertainty_deg"] == 90.0
    assert r["amp_uncertainty_m"] == 0.02 and sa.DISPUTED_NOTE in r["notes"]
    m = sa.restate(_t(microtidal=True), validation={"91-182": "validated"})
    assert m["phase_uncertainty_deg"] == 90.0 and sa.MICROTIDAL_NOTE in m["notes"]


def test_restate_an_unvalidated_band_says_so():
    r = sa.restate(_t(band="31-90", groups=["all", "31-90"]), validation={"31-90": "too few"})
    assert r["notes"][-1] == "accuracy calibrated on cut long records; not validated against real records of this length"
    assert sa.VALIDATION_NOTE not in sa.restate(_t(), validation={"91-182": "validated"})["notes"]


def test_run_implausible_needs_no_calibration():
    tab = fake_table()
    rows, fits = {}, {}
    for i in range(30):
        rows[f"L{i}"] = {"lat": 30.0 + i * 0.1, "lon": -90.0}
        fits[f"L{i}"] = {**_fit(tab, 400, {"M2": 0.01, "K1": 0.05})}
    rows["marsh"] = {"lat": 31.05, "lon": -90.05}
    fits["marsh"] = _fit(tab, 400, {"M2": 0.06, "K1": 0.05})
    out = sa.run_implausible(rows, fits, tab)
    assert list(out) == ["marsh"] and out["marsh"]["reason"] == "implausible_fit"


# --- sign inversion against EOT20 ------------------------------------------------------------------------

EOT = {"M2": [1.0, 40.0], "S2": [0.4, 80.0], "K1": [0.10, 10.0], "O1": [0.08, 350.0], "dist_km": 3.5}
SPEED = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356}


def eot_fit(shift=None, amp=None, lag_h=0.0):
    """A fit's mains: EOT's, each phase moved by shift[c] (deg) plus a clock lag of lag_h hours, amplitude x amp[c]."""
    shift, amp = shift or {}, amp or {}
    return {"constituents": {c: {"amp_m": EOT[c][0] * amp.get(c, 1.0),
                                 "phase_deg": (EOT[c][1] + shift.get(c, 0.0) + SPEED[c] * lag_h) % 360.0}
                             for c in sa.MAIN}}


def test_an_inverted_record_is_named_and_the_detail_has_the_shifts():
    why = sa.inverted_against_eot20(eot_fit({c: 195.0 for c in sa.MAIN}), EOT)    # antonio_enes: about +195 deg
    assert why and why.startswith("sign-inverted") and "M2 195" in why and "3.5 km" in why
    assert sa.inverted_against_eot20(eot_fit({"M2": 158.0, "S2": 203.0, "K1": 165.0, "O1": 205.0}), EOT)


@pytest.mark.parametrize("case", ["agree", "lag_6h", "lag_12h", "one_out", "band_split", "amp_ratio", "far", "tiny",
                                  "missing", "no_eot"])
def test_records_that_are_not_inverted_never_trip(case):
    fit, eot = eot_fit({c: 180.0 for c in sa.MAIN}), dict(EOT)
    if case == "agree":
        fit = eot_fit({c: 8.0 for c in sa.MAIN})
    elif case == "lag_6h":            # a clock offset: the semidiurnal band moves twice as far as the diurnal one
        fit = eot_fit(lag_h=6.0)
    elif case == "lag_12h":
        fit = eot_fit(lag_h=12.0)
    elif case == "one_out":
        fit = eot_fit({"M2": 180.0, "S2": 180.0, "K1": 180.0, "O1": 215.0})
    elif case == "band_split":        # all in the window, but the bands shifted 40 deg apart
        fit = eot_fit({"M2": 200.0, "S2": 200.0, "K1": 160.0, "O1": 160.0})
    elif case == "amp_ratio":
        fit = eot_fit({c: 180.0 for c in sa.MAIN}, amp={"S2": 2.2})
    elif case == "far":
        eot["dist_km"] = 15.1
    elif case == "tiny":
        eot["O1"] = [0.004, 350.0]
        fit["constituents"]["O1"]["amp_m"] = 0.004
    elif case == "missing":
        del fit["constituents"]["K1"]
    elif case == "no_eot":
        eot = None
    assert sa.inverted_against_eot20(fit, eot) is None


def test_the_window_and_band_limits_are_inclusive_at_30_degrees():
    assert sa.inverted_against_eot20(eot_fit({c: 210.0 for c in sa.MAIN}), EOT)
    assert sa.inverted_against_eot20(eot_fit({c: 210.5 for c in sa.MAIN}), EOT) is None
    assert sa.inverted_against_eot20(eot_fit({"M2": 195.0, "S2": 195.0, "K1": 165.0, "O1": 165.0}), EOT)
    assert sa.inverted_against_eot20(eot_fit({"M2": 195.5, "S2": 195.5, "K1": 165.0, "O1": 165.0}), EOT) is None


# --- long-period lines of a short record ----------------------------------------------------------------

LP_SPEEDS = {"SA": 0.0410686, "SSA": 0.0821373, "MM": 0.5443747, "MF": 1.0980331, "MSF": 1.0158958}


def lp_fit(days, **cons):
    """A short fit of `days` with M2 and the given long-period lines, name=(amp, u)."""
    c = {"M2": {"amp_m": 0.7, "phase_deg": 10.0, "u_m": 0.007}}
    c.update({n: {"amp_m": a, "phase_deg": 0.0, "u_m": u} for n, (a, u) in cons.items()})
    return {"constituents": c, "rayleigh_hours": days * 24.0, "band_noise_m": {"LP": 0.01}}


def test_a_short_record_never_publishes_sa_or_ssa():
    d = sa.long_period_drops(lp_fit(300, SA=(0.3, 0.01), SSA=(0.965, 0.01)), LP_SPEEDS, None)
    assert [(x["name"], x["dropped_reason"]) for x in d] == [("SA", "long_period_rule"), ("SSA", "long_period_rule")]


def _ref(**lines):
    return {"record": "long-1", "km": 1.2, "lines": {n: lines.get(n) for n in ("MM", "MF", "MSF")}}


def test_with_no_long_record_nearby_nothing_confirms_mm_mf_msf():
    # waal_km_922: MF 0.413 m at u 0.1 passed the old 2 u_m test, on a river reach with no long record within 3 km
    d = {x["name"]: x["detail"] for x in sa.long_period_drops(lp_fit(300, MF=(0.413, 0.1), MM=(0.02, 0.001)),
                                                                 LP_SPEEDS, None)}
    assert set(d) == {"MF", "MM"} and "no long record of the same water within 3 km confirms its 0.413 m" in d["MF"]


def test_a_line_the_long_record_confirms_is_published_over_three_cycles():
    # santana-716d against santana-716a: MSF 0.126 m at 41 deg against 0.12 at 45 deg
    ref = _ref(MSF=[0.12, 45.0], MM=[0.08, 10.0])
    f = lp_fit(300, MSF=(0.126, 0.01), MM=(0.08, 0.01))
    f["constituents"]["MSF"]["phase_deg"], f["constituents"]["MM"]["phase_deg"] = 41.0, 10.0
    assert sa.long_period_drops(f, LP_SPEEDS, ref) == []
    # the same values over 2.3 cycles of MM (64 days): MM is dropped for its cycles alone
    d = sa.long_period_drops(dict(f, rayleigh_hours=64 * 24.0), LP_SPEEDS, ref)
    assert [x["name"] for x in d] == ["MM"] and "2.3 cycles" in d[0]["detail"] and "differs" not in d[0]["detail"]
    assert [x["name"] for x in sa.long_period_drops(dict(f, rayleigh_hours=82 * 24.0), LP_SPEEDS, ref)] == ["MM"]
    assert sa.long_period_drops(dict(f, rayleigh_hours=83 * 24.0), LP_SPEEDS, ref) == []


def test_confirmed_means_within_3_cm_or_half_the_long_value_whichever_is_larger():
    def drops(a, g, line):
        f = lp_fit(300, MM=(a, 0.001))
        f["constituents"]["MM"]["phase_deg"] = g
        return [x["detail"] for x in sa.long_period_drops(f, LP_SPEEDS, _ref(MM=line))]
    assert drops(0.229, 0.0, [0.2, 0.0]) == []                 # 2.9 cm off a 20 cm line
    assert drops(0.26, 0.0, [0.1, 0.0])                        # 16 cm off a 10 cm line: over 3 cm and over 5 cm
    assert drops(0.149, 0.0, [0.1, 0.0]) == []                 # 4.9 cm off: under half of 10 cm
    assert drops(0.151, 0.0, [0.1, 0.0])
    assert drops(0.02, 0.0, [0.02, 180.0])                     # same amplitude, opposite phase: 4 cm vector apart
    assert drops(0.02, 0.0, [0.02, 60.0]) == []                # 2 cm vector apart
    assert drops(0.029, 0.0, None) == []                       # the long record has it under its noise: 0
    d = drops(0.221, 0.0, [0.03, 0.0])                         # caernarvon_outfall: MM 0.221 against 0.030
    assert d and "differs by 0.191 m (over 0.030 m) from the long record long-1 (1.2 km), which has 0.030 m" in d[0]
    assert "which has it under its noise" in drops(0.031, 0.0, None)[0]


def _long(rid, lat, lon, m2=(0.7, 10.0), **lp):
    c = {"M2": {"amp_m": m2[0], "phase_deg": m2[1]}}
    c.update({n: {"amp_m": a, "phase_deg": 5.0} for n, a in lp.items()})
    return {"record": rid, "lat": lat, "lon": lon, "fit": {"constituents": c}}


def test_the_reference_is_the_nearest_long_record_in_the_same_water_within_3_km():
    f = lp_fit(60)
    longs = [_long("far", 40.03, -74.0, MM=0.02),                       # 3.3 km
             _long("bay", 40.0, -74.005, m2=(0.04, 120.0), MM=0.5),     # 0.4 km, another water (M2 x0.06)
             _long("lag", 40.0, -74.01, m2=(0.7, 60.0), MM=0.5),        # 0.9 km, M2 50 deg away
             _long("near", 40.02, -74.0, MM=0.02, MF=0.01)]              # 2.2 km, same water
    r = sa.long_period_reference(f, 40.0, -74.0, longs)
    assert r == {"record": "near", "km": 2.22, "lines": {"MM": [0.02, 5.0], "MF": [0.01, 5.0], "MSF": None}}
    assert sa.long_period_reference(f, 40.0, -74.0, [longs[0]]) is None
    assert sa.long_period_reference(f, 40.0, -74.0, []) is None


# --- binding to the fit runs (FIX-SRCAL) ----------------------------------------------------------------

def _run(root, name, results, manifest=None):
    d = root / name
    (d / "fits").mkdir(parents=True)
    (d / "results.jsonl").write_text(results)
    if manifest is not None:
        (d / "timebase_manifest.json").write_text(json.dumps(manifest))
    return d


def test_a_calibration_binds_to_the_fit_runs_it_was_made_from(tmp_path):
    run = _run(tmp_path, "run", '{"record": "a"}\n', {"fits_sha256": {"a": "1" * 64}})
    over = _run(tmp_path, "over", '{"record": "b"}\n', {"fits_sha256": {"b": "2" * 64}})
    cal = {"config": {"method": sa.method()}, "inputs": {"fit_runs": [sa.run_identity(run), sa.run_identity(over)]}}
    sa.check_bound(cal, [run, over])
    copy = _run(tmp_path, "copy", '{"record": "a"}\n', {"fits_sha256": {"a": "1" * 64}})
    sa.check_bound({"config": {"method": sa.method()}, "inputs": {"fit_runs": [sa.run_identity(copy)]}}, [run])     # the bytes, not the path
    with pytest.raises(ValueError, match="re-run gesla.short_accuracy"):
        sa.check_bound(cal, [run])                                                # the overlay left out
    with pytest.raises(ValueError, match="re-run gesla.short_accuracy"):
        sa.check_bound(cal, [over, run])                                          # the order: the overlay wins
    with pytest.raises(ValueError, match="re-run gesla.short_accuracy"):
        sa.check_bound({"config": {"method": sa.method()}, "inputs": {}}, [run])   # a calibration naming none


def test_a_refit_breaks_the_binding_by_its_results_or_its_fits(tmp_path):
    run = _run(tmp_path, "run", '{"record": "a", "rms_m": 0.1}\n', {"fits_sha256": {"a": "1" * 64}})
    cal = {"config": {"method": sa.method()}, "inputs": {"fit_runs": [sa.run_identity(run)]}}
    refit = _run(tmp_path, "refit", '{"record": "a", "rms_m": 0.2}\n', {"fits_sha256": {"a": "1" * 64}})
    with pytest.raises(ValueError, match="the build uses"):
        sa.check_bound(cal, [refit])
    same_rows = _run(tmp_path, "same-rows", '{"record": "a", "rms_m": 0.1}\n', {"fits_sha256": {"a": "3" * 64}})
    with pytest.raises(ValueError, match="the build uses"):
        sa.check_bound(cal, [same_rows])                                          # one fit document changed


def test_the_calibration_takes_the_tcd_dump_the_fit_runs_name(tmp_path):
    dump = tmp_path / "otc-dump.json"
    dump.write_text('{"otc": 1}')
    t = {"path": str(dump), "sha256": sa.sha256_file(dump)}
    run = _run(tmp_path, "run", "", {"inputs": {"tcd_table": t}})
    over = _run(tmp_path, "over", "", {"inputs": {"tcd_table": t}})
    assert sa.fit_runs_tcd([run, over]) == dump
    other = tmp_path / "xtide-dump.json"
    other.write_text('{"xtide": 1}')
    mixed = _run(tmp_path, "mixed", "", {"inputs": {"tcd_table": {"path": str(other), "sha256": sa.sha256_file(other)}}})
    with pytest.raises(ValueError, match="different TCD dumps"):
        sa.fit_runs_tcd([run, mixed])
    with pytest.raises(ValueError, match="names no TCD dump"):
        sa.fit_runs_tcd([_run(tmp_path, "bare", "")])
    dump.write_text('{"otc": 2}')
    with pytest.raises(ValueError, match="not the TCD dump the fit runs name"):
        sa.fit_runs_tcd([run])


def test_a_gate_written_into_a_calibration_names_its_commit(tmp_path):
    tab = fake_table()
    sa.write_calibration(_cal(tab, _fit(tab, 40.5)) | {"config": {}}, tmp_path)
    cell = {"verdict": "pass", "amp_share_over": 0.5, "phase_share_over": 0.5}
    res = {"groups": {b: {"pooled": cell} for b in ("<=30", "31-90", "91-182", "183-364")}, "band": [0.35, 0.65],
           "failed_cells": [], "passed": True, "records": 0, "comparators": {}}
    run = _run(tmp_path, "run", '{"record": "a"}\n', {"fits_sha256": {"a": "1" * 64}})
    cal = sa.apply_gate(tmp_path, res, commit="abc1234", fit_runs=[run])
    assert cal["config"]["gates"][-1]["commit"] == "abc1234"
    entry = sa.read_calibration(tmp_path)["config"]["gates"][-1]
    assert entry["commit"] == "abc1234" and entry["fit_runs"] == [sa.run_identity(run)]


def test_a_fit_whose_selection_is_not_rebuilt_stops_r8_and_the_plan():
    # FIX-SRCAL: a fit made with another TCD's dump (or rule) was quietly left out of the long set and the pools
    tab = fake_table()
    rows, fits = {}, {}
    for i in range(30):
        rows[f"L{i}"] = {"lat": 30.0 + i * 0.1, "lon": -90.0}
        fits[f"L{i}"] = _fit(tab, 400, {"M2": 0.01, "K1": 0.05})
    fits["L7"] = {**fits["L7"], "rayleigh_kept": fits["L7"]["rayleigh_kept"][:-1]}
    with pytest.raises(sa.SelectionMismatch, match="1 fits' Rayleigh selection is not rebuilt") as e:
        sa.run_implausible(rows, fits, tab)
    assert list(e.value.mismatch) == ["L7"]
    fits["L9"] = {**fits["L9"], "candidate_list": "no-such-list"}
    meta = {r: {"TIME ZONE HOURS": "0"} for r in rows}
    full = {r: {**rows[r], "data_hours": 800 * 24.0, "time_base": "utc_instant", "months_dropped": 0} for r in rows}
    with pytest.raises(sa.SelectionMismatch) as e:
        sa.plan(full, fits, tab, meta)
    assert sorted(e.value.mismatch) == ["L7", "L9"]


def test_a_cut_takes_the_list_fit_run_would_choose_for_its_data(monkeypatch):
    # N4: production's choice for a record of the cut (scope, window A, both spans resolving the extended list),
    # never the station's own full-record list
    import test_fix_const as fc
    from gesla import select_constituents as sc
    def table(names):
        n = 2030 - 2000 + 1
        return TcdTable({"years": [2000, 2030], "tcd_sha256": "s" * 64, "aliases": {},
                         "constituents": {k: {"speed": fc.SPEEDS[k], "v0u": [0.0] * n, "f": [1.0] * n} for k in names}})
    tab = table(fc.SPEEDS)
    monkeypatch.setattr(sc, "CANDIDATE_LISTS", {"base": fc.BASE, "extended": fc.EXT})
    monkeypatch.setattr(sc, "EXTENDED_CANDIDATES", fc.EXT)
    full = fc.series(tab, ma2=0.05)                         # 2008-2025-08, window A covered, MA2 present
    assert sa.cut_candidate_list(tab, full, "NOR") == "extended"
    assert sa.cut_candidate_list(tab, full, "USA") == "base"                   # out of scope
    t = full.times_s
    for lo, hi in ((fc._ts(2024, 9), fc._ts(2024, 12)),                      # 3 months in window A: MA2 unresolved
                   (fc._ts(2015, 1), fc._ts(2015, 12))):                     # no window-A data
        cut = Series("cut", t[(t >= lo) & (t < hi)], full.heights_m[(t >= lo) & (t < hi)], {})
        assert sa.cut_candidate_list(tab, cut, "NOR") == sc.select_candidate_list(tab, cut, country="NOR")[0] == "base"
    bare = table([k for k in fc.SPEEDS if k not in ("MA2", "MB2")])              # no MA2/MB2: as in fit_run
    with pytest.raises(ValueError, match="extended list needs the OTC TCD"):
        sa.cut_candidate_list(bare, full, "NOR")


def test_the_station_job_fits_each_cut_with_its_own_choice(tmp_path, monkeypatch):
    from gesla.select_constituents import CANDIDATE_LISTS
    tab = fake_table()
    t = T2019 + np.arange(400 * 24, dtype=np.int64) * HOUR
    s = Series("donor", t, synth(tab, TRUTH, t), {})
    full = _fit(tab, 400) | {"candidate_list": "extended",
                             "constituents": {c: {"amp_m": a, "phase_deg": g} for c, (a, g) in TRUTH.items()}}
    (tmp_path / "fits").mkdir()
    (tmp_path / "fits" / "donor.json").write_text(json.dumps(full))
    hours = sa.series_hours(s)
    chosen, used = [], []

    class A:
        def consts(self):
            return {c: (a, g) for c, (a, g) in TRUTH.items()}

    def select(table, cut, *, country):
        chosen.append((len(cut), country))
        return ("extended" if len(chosen) == 2 else "base"), None

    def analyse(table, series, *, candidates=None):
        used.append(candidates)
        return A()
    monkeypatch.setattr(sa, "select_candidate_list", select)
    monkeypatch.setattr(sa, "analyse", analyse)
    monkeypatch.setattr(sa, "_fit_window", lambda rid: s)
    monkeypatch.setattr(sa, "cut_shifts", lambda sh, th, seed: [(1, hours[: 30 * 24]), (2, hours[: 60 * 24])])
    monkeypatch.setitem(sa._W, "table", tab)
    monkeypatch.setitem(sa._W, "fit_dirs", [tmp_path])
    monkeypatch.setitem(sa._W, "masks", {"t": hours[: 30 * 24]})
    monkeypatch.setitem(sa._W, "rel", type("R", (), {"metadata": lambda self: {"donor": {"COUNTRY": "NOR"}}})())
    d = sa._station_job("donor", ["t"])
    assert chosen == [(30 * 24, "NOR"), (60 * 24, "NOR")]
    assert used == [CANDIDATE_LISTS["base"], CANDIDATE_LISTS["extended"]]
    assert [c["candidate_list"] for c in d["cuts"]["t"]] == ["base", "extended"]


def test_a_calibration_made_on_another_tcd_dump_is_not_bound(tmp_path):
    run = _run(tmp_path, "run", '{"record": "a"}\n', {"fits_sha256": {"a": "1" * 64}})
    cal = {"config": {"method": sa.method()}, "inputs": {"fit_runs": [sa.run_identity(run)], "tcd_table_sha256": "e" * 64}}
    sa.check_bound(cal, [run], tcd_table_sha256="e" * 64)
    with pytest.raises(ValueError, match="made on the TCD dump e+, the build uses 7+"):
        sa.check_bound(cal, [run], tcd_table_sha256="7" * 64)


# --- calibrate(): the TCD dump it hands the workers, and the resume (FIX-SRCAL r1, N5/B1) -----------------------

class _Fut:
    def __init__(self, v):
        self._v = v

    def result(self):
        return self._v


class _Pool:
    """ProcessPoolExecutor stand-in: runs each job inline and records the worker initializer's arguments."""
    inits: list = []

    def __init__(self, max_workers, initializer, initargs):
        _Pool.inits.append(initargs)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def submit(self, fn, *a):
        return _Fut(fn(*a))


@pytest.fixture
def cal_harness(tmp_path, monkeypatch):
    """calibrate() on a two-record fit run (target t1, station s1), every heavy step replaced by a recorder."""
    import gesla.read_gesla as rg
    tab = fake_table()
    dump = tmp_path / "otc-dump.json"
    dump.write_text('{"otc": 1}')
    run = tmp_path / "run"
    (run / "fits").mkdir(parents=True)
    rows = [{"record": r, "outcome": "fitted", "lat": 50.0, "lon": 1.0} for r in ("s1", "t1")]
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for r in ("s1", "t1"):
        (run / "fits" / f"{r}.json").write_text(json.dumps(_fit(tab, 800 if r == "s1" else 40)))
    (run / "timebase_manifest.json").write_text(json.dumps(
        {"inputs": {"tcd_table": {"path": str(dump), "sha256": sa.sha256_file(dump)}}}))
    gesla_root = tmp_path / "gesla"
    gesla_root.mkdir()
    (gesla_root / "MANIFEST.json").write_text("{}")
    h = type("H", (), {})()
    h.run, h.dump, h.loaded, h.cut, h.commit = run, dump, [], [], "c0ffee"
    _Pool.inits = []
    monkeypatch.setattr(sa, "_git", lambda a: "" if a[0] == "status" else h.commit)
    monkeypatch.setattr(sa.TcdTable, "load", staticmethod(lambda p: h.loaded.append(Path(p)) or tab))
    monkeypatch.setattr(rg, "GeslaRelease", lambda: type("Rel", (), {"metadata": lambda self: {}, "version": "4.1",
                                                                     "root": str(gesla_root)})())
    monkeypatch.setattr(sa, "plan", lambda rows, fits, table, meta, seed, holdout_n: {
        "clusters": {}, "refused": {}, "stations": {"s1": "s1"}, "holdout": {}, "long": ["s1"],
        "short": {"t1": {"M2": 1.0}}, "pools": {"t1": [("s1", 5.0)]},
        "targets": {"t1": {"why": None, "good_hours": 960.0, "regime": "semidiurnal", "reference": ["M2", 1.0],
                           "fit_sha256": sa.fit_sha256(fits["t1"])}}})
    monkeypatch.setattr(sa, "ProcessPoolExecutor", _Pool)
    monkeypatch.setattr(sa, "as_completed", lambda fs: list(fs))
    monkeypatch.setattr(sa, "_mask_job", lambda rid: (rid, np.arange(24, dtype=np.int64) * HOUR, None))

    def station_job(station, targets):
        h.cut.append(station)
        full = json.loads((run / "fits" / f"{station}.json").read_text())
        return {"station": station, "full_sha256": sa.fit_sha256(full), "secs": 0.0,
                "cuts": {t: [{"k": 1, "n": 24, "target_n": 24, "candidate_list": "base", "errors": None}] for t in targets}}
    monkeypatch.setattr(sa, "_station_job", station_job)
    return h


def test_calibrate_reads_and_hands_its_workers_the_fit_runs_dump(cal_harness, tmp_path):
    h = cal_harness
    cal = sa.calibrate([h.run], tmp_path / "out", workers=1)
    assert h.loaded == [h.dump]
    assert [i[2] for i in _Pool.inits] == [str(h.dump), str(h.dump)]           # the mask pool, then the cut pool
    assert cal["inputs"]["tcd_table"] == str(h.dump)
    assert cal["inputs"]["tcd_table_sha256"] == sa.sha256_file(h.dump)
    assert "selection_mismatch" not in cal                                      # N3: no dead field


def test_a_resume_reuses_only_rows_stamped_by_this_run(cal_harness, tmp_path):
    h, out = cal_harness, tmp_path / "out"
    sa.calibrate([h.run], out, workers=1)
    assert h.cut == ["s1"]
    (row,) = [json.loads(x) for x in (out / sa.CUTS_FILE).read_text().splitlines()]
    assert row["stamp"] == {"commit": "c0ffee", "tcd_table_sha256": sa.sha256_file(h.dump), "seed": 0,
                            "cut_list_rule": sa.CUT_LIST_RULE}
    sa.calibrate([h.run], out, workers=1)
    assert h.cut == ["s1"]                                                      # reused
    for change in ("no stamp", "no list", "commit", "seed"):
        h.cut.clear()
        r = dict(row)
        if change == "no stamp":                                                # a row from before the stamp
            r.pop("stamp")
        if change == "no list":
            r["cuts"] = {"t1": [{k: v for k, v in c.items() if k != "candidate_list"} for c in r["cuts"]["t1"]]}
        (out / sa.CUTS_FILE).write_text(json.dumps(r) + "\n")
        h.commit = "beef" if change == "commit" else "c0ffee"
        sa.calibrate([h.run], out, workers=1, seed=1 if change == "seed" else 0)
        assert h.cut == ["s1"], change                                          # cut again
        (row,) = [json.loads(x) for x in (out / sa.CUTS_FILE).read_text().splitlines()]
        assert row["stamp"]["commit"] == h.commit
    h.cut.clear()
    h.commit = "c0ffee"
    row["stamp"] = dict(row["stamp"], tcd_table_sha256="0" * 64)
    (out / sa.CUTS_FILE).write_text(json.dumps(row) + "\n")
    sa.calibrate([h.run], out, workers=1)
    assert h.cut == ["s1"]                                                      # another TCD dump: cut again


def test_reusable_needs_stamp_fit_targets_and_lists():
    stamp = sa.cut_stamp("c", "d" * 64, 0)
    row = {"stamp": stamp, "full_sha256": "f", "cuts": {"t": [{"candidate_list": "base"}]}}
    assert sa.reusable(row, stamp, {"t"}, "f")
    assert not sa.reusable(row, sa.cut_stamp("c", "e" * 64, 0), {"t"}, "f")
    assert not sa.reusable(row, stamp, {"t", "u"}, "f")
    assert not sa.reusable(row, stamp, {"t"}, "g")
    assert not sa.reusable({**row, "cuts": {"t": [{}]}}, stamp, {"t"}, "f")


def test_a_relative_dump_path_is_the_manifests(tmp_path, monkeypatch):
    run = _run(tmp_path, "run", "", None)
    (run / "dump.json").write_text('{"d": 1}')
    (run / "timebase_manifest.json").write_text(json.dumps(
        {"inputs": {"tcd_table": {"path": "dump.json", "sha256": sa.sha256_file(run / "dump.json")}}}))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "dump.json").write_text('{"other": 1}')                       # a same-named file in the cwd
    monkeypatch.chdir(elsewhere)
    assert sa.fit_runs_tcd([run]) == run / "dump.json"


# --- the calibration method (FIX-SRCAL r2) ----------------------------------------------------------------------

def _bound(run, **config):
    return {"config": config, "inputs": {"fit_runs": [sa.run_identity(run)], "tcd_table_sha256": "d" * 64}}


def test_a_calibration_of_another_method_is_not_bound(tmp_path):
    run = _run(tmp_path, "run", '{"record": "a"}\n', {"fits_sha256": {"a": "1" * 64}})
    sa.check_bound(_bound(run, method=sa.method()), [run], tcd_table_sha256="d" * 64)
    with pytest.raises(ValueError, match=r"method is not recorded .*this code's is \{'cal_method_version': 3"):
        sa.check_bound(_bound(run), [run], tcd_table_sha256="d" * 64)                 # v6, v7: none recorded
    old = {"cal_method_version": 2, "cut_list_rule": "the donor's own full-record list"}
    with pytest.raises(ValueError, match=r"method is \{'cal_method_version': 2.*this code's is \{'cal_method_version': 3"):
        sa.check_bound(_bound(run, method=old), [run], tcd_table_sha256="d" * 64)
    with pytest.raises(ValueError, match="method is"):
        sa.check_bound(_bound(run, method={**sa.method(), "cut_list_rule": "x"}), [run])
    assert sa.method() == {"cal_method_version": sa.CAL_METHOD_VERSION, "cut_list_rule": sa.CUT_LIST_RULE}


def test_calibrate_records_the_method(cal_harness, tmp_path):
    cal = sa.calibrate([cal_harness.run], tmp_path / "out", workers=1)
    assert cal["config"]["method"] == sa.method()
    assert sa.read_calibration(tmp_path / "out")["config"]["method"] == sa.method()


def _stamped(tmp_path, rows):
    tab = fake_table()
    cal = {**_cal(tab, _fit(tab, 40.5)), "config": {"seed": 0}, "code": {"commit": "c0ffee"},
           "inputs": {"tcd_table_sha256": "d" * 64}}
    (tmp_path / sa.CUTS_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    sa.write_calibration(cal, tmp_path)
    return cal


def test_record_method_derives_it_from_the_stamps_and_changes_nothing_else(tmp_path):
    st = sa.cut_stamp("c0ffee", "d" * 64, 0)
    cal = _stamped(tmp_path, [{"station": "s", "stamp": st}, {"station": "u", "stamp": st}])
    cuts = (tmp_path / sa.CUTS_FILE).read_bytes()
    assert sa.main(["record-method", str(tmp_path)]) == 0
    got = sa.read_calibration(tmp_path)
    assert got["config"]["method"] == sa.method()
    got.pop("_sha256"); got["config"].pop("method")
    assert got == cal and (tmp_path / sa.CUTS_FILE).read_bytes() == cuts
    with pytest.raises(ValueError, match="already records its method"):
        sa.record_method(tmp_path)


@pytest.mark.parametrize("rows,why", [
    ([{"station": "s"}], "is not the calibration's own"),                                      # v7: no stamp
    ([{"station": "s", "stamp": {**sa.cut_stamp("c0ffee", "d" * 64, 0), "commit": "beef"}}], "is not the calibration's own"),
    ([{"station": "s", "stamp": {**sa.cut_stamp("c0ffee", "d" * 64, 0), "tcd_table_sha256": "e" * 64}}], "is not the calibration's own"),
    ([{"station": "s", "stamp": {**sa.cut_stamp("c0ffee", "d" * 64, 0), "seed": 1}}], "is not the calibration's own"),
    ([{"station": "s", "stamp": {**sa.cut_stamp("c0ffee", "d" * 64, 0), "cut_list_rule": "donor list"}}], "name no one method"),
    ([{"station": "s", "stamp": sa.cut_stamp("c0ffee", "d" * 64, 0)},
      {"station": "u", "stamp": {**sa.cut_stamp("c0ffee", "d" * 64, 0), "cut_list_rule": "donor list"}}], "name no one method"),
    ([], "name no one method")])
def test_record_method_refuses_cuts_it_cannot_vouch_for(tmp_path, rows, why):
    _stamped(tmp_path, rows)
    with pytest.raises(ValueError, match=why):
        sa.record_method(tmp_path)
    assert "method" not in sa.read_calibration(tmp_path)["config"]
