"""TB-SIB: the intra-site constants comparison and the provider-family rule (timebase_sibling), and how the audit
turns their findings into verdicts (timebase_audit.apply_sibling)."""

from __future__ import annotations

import pytest

from gesla import timebase_audit as ta
from gesla import timebase_sibling as ts
from gesla.timebase_audit import AuditRow, RecordInfo, SiblingConfig, WeakConfig
from gesla.timebase_verdict import NOT_USABLE, Correction
from dataclasses import replace

from gesla.timebase_weak import WeakInputs, WeakResult

SP = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356}
REF = {"M2": (0.6, 100.0), "S2": (0.25, 140.0), "K1": (0.2, 200.0), "O1": (0.15, 180.0)}


def cons(minutes: float, amp: float = 1.0) -> dict:
    """REF's tide stamped `minutes` late."""
    return {k: (a * amp, (g + SP[k] * minutes / 60.0) % 360.0) for k, (a, g) in REF.items()}


def eot(dist: float = 3.0) -> dict:
    return {**{k: [a, g] for k, (a, g) in REF.items()}, "dist_km": dist}


def rec(fid: str, minutes: float, *, lat: float = 10.0, lon: float = 20.0, gt: str = "Coastal", e=True,
        strong: bool = False, stations=()) -> ts.SiteRecord:
    return ts.SiteRecord(fid, lat, lon, gt, cons(minutes), eot() if e else None, strong, frozenset(stations))


def site(*rs) -> dict:
    return {r.file_id: r for r in rs}


# --- pairs ---------------------------------------------------------------------------------------

def test_pair_shift_recovers_one_clock_shift_and_its_sign():
    assert ts.pair_shift(cons(-480.0), cons(0.0), SP) == pytest.approx(-480.0, abs=0.6)
    assert ts.pair_shift(cons(126.0), cons(0.0), SP) == pytest.approx(126.0, abs=0.6)


def test_pair_shift_refuses_a_tide_that_differs_by_more_than_the_clock():
    b = cons(0.0)
    a = dict(cons(60.0))
    a["K1"] = (a["K1"][0], a["K1"][1] + 3 * ts.PAIR_RESID_DEG)        # one constituent off: not one clock shift
    assert ts.pair_shift(a, b, SP) is None
    assert ts.pair_shift(cons(60.0, amp=0.5), b, SP) is None         # M2 ratio outside PAIR_RATIO


def test_candidate_pairs_are_coastal_within_2_km_or_one_station():
    rs = site(rec("a-1-x-c1", 0), rec("b-2-x-c2", 0, lat=10.01),                   # 1.1 km
              rec("c-3-x-c3", 0, lat=10.025),                                      # 2.8 km from a, 1.7 from b
              rec("d-4-x-c4", 0, lat=10.005, gt="River"),
              rec("e-5-x-c5", 0, lat=11.0, stations={"T1"}), rec("f-6-x-c6", 0, lat=12.0, stations={"T1"}))
    got = ts.candidate_pairs(rs)
    assert ("a-1-x-c1", "b-2-x-c2") in got and ("b-2-x-c2", "c-3-x-c3") in got
    assert ("a-1-x-c1", "c-3-x-c3") not in got
    assert not any("d-4-x-c4" in p for p in got)
    assert ("e-5-x-c5", "f-6-x-c6") in got


# --- sides ---------------------------------------------------------------------------------------

def test_eot20_on_both_sides_picks_the_offset_record_and_a_whole_hour_is_unambiguous():
    r = ts.find_offsets(site(rec("a-1-x-bom", -480), rec("b-2-x-uh", 0)), SP)
    assert set(r.findings) == {"a-1-x-bom"}
    x = r.findings["a-1-x-bom"]
    assert x.resolved_by == "eot20" and x.whole_hours == -8 and x.unambiguous
    assert x.shift_min == pytest.approx(-480, abs=1) and x.references == ("b-2-x-uh",)


def test_a_shift_that_is_not_a_whole_hour_is_offset_but_not_unambiguous():
    x = ts.find_offsets(site(rec("a-1-x-uh", 126.5), rec("b-2-x-fd", 0), rec("c-3-x-bom", 0)), SP).findings
    assert set(x) == {"a-1-x-uh"} and x["a-1-x-uh"].whole_hours is None and not x["a-1-x-uh"].unambiguous


