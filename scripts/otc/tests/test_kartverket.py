"""Kartverket adapter (A-KV): parse, cm -> m, UTC+1 -> Greenwich, contract.

The fixtures in fixtures/kartverket/ are a real fetch cache (bodies plus the fetcher's
.meta.json) from `kv().fetch(...)` on 2026-10-08: the permanent-station list and
the constituents of Bergen (BGO), Trondheim (TRD) and Stavanger (SVG). stationlevels/*.xml are
SYNTHETIC bodies in the shape of Kartverket's stationlevels response (invented values, no fetched
data in git), with their cache .meta.json, for the DT5b datum tests. The tests run offline: the
cache answers every request, and any network call fails the test. Expected values are read
from the raw XML here, independently of the adapter.

The real-surface proof (OTC_LIVE=1 and OTC_CANARY_BUILD=<D> with the NHS fit documents) runs
the canary against our GESLA fits: it passes under the declared UTC+1 convention and fails with
the phases read as Greenwich.
"""
from __future__ import annotations

import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from otc_pipeline.adapter import (
    Adapter,
    assemble_release,
    assert_contract,
    check_contract,
)
from otc_pipeline.adapters.kartverket import KartverketAdapter, parse_constituents
from otc_pipeline.ios_definitions import P1_EPOCH, p1_deg, to_tcd_phase
from otc_pipeline.constituent_speeds import NO_ASTRO_ROW, SPEED_FLAG_DEG_PER_HOUR, SpeedDeviation, TableSpeeds
from otc_pipeline.release_writer import validate_release
from otc_pipeline.sources import SOURCES, registry_errors

from contract_stubs import shape_only_astro_tables

FIXTURES = Path(__file__).parent / "fixtures" / "kartverket"
STATIONS = ("BGO", "TRD", "SVG")
SPEEDS = TableSpeeds.from_json(FIXTURES / "tcd_speeds.json")


def kv(*a, **k) -> KartverketAdapter:
    """The adapter with the astro-table speeds of the saved responses' constituents (TCD extract)."""
    return KartverketAdapter(*a, speeds=SPEEDS, **k)
LIVE = os.environ.get("OTC_LIVE") == "1"
BUILD = os.environ.get("OTC_CANARY_BUILD")


@pytest.fixture
def no_network(monkeypatch):
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("the Kartverket fixture test made a network request")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)


@pytest.fixture
def cache(tmp_path, no_network):
    d = tmp_path / "kartverket"
    shutil.copytree(FIXTURES, d, ignore=shutil.ignore_patterns("tcd_speeds.json"))
    return d


def raw(code: str) -> dict[str, dict]:
    root = ET.parse(FIXTURES / "constituents" / f"{code}.xml").getroot()
    return {c.get("name"): {k: float(c.get(k)) for k in ("amplitude", "phaseangle", "speed")}
            for c in root.iter("constituent")}


def sets_by_code(adapter, cache_dir) -> dict:
    fetched = adapter.fetch(cache_dir, only=list(STATIONS))
    return {s.constant_set["source_record_id"]: s for s in adapter.sets(fetched)}


def test_registered_and_implements_the_protocol():
    assert SOURCES["kartverket"] is KartverketAdapter
    assert isinstance(KartverketAdapter(), Adapter)
    assert not [e for e in registry_errors() if "kartverket" in e]


def test_fixture_contract_passes(cache):
    adapter = kv()
    fetched = adapter.fetch(cache, only=list(STATIONS))
    assert_contract(adapter, fetched)
    assert set(fetched.items) == set(STATIONS)


def test_release_around_the_sets_validates(cache):
    adapter = kv()
    fetched = adapter.fetch(cache, only=list(STATIONS))
    sets = list(adapter.sets(fetched))
    # the astro tables are the build's (KP0b); a shape-only test table makes the shell whole
    release = assemble_release(adapter, sets, fetched, astro_tables=shape_only_astro_tables(sets))
    assert validate_release(release) == []
    assert release["licences"][0]["commercial_use"] is True
    assert [cs["qc_status"] for st in release["stations"] for cs in st["constant_sets"]] == ["ok"] * len(sets)


def test_amplitudes_are_metres_and_phases_greenwich(cache):
    sets = sets_by_code(kv(), cache)
    for code in STATIONS:
        served = raw(code)
        cons = {c["name"]: c for c in sets[code].constant_set["constituents"]}
        assert set(cons) == set(served) - {"GAM2"}   # GAM2 has no astro row: dropped (see below)
        for name, c in cons.items():
            s = served[name]
            assert c["amplitude_m"] == pytest.approx(s["amplitude"] / 100.0, abs=1e-9)
            assert c["speed_deg_per_hour"] == SPEEDS.speed(name)   # the astro row's, within 1e-4 of the served
            # UTC+1 -> Greenwich: g = g_local - speed x 1 h (sign fixed by the canary proof); SA and S1
            # are served with IOS's arguments and converted to the TCD's (ios_definitions)
            want = to_tcd_phase(name, (s["phaseangle"] - s["speed"]) % 360.0)
            assert abs((c["phase_deg"] - want + 180) % 360 - 180) < 1e-3
            assert 0.0 <= c["phase_deg"] < 360.0


