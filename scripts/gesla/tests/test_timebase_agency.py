"""FIX-LST: a record's fitted constants against nearby agency harmonic constants (timebase_agency), and how the audit
turns that evidence into verdicts (timebase_audit.apply_agency)."""

from __future__ import annotations

import json

import pytest

from gesla import timebase_agency as tag
from gesla import timebase_audit as ta
from gesla.timebase_audit import AuditRow, RecordInfo, SiblingConfig, WeakConfig
from gesla.timebase_verdict import NOT_USABLE, Correction
from gesla.timebase_weak import WeakInputs, WeakResult

SP = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356}
REF = {"M2": (0.6, 100.0), "S2": (0.25, 140.0), "K1": (0.2, 200.0), "O1": (0.15, 180.0)}
KM = 1.0 / 111.19          # degrees of latitude per km


def cons(minutes: float, amp: float = 1.0) -> dict:
    """REF's tide stamped `minutes` late (the record's tide `minutes` later than the reference's)."""
    return {k: (a * amp, (g + SP[k] * minutes / 60.0) % 360.0) for k, (a, g) in REF.items()}


def ref(rid: str, km: float, minutes: float = 0.0, amp: float = 1.0) -> tag.AgencyRef:
    """An agency station `km` north of (10, 20) whose tide is `minutes` later than REF."""
    return tag.AgencyRef("noaa", rid, 10.0 + km * KM, 20.0, cons(minutes, amp))


def eot(record_minutes: float, lag: float | None) -> dict | None:
    """EOT20 at the record: the record (REF + record_minutes) lags it by `lag` minutes."""
    if lag is None:
        return None
    return {**{k: [a, g] for k, (a, g) in cons(record_minutes - lag).items()}, "dist_km": 3.0}


UNV = ("unverified", "none", NOT_USABLE)


class _Ex:
    """ex.map stand-in: _sibling_confirm_task answers from a table (pass 2 and the weak result per record)."""

    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    def map(self, fn, rids, ns, refs, ost, indep):
        assert fn is ta._sibling_confirm_task
        out = []
        for rid, n, rf, i in zip(rids, ns, refs, indep):
            self.calls.append((rid, n, len(rf), i))
            out.append((rid, [], *self.answers[rid]))
        return out


def _setup(spec: dict, eots: dict | None = None):
    """spec: id -> (minutes the record's tide is late against REF, row spec (status, kind, correction))."""
    inv, rows, fits = {}, {}, {}
    for f, (mins, (status, kind, corr)) in spec.items():
        inv[f] = RecordInfo(f, f.rsplit("-", 1)[-1].upper(), 3600, 10.0, 20.0, 0, 10 ** 9)
        fits[f] = {"record": f, "outcome": "fitted", "lat": 10.0, "lon": 20.0, "gauge_type": "Coastal",
                   "time_base": "utc_instant", "main": {k: [a, g, 0.0] for k, (a, g) in cons(mins).items()}}
        rows[f] = AuditRow(f, True, inv[f].contributor, 3600, status, corr.label if corr.usable else "no_comparator",
                           corr.label, corr, None, kind, "", reason="r")
    wk = WeakConfig(WeakInputs({f: {"eot": eot(spec[f][0], (eots or {}).get(f)), "zone": None} for f in spec}), {}, {})
    return inv, rows, SiblingConfig(fits, {}), wk


def _apply(inv, rows, cfg, wk, refs, ex=None):
    dec = {f: 0 for f in rows}
    ex = ex or _Ex()
    return ta.apply_agency(rows, inv, cfg, wk, dec, {}, SP, ex, lambda f, p=False: {}, dec, tag.RefIndex(refs),
                           lambda *_: None), ex


# --- the shift -----------------------------------------------------------------------------------

def test_shift_is_the_smallest_lead_over_the_references_and_mixed_signs_give_none():
    near = [(ref("A", 1.0), 1.0), (ref("B", 8.0, minutes=+40.0), 8.0)]     # B's tide 40 min later than A's
    sh = tag.shift(cons(-300.0), near, SP)
    assert sh["tau"] == pytest.approx(-300.0, abs=0.6) and len(sh["refs"]) == 2          # -300 vs A, -340 vs B
    mixed = [(ref("A", 1.0), 1.0), (ref("C", 8.0, minutes=-200.0), 8.0)]
    assert tag.shift(cons(-100.0), mixed, SP)["tau"] == 0.0                                # -100 vs A, +100 vs C


def test_a_reference_in_other_water_is_left_out():
    near = [(ref("A", 1.0, amp=3.0), 1.0)]                                 # M2 ratio 1/3: outside AG_RATIO
    assert tag.shift(cons(-300.0), near, SP) is None
    bad = dict(cons(-300.0))
    bad["K1"] = (bad["K1"][0], bad["K1"][1] + SP["K1"] * 90.0 / 60.0)      # K1 90 min off the joint shift
    assert tag.shift(bad, [(ref("A", 1.0), 1.0)], SP) is None


def test_ref_index_finds_stations_by_distance():
    diag = tag.AgencyRef("noaa", "D", 10.0 + 11.0 * KM, 20.0 + 11.0 * KM / 0.9848, REF)     # 11 km N, 11 km E: 15.6 km
    idx = tag.RefIndex([ref("A", 1.0), ref("B", 14.0), ref("C", 16.0), diag])
    assert [r.id for r, _ in idx.near(10.0, 20.0)] == ["A", "B"]


def q(tau, km=2.5, ratio=1.0, resid=5.0, spread=8.0, ref="noaa:A"):
    return {"ref": ref, "tau": tau, "eff": tau, "via": "same water", "km": km, "ratio": ratio, "resid": resid,
            "spread": spread}


@pytest.mark.parametrize("tau, e, kind", [(-300.0, None, "lead"), (-121.0, None, "lead"), (-100.0, None, "none"),
                                          (+300.0, None, "none"), (+88.5, 85.0, "agreed"), (+88.5, 60.0, "none"),
                                          (-50.0, -45.0, "agreed"), (-30.0, -30.0, "none")])
