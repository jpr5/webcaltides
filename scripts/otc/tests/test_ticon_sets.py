"""TICON-4 fallback (owner decision: the best data until we can't, then the best data we can use): a GESLA
record whose own OTC fit is withheld is published with TICON-4's constants, as a source_type analysis set
under the ticon-4 licence, with OTC's time-base annotation of the same record. Synthetic numbers only."""
import hashlib
import json

import pytest

from otc_pipeline import gesla_release as gr
from otc_pipeline import ticon_sets as ts
from otc_pipeline.gesla_sets import time_base_annotation
from otc_pipeline.release_writer import validate_release

from synthetic_congen import congen_run, kp0b_tables
from test_format_1_0 import D, LICENCE, SPEEDS, TABLE_ID, doc, table, tb_row  # noqa: F401  (table: fixture)

HEAD = ("lat,lon,con,amp,pha,amp_std,pha_std,missing_obs,no_of_obs,years_of_obs,start_date,end_date,gesla_source,"
        "tide_gauge_name,type,country,record_quality,datum_information")
# the same tide as test_format_1_0.fit_dict()'s OTC fit (in cm), so the convention canary passes
CONS = (("M2", 100.0, 100.0), ("S2", 30.0, 130.0), ("K1", 10.0, 200.0), ("O1", 5.0, 0.0), ("M1", 1.0, 5.0),
        ("3N2", 0.2, 33.0))
HELD = {"licence_id": "gesla-chs-withheld", "spdx": "LicenseRef-CHS", "provider": "TEST ONLY", "attribution": "TEST ONLY",
        "commercial_use": False, "restriction": "TEST ONLY", "terms_url": "https://example.org/terms"}
WHY = "CHS: withheld until CHS permits"


def ticon_csv(tmp_path, recs):
    """recs: (name, source, quality) -> a TICON-4 shaped CSV; returns (path, sha256)."""
    lines = [HEAD]
    for name, src, quality in recs:
        for con, amp, pha in CONS:
            lines.append(f"60.0,5.0,{con},{amp},{pha},0.01,0.01,0.1,1000,2.0,01/01/2010,31/12/2011,{src},{name},"
                         f"Coastal,NOR,{quality},MSL")
    p = tmp_path / "ticon.csv"
    p.write_text("\n".join(lines) + "\n")
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


WINDOW = ("2010-01-01", "2011-12-31")    # the audit's checked span, covering the synthetic TICON span 2010-2011


def carry(tmp_path, recs, tbs, **kw):
    path, sha = ticon_csv(tmp_path, recs)
    return ts.TiconCarry(ts.load_ticon(path, sha256=sha), tbs, "20261011 test",
                         {name: WINDOW for name, _, _ in recs}, **kw)


def annotation(status="verified", **over):
    rid = over.pop("rid", "held-1-can-meds")
    return time_base_annotation(tb_row(status, **over) if status else None, 0, rid, decided_in="20261011",
                                decided_build_commit="f5a026a")


def kw(table, docs, ids, lic_ids, **over):
    k = dict(table=table, table_id=TABLE_ID, datestamp=D, created="2026-10-10T00:00:00Z", build_commit="abcdef0",
             station_ids=ids, licences=[LICENCE, HELD], licence_ids=lic_ids, withheld={HELD["licence_id"]: WHY},
             refused=[], epoch_hours={d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 0} for d in docs},
             contributor_roles={"TEST": "originator"}, sibling_flags={}, tombstones=[],
             station_map_inputs={"fit_run_results_sha256": "f" * 64}, fit_run_results_sha256="f" * 64,
             disputed_reasons={}, sampling_s={d["record_id"]: 3600 for d in docs},
             astro_tables=kp0b_tables(congen_run(table), SPEEDS, TABLE_ID), congen_run=congen_run(table))
    k.update(over)
    return k


HELD_IDS = ("held-1-can-meds", "held-2-can-meds", "held-3-can-meds")


def three_held(table):
    good = doc(table, rid="good-0-nor-test", row=tb_row())
    held = [doc(table, rid=r, row=tb_row()) for r in HELD_IDS]
    ids = {"good-0-nor-test": "OTC-T0000", **{r: f"OTC-T000{i + 1}" for i, r in enumerate(HELD_IDS)}}
    lic = {"good-0-nor-test": "gesla-derived", **{r: HELD["licence_id"] for r in HELD_IDS}}
    return [good] + held, ids, lic


