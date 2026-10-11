"""Station map and recommendation, fix round 3 (confirm-A/B/C): the narrowed join rule (C1-C6), the
offset sides (B2, A6), the D1 limits from both sides (B3), the rank order (B1, A4, C7), the overrides
(A2), the required build inputs (A5), the flags (A7, C4), and the mint's inputs (A3, A8, A9, B6, B8).
Synthetic data only."""
import json
import subprocess
from pathlib import Path

import pytest

from otc_pipeline import gesla_release as gr
from otc_pipeline import station_map as sm
from otc_pipeline.gesla_release import CURRENT_S, EPOCH_TIE, Candidate, recommend, sibling_flag
from otc_pipeline.gesla_sets import FitIntegrityError
from otc_pipeline.station_map import compare_tides, mint, offset_sides

from test_build_integrity import dump, make_run, table  # noqa: F401 (fixtures)
from test_format_1_0 import fit_dict
from test_station_map import SAME, built, rec, shifted, tide, two

YEAR = 365.25 * 86400


def ids(m):
    return m.record_ids()


# --- C1: clock offsets only at one position or along a registry/UHSLC link within 1 km -------------

def test_a_clock_offset_joins_only_at_one_position_or_on_a_strong_link_within_1_km():
    t = {"a": SAME, "b": shifted(SAME, 0.5)}
    same_pos = [rec("a", 60.0, 5.0), rec("b", 60.001, 5.0)]
    assert len(mint(same_pos, {}, tides=t).stations) == 1
    coord_far = [rec("a", 60.0, 5.0), rec("b", 60.008, 5.0)]               # 0.9 km, coord link only
    m = mint(coord_far, {}, tides=t)
    assert len(m.stations) == 2
    assert m.split_flags["a"]["verdict"] == m.split_flags["b"]["verdict"] == "clock_offset_far"   # 1 v 1: both
    assert len(mint(coord_far, {"a": ["T1"], "b": ["T1"]}, tides=t).stations) == 1      # registry link, 0.9 km
    alias_far = [rec("a", 60.0, 5.0), rec("b", 60.012, 5.0)]               # 1.3 km
    m = mint(alias_far, {"a": ["T1"], "b": ["T1"]}, tides=t)
    assert len(m.stations) == 2 and set(m.split_flags) == {"a", "b"}


def test_a_split_off_offset_record_is_flagged_and_the_larger_side_is_not():
    # Fremantle FFBH: three agreeing records and one 8 h off, 1.35 km away on a coord link
    recs = [rec("f1", -32.05, 115.74, "Fremantle"), rec("f2", -32.05, 115.7401, "Fremantle"),
            rec("f3", -32.0501, 115.74, "Fremantle"), rec("ff", -32.0597, 115.7486, "Fremantle_Fishing_Boat_Harbour")]
    t = {"f1": SAME, "f2": SAME, "f3": SAME, "ff": shifted(SAME, -8.0)}
    m = mint(recs, {}, tides=t)
    assert ids(m)["ff"] != ids(m)["f1"] == ids(m)["f2"] == ids(m)["f3"]
    assert set(m.split_flags) == {"ff"} and m.split_flags["ff"]["clock_offset_h"] == pytest.approx(-8.0, abs=0.01)
    assert set(m.sibling_flags()) == {"ff"}


def test_name_near_needs_equal_names_agreeing_tides_and_identity():
    # Naples FL: "Naples" is contained in the USGS name; the phases fit a 45 min "offset" (an estuary lag)
    recs = [rec("n", 60.0, 5.0, "Naples", "NOAA", code="8725110"),
            rec("g", 60.0, 5.03, "Gordon_River_At_Rowing_Club_Point_Near_Naples_Fl", "USGS", code="2291310")]
    assert len(mint(recs, {}, tides={"n": SAME, "g": shifted(SAME, 0.75)}).stations) == 2
    assert len(mint(recs, {}, tides={"n": SAME, "g": SAME}).stations) == 2      # names not equal
    assert sm.names_equal("Klagshamn", "KlagshamnTG") and not sm.names_equal("Naples", "Naples_Bay")


# --- C2: thresholds beyond 1 km, K1 on short records, the identity floor ------------------------------

def test_beyond_1_km_an_8_to_12_percent_m2_difference_splits_unless_one_uhslc_number_links():
    t = {"a": tide(m2=(1.0, 100.0)), "b": tide(m2=(0.9, 100.0))}     # 10 %
    far = [rec("a", 60.0, 5.0), rec("b", 60.0089, 5.0)]               # 0.99 km: coord
    far2 = [rec("a", 60.0, 5.0), rec("b", 60.0099, 5.005)]            # 1.17 km
    assert len(mint(far, {}, tides=t).stations) == 1
    assert len(mint(far2, {}, tides=t).stations) == 2
    uh = [rec("a", 60.0, 5.0, "Saipan", "UHSLC_FD", code="028"), rec("b", 60.0099, 5.005, "Saipan", "UHSLC_RQ", code="028A")]
    assert len(mint(uh, {}, tides=t).stations) == 1
    assert len(mint(far2, {}, tides={"a": t["a"], "b": tide(m2=(0.93, 100.0))}).stations) == 1   # 7 %