def test_bergen_m2_and_k1_values(cache):
    cons = {c["name"]: c for c in sets_by_code(kv(), cache)["BGO"].constant_set["constituents"]}
    assert raw("BGO")["K1"]["phaseangle"] == 178.98
    assert cons["K1"]["phase_deg"] == pytest.approx(178.98 - 15.04106863, abs=1e-3)
    assert cons["K1"]["amplitude_m"] == pytest.approx(0.0339)


def test_read_as_greenwich_flag_keeps_the_served_phase(cache):
    sets = sets_by_code(kv("greenwich_utc"), cache)
    cons = {c["name"]: c for c in sets["BGO"].constant_set["constituents"]}
    assert cons["M2"]["phase_deg"] == pytest.approx(raw("BGO")["M2"]["phaseangle"])


def test_station_hint_and_record_span(cache):
    s = sets_by_code(kv(), cache)["BGO"]
    assert s.station_hint == {"name": "Bergen", "country": "NOR", "lat": 60.398046, "lon": 5.320487,
                              "type": "reference", "timezone": "Europe/Oslo", "aliases": {"kartverket": "BGO"}}
    # observations 2006-01-01T00:00:00+01:00 .. 2020-12-31T23:00:00+01:00
    assert s.constant_set["record_span"] == {"start": "2005-12-31T23:00:00Z", "end": "2020-12-31T22:00:00Z"}


def test_convention_and_licence():
    a = KartverketAdapter()
    assert a.convention()["phase_reference"] == "local" and a.convention()["utc_offset_hours"] == 1
    assert a.licence()["spdx"] == "CC-BY-4.0"


def test_manifest_is_written(cache):
    kv().fetch(cache, only=list(STATIONS))
    m = json.loads((cache / "MANIFEST.json").read_text())
    assert m["licence"] == "CC-BY-4.0" and m["archive"] == "cache/kartverket/"
    assert {f["path"] for f in m["files"]} == {"stationlist.xml", *(f"constituents/{c}.xml" for c in STATIONS),
                                               *(f"stationlevels/{c}.xml" for c in STATIONS)}


def test_watch_key_changes_with_the_constants(cache):
    a = KartverketAdapter()
    fetched = a.fetch(cache, only=list(STATIONS))
    keys = a.watch_keys(fetched)
    assert len(set(keys.values())) == 3
    fetched.items["BGO"]["constituents"][0]["amplitude_cm"] += 0.01
    after = a.watch_keys(fetched)
    assert after["BGO"] != keys["BGO"] and after["TRD"] == keys["TRD"]


@pytest.mark.parametrize("old,new,needle", [
    (b'unit="cm"', b'unit="m"', "unit"),
    (b'utcoffset="+01:00"', b'utcoffset="+00:00"', "utcoffset"),
    (b'code="BGO"', b'code="TRD"', "response is for"),
])
def test_parse_refuses_an_unexpected_response(old, new, needle):
    data = (FIXTURES / "constituents" / "BGO.xml").read_bytes().replace(old, new)
    with pytest.raises(ValueError, match=needle):
        parse_constituents(data, "BGO")


def test_unknown_station_is_refused(cache):
    with pytest.raises(KeyError):
        kv().fetch(cache, only=["XXX"])


def test_bad_phase_reference_is_refused():
    with pytest.raises(ValueError):
        KartverketAdapter("zone")


# --- real surface -----------------------------------------------------------------------------------

@pytest.mark.skipif(not LIVE, reason="set OTC_LIVE=1")
def test_contract_live(tmp_path):
    a = KartverketAdapter()
    fetched = a.fetch(tmp_path, only=list(STATIONS))
    assert check_contract(a, fetched) == []


@pytest.mark.skipif(not (LIVE and BUILD), reason="set OTC_LIVE=1 and OTC_CANARY_BUILD=<D> (gesla fit documents)")
@pytest.mark.parametrize("phase_reference,passes", [("local", True), ("greenwich_utc", False)])
def test_kartverket_canary_against_nhs_fits_live(tmp_path, phase_reference, passes):
    from kartverket_canary import PROOF_STATIONS, run
    res = run(BUILD, phase_reference, PROOF_STATIONS, tmp_path)
    print(f"\nA-KV canary phase_reference={phase_reference}: " + json.dumps(res.to_dict(), sort_keys=True))
    assert res.n_gauges == 3 and res.passed is passes, res.to_dict()
    if passes:
        for p in res.pairs:
            assert abs(p.g9.dg_deg["M2"]) <= 2.0 and abs(p.g9.dg_deg["K1"]) <= 2.0, p.to_dict()


