"""G04d weak time-base evidence: the rule (timebase_weak docstring), its inputs and one end-to-end path."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gesla import timebase_weak as tw
from gesla.read_gesla import otc_work
from gesla.record import GaugeRecord
from gesla.tcd_table import TcdTable
from gesla.timebase_weak import (NA, OFF, Lineage, Served, ServedRow, combine, dst_check, independent, lineage_reason,
                                 load_served, phase_check, select_served, served_kind, zone_has_dst)

SPEED = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356}
REF = {"M2": (1.0, 100.0), "S2": (0.3, 140.0), "K1": (0.2, 200.0), "O1": (0.15, 180.0)}


def shifted(minutes, amp=1.0):
    return {k: (a * amp, g + SPEED[k] * minutes / 60.0) for k, (a, g) in REF.items()}


def _c(v, **k):
    return {"value": v, **k}


# --- phase_check and the module thresholds ---------------------------------------------------------

def test_phase_check_classes_whole_hours_and_refuses_sub_hour_lags():
    kw = dict(ratio=(0.9, 1.1), resid=10.0, band=6.0)
    assert phase_check(shifted(0), REF, SPEED, **kw)["value"] == 0
    assert phase_check(shifted(60), REF, SPEED, **kw)["value"] == 1
    assert phase_check(shifted(-120), REF, SPEED, **kw)["value"] == -2
    assert phase_check(shifted(17), REF, SPEED, **kw)["value"] == OFF
    assert phase_check(shifted(0, amp=1.5), REF, SPEED, **kw)["value"] == NA        # not the same tide
    assert phase_check({"M2": REF["M2"]}, REF, SPEED, **kw)["value"] == NA            # too few constituents


class _T:
    """The minimum of a TcdTable checks() needs (names and speeds)."""

    def __contains__(self, n):
        return n in SPEED

    def name(self, n):
        return n

    def speed(self, n):
        return SPEED[n]


def _feat(minutes):
    return {"consts": shifted(minutes), "season": {}, "year": 2020}


def _eot(dist=1.0):
    return {"dist_km": dist, **{k: list(v) for k, v in REF.items()}}


def _rows(kind="independent"):
    return [ServedRow(Served("T1", "o-1-x-noaa", dict(REF)), kind, "")]


@pytest.mark.parametrize("minutes,e,s", [(5.5, 0, 0), (6.5, 0, OFF), (9.5, 0, OFF), (10.5, OFF, OFF)])
def test_checks_uses_the_module_bands(minutes, e, s):
    """S band 6 min, E band 10 min, through checks() (the function the audit uses)."""
    c = tw.checks(_feat(minutes), _T(), _eot(), _rows(), "UTC")
    assert (c["E"]["value"], c["S"]["value"]) == (e, s)


def test_checks_eot20_cell_too_far_and_missing_inputs():
    assert tw.checks(_feat(0), _T(), _eot(dist=16.0), _rows(), "UTC")["E"]["value"] == NA
    assert tw.checks(_feat(0), _T(), _eot(dist=14.0), _rows(), "UTC")["E"]["value"] == 0
    c = tw.checks(_feat(0), _T(), None, _rows(), "UTC", missing_inputs=True)
    assert c["E"]["value"] == NA and "missing from the weak inputs" in c["E"]["why"]
    assert c["D"]["value"] == NA and c["D"]["code"] == "dst_inputs_missing"


def test_checks_never_compares_an_unqualified_station():
    c = tw.checks(_feat(0), _T(), _eot(), [ServedRow(Served("T1", "o", dict(REF)), "unqualified", "origin unverified")],
                  "UTC")
    assert c["S"]["value"] == NA and c["S"]["why"] == "origin unverified" and not c["S_independent"]


# --- lineage (M4) and origin status (M5) -------------------------------------------------------------

LIN = {"a-1-usa-noaa": Lineage("NOAA", "originator"), "b-2-usa-usgs": Lineage("USGS", "originator"),
       "c-3-x-uhslc_fd": Lineage("UHSLC_FD", "redistributor"), "d-4-x-uhslc_rq": Lineage("UHSLC_RQ", "redistributor"),
       "e-5-mex-unam": Lineage("UNAM", "originator"), "f-6-mex-unam_hist": Lineage("UNAM_HIST", "originator"),
       "g-7-nld-cmems": Lineage("CMEMS", "redistributor"), "h-8-nld-rws": Lineage("RWS", "originator"),
       "i-9-jpn-jodc_jma": Lineage("JODC_JMA", "redistributor"), "j-0-esp-ieo": Lineage("IEO", "originator"),
       "k-1-irl-mi_c": Lineage("MI_C", "originator"), "l-2-irl-mi_r": Lineage("MI_R", "originator")}


def _sv(origin):
    return Served("T", origin, {})


@pytest.mark.parametrize("rec,origin,indep", [
    ("a-1-usa-noaa", "b-2-usa-usgs", True),          # two originators of different lineages
    ("a-1-usa-noaa", "a-1-usa-noaa", False),         # the record itself
    ("a-1-usa-noaa", "", False),                     # no origin
    ("c-3-x-uhslc_fd", "d-4-x-uhslc_rq", False),     # UHSLC fast delivery vs research quality
    ("e-5-mex-unam", "f-6-mex-unam_hist", False),    # X and X_hist
    ("g-7-nld-cmems", "h-8-nld-rws", False),         # CMEMS aggregates the national networks
    ("h-8-nld-rws", "g-7-nld-cmems", False),
    ("d-4-x-uhslc_rq", "i-9-jpn-jodc_jma", False),   # UHSLC ingests JODC/JMA
    ("d-4-x-uhslc_rq", "j-0-esp-ieo", False),        # a redistributor's originator is not established
    ("k-1-irl-mi_c", "l-2-irl-mi_r", False),         # one agency, two series
    ("a-1-usa-noaa", "zz-9-usa-usgs", False),        # origin not in the inventory: unknown -> dependent
])
def test_independence_needs_two_established_lineages(rec, origin, indep):
    assert independent(rec, _sv(origin), LIN) is indep
    assert (lineage_reason(rec, _sv(origin), LIN) is None) is indep


def test_independence_without_lineage_data_is_dependent():
    assert not independent("a-1-usa-noaa", _sv("b-2-usa-usgs"), {})
    assert not independent("a-1-usa-noaa", _sv("b-2-usa-usgs"), None)


def test_served_kind_needs_a_verified_origin():
    sv = Served("T", "b-2-usa-usgs", {})
    assert served_kind("a-1-usa-noaa", sv, LIN, {"b-2-usa-usgs": "verified"}).kind == "independent"
    for st in ("unverified", "disputed", "corrected", "not audited"):
        r = served_kind("a-1-usa-noaa", sv, LIN, {"b-2-usa-usgs": st})
        assert r.kind == "unqualified" and st in r.why
    assert served_kind("a-1-usa-noaa", sv, LIN, {}).kind == "unqualified"
    same = served_kind("c-3-x-uhslc_fd", Served("T", "d-4-x-uhslc_rq", {}), LIN, {"d-4-x-uhslc_rq": "verified"})
    assert same.kind == "dependent" and "redistributor" in same.why


# --- several served stations (M3) -------------------------------------------------------------------

def test_load_served_keeps_every_station_in_id_order(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps({"stations": [
        {"id": "T2", "constituents": [{"name": "M2", "amp": 1.0, "phase": 10.0}]},
        {"id": "T1", "constituents": [{"name": "M2", "amp": 1.0, "phase": 20.0}]}]}))
    with open(tmp_path / "m.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "file_id", "match", "origin_40"])
        w.writerow(["T2", "r", "coord", "o2"])
        w.writerow(["T1", "r", "coord", "o1"])
        w.writerow(["T1", "q", "coord", "o1"])
    got = load_served(tmp_path / "t.json", tmp_path / "m.csv")
    assert [s.station for s in got["r"]] == ["T1", "T2"] and [s.origin for s in got["r"]] == ["o1", "o2"]
    assert [s.station for s in got["q"]] == ["T1"]


def test_select_served_prefers_independent_and_disputes_disagreement():
    ind = {"station": "T2", "kind": "independent", "value": 0}
    dep = {"station": "T1", "kind": "dependent", "value": 0}
    S, indep, conflict = select_served([dep, ind])
    assert S["station"] == "T2" and indep and conflict is None
    S, indep, conflict = select_served([dep, {**ind, "value": -1}])
    assert conflict and not indep and S["value"] == NA
    S, indep, conflict = select_served([{**dep, "value": 1}, {**dep, "station": "T3", "value": 0}])
    assert conflict                                     # dependent stations that disagree by 1 h: disputed too
    S, indep, conflict = select_served([dep, {"station": "T9", "kind": "unqualified", "value": NA, "why": "x"}])
    assert S["station"] == "T1" and not indep and conflict is None
    assert select_served([])[0]["value"] == NA


def test_served_disagreement_is_disputed_whatever_the_other_checks():
    r = combine(_c(0), _c(NA), False, _c("no_step"), 0, "UTC", served_conflict="T1 (dependent) 0, T2 (independent) -1")
    assert (r.annotation, r.code, r.label) == ("disputed", "disputed:served_stations_disagree", "utc_instant")


# --- the rule ---------------------------------------------------------------------------------------

def test_rule_verified_needs_eot20_and_an_independent_served_station():
    assert combine(_c(0), _c(0), True, _c("no_step"), 0, "UTC").code == "verified:eot20+served_independent"
    assert combine(_c(0), _c(0), True, _c(NA), 0, "UTC").code == "verified:eot20+served_independent"
    r = combine(_c(0), _c(0, station="T1", kind="dependent"), False, _c("no_step"), 0, "UTC")
    assert (r.annotation, r.code) == ("unverified", "unverified:eot20_only_served_dependent")
    assert "EOT20 alone cannot verify" in r.reason
    assert combine(_c(0), _c(NA), False, _c("no_step"), 0, "UTC").annotation == "unverified"
    assert combine(_c(0), _c(0), True, _c("ambiguous"), 0, "UTC").annotation == "unverified"


@pytest.mark.parametrize("d", ["no_step", NA])
@pytest.mark.parametrize("indep", [True, False])
def test_rule_served_alone_never_verifies(d, indep):
    """The removed path (amendment 1) and its D = n/a variant."""
    r = combine(_c(NA), _c(0), indep, _c(d), 0, "UTC")
    assert (r.annotation, r.label) == ("unverified", "utc_instant")


def test_rule_disagreement_is_disputed_and_keeps_the_declared_value():
    r = combine(_c(0), _c(OFF), False, _c("no_step"), 0, "UTC")        # a +17 min record: EOT20 cannot see it
    assert (r.annotation, r.label, r.code) == ("disputed", "utc_instant", "disputed:weak_checks_disagree")
    assert combine(_c(1), _c(0), True, _c("no_step"), 0, "UTC").annotation == "disputed"
    # EOT20 off with no served check is model bias, not a dispute
    assert combine(_c(OFF), _c(NA), False, _c("no_step"), 0, "UTC").annotation == "unverified"
    assert combine(_c(1), _c(NA), False, _c("no_step"), 0, "UTC").annotation == "unverified"


def test_rule_whole_hour_correction_needs_two_independent_checks():
    r = combine(_c(1), _c(1), True, _c("no_step"), 0, "UTC")
    assert (r.annotation, r.label) == ("corrected", "utc_plus_1")
    r = combine(_c(-1), _c(-1), True, _c("no_step"), 0, "UTC")
    assert (r.annotation, r.label) == ("corrected", "utc_minus_1")
    assert combine(_c(1), _c(1), False, _c("no_step"), 0, "UTC").annotation == "disputed"   # dependent served
    assert combine(_c(NA), _c(1), True, _c("no_step"), 0, "UTC").annotation == "disputed"
    assert combine(_c(1), _c(1), True, _c(NA), 0, "UTC").annotation == "disputed"           # no DST self-test


def test_negative_hour_labels_round_trip_to_the_correction():
    from gesla.timebase_audit import correction_of
    assert correction_of(tw._hours_label(-1)).n == -1 and correction_of(tw._hours_label(-1)).label == "utc_minus_1"
    assert correction_of(tw._hours_label(2)).n == 2
    assert correction_of(tw._hours_label(0)).kind == "utc_instant"


def test_rule_with_a_declared_time_base_other_than_utc():
    # declared UTC+1, both checks say +1, independent: verified as declared
    r = combine(_c(1), _c(1), True, _c("no_step"), 1, "UTC")
    assert (r.annotation, r.label) == ("verified", "utc_plus_1")
    # declared UTC+1, both checks say 0, served DEPENDENT: never a correction
    r = combine(_c(0), _c(0), False, _c("no_step"), 1, "UTC")
    assert (r.annotation, r.label) == ("disputed", "utc_plus_1")
    # declared UTC+1, both 0, independent, no DST step: corrected to UTC
    r = combine(_c(0), _c(0), True, _c("no_step"), 1, "UTC")
    assert (r.annotation, r.label, r.code) == ("corrected", "utc_instant",
                                               "corrected:utc_plus_1->utc_instant:eot20+served_independent+dst_selftest")


def test_rule_dst_step_is_corrected_only_when_confirmed():
    step = _c("step", season_m2_min=61.0)
    r = combine(_c(OFF), _c(OFF), True, step, 0, "Europe/Berlin", E2=_c(0), S2=_c(NA))
    assert (r.annotation, r.label) == ("corrected", "local_dst:Europe/Berlin")
    assert r.code == "corrected:utc_instant->local_dst:Europe/Berlin:dst_selftest+eot20"
    assert combine(_c(OFF), _c(OFF), True, step, 0, "Europe/Berlin", E2=_c(NA), S2=_c(NA)).annotation == "disputed"
    assert combine(_c(OFF), _c(OFF), True, step, 0, "Europe/Berlin", E2=_c(0), S2=_c(1)).annotation == "disputed"
    # a dependent served check (same lineage) neither confirms nor contradicts a correction
    r = combine(_c(OFF), _c(OFF), False, step, 0, "Europe/Berlin", E2=_c(0), S2=_c(1))
    assert r.annotation == "corrected"


# --- the DST self-test (M6, M7, M8) -----------------------------------------------------------------

def _season(lag_min, amp=0.5, base=100.0):
    return {"win": {"M2": (amp, base)}, "sum": {"M2": (amp, base + SPEED["M2"] * lag_min / 60.0)}}


@pytest.mark.parametrize("lag,want", [(0, "no_step"), (19.9, "no_step"), (-19.9, "no_step"), (20.1, "ambiguous"),
                                      (39.9, "ambiguous"), (40.1, "step"), (61, "step"), (79.9, "step"),
                                      (80.1, "ambiguous"), (-60, "ambiguous")])
def test_dst_check_bands(lag, want):
    assert dst_check(_season(lag), SPEED["M2"], "Europe/Berlin", 2020)["value"] == want


def test_dst_step_needs_a_zone_with_dst_and_a_known_zone():
    assert dst_check(_season(61), SPEED["M2"], "Asia/Tokyo", 2020)["value"] == "ambiguous"
    for z in (None, "", "Not/AZone", "../etc"):
        d = dst_check(_season(0), SPEED["M2"], z, 2020)
        assert d["value"] == NA and d["code"] == "dst_zone_unknown"


def test_dst_check_na_reasons_are_truthful():
    assert dst_check({"win": {"M2": (0.5, 1.0)}}, SPEED["M2"], "UTC", 2020)["code"] == "dst_too_little_data"
    d = dst_check({"win": {"error": "LinAlgError: singular"}, "sum": {"M2": (0.5, 1.0)}}, SPEED["M2"], "UTC", 2020)
    assert d["value"] == NA and d["code"] == "dst_fit_failed" and "singular" in d["why"]
    assert dst_check(_season(0, amp=0.1), SPEED["M2"], "UTC", 2020)["code"] == "dst_microtidal"


def test_zone_has_dst():
    assert zone_has_dst("Europe/Berlin", 2024) is True and zone_has_dst("Asia/Tokyo", 2024) is False
    assert zone_has_dst(None, 2024) is None and zone_has_dst("Not/AZone", 2024) is None


# --- end to end through fit_features() and assess() on synthetic records ---------------------------

@pytest.fixture(scope="module")
def table():
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    if not dump.exists():
        pytest.skip(f"TCD dump not in the cache: {dump}")
    return TcdTable.load(dump)


NAMES = ("M2", "S2", "K1", "O1")
AMPS = np.array([1.0, 0.3, 0.2, 0.15])


def _truth(table, t):
    from gesla.fit import design
    d = design(table, NAMES, np.asarray(t, dtype=np.int64))
    return d[:, 0::2] @ AMPS + d[:, 1::2] @ (AMPS * 0.5)


def _rec(t_stamped, h, lat=50.0, interval=60, name="syn"):
    t = np.asarray(t_stamped, dtype=np.int64)
    return GaugeRecord(record_id=name, source="gesla", source_version="test", contributor="X", originator=True,
                       licence_class="x", lat=lat, lon=5.0, gauge_type="Coastal", sampling="instantaneous",
                       interval_min=interval, declared_time_base=None, member_sha256="0" * 64, times_s=t,
                       heights_m=np.asarray(h, dtype=np.float64), good=np.ones(len(t), dtype=bool))


START = int(np.datetime64("2018-01-01", "s").astype(np.int64))
END = int(np.datetime64("2020-01-01", "s").astype(np.int64))
GRID = np.arange(START, END, 3600, dtype=np.int64)


@pytest.fixture(scope="module")
def truth_refs(table):
    """EOT20 and served references = the unshifted record's own constants (an ideal model and an
    independent, verified served station)."""
    feat = tw.fit_features(_rec(GRID, _truth(table, GRID)), table, (START, END))
    eot = {"dist_km": 1.0, **{k: list(v) for k, v in feat["consts"].items()}}
    served = [ServedRow(Served("T1", "o-1-x-noaa", dict(feat["consts"])), "independent", "")]
    return eot, served


@pytest.mark.parametrize("shift,annotation,label", [(0, "verified", "utc_instant"), (17, "disputed", "utc_instant"),
                                                     (60, "corrected", "utc_plus_1"),
                                                     (-60, "corrected", "utc_minus_1")])
def test_assess_end_to_end_on_shifted_series(table, truth_refs, shift, annotation, label):
    eot, served = truth_refs
    rec = _rec(GRID + shift * 60, _truth(table, GRID))           # stamps later than the truth by `shift` min
    span = (START + shift * 60, END + shift * 60)
    r = tw.assess(rec, table, span, eot=eot, served=served, zone="UTC")
    assert (r.annotation, r.label) == (annotation, label), r.reason


def test_assess_end_to_end_local_dst(table, truth_refs):
    eot, served = truth_refs
    off = pd.DatetimeIndex(GRID.astype("datetime64[s]")).tz_localize("UTC").tz_convert("Europe/Berlin")
    wall = (off.tz_localize(None).values.astype("datetime64[s]").astype(np.int64))     # wall clock as stamps
    r = tw.assess(_rec(wall, _truth(table, GRID)), table, (int(wall[0]), int(wall[-1]) + 1), eot=eot,
                  served=served, zone="Europe/Berlin")
    assert (r.annotation, r.label) == ("corrected", "local_dst:Europe/Berlin"), r.reason


def test_dst_seasons_are_swapped_south_of_the_equator(table):
    """Stamps one hour late in May-Aug only: north of the equator that is a +60 min summer step; south of
    it May-Aug is winter, so the same series gives -60 min (ambiguous), not a step."""
    m = (GRID.astype("datetime64[s]").astype("datetime64[M]").astype(np.int64) % 12) + 1
    stamped = GRID + np.where(np.isin(m, (5, 6, 7, 8)), 3600, 0)
    h = _truth(table, GRID)
    north = tw.fit_features(_rec(stamped, h, lat=50.0), table, (START, END + 3600))
    south = tw.fit_features(_rec(stamped, h, lat=-30.0), table, (START, END + 3600))
    dn = dst_check(north["season"], SPEED["M2"], "Europe/Berlin", 2019)
    ds = dst_check(south["season"], SPEED["M2"], "Europe/Berlin", 2019)
    assert dn["value"] == "step" and dn["season_m2_min"] == pytest.approx(60, abs=3)
    assert ds["value"] == "ambiguous" and ds["season_m2_min"] == pytest.approx(-60, abs=3)


def test_season_length_is_a_duration_not_a_sample_count(table):
    """A 2-hourly record with 60 days in each season: 720 samples but 1,440 hours (>= 45 days)."""
    days = np.concatenate([np.arange(np.datetime64("2019-01-01"), np.datetime64("2019-03-02")),
                           np.arange(np.datetime64("2019-06-01"), np.datetime64("2019-07-31"))])
    t = np.concatenate([np.arange(int(d.astype("datetime64[s]").astype(np.int64)),
                                  int(d.astype("datetime64[s]").astype(np.int64)) + 86400, 7200) for d in days])
    season = tw.season_fits(table, t, _truth(table, t), lat=50.0, interval_min=120)
    assert set(season) == {"win", "sum"} and "M2" in season["win"]
    assert tw.season_fits(table, t[::2], _truth(table, t[::2]), lat=50.0, interval_min=120) == {}   # 360 samples x 2 h = 720 h < 45 days


def test_a_failed_seasonal_fit_is_reported(table, monkeypatch):
    def boom(*a, **k):
        raise np.linalg.LinAlgError("singular matrix")
    monkeypatch.setattr(tw, "lsq", boom)
    feat = tw.fit_features(_rec(GRID, _truth(table, GRID)), table, (START, END))
    d = dst_check(feat["season"], SPEED["M2"], "UTC", 2019)
    assert d["code"] == "dst_fit_failed" and "singular" in d["why"]


@pytest.mark.parametrize("agg", ["GLOSS", "ESEAS"])
def test_gloss_and_eseas_aggregate_national_data_so_they_are_never_independent(agg):
    """The inventory lists GLOSS and ESEAS as originators, but both aggregate national agencies' gauges."""
    lin = {"g-1-grl-x": Lineage(agg, "originator"), "n-2-grl-dmi": Lineage("DMI", "originator")}
    for rec, origin in (("g-1-grl-x", "n-2-grl-dmi"), ("n-2-grl-dmi", "g-1-grl-x")):
        why = lineage_reason(rec, _sv(origin), lin)
        assert why is not None and agg in why