def test_k1_is_compared_only_when_both_records_are_six_months():
    t = {"a": SAME, "b": tide(k1=(0.28, 50.0))}                       # K1 29 %
    recs = [rec("a", 60.0, 5.0), rec("b", 60.001, 5.0)]
    assert len(mint(recs, {}, tides=t, hours={"a": 9000, "b": 9000}).stations) == 2
    assert len(mint(recs, {}, tides=t, hours={"a": 9000, "b": 700}).stations) == 1   # a one-month record
    assert len(mint(recs, {}, tides=t, hours={"a": 4383, "b": 4383}).stations) == 2  # exactly 6 months


def test_the_identity_floor_within_1_km():
    # Antalya (one code), Zuidland (one agency): 14-29 mm M2 differences on a small tide
    t = {"a": tide(m2=(0.137, 100.0)), "b": tide(m2=(0.166, 100.0))}
    one_agency = [rec("a", 51.8, 4.3, "Zuidland", "RWS_HIST", code="ZUIDLD"), rec("b", 51.8, 4.3, "Zuidland_Spui", "RWS", code="ZUI")]
    assert len(mint(one_agency, {}, tides=t).stations) == 1
    others = [rec("a", 51.8, 4.3, "Cypremort_Point", "NOAA", code="8765251"),
              rec("b", 51.8, 4.3, "Cypremort_Point", "USGS", code="7387040")]
    assert len(mint(others, {}, tides={"a": tide(m2=(0.072, 100.0)), "b": tide(m2=(0.085, 100.0))}).stations) == 2
    fenit = [rec("a", 52.27, -9.86, "Fenit", "CMEMS", code="Fen"), rec("b", 52.27, -9.86, "Fenit_OPW_Station", "MI_C", code="FEN")]
    assert len(mint(fenit, {}, tides={"a": tide(m2=(1.472, 100.0)), "b": tide(m2=(1.291, 100.0))}).stations) == 2


# --- C6: microtidal agreement only within 1 km or with one site code --------------------------------

def test_a_microtidal_agreement_does_not_join_beyond_1_km():
    t = {"a": tide(m2=(0.017, 100.0), k1=(0.005, 50.0)), "b": tide(m2=(0.009, 100.0), k1=(0.005, 50.0))}
    far = [rec("a", 46.5, -84.35, "Sault_Ste_Marie_Ontario", "MEDS", code="10980"),
           rec("b", 46.509, -84.355, "Sault_Ste_Marie_Ontario", "MEDS", code="11010")]   # 1.1 km
    assert len(mint(far, {}, tides=t).stations) == 2
    near = [far[0], rec("b", 46.505, -84.35, "Sault_Ste_Marie_Ontario", "MEDS", code="11010")]
    assert len(mint(near, {}, tides=t).stations) == 1


# --- C4: split-off defective records are flagged -----------------------------------------------------

def test_a_same_name_record_split_off_within_1_km_is_flagged():
    recs = [rec("u1", 58.35, 11.93, "Uddevalla", "SMHI", code="2541"), rec("u2", 58.35, 11.93, "Uddevalla", "CMEMS", code="Udd")]
    m = mint(recs, {}, tides={"u1": tide(m2=(0.008, 100.0)), "u2": tide(m2=(0.142, 100.0))})
    assert len(m.stations) == 2 and set(m.split_flags) == {"u1", "u2"}
    crozet = [rec("c1", -46.4, 51.86, "Crozet"), rec("c2", -46.4, 51.86, "Crozet"), rec("c3", -46.4, 51.86, "Crozet")]
    m = mint(crozet, {}, tides={"c1": SAME, "c2": SAME, "c3": tide(m2=(0.75, 100.0))})
    assert set(m.split_flags) == {"c3"} and m.split_flags["c3"]["distance_km"] == 0.0


# --- B2 / A6 / C8: offset sides --------------------------------------------------------------------------

def _station(*rids):
    return {"S": {"records": list(rids)}}


def test_eot20_picks_the_side_at_one_position_even_against_a_larger_class():
    t = {"a": SAME, "b": shifted(SAME, 1.0), "c": shifted(SAME, 1.0)}
    recs = {r: rec(r, 60.0, 5.0) for r in "abc"}
    review = []
    out = offset_sides(_station("a", "b", "c"), t, {"a": 0, "b": 1, "c": 1}, review, recs)
    assert set(out) == {"b", "c"} and out["b"]["decided_by"] == "eot20" and out["b"]["kind"] == "clock_offset"
    assert out["b"]["clock_offset_h"] == pytest.approx(1.0, abs=0.01) and out["b"]["against"] == ["a"]
    out = offset_sides(_station("a", "b", "c"), t, {"a": 1, "b": 0, "c": 0}, review, recs)   # the score's sign
    assert set(out) == {"a"} and out["a"]["clock_offset_h"] == pytest.approx(-1.0, abs=0.01)
    out = offset_sides(_station("a", "b", "c"), t, {}, review, recs)          # no EOT20: the majority
    assert set(out) == {"a"} and out["a"]["decided_by"] == "majority"