def test_a_withheld_record_is_published_with_ticon4_constants_licence_and_source(table, tmp_path):
    docs, ids, lic = three_held(table)
    # RED surface: without the fallback the withheld gauges are absent
    release, report = gr.build_release(docs, **kw(table, docs, ids, lic))
    assert [s["station_id"] for s in release["stations"]] == ["OTC-T0000"]
    # GREEN: the same build with TICON-4 carries them
    c = carry(tmp_path, [(r, "gesla4.MEDS", "No obvious issues") for r in HELD_IDS],
              {r: annotation(rid=r) for r in HELD_IDS})
    release, report = gr.build_release(docs, ticon=c, **kw(table, docs, ids, lic))
    assert validate_release(release) == []
    st = next(s for s in release["stations"] if s["station_id"] == "OTC-T0001")
    (cs,) = st["constant_sets"]
    assert st["recommended_set_id"] == cs["set_id"] == "OTC-T0001/ticon-4/held-1-can-meds"
    assert (cs["source"], cs["source_type"], cs["licence_id"], cs["convention_id"]) == \
        ("ticon-4", "analysis", "ticon-4", ts.CONVENTION_ID)
    assert cs["time_base"]["code"] == "verified" and cs["qc_status"] == "ok"
    assert "modified" not in cs["provenance"] and cs["provenance"]["input_sha256"] == [ts.TICON_SHA256]
    m2 = next(x for x in cs["constituents"] if x["name"] == "M2")
    assert (m2["amplitude_m"], m2["phase_deg"]) == (1.0, 100.0)            # cm -> m, phase as published
    assert {d["name"]: d["dropped_reason"] for d in cs["dropped_constituents"]} == {"M1": "convention", "3N2": "convention"}
    assert st["aliases"]["ticon"] == ["held-1-can-meds"] and st["aliases"]["gesla"] == ["held-1-can-meds"]
    (lrow,) = [x for x in release["licences"] if x["licence_id"] == "ticon-4"]
    assert lrow == ts.LICENCE and "TICON-4 (DGFI-TUM), CC BY 4.0" in lrow["attribution"]
    assert release["release"]["source_versions"] == {"gesla": "4.1", "ticon": "4"}
    (conv,) = [x for x in release["conventions"] if x["convention_id"] == ts.CONVENTION_ID]
    assert conv["canary"] == {"status": "pass", "gauges": 3, "median_amp_diff_m": 0.0, "median_phase_diff_deg": 0.0}
    assert report.withheld_stations == {}
    assert [(x["record"], x["ticon"], x["how"]) for x in report.carried] == [(r, r, "id") for r in HELD_IDS]
    # the OTC fit of the withheld record stays refused: it is only a check, never published
    assert ("held-1-can-meds", "withheld") in {(r["record"], r["reason"]) for r in report.refused}
    assert not any(c2["source"] == "gesla-fit" and c2["source_record_id"] == "held-1-can-meds"
                   for s in release["stations"] for c2 in s.get("constant_sets", []))


@pytest.mark.parametrize("status,src,why", [("disputed", "gesla4.MEDS", "time_base_disputed"),
                                            ("verified", "gesla4.CMEMS", "cmems_relayed")])
def test_disputed_and_cmems_relayed_records_are_not_carried(table, tmp_path, status, src, why):
    held = doc(table, rid="held-1-can-meds", row=tb_row())
    over = {"time_base_reason": "declared utc_instant kept; evidence disagrees"} if status == "disputed" else {}
    c = carry(tmp_path, [("held-1-can-meds", src, "No obvious issues")], {"held-1-can-meds": annotation(status, **over)})
    good = doc(table, rid="good-0-nor-test", row=tb_row())
    ids = {"good-0-nor-test": "OTC-T0000", "held-1-can-meds": "OTC-T0001"}
    lic = {"good-0-nor-test": "gesla-derived", "held-1-can-meds": HELD["licence_id"]}
    release, report = gr.build_release([good, held], ticon=c, **kw(table, [good, held], ids, lic))
    assert [s["station_id"] for s in release["stations"]] == ["OTC-T0000"]
    assert [(x["record"], x["reason"]) for x in report.not_carried] == [("held-1-can-meds", why)]
    assert report.withheld_stations == {"OTC-T0001": WHY}
    assert validate_release(release) == []


def test_a_source_that_fails_the_convention_check_stops_the_build(table, tmp_path):
    docs, ids, lic = three_held(table)
    path, sha = ticon_csv(tmp_path, [(r, "gesla4.MEDS", "No obvious issues") for r in HELD_IDS])
    recs = ts.load_ticon(path, sha256=sha)
    # every phase 3 deg off: within the 5 cm quality gate, not within the canary's 2 deg
    c = ts.TiconCarry({n: ts.TiconRecord(**{**r.__dict__, "constituents": {k: (a, p + 3.0) for k, (a, p) in
                                                                          r.constituents.items()}})
                       for n, r in recs.items()}, {r: annotation(rid=r) for r in HELD_IDS}, "test",
                      {r: WINDOW for r in HELD_IDS})
    with pytest.raises(ValueError, match="fails its convention check"):
        gr.build_release(docs, ticon=c, **kw(table, docs, ids, lic))
    c1 = ts.TiconCarry(recs, {HELD_IDS[0]: annotation(rid=HELD_IDS[0])}, "test", {r: WINDOW for r in HELD_IDS})   # one overlapping gauge only
    with pytest.raises(ValueError, match="'gauges': 1"):
        gr.build_release(docs, ticon=c1, **kw(table, docs, ids, lic))


def test_unverified_is_flagged_and_a_quality_note_is_carried(table, tmp_path):
    rec = ts.load_ticon(*ticon_csv(tmp_path, [("x-1-can-meds", "gesla4.MEDS", "Possible datum issues")]))["x-1-can-meds"]
    cs = ts.ticon_set(rec, time_base=annotation(None, rid="x-1-can-meds"), table=table, licence_id="ticon-4",
                      record_licence="gesla-chs-withheld")
    assert cs["qc_status"] == "flagged"
    assert [f["flag"] for f in cs["qc_flags"]] == ["time_base", "source_quality_note"]
    assert "Possible datum issues" in cs["qc_flags"][1]["reason"]