# --- speeds: the astro table's, by name (KP0b) --------------------------------------------------------

def test_speeds_are_the_astro_rows_and_gam2_is_dropped(cache):
    sets = sets_by_code(kv(), cache)
    for code in STATIONS:
        cs = sets[code].constant_set
        served = raw(code)
        for c in cs["constituents"]:
            assert c["speed_deg_per_hour"] == SPEEDS.speed(c["name"])          # the row's speed
            assert abs(served[c["name"]]["speed"] - c["speed_deg_per_hour"]) <= SPEED_FLAG_DEG_PER_HOUR
        names = {c["name"] for c in cs["constituents"]}
        assert "GAM2" not in names
        has_gam2 = "GAM2" in served
        assert (cs.get("dropped_constituents") == [{"name": "GAM2", "dropped_reason": "other",
                                                    "detail": NO_ASTRO_ROW}]) is has_gam2
        assert names | ({"GAM2"} if has_gam2 else set()) == set(served)
    # GAM2 is served at Bergen and Trondheim (0.10 and 0.31 cm), not at Stavanger
    assert [c for c in STATIONS if "GAM2" in raw(c)] == ["BGO", "TRD"]


def test_no_saved_speed_differs_from_its_row_by_more_than_the_flag():
    flagged = [(code, n, c["speed"], SPEEDS.speed(n)) for code in STATIONS for n, c in raw(code).items()
               if SPEEDS.speed(n) is not None and abs(c["speed"] - SPEEDS.speed(n)) > SPEED_FLAG_DEG_PER_HOUR]
    assert flagged == []


def test_a_speed_off_its_row_is_refused(cache):
    f = cache / "constituents" / "BGO.xml"
    f.write_text(f.read_text().replace('speed="28.98410425"', 'speed="28.98430425"'))   # M2 + 2e-4
    meta = cache / "constituents" / "BGO.xml.meta.json"
    m = json.loads(meta.read_text())
    import hashlib
    m["sha256"], m["size"] = hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_size
    meta.write_text(json.dumps(m))
    a = kv()
    with pytest.raises(SpeedDeviation, match="M2"):
        list(a.sets(a.fetch(cache, only=["BGO"])))


# --- the served licence (A3) -----------------------------------------------------------------------

def test_the_served_licence_pointer_is_the_one_claimed(cache):
    from otc_pipeline.adapters.kartverket import SERVED_LICENCE_URLS
    for code in STATIONS:
        root = ET.parse(FIXTURES / "constituents" / f"{code}.xml").getroot()
        assert root.find("meta").get("licenseurl") in SERVED_LICENCE_URLS
    assert kv().licence()["spdx"] == "CC-BY-4.0"


@pytest.mark.parametrize("pointer", ['<meta licenseurl="https://example.org/other-licence/"/>', ""])
def test_another_served_licence_is_refused(cache, pointer):
    import hashlib
    f = cache / "constituents" / "BGO.xml"
    f.write_text(f.read_text().replace('<meta licenseurl="http://kartverket.no/data/lisens/"/>', pointer))
    meta = cache / "constituents" / "BGO.xml.meta.json"
    m = json.loads(meta.read_text())
    m["sha256"], m["size"] = hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_size
    meta.write_text(json.dumps(m))
    with pytest.raises(ValueError, match="licen"):
        kv().fetch(cache, only=["BGO"])


def test_the_attribution_names_no_fixed_constituent():
    """The modification notice holds for any response: what is left out is in each set's
    dropped_constituents, not a fixed name in the licence."""
    a = kv().licence()["attribution"]
    assert "GAM2" not in a
    assert "constituents with no astro-table row left out (see each set's dropped_constituents)" in a


# -- published datums (datums spec §4.1 item 3, DT5b) on the synthetic stationlevels bodies ------

def raw_levels(code: str) -> dict[str, ET.Element]:
    root = ET.parse(FIXTURES / "stationlevels" / f"{code}.xml").getroot()
    return {r.get("code"): r for r in root.iter("reflevel")}


