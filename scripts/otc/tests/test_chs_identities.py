"""CHS source-domain separation and hash-bound, lossless metadata loading."""
import hashlib
import json

import pytest

from otc_pipeline.chs_identities import load_chs_identities


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")


def _inventory(root):
    entries = []
    for path in sorted(root.iterdir()):
        if path.name != "SHA256SUMS":
            entries.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    (root / "SHA256SUMS").write_text("".join(entries))


def _row(record_id, source_hash, latitude, longitude):
    return {"source_record_id": record_id, "source_sha256": source_hash, "name": "same code, distinct source",
            "latitude": latitude, "longitude": longitude, "country": None, "roles": ["wcslack"],
            "timezone_iana": None, "datums": [], "constants_acquired": False,
            "observations_acquired": False, "predictions_acquired": False,
            "raw": {"code": "00620", "unchanged_status": "UNKNOWN"}}


@pytest.fixture
def bundle(tmp_path):
    # Small contract fixture; the separate native proof uses all actual acquired CHS records.
    raw_bodies = {"open-data-csv": b"station metadata CSV\n", "iwls-stations": b"station API metadata\n",
                  "current-metadata-00620": b"current metadata\n"}
    requests = []
    for name, body in raw_bodies.items():
        (tmp_path / f"{name}.response").write_bytes(body)
        descriptor = {"method": "GET", "url": f"https://example.test/{name}", "headers": {}, "body": None}
        _write_json(tmp_path / f"{name}.request.json", descriptor)
        request_sha = hashlib.sha256(json.dumps(descriptor, separators=(",", ":")).encode()).hexdigest()
        provenance = {"name": name, "status": 200, "response_sha256": hashlib.sha256(body).hexdigest(),
                      "response_bytes": len(body), "request_sha256": request_sha, "url": descriptor["url"]}
        _write_json(tmp_path / f"{name}.provenance.json", provenance)
        requests.append(provenance)
    _write_json(tmp_path / "request-census.json", requests)
    hashes = {row["name"]: row["response_sha256"] for row in requests}
    csv = _row("620", hashes["open-data-csv"], 46.25, -60.283333)
    iwls = _row("actual-iwls-id", hashes["iwls-stations"], 46.281566, -60.424734)
    iwls["station_code"] = "00620"
    current = {**iwls, "metadata_evidence": {"path": str(tmp_path / "current-metadata-00620.response"),
                                           "sha256": hashes["current-metadata-00620"],
                                           "raw": {"datums": [], "timeZoneCode": "Canada/Atlantic"}}}
    for name, row in (("open-data-identities", csv), ("iwls-identities", iwls), ("current-identities", current)):
        _write_json(tmp_path / f"{name}.jsonl", row)
    _write_json(tmp_path / "census.json", {"open_data_rows": 1, "iwls_rows": 1,
                                          "same_number_different_positions": 1})
    _write_json(tmp_path / "metadata-census.json", {"current_station_identities": 1})
    _write_json(tmp_path / "source-number-comparison.json", [{"csv_number": "620", "iwls_id": "actual-iwls-id",
                                                             "position_differs": True}])
    _write_json(tmp_path / "licence-evidence.json", {
        "open_data_csv": {"publisher": "Fisheries and Oceans Canada", "licence_url": "https://example.test/ogl",
                          "attribution_as_published": "Published OGL attribution"},
        "iwls": {"terms_url": "https://example.test/iwls", "terms_fact": "No commercial or navigation use.",
                 "copyright_fact": "Crown rights retained.", "restriction_survival": "Restrictions survive."}})
    (tmp_path / "iwls-derivative-notice.raw.txt").write_text("Exact notice\n\twith original whitespace\n")
    _inventory(tmp_path)
    return tmp_path


def test_separate_rights_and_conflicting_identities_remain_lossless(bundle):
    result = load_chs_identities(bundle)
    by_source = {row["source"]: row for row in result.records}
    assert len(result.records) == 3
    assert (by_source["chs-dfo-ogl"]["source_record_id"], by_source["chs-iwls"]["source_record_id"]) == (
        "620", "actual-iwls-id")
    assert by_source["chs-dfo-ogl"]["lat"] == 46.25
    assert by_source["chs-iwls"]["lat"] == 46.281566
    assert by_source["chs-dfo-ogl"]["licence"]["commercial_use"] is True
    assert by_source["chs-iwls"]["licence"]["commercial_use"] is False
    current = by_source["chs-current-metadata"]
    assert current["licence"] == by_source["chs-iwls"]["licence"]
    assert current["licence"]["attribution"] == (bundle / "iwls-derivative-notice.raw.txt").read_text()
    raw = json.loads((bundle / "current-identities.jsonl").read_text())
    assert json.loads(current["original_metadata"]["row_json"]) == raw
    assert current["country"] is None
    assert "timezone" not in current and "datum" not in current and "constant_sets" not in current
    assert result.provenance["verified_acquisitions"] == 3


def test_changed_acquired_bytes_stop_loading_before_returning_rows(bundle):
    (bundle / "iwls-stations.response").write_bytes(b"changed acquired input")
    with pytest.raises(ValueError, match="SHA256"):
        load_chs_identities(bundle)


def test_declared_source_hash_must_name_an_acquired_response(bundle):
    path = bundle / "iwls-identities.jsonl"
    row = json.loads(path.read_text())
    row["source_sha256"] = "0" * 64
    _write_json(path, row)
    _inventory(bundle)
    with pytest.raises(ValueError, match="source response"):
        load_chs_identities(bundle)


def test_census_count_mismatch_is_not_silent_attrition(bundle):
    _write_json(bundle / "census.json", {"open_data_rows": 2, "iwls_rows": 1,
                                        "same_number_different_positions": 1})
    _inventory(bundle)
    with pytest.raises(ValueError, match="count"):
        load_chs_identities(bundle)