def test_a_corrected_time_base_moves_the_phases_and_says_so(table, tmp_path):
    rec = ts.load_ticon(*ticon_csv(tmp_path, [("x-1-can-meds", "gesla4.MEDS", "No obvious issues")]))["x-1-can-meds"]
    plus1 = annotation("corrected", rid="x-1-can-meds", time_base_code="corrected:utc_instant->utc_plus_1",
                       published_time_base="utc_plus_1", time_base_reason="declared utc_instant corrected to utc_plus_1")
    cs = ts.ticon_set(rec, time_base=plus1, table=table, licence_id="ticon-4", record_licence="gesla-chs-withheld",
                      checked_window=WINDOW)
    m2 = next(x for x in cs["constituents"] if x["name"] == "M2")
    # stamps are UTC+1: the instant is one hour earlier, so g = g_published - speed x 1 h
    assert m2["phase_deg"] == pytest.approx((100.0 - SPEEDS["M2"]) % 360) and m2["amplitude_m"] == 1.0
    assert "speed times -1 h" in cs["provenance"]["modified"]
    mean = annotation("corrected", rid="x-1-can-meds", time_base_code="corrected:utc_instant->utc_mean_start60",
                      published_time_base="utc_mean_start60", comparator_kind="a", comparator="sib-1~corrected",
                      time_base_reason="declared utc_instant corrected to utc_mean_start60")
    cs = ts.ticon_set(rec, time_base=mean, table=table, licence_id="ticon-4", record_licence="gesla-chs-withheld",
                      checked_window=WINDOW)
    m2 = next(x for x in cs["constituents"] if x["name"] == "M2")
    assert m2["phase_deg"] == pytest.approx(100.0 + SPEEDS["M2"] / 2) and m2["amplitude_m"] > 1.0
    assert "hourly-mean response" in cs["provenance"]["modified"]
    dst = {**plus1, "published": "local_dst:Europe/Oslo", "correction": {"from": "utc_instant", "to": "local_dst:Europe/Oslo"}}
    with pytest.raises(ts.NotCarried) as e:
        ts.ticon_set(rec, time_base=dst, table=table, licence_id="ticon-4", record_licence="gesla-chs-withheld",
                      checked_window=WINDOW)
    assert e.value.reason == "time_base_not_shift"


def test_the_download_is_pinned(tmp_path):
    path, _sha = ticon_csv(tmp_path, [("x-1-can-meds", "gesla4.MEDS", "No obvious issues")])
    with pytest.raises(ValueError, match="pinned TICON-4 download"):
        ts.load_ticon(path)


def test_an_analysis_set_ranks_after_a_gauge_fit_and_before_a_model_set():
    assert gr.TIER["gauge"] < gr.TIER["analysis"] < gr.TIER["model"]
    a = gr.Candidate({"source_type": "analysis", "source_record_id": "a", "quantity": "water_level"}, 100.0, 10)
    g = gr.Candidate({"source_type": "gauge", "source_record_id": "b", "quantity": "water_level"}, 0.0, 0)
    assert [c.cs["source_record_id"] for c, _ in gr.rank([a, g])] == ["b", "a"]


def test_a_clean_ticon_set_never_makes_an_unresolved_otc_fit_publishable(table, tmp_path):
    docs, ids, lic = three_held(table)
    ids = {**ids, "good-0-nor-test": "OTC-T0001"}          # the published record shares held-1's station
    clock = {"kind": "clock_offset", "clock_offset_h": -8.1, "against": ["held-1-can-meds"], "decided_by": "eot20"}
    c = carry(tmp_path, [(r, "gesla4.MEDS", "No obvious issues") for r in HELD_IDS], {r: annotation(rid=r) for r in HELD_IDS})
    release, report = gr.build_release(docs, ticon=c, **kw(table, docs, ids, lic, sibling_flags={"good-0-nor-test": clock}))
    st = next(s for s in release["stations"] if s["station_id"] == "OTC-T0001")
    assert [cs["set_id"] for cs in st["constant_sets"]] == ["OTC-T0001/ticon-4/held-1-can-meds"]
    assert st["recommended_set_id"] == "OTC-T0001/ticon-4/held-1-can-meds"
    assert ("good-0-nor-test", "clock_offset_unresolved") in {(r["record"], r["reason"]) for r in report.refused}
    assert report.published == []
    assert validate_release(release) == []
    # the same flag on the withheld record itself: its TICON-4 set (the same data) carries the flag, as an OTC fit
    # would, and the clean OTC fit is recommended
    release, report = gr.build_release(docs, ticon=c, **kw(table, docs, ids, lic,
                                                           sibling_flags={"held-1-can-meds": clock}))
    st = next(s for s in release["stations"] if s["station_id"] == "OTC-T0001")
    assert [cs["source"] for cs in st["constant_sets"]] == ["gesla-fit", "ticon-4"]
    tcs = st["constant_sets"][1]
    assert tcs["qc_status"] == "flagged" and [f["flag"] for f in tcs["qc_flags"]] == ["sibling_disagreement"]
    assert validate_release(release) == []


def test_a_cmems_relayed_record_is_refused_by_ticon_set_itself(table, tmp_path):
    # match() already skips it; ticon_set refuses it too, so no other caller can put CC BY-NC under ticon-4
    rec = ts.load_ticon(*ticon_csv(tmp_path, [("x-1-gbr-cmems", "gesla4.CMEMS", "No obvious issues")]))["x-1-gbr-cmems"]
    with pytest.raises(ts.NotCarried) as e:
        ts.ticon_set(rec, time_base=annotation(rid="x-1-gbr-cmems"), table=table, licence_id="ticon-4",
                     record_licence="gesla-bodc-nc")
    assert e.value.reason == "cmems_relayed"


# --- quality gates (TICON-FB fix rounds r2, r3) ---------------------------------------------------------------

import math  # noqa: E402

import numpy as np  # noqa: E402

from gesla.tcd_table import TcdTable  # noqa: E402

GATE_SPEEDS = {**SPEEDS, "SA": 0.0410686, "MK3": 44.0251729, "MM": 0.5443747}


@pytest.fixture(scope="module")
def table_sa():
    return TcdTable({"years": [2020, 2022], "tcd_sha256": "c" * 64, "aliases": {},
                     "constituents": {n: {"speed": s, "v0u": [10.0 * i for i in range(3)], "f": [1.0] * 3}
                                      for n, s in GATE_SPEEDS.items()}})


