"""The per-source canary (manifest §2.5, F3) and the gesla-fit comparator sets.

Unit tests run on constructed sets. The real-surface test (OTC_LIVE=1, and the GESLA fit
documents of $OTC_CANARY_BUILD in the cache or the work bucket) fetches NOAA harcon live at
Boston, Seattle and San Francisco and runs the canary against our GESLA fits: it passes under
NOAA's true convention and fails with the phase reference mislabelled local.
"""
from __future__ import annotations

import json
import math
import os

import pytest

from otc_pipeline.adapter import SourceSet, assert_contract
from otc_pipeline.canary import PAIR_RADIUS_KM, canary, distance_km, pair_sets, to_greenwich
from gesla.select_constituents import CANDIDATES
from gesla.tcd_table import TcdTable
from otc_pipeline.gesla_sets import (DOC_VERSION, fit_document, load_gesla_sets, read_fits, source_set,
                                     time_base_annotation, write_fits)

from noaa_harcon_canary import G9_GESLA, PROOF_STATIONS, NoaaHarconProof, run

SPEEDS = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356}
BOSTON = {"M2": (1.371, 109.2), "S2": (0.208, 146.2), "K1": (0.143, 205.6), "O1": (0.116, 187.1)}


def mkset(rid, lat, lon, consts, source="noaa", country="USA"):
    return SourceSet(
        station_hint={"name": rid, "country": country, "lat": lat, "lon": lon, "type": "reference",
                      "timezone": None, "aliases": {source: rid}},
        constant_set={"source": source, "source_record_id": rid,
                      "constituents": [{"name": n, "speed_deg_per_hour": SPEEDS[n], "amplitude_m": a,
                                        "phase_deg": g} for n, (a, g) in consts.items()]})


def shifted(consts, dg=0.0, da=0.0):
    return {n: (a + da, (g + dg) % 360) for n, (a, g) in consts.items()}


def three(consts_src, consts_gesla, n=3):
    src = [mkset(f"s{i}", 40.0 + i, -70.0, consts_src) for i in range(n)]
    ges = [mkset(f"g{i}", 40.0 + i + 0.004, -70.0 - 0.003, consts_gesla, "gesla-fit") for i in range(n)]
    return src, ges


# --- to_greenwich ---------------------------------------------------------------------------------

def test_to_greenwich_is_identity_for_greenwich():
    conv = {"phase_reference": "greenwich_utc"}
    assert to_greenwich(109.2, SPEEDS["M2"], conv) == 109.2


def test_to_greenwich_matches_noaa_local_and_gmt_phases():
    # NOAA serves both for Boston (Z = -5): M2 phase_local 324.2 / phase_GMT 109.2, K1 130.4 / 205.6
    conv = {"phase_reference": "local", "utc_offset_hours": -5}
    assert to_greenwich(324.2, 28.984104, conv) == pytest.approx(109.12, abs=0.01)
    assert to_greenwich(130.4, 15.041069, conv) == pytest.approx(205.61, abs=0.01)


def test_to_greenwich_east_offset_and_range():
    conv = {"phase_reference": "local", "utc_offset_hours": 1}
    assert to_greenwich(10.0, 15.0, conv) == pytest.approx(355.0)
    g = to_greenwich(0.0, 0.0, conv)
    assert 0.0 <= g < 360.0


@pytest.mark.parametrize("conv", [{"phase_reference": "local"}, {"phase_reference": "local", "utc_offset_hours": "1"},
                                  {"phase_reference": "local", "utc_offset_hours": math.nan},
                                  {"phase_reference": "zone"}, {}])
def test_to_greenwich_rejects_an_unusable_convention(conv):
    with pytest.raises(ValueError):
        to_greenwich(10.0, 15.0, conv)


# --- pairing --------------------------------------------------------------------------------------

KM_LAT = 1 / 111.195     # degrees of latitude per km on the EARTH_RADIUS_KM sphere


def ids(matches):
    return [(m.source_set.constant_set["source_record_id"], m.gesla_set.constant_set["source_record_id"])
            for m in matches]


def test_distance_km_is_great_circle():
    assert distance_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.195, abs=1e-3)
    assert distance_km(60.0, 10.0, 60.0, 11.0) == pytest.approx(55.6, abs=0.1)   # cos(60) shrinks longitude
    assert distance_km(-16.0, 179.998, -16.0, -179.997) == pytest.approx(0.5348, abs=1e-3)   # antimeridian


