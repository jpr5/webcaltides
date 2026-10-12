"""OTC station ids (otc_pipeline.station_map) and several sets per station (gesla_release.build_release).

Synthetic records only, except the checks of the committed files under data/otc/ (no GESLA data read).
"""
import hashlib
import random

import pytest

from otc_pipeline import station_map as sm
from otc_pipeline.combine import AGENCY_SOURCES
from otc_pipeline.gesla_release import Candidate, recommend
from otc_pipeline.release_writer import validate_release
from otc_pipeline.station_map import MintConflict, RecordInfo, mint, new_id, site_names_agree

from gesla.tcd_table import TcdTable

from test_format_1_0 import SPEEDS, build, doc, fit_dict, tb_row


@pytest.fixture(scope="module")
def table():
    return TcdTable({"years": [2020, 2022], "tcd_sha256": "c" * 64, "aliases": {},
                     "constituents": {n: {"speed": s, "v0u": [10.0 * i for i in range(3)], "f": [1.0] * 3}
                                      for n, s in SPEEDS.items()}})


def rec(rid, lat, lon, name="Harbour", contributor="A", country="NOR", code=""):
    return RecordInfo(rid, name, contributor, country, lat, lon, code)


def ids(m):
    return m.record_ids()


def hid(founder):
    return "OTC-" + hashlib.sha256(founder.encode()).hexdigest()[:8]


def tide(m2=(1.0, 100.0), k1=(0.2, 50.0), s2=(0.3, 130.0), o1=(0.1, 40.0)):
    return {"M2": m2, "K1": k1, "S2": s2, "O1": o1}


def shifted(t, hours):
    """The same tide recorded with a clock `hours` off."""
    return {n: (a, (p + sm.SPEED[n] * hours) % 360) for n, (a, p) in t.items()}


SAME = tide()


# --- identity -------------------------------------------------------------------------------------

def test_records_within_001_degree_are_one_station_and_farther_ones_are_not():
    m = mint([rec("a", 60.0, 5.0), rec("b", 60.009, 5.009), rec("c", 60.0, 5.0211)], {})
    assert ids(m)["a"] == ids(m)["b"] != ids(m)["c"]
    assert set(m.stations) == {hid("a"), hid("c")}


def test_a_registry_alias_links_records_farther_apart():
    m = mint([rec("a", 60.0, 5.0), rec("b", 60.015, 5.0)], {"a": ["T1"], "b": ["T1"]})
    assert ids(m)["a"] == ids(m)["b"]
    assert m.release_aliases() == {hid("a"): {"webcaltides": "T1"}}


def test_the_closure_is_transitive_and_a_wide_group_is_listed_for_review():
    m = mint([rec("a", 60.0, 5.0), rec("b", 60.008, 5.0), rec("c", 60.016, 5.0)], {})
    assert len(set(ids(m).values())) == 1
    assert [r["reason"] for r in m.review] == [["span_over_0.01deg", "no_fit"]]
    m = mint([rec("a", 60.0, 5.0), rec("b", 60.008, 5.0), rec("c", 60.016, 5.0)], {}, tides=dict.fromkeys("abc", SAME))
    assert [r["reason"] for r in m.review] == [["span_over_0.01deg"]]


def test_different_names_are_held_apart_and_proposed():
    m = mint([rec("a", 60.0, 5.0, "Krabbersgatsluizen_NoordTG"), rec("b", 60.001, 5.0, "KrabbersgatsluizenZuidTG")], {})
    assert ids(m)["a"] != ids(m)["b"]
    (p,) = m.proposals
    assert p["reason"] == ["names_disagree"] and [[x["record"] for x in part] for part in p["parts"]] == [["a"], ["b"]]


def test_same_name_other_agency_is_merged_and_listed_for_review():
    m = mint([rec("a", 60.0, 5.0, "Klagshamn", "SMHI"), rec("b", 60.001, 5.0, "KlagshamnTG_60minute", "CMEMS")], {},
             tides={"a": SAME, "b": SAME})
    assert ids(m)["a"] == ids(m)["b"]
    assert [r["reason"] for r in m.review] == [["different_agencies"]] and m.proposals == []


def test_other_countries_are_held_apart():
    m = mint([rec("a", 60.0, 5.0, country="USA"), rec("b", 60.0, 5.0, country="PRI")], {})
    assert ids(m)["a"] != ids(m)["b"] and len(m.proposals) == 1


@pytest.mark.parametrize("a,b,same", [
    ("Klagshamn", "KlagshamnTG", True), ("DongesTG_60minute", "Donges", True), ("Prince_Rupert", "Prince_Rupert_BC", True),
    ("St_Lambert_Amont", "St_Lambert_Aval", False), ("Nordfeldoberwasser", "Nordfeldunterwasser", False),
    ("Hartelkering_Zeezijde", "Hartelkering_Landzijde", False), ("Tuckahoe", "Tuckahoe_2", False),
    ("Almeria2TG", "AlmeriaTG", False), ("Dungeness", "Cumberland_Sound_At_Sea_Camp_Dock", False),
    ("Brest", "BrestTG", True), ("Hartelkering", "Hartelkering_Zeezijde", False), ("Den_Helder", "Den_Helder_Noord", False)])
def test_site_names_agree(a, b, same):
    assert site_names_agree(a, b) is same and site_names_agree(b, a) is same