def rec_of(cons, std=None, name="x-1-can-meds", gauge_type="Coastal", n_obs=17000):
    return ts.TiconRecord(name, 60.0, 5.0, "gesla4.MEDS", "01/01/2010", "31/12/2011", "No obvious issues",
                          constituents={n: (a, p) for n, a, p in cons},
                          amp_std={n: (std or {}).get(n, 0.01) for n, _, _ in cons}, gauge_type=gauge_type,
                          n_obs=n_obs)


OTC_CONS = (("M2", 100.0, 100.0), ("S2", 30.0, 130.0), ("K1", 10.0, 200.0), ("O1", 5.0, 0.0))
FAR = (("M2", 120.0, 100.0), ("S2", 30.0, 130.0), ("K1", 10.0, 200.0), ("O1", 5.0, 0.0))   # M2 20 cm over OTC's


def otc_doc(cons=OTC_CONS, halves=None, u=0.001, good_hours=None):
    fit = {"constituents": {n: {"amp_m": a / 100, "phase_deg": p, "u_m": u} for n, a, p in cons}}
    if halves:
        fit["halves"] = halves
    return {"fit": fit, **({"good_hours": good_hours} if good_hours is not None else {})}


def gate(table, rec, doc_, refs=()):
    cs = ts.ticon_set(rec, time_base=annotation(rid=rec.name), table=table, licence_id="ticon-4",
                      record_licence="gesla-chs-withheld", checked_window=WINDOW)
    return ts.quality_gate(cs, rec=rec, fit_doc=doc_, references=list(refs), table=table)


def const_ref(kind, cons, km=1.0, rid="official:jma:XX"):
    return {"kind": kind, "id": rid, "km": km, "constituents": {n: [a / 100, p] for n, a, p in cons}}


def series_ref(table, cons, months=(1, 4, 7, 10), year=2021, rid="official:chs:00001", noise=0.0):
    """Official predictions made from `cons` (cm, deg): hourly month segments of `year`."""
    import datetime as dt
    t0 = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc).timestamp()
    segs, rng = [], np.random.default_rng(7)
    for m in months:
        start = dt.datetime(year, m, 1, tzinfo=dt.timezone.utc).timestamp()
        h = (start - t0) / 3600 + np.arange(30 * 24)
        v = ts._predict({n: (a / 100, p) for n, a, p in cons}, table, year, h) + noise * rng.standard_normal(len(h))
        segs.append({"start_s": int(start), "values": [float(x) for x in v]})
    return {"kind": "official_series", "id": rid, "km": 0.5, "segments": segs}


def quality(f):
    with pytest.raises(ts.NotCarried) as e:
        f()
    assert e.value.reason == "ticon_quality"
    return str(e.value)


def test_within_5_cm_of_otc_over_the_shared_lines_passes_and_otc_only_lines_do_not_count(table_sa):
    # D3: OTC's fit holds an 8 cm MK3 (poorly determined, so no omission) that TICON-4 does not publish: not a
    # disagreement (counted, it would be 5.7 cm)
    assert gate(table_sa, rec_of(OTC_CONS), otc_doc(OTC_CONS + (("MK3", 8.0, 50.0),), u=0.05))
    assert "no independent reference" in quality(lambda: gate(table_sa, rec_of(FAR), otc_doc()))


def test_omission_of_a_well_determined_line_that_moves_high_and_low_waters_refuses(table_sa):
    # a small, flat diurnal tide: a 6 cm MK3 moves the extremes by far more than 15 min
    diurnal = (("K1", 20.0, 200.0), ("O1", 15.0, 10.0), ("M2", 3.0, 40.0))
    msg = quality(lambda: gate(table_sa, rec_of(diurnal), otc_doc(diurnal + (("MK3", 6.0, 80.0),))))
    assert "lacks MK3" in msg and "min" in msg
    # the same line poorly determined (its uncertainty over a third of it) is not an omission
    assert gate(table_sa, rec_of(diurnal), otc_doc(diurnal + (("MK3", 6.0, 80.0),), u=0.03))
    # nor is a line whose absence moves the extremes little (a large semidiurnal tide)
    assert gate(table_sa, rec_of(OTC_CONS), otc_doc(OTC_CONS + (("MK3", 2.5, 80.0),)))


def test_an_official_series_decides_in_the_time_domain_with_and_without_the_long_period_lines(table_sa):
    ok = gate(table_sa, rec_of(FAR), otc_doc(), [series_ref(table_sa, FAR)])            # the series is TICON-4's tide
    assert "official predictions official:chs:00001" in ok["provenance"]["selection_reason"]
    assert "side with OTC" in quality(lambda: gate(table_sa, rec_of(FAR), otc_doc(), [series_ref(table_sa, OTC_CONS)]))
    halfway = (("M2", 110.0, 100.0),) + FAR[1:]                                          # 10 / 10 cm: not 0.8
    assert "not clearly closer" in quality(lambda: gate(table_sa, rec_of(FAR), otc_doc(),
                                                        [series_ref(table_sa, halfway)]))
    # the whole set must be clearly closer too: TICON-4's tide is the series', but its MM is 30 cm off it
    mm = FAR + (("MM", 30.0, 10.0),)
    assert "not clearly closer" in quality(lambda: gate(table_sa, rec_of(mm), otc_doc(), [series_ref(table_sa, FAR)]))
    # and the tidal lines must be closer unless they agree with OTC's: OTC's SA is wrong (so the whole set is clearly
    # closer to the series), but the series' tide is halfway between the two
    t, o = FAR + (("SA", 30.0, 10.0),), OTC_CONS + (("SA", 80.0, 10.0),)
    assert "not clearly closer" in quality(lambda: gate(table_sa, rec_of(t), otc_doc(o), [series_ref(
        table_sa, (("M2", 110.0, 100.0),) + OTC_CONS[1:] + (("SA", 30.0, 10.0),))]))
    # a series outranks a constants-only reference
    assert "side with OTC" in quality(lambda: gate(
        table_sa, rec_of(FAR), otc_doc(), [series_ref(table_sa, OTC_CONS), const_ref("official_constants", FAR)]))