def test_pairing_takes_the_nearest_within_the_radius_and_skips_subordinates():
    src = [mkset("a", 40.0, -70.0, BOSTON), mkset("far", 41.0, -70.0, BOSTON),
           SourceSet({"lat": 40.0, "lon": -70.0}, None, {"reference_source_record_id": "a"})]
    ges = [mkset("g-near", 40.0 + 1.5 * KM_LAT, -70.0, BOSTON, "gesla-fit"),
           mkset("g-other", 40.0 + 4.0 * KM_LAT, -70.0, BOSTON, "gesla-fit"),
           mkset("g-out", 41.0 + (PAIR_RADIUS_KM + 0.1) * KM_LAT, -70.0, BOSTON, "gesla-fit")]
    matches, unpaired = pair_sets(src, ges)
    assert ids(matches) == [("a", "g-near")] and unpaired == ["far"]
    m = matches[0]
    assert m.method == "distance" and m.distance_km == pytest.approx(1.5, abs=1e-3) and m.ambiguous_with == ()


def test_pairing_beyond_a_hundredth_of_a_degree():
    # LINZ positions are whole arc-minutes: a gauge 2.7 km (0.024 deg) away is the same port
    matches, unpaired = pair_sets([mkset("070AUCKLAND", -36.8333, 174.7667, BOSTON, "linz", "NZL")],
                                  [mkset("auckland-auct-nzl-ttw", -36.8435, 174.7853, BOSTON, "gesla-fit", "NZL")])
    assert ids(matches) == [("070AUCKLAND", "auckland-auct-nzl-ttw")] and unpaired == []
    assert 1.0 < matches[0].distance_km < PAIR_RADIUS_KM


def test_pairing_radius_is_a_parameter():
    src = [mkset("a", 40.0, -70.0, BOSTON)]
    ges = [mkset("g", 40.0 + 3.0 * KM_LAT, -70.0, BOSTON, "gesla-fit")]
    assert len(pair_sets(src, ges)[0]) == 1
    assert pair_sets(src, ges, radius_km=2.0) == ([], ["a"])


def test_pairing_reports_an_ambiguous_pair():
    src = [mkset("a", 40.0, -70.0, BOSTON)]
    ges = [mkset("g1", 40.0 + 1.0 * KM_LAT, -70.0, BOSTON, "gesla-fit"),
           mkset("g2", 40.0 - 1.6 * KM_LAT, -70.0, BOSTON, "gesla-fit"),
           mkset("g3", 40.0 + 2.5 * KM_LAT, -70.0, BOSTON, "gesla-fit")]
    (m,), _ = pair_sets(src, ges)
    assert m.gesla_set.constant_set["source_record_id"] == "g1" and m.ambiguous_with == ("g2",)


def test_pairing_prefers_an_alias_link_to_a_nearer_gauge():
    # NOAA 8443970 <-> GESLA boston-8443970-usa-noaa, although another record is nearer
    src = [mkset("8443970", 42.3548, -71.0534, BOSTON)]
    ges = [mkset("boston-8443970-usa-noaa", 42.3548 + 2.0 * KM_LAT, -71.0534, BOSTON, "gesla-fit"),
           mkset("boston-bos-usa-uhslc_fd", 42.3548 + 0.2 * KM_LAT, -71.0534, BOSTON, "gesla-fit")]
    (m,), _ = pair_sets(src, ges, source="noaa")
    assert m.gesla_set.constant_set["source_record_id"] == "boston-8443970-usa-noaa" and m.method == "alias"
    assert m.distance_km == pytest.approx(2.0, abs=1e-3)
    # without the source name there is no link; the nearest wins
    (m,), _ = pair_sets(src, ges)
    assert m.gesla_set.constant_set["source_record_id"] == "boston-bos-usa-uhslc_fd" and m.method == "distance"


def test_pairing_follows_an_explicit_gesla_alias_beyond_the_radius():
    src = [SourceSet({**mkset("x", 0.0, 0.0, BOSTON).station_hint, "aliases": {"linz": "x", "gesla": ["g-far"]}},
                     mkset("x", 0.0, 0.0, BOSTON).constant_set)]
    ges = [mkset("g-far", 0.0, 0.2, BOSTON, "gesla-fit"), mkset("g-near", 0.0, 0.01, BOSTON, "gesla-fit")]
    (m,), _ = pair_sets(src, ges, source="linz")
    assert m.gesla_set.constant_set["source_record_id"] == "g-far" and m.method == "alias"
    assert m.distance_km == pytest.approx(22.24, abs=0.01)