def test_a_shift_under_offset_min_is_no_evidence():
    assert not ts.find_offsets(site(rec("a-1-x-c1", ts.OFFSET_MIN - 3), rec("b-2-x-c2", 0)), SP).findings


def test_without_eot20_the_majority_picks_the_side_and_is_never_unambiguous():
    x = ts.find_offsets(site(rec("a-1-x-c1", 60, e=False), rec("b-2-x-c2", 0, e=False), rec("c-3-x-c3", 0, e=False)),
                        SP).findings
    assert set(x) == {"a-1-x-c1"} and x["a-1-x-c1"].resolved_by == "majority" and not x["a-1-x-c1"].unambiguous


def test_one_against_one_without_eot20_has_no_side():
    r = ts.find_offsets(site(rec("a-1-x-c1", 60, e=False), rec("b-2-x-c2", 0, e=False)), SP)
    assert not r.findings and len(r.unresolved) == 1


def test_eot20_beats_the_majority_but_an_outnumbered_reference_is_not_unambiguous():
    # Dakar: four records on one clock, an hour off; three on UTC. EOT20 says the four are offset.
    rs = site(*[rec(f"o{i}-1-x-uh", -60) for i in range(4)], *[rec(f"r{i}-2-x-fd", 0) for i in range(3)])
    x = ts.find_offsets(rs, SP).findings
    assert set(x) == {f"o{i}-1-x-uh" for i in range(4)}
    assert all(v.resolved_by == "eot20" and v.whole_hours == -1 and not v.unambiguous and "outnumbers" in v.why
               for v in x.values())


def test_a_group_holding_a_strongly_verified_record_is_never_the_offset_one():
    r = ts.find_offsets(site(rec("a-1-x-usgs", 48.5, strong=True), rec("b-2-x-noaa", 0, strong=True)), SP)
    assert not r.findings and len(r.conflicts) == 1


# --- the provider family ---------------------------------------------------------------------------

def test_family_of_is_contributor_country_and_code_shape():
    assert ts.family_of("fremantle_fishing_boat_harbour-fffbh01-aus-bom") == ("bom", "aus", "aaaaa99")
    assert ts.family_of("fremantle-62230-aus-bom") == ("bom", "aus", "99999")
    assert ts.family_of("nohyphens") is None


def _wa(n_off: int, n_utc: int, confirmed: bool = True) -> dict:
    rs = [rec(f"s{i}-aaaaa{i:02d}-aus-bom", -480, lat=10 + i) for i in range(n_off)]
    rs += [rec(f"u{i}-uuuuu{i:02d}-aus-bom", 0, lat=30 + i) for i in range(n_utc)]
    if confirmed:                        # s0 has a UTC sibling (BOM national numbering: another family)
        rs.append(rec("n0-62230-aus-bom", 0, lat=10.005))
    return site(*rs)


def test_family_rule_needs_a_site_confirmed_member_and_a_majority_of_the_family():
    rs = _wa(4, 1)
    f = ts.family_offsets(rs, SP, ts.find_offsets(rs, SP).findings)
    assert set(f) == {"s1-aaaaa01-aus-bom", "s2-aaaaa02-aus-bom", "s3-aaaaa03-aus-bom"}   # s0 is the site finding
    assert all(v.hours == -8 and v.unambiguous and v.confirmed == ("s0-aaaaa00-aus-bom",) for v in f.values())
    rs = _wa(4, 1, confirmed=False)
    assert not ts.family_offsets(rs, SP, ts.find_offsets(rs, SP).findings)
    rs = _wa(3, 3)
    assert not ts.family_offsets(rs, SP, ts.find_offsets(rs, SP).findings)


def test_family_rule_may_leave_out_one_constituent_but_then_never_corrects():
    rs = _wa(4, 0)
    c = dict(rs["s3-aaaaa03-aus-bom"].consts)
    c["K1"] = (c["K1"][0], c["K1"][1] - 13.0)                                     # Onslow Salt: K1 53 min off
    rs["s3-aaaaa03-aus-bom"] = ts.SiteRecord("s3-aaaaa03-aus-bom", 13, 20, "Coastal", c, eot())
    f = ts.family_offsets(rs, SP, ts.find_offsets(rs, SP).findings)
    assert f["s3-aaaaa03-aus-bom"].how.startswith("without K1") and not f["s3-aaaaa03-aus-bom"].unambiguous