def test_a_constants_only_reference_counts_only_when_it_can_see_the_difference(table_sa):
    # JMA-like M2 S2 K1 O1 that side with TICON-4 on M2
    ok = gate(table_sa, rec_of(FAR), otc_doc(), [const_ref("official_constants", FAR)])
    assert "closer to TICON-4" in ok["provenance"]["selection_reason"]
    # the disagreement is also in SA (8 cm, outside the reference's lines): the reference cannot see it
    far_sa = FAR + (("SA", 30.0, 10.0),)
    msg = quality(lambda: gate(table_sa, rec_of(far_sa), otc_doc(OTC_CONS + (("SA", 22.0, 10.0),), good_hours=9000.0),
                               [const_ref("official_constants", FAR)]))
    assert "cannot see the difference" in msg


def test_a_neighbour_must_discriminate_by_1_cm(table_sa):
    # TICON-4 and OTC differ in SSA (10 cm, which the neighbour has); M2 S2 K1 O1 are the same, so it decides on SSA
    t = OTC_CONS + (("SSA", 20.0, 40.0),)
    o = OTC_CONS + (("SSA", 10.0, 40.0),)
    seen = dict(good_hours=9000.0)                                                      # OTC's fit sees the seasons
    n_near = const_ref("neighbour_fit", OTC_CONS + (("SSA", 19.5, 40.0),), rid="gesla-fit:n")
    assert gate(table_sa, rec_of(t), otc_doc(o, **seen), [n_near])
    n_flat = const_ref("neighbour_fit", OTC_CONS + (("SSA", 15.0, 40.0),), rid="gesla-fit:n")   # 5 against 5 cm
    assert "does not discriminate" in quality(lambda: gate(table_sa, rec_of(t), otc_doc(o, **seen), [n_flat]))


def test_sa_is_dropped_only_when_that_brings_the_set_closer_and_within_5_cm(table_sa):
    # OTC's fit, from a year of record, has no SA: without TICON-4's 30 cm SA the set equals OTC's -> dropped, declared
    out = gate(table_sa, rec_of(OTC_CONS + (("SA", 30.0, 10.0),)), otc_doc(good_hours=9000.0))
    (sa,) = [d for d in out["dropped_constituents"] if d["name"] == "SA"]
    assert sa["dropped_reason"] == "other" and "closer to OTC's own fit" in sa["detail"]
    # both have the same SA (2 cm apart): it is kept, never dropped
    kept = gate(table_sa, rec_of(OTC_CONS + (("SA", 30.0, 10.0),)), otc_doc(OTC_CONS + (("SA", 28.0, 10.0),),
                                                                             good_hours=9000.0))
    assert "SA" in {c["name"] for c in kept["constituents"]}
    # B1: both have a large SA, 8 cm apart (5.7 cm RMS); dropping it is farther from OTC -> kept, over 5 cm: refused
    assert "no independent reference" in quality(lambda: gate(
        table_sa, rec_of(OTC_CONS + (("SA", 52.0, 10.0),)), otc_doc(OTC_CONS + (("SA", 44.0, 10.0),), good_hours=9000.0)))
    # with an official series that has a seasonal signal, its monthly means decide; the kept variant must be within
    # 5 cm (here the series' SA is 3 cm: a signal, but far from TICON-4's 30 cm, so dropping it is closer)
    t = FAR + (("SA", 30.0, 10.0),)
    out = gate(table_sa, rec_of(t), otc_doc(), [series_ref(table_sa, FAR + (("SA", 3.0, 10.0),))])
    assert "SA" in {d["name"] for d in out["dropped_constituents"]} and "monthly mean" in \
        next(d for d in out["dropped_constituents"] if d["name"] == "SA")["detail"]
    kept = gate(table_sa, rec_of(t), otc_doc(), [series_ref(table_sa, t)])            # the series has TICON-4's SA
    assert "SA" in {c["name"] for c in kept["constituents"]}
    wrong = FAR + (("SA", 30.0, 190.0),)                                                 # the series' SA is opposite
    assert "monthly mean levels" in quality(lambda: gate(table_sa, rec_of(t), otc_doc(OTC_CONS + (("SA", 30.0, 10.0),)),
                                                         [series_ref(table_sa, wrong)]))


def test_the_standard_error_gate_lifts_within_5_cm_of_otc_or_per_line_of_the_deciding_reference(table_sa):
    lifted = gate(table_sa, rec_of(OTC_CONS, {"S2": 2.5}), otc_doc())
    assert "standard error of S2 2.5 cm" in lifted["provenance"]["selection_reason"]
    assert gate(table_sa, rec_of(FAR, {"M2": 5.0}), otc_doc(), [const_ref("official_constants", FAR)])   # M2 in it
    jma_m2 = const_ref("official_constants", (FAR[0],))                                  # the reference has only M2
    msg = quality(lambda: gate(table_sa, rec_of(FAR, {"S2": 5.0}), otc_doc(), [jma_m2]))
    assert "standard error of S2" in msg and "does not contain the line" in msg
    assert gate(table_sa, rec_of(FAR, {"S2": 5.0}), otc_doc(), [series_ref(table_sa, FAR)])               # a series has all