def test_assess_independent_served_only_keeps_independent_rows_and_lists_the_excluded(monkeypatch):
    # Confirm-2 C-1: the filter at the module boundary. A dependent station at 0 h and an independent one at +1 h:
    # without the filter they disagree (served conflict); with it only the independent one counts.
    rows = [tw.ServedRow(tw.Served("TD", "dep-origin", {"M2": (1.0, 0.0)}), "dependent", "same lineage"),
            tw.ServedRow(tw.Served("TI", "ind-origin", {"M2": (1.0, 30.0)}), "independent", "")]
    monkeypatch.setattr(tw, "fit_features", lambda *a, **k: {"consts": {}, "season": {}, "year": 2010})
    monkeypatch.setattr(tw, "speeds", lambda table: {"M2": 28.984104})
    monkeypatch.setattr(tw, "dst_check", lambda *a, **k: {"value": "no_step", "code": "no_step"})

    def phase_check(fit, ref, sp, **k):
        if "dist_km" in k:
            return {"value": 1, "tau": 0.0}
        return {"value": 1 if ref["M2"][1] else 0, "tau": 0.0}

    monkeypatch.setattr(tw, "phase_check", phase_check)
    kw = {"eot": {"dist_km": 1.0}, "served": rows, "zone": "Europe/Oslo", "n_decl": 1}
    both = tw.assess(None, None, (0, 1), **kw)
    assert both.code == "disputed:served_stations_disagree", both.code
    only = tw.assess(None, None, (0, 1), independent_served_only=True, **kw)
    assert only.code == "verified:eot20+served_independent", only.code
    assert only.evidence["S"]["station"] == "TI" and only.evidence["S_independent"] is True
    assert only.evidence["S_excluded"] == [{"station": "TD", "origin": "dep-origin", "kind": "dependent"}]
    assert "S_excluded" not in both.evidence


@pytest.mark.parametrize("exc", [np.linalg.LinAlgError("SVD did not converge"), ValueError("all-NaN slice")])
def test_the_trial_fit_call_site_turns_its_value_and_linalg_errors_into_fit_error(monkeypatch, exc):
    # Fitfix review C M4: the data limits of the trial fit become FitError where the fit is called, so the audit
    # treats them as expected (weak_failed) while any other ValueError stays a crash.
    from gesla.fit import FitError

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(tw, "clean", lambda rec, corr=None: object())
    monkeypatch.setattr(tw, "between", lambda s, a, b: s)
    monkeypatch.setattr(tw, "in_tcd_years", lambda table, s: s)
    monkeypatch.setattr(tw, "analyse", boom)
    rec = type("R", (), {"record_id": "x-1-nor-nhs", "lat": 60.0, "interval_min": 60})()
    with pytest.raises(FitError, match=type(exc).__name__) as e:
        tw.fit_features(rec, None, (0, 3600))
    assert e.value.__cause__ is exc
