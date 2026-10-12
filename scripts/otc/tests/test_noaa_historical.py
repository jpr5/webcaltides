"""Pinned NOAA historical source loader: actual rows and fail-loud tamper controls."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket

import pytest
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
