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
    bad["K1"] = (bad["K1"][0], bad["K1"][1] + SP["K1"] * 60.0 / 60.0)      # K1 60 min off the joint shift
    assert tag.shift(bad, [(ref("A", 1.0), 1.0)], SP) is None


def test_ref_index_finds_stations_by_distance():
    diag = tag.AgencyRef("noaa", "D", 10.0 + 11.0 * KM, 20.0 + 11.0 * KM / 0.9848, REF)     # 11 km N, 11 km E: 15.6 km
    idx = tag.RefIndex([ref("A", 1.0), ref("B", 14.0), ref("C", 16.0), diag])
    assert [r.id for r, _ in idx.near(10.0, 20.0)] == ["A", "B"]


@pytest.mark.parametrize("tau, e, kind", [(-300.0, None, "lead"), (-121.0, None, "lead"), (-100.0, None, "none"),
                                          (+300.0, None, "none"), (+88.5, 85.0, "agreed"), (+88.5, 60.0, "none"),
                                          (-50.0, -45.0, "agreed"), (-30.0, -30.0, "none")])
def test_classes(tau, e, kind):
    sh = {"tau": tau, "refs": [{"ref": "noaa:A", "tau": tau, "km": 5.0, "ratio": 1.0, "resid": 5.0}]}
    assert tag.classify(sh, e)["kind"] == kind


def test_a_whole_hour_needs_every_reference_eot20_and_a_reference_in_the_same_water():
    same = {"ref": "noaa:A", "tau": -301.0, "km": 1.0, "ratio": 1.0, "resid": 5.0}
    far = {"ref": "noaa:B", "tau": -241.0, "km": 11.0, "ratio": 0.8, "resid": 30.0}
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
