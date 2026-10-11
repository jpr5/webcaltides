"""Station map and recommendation, fix round 6 (final-confirm-A/C): flags never overwrite each other, the
EOT20 reference needs EOT20 evidence, one constituent never makes a clock offset, umlaut readings, the gaps
the surviving mutants showed, the constituent list, and the written order of a station's sets."""
import pytest

from otc_pipeline import gesla_release as gr
from otc_pipeline import station_map as sm
from otc_pipeline.gesla_release import recommend, set_order
from otc_pipeline.station_map import compare_tides, mint, offset_sides

from test_build_integrity import dump, table  # noqa: F401 (fixtures)
from test_format_1_0 import doc, fit_dict
from test_station_map import SAME, rec, shifted, tide, two
from test_station_map_r3 import _kw, cand


# --- item 1: a record with a split-off flag and a suspect flag keeps both ---------------------------------

SPLIT = {"kind": "split_off", "verdict": "clock_offset_far", "clock_offset_h": -1.0, "distance_km": 1.55,
         "against": ["dakar-223-sen-uhslc_fd"], "decided_by": "eot20", "station_id": "OTC-ONE"}
SUSPECT = {"kind": "amplitude_only", "clock_offset_h": None, "against": ["dakar-223c-sen-uhslc_rq"],
           "decided_by": "majority", "station_id": "OTC-ONE"}


def test_both_flags_of_a_record_survive_in_the_station_map():
    m = sm.StationMap(stations={"OTC-ONE": {"founder": "a", "records": ["a"]}},
                      split_flags={"a": SPLIT}, time_base_suspects={"a": SUSPECT})
    assert m.sibling_flags() == {"a": [SPLIT, SUSPECT]}


def test_a_station_of_split_off_offset_records_is_withheld_whatever_their_suspect_flags(table):  # noqa: F811
    # Dakar OTC-afdd8665: 223B and 223D are on the offset side (split flag, -1 h) and also amplitude suspects
    docs = two(table)
    one = {d["record_id"]: "OTC-ONE" for d in docs}
    with pytest.raises(ValueError, match="no record is publishable"):
        gr.build_release(docs, **_kw(table, docs, one, {d["record_id"]: [SPLIT, SUSPECT] for d in docs}))
    for order in ([SUSPECT, SPLIT], [SPLIT, SUSPECT]):                    # the order of the flags does not matter
        other = {d["record_id"]: f"OTC-{i}" for i, d in enumerate(docs)}
        release, report = gr.build_release(docs, **_kw(table, docs, other, {"r1-nor-test": order}))
        assert [s["station_id"] for s in release["stations"]] == ["OTC-0"]
        assert report.counts() == {"clock_offset_unresolved": 1}


def test_every_flag_is_written_and_the_clock_offset_is_named(table):  # noqa: F811
    docs = two(table)
    one = {d["record_id"]: "OTC-ONE" for d in docs}
    (st,) = gr.build_release(docs, **_kw(table, docs, one, {"r1-nor-test": [SPLIT, SUSPECT]}))[0]["stations"]
    cs = next(c for c in st["constant_sets"] if c["source_record_id"] == "r1-nor-test")
    flags = [f for f in cs["qc_flags"] if f["flag"] == "sibling_disagreement"]
    assert len(flags) == 2
    assert flags[0]["values"] == {"distance_km": 1.55, "clock_offset_h": -1.0} and "-1.00 h (clock_offset_h)" in flags[0]["reason"]
    assert "values" not in flags[1] and "amplitudes disagree" in flags[1]["reason"]


# --- item 2: a class with no EOT20 evidence is never the EOT20 reference ------------------------------------

