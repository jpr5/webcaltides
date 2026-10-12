"""Original source inventory accounting; publication and quality selection are separate."""
import hashlib
import json
from pathlib import Path

import pytest

from otc_pipeline import gesla_build as gb
from otc_pipeline.gesla_release import BuildReport
from otc_pipeline.source_inventory import report_inventory


def inventory_file(tmp_path, identities=None):
    raw = tmp_path / "original.csv"
    raw.write_text("record,amplitude\noriginal,1.2\n")
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    identities = identities or [{"source": "dmi", "version": "capture:abc", "upstream_record_id": "20047",
                                 "granularity": "actual_observation_acquisition_record", "input_sha256": [digest]}]
    doc = {"inventory_version": 1, "input_files": [{"path": str(raw), "sha256": digest, "size": raw.stat().st_size}],
           "identities": identities, "limitations": [{"source": "dmi", "gap": "semantic version unresolved"}]}
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(doc))
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), raw


def test_explicit_pinned_inventory_retains_original_version_and_hash(tmp_path):
    path, pin, raw = inventory_file(tmp_path)
    inventory = gb.load_source_inventory(path, pin)
    assert inventory.document["identities"][0]["version"] == "capture:abc"
    assert inventory.provenance["sha256"] == pin
    assert inventory.document["identities"][0]["input_sha256"] == [hashlib.sha256(raw.read_bytes()).hexdigest()]


@pytest.mark.parametrize("corruption", ["inventory_pin", "source_bytes", "unknown_input_ref", "duplicate_identity"])
def test_inventory_integrity_failures_halt(tmp_path, corruption):
    path, pin, raw = inventory_file(tmp_path)
    if corruption == "inventory_pin":
        pin = "0" * 64
    elif corruption == "source_bytes":
        raw.write_text("changed")
    else:
        doc = json.loads(path.read_text())
        if corruption == "unknown_input_ref":
            doc["identities"][0]["input_sha256"] = ["0" * 64]
        else:
            doc["identities"].append(doc["identities"][0])
        path.write_text(json.dumps(doc))
        pin = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        gb.load_source_inventory(path, pin)


def test_existing_nonfit_outcomes_are_reported_without_changing_published_sets():
    rows = [{"record": "available", "outcome": "fitted"},
            {"record": "short", "outcome": "short", "reason": "no good data", "data_hours": 0.0},
            {"record": "unusable", "outcome": "unusable", "reason": "rank deficiency", "data_hours": 1200.0}]
    report = BuildReport(published=["available"])
    gb.account_nonfit(rows, report)
    body = report.as_dict()
    assert body["records"]["published"] == ["available"]
    assert {r["record"]: (r["reason"], r["good_hours"]) for r in body["records"]["refused"]} == {
        "short": ("too_short", 0.0), "unusable": ("fit_unusable", 1200.0)}


def test_native_report_writer_accounts_missing_source_without_claiming_publication(tmp_path):
    path, pin, _raw = inventory_file(tmp_path)
    inventory = gb.load_source_inventory(path, pin)
    release = {"stations": []}
    report_path = tmp_path / "BUILD_REPORT.json"
    gb.write_report(BuildReport(), {"build_commit": "abc1234"}, report_path, release=release, inventory=inventory)
    body = json.loads(report_path.read_text())
    row = body["source_inventory"]["identities"][0]
    assert row["upstream_record_id"] == "20047"
    assert row["disposition"] == "acquired_observations_pending_publication"
    assert row["published_set_ids"] == []
    assert body["source_inventory"]["identity_count"] == 1
    assert body["source_inventory"]["limitations"] == inventory.document["limitations"]


def test_alias_and_direct_set_lineage_are_distinguished_from_versions(tmp_path):
    path, pin, raw = inventory_file(tmp_path)
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    rows = [{"source": "noaa", "version": "20261008", "upstream_record_id": "1234567",
             "granularity": "native_source_set", "input_sha256": [digest]},
            {"source": "gesla", "version": "4.1", "upstream_record_id": "record",
             "granularity": "upstream_gauge_record", "input_sha256": [digest]}]
    path, pin, _raw = inventory_file(tmp_path, rows)
    inventory = gb.load_source_inventory(path, pin)
    release = {"stations": [{"station_id": "OTC-1", "status": "active", "aliases": {"noaa": "1234567"},
                             "constant_sets": [{"set_id": "fit", "source": "gesla-fit", "source_record_id": "record",
                                                "source_version": "4.1"}]}]}
    report_path = tmp_path / "BUILD_REPORT.json"
    gb.write_report(BuildReport(published=["record"]), {}, report_path, release=release, inventory=inventory)
    body = json.loads(report_path.read_text())["source_inventory"]
    assert [(r["source"], r["disposition"]) for r in body["identities"]] == [
        ("gesla", "published_source_set"), ("noaa", "published_source_identity")]
    assert body["identities"][0]["published_set_ids"] == ["fit"]
    assert body["identities"][1]["station_ids"] == ["OTC-1"]


