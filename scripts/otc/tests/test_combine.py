"""C1 (combine): the agency sets in the release next to the GESLA build (RELEASE-AGENCY).

Synthetic sets and stations only (no cache, no network). The real build is the RELEASE-AGENCY proof."""
from __future__ import annotations

import copy
import hashlib
import math

import pytest

from otc_pipeline import combine
from otc_pipeline import currents_canary as cc
from otc_pipeline import station_map as sm
from otc_pipeline.adapter import SourceSet
from otc_pipeline.adapters.noaa import NoaaAdapter
from otc_pipeline.adapters.noaa_currents import NoaaCurrentsAdapter
from otc_pipeline.combine import AgencySource, CombineError, Place
from otc_pipeline.current_proof import Comparison

NOAA_LIC = NoaaAdapter().licence()
TABLE = "tcd-20251228"


def test_noaa_sources_share_one_licence_object():
    # one release holds one licence per licence_id: both NOAA adapters must give the same object
    assert NoaaAdapter().licence() == NoaaCurrentsAdapter().licence()


# --- builders ---------------------------------------------------------------------------------------

def cons(m2=(1.0, 100.0)):
    return [{"name": "M2", "amplitude_m": m2[0], "phase_deg": m2[1], "speed_deg_per_hour": 28.9841042},
            {"name": "K1", "amplitude_m": 0.2, "phase_deg": 50.0, "speed_deg_per_hour": 15.0410686}]


def ref(up, lat, lon, *, m2=(1.0, 100.0), source="noaa", quantity="water_level", name="Harbor"):
    cs = {"source": source, "source_type": "official", "quantity": quantity, "source_record_id": up,
          "source_version": "20261008", "convention_id": f"{source}-conv", "licence_id": "noaa-public-domain",
          "qc_status": "ok", "constituents": cons(m2), "provenance": {"adapter_version": "1"}}
    if quantity == "current":
        cs["constituents"] = []
        cs["current_bins"] = [{"bin": 1, "azimuth_deg": 0.0, "constituents": [
            {"name": "M2", "major_amplitude_ms": 0.5, "major_phase_deg": 10.0}]}]
    hint = {"name": name, "country": "USA", "lat": lat, "lon": lon, "type": "reference",
            "timezone": "America/New_York", "aliases": {"noaa": up}}
    return SourceSet(hint, cs)


def sub(up, lat, lon, reference):
    hint = {"name": "Sub", "country": "USA", "lat": lat, "lon": lon, "type": "subordinate",
            "timezone": "America/New_York", "aliases": {"noaa": up}}
    return SourceSet(hint, None, {"reference_source_record_id": reference, "time_offset_high_min": 5,
                                  "height_adjusted_type": "R", "licence_id": "noaa-public-domain"})


def current_sub(up, lat, lon, reference):
    hint = {"name": "CSub", "country": "USA", "lat": lat, "lon": lon, "type": "subordinate",
            "timezone": "America/New_York", "aliases": {"noaa": up}, "default_current_bin": 1}
    return SourceSet(hint, None, {"current_offsets": [{"bin": 1, "reference_source_record_id": reference,
                                                       "reference_bin": 1, "licence_id": "noaa-public-domain"}]})


def agency(*sets, source="noaa"):
    conv = {"convention_id": f"{source}-conv", "phase_reference": "greenwich_utc", "v0_model": "schureman_tcd",
            "nodal_handling": "f_u_at_prediction", "constituent_table_version": "noaa-37"}
    return AgencySource(source, "20261008", conv, [NOAA_LIC], list(sets), "noaa")


def m2c(a, g):
    return complex(a * math.cos(math.radians(g)), a * math.sin(math.radians(g)))


def hid(founder):
    return "OTC-" + hashlib.sha256(founder.encode()).hexdigest()[:8]


def gesla_set(sid, rid, *, m2=(1.0, 100.0)):
    return {"set_id": f"{sid}/gesla-fit/{rid}", "source": "gesla-fit", "source_type": "gauge",
            "quantity": "water_level", "source_record_id": rid, "source_version": "4.1",
            "convention_id": "gesla-fit", "licence_id": "gesla-a", "qc_status": "ok",
            "constituents": cons(m2), "provenance": {"build": {"build_commit": "abcdef0"}},
            "record_span": {"start": "2010-01-01T00:00:00Z", "end": "2015-01-01T00:00:00Z", "good_hours": 40000}}