def test_classes(tau, e, kind):
    assert tag.classify({"tau": tau, "refs": [q(tau)]}, e)["kind"] == kind


def test_an_agreed_offset_needs_a_uniform_undamped_shift():
    # Settlement Point 257: M2 +86 .. O1 +112 (spread 26), M2 ratio 0.84: the relocated gauge's response, not a clock
    assert tag.classify({"tau": 88.5, "refs": [q(88.5, ratio=0.84, spread=26.0)]}, 85.0)["kind"] == "none"
    assert tag.classify({"tau": 88.5, "refs": [q(88.5, ratio=0.84, spread=8.0)]}, 85.0)["kind"] == "none"
    assert tag.classify({"tau": 88.5, "refs": [q(88.5, ratio=0.95, spread=26.0)]}, 85.0)["kind"] == "none"
    assert tag.classify({"tau": 88.5, "refs": [q(88.5, ratio=0.95, spread=8.0)]}, 85.0)["kind"] == "agreed"


def test_a_lag_both_sources_see_from_far_off_is_physical_not_agreed():
    # AIW at Nixons Crossroads SC: +54 against NOAA 13.8 km away, EOT20 +52: the waterway's lag, not a clock
    far = {"tau": 54.0, "refs": [{"ref": "noaa:A", "tau": 54.0, "km": 13.8, "ratio": 0.9, "resid": 8.0}]}
    assert tag.classify(far, 52.0)["kind"] == "none"


def test_a_whole_hour_needs_every_reference_eot20_and_a_reference_in_the_same_water():
    same = q(-301.0, km=1.0)
    far = q(-241.0, km=11.0, ratio=0.8, resid=30.0, ref="noaa:B")
    assert tag.classify({"tau": -301.0, "refs": [same]}, -298.0)["hours"] == -5
    assert tag.classify({"tau": -301.0, "refs": [same]}, None)["hours"] is None            # one source
    assert tag.classify({"tau": -301.0, "refs": [dict(same, km=5.0)]}, -298.0)["hours"] is None   # not the same water
    # Miami S25: EST read as -4 h against a far reference, EOT20 agreeing; the near one says -5: never corrected
    assert tag.classify({"tau": -241.0, "refs": [dict(same, tau=-287.0), far]}, -245.0)["hours"] is None


# --- apply_agency ---------------------------------------------------------------------------------

def test_a_record_in_local_standard_time_is_disputed_on_its_own_lead():
    # SFWMD: stamped EST, published utc_instant; NOAA stations 4 and 9 km off (one up the canal, 60 min later)
    inv, rows, cfg, wk = _setup({"s123-s123-usa-sfwmd": (-300.0, UNV)})
    (crashes, counts), ex = _apply(inv, rows, cfg, wk, [ref("A", 4.0), ref("B", 9.0, minutes=60.0)])
    r = rows["s123-s123-usa-sfwmd"]
    assert r.status == "residue" and r.comparator_id == ta.AGENCY_DISPUTED and not r.correction.usable
    assert not ex.calls and counts["lead"] == 1 and counts["disputed"] == 1 and not crashes
    ta.annotate(r, 0)
    assert r.annotation == "disputed" and r.annotation_code == "disputed:agency_constants_offset"
    assert r.agency["tau_min"] == pytest.approx(-300.0, abs=0.6) and "leads every agency station" in r.reason
    assert ta.csv_row(r)["agency_constants"].startswith("agency constants")


def test_a_river_lag_is_never_flagged_and_a_strong_record_is_not_judged():
    spec = {"hudson-1-usa-usgs": (+250.0, UNV),                                    # far up a river: it lags
            "battery-8518750-usa-noaa": (-300.0, ("used", "c", Correction("utc_instant")))}
    inv, rows, cfg, wk = _setup(spec)
    (_, counts), _ = _apply(inv, rows, cfg, wk, [ref("A", 3.0)])
    assert rows["hudson-1-usa-usgs"].status == "unverified" and not rows["hudson-1-usa-usgs"].agency
    assert rows["battery-8518750-usa-noaa"].status == "used" and counts["bound"] == 1


def test_a_lag_agreed_by_eot20_is_disputed_not_corrected():
    # Settlement Point 257: +88 against NOAA 9710441 (2.6 km), EOT20 +85
    inv, rows, cfg, wk = _setup({"settlement_point-257-bhs-uhslc_fd": (88.0, UNV)},
                                eots={"settlement_point-257-bhs-uhslc_fd": 85.0})
    _apply(inv, rows, cfg, wk, [ref("A", 2.6)])
    r = rows["settlement_point-257-bhs-uhslc_fd"]
    assert r.status == "residue" and r.comparator_id == ta.AGENCY_DISPUTED and r.agency["rule"] == "agreed"


def test_a_lead_inside_the_known_good_envelope_is_left_alone():
    inv, rows, cfg, wk = _setup({"vienna-8571773-usa-x": (-60.0, UNV)})                 # EOT20 says 0
    inv2, rows2, cfg2, wk2 = _setup({"vienna-8571773-usa-x": (-60.0, UNV)}, eots={"vienna-8571773-usa-x": 0.0})
    for a in ((inv, rows, cfg, wk), (inv2, rows2, cfg2, wk2)):
        _apply(*a, [ref("A", 5.0)])
        assert a[1]["vienna-8571773-usa-x"].status == "unverified"