def test_eot20_is_not_used_when_the_classes_do_not_meet_at_one_position():
    t = {"a": SAME, "b": shifted(SAME, 1.0), "c": shifted(SAME, 1.0)}
    recs = {"a": rec("a", 60.0, 5.0), "b": rec("b", 60.008, 5.0), "c": rec("c", 60.008, 5.0)}
    out = offset_sides(_station("a", "b", "c"), t, {"a": 0, "b": 1, "c": 1}, [], recs)
    assert set(out) == {"a"} and out["a"]["decided_by"] == "majority"


def test_the_offset_side_is_penalised_for_its_offset_records():
    # A = {a: on time}; B = {b: on time, c and d one hour off}: B is larger, but its offset records count against it
    t = {"a": SAME, "b": shifted(SAME, 1.0), "c": shifted(SAME, 1.0), "d": shifted(SAME, 1.0)}
    recs = {r: rec(r, 60.0, 5.0) for r in "abcd"}
    out = offset_sides(_station(*"abcd"), t, {"a": 0, "b": 0, "c": 1, "d": 1}, [], recs)
    assert set(out) == {"b", "c", "d"} and out["b"]["decided_by"] == "eot20"


def test_a_record_s_kind_is_its_most_common_verdict_against_the_reference():
    # A6: a, b, c agree (a's O1 is off, which the agree test does not see); d is one hour late against b and c,
    # and disagrees with a. The verdict against the whole reference is a clock offset, not a's "disagree".
    t = {"a": {**SAME, "O1": (0.1, 70.0)}, "b": SAME, "c": SAME, "d": shifted(SAME, 1.0)}
    assert compare_tides(t["a"], t["d"])[0] == "disagree" and compare_tides(t["b"], t["d"])[0] == "clock_offset"
    out = offset_sides(_station(*"abcd"), t, {}, [], {r: rec(r, 60.0, 5.0) for r in "abcd"})
    assert set(out) == {"d"} and out["d"]["kind"] == "clock_offset" and out["d"]["clock_offset_h"] == pytest.approx(1.0, abs=0.01)


def test_a_tie_decides_nothing_and_is_listed():
    t = {"a": SAME, "b": shifted(SAME, 1.0)}
    review = []
    assert offset_sides(_station("a", "b"), t, {"a": 0, "b": 0}, review, {r: rec(r, 60.0, 5.0) for r in "ab"}) == {}
    assert review == [{"reason": ["clock_side_undecided"], "station_id": "S", "classes": [["a"], ["b"]]}]


def test_every_member_is_judged_whatever_the_record_ids():
    # A6 (confirm-A exp3): X and Y agree; A-X is a clock offset, A-Y a disagreement. Swap the ids: same result.
    a = SAME
    x = shifted(SAME, 1.14)
    y = {**x, "K1": (x["K1"][0], (x["K1"][1] + 9.5) % 360)}
    assert compare_tides(x, y)[0] == "agree" and compare_tides(a, x)[0] == "clock_offset"
    for xi, yi in (("b", "c"), ("c", "b")):
        t = {"a": a, xi: x, yi: y}
        recs = {r: rec(r, 60.0, 5.0) for r in "abc"}
        out = offset_sides(_station("a", "b", "c"), t, {"a": 0, "b": 1, "c": 1}, [], recs)
        assert set(out) == {"b", "c"}, (xi, yi)
        assert out[xi]["kind"] == "clock_offset" and out[yi]["kind"] in ("clock_offset", "disagree")


# --- B3: each D1 limit from both sides ----------------------------------------------------------------

@pytest.mark.parametrize("m2, k1, verdict", [
    ((0.89, 100.0), (0.2, 50.0), "agree"),          # M2 11 %
    ((0.87, 100.0), (0.2, 50.0), "amplitude_only"),  # M2 13 %
    ((1.0, 100.0), (0.152, 50.0), "agree"),         # K1 24 %
    ((1.0, 100.0), (0.148, 50.0), "amplitude_only"),  # K1 26 %
])
def test_amplitude_limits_from_both_sides(m2, k1, verdict):
    assert compare_tides(tide(), tide(m2=m2, k1=k1))[0] == verdict


def test_phase_limits_from_both_sides():
    assert compare_tides(tide(), tide(m2=(1.0, 109.5)))[0] == "agree"
    assert compare_tides(tide(), tide(m2=(1.0, 111.0)))[0] == "disagree"         # M2 alone moved 11 deg
    assert compare_tides(tide(), tide(k1=(0.2, 61.0)))[0] == "disagree"          # K1 alone moved 11 deg
    assert compare_tides(tide(), tide(k1=(0.2, 59.5)))[0] == "agree"
    small = dict(m2=(0.04, 100.0), k1=(0.2, 50.0))                                # M2 under PHASE_MIN_AMP_M
    assert compare_tides(tide(**small), tide(m2=(0.04, 130.0), k1=(0.2, 50.0)))[0] == "agree"
    big = dict(m2=(0.06, 100.0), k1=(0.2, 50.0))
    assert compare_tides(tide(**big), tide(m2=(0.06, 130.0), k1=(0.2, 50.0)))[0] == "disagree"