def gesla_release():
    a = [gesla_set("OTC-aaaaaaaa", "boston-8443970-usa-noaa"), gesla_set("OTC-aaaaaaaa", "boston-x-usa-uhslc")]
    a[1]["source_type"] = "analysis"
    b = [gesla_set("OTC-bbbbbbbb", "far-1-usa-noaa")]
    return {"format_version": "1.0",
            "release": {"datestamp": "20261011", "source_versions": {"gesla": "4.1"}},
            "conventions": [{"convention_id": "gesla-fit"}],
            "licences": [{"licence_id": "gesla-a", "commercial_use": True}],
            "constituents": [{"constituent_table_version": TABLE, "name": n, "speed_deg_per_hour": s}
                             for n, s in (("K1", 15.0410686), ("M2", 28.9841042))],
            "astro_tables": [{"astro_table_id": TABLE, "constituents": {
                "K1": {"speed_deg_per_hour": 15.0410686}, "M2": {"speed_deg_per_hour": 28.9841042}}}],
            "stations": [
                {"station_id": "OTC-aaaaaaaa", "status": "active", "name": "Boston", "country": "USA",
                 "lat": 42.355, "lon": -71.05, "timezone": "America/New_York", "type": "reference",
                 "aliases": {"gesla": ["boston-8443970-usa-noaa"]}, "recommended_set_id": a[0]["set_id"],
                 "constant_sets": a},
                {"station_id": "OTC-bbbbbbbb", "status": "active", "name": "Far", "country": "USA",
                 "lat": 40.0, "lon": -70.0, "timezone": "America/New_York", "type": "reference",
                 "aliases": {"gesla": ["far-1-usa-noaa"]}, "recommended_set_id": b[0]["set_id"],
                 "constant_sets": b},
                {"station_id": "OTC-cccccccc", "status": "removed", "removed_in": "20261011",
                 "removed_reason": "merged", "merged_into": "OTC-aaaaaaaa"}]}


PASS = {"status": "pass", "gauges": 5}


# --- the join rule ----------------------------------------------------------------------------------

PLACES = [Place("OTC-aaaaaaaa", 42.355, -71.05, m2c(1.0, 100.0)),
          Place("OTC-bbbbbbbb", 42.360, -71.05, m2c(0.5, 140.0))]   # 0.56 km apart


def test_one_station_within_1km_joins():
    sid, ev = combine.join(ref("1", 40.004, -70.0), [Place("OTC-bbbbbbbb", 40.0, -70.0, None)])
    assert (sid, ev["how"]) == ("OTC-bbbbbbbb", "unambiguous") and ev["distance_km"] < 1


def test_no_station_within_1km_gives_none():
    sid, ev = combine.join(ref("1", 40.0105, -70.0), [Place("OTC-bbbbbbbb", 40.0, -70.0, None)])  # 1.17 km
    assert sid is None and ev["how"] == "no_station_within_radius"


def test_ambiguous_joins_the_best_m2_vector_not_the_nearest():
    # nearer to OTC-aaaaaaaa, but its M2 is OTC-bbbbbbbb's
    sid, ev = combine.join(ref("1", 42.356, -71.05, m2=(0.5, 141.0)), PLACES)
    assert (sid, ev["how"]) == ("OTC-bbbbbbbb", "m2_vector")
    sid, ev = combine.join(ref("1", 42.359, -71.05, m2=(1.0, 101.0)), PLACES)
    assert (sid, ev["how"]) == ("OTC-aaaaaaaa", "m2_vector")


def test_current_and_subordinate_stations_never_join():
    assert combine.join(ref("C1", 42.355, -71.05, quantity="current"), PLACES)[0] is None
    assert combine.join(sub("S1", 42.355, -71.05, "1"), PLACES)[0] is None
    # a subordinate station that also carries constants is still a station of its own
    r, s = ref("S2", 42.355, -71.05), sub("S2", 42.355, -71.05, "1")
    assert combine.join(SourceSet(s.station_hint, r.constant_set, s.subordinate_offsets), PLACES)[0] is None
    assert combine.join(SourceSet(s.station_hint, r.constant_set), PLACES)[0] is None


# --- minting ----------------------------------------------------------------------------------------

def smap(**stations):
    return sm.StationMap(stations={s: {"founder": r[0], "records": list(r)} for s, r in stations.items()})


def test_mint_joins_or_gives_a_new_id_from_the_record_id():
    m = smap(**{"OTC-aaaaaaaa": ["boston-8443970-usa-noaa"], "OTC-bbbbbbbb": ["far-1-usa-noaa"]})
    a = agency(ref("8443970", 42.3551, -71.05), ref("9999999", 30.0, -80.0), sub("S1", 42.355, -71.05, "8443970"))
    new, rows = combine.mint_agency(m, {"noaa": a}, PLACES[:1])
    ids = new.record_ids()
    assert ids["noaa:8443970"] == "OTC-aaaaaaaa"
    assert ids["noaa:9999999"] == hid("noaa:9999999") and new.stations[hid("noaa:9999999")]["founder"] == "noaa:9999999"
    assert ids["noaa:S1"] == hid("noaa:S1")
    assert {r["record"]: r["joined"] for r in rows} == {"noaa:8443970": True, "noaa:9999999": False, "noaa:S1": False}
    assert ids["boston-8443970-usa-noaa"] == "OTC-aaaaaaaa"     # GESLA records are untouched


