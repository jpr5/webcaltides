"""Original source inventory accounting; publication and quality selection are separate."""
import hashlib
import json
from pathlib import Path

import pytest

from otc_pipeline import gesla_build as gb
from otc_pipeline.gesla_release import BuildReport


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