def test_the_absolute_floor_and_the_clock_limits():
    micro = tide(m2=(0.009, 100.0), k1=(0.005, 50.0))
    assert compare_tides(micro, tide(m2=(0.018, 100.0), k1=(0.005, 50.0)))[0] == "agree"      # 9 mm
    assert compare_tides(micro, tide(m2=(0.021, 100.0), k1=(0.005, 50.0)))[0] == "amplitude_only"  # 12 mm
    assert compare_tides(SAME, shifted(SAME, 12.9))[0] == "clock_offset"
    assert compare_tides(SAME, shifted(SAME, 13.5))[0] in ("disagree", "clock_offset")
    assert sm.CLOCK_MAX_H == 13.0
    # a residual just over PHASE_DEG after the best clock shift is not a clock offset
    off = shifted(SAME, 2.0)
    off = {**off, "O1": (off["O1"][0], (off["O1"][1] + 11.0) % 360)}
    assert compare_tides(SAME, off)[0] == "disagree"
    off = shifted(SAME, 2.0)
    off = {**off, "O1": (off["O1"][0], (off["O1"][1] + 9.0) % 360)}
    assert compare_tides(SAME, off)[0] == "clock_offset"


def test_the_limits_are_the_calibrated_ones():
    assert sm.AMP_REL == {"M2": 0.12, "K1": 0.25} and sm.AMP_REL_FAR == {"M2": 0.075, "K1": 0.25}
    assert (sm.AMP_ABS_M, sm.IDENTITY_ABS_M, sm.PHASE_DEG, sm.PHASE_MIN_AMP_M, sm.CLOCK_MIN_AMP_M) == (0.01, 0.03, 10.0, 0.05, 0.02)
    assert (sm.SAME_POS_DEG, sm.NEAR_KM, sm.K1_MIN_HOURS) == (0.002, 1.0, 4383.0)


# --- B1 / A4 / C7: the rank order --------------------------------------------------------------------

def cand(rid, *, tier="gauge", short=False, good=50000.0, disputed=False, epoch=100000, last=2.0e9, orig=True,
         commercial=True, suspect=False, m2=(1.0, 100.0), interval=3600, averaged=False, fd=False, status=None):
    cs = {"set_id": f"S/{rid}", "source_record_id": rid, "source_type": tier, "quantity": "water_level",
          "record_span": {"good_hours": good},
          "time_base": {"code": "disputed" if disputed else "verified",
                        "published": "utc_mean_start60" if averaged else "utc_instant"},
          "qc_flags": [{"flag": "short_record", "reason": "x"}] if short else [],
          "constituents": [{"name": "M2", "amplitude_m": m2[0], "phase_deg": m2[1]},
                           {"name": "K1", "amplitude_m": 0.2, "phase_deg": 50.0}]}
    if status:
        cs["qc_status"] = status
    return Candidate(cs, epoch, int(last), originator=orig, commercial=commercial, suspect=suspect,
                     interval_s=interval, fast_delivery=fd)


def pick(*cands):
    win, how = recommend(list(cands))
    return (win.cs["source_record_id"] if win else None), how["decided_by"]


def test_the_rank_steps_are_the_published_ones():
    assert gr.RANK_STEPS == ("tier", "time_base", "short_record", "instantaneous", "sub_hourly", "current", "originator",
                             "commercial", "epoch_hours", "recency", "research_quality", "record_id")
    assert CURRENT_S == 2 * YEAR and EPOCH_TIE == 0.02


@pytest.mark.parametrize("winner, loser, step", [
    # each pair: the winner is better at `step` and worse at the next step, so swapping the two steps flips it
    (dict(tier="official", disputed=True), dict(), "tier"),
    (dict(short=True, good=60000), dict(disputed=True, good=50000), "time_base"),
    (dict(averaged=True), dict(short=True, good=60000), "short_record"),
    (dict(interval=3600), dict(averaged=True, interval=600), "instantaneous"),
    (dict(interval=600, last=2.0e9 - 3 * YEAR), dict(interval=3600), "sub_hourly"),
    (dict(orig=False), dict(last=2.0e9 - 3 * YEAR), "current"),
    (dict(commercial=False), dict(orig=False), "originator"),
    (dict(epoch=10), dict(commercial=False, epoch=100000), "commercial"),
    (dict(epoch=100000, last=1.95e9), dict(epoch=90000, last=2.0e9), "epoch_hours"),
    (dict(last=2.0e9, fd=True), dict(last=1.99e9), "recency"),
    (dict(fd=False), dict(fd=True), "research_quality"),
])
def test_each_step_comes_before_the_next(winner, loser, step):
    # B1: the loser's id sorts first, so only the steps decide
    assert pick(cand("b", **winner), cand("a", **loser)) == ("b", step)