def test_a_mapped_record_keeps_its_station_and_a_taken_id_is_never_reused():
    m = smap(**{"OTC-aaaaaaaa": ["boston-8443970-usa-noaa"], hid("noaa:1"): ["noaa:1"]})
    m.retired[hid("noaa:2")] = {"merged_into": "OTC-aaaaaaaa", "removed_in": "20261011", "removed_reason": "merged"}
    a = agency(ref("1", 42.355, -71.05), ref("2", 30.0, -80.0))     # "1" is now within 1 km of a GESLA station
    new, rows = combine.mint_agency(m, {"noaa": a}, PLACES[:1])
    assert new.record_ids()["noaa:1"] == hid("noaa:1")          # kept, not re-joined
    assert [r["record"] for r in rows] == ["noaa:2"]
    assert new.record_ids()["noaa:2"] != hid("noaa:2") and new.record_ids()["noaa:2"].startswith(hid("noaa:2"))


def test_one_record_per_source_and_station():
    m = smap(**{"OTC-aaaaaaaa": ["boston-8443970-usa-noaa"]})
    a = agency(ref("1", 42.3555, -71.05, m2=(1.3, 100.0)), ref("2", 42.358, -71.05, m2=(1.0, 100.5)))
    new, rows = combine.mint_agency(m, {"noaa": a}, PLACES[:1])
    by = {r["record"]: r for r in rows}
    assert by["noaa:2"]["joined"] and new.record_ids()["noaa:2"] == "OTC-aaaaaaaa"      # better M2 wins
    assert not by["noaa:1"]["joined"] and by["noaa:1"]["how"] == "source_already_joined"
    # a station that already holds a noaa record takes no second one
    new2, rows2 = combine.mint_agency(new, {"noaa": agency(ref("3", 42.355, -71.05))}, PLACES[:1])
    assert not rows2[0]["joined"] and new2.record_ids()["noaa:3"] == hid("noaa:3")


def test_a_gesla_re_mint_keeps_the_agency_records():
    old = smap(**{hid("a"): ["a"], hid("noaa:1"): ["noaa:1"]})
    old.stations[hid("a")]["records"].append("noaa:2")
    again = sm.mint([sm.RecordInfo("a", "Harbour", "A", "NOR", 60.0, 5.0)], {}, existing=old)
    assert again.record_ids() == {"a": hid("a"), "noaa:2": hid("a"), "noaa:1": hid("noaa:1")}
    assert sm.dump(again) == sm.dump(old)


def test_the_committed_map_holds_every_agency_record_once():
    m = sm.load()
    agency_records = [r for r in m.record_ids() if r.split(":", 1)[0] in combine.AGENCY_SOURCES]
    assert len(agency_records) > 6000
    for r in agency_records:
        sid = m.record_ids()[r]
        same = [x for x in m.stations[sid]["records"] if x.split(":", 1)[0] == r.split(":", 1)[0]]
        assert same == [r], (sid, same)


# --- position corrections ---------------------------------------------------------------------------

def correction(up="TPT2891", pub=(-19.0333, 169.9167), to=(-19.0333, -169.9167), tz="Pacific/Niue", country="NIU"):
    return {"source": "noaa", "upstream_id": up, "published": {"lat": pub[0], "lon": pub[1]},
            "corrected": {"lat": to[0], "lon": to[1]}, "timezone": tz, "country": country, "reason": "sign"}


def niue(lon=169.9167):
    s = sub("TPT2891", -19.0333, lon, "1")
    return SourceSet({**s.station_hint, "country": "NIU", "timezone": "Pacific/Niue"}, None, s.subordinate_offsets)


def test_a_correction_moves_the_station_and_is_recorded():
    a = agency(niue())
    combine.apply_corrections(a, [correction()])
    h = a.sets[0].station_hint
    assert (h["lat"], h["lon"], h["timezone"]) == (-19.0333, -169.9167, "Pacific/Niue")
    assert a.corrections[0]["upstream_id"] == "TPT2891" and a.corrections[0]["from"]["lon"] == 169.9167


def test_a_correction_for_a_position_the_source_no_longer_serves_stops():
    with pytest.raises(CombineError, match="now serves"):
        combine.apply_corrections(agency(niue(lon=-169.9)), [correction()])


def test_a_correction_whose_zone_is_not_the_rows_stops():
    with pytest.raises(CombineError, match="Pacific/Niue"):
        combine.apply_corrections(agency(niue()), [correction(tz="Pacific/Tongatapu")])