def test_ticon_refs_build_and_load_are_reproducible_and_hashed(tmp_path, table):
    from otc_pipeline import ticon_refs
    cache = tmp_path / "cache"
    (cache / "chs").mkdir(parents=True)
    (cache / "chs2026").mkdir()
    (cache / "chs_stations.json").write_text(json.dumps([{"code": "00001", "id": "abc", "latitude": 60.0, "longitude": 5.0,
                                                         "officialName": "X", "timeSeries": [{"code": "wlp"}]}]))
    rows = [{"eventDate": f"2024-01-{1 + h // 24:02d}T{h % 24:02d}:00:00Z", "value": 0.1 * (h % 7)} for h in range(31 * 24)]
    (cache / "chs" / "00001_01.json").write_text(json.dumps(rows))
    (cache / "chs" / "00001_07.json").write_text(json.dumps(rows[:100]))                 # too short: not used
    (cache / "station2026.html").write_text("<html></html>")
    csv_path, _ = ticon_csv(tmp_path, [("x-1-can-meds", "gesla4.MEDS", "No obvious issues")])
    weak = tmp_path / "weak.json"
    weak.write_text(json.dumps({"records": {"x-1-can-meds": {"eot": {"M2": [1.0, 100.0], "dist_km": 2.0}}}}))
    doc_ = ticon_refs.build_references(cache, csv_path, weak)
    (series, eot) = doc_["records"]["x-1-can-meds"]
    assert series["kind"] == "official_series" and len(series["segments"][0]["values"]) == 31 * 24
    assert len(series["segments"]) == 1 and "chs/00001_07.json" not in doc_["inputs"]
    assert eot["kind"] == "eot20" and "chs/00001_01.json" in doc_["inputs"]
    out = tmp_path / "refs.json"
    out.write_text(json.dumps(doc_, sort_keys=True))
    refs, sha = ticon_refs.load_references(out)
    assert refs == doc_["records"] and sha == hashlib.sha256(out.read_bytes()).hexdigest()
    assert ticon_refs.build_references(cache, csv_path, weak) == doc_                      # reproducible


def test_the_references_sha256_is_in_every_carried_set(table, tmp_path):
    docs, ids, lic = three_held(table)
    c = carry(tmp_path, [(r, "gesla4.MEDS", "No obvious issues") for r in HELD_IDS],
              {r: annotation(rid=r) for r in HELD_IDS}, references_sha256="e" * 64)
    release, _ = gr.build_release(docs, ticon=c, **kw(table, docs, ids, lic))
    sets = [cs for s in release["stations"] for cs in s.get("constant_sets", []) if cs["source"] == "ticon-4"]
    assert len(sets) == 3 and all(cs["provenance"]["input_sha256"] == [ts.TICON_SHA256, "e" * 64] for cs in sets)


def test_gate_b_otc_half_record_fits_that_disagree(table):
    def half(k1, ph=200.0):
        return {"constituents": {"M2": {"amp_m": 0.3, "phase_deg": 100.0}, "K1": {"amp_m": k1, "phase_deg": ph}}}
    big_k1 = (("M2", 30.0, 100.0), ("S2", 5.0, 130.0), ("K1", 30.0, 200.0), ("O1", 5.0, 0.0))
    broken = otc_doc(big_k1, {"half1": half(0.30), "half2": half(0.10, 20.0)})   # K1 changes in the second half
    with pytest.raises(ts.NotCarried, match="half-record fits") as e:
        gate(table, rec_of(big_k1), broken)
    assert e.value.reason == "ticon_quality"
    assert gate(table, rec_of(big_k1), otc_doc(big_k1, {"half1": half(0.30), "half2": half(0.29)}))


FAR = (("M2", 120.0, 100.0), ("S2", 30.0, 130.0), ("K1", 10.0, 200.0), ("O1", 5.0, 0.0))   # M2 20 cm over OTC's


def test_verified_holds_only_where_the_checked_window_covers_ticons_span(table):
    rec = rec_of(OTC_CONS)
    cs = ts.ticon_set(rec, time_base=annotation(rid=rec.name), table=table, licence_id="ticon-4",
                      record_licence="gesla-chs-withheld", checked_window=("2012-06-01", "2013-01-01"))
    assert cs["time_base"]["code"] == "unverified" and cs["time_base"]["published"] == "utc_instant"
    assert "does not cover" in cs["time_base"]["reason"] and cs["qc_flags"][0]["flag"] == "time_base"
    near = ts.ticon_set(rec, time_base=annotation(rid=rec.name), table=table, licence_id="ticon-4",
                        record_licence="gesla-chs-withheld", checked_window=("2011-06-01", "2012-06-01"))
    assert near["time_base"]["code"] == "verified"                       # within 2 years at both ends
    plus1 = annotation("corrected", rid=rec.name, time_base_code="corrected:utc_instant->utc_plus_1",
                       published_time_base="utc_plus_1", time_base_reason="declared utc_instant corrected to utc_plus_1")
    with pytest.raises(ts.NotCarried) as e:
        ts.ticon_set(rec, time_base=plus1, table=table, licence_id="ticon-4", record_licence="gesla-chs-withheld",
                     checked_window=("2015-01-01", "2016-01-01"))
    assert e.value.reason == "time_base_not_covered"


def test_an_analysis_set_never_outranks_a_gauge_set_even_by_the_override_rules():
    def cand(st, rid, m2):
        return gr.Candidate({"source_type": st, "source_record_id": rid, "set_id": rid, "quantity": "water_level",
                             "qc_status": "ok", "constituents": [
                                 {"name": "M2", "amplitude_m": m2, "phase_deg": 100.0},
                                 {"name": "K1", "amplitude_m": 0.1, "phase_deg": 200.0}]}, 0.0, 0)
    # the one gauge fit disagrees with three analysis sets: sibling_agreement would pick an analysis set
    g = cand("gauge", "g", 2.0)
    a = [cand("analysis", f"a{i}", 1.0) for i in range(3)]
    win, how = gr.recommend([g] + a)
    assert win is g and how["overridden"] is None
    # with no gauge set, the analysis sets compete among themselves as before
    win, _ = gr.recommend(a)
    assert win.cs["source_type"] == "analysis"