def test_datum_is_kartverkets_levels_above_cd(cache):
    sets = sets_by_code(kv(), cache)
    for code in STATIONS:
        d, lv = sets[code].constant_set["datum"], raw_levels(code)
        assert (d["zero"], d["zero_name"], d["chart_datum"]) == ("chart_datum", "CD", "cd")
        # Z0 above CD is Kartverket's MSL: its own tables are reproduced with it (ADPORT sa-fix evidence)
        assert d["msl_offset_m"] == pytest.approx(float(lv["MSL"].get("value")) / 100.0, abs=1e-9)
        want = {k.lower(): float(lv[k].get("value")) / 100.0
                for k in ("HAT", "MHWS", "MHW", "MHWN", "MSL", "MLWN", "MLW", "MLWS", "LAT", "CD", "NN2000")}
        assert d["named"] == pytest.approx(want, abs=1e-9)
        assert set(d["basis"]) == set(want)    # storm-surge (RETURN) and observed (OBS) levels not taken
        for k, b in d["basis"].items():
            assert (b["kind"], b["method"], b["source_name"]) == ("published", "source", k.upper())
            assert ("epoch" in b) == (k == "msl"), k


def test_msl_epoch_from_attribute_or_name(cache):
    sets = sets_by_code(kv(), cache)
    want = {"start": "1996-01-01", "end": "2014-12-31", "name": "Kartverket MSL 1996-2014"}
    assert sets["BGO"].constant_set["datum"]["basis"]["msl"]["epoch"] == want   # epoch="1996-2014"
    assert sets["TRD"].constant_set["datum"]["basis"]["msl"]["epoch"] == want   # only in the name


def test_cd_below_lat_keeps_cd_as_chart_datum(cache):
    d = sets_by_code(kv(), cache)["SVG"].constant_set["datum"]
    assert d["named"]["lat"] == pytest.approx(0.2) and d["named"]["cd"] == 0.0 and d["chart_datum"] == "cd"


@pytest.mark.parametrize("old, new, msg", [
    ('reflevel="CD">', 'reflevel="LAT">', "reflevel"),
    ('unit="cm"', 'unit="m"', "unit"),
    ('<reflevel code="CD" value="0.0"', '<reflevel code="CDX" value="0.0"', "no CD"),
    ('<reflevel code="CD" value="0.0"', '<reflevel code="CD" value="5.0"', "CD is"),
    ('licenseurl="http://kartverket.no/data/lisens/"', 'licenseurl="https://example.org/"', "licence"),
])
def test_unexpected_levels_response_is_refused(old, new, msg):
    from otc_pipeline.adapters.kartverket import parse_levels
    data = (FIXTURES / "stationlevels" / "BGO.xml").read_text()
    assert old in data
    with pytest.raises(ValueError, match=msg):
        parse_levels(data.replace(old, new).encode(), "BGO")


def test_datums_pass_build_checks_8_9_13(cache):
    from otc_pipeline.datums import datum_build_errors
    for s in sets_by_code(kv(), cache).values():
        assert datum_build_errors(s.constant_set["datum"]) == []


# --- SA and S1: IOS's arguments converted to the TCD's -------------------------------------------------

def test_sa_and_s1_are_converted_from_ios_to_the_tcd(cache):
    cons = {c["name"]: c for c in sets_by_code(kv(), cache)["BGO"].constant_set["constituents"]}
    sa, s1 = raw("BGO")["SA"], raw("BGO")["S1"]
    p1 = p1_deg(P1_EPOCH)
    assert cons["SA"]["phase_deg"] == pytest.approx((sa["phaseangle"] - sa["speed"] + p1) % 360, abs=1e-3)
    assert cons["S1"]["phase_deg"] == pytest.approx((s1["phaseangle"] - s1["speed"] - p1 + 90) % 360, abs=1e-3)


def _mutate(cache, code, old, new):
    import hashlib
    f = cache / "constituents" / f"{code}.xml"
    t = f.read_text()
    assert old in t
    f.write_text(t.replace(old, new))
    meta = cache / "constituents" / f"{code}.xml.meta.json"
    m = json.loads(meta.read_text())
    m["sha256"], m["size"] = hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_size
    meta.write_text(json.dumps(m))


def test_sa_served_with_another_definition_is_refused(cache):
    _mutate(cache, "BGO", 'speed="0.04106668" doodson="ZZAZZYZ"', 'speed="0.04106860" doodson="ZZAZZZZ"')
    a = kv()
    with pytest.raises(ValueError, match="SA"):
        list(a.sets(a.fetch(cache, only=["BGO"])))


def test_a_hidden_p1_term_is_refused(cache):
    # M2 served 2e-6 deg/h off its row (a p1 term): inside the 1e-4 flag, outside Kartverket's precision
    _mutate(cache, "BGO", 'speed="28.98410425"', 'speed="28.98410621"')
    a = kv()
    with pytest.raises(ValueError, match="M2"):
        list(a.sets(a.fetch(cache, only=["BGO"])))