def test_the_current_and_epoch_tie_boundaries():
    assert pick(cand("b", epoch=150000), cand("a", epoch=160000, last=2.0e9 - 1.9 * YEAR))[1] == "epoch_hours"
    assert pick(cand("b", epoch=150000), cand("a", epoch=160000, last=2.0e9 - 2.1 * YEAR)) == ("b", "current")
    assert pick(cand("b", epoch=100000, last=2.0e9), cand("a", epoch=101900, last=1.95e9)) == ("b", "recency")   # 1.9 %
    assert pick(cand("b", epoch=100000, last=2.0e9), cand("a", epoch=102100, last=1.95e9)) == ("a", "epoch_hours")  # 2.1 %


def test_rq_before_fd_on_an_exact_tie():
    # C7: "192" < "192c" put the fast-delivery copy first on a record-id tie
    assert pick(cand("pemba-192-tza-uhslc_fd", fd=True), cand("pemba-192c-tza-uhslc_rq")) == ("pemba-192c-tza-uhslc_rq",
                                                                                          "research_quality")


def test_an_official_reference_settles_a_disagreement():
    off = cand("o", tier="official", disputed=True, m2=(1.0, 100.0))
    a = cand("a", m2=(0.6, 100.0), epoch=166000)
    b = cand("b", m2=(0.98, 101.0), epoch=100000)
    win, how = recommend([off, a, b])
    assert how["decided_by"] == "tier" and win is off       # an official set wins its tier, disputed or not
    sh = cand("o", tier="official", short=True, good=100, m2=(1.0, 100.0))   # check 2 makes it ineligible
    win, how = recommend([sh, a, b])
    assert win.cs["source_record_id"] == "b" and how["decided_by"] == "official_reference" and how["overridden"] == "S/a"


def test_the_sibling_override_never_picks_a_suspect_over_a_clean_set():
    # A2 (confirm-A exp1): one clean set and three suspects one hour late
    w = cand("a-w", epoch=166000)
    sus = [cand(f"b-s{i}", suspect=True, m2=(1.0, 129.0), epoch=100000) for i in range(3)]
    win, how = recommend([w] + sus)
    assert win is w and how["overridden"] is None
    clean = [cand(f"c{i}", m2=(1.0, 129.0), epoch=100000) for i in range(3)]
    win, how = recommend([w] + clean)                         # clean siblings may overrule it
    assert how["decided_by"] == "sibling_agreement" and how["overridden"] == "S/a-w"


# --- A7: a flag's reason names its values --------------------------------------------------------------

def test_flag_reasons_match_their_values():
    r, v = sibling_flag({"kind": "amplitude_only", "clock_offset_h": -0.97, "against": ["x"], "decided_by": "eot20"})
    assert v == {} and "amplitudes disagree" in r and "clock" not in r
    r, v = sibling_flag({"kind": "clock_offset", "clock_offset_h": -8.09, "against": ["x"], "decided_by": "eot20"})
    assert v == {"clock_offset_h": -8.09} and "-8.09 h (clock_offset_h)" in r
    r, v = sibling_flag({"kind": "split_off", "verdict": "clock_offset_far", "clock_offset_h": 1.0, "distance_km": 1.35,
                         "against": ["x"]})
    assert v == {"distance_km": 1.35, "clock_offset_h": 1.0} and "(distance_km)" in r and "(clock_offset_h)" in r
    r, v = sibling_flag({"kind": "split_off", "verdict": "tides_disagree", "clock_offset_h": None, "distance_km": 0.0,
                         "against": ["x"]})
    assert v == {"distance_km": 0.0} and "disagree" in r


# --- A5: the build's safety inputs are required and the fit run is the map's --------------------------

def test_build_release_requires_the_sibling_flags_tombstones_and_the_fit_run_pin(table):  # noqa: F811
    import inspect
    params = inspect.signature(gr.build_release).parameters
    for name in ("sibling_flags", "tombstones", "station_map_inputs", "fit_run_results_sha256"):
        assert params[name].default is inspect.Parameter.empty, name
    with pytest.raises(ValueError, match="minted from fit run results"):
        built(table, two(table), (1, 2), station_map_inputs={"fit_run_results_sha256": "e" * 64})


def test_a_split_off_set_is_flagged_in_the_release(table):  # noqa: F811
    docs = two(table)
    flags = {"r1-nor-test": {"kind": "split_off", "verdict": "tides_disagree", "against": ["r9"], "distance_km": 0.2,
                             "clock_offset_h": None, "station_id": "OTC-ONE"}}
    (st,) = built(table, docs, (100, 9000), sibling_flags=flags)["stations"]
    cs = next(c for c in st["constant_sets"] if c["source_record_id"] == "r1-nor-test")
    assert "sibling_disagreement" in [f["flag"] for f in cs["qc_flags"]] and cs["qc_status"] == "flagged"
    assert st["recommended_set_id"].endswith("r0-nor-test")