def test_the_committed_corrections_fix_the_seven_noaa_positions():
    rows = combine.load_corrections()
    assert sorted((c["source"], c["upstream_id"]) for c in rows) == sorted(
        [("noaa", "TPT2891"), ("noaa", "TPT2893"), ("noaa", "TPT2897"), ("noaa", "TWC0279"),
         ("noaa-currents", "SAV2304"), ("noaa-currents", "SAV2305"), ("noaa-currents", "SAV2326")])
    for c in rows:
        assert c["corrected"]["lon"] < 0 < c["published"]["lon"]


# --- merging ----------------------------------------------------------------------------------------

IDS = {"noaa:8443970": "OTC-aaaaaaaa", "noaa:8444351": hid("noaa:8444351"), "noaa:9999999": hid("noaa:9999999"),
       "noaa:S0": hid("noaa:S0"), "noaa:ACT1": hid("noaa:ACT1"), "noaa:ACT2": hid("noaa:ACT2")}


def test_a_joined_station_recommends_the_official_set_and_keeps_its_gesla_sets():
    rel = gesla_release()
    before = copy.deepcopy(rel)
    out, rep = combine.merge(rel, {"noaa": agency(ref("8443970", 42.3551, -71.05))}, station_ids=IDS,
                             build_commit="1234567", table_id=TABLE, canaries={"noaa": PASS})
    assert rel == before                                                   # the input is not changed
    st = next(s for s in out["stations"] if s["station_id"] == "OTC-aaaaaaaa")
    old = before["stations"][0]
    assert st["recommended_set_id"] == "OTC-aaaaaaaa/noaa/8443970"
    assert st["constant_sets"][0]["set_id"] == "OTC-aaaaaaaa/noaa/8443970"
    assert st["constant_sets"][1:] == old["constant_sets"]                 # byte-equal, same order
    # G2: Boston holds NOAA's own gauge (boston-8443970-usa-noaa): the station shows NOAA's name and position
    assert (st["name"], st["lat"], st["lon"]) == ("Harbor", 42.3551, -71.05)
    assert {k: st[k] for k in ("country", "type")} == {k: old[k] for k in ("country", "type")}
    assert rep.joined[0]["same_gauge"] and rep.joined[0]["gesla_place"] == {
        "name": "Boston", "lat": 42.355, "lon": -71.05, "timezone": "America/New_York"}
    assert st["aliases"] == {"gesla": ["boston-8443970-usa-noaa"], "noaa": "8443970"}
    assert st["constant_sets"][0]["provenance"]["build"] == {"build_commit": "1234567"}
    assert out["stations"][1] == before["stations"][1] and out["stations"][-1] == before["stations"][-1]
    assert rep.joined[0]["was_recommended"] == old["recommended_set_id"]
    assert rep.sources["noaa"]["joined"] == 1


def test_a_station_joined_by_distance_keeps_its_gesla_name_and_position():
    ids = {**IDS, "noaa:7777777": "OTC-bbbbbbbb"}
    out, rep = combine.merge(gesla_release(), {"noaa": agency(ref("7777777", 40.004, -70.0))}, station_ids=ids,
                             build_commit="1234567", table_id=TABLE, canaries={"noaa": PASS})
    st = next(s for s in out["stations"] if s["station_id"] == "OTC-bbbbbbbb")
    assert (st["name"], st["lat"], st["lon"]) == ("Far", 40.0, -70.0) and not rep.joined[0]["same_gauge"]
    assert st["recommended_set_id"] == "OTC-bbbbbbbb/noaa/7777777"


def test_new_stations_subordinates_and_release_level_rows():
    a = agency(ref("8443970", 42.3551, -71.05), ref("9999999", 30.0, -80.0), sub("8444351", 42.3, -71.0, "8443970"),
               sub("S0", 42.3, -71.0, "missing"))
    out, rep = combine.merge(gesla_release(), {"noaa": a}, station_ids=IDS, build_commit="1234567", table_id=TABLE,
                             canaries={"noaa": PASS})
    by = {s["station_id"]: s for s in out["stations"]}
    new = by[hid("noaa:9999999")]
    assert new["recommended_set_id"] == f"{hid('noaa:9999999')}/noaa/9999999" and new["type"] == "reference"
    hull = by[hid("noaa:8444351")]
    assert hull["subordinate_offsets"]["reference_station_id"] == "OTC-aaaaaaaa"   # the joined station
    assert "reference_source_record_id" not in hull["subordinate_offsets"]
    assert hull["recommended_set_id"] is None and hull["constant_sets"] == []
    assert hid("noaa:S0") not in by and rep.refused[0]["reason"] == "reference_not_published"
    conv = next(c for c in out["conventions"] if c["convention_id"] == "noaa-conv")
    assert conv["astro_table_id"] == TABLE and conv["canary"] == PASS
    assert out["release"]["source_versions"] == {"gesla": "4.1", "noaa": "20261008"}
    assert ("noaa-37", "M2") in {(r["constituent_table_version"], r["name"]) for r in out["constituents"]}
    assert [lic["licence_id"] for lic in out["licences"]] == ["gesla-a", "noaa-public-domain"]
    active = [s["station_id"] for s in out["stations"] if s["status"] == "active"]
    assert active == sorted(active) and out["stations"][-1]["status"] == "removed"