def test_two_sources_on_one_whole_hour_in_the_same_water_are_corrected_after_pass_2():
    f = "mrms4-mrms4-usa-sfwmd"
    inv, rows, cfg, wk = _setup({f: (-300.0, UNV)}, eots={f: -300.0})
    ok = WeakResult("unverified", "utc_minus_5", "unverified:weak_insufficient", "x")
    ex = _Ex({f: ([], {"pass2": {"against": "references", "ok": True}, "weak": ok, "span": (0, 1)})})
    (_, counts), ex = _apply(inv, rows, cfg, wk, [ref("A", 1.2)], ex)
    r = rows[f]
    assert ex.calls == [(f, -5, 1, True)]
    assert r.status == "used" and r.correction.label == "utc_minus_5"
    assert r.comparator_id == "corrected:utc_instant->utc_minus_5:agency_constants+eot20" and counts["corrected"] == 1
    ta.annotate(r, 0)
    assert r.annotation_code == "corrected:utc_instant->utc_minus_5:agency_constants+eot20"


@pytest.mark.parametrize("p2_ok, weak", [(False, WeakResult("unverified", "utc_minus_5", "unverified:x", "y")),
                                         (True, WeakResult("disputed", "utc_instant", "disputed:x", "y")),
                                         (True, WeakResult("verified", "utc_instant", "verified:x", "y"))])
def test_pass_2_or_the_own_weak_checks_make_the_correction_yield_to_disputed(p2_ok, weak):
    f = "mrms4-mrms4-usa-sfwmd"
    inv, rows, cfg, wk = _setup({f: (-300.0, UNV)}, eots={f: -300.0})
    ex = _Ex({f: ([], {"pass2": {"against": "references", "ok": p2_ok}, "weak": weak, "span": (0, 1)})})
    _apply(inv, rows, cfg, wk, [ref("A", 1.2)], ex)
    assert rows[f].status == "residue" and rows[f].comparator_id == ta.AGENCY_DISPUTED


def test_a_crash_in_pass_2_is_disputed_and_reported():
    f = "mrms4-mrms4-usa-sfwmd"
    inv, rows, cfg, wk = _setup({f: (-300.0, UNV)}, eots={f: -300.0})
    ex = _Ex({f: (["boom"], {"failed": True, "error_type": "KeyError"})})
    (crashes, _), _ = _apply(inv, rows, cfg, wk, [ref("A", 1.2)], ex)
    assert crashes == [f] and rows[f].status == "residue"


def test_each_record_is_judged_on_its_own_evidence_never_its_family():
    # CRMS: one gauge leads by 3.5 h, its neighbours of the same provider are on time
    spec = {"crms0590-crms0590-usa-crms": (-210.0, UNV), "crms4355-crms4355-usa-crms": (0.0, UNV),
            "crms0527-crms0527-usa-crms": (5.0, UNV)}
    inv, rows, cfg, wk = _setup(spec)
    _apply(inv, rows, cfg, wk, [ref("A", 3.0)])
    assert rows["crms0590-crms0590-usa-crms"].status == "residue"
    assert rows["crms4355-crms4355-usa-crms"].status == "unverified"
    assert rows["crms0527-crms0527-usa-crms"].status == "unverified"


def test_a_disputed_record_keeps_its_code_and_an_unbound_fit_is_not_used():
    dis = ("residue", "w", NOT_USABLE)
    inv, rows, cfg, wk = _setup({"a-1-usa-sfwmd": (-300.0, dis), "b-2-usa-sfwmd": (-300.0, UNV)})
    rows["a-1-usa-sfwmd"].comparator_id = "disputed:eot20_whole_hour"
    cfg.fits["b-2-usa-sfwmd"]["time_base"] = "utc_minus_5"            # fitted on another base than it publishes
    (_, counts), _ = _apply(inv, rows, cfg, wk, [ref("A", 1.0)])
    assert rows["a-1-usa-sfwmd"].comparator_id == "disputed:eot20_whole_hour"
    assert "agency constants" in rows["a-1-usa-sfwmd"].reason and counts["already_disputed"] == 1
    assert rows["b-2-usa-sfwmd"].status == "unverified" and counts["bound"] == 1


def test_no_references_judges_nothing():
    inv, rows, cfg, wk = _setup({"a-1-usa-sfwmd": (-300.0, UNV)})
    (_, counts), _ = _apply(inv, rows, cfg, wk, [])
    assert rows["a-1-usa-sfwmd"].status == "unverified" and counts["references"] == 0


def test_load_noaa_refs_reads_the_harcon_cache_and_names_a_bad_file(tmp_path):
    assert tag.load_noaa_refs(tmp_path / "none")[0] == []
    (tmp_path / "harcon").mkdir()
    st = [{"id": "1", "lat": 10.0, "lng": 20.0}, {"id": "2", "lat": 11.0, "lng": 21.0}, {"id": "3", "lat": 1, "lng": 2}]
    (tmp_path / "stations.json").write_text(json.dumps({"stations": st}))
    hc = {"units": "meters", "HarmonicConstituents": [{"name": "M2", "amplitude": 0.5, "phase_GMT": 10.0},
                                                      {"name": "K1", "amplitude": 0.2, "phase_GMT": 20.0}]}
    (tmp_path / "harcon/1.json").write_text(json.dumps(hc))
    (tmp_path / "harcon/2.json").write_text(json.dumps({**hc, "units": "feet"}))
    refs, skipped = tag.load_noaa_refs(tmp_path)
    assert [(r.id, dict(r.consts)) for r in refs] == [("1", {"M2": (0.5, 10.0), "K1": (0.2, 20.0)})]
    assert len(skipped) == 1 and "feet" in skipped[0]


# --- two independent sources (FIX-LST-2) -----------------------------------------------------------

def _setup2(spec: dict, eots: dict | None = None, gt: dict | None = None):
    """spec: id -> (minutes late against REF, row spec, lat offset km); contributors from the id suffix."""
    inv, rows, fits = {}, {}, {}
    for f, (mins, (status, kind, corr), km) in spec.items():
        lat = 10.0 + km * KM
        inv[f] = RecordInfo(f, f.rsplit("-", 1)[-1].upper(), 3600, lat, 20.0, 0, 10 ** 9)
        fits[f] = {"record": f, "outcome": "fitted", "lat": lat, "lon": 20.0, "gauge_type": (gt or {}).get(f, "Coastal"),
                   "time_base": "utc_instant", "main": {k: [a, g, 0.0] for k, (a, g) in cons(mins).items()}}
        rows[f] = AuditRow(f, True, inv[f].contributor, 3600, status, corr.label if corr.usable else "no_comparator",
                           corr.label, corr, None, kind, "", reason="r")
    wk = WeakConfig(WeakInputs({f: {"eot": eot(spec[f][0], (eots or {}).get(f)), "zone": None} for f in spec}), {}, {})
    return inv, rows, SiblingConfig(fits, {}), wk