def test_current_subordinate_retains_actual_noaa_alias_lineage(tmp_path):
    path, pin, raw = inventory_file(tmp_path)
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    path, pin, _raw = inventory_file(tmp_path, [{"source": "noaa-currents", "version": "20261008",
                                               "upstream_record_id": "ACT0091", "granularity": "native_source_set",
                                               "input_sha256": [digest]}])
    inventory = gb.load_source_inventory(path, pin)
    release = {"stations": [{"station_id": "OTC-current", "status": "active", "aliases": {"noaa": "ACT0091"},
                             "constant_sets": [], "current_offsets": [{"reference_station_id": "OTC-reference"}]}]}
    report_path = tmp_path / "BUILD_REPORT.json"
    gb.write_report(BuildReport(), {}, report_path, release=release, inventory=inventory)
    row = json.loads(report_path.read_text())["source_inventory"]["identities"][0]
    assert row["disposition"] == "published_source_identity"
    assert row["station_ids"] == ["OTC-current"]
    assert row["published_set_ids"] == []


def retained_record(source="dmi", version="capture:abc", rid="20047", disposition="metadata_only"):
    return {"source": source, "source_version": version, "source_record_id": rid,
            "role": "gauge_station", "disposition": disposition, "licence_id": "original-licence",
            "published_set_ids": [], "detail": "Original metadata retained; no fit or accuracy claim.",
            "provenance": {"input_sha256": ["a" * 64]}, "original_metadata": {"raw_country": "unresolved"}}


def inventory_report(tmp_path, records, identities=None, constant_sets=None):
    path, pin, _raw = inventory_file(tmp_path, identities)
    inventory = gb.load_source_inventory(path, pin)
    release = {"stations": [{"station_id": "OTC-retained", "constant_sets": constant_sets or [],
                             "source_records": records}]}
    return inventory, report_inventory(inventory, release, BuildReport())


@pytest.mark.parametrize("disposition", ["retained", "metadata_only", "licence_restricted", "not_fitted"])
def test_actual_retained_record_replaces_pending_label_without_claiming_a_fit(tmp_path, disposition):
    record = retained_record(disposition=disposition)
    inventory, body = inventory_report(tmp_path, [record])
    row = body["identities"][0]
    assert row["disposition"] == "retained_source_record"
    assert row["published_set_ids"] == []
    assert row["station_ids"] == ["OTC-retained"]
    lineage = row["source_record_lineage"]
    assert lineage == [{"station_id": "OTC-retained", **{k: v for k, v in record.items() if k != "original_metadata"}}]
    assert all(row[k] == v for k, v in inventory.document["identities"][0].items())
    assert body["source_record_summary"] == {"total": 1, "original": 1, "additional": 0,
                                             "counts_by_disposition": {disposition: 1},
                                             "count_unit": "source/version/upstream record identities; not distinct places or predictive sets"}


def test_added_records_are_separate_and_exact_versions_are_required(tmp_path):
    record = retained_record(version="other-capture")
    chs = retained_record(source="chs-iwls", version="actual-capture", rid="actual-api-id",
                          disposition="licence_restricted")
    _inventory, body = inventory_report(tmp_path, [record, chs])
    row = body["identities"][0]
    assert row["disposition"] == "acquired_observations_pending_publication"
    assert "source_record_lineage" not in row
    assert body["identity_count"] == 1
    assert [(r["source"], r["source_version"], r["source_record_id"], r["disposition"])
            for r in body["additional_source_records"]] == [
        ("chs-iwls", "actual-capture", "actual-api-id", "licence_restricted"),
        ("dmi", "other-capture", "20047", "metadata_only")]
    assert body["source_record_summary"]["additional"] == 2


def test_original_nonspatial_units_stay_comparison_inputs(tmp_path):
    path, pin, raw = inventory_file(tmp_path)
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    identities = [{"source": "noaa", "version": "capture", "upstream_record_id": "response.json",
                   "granularity": "reference_response_file", "input_sha256": [digest]}]
    inventory, body = inventory_report(tmp_path, [], identities)
    assert body["identities"][0]["disposition"] == "retained_comparison_input"
    assert all(body["identities"][0][k] == v for k, v in inventory.document["identities"][0].items())


def test_retained_record_keeps_existing_published_set_lineage(tmp_path):
    record = retained_record(disposition="retained")
    cs = {"source": "dmi", "source_version": "capture:abc", "source_record_id": "20047", "set_id": "set"}
    _inventory, body = inventory_report(tmp_path, [record], constant_sets=[cs])
    row = body["identities"][0]
    assert row["disposition"] == "published_source_set"
    assert row["published_set_ids"] == ["set"]
    assert row["source_record_lineage"][0]["disposition"] == "retained"