def test_current_subordinates_map_every_bin_reference():
    a = agency(ref("ACT1", 30.0, -80.0, quantity="current"), current_sub("ACT2", 30.1, -80.0, "ACT1"))
    out, _ = combine.merge(gesla_release(), {"noaa": a}, station_ids=IDS, build_commit="1234567", table_id=TABLE,
                           canaries={"noaa": PASS})
    st = next(s for s in out["stations"] if s["station_id"] == hid("noaa:ACT2"))
    assert st["current_offsets"][0]["reference_station_id"] == hid("noaa:ACT1")
    assert st["default_current_bin"] == 1


def test_a_source_whose_canary_fails_stops_the_build():
    with pytest.raises(CombineError, match="D11"):
        combine.merge(gesla_release(), {"noaa": agency(ref("8443970", 42.3551, -71.05))}, station_ids=IDS,
                      build_commit="1234567", table_id=TABLE, canaries={"noaa": {"status": "fail", "gauges": 1}})


def test_a_source_with_no_canary_stops_the_build():
    a = agency(ref("8443970", 42.3551, -71.05))
    with pytest.raises(CombineError, match="D11"):
        combine.merge(gesla_release(), {"noaa": a}, station_ids=IDS, build_commit="1234567", table_id=TABLE,
                      canaries={})
    with pytest.raises(CombineError, match="D11"):
        combine.merge(gesla_release(), {"noaa": a}, station_ids=IDS, build_commit="1234567", table_id=TABLE,
                      canaries={"noaa": {"gauges": 5}})


def test_an_agency_record_with_no_station_id_stops_the_build():
    with pytest.raises(CombineError, match="no station id"):
        combine.merge(gesla_release(), {"noaa": agency(ref("7", 1.0, 1.0))}, station_ids=IDS,
                      build_commit="1234567", table_id=TABLE, canaries={"noaa": PASS})


def test_two_objects_for_one_licence_id_stop_the_build():
    a = agency(ref("9999999", 30.0, -80.0))
    a.licences = [{**NOAA_LIC, "attribution": "other"}]
    rel = gesla_release()
    rel["licences"].append(dict(NOAA_LIC))
    with pytest.raises(CombineError, match="another object"):
        combine.merge(rel, {"noaa": a}, station_ids=IDS, build_commit="1234567", table_id=TABLE,
                      canaries={"noaa": PASS})


# --- the NOAA-currents canary -----------------------------------------------------------------------

def comparison(station="A", missed=0, extra=0, dt=(1.0, 2.0), dv=(0.002,)):
    return Comparison(station, 1, 10, 10, 10, missed, extra, 0, 0, 0, 0, 0, 0, 0, tuple(dt), tuple(dv))


def test_the_currents_bar_is_the_canary_rule_read_for_events():
    assert round(cc.TOL_DT_MIN, 2) == 4.14 and cc.TOL_DV_MS == 0.01 and cc.MIN_GAUGES == 3
    ok = [comparison(s) for s in "ABC"]
    assert cc.judge(ok, [])["status"] == "pass"
    assert cc.judge(ok[:2], [])["status"] == "fail"                                        # < 3 gauges
    assert cc.judge(ok[:2] + [comparison("C", dt=(), dv=())], [])["status"] == "fail"       # a station with no data
    # like the other canaries, a median bar: an event one side does not list is counted, not failed
    r = cc.judge(ok[:2] + [comparison("C", missed=1, extra=2)], [])
    assert (r["status"], r["unmatched_noaa"], r["unmatched_engine"]) == ("pass", 1, 2)
    assert cc.judge([comparison(s, dt=(5.0,)) for s in "ABC"], [])["status"] == "fail"      # median dt > 4.14
    assert cc.judge([comparison(s, dt=(4.0,)) for s in "ABC"], [])["status"] == "pass"
    assert cc.judge([comparison(s, dv=(0.011,)) for s in "ABC"], [])["status"] == "fail"    # median dv > 1 cm/s
    assert cc.judge(ok, ["D bin 1"])["status"] == "fail"                                    # not cached


def test_one_station_off_by_more_than_10_min_or_3_cm_s_fails_whatever_the_medians():
    ok = [comparison(s) for s in "ABCDE"]
    assert cc.judge(ok, [])["status"] == "pass"
    assert cc.judge(ok[:4] + [comparison("E", dt=(1.0, 10.5))], [])["status"] == "fail"
    assert cc.judge(ok[:4] + [comparison("E", dt=(1.0, 10.0))], [])["status"] == "pass"
    assert cc.judge(ok[:4] + [comparison("E", dv=(0.031,))], [])["status"] == "fail"
    assert cc.judge(ok[:4] + [comparison("E", dv=(0.03,))], [])["status"] == "pass"