def _apply2(inv, rows, cfg, wk, refs=()):
    dec = {f: 0 for f in rows}
    return ta.apply_two_source(rows, inv, cfg, wk, dec, {}, SP, tag.RefIndex(list(refs)), lambda *_: None)


def test_eot20_and_another_contributors_sets_on_one_lead_dispute_a_river_record():
    # Tennesseehaven (RWS, River): EOT20 -59, the CMEMS and RWS_HIST sets at Hoek van Holland 2.4-2.6 km say -56
    spec = {"tennesseehaven-ten-nld-rws": (-59.0, UNV, 0.0), "hoekvh-hoe-nld-cmems": (0.0, UNV, 2.5),
            "hoek-hoekvhld-nld-rws_hist": (0.0, UNV, 2.4)}
    inv, rows, cfg, wk = _setup2(spec, eots={"tennesseehaven-ten-nld-rws": -59.0, "hoekvh-hoe-nld-cmems": 0.0,
                                             "hoek-hoekvhld-nld-rws_hist": 0.0},
                                 gt={"tennesseehaven-ten-nld-rws": "River"})
    counts = _apply2(inv, rows, cfg, wk)
    r = rows["tennesseehaven-ten-nld-rws"]
    assert r.status == "residue" and r.comparator_id == ta.TWO_SOURCE_DISPUTED and counts["disputed"] == 1
    assert r.agency["two_source"]["agree"] == ["E", "S"] and r.agency["two_source"]["sign"] == -1
    ta.annotate(r, 0)
    assert r.annotation_code == "disputed:two_source_offset" and not r.correction.usable
    assert rows["hoekvh-hoe-nld-cmems"].status == "unverified"          # its own E is 0 and its S is +59 alone


def test_one_source_is_never_enough_and_a_lag_needs_the_sets():
    # San Felipe 307a: EOT20 +58 and nothing else
    inv, rows, cfg, wk = _setup2({"san_felipe-307a-mex-uhslc_rq": (58.0, UNV, 0.0)},
                                 eots={"san_felipe-307a-mex-uhslc_rq": 58.0})
    _apply2(inv, rows, cfg, wk)
    assert rows["san_felipe-307a-mex-uhslc_rq"].status == "unverified"
    # a lag both open-water sources see (EOT20 +60, agency +60) without a set: an inland gauge, not flagged
    inv, rows, cfg, wk = _setup2({"aiw-1-usa-usgs": (60.0, UNV, 0.0)}, eots={"aiw-1-usa-usgs": 60.0})
    _apply2(inv, rows, cfg, wk, [ref("A", 13.8)])
    assert rows["aiw-1-usa-usgs"].status == "unverified"


def test_the_lead_and_lag_limits_are_the_calibrated_ones():
    spec = {"a-1-x-c1": (-44.0, UNV, 0.0), "b-2-x-c2": (0.0, UNV, 1.0)}
    inv, rows, cfg, wk = _setup2(spec, eots={"a-1-x-c1": -44.0})
    _apply2(inv, rows, cfg, wk)
    assert rows["a-1-x-c1"].status == "unverified"                    # 44 < TS_LEAD_MIN
    # Naples Bay City Dock: +45 against EOT20 and +48.5 against a set: a harbour lag below TS_LAG_MIN
    spec = {"naples-1-usa-usgs": (48.5, UNV, 0.0), "pier-2-usa-noaa": (0.0, UNV, 1.5)}
    inv, rows, cfg, wk = _setup2(spec, eots={"naples-1-usa-usgs": 45.0})
    _apply2(inv, rows, cfg, wk)
    assert rows["naples-1-usa-usgs"].status == "unverified"
    spec = {"mol-1-ita-cmems": (80.0, UNV, 0.0), "tr-2-ita-ispra": (0.0, UNV, 0.7)}
    inv, rows, cfg, wk = _setup2(spec, eots={"mol-1-ita-cmems": 80.0})
    _apply2(inv, rows, cfg, wk)
    assert rows["mol-1-ita-cmems"].status == "residue"


def test_signs_must_agree_sets_must_be_another_contributor_and_offset_sets_are_no_reference():
    spec = {"a-1-x-c1": (-60.0, UNV, 0.0), "b-2-x-c1": (0.0, UNV, 1.0)}             # same contributor
    inv, rows, cfg, wk = _setup2(spec, eots={"a-1-x-c1": -60.0})
    _apply2(inv, rows, cfg, wk)
    assert rows["a-1-x-c1"].status == "unverified"
    # Boynton Beach NOAA against SFWMD s41_t (EST, disputed for it): never a reference
    dis = ("residue", "w", NOT_USABLE)
    spec = {"boynton-8722706-usa-noaa": (0.0, UNV, 0.0), "s41_t-s41_t-usa-sfwmd": (-280.0, dis, 1.4)}
    inv, rows, cfg, wk = _setup2(spec, eots={"boynton-8722706-usa-noaa": 110.0})
    rows["s41_t-s41_t-usa-sfwmd"].comparator_id = ta.AGENCY_DISPUTED
    _apply2(inv, rows, cfg, wk)
    assert rows["boynton-8722706-usa-noaa"].status == "unverified"
    spec = {"a-1-x-c1": (-60.0, UNV, 0.0), "b-2-x-c2": (0.0, UNV, 1.0)}               # EOT20 the other way
    inv, rows, cfg, wk = _setup2(spec, eots={"a-1-x-c1": 60.0})
    _apply2(inv, rows, cfg, wk)
    assert rows["a-1-x-c1"].status == "unverified"


