"""Pinned NOAA historical source loader: actual rows and fail-loud tamper controls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket

import pytest
from otc_pipeline import build_otc
from otc_pipeline import noaa_historical as historical
from otc_pipeline import station_map as sm
import otc_pipeline.release_writer as rw
from otc_pipeline.release_writer import _json_bytes
from otc_pipeline.noaa_historical import load_noaa_historical_inputs

MANIFEST = "noaa-two-gap-followup-v1-manifest.json"


@pytest.fixture
def actual_copy(tmp_path):
    bundle = tmp_path / "actual-bundle"
    shutil.copytree(Path(os.environ["OTC_NOAA_INPUTS"]), bundle)
    return bundle


def repin(bundle, manifest):
    raw = json.dumps(manifest, ensure_ascii=False).encode()
    (bundle / MANIFEST).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def changed_matrix(bundle, change):
    matrix = json.loads((bundle / "station-matrix-v1.json").read_text())
    change(matrix["stations"])
    raw = json.dumps(matrix, ensure_ascii=False).encode()
    (bundle / "station-matrix-v1.json").write_bytes(raw)
    manifest = json.loads((bundle / MANIFEST).read_text())
    manifest["station_matrix"]["sha256"] = hashlib.sha256(raw).hexdigest()
    return repin(bundle, manifest)


def test_native_loader_rejects_raw_position_reinterpretation(actual_copy):
    pin = changed_matrix(actual_copy, lambda rows: rows[0]["raw_position"].update(latitude="33°55.0001′N"))
    with pytest.raises(ValueError, match="position|coordinate"):
        load_noaa_historical_inputs(actual_copy, pin)


@pytest.mark.parametrize("change", [
    "ratio", "feet", "crosswalk", "missing_crosswalk", "removal_clock", "removal_event",
])
def test_native_loader_rejects_rehashed_source_annotation_changes(actual_copy, change):
    def corrupt(rows):
        if change == "ratio":
            rows[0]["contract_numeric_representation"]["height_adjusted_type"] = "A"
        elif change == "feet":
            rows[1]["contract_numeric_representation"]["height_offset_high"] = -0.2
        elif change == "crosswalk":
            rows[0]["modern_replacement_alias_verified"] = True
        elif change == "missing_crosswalk":
            rows[0].pop("id_crosswalk_status")
        elif change == "removal_clock":
            rows[0]["removal_events"][0]["timezone_not_declared_by_history"] = False
        else:
            rows[1]["removal_events"].pop()
    pin = changed_matrix(actual_copy, corrupt)
    with pytest.raises(ValueError):
        load_noaa_historical_inputs(actual_copy, pin)


@pytest.mark.parametrize("change", ["member_hash", "matrix_hash", "raw_row", "missing_member", "path_escape", "symlink_escape"])
def test_native_loader_refuses_incomplete_or_unbound_members(actual_copy, change, tmp_path):
    manifest = json.loads((actual_copy / MANIFEST).read_text())
    pin = os.environ["OTC_NOAA_INPUTS_SHA"]
    if change == "member_hash":
        (actual_copy / manifest["members"][0]["path"]).write_bytes(b"corrupt")
    elif change == "matrix_hash":
        (actual_copy / "station-matrix-v1.json").write_bytes(b"corrupt")
    elif change == "raw_row":
        path = actual_copy / "derived/atlantic-target-page-351.txt"
        path.write_text(path.read_text().replace("*0.84", "*0.85"))
    elif change == "missing_member":
        (actual_copy / manifest["members"][-1]["path"]).unlink()
    elif change == "path_escape":
        manifest["members"][0]["path"] = "../outside"
        pin = repin(actual_copy, manifest)
    else:
        path = actual_copy / manifest["members"][0]["path"]
        original = path.read_bytes()
        outside = tmp_path / "outside-source"
        outside.write_bytes(original)
        path.unlink()
        path.symlink_to(outside)
    with pytest.raises(ValueError):
        load_noaa_historical_inputs(actual_copy, pin)


def test_native_loader_rejects_wrong_manifest_pin():
    with pytest.raises(ValueError, match="manifest SHA-256"):
        load_noaa_historical_inputs(Path(os.environ["OTC_NOAA_INPUTS"]), "0" * 64)


def test_native_loader_never_requests_live_source(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("loader attempted a live source request")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    loaded = load_noaa_historical_inputs(Path(os.environ["OTC_NOAA_INPUTS"]), os.environ["OTC_NOAA_INPUTS_SHA"])
    assert len(loaded.profiles) == 2

def test_native_loader_retains_both_exact_official_rows():
    bundle = Path(os.environ["OTC_NOAA_INPUTS"])
    pin = os.environ["OTC_NOAA_INPUTS_SHA"]
    inputs = load_noaa_historical_inputs(bundle, pin)
    assert len(inputs.profiles) == 2
    assert inputs.manifest_sha256 == pin


def test_native_loader_actual_cells_and_provenance():
    bundle = Path(os.environ["OTC_NOAA_INPUTS"])
    inputs = load_noaa_historical_inputs(bundle, os.environ["OTC_NOAA_INPUTS_SHA"])
    assert len(inputs.profiles) == 2
    assert len(inputs.members) == 56
    assert len(inputs.input_roles) == 58
    assert len({member["path"] for member in inputs.members}) == 56
    assert inputs.place_parameter_rows == 2
    rows = json.loads((bundle / "station-matrix-v1.json").read_text())["stations"]
    for profile, row in zip(inputs.profiles, rows, strict=True):
        offsets = profile["subordinate_offsets"]
        assert {key: offsets[key] for key in row["contract_numeric_representation"]} == row["contract_numeric_representation"]
        assert json.loads(offsets["provenance"]["raw_values_json"]) == row
        assert offsets["provenance"]["id_crosswalk"]["status"] == "inferred"
        assert offsets["provenance"]["id_crosswalk"]["modern_alias_verified"] is False
        assert offsets["vintage"]["current_validity"] == "unestablished"
        assert offsets["vintage"]["current_accuracy"] == "unverified"
        assert [event["source_clock_timestamp"] for event in offsets["vintage"]["removal_events"]] == [event["source_clock_timestamp"] for event in row["removal_events"]]
        assert all(event["source_timezone"] is None for event in offsets["vintage"]["removal_events"])
        assert profile["source_records"][0]["published_set_ids"] == []
        assert profile["source_records"][0]["original_metadata"]["raw_values_json"] == offsets["provenance"]["raw_values_json"]
    print("PASS 56 members/58 roles/two rows; TEC high +4min,*0.84; TEC low +15min,*1.00; TWC high -35min,-0.06096m; TWC low -31min,0m; complete original source labels")


def station_hashes(doc):
    return {st["station_id"]: hashlib.sha256(_json_bytes(st)).hexdigest() for st in doc["stations"]}


def test_native_cached_release_adds_only_two_exact_source_profiles():
    base_path = Path(os.environ["OTC_NOAA_BASE_RELEASE"])
    doc = json.loads(base_path.read_text())
    before = station_hashes(doc)
    old_metadata = json.loads(json.dumps({key: value for key, value in doc.items() if key != "stations"}))
    old_cells = []
    for noaa_id in ("8443970", "9447130", "9414290"):
        station = next(st for st in doc["stations"] if st.get("aliases", {}).get("noaa") == noaa_id)
        selected = next(cs for cs in station["constant_sets"] if cs["set_id"] == station["recommended_set_id"])
        m2 = next(c for c in selected["constituents"] if c["name"] == "M2")
        old_cells.append({"station_id": station["station_id"], "set_id": selected["set_id"], "M2": dict(m2)})
    inputs = historical.load_noaa_historical_inputs(Path(os.environ["OTC_NOAA_INPUTS"]), os.environ["OTC_NOAA_INPUTS_SHA"])
    report = historical.apply_noaa_historical_offsets(doc, inputs, build_commit="374c61431ca9d88580ab93d2eda7a6e37dc406e0")
    after = station_hashes(doc)
    assert {sid: after[sid] for sid in before} == before
    added = [st for st in doc["stations"] if st["station_id"] not in before]
    assert len(added) == 2 and report["created_stations"] == 2
    for station, profile in zip(added, inputs.profiles, strict=True):
        assert station["name"] == profile["name"] and station["lat"] == profile["lat"] and station["lon"] == profile["lon"]
        assert station["type"] == "subordinate" and station["country"] == "USA"
        assert station["aliases"] == {} and station["constant_sets"] == [] and station["recommended_set_id"] is None
        assert station["subordinate_offsets"] == profile["subordinate_offsets"]
        assert station["source_records"] == profile["source_records"]
    schema = rw.pinned_schema()
    for definition, value in [(schema["properties"]["release"], doc["release"]),
                              *[({"$ref": "#/$defs/active_station"}, station) for station in added]]:
        validator = rw._Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], **definition})
        assert list(validator.iter_errors(value)) == []
    assert rw._historical_offset_errors(doc) == []
    for cell in old_cells:
        station = next(st for st in doc["stations"] if st["station_id"] == cell["station_id"])
        selected = next(cs for cs in station["constant_sets"] if cs["set_id"] == cell["set_id"])
        assert next(c for c in selected["constituents"] if c["name"] == "M2") == cell["M2"]
    assert report["profile_count"] == 2 and report["verified_member_count"] == 56
    assert doc["release"]["source_versions"]["noaa"] == old_metadata["release"]["source_versions"]["noaa"]
    assert doc["release"]["source_versions"]["noaa-historical"] == "annual-tide-tables-2020"
    old_metadata["release"]["source_versions"]["noaa-historical"] = "annual-tide-tables-2020"
    assert {key: value for key, value in doc.items() if key != "stations"} == old_metadata
    repeated = historical.apply_noaa_historical_offsets(doc, inputs, build_commit="374c61431ca9d88580ab93d2eda7a6e37dc406e0")
    assert station_hashes(doc) == after and repeated["created_stations"] == 0
    proof = {"old_station_count": len(before), "old_station_values_all_unchanged": True,
             "new_source_profiles": [station["station_id"] for station in added],
             "augmentation": report, "replay": repeated, "actual_unchanged_reference_cells": old_cells}
    Path(os.environ["OTC_H2_ADDED_STATIONS"]).write_text(json.dumps(added, ensure_ascii=False, indent=4) + "\n")
    Path(os.environ["OTC_H2_CANDIDATE"]).write_bytes(_json_bytes(doc))
    Path(os.environ["OTC_H2_PROOF"]).write_text(json.dumps(proof, indent=4) + "\n")
    print("PASS cached native source join: every", len(before), "old station object unchanged, exact two source profiles added, replay stable")


@pytest.fixture
def actual_application_inputs():
    doc = json.loads(Path(os.environ["OTC_H2_SMALL_RELEASE"]).read_text())
    inputs = historical.load_noaa_historical_inputs(Path(os.environ["OTC_NOAA_INPUTS"]), os.environ["OTC_NOAA_INPUTS_SHA"])
    return doc, inputs


def actual_private_existing_target(doc, inputs):
    """Private source-derived prior import; no invented parameters or alias."""
    profile = copy.deepcopy(inputs.profiles[0])
    founder = "noaa:annual-tide-tables-2020:atlantic:table2:2513"
    sid = sm.new_id(founder, {station["station_id"] for station in doc["stations"]})
    return {"station_id": sid, "status": "active", "name": profile["name"], "country": "USA",
            "lat": profile["lat"], "lon": profile["lon"], "timezone": "America/New_York",
            "type": "subordinate", "aliases": {}, "constant_sets": [], "recommended_set_id": None,
            "subordinate_offsets": profile["subordinate_offsets"], "source_records": profile["source_records"]}


@pytest.mark.parametrize("change,needle", [
    ("reference_missing", "reference"), ("reference_ambiguous", "ambiguous"),
    ("licence_missing", "licence"), ("reference_datum", "MLLW"),
    ("country_ambiguous", "country"), ("target_ambiguous", "ambiguous"),
    ("ordinary_offset", "offset"),
])
def test_native_application_actual_refusals_are_atomic(actual_application_inputs, change, needle):
    doc, inputs = actual_application_inputs
    first_reference = inputs.profiles[0]["subordinate_offsets"]["reference_station_id"]
    if change == "reference_missing":
        doc["stations"] = [station for station in doc["stations"] if station["station_id"] != first_reference]
    elif change == "reference_ambiguous":
        # Corrupt only the alias of another real reference; coefficients remain actual.
        next(station for station in doc["stations"] if station["station_id"] == "OTC-ee4593cd")["aliases"]["noaa"] = "8661070"
    elif change == "licence_missing":
        doc["licences"] = [licence for licence in doc["licences"] if licence["licence_id"] != "noaa-public-domain"]
    elif change == "reference_datum":
        reference = next(station for station in doc["stations"] if station["station_id"] == first_reference)
        selected = next(cs for cs in reference["constant_sets"] if cs["set_id"] == reference["recommended_set_id"])
        selected["datum"]["chart_datum"] = "lat"
    else:
        target = actual_private_existing_target(doc, inputs)
        doc["stations"].append(target)
        if change == "country_ambiguous":
            target["country"] = "CAN"  # Deliberately corrupted actual source-derived target metadata.
        elif change == "target_ambiguous":
            twin = copy.deepcopy(target)
            twin["station_id"] = sm.new_id("noaa:annual-tide-tables-2020:atlantic:table2:2513",
                                           {station["station_id"] for station in doc["stations"]})
            doc["stations"].append(twin)
        else:
            # Private old-format projection of the same genuine row. Existing
            # ordinary offsets cannot be reclassified or overwritten silently.
            fields = ("reference_station_id", "licence_id", "datum", "height_adjusted_type",
                      "time_offset_high_min", "time_offset_low_min", "height_offset_high", "height_offset_low")
            target["subordinate_offsets"] = {key: target["subordinate_offsets"][key] for key in fields}
    original = copy.deepcopy(doc)
    with pytest.raises(ValueError, match=needle):
        historical.apply_noaa_historical_offsets(doc, inputs, build_commit="374c61431ca9d88580ab93d2eda7a6e37dc406e0")
    assert doc == original


def test_native_application_preserves_real_related_river_and_current(actual_application_inputs):
    doc, inputs = actual_application_inputs
    controls = {station["station_id"]: copy.deepcopy(station) for station in doc["stations"]
                if station["station_id"] in {"OTC-0d711dda", "OTC-7620685d"}}
    assert len(controls) == 2
    report = historical.apply_noaa_historical_offsets(doc, inputs, build_commit="374c61431ca9d88580ab93d2eda7a6e37dc406e0")
    assert report["created_stations"] == 2
    assert {station["station_id"]: station for station in doc["stations"] if station["station_id"] in controls} == controls


@pytest.fixture
def actual_cli_args():
    return json.loads(Path(os.environ["OTC_H2_NATIVE_ARGV"]).read_text())


def test_native_cli_verified_input_guard_is_partial(actual_cli_args, capsys):
    with pytest.raises(SystemExit, match="not empty"):
        build_otc.main(actual_cli_args)
    assert "noaa historical inputs: 2 profiles, 56 verified members" in capsys.readouterr().out


@pytest.mark.parametrize("missing", ["--noaa-historical-inputs", "--noaa-historical-inputs-sha256"])
def test_native_cli_requires_paired_historical_inputs(actual_cli_args, missing):
    index = actual_cli_args.index(missing)
    del actual_cli_args[index:index + 2]
    with pytest.raises(ValueError, match="must be supplied together"):
        build_otc.main(actual_cli_args)


def test_native_cli_checks_actual_historical_manifest_hash(actual_cli_args):
    actual_cli_args[actual_cli_args.index("--noaa-historical-inputs-sha256") + 1] = "0" * 64
    with pytest.raises(ValueError, match="historical input manifest SHA-256 mismatch"):
        build_otc.main(actual_cli_args)