# --- ids ------------------------------------------------------------------------------------------

def test_the_same_input_gives_the_same_ids_in_any_order():
    recs = [rec(f"r{i:03d}", 60 + (i // 3) * 0.1, 5.0 + (i % 3) * 0.004) for i in range(30)]
    one = mint(recs, {})
    for seed in range(5):
        shuffled = recs[:]
        random.Random(seed).shuffle(shuffled)
        assert sm.dump(mint(shuffled, {})) == sm.dump(one)
    assert all(sm.ID_RE.fullmatch(s) for s in one.stations) and len(one.stations) == 10


def test_a_taken_id_gets_two_more_digits():
    h = hashlib.sha256(b"a").hexdigest()
    assert new_id("a", set()) == f"OTC-{h[:8]}"
    assert new_id("a", {f"OTC-{h[:8]}"}) == f"OTC-{h[:10]}"


# --- stability ------------------------------------------------------------------------------------

def test_a_remint_keeps_every_id_and_new_records_join_or_get_new_ids():
    old = mint([rec("m", 60.0, 5.0), rec("z", 61.0, 5.0)], {})
    new = mint([rec("a", 60.005, 5.0), rec("m", 60.0, 5.0), rec("b", 62.0, 5.0)], {}, existing=old)
    # "a" sorts before the founder "m" but joins m's station: the id does not move to hid("a")
    assert ids(new)["a"] == ids(new)["m"] == hid("m")
    assert ids(new)["z"] == hid("z")                 # missing from the input: it stays
    assert ids(new)["b"] == hid("b")
    assert sm.dump(mint([rec("a", 60.005, 5.0), rec("m", 60.0, 5.0), rec("b", 62.0, 5.0)], {}, existing=new)) == sm.dump(new)


def test_the_rule_never_joins_two_existing_stations_without_a_decision():
    old = mint([rec("a", 60.0, 5.0), rec("b", 60.018, 5.0)], {})
    assert len(old.stations) == 2
    bridge = [rec("a", 60.0, 5.0), rec("b", 60.018, 5.0), rec("c", 60.009, 5.0)]
    with pytest.raises(MintConflict, match="joins existing stations"):
        mint(bridge, {}, existing=old)
    decisions = {"merge": [{"records": ["a", "b"], "note": "one gauge"}]}
    with pytest.raises(ValueError, match="removed_in"):
        mint(bridge, {}, existing=old, decisions=decisions)
    new = mint(bridge, {}, existing=old, decisions=decisions, removed_in="20261101")
    assert set(ids(new).values()) == {hid("a")}
    assert new.retired == {hid("b"): {"merged_into": hid("a"), "removed_in": "20261101", "removed_reason": "merged",
                                      "detail": "merge decision (data/otc/station_decisions.json)"}}


def test_a_merge_decision_joins_a_held_apart_proposal():
    recs = [rec("a", 60.0, 5.0, "Chesapeake_BBT"), rec("b", 60.0, 5.0, "Chesapeake_Bay_Bridge_Tunnel")]
    assert len(mint(recs, {}).stations) == 2
    m = mint(recs, {}, decisions={"merge": [{"records": ["a", "b"]}]})
    assert len(m.stations) == 1 and m.proposals == []


def test_a_separate_decision_divides_a_station_and_the_founder_keeps_the_id():
    recs = [rec("a", 60.0, 5.0), rec("b", 60.001, 5.0)]
    old = mint(recs, {})
    assert len(old.stations) == 1
    new = mint(recs, {}, existing=old, decisions={"separate": [{"records": ["b"]}]})
    assert ids(new)["a"] == hid("a") and ids(new)["b"] == hid("b")


# --- the committed files --------------------------------------------------------------------------

def test_the_committed_station_map_loads_and_has_every_epoch_count():
    m = sm.load()
    rids = m.record_ids()
    assert len(rids) == sum(len(s["records"]) for s in m.stations.values())
    assert all(sm.ID_RE.fullmatch(s) for s in m.stations)
    epoch = sm.load_epoch_hours()
    # Epoch hours rank GESLA observations. Agency records and the audited TICON-only
    # published harmonic records have no GESLA observations to count.
    assert set(epoch) == {r for r in rids if r.split(":", 1)[0] not in AGENCY_SOURCES} - set(TICON_ONLY_RWS)
    assert all(0 <= e["hours"] <= 19 * 366 * 24 for e in epoch.values())
    aliases = m.release_aliases()
    tids = [a["webcaltides"] for a in aliases.values()]
    assert len(tids) == len(set(tids))


# --- several sets per station: the recommendation -----------------------------------------------------

def two(table, *, fits=(None, None), rows=(None, None)):
    return [doc(table, rid=f"r{i}-nor-test", fit=fits[i], row=rows[i]) for i in range(2)]


def built(table, docs, hours, **over):
    sid = {d["record_id"]: "OTC-ONE" for d in docs}
    eh = {d["record_id"]: {"member_sha256": d["member_sha256"], "hours": h} for d, h in zip(docs, hours)}
    return build(table, docs, station_ids=sid, epoch_hours=eh, **over)


def test_records_with_one_station_id_are_one_station_with_a_set_each(table):
    release = built(table, two(table), (100, 200))
    assert validate_release(release) == []
    (st,) = release["stations"]
    # written in recommendation order: r1 (more epoch hours) first
    assert [cs["set_id"] for cs in st["constant_sets"]] == ["OTC-ONE/gesla-fit/r1-nor-test", "OTC-ONE/gesla-fit/r0-nor-test"]
    assert st["aliases"] == {"gesla": ["r0-nor-test", "r1-nor-test"]}
    assert st["recommended_set_id"] == "OTC-ONE/gesla-fit/r1-nor-test"      # more epoch hours
    release = built(table, two(table), (300, 200), station_aliases={"OTC-ONE": {"webcaltides": "T1"}})
    assert release["stations"][0]["recommended_set_id"] == "OTC-ONE/gesla-fit/r0-nor-test"
    assert release["stations"][0]["aliases"] == {"gesla": ["r0-nor-test", "r1-nor-test"], "webcaltides": "T1"}


def test_equal_epoch_hours_go_to_the_most_recent_record_then_the_record_id(table):
    later = {**fit_dict(), "last_s": fit_dict()["last_s"] + 3600}
    release = built(table, two(table, fits=(None, later)), (100, 100))
    assert release["stations"][0]["recommended_set_id"].endswith("r1-nor-test")
    release = built(table, two(table), (100, 100))
    assert release["stations"][0]["recommended_set_id"].endswith("r0-nor-test")


def test_a_disputed_time_base_is_recommended_only_when_every_set_is_disputed(table):
    disputed = tb_row("disputed", comparator_kind="b", time_base_code="disputed:comparators_disagree",
                      time_base_reason="declared utc_instant kept; evidence disagrees")
    release = built(table, two(table, rows=(None, disputed)), (100, 9000))
    assert release["stations"][0]["recommended_set_id"].endswith("r0-nor-test")


def test_epoch_hours_are_required_and_bound_to_the_member(table):
    docs = two(table)
    with pytest.raises(ValueError, match="no epoch hours"):
        build(table, docs, epoch_hours={})
    bad = {d["record_id"]: {"member_sha256": "b" * 64, "hours": 1} for d in docs}
    with pytest.raises(ValueError, match="counted from member"):
        build(table, docs, epoch_hours=bad)


YEAR = 365.25 * 86400


def cand(rid, *, tier="gauge", short=False, good=50000.0, disputed=False, epoch=100000, last=2.0e9, orig=True,
         commercial=True, suspect=False, m2=(1.0, 100.0), status=None):
    cs = {"set_id": f"S/{rid}", "source_record_id": rid, "source_type": tier, "quantity": "water_level",
          "record_span": {"good_hours": good}, "time_base": {"code": "disputed" if disputed else "verified"},
          "qc_flags": [{"flag": "short_record", "reason": "x"}] if short else [],
          "constituents": [{"name": "M2", "amplitude_m": m2[0], "phase_deg": m2[1]},
                           {"name": "K1", "amplitude_m": 0.2, "phase_deg": 50.0}]}
    if status:
        cs["qc_status"] = status
    return Candidate(cs, epoch, int(last), originator=orig, commercial=commercial, suspect=suspect)


def pick(*cands):
    win, how = recommend(list(cands))
    return (win.cs["source_record_id"] if win else None), how["decided_by"]


def test_the_rank_steps_in_order():
    # D4: tier, not disputed, not short, current, originator, commercial, epoch hours (2 % tie), recency, id
    assert pick(cand("a")) == ("a", "single")
    assert pick(cand("a", tier="gauge", epoch=900), cand("b", tier="official", epoch=10)) == ("b", "tier")
    assert pick(cand("a", tier="model"), cand("b", tier="gauge", disputed=True)) == ("b", "tier")   # A3
    assert pick(cand("a", disputed=True, epoch=900), cand("b", epoch=10)) == ("b", "time_base")
    assert pick(cand("a", suspect=True, epoch=900), cand("b", epoch=10)) == ("b", "time_base")
    assert pick(cand("a", short=True), cand("b")) == ("b", "short_record")
    assert pick(cand("a", last=2.0e9 - 3 * YEAR, epoch=160000), cand("b", epoch=150000)) == ("b", "current")
    assert pick(cand("a", last=2.0e9 - 1 * YEAR, epoch=160000), cand("b", epoch=150000)) == ("a", "epoch_hours")
    assert pick(cand("a", orig=False, epoch=166560), cand("b", epoch=166541)) == ("b", "originator")
    assert pick(cand("a", commercial=False, epoch=166560), cand("b", epoch=100)) == ("b", "commercial")
    assert pick(cand("a", epoch=166560, last=1.9e9), cand("b", epoch=166541, last=1.95e9)) == ("b", "recency")
    assert pick(cand("a", epoch=166560), cand("b", epoch=160000)) == ("a", "epoch_hours")     # 3.9 %: not a tie
    assert pick(cand("b"), cand("a")) == ("a", "record_id")
    assert pick(cand("a", tier="official", short=True, good=1000), cand("b", good=50000)) == ("b", "check2_guard")


def test_a_set_with_no_constants_is_never_recommended_and_all_no_constants_is_null():
    # A4
    assert pick(cand("n", status="no_constants", good=90000), cand("s", short=True, good=5000)) == ("s", "single")
    assert pick(cand("n", status="no_constants")) == (None, "no_constants")


def test_a_set_that_disagrees_with_most_of_its_siblings_gives_way():
    # D4 agreement check: Cape Ferguson / Crozet / Uddevalla shape
    odd = cand("a", epoch=166560, m2=(0.6, 330.0))
    rest = [cand("b", epoch=100000), cand("c", epoch=90000), cand("d", epoch=80000)]
    win, how = recommend([odd] + rest)
    assert win.cs["source_record_id"] == "b" and how["decided_by"] == "sibling_agreement" and how["overridden"] == "S/a"
    win, how = recommend([odd, rest[0]])                     # two sets: no majority to ask
    assert win.cs["source_record_id"] == "a" and how["overridden"] is None


def test_a_merge_decision_may_name_any_records_of_the_stations():
    base = [rec("a", 60.0, 5.0), rec("a2", 60.005, 5.0), rec("b", 60.023, 5.0)]
    old = mint(base, {})
    assert len(old.stations) == 2
    new = mint(base + [rec("c", 60.014, 5.0)], {}, existing=old, decisions={"merge": [{"records": ["a2", "b"]}]},
               removed_in="20261101")
    assert set(ids(new).values()) == {hid("a")} and set(new.retired) == {hid("b")}


def test_a_station_whose_sets_have_different_licences_lists_every_licence(table):
    from test_build_integrity import NC
    from test_format_1_0 import LICENCE
    docs = two(table)
    release = built(table, docs, (100, 200), licences=[LICENCE, NC],
                    licence_ids={"r0-nor-test": "gesla-cmems-nc", "r1-nor-test": "gesla-derived"})
    assert [lic["licence_id"] for lic in release["licences"]] == ["gesla-cmems-nc", "gesla-derived"]
    assert {cs["licence_id"] for cs in release["stations"][0]["constant_sets"]} == {"gesla-cmems-nc", "gesla-derived"}
    assert validate_release(release) == []


# --- fix round 1: the rule ------------------------------------------------------------------------

def test_records_on_a_0_01_boundary_are_linked():
    # A1: float floor division put 20.14 and 20.15 two grid cells apart
    for la, lb in ((20.14, 20.15), (-33.87, -33.86), (0.0, 0.01)):
        m = mint([rec("a", la, 5.0), rec("b", lb, 5.0)], {})
        assert len(m.stations) == 1, (la, lb)
    m = mint([rec("a", 60.0, 5.0), rec("b", 60.0, 5.015)], {})   # longitude alone keeps them apart
    assert len(m.stations) == 2
    m = mint([rec("a", 10.0, 179.996), rec("b", 10.0, -179.997)], {})   # across the antimeridian
    assert len(m.stations) == 1


def test_a_separate_decision_moves_only_the_records_it_names():
    # A2: s is the bridge of a - s - b; separating s must not move b
    recs = [rec("a", 0.0, 0.0, "Port"), rec("s", 0.008, 0.0, "Port"), rec("b", 0.016, 0.0, "Port")]
    old = mint(recs, {})
    assert len(old.stations) == 1
    new = mint(recs, {}, existing=old, decisions={"separate": [{"records": ["s"]}]})
    assert ids(new)["a"] == ids(new)["b"] == hid("a") and ids(new)["s"] == hid("s")
    # also when the founder is the one that leaves
    new = mint(recs, {}, existing=old, decisions={"separate": [{"records": ["a"]}]})
    assert ids(new)["s"] == ids(new)["b"] == hid("a") and ids(new)["a"] != hid("a")
    assert new.stations[hid("a")]["founder"] == "b"


def test_the_rule_never_divides_an_existing_station_without_a_decision():
    # B1: two records linked only by an alias; the alias is gone at the re-mint
    old = mint([rec("a", 60.0, 5.0), rec("b", 60.5, 5.0)], {"a": ["T1"], "b": ["T1"]})
    assert len(old.stations) == 1
    with pytest.raises(MintConflict, match="divides"):
        mint([rec("a", 60.0, 5.0), rec("b", 60.5, 5.0)], {}, existing=old)


def test_a_retired_id_is_never_minted_again():
    # B2: merge a and b (b's id is retired), then separate b: b needs an id, and not its old one
    old = mint([rec("a", 60.0, 5.0), rec("b", 60.018, 5.0)], {})
    bridge = [rec("a", 60.0, 5.0), rec("b", 60.018, 5.0), rec("c", 60.009, 5.0)]
    merged = mint(bridge, {}, existing=old, decisions={"merge": [{"records": ["a", "b"]}]}, removed_in="20261101")
    assert hid("b") in merged.retired
    again = mint(bridge, {}, existing=merged, decisions={"separate": [{"records": ["b"]}]})
    assert ids(again)["b"] not in (hid("b"), hid("a")) and ids(again)["b"].startswith(hid("b"))


def test_decision_typos_and_overlaps_stop_the_mint():
    # A6
    recs = [rec("a", 60.0, 5.0), rec("b", 60.001, 5.0)]
    with pytest.raises(ValueError, match="neither a GESLA record"):
        mint(recs, {}, decisions={"separate": [{"records": ["bb"]}]})
    with pytest.raises(ValueError, match="two decisions"):
        mint(recs, {}, decisions={"separate": [{"records": ["a"]}], "merge": [{"records": ["a", "b"]}]})
    with pytest.raises(ValueError, match="two decisions"):
        mint(recs, {}, decisions={"separate": [{"records": ["a"]}, {"records": ["a"]}]})
    old = mint(recs, {})
    # a record of the committed map that left the input is not a typo
    assert mint([rec("a", 60.0, 5.0)], {}, existing=old, decisions={"separate": [{"records": ["b"]}]})


def test_registry_links_do_not_depend_on_which_record_sorts_first():
    # A8: r0's name disagrees; r1 and r2 (0.5 deg apart, one registry id) must still join
    recs = [rec("r0", 60.0, 5.0, "Elsewhere"), rec("r1", 60.2, 5.0, "Port"), rec("r2", 60.7, 5.0, "Port")]
    m = mint(recs, {r.record_id: ["T1"] for r in recs})
    assert ids(m)["r1"] == ids(m)["r2"] != ids(m)["r0"]


def test_different_tides_at_one_position_are_two_stations():
    # D1: the Ocean City / Ocean City Inlet case (M2 -41 %)
    recs = [rec("a", 38.33, -75.08, "Ocean_City"), rec("b", 38.33, -75.085, "Ocean_City_Inlet")]
    m = mint(recs, {}, tides={"a": tide(m2=(0.486, 1.5)), "b": tide(m2=(0.289, 9.2))})
    assert ids(m)["a"] != ids(m)["b"] and m.proposals[0]["reason"] == ["tides_disagree"]
    m = mint(recs, {}, tides={"a": tide(m2=(0.486, 1.5)), "b": tide(m2=(0.480, 2.5))})
    assert len(m.stations) == 1


@pytest.mark.parametrize("hours", [0.5, -1.0, 2.2, 8.0])
def test_a_clock_offset_joins_and_is_listed_as_a_time_base_defect(hours):
    recs = [rec("a", 50.0, 1.0), rec("b", 50.001, 1.0)]
    m = mint(recs, {}, tides={"a": SAME, "b": shifted(SAME, hours)})
    assert len(m.stations) == 1 and "clock_offset" in m.review[0]["reason"]
    (d,) = m.time_base_defects
    assert d["records"] == ["a", "b"] and d["clock_offset_h"] == pytest.approx(hours, abs=0.01)


def test_a_phase_difference_that_no_clock_offset_explains_splits():
    other = {**shifted(SAME, 1.0), "K1": (0.2, 50.0 + 40.0)}   # M2 says 1 h, K1 says otherwise
    m = mint([rec("a", 50.0, 1.0), rec("b", 50.001, 1.0)], {}, tides={"a": SAME, "b": other})
    assert len(m.stations) == 2


def test_a_record_with_no_fit_joins_by_position_and_name_and_is_flagged():
    recs = [rec("a", 50.0, 1.0), rec("b", 50.001, 1.0), rec("c", 50.0, 1.0, "Port", country="FRA")]
    m = mint(recs, {}, tides={"a": SAME})
    assert ids(m)["a"] == ids(m)["b"] != ids(m)["c"]
    assert "no_fit" in m.review[0]["reason"]


def test_one_uhslc_number_links_up_to_0_1_degree():
    # D2a: Akyab 907 / 907A (FD and RQ rounded differently)
    recs = [rec("a-907-mmr-uhslc_fd", 20.15, 92.90, "Akyab", "UHSLC_FD", code="907"),
            rec("a-907a-mmr-uhslc_rq", 20.08, 92.90, "Akyab", "UHSLC_RQ", code="907A"),
            rec("a-907b-mmr-uhslc_rq", 19.90, 92.90, "Akyab", "UHSLC_RQ", code="907B")]
    m = mint(recs, {}, tides=dict.fromkeys([r.record_id for r in recs], SAME))
    assert ids(m)["a-907-mmr-uhslc_fd"] == ids(m)["a-907a-mmr-uhslc_rq"] != ids(m)["a-907b-mmr-uhslc_rq"]
    # a UHSLC number does not join disagreeing phases, nor other agencies' codes
    m = mint(recs[:2], {}, tides={"a-907-mmr-uhslc_fd": SAME, "a-907a-mmr-uhslc_rq": tide(m2=(1.0, 140.0))})
    assert len(m.stations) == 2
    # amplitudes alone disagree (Dakar 223C): one number keeps them together within 1 km, flagged, and the odd
    # one is a suspect (C3); beyond 1 km they split (Settlement Point, Fanning 012B/012C, Lagos 233A)
    t3 = {"a-907-mmr-uhslc_fd": SAME, "a-907a-mmr-uhslc_rq": tide(m2=(0.8, 100.0)), "a-907c-mmr-uhslc_rq": SAME}
    near3 = [rec("a-907-mmr-uhslc_fd", 20.15, 92.90, "Akyab", "UHSLC_FD", code="907"),
             rec("a-907a-mmr-uhslc_rq", 20.155, 92.90, "Akyab", "UHSLC_RQ", code="907A"),
             rec("a-907c-mmr-uhslc_rq", 20.15, 92.90, "Akyab", "UHSLC_RQ", code="907C")]
    m = mint(near3, {}, tides=t3)
    assert len(m.stations) == 1 and "amplitude_disagrees" in m.review[0]["reason"]
    assert m.time_base_suspects["a-907a-mmr-uhslc_rq"]["kind"] == "amplitude_only"
    far3 = [near3[0], rec("a-907a-mmr-uhslc_rq", 20.17, 92.90, "Akyab", "UHSLC_RQ", code="907A"), near3[2]]
    m = mint(far3, {}, tides=t3)
    assert len(m.stations) == 2 and "a-907a-mmr-uhslc_rq" not in m.split_flags   # C4 flags only within 1 km
    assert len(mint([rec("x", 20.15, 92.9), rec("y", 20.15, 92.9)], {},
                    tides={"x": SAME, "y": tide(m2=(0.8, 100.0))}).stations) == 2   # not on a coord link
    assert mint([rec("x", 20.15, 92.9, "Akyab", "NOAA", code="907"), rec("y", 20.08, 92.9, "Akyab", "NOAA", code="907")],
                {}, tides={"x": SAME, "y": SAME}).stations.__len__() == 2


def test_one_name_within_0_05_degree_joins_only_with_agreeing_tides_and_identity():
    # D2b as narrowed by C1: whole-token names, tides that agree, and one site code or at most 1 km
    near = [rec("a", 60.0, 5.0, "Mokuoloe"), rec("b", 60.0, 5.015, "Mokuoloe")]   # 0.015 deg of longitude, 0.84 km
    assert len(mint(near, {}, tides={"a": SAME, "b": SAME}).stations) == 1
    assert len(mint(near, {}).stations) == 2                                     # no tide evidence
    assert len(mint(near, {}, tides={"a": SAME, "b": tide(m2=(0.5, 100))}).stations) == 2
    assert len(mint(near, {}, tides={"a": SAME, "b": shifted(SAME, 0.5)}).stations) == 2   # a clock offset is not "agree"
    contained = [rec("a", 60.0, 5.0, "Naples"), rec("b", 60.0, 5.015, "Gordon_River_Near_Naples")]
    assert len(mint(contained, {}, tides={"a": SAME, "b": SAME}).stations) == 2  # containment is not equality
    far = [rec("a", 21.433, -157.79, "Mokuoloe", "NOAA", code="1612480"),
           rec("b", 21.453, -157.79, "Mokuoloe", "NOAA", code="1612480")]        # 2.2 km, one agency code
    assert len(mint(far, {}, tides={"a": SAME, "b": SAME}).stations) == 1
    far_other = [far[0], rec("b", 21.453, -157.79, "Mokuoloe", "USGS", code="999")]
    assert len(mint(far_other, {}, tides={"a": SAME, "b": SAME}).stations) == 2
    beyond = [rec("a", 21.40, -157.79, "Mokuoloe", "NOAA", code="1"), rec("b", 21.46, -157.79, "Mokuoloe", "NOAA", code="1")]
    assert len(mint(beyond, {}, tides={"a": SAME, "b": SAME}).stations) == 2       # 0.06 deg


def test_a_country_code_difference_joins_when_the_tides_agree():
    # D2c: NOAA files foreign gauges under USA
    recs = [rec("a", 32.37, -64.70, "Bermuda", country="GBR"), rec("b", 32.37, -64.70, "Bermuda", country="USA")]
    assert len(mint(recs, {}, tides={"a": SAME, "b": SAME}).stations) == 1
    m = mint(recs, {})
    assert len(m.stations) == 2 and m.proposals[0]["reason"] == ["countries_disagree"]


def test_one_agency_with_several_site_codes_is_listed_for_review():
    # D3: Green Bay 9087078 -> 9087079; CMEMS codes and UHSLC version letters do not count
    t = {"a": SAME, "b": SAME}
    m = mint([rec("a", 44.5, -88.0, "Green_Bay", "NOAA", code="9087078"),
              rec("b", 44.5, -88.001, "Green_Bay", "NOAA", code="9087079")], {}, tides=t)
    assert m.review[0]["reason"] == ["agency_codes_differ"]
    m = mint([rec("a", 44.5, -88.0, "Suva", "UHSLC_RQ", code="018A"),
              rec("b", 44.5, -88.001, "Suva", "UHSLC_RQ", code="018B")], {}, tides=t)
    assert m.review == []
    m = mint([rec("a", 44.5, -88.0, "Dover", "CMEMS", code="Dov"), rec("b", 44.5, -88.001, "Dover", "CMEMS", code="DOV2")],
             {}, tides=t)
    assert m.review == []


def test_epoch_good_hours_counts_2002_to_2020():
    # B4
    s, e = sm.EPOCH_START_S, sm.EPOCH_END_S
    assert sm.epoch_good_hours([s - 3600, s, s + 3600, e - 3600, e, e + 3600]) == 3
    assert (s, e) == (1009843200, 1609459200)    # 2002-01-01T00Z, 2021-01-01T00Z


def test_the_station_takes_name_and_position_from_its_recommended_set(table):
    # B3
    docs = [doc(table, rid="r0-nor-test", meta={"SITE NAME": "Alpha", "LATITUDE": "60.39", "LONGITUDE": "5.32"}),
            doc(table, rid="r1-nor-test", meta={"SITE NAME": "Beta", "LATITUDE": "60.395", "LONGITUDE": "5.325"})]
    for hours, name, lat in (((100, 200), "Beta", 60.395), ((300, 200), "Alpha", 60.39)):
        (st,) = built(table, docs, hours)["stations"]
        assert (st["name"], st["lat"]) == (name, lat)


def test_a_disputed_set_is_retained_with_a_truthful_fallback_label(table):
    # U1b retains fitted values; F0b ranks an ordinary set before a fallback.
    disputed = tb_row("disputed", comparator_kind="b", time_base_code="disputed:comparators_disagree",
                      time_base_reason="declared utc_instant kept; evidence disagrees")
    release = built(table, two(table, rows=(disputed, disputed)), (100, 9000))
    (st,) = release["stations"]
    assert {cs["source_record_id"] for cs in st["constant_sets"]} == {"r0-nor-test", "r1-nor-test"}
    assert all(cs["qc_status"] == "flagged" and cs["fallback"]["reason"] == "time_base_disputed"
               and cs["constituents"] for cs in st["constant_sets"])
    assert validate_release(release) == []
    # the disputed set has more epoch hours, so it would rank first on every other step
    release = built(table, two(table, rows=(None, disputed)), (100, 9000))
    (st,) = release["stations"]
    assert [cs["source_record_id"] for cs in st["constant_sets"]] == ["r0-nor-test", "r1-nor-test"]
    assert st["recommended_set_id"].endswith("r0-nor-test") and st["aliases"]["gesla"] == ["r0-nor-test", "r1-nor-test"]
    assert "fallback" not in st["constant_sets"][0]
    assert st["constant_sets"][1]["fallback"]["reason"] == "time_base_disputed"
    assert st["constant_sets"][1]["fallback"]["preferred_set_id"] == st["recommended_set_id"]
    assert validate_release(release) == []


def test_a_time_base_suspect_is_flagged_and_not_recommended_over_a_clean_set(table):
    docs = two(table)
    sus = {"r1-nor-test": {"station_id": "OTC-ONE", "kind": "clock_offset", "clock_offset_h": -8.0,
                           "against": ["r0-nor-test"], "decided_by": "eot20"}}
    release = built(table, docs, (100, 9000), sibling_flags=sus)
    (st,) = release["stations"]
    assert st["recommended_set_id"].endswith("r0-nor-test")
    cs = next(c for c in st["constant_sets"] if c["source_record_id"] == "r1-nor-test")
    (flag,) = [f for f in cs["qc_flags"] if f["flag"] == "sibling_disagreement"]
    assert flag["values"] == {"clock_offset_h": -8.0} and cs["qc_status"] == "flagged"
    assert validate_release(release) == []


def test_retired_ids_are_written_as_tombstones(table):
    # A5
    docs = two(table)
    tomb = [{"station_id": "OTC-OLD", "status": "removed", "removed_in": "20261010", "removed_reason": "merged",
             "merged_into": "OTC-ONE", "detail": "merge decision"}]
    release = built(table, docs, (1, 2), tombstones=tomb)
    assert [s["station_id"] for s in release["stations"] if s["status"] == "removed"] == ["OTC-OLD"]
    assert validate_release(release) == []
    with pytest.raises(ValueError, match="not a station of this release"):
        built(table, docs, (1, 2), tombstones=[{**tomb[0], "merged_into": "OTC-GONE"}])
    m = mint([rec("a", 60.0, 5.0), rec("b", 60.018, 5.0)], {})
    m = mint([rec("a", 60.0, 5.0), rec("b", 60.018, 5.0), rec("c", 60.009, 5.0)], {}, existing=m,
             decisions={"merge": [{"records": ["a", "b"]}]}, removed_in="20261101")
    assert m.tombstones() == [{"station_id": hid("b"), "status": "removed", "removed_in": "20261101",
                               "removed_reason": "merged", "merged_into": hid("a"),
                               "detail": "merge decision (data/otc/station_decisions.json)"}]


def test_validate_release_allows_null_only_when_every_set_is_no_constants(table):
    # A4 / build check 6
    release = built(table, two(table), (1, 2))
    st = release["stations"][0]
    st["recommended_set_id"] = None
    assert any("not every set is no_constants" in e for e in validate_release(release))
    for cs in st["constant_sets"]:
        cs["qc_status"] = "no_constants"
    assert not any("recommended_set_id is null" in e for e in validate_release(release))


# TICON-4's 56 RWS identities absent from GESLA 4.1. Audited source aliases
# keep existing IDs; uncertain offshore crosslinks stay separate. This does not
# assert scientific agreement or 56 new places.
TICON_ONLY_RWS = {
    "amaliahaven-amlahvn-nld-rws": "OTC-deca8f61",
    "amelander_westgat_platform-awgpfm-nld-rws": "OTC-7ad4aeb2",
    "antwerpen_prosperpolder-antwppppdr-nld-rws": "OTC-91910ee0",
    "aukfield_platform-aukfpfm-nld-rws": "OTC-a4f93015",
    "bergse_diepsluis_west-bergsdswt-nld-rws": "OTC-341526a1",
    "brouwershavensche_gat_08-brouwhvsgt08-nld-rws": "OTC-cdb2418f",
    "brouwershavensche_gat_punt_02-brouwhvsgt02-nld-rws": "OTC-ee7df2cd",
    "cadzand-cadzd-nld-rws": "OTC-33e52ffe",
    "eemshaven-eemshvn-nld-rws": "OTC-57575ba0",
    "euro_platform-eurpfm-nld-rws": "OTC-730d7688",
    "geulhaven-geulhvn-nld-rws": "OTC-0f8f071b",
    "goidschalxoord-goidsod-nld-rws": "OTC-cae6b6a0",
    "gouda_brug-goudbg-nld-rws": "OTC-f693ace0",
    "ijmuiden_buitenhaven-ijmdbthvn-nld-rws": "OTC-826c08c4",
    "ijmuiden_stroommeetpaal-ijmdsmpl-nld-rws": "OTC-b45a591d",
    "k13a_platform-k13apfm-nld-rws": "OTC-d97bb15f",
    "l9_platform-l9pfm-nld-rws": "OTC-0ec58bb9",
    "lauwersoog-lauwog-nld-rws": "OTC-d5b7f36a",
    "lichteiland_goeree-lichtelgre-nld-rws": "OTC-8ed6229a",
    "maasmond_stroommeetpaal-maasmsmpl-nld-rws": "OTC-2d587551",
    "maeslantkering_zeezijde-maeslkrzzde-nld-rws": "OTC-59fe48a8",
    "marollegat-marlgt-nld-rws": "OTC-62687e1f",
    "moerdijk-moerdk-nld-rws": "OTC-34162955",
    "north_cormorant-northcmrt-nld-rws": "OTC-67992fae",
    "oosterschelde_11-oostsde11-nld-rws": "OTC-13a3a3b4",
    "oosterschelde_13-oostsde13-nld-rws": "OTC-c5a3e2c2",
    "oosterschelde_14-oostsde14-nld-rws": "OTC-4c864fcf",
    "oosterschelde_15-oostsde15-nld-rws": "OTC-8721828f",
    "oudeschild-oudsd-nld-rws": "OTC-c9acbb22",
    "parksluis-parkss-nld-rws": "OTC-f464598f",
    "petten_zuid-pettzd-nld-rws": "OTC-890e4883",
    "platform_a12-a12-nld-rws": "OTC-657a5879",
    "platform_d15_a-d15-nld-rws": "OTC-74c13e16",
    "platform_f16_a-f16-nld-rws": "OTC-607a1052",
    "platform_hoorn_q1_a-q1-nld-rws": "OTC-588e03ba",
    "platform_j6-j6-nld-rws": "OTC-d4c14187",
    "schaar_van_de_noord-schaarvdnd-nld-rws": "OTC-0951a691",
    "scheveningen-schevngn-nld-rws": "OTC-6165b135",
    "schiermonnikoog-schiermnog-nld-rws": "OTC-58c58a98",
    "schoonhoven-schoonhvn-nld-rws": "OTC-8c144a20",
    "sint_annaland_haven_steiger-sintanlhvsgr-nld-rws": "OTC-05ae1373",
    "spijkenisse-spijknse-nld-rws": "OTC-a4ffdacb",
    "stavenisse-stavnse-nld-rws": "OTC-5e1e6b46",
    "stellendam_buiten-stelldbtn-nld-rws": "OTC-455311cf",
    "suurhofbrug_noordzijde-suurhbnzde-nld-rws": "OTC-887930b2",
    "terneuzen-ternzn-nld-rws": "OTC-3eccfc5c",
    "terschelling_noordzee-terslnze-nld-rws": "OTC-89261d11",
    "texel_noordzee-texnze-nld-rws": "OTC-d21b1c7d",
    "vlissingen-vlis-nld-rws": "OTC-c4170535",
    "walsoorden-walsodn-nld-rws": "OTC-e31edaa7",
    "werkendam_buiten-werkdbtn-nld-rws": "OTC-9e9bac97",
    "west_terschelling-westtslg-nld-rws": "OTC-bb915177",
    "westkapelle-westkple-nld-rws": "OTC-be2b5365",
    "wierumergronden-wiermgdn-nld-rws": "OTC-c1dd24b1",
    "yerseke-yerske-nld-rws": "OTC-63ef1949",
    "zeelandbrug_noord-zeelbnd-nld-rws": "OTC-e1923a56",
}


def test_the_committed_map_retains_all_56_ticon_only_rws_identities():
    mapped = sm.load().record_ids()
    assert len(TICON_ONLY_RWS) == 56
    assert {rid: mapped.get(rid) for rid in TICON_ONLY_RWS} == TICON_ONLY_RWS


@pytest.mark.parametrize("rid, gesla_alias", [
    ("amaliahaven-amlahvn-nld-rws", "maasvlakte_amaliahaven-maa-nld-rws"),
    ("scheveningen-schevngn-nld-rws", "scheveningen-sch-nld-rws"),
    ("vlissingen-vlis-nld-rws", "vlissingen-vli-nld-rws"),
])
def test_actual_ticon_rws_aliases_keep_the_existing_gauge_id(rid, gesla_alias):
    mapped = sm.load().record_ids()
    assert mapped.get(rid) == mapped[gesla_alias]


@pytest.mark.parametrize("rid, possible_alias", [
    ("platform_hoorn_q1_a-q1-nld-rws", "q11tg-q11-nld-cmems"),
    ("platform_j6-j6-nld-rws", "j61tg-j61-nld-cmems"),
    ("l9_platform-l9pfm-nld-rws", "l91tg-l91-nld-cmems"),
    ("north_cormorant-northcmrt-nld-rws", "northcormoranttg-nor-gbr-cmems"),
])
def test_unverified_offshore_crosslinks_do_not_merge_existing_gauges(rid, possible_alias):
    mapped = sm.load().record_ids()
    assert mapped.get(rid) == TICON_ONLY_RWS[rid]
    assert mapped[rid] != mapped[possible_alias]