def test_a_verified_or_corrected_record_is_not_judged_and_a_disputed_one_keeps_its_code():
    spec = {"a-1-x-c1": (-60.0, ("used", "w", Correction("utc_instant")), 0.0), "b-2-x-c2": (0.0, UNV, 1.0),
            "c-3-x-c3": (-60.0, ("residue", "w", NOT_USABLE), 20.0), "d-4-x-c4": (0.0, UNV, 21.0)}
    inv, rows, cfg, wk = _setup2(spec, eots={"a-1-x-c1": -60.0, "c-3-x-c3": -60.0})
    rows["c-3-x-c3"].comparator_id = "disputed:served_stations_disagree"
    counts = _apply2(inv, rows, cfg, wk)
    assert rows["a-1-x-c1"].status == "used"
    assert rows["c-3-x-c3"].comparator_id == "disputed:served_stations_disagree" and counts["already_disputed"] == 1
    assert "two independent sources" in rows["c-3-x-c3"].reason


def test_a_diurnal_tide_is_judged_on_k1_and_o1():
    # Alicante DA_MM: M2 0.018 m; K1/O1 1 h early against CMEMS at the same site and EOT20
    small = {"M2": (0.018, 100.0), "S2": (0.01, 140.0), "K1": (0.036, 200.0), "O1": (0.023, 180.0)}
    late = lambda m: {k: (a, (g + SP[k] * m / 60.0) % 360.0) for k, (a, g) in small.items()}
    assert tag.joint_shift(late(-60.0), late(0.0), SP, ratio=tag.TS_SET_RATIO, resid=15.0)[0] == pytest.approx(-60, abs=1)
    assert tag.joint_shift(late(-60.0), late(0.0), SP, ratio=tag.TS_SET_RATIO, resid=15.0)[1] == "diurnal"
    big = {k: (a * 10, g) for k, (a, g) in late(0.0).items()}
    assert tag.joint_shift(late(-60.0), big, SP, ratio=tag.TS_SET_RATIO, resid=15.0) is None     # K1 ratio 0.1
    odd = dict(late(-60.0))
    odd["O1"] = (odd["O1"][0], (odd["O1"][1] + SP["O1"] * 40 / 60.0) % 360.0)                     # O1 40 min off K1
    assert tag.joint_shift(odd, late(0.0), SP, ratio=tag.TS_SET_RATIO, resid=15.0) is None
    semi = dict(late(-60.0), M2=(0.5, 100.0))                                                     # a semidiurnal: no fallback
    assert tag.joint_shift(semi, dict(late(0.0), M2=(0.05, 100.0)), SP, ratio=tag.TS_SET_RATIO, resid=15.0) is None


def test_a_diurnal_fallback_needs_both_diurnals_and_the_sets_must_be_near():
    small = {"M2": (0.018, 100.0), "S2": (0.01, 140.0), "K1": (0.036, 200.0), "O1": (0.012, 180.0)}   # O1 1.2 cm
    assert tag.joint_shift(small, small, SP, ratio=tag.TS_SET_RATIO, resid=15.0) is None
    spec = {"a-1-x-c1": (-60.0, UNV, 0.0), "b-2-x-c2": (0.0, UNV, 3.5)}                              # 3.5 km off
    inv, rows, cfg, wk = _setup2(spec, eots={"a-1-x-c1": -60.0})
    _apply2(inv, rows, cfg, wk)
    assert rows["a-1-x-c1"].status == "unverified"


def test_the_sets_source_is_their_median():
    # two sets on UTC say -60, a third shares the record's clock (0): the median is -60
    spec = {"a-1-x-c1": (-60.0, UNV, 0.0), "b-2-x-c2": (0.0, UNV, 1.0), "c-3-x-c3": (0.0, UNV, 1.5),
            "d-4-x-c4": (-60.0, UNV, 2.0)}
    inv, rows, cfg, wk = _setup2(spec, eots={"a-1-x-c1": -60.0})
    _apply2(inv, rows, cfg, wk)
    assert rows["a-1-x-c1"].status == "residue" and rows["a-1-x-c1"].agency["two_source"]["S"]["tau"] == -60.0


def test_a_neighbour_without_a_row_is_still_a_reference_set():
    # an --only run: the Hoek van Holland sets are outside the audited pool, their fits still count
    spec = {"tennesseehaven-ten-nld-rws": (-59.0, UNV, 0.0), "hoekvh-hoe-nld-cmems": (0.0, UNV, 2.5)}
    inv, rows, cfg, wk = _setup2(spec, eots={"tennesseehaven-ten-nld-rws": -59.0})
    del rows["hoekvh-hoe-nld-cmems"]
    _apply2(inv, rows, cfg, wk)
    assert rows["tennesseehaven-ten-nld-rws"].status == "residue" and "hoekvh-hoe-nld-cmems" not in rows


# --- FIX-LST-3 -------------------------------------------------------------------------------------

def damped(minutes: float, amp: float) -> dict:
    """REF stamped `minutes` late with every amplitude times amp."""
    return cons(minutes, amp)