# --- apply_sibling ---------------------------------------------------------------------------------

class _Ex:
    """ex.map stand-in: _sibling_confirm_task answers from a table (pass 2 and the weak result per record)."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def map(self, fn, rids, ns, refs, ost, indep):
        out = []
        for rid, n, rf, i in zip(rids, ns, refs, indep):
            self.calls.append((rid, n, bool(rf), i))
            out.append((rid, [], *self.answers[rid]))
        return out


def _setup(rows_spec, fits_spec, eots=None):
    """rows_spec: id -> (status, comparator kind, correction); fits_spec: id -> (minutes, time_base)."""
    inv, rows, fits = {}, {}, {}
    for i, (f, (mins, tb)) in enumerate(sorted(fits_spec.items())):
        inv[f] = RecordInfo(f, f.rsplit("-", 1)[-1].upper(), 3600, 10.0 + 0.001 * i, 20.0, 0, 10 ** 9)
        fits[f] = {"record": f, "outcome": "fitted", "lat": inv[f].lat, "lon": 20.0, "gauge_type": "Coastal",
                   "time_base": tb, "main": {k: [a, g, 0.0] for k, (a, g) in cons(mins).items()}}
    for f, (status, kind, corr) in rows_spec.items():
        rows[f] = AuditRow(f, True, inv[f].contributor, 3600, status, corr.label if corr.usable else "no_comparator",
                           corr.label, corr, None, kind, "", reason="r")
    wk = WeakConfig(WeakInputs({f: {"eot": (eots or {}).get(f, eot()), "zone": None} for f in fits}), {}, {})
    return inv, rows, SiblingConfig(fits, {}), wk


def _apply(inv, rows, cfg, wk, ex, dec=None):
    dec = dec or {f: 0 for f in rows}
    return ta.apply_sibling(rows, inv, cfg, wk, dec, {}, SP, ex, lambda f, p=False: {}, dec, lambda *_: None)


UNV = ("unverified", "none", NOT_USABLE)
WEAK_OK = WeakResult("unverified", "utc_minus_8", "unverified:weak_insufficient", "x")


def test_an_unambiguous_whole_hour_offset_is_corrected_after_pass_2():
    inv, rows, cfg, wk = _setup({"a-1-x-bom": UNV, "b-2-x-uh": UNV},
                                {"a-1-x-bom": (-480, "utc_instant"), "b-2-x-uh": (0, "utc_instant")})
    ex = _Ex({"a-1-x-bom": ([], {"pass2": {"against": "references", "ok": True}, "weak": WEAK_OK, "span": (0, 1)})})
    crashes, counts = _apply(inv, rows, cfg, wk, ex)
    r = rows["a-1-x-bom"]
    assert ex.calls == [("a-1-x-bom", -8, True, True)]
    assert r.status == "used" and r.correction.label == "utc_minus_8" and r.comparator_kind == "w"
    assert r.comparator_id == "corrected:utc_instant->utc_minus_8:sibling_constants+eot20"
    ta.annotate(r, 0)
    assert r.annotation_code == "corrected:utc_instant->utc_minus_8:sibling_constants+eot20"
    assert rows["b-2-x-uh"].status == "unverified" and counts["corrected"] == 1 and not crashes


def test_a_declared_base_wrongly_applied_is_corrected_back_to_utc():
    # Wewak 56070: declared +1, fitted with it, an hour early against its UTC sibling
    inv, rows, cfg, wk = _setup({"a-1-x-bom": UNV, "b-2-x-uh": UNV},
                                {"a-1-x-bom": (-60, "utc_plus_1"), "b-2-x-uh": (0, "utc_instant")})
    ok = WeakResult("unverified", "utc_instant", "unverified:weak_insufficient", "x")
    ex = _Ex({"a-1-x-bom": ([], {"pass2": {"ok": True, "against": "references"}, "weak": ok, "span": (0, 1)})})
    _apply(inv, rows, cfg, wk, ex, dec={"a-1-x-bom": 1, "b-2-x-uh": 0})
    assert ex.calls == [("a-1-x-bom", 0, True, True)] and rows["a-1-x-bom"].correction.label == "utc_instant"


@pytest.mark.parametrize("p2_ok, weak", [(False, WEAK_OK),
                                         (True, WeakResult("disputed", "utc_instant", "disputed:x", "y")),
                                         (True, WeakResult("verified", "utc_instant", "verified:x", "y"))])
def test_pass_2_or_the_records_own_weak_checks_make_a_correction_yield_to_disputed(p2_ok, weak):
    inv, rows, cfg, wk = _setup({"a-1-x-bom": UNV, "b-2-x-uh": UNV},
                                {"a-1-x-bom": (-480, "utc_instant"), "b-2-x-uh": (0, "utc_instant")})
    ex = _Ex({"a-1-x-bom": ([], {"pass2": {"ok": p2_ok, "against": "references"}, "weak": weak, "span": (0, 1)})})
    _apply(inv, rows, cfg, wk, ex)
    r = rows["a-1-x-bom"]
    assert r.status == "residue" and r.comparator_id == ta.SIB_DISPUTED and not r.correction.usable


def test_a_crash_in_pass_2_is_disputed_and_reported_a_fit_error_is_not_a_crash():
    for err, crash in (("KeyError", True), ("FitError", False)):
        inv, rows, cfg, wk = _setup({"a-1-x-bom": UNV, "b-2-x-uh": UNV},
                                    {"a-1-x-bom": (-480, "utc_instant"), "b-2-x-uh": (0, "utc_instant")})
        ex = _Ex({"a-1-x-bom": (["boom"], {"failed": True, "error_type": err})})
        crashes, _ = _apply(inv, rows, cfg, wk, ex)
        assert rows["a-1-x-bom"].status == "residue" and (crashes == ["a-1-x-bom"]) is crash


def test_an_ambiguous_offset_is_disputed_without_pass_2_and_a_used_provider_verdict_yields():
    inh = ("used", "inherit", Correction("utc_instant"))
    inv, rows, cfg, wk = _setup({"a-1-x-uh": inh, "b-2-x-fd": UNV, "c-3-x-bom": UNV},
                                {"a-1-x-uh": (126.5, "utc_instant"), "b-2-x-fd": (0, "utc_instant"),
                                 "c-3-x-bom": (0, "utc_instant")})
    ex = _Ex({})
    _apply(inv, rows, cfg, wk, ex)
    r = rows["a-1-x-uh"]
    assert not ex.calls and r.status == "residue" and r.comparator_id == ta.SIB_DISPUTED and "was utc_instant" in r.reason


def test_a_whole_hour_found_by_the_majority_alone_is_disputed_never_corrected():
    spec = {"a-1-x-uh": (-60, "utc_instant"), "b-2-x-fd": (0, "utc_instant"), "c-3-x-bom": (0, "utc_instant")}
    no_eot = {f: None for f in spec}
    inv, rows, cfg, wk = _setup({f: UNV for f in spec}, spec, eots=no_eot)
    wk = WeakConfig(WeakInputs({f: {"eot": None, "zone": None} for f in spec}), {}, {})
    ex = _Ex({})
    _apply(inv, rows, cfg, wk, ex)
    assert not ex.calls and rows["a-1-x-uh"].status == "residue" and rows["a-1-x-uh"].comparator_id == ta.SIB_DISPUTED
    assert "majority" in rows["a-1-x-uh"].reason


def test_a_disputed_record_keeps_its_own_code_and_a_strong_one_is_never_touched():
    dis = ("residue", "w", NOT_USABLE)
    rows_spec = {"a-1-x-uh": dis, "b-2-x-fd": UNV, "c-3-x-noaa": ("used", "b", Correction("utc_instant")),
                 "d-4-x-usgs": UNV}
    fits = {"a-1-x-uh": (-60, "utc_instant"), "b-2-x-fd": (0, "utc_instant"), "c-3-x-noaa": (48.5, "utc_instant"),
            "d-4-x-usgs": (0, "utc_instant")}
    inv, rows, cfg, wk = _setup(rows_spec, fits)
    rows["a-1-x-uh"].comparator_id = "disputed:served_stations_disagree"
    _apply(inv, rows, cfg, wk, _Ex({}))
    assert rows["a-1-x-uh"].comparator_id == "disputed:served_stations_disagree" and "fitted constants" in rows["a-1-x-uh"].reason
    assert rows["c-3-x-noaa"].status == "used" and rows["c-3-x-noaa"].comparator_kind == "b"


def test_constants_fitted_on_another_base_than_the_published_one_are_left_out():
    inv, rows, cfg, wk = _setup({"a-1-x-bom": UNV, "b-2-x-uh": UNV},
                                {"a-1-x-bom": (-480, "utc_mean_start60"), "b-2-x-uh": (0, "utc_instant")})
    _, counts = _apply(inv, rows, cfg, wk, _Ex({}))
    assert counts["fitted_unbound"] == 1 and rows["a-1-x-bom"].status == "unverified"


def test_csv_row_carries_the_sibling_summary_and_the_pool_flag():
    r = AuditRow("a-1-x-bom", False, "BOM", 3600, "unverified", "no_comparator", "no_comparator", NOT_USABLE, None,
                 "none", "", reason="r", in_pool=False)
    r.sibling = {"summary": "fitted constants -480.0 min"}
    row = ta.csv_row(r)
    assert row["sibling_constants"] == "fitted constants -480.0 min" and row["in_pool"] == 0
    assert set(row) == set(ta.CSV_FIELDS)


# --- D-O3: provider_class_inherit (owner switch) ----------------------------------------------------

MEAN60 = Correction("utc_mean_start60")


def _wsv(n_voters=3, extra=None, country="deu"):
    """n_voters WSV records a sibling series corrected to mean60, plus extra: id -> (status, kind, correction)."""
    inv, rows = {}, {}
    spec = {f"v{i}-99{i}-{country}-wsv": ("used", "a", MEAN60) for i in range(n_voters)}
    spec.update(extra or {})
    for i, (f, (status, kind, corr)) in enumerate(sorted(spec.items())):
        inv[f] = RecordInfo(f, f.rsplit("-", 1)[-1].upper(), 3600, 54.0 + 0.1 * i, 8.0, 0, 10 ** 9, f.split("-")[-2])
        rows[f] = AuditRow(f, True, inv[f].contributor, 3600, status, corr.label if corr.usable else "unexplained",
                           corr.label, corr, None, kind, "", reason="r")
    return inv, rows


def test_d_o3_inherits_the_sibling_verified_class_into_unverified_and_disputed_records():
    inv, rows = _wsv(3, {"a-1-deu-wsv": UNV, "b-2-deu-wsv": ("residue", "b", NOT_USABLE)})
    out = ta.provider_class_inherit(rows, inv, sorted(inv), ("WSV",))
    assert sorted(out["inherited"]) == ["a-1-deu-wsv", "b-2-deu-wsv"]
    for f in ("a-1-deu-wsv", "b-2-deu-wsv"):
        r = ta.annotate(rows[f], 0)
        assert r.correction.label == "utc_mean_start60" and r.comparator_kind == "inherit"
        assert r.annotation_code == "corrected:utc_instant->utc_mean_start60:provider" and "D-O3" in r.comparator_id


def test_d_o3_switch_off_or_too_few_voters_or_mixed_voters_inherit_nothing():
    for kw in ({"contributors": ()}, {"n": 2}, {"mixed": True}):
        extra = {"a-1-deu-wsv": UNV}
        if kw.get("mixed"):
            extra["m-9-deu-wsv"] = ("used", "a", Correction("utc_instant"))
        inv, rows = _wsv(kw.get("n", 3), extra)
        out = ta.provider_class_inherit(rows, inv, sorted(inv), kw.get("contributors", ("WSV",)))
        assert out["inherited"] == [] and rows["a-1-deu-wsv"].status == "unverified"


def test_d_o3_never_touches_a_strong_verdict_and_official_verdicts_do_not_vote():
    inst = Correction("utc_instant")
    inv, rows = _wsv(3, {"o-1-deu-wsv": ("used", "b", inst), "o-2-deu-wsv": ("used", "b", inst),
                         "o-3-deu-wsv": ("used", "b", inst), "a-1-deu-wsv": UNV})
    out = ta.provider_class_inherit(rows, inv, sorted(inv), ("WSV",))
    assert out["inherited"] == ["a-1-deu-wsv"] and all(rows[f"o-{i}-deu-wsv"].correction == inst for i in (1, 2, 3))


def test_d_o3_needs_the_voters_in_the_records_own_country():
    inv, rows = _wsv(3, {"a-1-nld-wsv": UNV})
    assert ta.provider_class_inherit(rows, inv, sorted(inv), ("WSV",))["inherited"] == []


def _fit(f, inv, minutes, tb="utc_instant"):
    return {"record": f, "outcome": "fitted", "gauge_type": "Coastal", "time_base": tb,
            "main": {k: [a, g, 0.0] for k, (a, g) in cons(minutes).items()}}


@pytest.mark.parametrize("shift, inherits", [(-30.0, True), (-38.0, True), (0.0, False), (-60.0, False)])
def test_d_o3_own_constants_against_a_utc_record_of_another_contributor_confirm_or_stop_it(shift, inherits):
    inv, rows = _wsv(3, {"a-1-deu-wsv": UNV, "c-1-deu-cmems": UNV})
    inv["c-1-deu-cmems"] = replace(inv["c-1-deu-cmems"], lat=inv["a-1-deu-wsv"].lat + 0.005, lon=8.0)
    fits = {"a-1-deu-wsv": _fit("a-1-deu-wsv", inv, shift), "c-1-deu-cmems": _fit("c-1-deu-cmems", inv, 0.0)}
    out = ta.provider_class_inherit(rows, inv, sorted(inv), ("WSV",), fits, SP, {})
    assert (out["inherited"] == ["a-1-deu-wsv"]) is inherits
    assert rows["a-1-deu-wsv"].correction.usable is inherits
    if inherits:
        assert "show the -30 min" in rows["a-1-deu-wsv"].reason
    else:
        assert out["contradicted"][0][0] == "a-1-deu-wsv" and "not inherited" in rows["a-1-deu-wsv"].reason


# --- eot20_whole_hour (owner rule: EOT20 alone disputes |n| >= 3 h, never corrects) ------------------

def _alone(minutes, **kw):
    return site(rec("a-1-x-sfwmd", minutes, **kw))


@pytest.mark.parametrize("minutes, hit", [(-240.0, True), (-232.0, True), (540.0, True), (-180.0, True),
                                          (-120.0, False), (60.0, False), (-225.0, False)])
def test_whole_hour_rule_needs_three_or_more_hours_within_ten_minutes(minutes, hit):
    assert ("a-1-x-sfwmd" in ts.eot_whole_hours(_alone(minutes), SP)) is hit


def test_whole_hour_rule_spares_strong_river_and_already_found_records():
    assert not ts.eot_whole_hours(_alone(-240.0, strong=True), SP)
    assert not ts.eot_whole_hours(_alone(-240.0, gt="River"), SP)
    assert not ts.eot_whole_hours(_alone(-240.0), SP, skip={"a-1-x-sfwmd"})


def test_whole_hour_rule_disputes_never_corrects_and_names_the_reason():
    inv, rows, cfg, wk = _setup({"a-1-x-sfwmd": UNV}, {"a-1-x-sfwmd": (-240, "utc_instant")})
    ex = _Ex({})
    _, counts = _apply(inv, rows, cfg, wk, ex)
    r = ta.annotate(rows["a-1-x-sfwmd"], 0)
    assert not ex.calls and counts["eot20_whole_hour"] == 1
    assert r.annotation_code == "disputed:eot20_whole_hour" and not r.correction.usable
    assert ta.csv_row(r)["disputed_reason"] == "eot20_whole_hour"


def test_disputed_reason_lists_eot20_whole_hour_also_on_an_already_disputed_record():
    inv, rows, cfg, wk = _setup({"a-1-x-sfwmd": ("residue", "w", NOT_USABLE)}, {"a-1-x-sfwmd": (-240, "utc_instant")})
    rows["a-1-x-sfwmd"].comparator_id = "disputed:weak_checks_disagree"
    _apply(inv, rows, cfg, wk, _Ex({}))
    r = ta.annotate(rows["a-1-x-sfwmd"], 0)
    assert ta.csv_row(r)["disputed_reason"] == "weak_checks_disagree eot20_whole_hour"
    assert ta.csv_row(ta.annotate(AuditRow("b", True, "X", 3600, "unverified", "no_comparator", "no_comparator",
                                           NOT_USABLE, None, "none", "", reason="r")))["disputed_reason"] == ""