# --- A3 / B8 / B6 / A8 / A9: the mint's inputs ----------------------------------------------------------

def _derived(tmp_path, dump, fits, **kw):  # noqa: F811
    run, audit, rel = make_run(tmp_path, dump, fits, [], **kw)
    wi = tmp_path / "weak_inputs.json"
    wi.write_text(json.dumps({"records": {}}))
    return sm.derived_inputs(run, wi, rel, audit_dir=audit, tcd=dump), run, rel


def test_the_mint_reads_a_checked_fit_run_and_pins_it(tmp_path, dump):  # noqa: F811
    (tides, hours, eot20, pins, texts), run, rel = _derived(tmp_path, dump, {"x-1-nor-test": fit_dict()})
    assert set(tides) == {"x-1-nor-test"} and tides["x-1-nor-test"]["M2"] == (1.0, 100.0)
    assert set(pins) == {"fit_run_results_sha256", "fit_run_manifest_sha256", "audit_timebase_sha256",
                         "weak_inputs_sha256", "tide_summary_sha256", "eot20_lag_sha256"}
    row = texts["gesla_tide_summary.csv"].splitlines()[1].split(",")
    assert row[1] == rel.members["x-1-nor-test"]["sha256"]                 # B8: the member the run fitted
    manifest = json.loads((run / "timebase_manifest.json").read_text())
    assert row[2] == manifest["fits_sha256"]["x-1-nor-test"]


@pytest.mark.parametrize("kw, needle", [(dict(status="partial"), "status 'partial'"), (dict(dirty=True), "dirty True"),
                                        (dict(fits_map=False), "no fits_sha256")])
def test_the_mint_refuses_a_fit_run_the_build_would_refuse(tmp_path, dump, kw, needle):  # noqa: F811
    with pytest.raises(FitIntegrityError, match=needle):
        _derived(tmp_path, dump, {"x-1-nor-test": fit_dict()}, **kw)


def test_a_duplicate_results_row_is_refused(tmp_path, dump):  # noqa: F811
    run, audit, rel = make_run(tmp_path, dump, {"x-1-nor-test": fit_dict()}, [])
    p = run / "results.jsonl"
    p.write_text(p.read_text() * 2)
    m = json.loads((run / "timebase_manifest.json").read_text())
    import hashlib
    m["outputs"]["results.jsonl"] = hashlib.sha256(p.read_bytes()).hexdigest()
    (run / "timebase_manifest.json").write_text(json.dumps(m))
    wi = tmp_path / "wi.json"
    wi.write_text('{"records": {}}')
    with pytest.raises(FitIntegrityError, match="more than one row"):
        sm.derived_inputs(run, wi, rel, audit_dir=audit, tcd=dump)


def test_derived_out_is_refused_inside_any_git_work_tree(tmp_path):
    repo = tmp_path / "other-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert sm.inside_git_work_tree(repo / "data" / "otc") == str(repo.resolve())
    plain = tmp_path / "plain"              # A5 (fix-r6): the test makes its own trees; it passes outside git too
    plain.mkdir()
    assert sm.inside_git_work_tree(plain / "x") is None or not sm.inside_git_work_tree(plain / "x").startswith(str(tmp_path.resolve()))


class _FakeRel:
    version = "4.1"
    metadata_path = "meta.csv"
    files = {"meta.csv": {"sha256": "m" * 64}}
    members = {}

    def metadata(self):
        return {"a-1-nor-test": {"SITE NAME": "Harbour", "CONTRIBUTOR (ABBREVIATED)": "A", "COUNTRY": "NOR",
                                 "LATITUDE": "60.0", "LONGITUDE": "5.0", "SITE CODE": "1"}}


@pytest.fixture
def mint_cli(tmp_path, monkeypatch):
    """main('mint ...') on a fake GESLA release and fake derived inputs, writing under tmp_path."""
    import gesla.read_gesla as rg
    monkeypatch.setattr(rg, "GeslaRelease", lambda: _FakeRel())
    data = tmp_path / "data"
    data.mkdir()
    for name in ("MAP_PATH", "REVIEW_PATH", "DECISIONS_PATH", "EPOCH_PATH", "REGISTRY_MAP_PATH"):
        monkeypatch.setattr(sm, name, data / getattr(sm, name).name)
    (data / "station_decisions.json").write_text('{"merge": [], "separate": []}')
    (data / "station_map.csv").write_text("id,file_id,match,origin_40\n")
    monkeypatch.setattr(sm, "REPO", tmp_path)
    state = {"pins": {"fit_run_results_sha256": "1" * 64, "audit_timebase_sha256": "a" * 64}, "epoch_audit": "a" * 64}

    def fake_derived(fit_run, weak_inputs, release, *, audit_dir, tcd=None):
        state["tcd"] = tcd
        return {}, {}, {}, dict(state["pins"]), {"gesla_tide_summary.csv": "x\n", "gesla_eot20_lag.csv": "y\n"}
    monkeypatch.setattr(sm, "derived_inputs", fake_derived)
    from otc_pipeline import gesla_sets as gs
    monkeypatch.setattr(gs, "load_tcd_dump", lambda p: ("dump of", Path(p)))

    def run(*extra):
        sm._write_csv(sm.EPOCH_PATH, sm.EPOCH_COLUMNS, [["a-1-nor-test", "s" * 64, 10, "utc_instant", state["epoch_audit"]]])
        return sm.main(["mint", "--fit-run", "R", "--audit-dir", "A", "--weak-inputs", "W", *extra])
    return run, state, data