def test_a_one_cm_diurnal_fixes_the_cycle_of_an_m2_only_canal_tide_but_not_a_marsh():
    # SFWMD mdts: M2 6 cm, K1/O1 1 cm; Card Sound M2 20 cm, K1/O1 3 cm: the diurnals count at a consistent ratio
    rec_ = {"M2": (0.06, 100.0), "S2": (0.009, 140.0), "K1": (0.011, 200.0), "O1": (0.010, 180.0)}
    late = {k: (a, (g + SP[k] * -225 / 60.0) % 360.0) for k, (a, g) in rec_.items()}
    refc = {"M2": (0.07, 100.0), "S2": (0.012, 140.0), "K1": (0.013, 200.0), "O1": (0.012, 180.0)}
    assert tag.joint_lag(late, refc, SP, ratio=tag.AG_RATIO)["tau"] == pytest.approx(-225, abs=1)
    assert tag.joint_lag(late, refc, SP, ratio=tag.AG_RATIO, diurnal_floor=0.02) is None          # the old 2 cm floor
    # CRMS: M2 1 cm, K1 13 cm: the semidiurnal keeps its 2 cm floor, so no shift (diurnal-only tides go by pair())
    marsh = {"M2": (0.01, 100.0), "S2": (0.006, 140.0), "K1": (0.13, 200.0), "O1": (0.12, 180.0)}
    assert tag.joint_lag(marsh, marsh, SP, ratio=tag.AG_RATIO) is None
    # a small constituent whose amplitude ratio is off is left out, not counted
    odd = dict(late, S2=(0.03, late["S2"][1]), O1=(0.0101, 0.0))
    j = tag.joint_lag(odd, dict(refc, S2=(0.03, 140.0), O1=(0.05, 180.0)), SP, ratio=tag.AG_RATIO)
    assert j is not None and "O1" not in j["res"]


def test_a_lead_over_lagged_inland_water_is_judged_against_open_water_through_it():
    # CRMS 0535: -135.5 against Bayou Fearman, which lags the open coast by +265: +130 over open water, a damped lag
    r = ref("A", 4.6)
    near = [(r, 4.6)]
    lags = {"A": {"lag": 265.0, "how": "noaa:B", "confirmed": None}}
    a = damped(-135.5, 0.82)
    assert tag.shift(a, near, SP, lags)["tau"] == pytest.approx(129.5, abs=1)
    assert tag.shift(a, near, SP, None)["tau"] == pytest.approx(-135.5, abs=1)                # FIX-LST-1 behaviour
    # in the SAME water (ratio ~1, constituents together) the record is judged directly (SFWMD vali7 vs Fort Myers)
    assert tag.shift(cons(-281.5), near, SP, lags)["tau"] == pytest.approx(-281.5, abs=1)
    # an unknown open lag: only a reference an undisputed record confirms may judge a record in other water
    unk = {"A": {"lag": None, "how": "unknown", "confirmed": None}}
    assert tag.shift(a, near, SP, unk) is None
    # FIX-LST-5 B1: a record confirming the reference's constants says nothing of its water: still no verdict
    assert tag.shift(a, near, SP, {"A": {**unk["A"], "confirmed": "card_sound_bridge-8723534-usa-noaa"}}) is None
    # ... but in the reference's own water an unknown open lag does not matter
    assert tag.shift(cons(-212.5), near, SP, unk)["tau"] == pytest.approx(-212.5, abs=1)


def test_a_reference_ahead_of_the_open_water_never_moves_a_record_earlier():
    # a reference with a negative open lag (it leads EOT20 by 50 min): the record is judged by its own tau, clipped at 0
    near = [(ref("A", 4.6), 4.6)]
    lead = {"A": {"lag": -50.0, "how": "eot20", "confirmed": None}}
    a = damped(-100.0, 0.82)
    sh = tag.shift(a, near, SP, lead)
    assert sh["tau"] == pytest.approx(-100.0, abs=1)                # not -150: no lead of 120 made by the reference
    assert tag.classify(sh, None)["kind"] == "none"


def test_the_lead_residual_allowance_is_for_a_near_reference_and_a_lead_only():
    bad = dict(cons(-180.0))
    bad["O1"] = (bad["O1"][0], (bad["O1"][1] + SP["O1"] * 56.0 / 60.0) % 360.0)           # O1 56 min off: resid ~52
    j = tag.joint_lag(bad, REF, SP, ratio=tag.AG_RATIO)
    assert tag.AG_RESID_MIN < j["resid"] <= tag.AG_LEAD_RESID_MIN
    assert tag.shift(bad, [(ref("A", 4.2), 4.2)], SP)["tau"] == pytest.approx(j["tau"], abs=0.1)   # s29_t, Haulover
    assert tag.shift(bad, [(ref("A", 14.9), 14.9)], SP) is None                               # Middle Hoopers Island
    lag = dict(cons(180.0))
    lag["O1"] = (lag["O1"][0], (lag["O1"][1] + SP["O1"] * 56.0 / 60.0) % 360.0)
    assert tag.shift(lag, [(ref("A", 4.2), 4.2)], SP) is None                                 # a lag: 45 min


def test_open_lags_from_eot20_at_the_reference_then_from_the_nearest_open_coast_station():
    coast = ref("C", 0.0)                        # at (10, 20)
    inland = tag.AgencyRef("noaa", "I", 10.0 + 30 * KM, 20.0, damped(265.0, 0.5))
    far = tag.AgencyRef("noaa", "F", 13.0, 20.0, REF)
    sites = [{"id": "c-1-x-noaa", "lat": 10.0, "lon": 20.0, "consts": REF, "disputed": False,
              "eot": {**{k: list(v) for k, v in REF.items()}, "dist_km": 4.0}},
             {"id": "i-2-x-noaa", "lat": inland.lat, "lon": 20.0, "consts": damped(265.0, 0.5), "disputed": True,
              "eot": None}]
    got = tag.open_lags([coast, inland, far], sites, SP)
    assert got["C"]["lag"] == pytest.approx(0.0, abs=1) and got["C"]["confirmed"] == "c-1-x-noaa"
    assert got["I"]["lag"] == pytest.approx(265.0, abs=1) and got["I"]["how"].startswith("noaa:C")
    assert got["I"]["confirmed"] is None                                     # its only record is disputed
    assert got["F"]["lag"] is None                                           # 333 km from any open-coast station