def test_r4_an_official_series_never_sides_with_otc_against_ticon_on_any_path(table_sa):
    # TICON-4 is within 5 cm of OTC's fit (the shared-lines pass), but the series is 6 cm from TICON-4, 1.4 from OTC
    t = (("M2", 106.5, 100.0),) + OTC_CONS[1:]
    series = series_ref(table_sa, (("M2", 98.0, 100.0),) + OTC_CONS[1:])
    assert "side with OTC's fit" in quality(lambda: gate(table_sa, rec_of(t), otc_doc(), [series]))
    assert gate(table_sa, rec_of(t), otc_doc())                                          # without the series it passes
    # within 5 cm of the series, or not clearly on OTC's side: no veto
    assert gate(table_sa, rec_of(t), otc_doc(), [series_ref(table_sa, (("M2", 103.0, 100.0),) + OTC_CONS[1:])])


def test_r4_n1_seasonal_error_shared_with_otcs_own_sa_is_carried_with_a_note(table_sa):
    sa_wrong = (("SA", 30.0, 100.0),)                                                    # both fits' SA, 90 deg off
    t, o = OTC_CONS + sa_wrong, OTC_CONS + sa_wrong
    series = series_ref(table_sa, OTC_CONS + (("SA", 30.0, 10.0),))
    out = gate(table_sa, rec_of(t), otc_doc(o, good_hours=9000.0), [series])
    (note,) = [f for f in out["qc_flags"] if f["flag"] == "source_quality_note"]
    assert out["qc_status"] == "flagged" and "seasonal agreement is limited for both" in note["reason"]
    # r5: at a river site the note says what users meet: decimetres with river flow, no figure against predictions
    river = gate(table_sa, rec_of(t, gauge_type="River"), otc_doc(o, good_hours=9000.0), [series])
    (rn,) = [f for f in river["qc_flags"] if f["flag"] == "source_quality_note"]
    assert "several decimetres with river flow" in rn["reason"] and "cm" not in rn["reason"]
    assert note["values"]["seasonal_error_m"] <= note["values"]["otc_seasonal_error_m"] + 0.01
    # OTC's SA from under a year of record does not count
    assert "monthly mean levels" in quality(lambda: gate(table_sa, rec_of(t), otc_doc(o, good_hours=8000.0), [series]))
    # OTC's own seasonal levels match the series: TICON-4's error is its own
    assert "monthly mean levels" in quality(lambda: gate(
        table_sa, rec_of(t), otc_doc(OTC_CONS + (("SA", 30.0, 10.0),), good_hours=9000.0), [series]))


def test_r4_a_series_without_a_seasonal_signal_never_decides_sa(table_sa):
    # TICON-4 and OTC agree on a 30 cm SA; the series has none: OTC's full fit decides, SA is kept
    t = OTC_CONS + (("SA", 30.0, 10.0),)
    out = gate(table_sa, rec_of(t), otc_doc(t), [series_ref(table_sa, OTC_CONS)])
    assert "SA" in {c["name"] for c in out["constituents"]}


def test_r5_the_seasonal_variant_is_chosen_among_those_that_pass_the_later_gates(table_sa):
    # N2: the series' monthly means prefer the set without SA (the series has a 3 cm SA, TICON-4 and OTC 12 cm), but
    # without SA the set is 8.5 cm from OTC's fit and the series, 17 cm off both tides, cannot rescue it; the full set
    # passes every gate (its seasonal error is within OTC's own + 1 cm, OTC's SA is from a year of record): carried
    t = OTC_CONS + (("SA", 12.0, 10.0),)
    series = series_ref(table_sa, (("M2", 100.0, 110.0),) + OTC_CONS[1:] + (("SA", 3.0, 10.0),))
    out = gate(table_sa, rec_of(t), otc_doc(t, good_hours=9000.0), [series])
    assert "SA" in {c["name"] for c in out["constituents"]}
    assert "first choice, without SA, is refused" in out["provenance"]["selection_reason"]
    # r6: OTC's fit from under a year cannot see the seasons, so it is no seasonal evidence: the series' choice stands
    out = gate(table_sa, rec_of(t), otc_doc(t, good_hours=8000.0), [series])
    assert "SA" not in {c["name"] for c in out["constituents"]}


def test_r5_from_6_months_a_series_decides_sa_only_with_a_fitted_annual_term_of_2_cm(table_sa):
    t = OTC_CONS + (("SA", 30.0, 10.0),)
    # 12 months whose monthly means span over 2 cm (MM-like 1.5 cm SSA-free wobble from an SSA of 1.5 cm) but whose
    # annual term is under 2 cm: no seasonal signal, OTC's full fit decides, and the agreed SA is kept
    no_annual = series_ref(table_sa, OTC_CONS + (("SSA", 1.5, 0.0),), months=tuple(range(1, 13)))
    assert ts._Series(no_annual, table_sa).seasonal_range() >= 0.02
    out = gate(table_sa, rec_of(t), otc_doc(t, good_hours=9000.0), [no_annual])
    assert "SA" in {c["name"] for c in out["constituents"]}
    # with an annual term of 3 cm in 12 months, the series decides (and prefers the set without TICON-4's 30 cm SA)
    annual = series_ref(table_sa, OTC_CONS + (("SA", 3.0, 10.0),), months=tuple(range(1, 13)))
    assert ts._Series(annual, table_sa).has_seasonal_signal()
    s5 = ts._Series(series_ref(table_sa, OTC_CONS + (("SSA", 1.5, 0.0),), months=(1, 3, 5, 7, 9)), table_sa)
    assert s5.has_seasonal_signal() == (s5.seasonal_range() >= 0.02)                  # under 6 months: the span


