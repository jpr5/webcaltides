"""Actual original-source publication boundary; inputs must be pinned acquisitions."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


def _env(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"native proof requires {name}")
    return Path(value)


def test_restored_original_observation_adapters_are_registered():
    from otc_pipeline.sources import SOURCES, registry_errors

    names = {"dmi", "fmi", "rws", "smhi", "shom-refmar"}
    assert names <= set(SOURCES)
    assert registry_errors() == []


def test_actual_new_source_stations_follow_existing_country_alias_and_licence_shapes():
    from otc_pipeline.release_writer import pinned_schema

    proof = json.loads(_env("OTC_NATIVE_SHAPE_CELLS").read_text())
    schema = pinned_schema()
    validator = Draft202012Validator({"$ref": "#/$defs/active_station", "$defs": schema["$defs"]},
                                      format_checker=Draft202012Validator.FORMAT_CHECKER)
    assert len({(c["record"]["source"], c["record"]["source_record_id"]) for c in proof["cells"]}) >= 3
    errors = [error.message for cell in proof["cells"] for error in validator.iter_errors(cell["station"])]
    assert errors == []


def test_actual_bgo_metric_binding_preserves_original_and_tested_versions():
    station = json.loads(_env("OTC_NATIVE_BGO_BINDING").read_text())
    record = next(row for row in station["source_records"] if row["source"] == "kartverket")
    tested = next(cs for cs in station["constant_sets"] if cs["set_id"] == "OTC-e75bb97b/kartverket/BGO")
    encoded = record["original_metadata"]["OTC_metric_tested_set_bindings_json"]
    binding = json.loads(encoded)[0]
    assert binding["row_id"] == "kartverket/BGO/extremes"
    assert binding["original_source_version"] == record["source_version"] == "20261008"
    assert binding["tested_set_source_version"] == tested["source_version"] == "20261010"
    assert binding["set_id"] == tested["set_id"]
    assert binding["tested_input_sha256"] == tested["provenance"]["input_sha256"]
    assert binding["tested_constituents_sha256"] == hashlib.sha256(json.dumps(
        tested["constituents"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert binding["comparison_sha256"] == "30f78c762b4b282d5a0801c59bdb268622498d75aa34bc83cd9b7aa7221bef6d"
    assert record["published_set_ids"] == []


def test_native_country_cache_options_require_both_path_and_hash():
    from otc_pipeline.build_otc import main

    command = json.loads(_env("OTC_NATIVE_COUNTRY_COMMAND").read_text())
    args = command["argv"][1:]
    index = args.index("--country-resolutions-sha256")
    args = args[:index] + args[index + 2:]
    with pytest.raises(ValueError, match="country-resolutions.*supplied together"):
        main(args)


def test_actual_unknown_country_and_position_records_require_lookup_cache():
    from otc_pipeline.original_sources import require_country_resolution_cache

    stations = json.loads(_env("OTC_NATIVE_COUNTRY_STATIONS").read_text())
    assert len(stations) == 4999
    with pytest.raises(ValueError, match="pinned --country-resolutions"):
        require_country_resolution_cache({"stations": stations}, None)


def test_actual_incomplete_country_cache_requires_explicit_refresh():
    from otc_pipeline.original_sources import apply_country_resolution_cache

    stations = json.loads(_env("OTC_NATIVE_COUNTRY_STATIONS").read_text())
    for station in stations:
        station.setdefault("country", None)
    path = _env("OTC_NATIVE_INCOMPLETE_COUNTRY_CACHE")
    with pytest.raises(ValueError, match="explicit refresh.*1 missing/stale.*0 conflicting"):
        apply_country_resolution_cache({"stations": stations}, path=path,
                                       sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def test_native_original_sources_retain_every_spatial_identity_and_real_fit_payload():
    inventory_path = _env("OTC_ORIGINAL_INVENTORY")
    inventory = json.loads(inventory_path.read_text())
    release = json.loads(_env("OTC_ORIGINAL_RELEASE").read_text())
    inventory_hash = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    records = {}
    for station in release["stations"]:
        for record in station.get("source_records", []):
            key = (record["source"], record["source_version"], record["source_record_id"])
            assert key not in records, key
            records[key] = (station, record)
    auxiliary = {"reference_response_file", "model_grid_artifact", "official_prediction_response_file"}
    originals = [row for row in inventory["identities"] if row["granularity"] not in auxiliary]
    assert all((r["source"], r["version"], r["upstream_record_id"]) in records for r in originals)
    fit_run = _env("OTC_ORIGINAL_FIT_RUN")
    restricted = 0
    for original in originals:
        key = (original["source"], original["version"], original["upstream_record_id"])
        station, record = records[key]
        assert record["provenance"]["inventory_sha256"] == inventory_hash
        assert set(record["provenance"]["input_sha256"]) == set(original["input_sha256"])
        assert record["detail"].strip()
        ids = {cs["set_id"] for cs in station["constant_sets"]}
        assert set(record["published_set_ids"]) <= ids
        if original["source"] == "gesla" and record["disposition"] == "licence_restricted":
            assert original["fit_outcome"] == "fitted"
            path = fit_run / "fits" / (original["upstream_record_id"] + ".json")
            payload = path.read_bytes()
            metadata = record["original_metadata"]
            assert metadata["original_fit_sha256"] == hashlib.sha256(payload).hexdigest()
            assert json.loads(metadata["original_fit_json"]) == json.loads(payload)
            assert Path(metadata["original_fit_path"]).resolve() == path.resolve()
            restricted += 1
    assert restricted == 1543


def test_native_historical_fit_documents_retain_full_original_bytes_and_binding():
    originals = json.loads(_env("OTC_ORIGINAL_DERIVED_INPUTS").read_text())
    release = json.loads(_env("OTC_ORIGINAL_RELEASE").read_text())
    records = {(record["source"], record["source_version"], record["source_record_id"]): record
               for station in release["stations"] for record in station.get("source_records", [])}
    assert len(originals) == 9
    for original in originals:
        record = records[("gesla", original["source_version"], original["record_id"])]
        retained = json.loads(record["original_metadata"]["historical_original_fit_documents_json"])
        actual = next(row for row in retained if row["sha256"] == original["sha256"])
        raw = Path(original["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == original["sha256"]
        assert actual["original_document_json"].encode("utf-8") == raw
        assert json.loads(actual["original_document_json"]) == json.loads(raw)
        assert actual["role"] == original["role"]


@pytest.mark.parametrize("mutation", ["wrong_hash", "missing_row", "wrong_bin", "widened_bound"])
def test_actual_quality_ingress_refuses_changed_hash_scope_bin_or_bound(tmp_path, mutation):
    from otc_pipeline.original_sources import apply_quality_findings
    from otc_pipeline.source_inventory import load_source_inventory

    original = _env("OTC_SOURCE_QUALITY_FINDINGS")
    original_facts = json.loads(original.read_text())
    data = json.loads(original.read_text())
    if mutation == "missing_row":
        data["label_facts"].pop()
    elif mutation == "wrong_bin":
        data["label_facts"][0]["finding"]["bin_id"] = 2
    elif mutation == "widened_bound":
        data["label_facts"][-1]["finding"]["bound"] = 183
    for fact in data["label_facts"]:
        fact["detail_line"] = "OTC metric finding: " + json.dumps(fact["finding"], sort_keys=True, separators=(",", ":"))
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(data))
    sha = "0" * 64 if mutation == "wrong_hash" else hashlib.sha256(path.read_bytes()).hexdigest()
    inventory_path = _env("OTC_ORIGINAL_INVENTORY")
    inventory = load_source_inventory(inventory_path, hashlib.sha256(inventory_path.read_bytes()).hexdigest())
    release = json.loads(_env("OTC_ORIGINAL_RELEASE").read_text())
    # The old native artifact deliberately lacks these records. This controlled
    # identity projection isolates ingress refusal paths, not publication GREEN.
    stations = {station["station_id"]: station for station in release["stations"]}
    for fact in original_facts["label_facts"]:
        station = stations[fact["finding"]["station_id"]]
        identity = fact["identity"]
        records = station.setdefault("source_records", [])
        if not any(all(record[key] == identity[key] for key in identity) for record in records):
            records.append({**identity, "provenance": fact["inventory_provenance"], "detail": "Actual native source identity."})
    message = {"wrong_hash": "SHA-256 mismatch", "missing_row": "six-row scope",
               "wrong_bin": "changes its row", "widened_bound": "changes its timing"}[mutation]
    with pytest.raises(ValueError, match=message):
        apply_quality_findings(release, inventory, path=path, sha256=sha)