def test_two_sources_at_one_physical_site_are_one_source():
    # crms0003: NOAA Grand Pass harcon (4.3 km) and USGS at Grand Pass (2.6 km from the record, by the harcon station)
    spec = {"crms0003-crms0003-usa-crms": (-54.5, UNV, 0.0), "grand_pass-1-usa-usgs": (0.0, UNV, 2.6)}
    inv, rows, cfg, wk = _setup2(spec)
    assert _apply2(inv, rows, cfg, wk, [ref("GP", 4.3)])["sets_same_site_dropped"] == 1
    assert rows["crms0003-crms0003-usa-crms"].status == "unverified"
    # L2: a set that IS an H reference (its NOAA number) is dropped too, wherever it sits
    spec = {"a-1-usa-c1": (-60.0, UNV, 0.0), "pier-8722670-usa-noaa": (0.0, UNV, 2.0)}
    inv, rows, cfg, wk = _setup2(spec)
    far_ref = tag.AgencyRef("noaa", "8722670", 10.0 + 9 * KM, 20.0, damped(0.0, 0.6))      # 9 km, other water
    assert _apply2(inv, rows, cfg, wk, [far_ref])["sets_same_site_dropped"] == 1
    assert rows["a-1-usa-c1"].status == "unverified"


def test_a_shift_under_two_hours_needs_a_uniform_undamped_source():
    # Settlement Point 257 in rule 2's terms: EOT20 and a set both see it -60 but damped (ratio 0.82): no dispute
    spec = {"a-1-x-c1": (-60.0, UNV, 0.0), "b-2-x-c2": (0.0, UNV, 1.0)}
    for amp, want, nu in ((0.82, "unverified", 1), (0.95, "residue", 0)):
        inv, rows, cfg, wk = _setup2(spec, eots={"a-1-x-c1": -60.0})
        cfg.fits["a-1-x-c1"]["main"] = {k: [a, g, 0.0] for k, (a, g) in damped(-60.0, amp).items()}
        wk.inputs.records["a-1-x-c1"]["eot"] = {**{k: list(v) for k, v in REF.items()}, "dist_km": 3.0}
        counts = _apply2(inv, rows, cfg, wk)
        assert rows["a-1-x-c1"].status == want and counts["not_uniform"] == nu, amp


def test_a_verified_set_within_5_km_judges_a_lead_of_two_hours_but_never_a_lag():
    ver = ("used", "c", Correction("utc_instant"))
    for mins, want in ((-186.0, "residue"), (186.0, "unverified")):
        spec = {"s29_t-s29_t-usa-sfwmd": (mins, UNV, 0.0), "haulover-1-usa-uhslc_rq": (0.0, ver, 4.24)}
        inv, rows, cfg, wk = _setup2(spec, eots={"s29_t-s29_t-usa-sfwmd": mins, "haulover-1-usa-uhslc_rq": 0.0})
        _apply2(inv, rows, cfg, wk)
        assert rows["s29_t-s29_t-usa-sfwmd"].status == want, mins


def test_a_set_across_a_structure_judges_a_lead_on_k1_o1_only():
    # s79_t: the semidiurnal does not pass the S-79 lock (M2 ratio 0.48); K1/O1 put the record 441 min early
    up = {k: (a * (0.48 if k in ("M2", "S2") else 0.8), (g + SP[k] * -441 / 60.0) % 360.0) for k, (a, g) in REF.items()}
    x = tag.lock_pair(up, REF, SP, ratio=tag.TS_SET_RATIO, resid=15.0)
    assert x["how"] == "diurnal across a structure" and x["tau"] == pytest.approx(-441, abs=1)
    assert tag.lock_pair(cons(-441.0), REF, SP, ratio=tag.TS_SET_RATIO, resid=15.0) is None    # the full pair passes


def test_the_neighbour_search_is_a_true_radius_at_high_latitude():
    # L1: at 60 N, 2.5 km due east is 0.045 deg of longitude: outside a 1/30-degree grid's neighbours
    inv = {"a": RecordInfo("a", "C1", 3600, 60.0, 10.0, 0, 1), "b": RecordInfo("b", "C2", 3600, 60.0, 10.045, 0, 1),
           "c": RecordInfo("c", "C3", 3600, 60.0, 10.07, 0, 1)}
    got = ta._Near(["a", "b", "c"], inv).within(60.0, 10.0, 3.0)
    assert [g for g, _ in got] == ["a", "b"] and got[1][1] == pytest.approx(2.5, abs=0.05)


BLACK_EOT = {"M2": (0.021, 22.0), "S2": (0.012, 30.0), "K1": (0.013, 100.0), "O1": (0.008, 90.0)}


def black(minutes: float, amp: float = 1.0) -> dict:
    return {k: (a * amp, (g + SP[k] * minutes / 60.0) % 360.0) for k, (a, g) in BLACK_EOT.items()}


def _setup_sea(spec):
    """spec: id -> (minutes late against EOT20, row spec, lat km, eot minutes offset of the cell (default 0))."""
    inv, rows, fits = {}, {}, {}
    for f, (mins, (status, kind, corr), km) in spec.items():
        lat = 43.0 + km * KM
        inv[f] = RecordInfo(f, f.rsplit("-", 1)[-1].upper(), 3600, lat, 28.0, 0, 10 ** 9)
        fits[f] = {"record": f, "outcome": "fitted", "lat": lat, "lon": 28.0, "gauge_type": "Coastal",
                   "time_base": "utc_instant", "main": {k: [a, g, 0.0] for k, (a, g) in black(mins).items()}}
        rows[f] = AuditRow(f, True, inv[f].contributor, 3600, status, corr.label if corr.usable else "no_comparator",
                           corr.label, corr, None, kind, "", reason="r")
    e = {**{k: list(v) for k, v in BLACK_EOT.items()}, "dist_km": 5.0}
    wk = WeakConfig(WeakInputs({f: {"eot": e, "zone": None} for f in spec}), {}, {})
    return inv, rows, SiblingConfig(fits, {}), wk


def _sea(inv, rows, cfg, wk):
    return ta.apply_regional(rows, inv, cfg, wk, {f: 0 for f in rows}, {}, SP, lambda *_: None)