def test_pairing_never_crosses_a_country_border():
    src = [mkset("a", 54.8, 9.4, BOSTON, "dmi", "DNK")]
    ges = [mkset("g-deu", 54.8 + 0.5 * KM_LAT, 9.4, BOSTON, "gesla-fit", "DEU"),
           mkset("g-dnk", 54.8 + 3.0 * KM_LAT, 9.4, BOSTON, "gesla-fit", "DNK")]
    (m,), _ = pair_sets(src, ges)
    assert m.gesla_set.constant_set["source_record_id"] == "g-dnk"
    assert pair_sets(src, ges[:1]) == ([], ["a"])


# --- the canary -----------------------------------------------------------------------------------

def test_canary_passes_on_small_differences():
    src, ges = three(BOSTON, shifted(BOSTON, dg=0.3, da=0.002))
    r = canary("noaa", src, ges)
    assert r.passed and r.reasons == () and r.n_pairs == 3 and r.n_gauges == 3
    assert r.median_dg_deg == pytest.approx(0.3) and r.median_da_m == pytest.approx(0.002)
    assert r.convention_canary() == {"status": "pass", "gauges": 3, "median_amp_diff_m": 0.002,
                                     "median_phase_diff_deg": 0.3}
    assert all(p.g9.passed for p in r.pairs)
    d = json.loads(json.dumps(r.to_dict(), allow_nan=False))
    assert d["pair_radius_km"] == PAIR_RADIUS_KM and d["ambiguous"] == []
    assert all(p["method"] == "distance" and 0.0 < p["distance_km"] < 1.0 and p["ambiguous_with"] == []
               for p in d["pairs"])


def test_canary_fails_on_a_phase_convention_error():
    src, ges = three(BOSTON, BOSTON)
    conv = {"phase_reference": "local", "utc_offset_hours": -5}
    mislabelled = [mkset(s.constant_set["source_record_id"], s.station_hint["lat"], s.station_hint["lon"],
                         {n: (a, to_greenwich(g, SPEEDS[n], conv)) for n, (a, g) in BOSTON.items()}) for s in src]
    assert canary("noaa", src, ges).passed
    r = canary("noaa", mislabelled, ges)
    assert not r.passed and any("median |dg|" in x for x in r.reasons)
    assert r.convention_canary()["status"] == "fail"


def test_canary_fails_on_an_amplitude_error():
    src, ges = three(BOSTON, shifted(BOSTON, da=0.05))
    r = canary("noaa", src, ges)
    assert not r.passed and any("median |dA|" in x for x in r.reasons)


def test_canary_needs_min_gauges_distinct_gesla_gauges():
    src, ges = three(BOSTON, BOSTON, n=2)
    r = canary("noaa", src, ges)
    assert not r.passed and "2 GESLA gauge(s) paired" in r.reasons[0]
    # two source sets on one GESLA gauge count once
    src3 = src + [mkset("dup", src[0].station_hint["lat"], -70.0, BOSTON)]
    assert canary("noaa", src3, ges).n_gauges == 2
    assert canary("noaa", src, ges, min_gauges=2).passed


def test_canary_fails_when_a_pair_misses_a_constituent():
    src, ges = three(BOSTON, BOSTON)
    src[1] = mkset("s1", 41.0, -70.0, {n: v for n, v in BOSTON.items() if n != "O1"})
    r = canary("noaa", src, ges)
    assert not r.passed and any("s1" in x and "O1" in x for x in r.reasons)


def test_canary_with_no_overlap_is_strict_json():
    r = canary("noaa", [mkset("a", 0.0, 0.0, BOSTON)], [mkset("g", 10.0, 10.0, BOSTON, "gesla-fit")])
    assert not r.passed and r.unpaired == ("a",) and math.isnan(r.median_da_m)
    d = json.loads(json.dumps(r.to_dict(), allow_nan=False))
    assert d["median_da_cm"] is None and r.convention_canary() == {"status": "fail", "gauges": 0}


