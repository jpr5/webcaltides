"""Pinned NOAA historical source loader: actual rows and fail-loud tamper controls."""
import copy
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket

import pytest
import pyarrow.parquet as pq
from otc_pipeline.astro_tables import load_congen_run
from otc_pipeline.release_writer import validate_release, write_release
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


def test_native_comparison_ingress_fresh_actual_writer(tmp_path):
    report_path = Path(os.environ["OTC_NOAA_COMPARISON"])
    report_sha = os.environ["OTC_NOAA_COMPARISON_SHA"]
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == report_sha
    report = json.loads(report_path.read_text())
    assert report["comparison_completed"] is True and report["implementation_passed"] is True
    doc = json.loads(Path(os.environ["OTC_NOAA_BASE_RELEASE"]).read_text())
    before = {st["station_id"]: hashlib.sha256(_json_bytes(st)).hexdigest() for st in doc["stations"]}
    inputs = historical.load_noaa_historical_inputs(Path(os.environ["OTC_NOAA_INPUTS"]), os.environ["OTC_NOAA_INPUTS_SHA"])
    if hasattr(historical, "load_noaa_historical_comparison"):
        comparison = historical.load_noaa_historical_comparison(report_path, report_sha)
        historical.apply_noaa_historical_offsets(doc, inputs, build_commit="73aaa87f29537606117e138e517c968272d5e315", comparison=comparison)
    else:
        historical.apply_noaa_historical_offsets(doc, inputs, build_commit="73aaa87f29537606117e138e517c968272d5e315")
    assert {st["station_id"]: hashlib.sha256(_json_bytes(st)).hexdigest() for st in doc["stations"] if st["station_id"] in before} == before
    added = [st for st in doc["stations"] if st["station_id"] not in before]
    assert len(added) == 2
    # Direct writer proof uses unchanged actual references/controls from that fresh
    # full join; the single full producer publication remains the later H4 gate.
    selected = {p["reference"]["station_id"] for p in report["profiles"]} | {"OTC-e813b80c", "OTC-bb22c37b"} | {st["station_id"] for st in added}
    doc["stations"] = [st for st in doc["stations"] if st["station_id"] in selected]
    assert validate_release(doc) == []
    congen = load_congen_run(Path(os.environ["OTC_NOAA_CONGEN_RUN"]))
    written = write_release(doc, tmp_path, datestamp="20261011", app_export=None, congen_run=congen, parquet=True, jsonl_gz=True)
    payload = json.loads((tmp_path / "OTC_20261011.json").read_text())
    observed = {st["subordinate_offsets"]["offset_id"]: st["subordinate_offsets"]["vintage"] for st in payload["stations"] if st.get("subordinate_offsets")}
    print("ACTUAL fresh R3 → native loader/apply/validate/write; vintage", observed)
    for profile in report["profiles"]:
        vintage = observed[profile["offset_id"]]
        assert vintage["reference_2020_equivalence"] == profile["reference_2020_equivalence"], "genuine completed H3 outcome lost on fresh producer"
        assert vintage["reference_2020_equivalence_sha256"] == report_sha
    expected = {st["station_id"]: st["subordinate_offsets"] for st in added}
    for name, reader in [("OTC_20261011.json.gz", lambda p: json.loads(gzip.decompress(p.read_bytes()))["stations"]),
                         ("OTC_20261011.jsonl", lambda p: [json.loads(line) for line in p.read_text().splitlines()]),
                         ("OTC_20261011.jsonl.gz", lambda p: [json.loads(line) for line in gzip.decompress(p.read_bytes()).splitlines()])]:
        rows = reader(tmp_path / name)
        assert {st["station_id"]: st["subordinate_offsets"] for st in rows if st["station_id"] in expected} == expected
    with (tmp_path / "OTC_20261011.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    parquet = pq.read_table(tmp_path / "OTC_20261011.parquet").to_pylist()
    for rows in (rows, parquet):
        actual = {row["station_id"]: json.loads(row["subordinate_offsets"]) for row in rows if row.get("subordinate_offsets")}
        assert actual == expected
    for station, original in zip(added, inputs.profiles, strict=True):
        offsets = copy.deepcopy(station["subordinate_offsets"])
        offsets["vintage"].pop("reference_2020_equivalence_sha256")
        offsets["vintage"]["reference_2020_equivalence"] = "not_checked"
        assert offsets == original["subordinate_offsets"]
        assert station["source_records"] == original["source_records"]
    print("PASS same actual fresh annotation + all eight native writer files; all16,036 old station objects and exact source values unchanged")


@pytest.mark.parametrize("change", ["source", "row", "raw", "reference", "astro", "window", "datum", "noncomplete", "implementation", "duplicate", "errors", "bounds", "counts", "outcome", "input_descriptor", "residuals"])
def test_native_comparison_ingress_rehashed_binding_refusals(tmp_path, change):
    report = json.loads(Path(os.environ["OTC_NOAA_COMPARISON"]).read_text())
    profile = report["profiles"][0]
    if change == "source":
        report["source_manifest_sha256"] = "0" * 64
    elif change == "row":
        profile["table2"]["row"] = 2514
    elif change == "raw":
        profile["table2"]["raw_row_text_sha256"] = "0" * 64
    elif change == "reference":
        profile["reference"]["constant_set_payload"]["constituents"][0]["amplitude_m"] += 0.01
        profile["reference"]["constant_set_payload_sha256"] = hashlib.sha256(json.dumps(profile["reference"]["constant_set_payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    elif change == "astro":
        profile["astronomy"]["payload"]["constituents"]["M2"]["f"][0] += 0.01
        profile["astronomy"]["payload_sha256"] = hashlib.sha256(json.dumps(profile["astronomy"]["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    elif change == "window":
        profile["window"]["stop"] = "2020-12-31T05:00:00Z"
    elif change == "datum":
        profile["book"]["chart_datum"] = "MSL"
    elif change == "noncomplete":
        profile["comparison_completed"] = False
    elif change == "implementation":
        report["implementation_passed"] = False
    elif change == "duplicate":
        report["profiles"][1] = copy.deepcopy(profile)
    elif change == "errors":
        profile["errors"] = [{"class": "RuntimeError", "message": "deliberate corrupted captured report"}]
    elif change == "bounds":
        profile["reference_comparison"]["bounds"]["time_max_s"] = 1000
    elif change == "counts":
        profile["implementation_check"]["expected_events"].pop()
    elif change == "outcome":
        profile["reference_2020_equivalence"] = "matched_at_publisher_precision"
    elif change == "input_descriptor":
        profile["prediction_input"]["profile"] = "other"
    else:
        profile["implementation_check"]["pairs"][0]["height_error_m"] = 0.1
    if change in {"reference", "astro"}:
        descriptor = profile["prediction_input"]
        if change == "reference":
            descriptor["reference_constant_set_payload_sha256"] = profile["reference"]["constant_set_payload_sha256"]
        else:
            descriptor["astro_payload_sha256"] = profile["astronomy"]["payload_sha256"]
        profile["prediction_input_sha256"] = hashlib.sha256(json.dumps(descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    path = tmp_path / "rehashed-actual-report.json"
    raw = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    path.write_bytes(raw)
    doc = json.loads(Path(os.environ["OTC_H2_SMALL_RELEASE"]).read_text())
    before = copy.deepcopy(doc)
    inputs = historical.load_noaa_historical_inputs(Path(os.environ["OTC_NOAA_INPUTS"]), os.environ["OTC_NOAA_INPUTS_SHA"])
    with pytest.raises(ValueError):
        comparison = historical.load_noaa_historical_comparison(path, hashlib.sha256(raw).hexdigest())
        historical.apply_noaa_historical_offsets(doc, inputs, build_commit="73aaa87f29537606117e138e517c968272d5e315", comparison=comparison)
    assert doc == before


def test_native_comparison_ingress_wrong_file_hash():
    with pytest.raises(ValueError, match="SHA-256"):
        historical.load_noaa_historical_comparison(Path(os.environ["OTC_NOAA_COMPARISON"]), "0" * 64)


def test_native_comparison_ingress_actual_publication_stamp():
    comparison = json.loads(Path(os.environ['OTC_NOAA_COMPARISON']).read_text())
    pairs = json.loads(Path(os.environ['OTC_H4_STAMP_PROOF']).read_text())['selected_references']
    doc = json.loads(Path(os.environ['OTC_H2_SMALL_RELEASE']).read_text())
    for pair in pairs:
        reference = next(st for st in doc['stations'] if pair['reference_set_id'] == st.get('recommended_set_id'))
        reference['constant_sets'] = [copy.deepcopy(pair['fresh_native_merge_payload'])]
    inputs = historical.load_noaa_historical_inputs(Path(os.environ['OTC_NOAA_INPUTS']), os.environ['OTC_NOAA_INPUTS_SHA'])
    if not hasattr(historical, 'load_noaa_historical_comparison'):
        for pair in pairs:
            assert pair['fresh_native_merge_payload_sha256'] == pair['old_payload_sha256'], 'strict full reference hash rejects genuine changed publication stamp'
    loaded = historical.load_noaa_historical_comparison(Path(os.environ['OTC_NOAA_COMPARISON']), os.environ['OTC_NOAA_COMPARISON_SHA'])
    augmentation = historical.apply_noaa_historical_offsets(doc, inputs, build_commit='73aaa87', comparison=loaded)
    bindings = augmentation['reference_equivalence_bindings']
    assert len(bindings) == 2
    for pair, binding in zip(pairs, bindings, strict=True):
        assert binding['measured_payload_sha256'] == pair['old_payload_sha256']
        assert binding['published_payload_sha256'] == pair['fresh_native_merge_payload_sha256']
        assert binding['measured_build_commit'] == '632607a'
        assert binding['published_build_commit'] == '73aaa87'
        assert binding['allowed_difference_path'] == '/provenance/build/build_commit'
        target = next(st for st in doc['stations'] if st.get('subordinate_offsets', {}).get('offset_id') == pair['offset_id'])
        assert target['subordinate_offsets']['vintage']['reference_2020_equivalence'] == 'disagrees'
    print('PASS both actual native selected references: only publication stamp allowed; both complete raw hashes retained')


@pytest.mark.parametrize('change', ['amplitude', 'version', 'input_hash', 'stamp', 'missing', 'convention'])
def test_native_comparison_ingress_actual_nonstamp_refusals(change):
    pairs = json.loads(Path(os.environ['OTC_H4_STAMP_PROOF']).read_text())['selected_references']
    doc = json.loads(Path(os.environ['OTC_H2_SMALL_RELEASE']).read_text())
    reference = next(st for st in doc['stations'] if pairs[0]['reference_set_id'] == st.get('recommended_set_id'))
    selected = copy.deepcopy(pairs[0]['fresh_native_merge_payload'])
    reference['constant_sets'] = [selected]
    if change == 'amplitude':
        selected['constituents'][0]['amplitude_m'] += 0.01
    elif change == 'version':
        selected['source_version'] = '20261009'
    elif change == 'input_hash':
        selected['provenance']['input_sha256'][0] = '0' * 64
    elif change == 'stamp':
        selected['provenance']['build']['build_commit'] = '0000000'
    elif change == 'missing':
        selected['provenance'].pop('selection_reason')
    else:
        convention = next(c for c in doc['conventions'] if c['convention_id'] == selected['convention_id'])
        convention['canary']['gauges'] += 1
    before = copy.deepcopy(doc)
    inputs = historical.load_noaa_historical_inputs(Path(os.environ['OTC_NOAA_INPUTS']), os.environ['OTC_NOAA_INPUTS_SHA'])
    with pytest.raises(ValueError):
        loaded = historical.load_noaa_historical_comparison(Path(os.environ['OTC_NOAA_COMPARISON']), os.environ['OTC_NOAA_COMPARISON_SHA'])
        historical.apply_noaa_historical_offsets(doc, inputs, build_commit='73aaa87', comparison=loaded)
    assert doc == before


def genuine_comparison_args(actual_cli_args):
    return actual_cli_args + ['--noaa-historical-comparison', os.environ['OTC_NOAA_COMPARISON'],
                              '--noaa-historical-comparison-sha256', os.environ['OTC_NOAA_COMPARISON_SHA']]


@pytest.mark.parametrize('missing', ['--noaa-historical-comparison', '--noaa-historical-comparison-sha256'])
def test_native_comparison_ingress_cli_requires_pair(actual_cli_args, missing):
    args = genuine_comparison_args(actual_cli_args)
    index = args.index(missing)
    del args[index:index + 2]
    with pytest.raises(ValueError, match='must be supplied together'):
        build_otc.main(args)


def test_native_comparison_ingress_cli_requires_source(actual_cli_args):
    args = genuine_comparison_args(actual_cli_args)
    for flag in ('--noaa-historical-inputs', '--noaa-historical-inputs-sha256'):
        index = args.index(flag)
        del args[index:index + 2]
    with pytest.raises(ValueError, match='requires the pinned historical source inputs'):
        build_otc.main(args)


def test_native_comparison_ingress_cli_wrong_report_hash(actual_cli_args):
    args = genuine_comparison_args(actual_cli_args)
    args[-1] = '0' * 64
    with pytest.raises(ValueError, match='comparison report SHA-256'):
        build_otc.main(args)


def test_native_comparison_ingress_cli_verified_guard_partial(actual_cli_args):
    with pytest.raises(SystemExit, match='not empty'):
        build_otc.main(genuine_comparison_args(actual_cli_args))