def test_a_microtidal_record_eot20_puts_hours_off_is_disputed_when_another_provider_agrees_with_eot20():
    spec = {"ptm3066-ptm-bgr-cmems": (165.0, UNV, 0.0), "ioc_mang-ioc-rou-cmems": (10.0, UNV, 87.0)}
    inv, rows, cfg, wk = _setup_sea(spec)
    counts = _sea(inv, rows, cfg, wk)
    r = rows["ptm3066-ptm-bgr-cmems"]
    assert r.status == "residue" and r.comparator_id == ta.REGIONAL_DISPUTED and counts["disputed"] == 1
    assert rows["ioc_mang-ioc-rou-cmems"].status == "unverified"
    ta.annotate(r, 0)
    assert r.annotation_code == "disputed:eot20_regional_offset"


@pytest.mark.parametrize("change", ["no_control", "same_family_control", "control_off", "too_far", "macrotidal",
                                    "one_band", "diurnal_short", "damped", "verified_veto", "same_site_veto",
                                    "under_two_hours"])
def test_the_regional_rule_refuses(change):
    spec = {"ptm3066-ptm-bgr-cmems": (165.0, UNV, 0.0), "ioc_mang-ioc-rou-cmems": (10.0, UNV, 87.0)}
    if change == "no_control":
        del spec["ioc_mang-ioc-rou-cmems"]
    if change == "same_family_control":
        spec = {"ptm3066-ptm-bgr-cmems": (165.0, UNV, 0.0), "varna-var-bgr-cmems": (10.0, UNV, 87.0)}
    if change == "control_off":
        spec["ioc_mang-ioc-rou-cmems"] = (40.0, UNV, 87.0)
    if change == "too_far":
        spec["ioc_mang-ioc-rou-cmems"] = (10.0, UNV, 105.0)
    if change == "verified_veto":
        spec["varna_x-1-bgr-noaa"] = (160.0, ("used", "c", Correction("utc_instant")), 40.0)
    if change == "same_site_veto":
        spec["ptm_ispra-ptm-bgr-ispra"] = (170.0, UNV, 1.0)
    if change == "under_two_hours":
        spec["ptm3066-ptm-bgr-cmems"] = (110.0, UNV, 0.0)
    inv, rows, cfg, wk = _setup_sea(spec)
    m = cfg.fits["ptm3066-ptm-bgr-cmems"]["main"]
    if change == "macrotidal":
        for k in m:
            m[k][0] *= 6.0
        wk.inputs.records["ptm3066-ptm-bgr-cmems"]["eot"] = {
            **{k: [a * 6.0, g] for k, (a, g) in BLACK_EOT.items()}, "dist_km": 5.0}
    if change == "one_band":
        for k in ("K1", "O1"):
            m[k][1] = (m[k][1] - SP[k] * 165.0 / 60.0) % 360.0          # diurnals on time
    if change == "diurnal_short":
        for k in ("K1", "O1"):
            m[k][1] = (m[k][1] - SP[k] * 55.0 / 60.0) % 360.0           # diurnals +110: within 60 of +165, under 120
    if change == "damped":
        for k in ("M2", "S2"):
            m[k][0] *= 0.5
    _sea(inv, rows, cfg, wk)
    assert rows["ptm3066-ptm-bgr-cmems"].status == "unverified"


def test_a_set_across_a_structure_never_judges_a_lag():
    up = {k: (a * (0.48 if k in ("M2", "S2") else 1.0), (g + SP[k] * 441 / 60.0) % 360.0) for k, (a, g) in REF.items()}
    spec = {"s79_t-s79_t-usa-sfwmd": (441.0, UNV, 0.0), "s79-1-usa-usgs": (0.0, UNV, 0.2)}
    inv, rows, cfg, wk = _setup2(spec, eots={"s79_t-s79_t-usa-sfwmd": 441.0})
    cfg.fits["s79_t-s79_t-usa-sfwmd"]["main"] = {k: [a, g, 0.0] for k, (a, g) in up.items()}
    wk.inputs.records["s79_t-s79_t-usa-sfwmd"]["eot"] = {**{k: [a, (g - SP[k] * 441 / 60.0) % 360.0] for k, (a, g) in
                                                             up.items()}, "dist_km": 3.0}
    _apply2(inv, rows, cfg, wk)
    assert rows["s79_t-s79_t-usa-sfwmd"].status == "unverified"


def test_a_reviewed_withhold_is_never_a_reference():
    # FIX-LST-5 L3: a listed record is no set for rule 2, no control for rule B, no site for open_lags
    spec = {"a-1-x-c1": (-60.0, UNV, 0.0), "b-2-x-c2": (0.0, UNV, 1.0)}
    inv, rows, cfg, wk = _setup2(spec, eots={"a-1-x-c1": -60.0})
    ta.apply_two_source(rows, inv, cfg, wk, {f: 0 for f in rows}, {}, SP, tag.RefIndex([]), lambda *_: None,
                        exclude={"b-2-x-c2"})
    assert rows["a-1-x-c1"].status == "unverified"
    spec = {"ptm3066-ptm-bgr-cmems": (165.0, UNV, 0.0), "ioc_mang-ioc-rou-cmems": (10.0, UNV, 87.0)}
    inv, rows, cfg, wk = _setup_sea(spec)
    ta.apply_regional(rows, inv, cfg, wk, {f: 0 for f in rows}, {}, SP, lambda *_: None,
                      exclude={"ioc_mang-ioc-rou-cmems"})
    assert rows["ptm3066-ptm-bgr-cmems"].status == "unverified"
    coast = ref("C", 0.0)
    site = [{"id": "c-1-x-noaa", "lat": 10.0, "lon": 20.0, "consts": REF, "disputed": False,
             "eot": {**{k: list(v) for k, v in REF.items()}, "dist_km": 4.0}}]
    assert tag.open_lags([coast], site, SP)["C"]["lag"] == pytest.approx(0.0, abs=1)
    got = tag.open_lags([coast], site, SP, exclude={"c-1-x-noaa"})["C"]
    assert got["lag"] is None and got["confirmed"] is None