def test_canary_tolerances_are_parameters():
    src, ges = three(BOSTON, shifted(BOSTON, dg=1.5))
    assert canary("noaa", src, ges).passed
    assert not canary("noaa", src, ges, phase_tol_deg=1.0).passed


# --- gesla-fit documents (DOC_VERSION 3, format 1.0) -----------------------------------------------

TABLE = TcdTable({"years": [2020, 2020], "tcd_sha256": "b" * 64, "aliases": {},
                  "constituents": {n: {"speed": sp, "v0u": [0.0], "f": [1.0]} for n, sp in SPEEDS.items()}})
TEST_LICENCE = "test-only-licence"   # TEST ONLY: the per-record licence ids are L-NC's


def doc(rid="boston-8443970-usa-noaa", lat=42.354801, lon=-71.0534):
    meta = {"SITE NAME": "Boston", "SITE CODE": "8443970", "COUNTRY": "USA", "LATITUDE": str(lat),
            "LONGITUDE": str(lon), "CONTRIBUTOR (ABBREVIATED)": "NOAA"}
    fit = {"first_s": 1104537600, "last_s": 1704067200, "n": 160000, "z0_m": 1.6,
           "rayleigh_hours": 166000.0, "rayleigh_kept": [n for n in CANDIDATES if n in SPEEDS],
           "candidates_missing_from_tcd": [n for n in CANDIDATES if n not in SPEEDS],
           "constituents": {n: {"amp_m": a, "phase_deg": g, "u_m": 0.001} for n, (a, g) in BOSTON.items()}}
    tb = time_base_annotation(None, 0, rid, decided_in="20261010")
    return fit_document(rid, "4.1", "a" * 64, meta, TABLE, fit, good_hours=166000.0, time_base=tb)


def test_source_set_from_a_fit_document():
    s = source_set(doc(), licence_id=TEST_LICENCE)
    cs = s.constant_set
    assert cs["source"] == "gesla-fit" and cs["source_type"] == "gauge" and cs["source_record_id"] == "boston-8443970-usa-noaa"
    assert cs["record_span"] == {"start": "2005-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z", "good_samples": 160000,
                                 "good_hours": 166000.0}
    m2 = next(c for c in cs["constituents"] if c["name"] == "M2")
    assert m2 == {"name": "M2", "speed_deg_per_hour": SPEEDS["M2"], "amplitude_m": 1.371, "phase_deg": 109.2,
                  "amp_uncertainty_m": 0.001}
    assert cs["licence_id"] == TEST_LICENCE and cs["time_base"]["code"] == "unverified"
    assert s.station_hint["lat"] == 42.354801 and s.station_hint["aliases"] == {"gesla": ["boston-8443970-usa-noaa"]}


def test_source_set_rejects_another_doc_version():
    with pytest.raises(ValueError, match="doc_version"):
        source_set(dict(doc(), doc_version=DOC_VERSION + 1), licence_id=TEST_LICENCE)


def test_fits_round_trip_and_manifest_check(tmp_path):
    docs = [doc(), doc("seattle-9447130-usa-noaa", 47.6, -122.3)]
    write_fits(docs, tmp_path)
    ids = {d["record_id"]: TEST_LICENCE for d in docs}
    assert [d["record_id"] for d in read_fits(tmp_path)] == ["boston-8443970-usa-noaa", "seattle-9447130-usa-noaa"]
    sets = load_gesla_sets(in_dir=tmp_path, licence_ids=ids, only=["seattle-9447130-usa-noaa"])
    assert [s.constant_set["source_record_id"] for s in sets] == ["seattle-9447130-usa-noaa"]
    with pytest.raises(KeyError, match="key_west"):
        load_gesla_sets(in_dir=tmp_path, licence_ids=ids, only=["key_west-8724580-usa-noaa"])
    p = tmp_path / "boston-8443970-usa-noaa.json"
    p.write_text(p.read_text().replace("1.371", "1.372"))
    with pytest.raises(ValueError, match="sha256"):
        read_fits(tmp_path)


def test_load_needs_a_datestamp_or_dir():
    with pytest.raises(ValueError):
        load_gesla_sets(licence_ids={})


# --- the proof adapter on its own (no network) ----------------------------------------------------