def test_a_class_without_eot20_evidence_never_wins_by_eot20():
    # Dakar OTC-afdd8665: 223C (no EOT20 lag) against 223A/B/D (EOT20 -1 h): no class is on time
    t = {"a": SAME, "b": SAME, "d": SAME, "c": tide(m2=(0.75, 100.0))}
    recs = {r: rec(r, 14.69, -17.43) for r in "abcd"}
    out = offset_sides({"S": {"records": list("abcd")}}, t, {"a": (-1, -60.0), "b": (-1, -60.0), "d": (-1, -57.0)}, [], recs)
    assert set(out) == {"c"} and out["c"]["decided_by"] == "majority"       # the majority, not "eot20"
    review = []
    t2 = {"a": SAME, "c": tide(m2=(0.75, 100.0))}
    assert offset_sides({"S": {"records": ["a", "c"]}}, t2, {"a": (-1, -60.0)}, review, recs) == {}
    assert review and review[0]["reason"] == ["clock_side_undecided"]


# --- item 3: the split flag says who decided the side --------------------------------------------------------

def test_a_split_flag_records_how_its_side_was_decided():
    clean = [rec(f"c{i}", 14.68, -17.42, "Dakar") for i in range(3)]
    off = [rec(f"o{i}", 14.69, -17.43, "Dakar") for i in range(3)]
    t = {**{r.record_id: SAME for r in clean}, **{r.record_id: shifted(SAME, -1.0) for r in off}}
    reg = {r.record_id: ["T1"] for r in clean + off}
    e = {**{f"c{i}": (0, 0.0) for i in range(3)}, **{f"o{i}": (-1, -60.0) for i in range(3)}}
    m = mint(clean + off, reg, tides=t, eot20=e)
    assert {f["decided_by"] for f in m.split_flags.values()} == {"eot20"} and set(m.split_flags) == {"o0", "o1", "o2"}
    m = mint(clean + off, reg, tides=t, eot20={})
    assert {f["decided_by"] for f in m.split_flags.values()} == {"size"}


# --- item 4: one constituent never defines a clock offset ----------------------------------------------------

def test_a_clock_offset_needs_two_constituents():
    # Neustadt: only M2 is above 2 cm; with S2, K1 and O1 at 1-2 cm the four agree on +0.49 h, not -11.93 h
    a = {"M2": (0.062, 100.0), "S2": (0.011, 50.0), "K1": (0.015, 200.0), "O1": (0.018, 150.0)}
    b = {"M2": (0.062, 114.2), "S2": (0.011, 64.7), "K1": (0.015, 207.4), "O1": (0.018, 156.8)}
    assert compare_tides(a, b) == ("clock_offset", 0.49)
    lone = {"M2": (0.062, 100.0), "S2": (0.001, 50.0), "K1": (0.002, 200.0), "O1": (0.003, 150.0)}
    lone_b = {"M2": (0.062, 114.2), "S2": (0.001, 90.0), "K1": (0.002, 20.0), "O1": (0.003, 10.0)}
    assert compare_tides(lone, lone_b)[0] == "disagree"          # M2 alone: no clock offset
    assert sm.CLOCK_MIN_AGREE == 2 and sm.CLOCK_SUPPORT_MIN_M == 0.01


# --- item 6: umlauts and diacritics -------------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [("Hrnum", "HoernumTG"), ("Hörnum", "Hornum"), ("Hörnum", "Hoernum"), ("Eckernfrde", "EckernfoerdeTG"),
                                  ("Bsum", "BuesumTG"), ("Göteborg", "Goteborg"), ("Råhe", "Rahe"), ("Søby", "Soby"),
                                  ("Saint-Malo", "Saint_Malo"), ("Bréhat", "Brehat"), ("Wittowerfhre", "WittowerFaehreTG")])
def test_umlaut_and_diacritic_readings_agree(a, b):
    assert sm.site_names_agree(a, b)


def test_umlaut_readings_still_respect_side_words_and_numbers():
    assert not sm.site_names_agree("Schleuse_Oberwasser", "Schleuse_Unterwasser")
    assert not sm.site_names_agree("Hörnum_2", "Hornum")
    assert sm.names_equal("Hrnum", "Hörnum") and not sm.names_equal("Hrnum", "Hrnum_Hafen")