def test_r6_with_no_source_that_sees_the_seasons_ticons_sa_is_kept_unless_its_std_err_fails(table_sa):
    t = OTC_CONS + (("SA", 30.0, 10.0),)
    # OTC's fit from under a year has no SA, no series: neither can see the seasons -> TICON-4's SA is kept, and SA is
    # left out of the TICON-vs-OTC distance (counted, it would be 21 cm)
    out = gate(table_sa, rec_of(t), otc_doc(good_hours=5000.0))
    assert "SA" in {c["name"] for c in out["constituents"]}
    # its own standard error fails it (30 cm with 3 cm): dropped, declared, and the set is carried
    out = gate(table_sa, rec_of(t, {"SA": 3.0}), otc_doc(good_hours=5000.0))
    (sa,) = [d for d in out["dropped_constituents"] if d["name"] == "SA"]
    assert "own standard error" in sa["detail"] and "no source here can see the seasonal cycle" in sa["detail"]
    # a year of record: OTC's fit is evidence (its absent SA counts), and the set is closer without TICON-4's SA
    out = gate(table_sa, rec_of(t), otc_doc(good_hours=9000.0))
    assert "SA" in {d["name"] for d in out["dropped_constituents"]}


def test_r6_a_series_sees_the_seasons_only_with_a_two_harmonic_signal_and_an_annual_term(table_sa):
    months = tuple(range(1, 13))
    sig = lambda cons: ts._Series(series_ref(table_sa, OTC_CONS + cons, months=months), table_sa).has_seasonal_signal()
    assert sig((("SA", 3.0, 10.0),))                                                      # annual 3 cm
    assert sig((("SA", 1.6, 10.0), ("SSA", 1.6, 0.0)))                                     # 2.3 cm combined
    assert not sig((("SA", 1.2, 10.0), ("SSA", 1.2, 0.0)))                                 # 1.7 cm combined
    assert not sig((("SSA", 5.0, 0.0),))                     # semiannual only: the agency has no SA -> never decides SA
    # Holyrood-like: the series has only a semiannual cycle; OTC's fit (a year) agrees with TICON-4's SA -> kept
    t = OTC_CONS + (("SA", 8.0, 250.0),)
    out = gate(table_sa, rec_of(t), otc_doc(t, good_hours=9000.0),
               [series_ref(table_sa, OTC_CONS + (("SSA", 5.0, 0.0),), months=months)])
    assert "SA" in {c["name"] for c in out["constituents"]}


def test_r6_a_site_where_neither_set_follows_the_official_predictions_says_so(table_sa):
    off = (("M2", 100.0, 105.0),) + OTC_CONS[1:]                    # 8.7 cm (RMS 6.2) from both TICON-4 and OTC
    out = gate(table_sa, rec_of(OTC_CONS), otc_doc(), [series_ref(table_sa, off)])
    (note,) = [f for f in out["qc_flags"] if f["flag"] == "source_quality_note"]
    assert "tidal agreement with the official predictions" in note["reason"] and out["qc_status"] == "flagged"
    assert note["values"]["tidal_error_m"] > 0.05 and note["values"]["otc_tidal_error_m"] > 0.05
    # both over 5 cm, TICON-4 clearly closer (7 against 21 cm, the BC/Fundy case): no such note
    ok = gate(table_sa, rec_of(FAR), otc_doc(), [series_ref(table_sa, (("M2", 130.0, 100.0),) + OTC_CONS[1:])])
    assert not any("tidal agreement" in f["reason"] for f in ok.get("qc_flags", []))


def test_r7_with_no_seasonal_source_ticons_sa_needs_a_year_of_its_own_data(table_sa):
    t = OTC_CONS + (("SA", 12.0, 247.0), ("SSA", 3.0, 100.0))
    # Stanovan-like: neither OTC's fit (under a year) nor a series can see the seasons, and TICON-4's own analysis
    # has 7,312 hourly samples: SA and SSA are dropped, declared, and the set is carried
    out = gate(table_sa, rec_of(t, n_obs=7312), otc_doc(good_hours=5444.0))
    assert {"SA", "SSA"} <= {d["name"] for d in out["dropped_constituents"]}
    assert "7312 hours of data, under a year" in next(d for d in out["dropped_constituents"] if d["name"] == "SA")["detail"]
    # a year of hourly data (Holyrood: 9,179): kept
    assert {"SA", "SSA"} <= {c["name"] for c in gate(table_sa, rec_of(t, n_obs=9179),
                                                      otc_doc(good_hours=7021.0))["constituents"]}
    # hours count the record's sampling interval: 60,000 10-minute samples are 10,000 hours (kept); 20,000 are 3,333
    # hours (dropped), though 20,000 hourly samples would be over a year
    cs = ts.ticon_set(rec_of(t, n_obs=60000), time_base=annotation(), table=table_sa, licence_id="ticon-4",
                      record_licence="gesla-chs-withheld", checked_window=WINDOW)
    out = ts.quality_gate(cs, rec=rec_of(t, n_obs=60000), fit_doc=otc_doc(good_hours=5000.0), references=[],
                          table=table_sa, interval_s=600)
    assert "SA" in {c["name"] for c in out["constituents"]}                              # 10,000 hours
    out = ts.quality_gate(cs, rec=rec_of(t, n_obs=20000), fit_doc=otc_doc(good_hours=5000.0), references=[],
                          table=table_sa, interval_s=600)
    assert "SA" not in {c["name"] for c in out["constituents"]}