def test_a_station_whose_floods_and_ebbs_do_not_pair_fails():
    # a full flood/ebb negation: the slacks pair (small dt), every NOAA peak is missed and every engine peak extra
    def neg(st):
        return Comparison(st, 1, 54, 54, 27, 27, 27, 0.5, 0, 0, 0, 0, 0, 0, (0.3,) * 27, (), 27)
    ok = [comparison(s) for s in "ABCDE"]
    assert cc.judge(ok[:4] + [neg("E")], [])["status"] == "fail"
    half = Comparison("E", 1, 54, 54, 40, 14, 14, 0.5, 1, 0.01, 0, 0, 0, 0, (0.3,) * 40, (0.002,) * 13, 27)
    assert cc.judge(ok[:4] + [half], [])["status"] == "fail"                    # 13 of 27 peaks paired
    most = Comparison("E", 1, 54, 54, 41, 13, 13, 0.5, 1, 0.01, 0, 0, 0, 0, (0.3,) * 41, (0.002,) * 14, 27)
    assert cc.judge(ok[:4] + [most], [])["status"] == "pass"                    # 14 of 27
    assert cc.judge(ok, [])["status"] == "pass"


def test_each_station_says_canary_pass_or_fail_and_unmatched_events_are_notes():
    # INT-2 C1: current_proof's all-events verdict (passed=false on a weak unmatched event) read as a failure while
    # the canary passed; a station now carries its own canary result, its failures and its notes
    ok = [comparison(s) for s in "ABCD"]
    weak = Comparison("E", 1, 45, 42, 41, 4, 1, 0.6, 0.9, 0.001, 0, 0, 0, 0, (0.3,) * 41, (0.001,) * 22, 25)
    r = cc.judge(ok + [weak], [])
    e = r["stations"][-1]
    assert r["status"] == "pass" and e["canary"] == "pass" and e["reasons"] == [] and "passed" not in e
    assert e["current_proof_all_events_pass"] is False
    assert e["notes"] == ["4 NOAA event(s) with no engine event of the same type within 90 min (3 of them max flood/ebb)",
                          "1 engine event(s) NOAA does not list"]
    swapped = Comparison("E", 1, 54, 54, 27, 27, 27, 0.5, 0, 0, 0, 0, 0, 0, (0.3,) * 27, (), 27)
    r = cc.judge(ok + [swapped], [])
    e = r["stations"][-1]
    assert r["status"] == "fail" and e["canary"] == "fail" and e["reasons"] == r["reasons"]
    assert all(s["canary"] == "pass" for s in r["stations"][:-1])


def test_the_sample_is_fixed_whatever_the_order():
    sets = [ref(f"ACT{i}", 30.0, -80.0, quantity="current") for i in range(40)]
    s = cc.sample(sets)
    assert len(s) == cc.SAMPLE and s == cc.sample(list(reversed(sets)))


def test_run_canary_reads_only_the_cache(tmp_path):
    sets = [ref(f"ACT{i}", 30.0, -80.0, quantity="current") for i in range(4)]
    for station, b in cc.sample(sets, 3)[:2]:
        p = tmp_path / f"predictions/{station}_{b}_20261008_7d.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"current_predictions": {"cp": [{"Type": "slack", "Time": "2026-10-09 00:00", "Velocity_Major": 0}]}}')
    engine = lambda inp, wd: {"events": [{"type": "slack", "time": "2026-10-09T00:01:00Z", "velocity": 0.0}]}  # noqa: E731
    r = cc.run_canary(sets, tmp_path, n=3, engine=engine)
    assert r["gauges"] == 2 and r["status"] == "fail" and any("no cached" in x for x in r["reasons"])


# --- the GESLA-comparator canaries (build_otc) --------------------------------------------------------