# --- item 8: the surviving mutants --------------------------------------------------------------------------

def test_the_sibling_agreement_tie_does_not_override():
    # w agrees with b and disagrees with c (a tie); b agrees with both, so only a strict majority keeps w
    w = cand("a", m2=(1.0, 100.0), epoch=166000)
    b = cand("b", m2=(1.0, 108.0), epoch=100000)
    c = cand("c", m2=(1.0, 116.0), epoch=90000)
    win, how = recommend([w, b, c])
    assert win is w and how["overridden"] is None


def test_a_clock_offset_sibling_counts_as_a_disagreement():
    # three siblings one hour late: the winner disagrees with all three, so a clean late one takes over
    w = cand("a", epoch=166000)
    late = [cand(f"b{i}", m2=(1.0, 128.98), epoch=100000 - i) for i in range(3)]
    for c in late:
        c.cs["constituents"] = [{"name": n, "amplitude_m": a_, "phase_deg": p} for n, (a_, p) in shifted(SAME, 1.0).items()]
    w.cs["constituents"] = [{"name": n, "amplitude_m": a_, "phase_deg": p} for n, (a_, p) in SAME.items()]
    win, how = recommend([w] + late)
    assert how["decided_by"] == "sibling_agreement" and how["overridden"] == "S/a"


def test_all_zero_epoch_hours_reach_the_next_step():
    # every set has 0 epoch hours: the tie must keep them all for recency (the top == 0 guard)
    assert recommend([cand("a", epoch=0, last=1.9e9), cand("b", epoch=0, last=1.95e9)])[1]["decided_by"] == "recency"


def test_only_a_uhslc_fast_delivery_record_is_a_fast_delivery_copy(table):  # noqa: F811
    docs = [doc(table, rid="x-192-nor-uhslc_fd", meta={"CONTRIBUTOR (ABBREVIATED)": "UHSLC_FD"}),
            doc(table, rid="x-192c-nor-uhslc_rq", meta={"CONTRIBUTOR (ABBREVIATED)": "UHSLC_RQ"})]
    one = {d["record_id"]: "OTC-ONE" for d in docs}
    kw = {**_kw(table, docs, one, {}), "contributor_roles": {"UHSLC_FD": "redistributor", "UHSLC_RQ": "redistributor"}}
    release, report = gr.build_release(docs, **kw)
    assert release["stations"][0]["recommended_set_id"].endswith("x-192c-nor-uhslc_rq")
    assert report.recommendations[0]["decided_by"] == "research_quality"


def test_an_unlisted_contributor_is_not_an_originator(table):  # noqa: F811
    docs = [doc(table, rid="a-1-nor-test", meta={"CONTRIBUTOR (ABBREVIATED)": "NEWAGENCY"}),
            doc(table, rid="b-1-nor-test", meta={"CONTRIBUTOR (ABBREVIATED)": "NOAA"})]
    one = {d["record_id"]: "OTC-ONE" for d in docs}
    kw = {**_kw(table, docs, one, {}), "contributor_roles": {"NOAA": "originator"}}
    release, report = gr.build_release(docs, **kw)
    assert release["stations"][0]["recommended_set_id"].endswith("b-1-nor-test")
    assert report.recommendations[0]["decided_by"] == "originator"


def test_an_official_set_with_no_comparison_settles_nothing():
    # the official set has no K1: it cannot be compared, so the sibling rule decides
    off = cand("o", tier="official", short=True, good=100)
    off.cs["constituents"] = [{"name": "M2", "amplitude_m": 1.0, "phase_deg": 100.0}]
    w = cand("a", m2=(0.6, 100.0), epoch=166000)
    sib = [cand("b", epoch=100000), cand("c", epoch=90000), cand("d", epoch=80000)]
    win, how = recommend([off, w] + sib)
    assert how["decided_by"] == "sibling_agreement" and how["overridden"] == "S/a"