def test_mint_check_reports_each_moved_input(mint_cli, capsys):
    run, state, data = mint_cli
    assert run() == 0
    assert run("--check") == 0 and "station map unchanged" in capsys.readouterr().out
    state["pins"]["fit_run_results_sha256"] = "2" * 64
    assert run("--check") == 1
    out = capsys.readouterr().out
    assert f"input fit_run_results_sha256: map pins {'1' * 64}, now {'2' * 64}" in out and "would change" in out


def test_mint_reads_the_tcd_dump_it_is_given(mint_cli):
    # the OTC TCD's fit dump (FIX-CONST): the fit run's TCD, not $OTC_WORK/tcd_table.json, which check_fit_run refuses
    run, state, data = mint_cli
    assert run("--tcd", "/x/tcd-1900-2100.json") == 0 and state["tcd"] == ("dump of", Path("/x/tcd-1900-2100.json"))
    assert run() == 0 and state["tcd"] is None


def test_mint_refuses_derived_out_in_a_work_tree_and_an_epoch_csv_of_another_audit(mint_cli, tmp_path):
    run, state, data = mint_cli
    repo = tmp_path / "wt"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(SystemExit, match="inside the git work tree"):
        run("--derived-out", str(repo / "x"))
    assert not (repo / "x").exists()
    state["epoch_audit"] = "b" * 64
    with pytest.raises(SystemExit, match="counted with audit"):
        run()


# --- fix-r4 decision 2: clock-offset-only stations are withheld ------------------------------------

def test_a_station_whose_every_set_has_a_clock_offset_flag_is_refused_and_reported(table):  # noqa: F811
    docs = two(table)
    ffbh = {"kind": "split_off", "verdict": "clock_offset_far", "clock_offset_h": -8.1, "distance_km": 1.35,
            "against": ["fremantle-62230-aus-bom"], "station_id": "OTC-ONE"}
    sid = {d["record_id"]: "OTC-ONE" for d in docs}
    eh = {d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 1} for d in docs}
    with pytest.raises(ValueError, match="no record is publishable"):
        from test_format_1_0 import build
        build(table, docs, station_ids=sid, epoch_hours=eh, sibling_flags={d["record_id"]: ffbh for d in docs})
    # one clean set in the station: published, the flagged one stays an alternate
    release = built(table, docs, (1, 2), sibling_flags={"r1-nor-test": ffbh})
    assert [cs["source_record_id"] for cs in release["stations"][0]["constant_sets"]] == ["r0-nor-test", "r1-nor-test"]
    # a station of its own with only a clock-offset set is withheld; an amplitude-only flag is not
    other = {d["record_id"]: f"OTC-{i}" for i, d in enumerate(docs)}
    release, report = gr.build_release(docs, **_kw(table, docs, other, {"r1-nor-test": ffbh}))
    assert [s["station_id"] for s in release["stations"]] == ["OTC-0"]
    (r,) = [x for x in report.refused if x["reason"] == "clock_offset_unresolved"]
    assert r["record"] == "r1-nor-test" and "OTC-1" in r["message"] and "-8.10 h" in r["message"]
    assert report.published == ["r0-nor-test"] and report.counts() == {"clock_offset_unresolved": 1}
    amp = {"kind": "amplitude_only", "clock_offset_h": None, "against": ["x"], "decided_by": "eot20"}
    release, report = gr.build_release(docs, **_kw(table, docs, other, {"r1-nor-test": amp}))
    assert len(release["stations"]) == 2 and report.counts() == {}


def _kw(table, docs, station_ids, flags):  # noqa: F811
    from synthetic_congen import congen_run, kp0b_tables
    from test_format_1_0 import LICENCE, SPEEDS, TABLE_ID
    return dict(table=table, table_id=TABLE_ID, datestamp="20261010", created="2026-10-10T00:00:00Z",
                build_commit="abcdef0", station_ids=station_ids, licences=[LICENCE],
                licence_ids={d["record_id"]: "gesla-derived" for d in docs}, withheld={}, refused=[],
                epoch_hours={d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 1} for d in docs},
                contributor_roles={d["contributor"]: "originator" for d in docs}, sibling_flags=flags, tombstones=[],
                station_map_inputs={"fit_run_results_sha256": "f" * 64}, fit_run_results_sha256="f" * 64, disputed_reasons={},
                sampling_s={d["record_id"]: 3600 for d in docs},
                astro_tables=kp0b_tables(congen_run(table), SPEEDS, TABLE_ID), congen_run=congen_run(table))