def test_canaries_compare_only_sets_with_the_four_constituents(monkeypatch):
    from types import SimpleNamespace
    from otc_pipeline import build_otc as bo

    def four(rid, m2=(1.0, 100.0), drop=()):
        cs = [{"name": n, "amplitude_m": a, "phase_deg": g, "speed_deg_per_hour": 1.0}
              for n, a, g in (("M2", *m2), ("S2", 0.3, 130.0), ("K1", 0.2, 50.0), ("O1", 0.1, 40.0)) if n not in drop]
        return cs

    docs = [{"record_id": f"g{i}", "time_base": {"code": "verified"}} for i in range(6)]
    docs[4]["time_base"]["code"] = "disputed"
    parts = SimpleNamespace(docs=docs, disputed_reasons={"g3": "x"}, sibling_flags={"g2": [{}]})

    def comparator(d):
        i = int(d["record_id"][1:])
        return SourceSet({"lat": 40.0 + i, "lon": -70.0, "country": "USA"},
                         {"source_record_id": d["record_id"], "constituents": four(d["record_id"], drop=("O1",) if i == 1 else ())})
    monkeypatch.setattr(bo.gs, "comparator_set", comparator)
    comp = bo.gesla_comparators(parts)
    assert [c.constant_set["source_record_id"] for c in comp] == ["g0", "g5"]   # g1 lacks O1; g2-g4 flagged/disputed
    a = agency(*(SourceSet({"lat": lat, "lon": -70.0, "country": "USA", "type": "reference", "aliases": {}},
                           {"source_record_id": rid, "constituents": four(rid, drop=drop)})
                 for rid, lat, drop in (("n0", 40.0, ()), ("n5", 45.0, ()), ("n9", 49.0, ()), ("nk", 40.0, ("K1",)))))
    comp9 = comp + [comparator({"record_id": "g9"})]
    exc = [{"source": "noaa", "source_record_id": "nk", "missing": ["K1"], "reason": "publishes no K1"}]
    conv, full = bo.run_canaries({"noaa": a}, comp9, None, exceptions=exc, log=lambda m: None)
    assert conv["noaa"]["status"] == "pass" and conv["noaa"]["gauges"] == 3
    assert full["noaa"]["not_compared_missing_constituents"] == [{"source_record_id": "nk", "missing": ["K1"]}]
    # F4b: a set that cannot be compared fails the canary unless a reasoned exception names it
    conv, full = bo.run_canaries({"noaa": a}, comp9, None, exceptions=[], log=lambda m: None)
    assert conv["noaa"]["status"] == "fail" and any("nk: missing K1" in r for r in full["noaa"]["reasons"])
    wrong = [{**exc[0], "missing": ["O1"]}]
    with pytest.raises(CombineError, match="match no uncompared set"):          # does not match: a stale row
        bo.run_canaries({"noaa": a}, comp9, None, exceptions=wrong, log=lambda m: None)


def test_the_committed_canary_exceptions_are_viker_only():
    from otc_pipeline import build_otc as bo
    rows = bo.load_canary_exceptions()
    assert [(r["source"], r["source_record_id"], r["missing"]) for r in rows] == [("kartverket", "VIK", ["K1"])]
    assert all(r["reason"].strip() for r in rows)


# --- F1: the same agency gauge joins first (fix round 1) --------------------------------------------

BALT = [Place("OTC-39db6605", 39.2667, -76.5783, m2c(0.150, 230.0), ("baltimore-8574680-usa-noaa",)),
        Place("OTC-a803b2b4", 39.2667, -76.5800, m2c(0.152, 231.0), ("fort_mchenry_marsh-8574683-usa-noaa",))]


def test_the_station_with_the_same_agency_gauge_wins_over_a_better_m2():
    # 8574680's M2 is nearer Fort McHenry Marsh, but Baltimore holds the GESLA record of gauge 8574680
    sid, ev = combine.join(ref("8574680", 39.2667, -76.5795, m2=(0.152, 231.0)), BALT, source="noaa")
    assert (sid, ev["how"]) == ("OTC-39db6605", "same_id")
    sid, ev = combine.join(ref("8574683", 39.2667, -76.5800, m2=(0.150, 230.0)), BALT, source="noaa")
    assert (sid, ev["how"]) == ("OTC-a803b2b4", "same_id")


def test_the_same_gauge_joins_up_to_5km_and_not_beyond():
    far = [Place("OTC-cccccccc", 40.0, -70.0, None, ("riverside-8440889-usa-noaa",))]
    assert combine.join(ref("8440889", 40.031, -70.0), far, source="noaa")[1]["how"] == "same_id"      # 3.4 km
    assert combine.join(ref("8440889", 40.046, -70.0), far, source="noaa")[0] is None                   # 5.1 km
    # another source's id, or another code, is not the same gauge
    assert combine.join(ref("8440889", 40.031, -70.0), far, source="kartverket")[0] is None
    assert combine.join(ref("8440888", 40.031, -70.0), far, source="noaa")[0] is None


def test_a_kartverket_station_joins_its_nhs_record():
    p = [Place("OTC-8c70dc71", 68.2, 14.5, m2c(1.0, 0.0), ("kabelvag-kab-nor-nhs",)),
         Place("OTC-0bac4c60", 68.201, 14.5, m2c(0.8, 10.0), ())]
    k = ref("KAB", 68.2005, 14.5, m2=(0.8, 10.0), source="kartverket")
    assert combine.join(k, p, source="kartverket") == ("OTC-8c70dc71", combine.join(k, p, source="kartverket")[1])
    assert combine.join(k, p, source="kartverket")[1]["how"] == "same_id"