# --- item 9: withheld sets add no constituents -----------------------------------------------------------------

def test_withheld_sets_add_no_constituents(table):  # noqa: F811
    big = fit_dict()
    big["constituents"] = {**big["constituents"], "SSA": {"amp_m": 0.05, "phase_deg": 10.0, "u_m": 0.001}}
    big["snr_dropped"] = []
    docs = [doc(table, rid="r0-nor-test"), doc(table, rid="r1-nor-test", fit=big)]
    other = {d["record_id"]: f"OTC-{i}" for i, d in enumerate(docs)}
    release, report = gr.build_release(docs, **_kw(table, docs, other, {"r1-nor-test": [SPLIT]}))
    assert report.counts() == {"clock_offset_unresolved": 1}
    assert "SSA" not in {c["name"] for c in release["constituents"]}


# --- the written order of a station's sets ----------------------------------------------------------------------

def test_a_station_s_sets_are_written_in_recommendation_order(table):  # noqa: F811
    docs = [doc(table, rid=f"r{i}-nor-test") for i in range(4)]
    one = {d["record_id"]: "OTC-ONE" for d in docs}
    kw = _kw(table, docs, one, {})
    kw["epoch_hours"] = {d["record_id"]: {"member_sha256": d["member_sha256"], "hours": h}
                         for d, h in zip(docs, (100, 400, 300, 200))}
    release, report = gr.build_release(docs, **kw)
    (st,) = release["stations"]
    got = [cs["source_record_id"] for cs in st["constant_sets"]]
    assert got == ["r1-nor-test", "r2-nor-test", "r3-nor-test", "r0-nor-test"]
    assert st["constant_sets"][0]["set_id"] == st["recommended_set_id"]


def test_set_order_puts_the_recommended_set_first_then_the_ranking_then_no_constants():
    a, b, c = cand("a", epoch=100), cand("b", epoch=300), cand("c", epoch=200)
    n = cand("n", status="no_constants")
    assert [x.cs["source_record_id"] for x in set_order(a, [a, b, c, n])] == ["a", "b", "c", "n"]   # an override winner
    assert [x.cs["source_record_id"] for x in set_order(b, [n, c, b, a])] == ["b", "c", "a", "n"]
    tie = [cand("y"), cand("x")]                                                    # a full tie: record id decides
    assert [x.cs["source_record_id"] for x in set_order(None, tie)] == ["x", "y"]


# --- fix-r8: a fit the build cannot annotate yet still gives tide evidence ----------------------------------

def test_a_fit_whose_audit_row_the_build_cannot_read_still_counts_as_tide_evidence(tmp_path, dump):  # noqa: F811
    from test_build_integrity import make_run
    from test_format_1_0 import tb_row
    row = tb_row("disputed", file_id="x-1-nor-test", time_base_code="disputed:sibling_constants_offset",
                 comparator_kind="w", comparator="disputed:sibling_constants_offset", official_lag="", lag_median="")
    run, audit, rel = make_run(tmp_path, dump, {"x-1-nor-test": fit_dict(), "y-1-nor-test": fit_dict()}, [row])
    wi = tmp_path / "wi.json"
    wi.write_text('{"records": {}}')
    tides, hours, eot20, pins, texts = sm.derived_inputs(run, wi, rel, audit_dir=audit, tcd=dump)
    assert set(tides) == {"x-1-nor-test", "y-1-nor-test"} and tides["x-1-nor-test"]["M2"] == (1.0, 100.0)
    assert pins["tides_without_annotation"] == ["x-1-nor-test"]
    (run / "fits" / "x-1-nor-test.json").write_text("{}")          # a fit file the manifest does not bind
    import pytest as _p
    with _p.raises(Exception):
        sm.derived_inputs(run, wi, rel, audit_dir=audit, tcd=dump)