def test_proof_adapter_mislabel_shifts_phases_by_speed_times_offset(tmp_path):
    from otc_pipeline.adapter import FetchResult
    station = {"name": "Boston", "lat": 42.35389, "lng": -71.05028, "timezonecorr": -5}
    harcon = {"units": "meters", "HarmonicConstituents": [
        {"name": "M2", "amplitude": 1.371, "phase_GMT": 109.2, "speed": 28.984104}]}
    fetched = FetchResult("noaa", "20261008", tmp_path, (), {"8443970": {"station": station, "harcon": harcon,
                                                                         "input_sha256": []}})
    true_ = next(NoaaHarconProof().sets(fetched)).constant_set["constituents"][0]["phase_deg"]
    wrong = next(NoaaHarconProof("local").sets(fetched)).constant_set["constituents"][0]["phase_deg"]
    assert true_ == 109.2 and wrong == pytest.approx((109.2 + 5 * 28.984104) % 360, abs=1e-3)


# --- real surface (live NOAA, GESLA fits of $OTC_CANARY_BUILD) -------------------------------------

LIVE = os.environ.get("OTC_LIVE") == "1"
BUILD = os.environ.get("OTC_CANARY_BUILD")


@pytest.mark.skipif(not (LIVE and BUILD), reason="set OTC_LIVE=1 and OTC_CANARY_BUILD=<D> (gesla fit documents)")
@pytest.mark.parametrize("phase_reference,passes", [("greenwich_utc", True), ("local", False)])
def test_noaa_canary_against_gesla_fits_live(tmp_path, phase_reference, passes):
    res = run(BUILD, phase_reference, PROOF_STATIONS, tmp_path)
    print(f"\nF3 canary noaa phase_reference={phase_reference}: " + json.dumps(res.to_dict(), sort_keys=True))
    assert res.n_gauges == 3 and res.passed is passes, res.to_dict()
    assert all(p.method == "alias" and p.distance_km < 1.0 for p in res.pairs), res.to_dict()


@pytest.mark.skipif(not LIVE, reason="set OTC_LIVE=1")
def test_proof_adapter_contract_live(tmp_path):
    adapter = NoaaHarconProof()
    assert_contract(adapter, adapter.fetch(tmp_path, only=["8443970"]))
    assert set(G9_GESLA) >= set(PROOF_STATIONS)


def test_proof_adapter_passes_the_1_0_contract_offline(tmp_path):
    """The proof adapter on the saved NOAA responses (Boston, Seattle, San Francisco), offline."""
    import gzip
    import hashlib
    from pathlib import Path

    from otc_pipeline.adapter import FetchResult, InputFile, check_contract
    from otc_pipeline.adapters import noaa
    fix = Path(__file__).parent / "fixtures" / "noaa"
    listing = {s["id"]: s for s in json.loads(gzip.decompress((fix / "stations.json.gz").read_bytes()))["stations"]}
    files, items = [], {}
    for sid in PROOF_STATIONS:
        data = (fix / "harcon" / f"{sid}.json").read_bytes()
        (tmp_path / f"harcon_{sid}.json").write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        files.append(InputFile(f"harcon_{sid}.json", sha, len(data), "https://example.invalid", "2026-10-08T00:00:00Z"))
        items[sid] = {"station": listing[sid], "harcon": json.loads(data), "input_sha256": [sha]}
    fetched = FetchResult("noaa", "20261008", tmp_path, tuple(files), items)
    adapter = NoaaHarconProof()
    assert check_contract(adapter, fetched) == []
    lic, prod = adapter.licence(), noaa.NoaaAdapter().licence()
    # the production adapter's licence (same data, same terms) ...
    assert {k: lic[k] for k in ("licence_id", "spdx", "commercial_use")} == \
        {k: prod[k] for k in ("licence_id", "spdx", "commercial_use")}
    # ... with an attribution that says what this proof adapter does: NOAA's own served speeds
    assert "NOAA's own speeds" in lic["attribution"] and "astro table" not in lic["attribution"], lic
    served = {c["name"]: float(c["speed"]) for c in items["8443970"]["harcon"]["HarmonicConstituents"]}
    boston = next(s for s in adapter.sets(fetched) if s.constant_set["source_record_id"] == "8443970")
    assert all(c["speed_deg_per_hour"] == served[c["name"]] for c in boston.constant_set["constituents"])
    assert {s.constant_set["qc_status"] for s in adapter.sets(fetched)} == {"ok"}