def test_a_same_gauge_claim_beats_another_claim_on_the_station():
    m = smap(**{"OTC-39db6605": ["baltimore-8574680-usa-noaa"], "OTC-a803b2b4": ["fort_mchenry_marsh-8574683-usa-noaa"]})
    # 8574683 alone would join Baltimore by M2 if Fort McHenry Marsh were not a place
    a = agency(ref("8574683", 39.2667, -76.5795, m2=(0.150, 230.0)), ref("8574680", 39.2668, -76.5783, m2=(0.3, 250.0)))
    new, rows = combine.mint_agency(m, {"noaa": a}, BALT[:1])
    by = {r["record"]: r for r in rows}
    assert by["noaa:8574680"]["how"] == "same_id" and new.record_ids()["noaa:8574680"] == "OTC-39db6605"
    # G4: 8574683 may not join Baltimore by rule 3 either: the station holds another NOAA gauge
    assert by["noaa:8574683"]["how"] == "no_station_within_radius"
    assert by["noaa:8574683"]["other_gauge_stations"] == ["OTC-39db6605"]


# --- G4: the same gauge is the same country and agency; another gauge of the agency never joins ---------

def test_the_same_gauge_needs_the_country_and_the_agency_of_the_record():
    far = [Place("OTC-cccccccc", 40.0, -70.0, None, ("riverside-8440889-can-noaa",))]
    assert combine.join(ref("8440889", 40.031, -70.0), far, source="noaa")[0] is None
    nhs = [Place("OTC-dddddddd", 40.0, -70.0, None, ("x-8440889-usa-nhs",))]
    assert combine.join(ref("8440889", 40.031, -70.0), nhs, source="noaa")[0] is None


def test_rules_2_and_3_never_join_a_station_holding_another_gauge_of_the_agency():
    one = [Place("OTC-cccccccc", 40.0, -70.0, m2c(1.0, 100.0), ("x-8440888-usa-noaa",))]
    sid, ev = combine.join(ref("8440889", 40.004, -70.0), one, source="noaa")          # rule 2 would join
    assert sid is None and ev["other_gauge_stations"] == ["OTC-cccccccc"]
    two = one + [Place("OTC-dddddddd", 40.002, -70.0, m2c(0.5, 140.0), ("y-y-usa-uhslc",))]
    sid, ev = combine.join(ref("8440889", 40.004, -70.0, m2=(1.0, 100.0)), two, source="noaa")  # rule 3: best M2 is the other gauge
    assert (sid, ev["how"]) == ("OTC-dddddddd", "unambiguous")
    # another source's records do not count as this agency's gauges
    assert combine.join(ref("8440889", 40.004, -70.0), one, source="kartverket")[0] == "OTC-cccccccc"


# --- one TCD dump for the fits, the astro table and every agency source (INT-2, FIX-CONST review N7) ---------------

def _tcd_dump(path, tcd_sha256, rows):
    import json
    path.write_text(json.dumps({"tcd_sha256": tcd_sha256, "years": [2000, 2001], "aliases": {},
                                "constituents": {k: {"speed": v, "v0u": [0.0, 0.0], "f": [1.0, 1.0]}
                                                 for k, v in rows.items()}}))
    return path


@pytest.mark.parametrize("source", combine.AGENCY_SOURCES)
def test_every_agency_source_reads_its_speeds_from_the_build_dump(tmp_path, source):
    from otc_pipeline.astro_tables import TCD_SHA256
    dump = _tcd_dump(tmp_path / "otc.json", TCD_SHA256, {"M2": 28.9841042, "MA2": 28.9430356})
    sp = combine.agency_speeds(source, dump)
    assert sp.tcd_sha256 == TCD_SHA256 and sp.dump_sha256 == hashlib.sha256(dump.read_bytes()).hexdigest()
    assert sp.speeds["MA2"] == 28.9430356            # a row of the OTC TCD, never "no table row"


@pytest.mark.parametrize("source", combine.AGENCY_SOURCES)
def test_a_dump_of_another_tcd_stops_the_agency_read(tmp_path, source):
    from otc_pipeline.astro_tables import SOURCE_TCD_SHA256
    dump = _tcd_dump(tmp_path / "xtide.json", SOURCE_TCD_SHA256, {"M2": 28.9841042})
    with pytest.raises(CombineError, match="the agency sets are read on the release's otc-tcd-20251228-ma2mb2"):
        combine.agency_speeds(source, dump)


def test_a_source_on_another_dump_than_the_build_stops_the_build():
    from types import SimpleNamespace

    from otc_pipeline import build_otc as bo
    parts = SimpleNamespace(provenance={"tcd": {"path": "/otc.json", "sha256": "d" * 64}})
    same = {s: SimpleNamespace(tcd_dump_sha256="d" * 64) for s in combine.AGENCY_SOURCES}
    bo.check_one_table(same, parts)
    for bad in ("e" * 64, None):
        with pytest.raises(CombineError, match="read another TCD dump than the build's"):
            bo.check_one_table({**same, "kartverket": SimpleNamespace(tcd_dump_sha256=bad)}, parts)