# --- fix-r5: EOT20 picks the side of a refused clock offset only on a clear verdict ---------------

def _dakar(eot):
    # three on-time records and three one hour off, 1.6 km apart on a coord + registry link: a refused clock offset
    clean = [rec(f"c{i}", 14.68, -17.42, "Dakar") for i in range(3)]
    off = [rec(f"o{i}", 14.69, -17.43, "Dakar") for i in range(3)]
    t = {**{r.record_id: SAME for r in clean}, **{r.record_id: shifted(SAME, -1.0) for r in off}}
    reg = {r.record_id: ["T1"] for r in clean + off}
    return mint(clean + off, reg, tides=t, eot20=eot)


def test_a_clear_eot20_verdict_flags_only_the_offset_side():
    m = _dakar({**{f"c{i}": (0, -0.5) for i in range(3)}, **{f"o{i}": (-1, -59.5) for i in range(3)}})
    assert set(m.split_flags) == {"o0", "o1", "o2"}


@pytest.mark.parametrize("case, eot", [
    ("clean side off zero by more than 10 min", {**{f"c{i}": (0, 12.0) for i in range(3)}, **{f"o{i}": (-1, -60.0) for i in range(3)}}),
    ("offset side off a whole hour by more than 10 min", {**{f"c{i}": (0, 0.0) for i in range(3)}, **{f"o{i}": (None, -45.0) for i in range(3)}}),
    ("offset side at zero", {**{f"c{i}": (0, 0.0) for i in range(3)}, **{f"o{i}": (0, 3.0) for i in range(3)}}),
    ("clean side fails the amplitude gate", {**{f"c{i}": (None, None) for i in range(3)}, **{f"o{i}": (-1, -60.0) for i in range(3)}}),
    ("offset side fails the amplitude gate", {**{f"c{i}": (0, 0.0) for i in range(3)}, **{f"o{i}": (None, None) for i in range(3)}}),
    ("offset side on two different hours", {**{f"c{i}": (0, 0.0) for i in range(3)}, "o0": (-1, -60.0), "o1": (-2, -120.0), "o2": (-1, -60.0)}),
])
def test_without_a_clear_eot20_verdict_both_sides_stay_flagged(case, eot):
    m = _dakar(eot)
    assert set(m.split_flags) == {"c0", "c1", "c2", "o0", "o1", "o2"}, case


def test_eot20_clear_side_boundaries():
    e = {"c": (0, 10.0), "o": (-1, -50.0)}
    assert sm.eot20_clear_side(["c"], ["o"], e, -1.0) == 1 and sm.eot20_clear_side(["o"], ["c"], e, 1.0) == 0
    assert sm.eot20_clear_side(["c"], ["o"], {"c": (0, 10.5), "o": (-1, -60.0)}, -1.0) is None
    assert sm.eot20_clear_side(["c"], ["o"], {"c": (0, 0.0), "o": (-1, -49.5)}, -1.0) is None
    assert sm.eot20_clear_side(["c", "x"], ["o"], {"c": (0, 0.0), "x": (None, None), "o": (1, 61.0)}, 1.0) == 1


def test_eot20_must_match_the_pair_s_measured_offset():
    # A4 (fix-r6): EOT20 says one hour, the pair's phases say two (or the other sign): no clear verdict
    e = {"c": (0, 0.0), "o": (-1, -60.0)}
    assert sm.eot20_clear_side(["c"], ["o"], e, -1.0) == 1
    assert sm.eot20_clear_side(["c"], ["o"], e, -1.15) == 1          # 9 min apart
    assert sm.eot20_clear_side(["c"], ["o"], e, -1.2) is None        # 12 min apart
    assert sm.eot20_clear_side(["c"], ["o"], e, -2.0) is None
    assert sm.eot20_clear_side(["c"], ["o"], e, 1.0) is None         # the other sign
    assert sm.eot20_clear_side(["c"], ["o"], e, None) is None


def test_a_side_without_a_gated_lag_never_qualifies():
    # A2 (fix-r6): a group with no gate-passing lag is never the on-time side, on either side
    assert sm.eot20_clear_side(["c"], ["o"], {"c": (None, None), "o": (-1, -60.0)}, -1.0) is None
    assert sm.eot20_clear_side(["c"], ["o"], {"c": (0, 0.0), "o": (None, None)}, -1.0) is None


def test_the_eot20_side_rule_is_only_for_clock_offsets():
    # a same-name split for disagreeing amplitudes keeps both sides flagged, whatever EOT20 says
    recs = [rec("u1", 58.35, 11.93, "Uddevalla", "SMHI", code="2541"), rec("u2", 58.35, 11.93, "Uddevalla", "CMEMS", code="Udd")]
    m = mint(recs, {}, tides={"u1": tide(m2=(0.008, 100.0)), "u2": tide(m2=(0.142, 100.0))},
             eot20={"u1": (0, 0.0), "u2": (-1, -60.0)})
    assert set(m.split_flags) == {"u1", "u2"}
