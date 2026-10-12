"""Offline cache integrity, source freshness and country/position preservation."""
import copy
import hashlib
import json

import pytest

from otc_pipeline.country_resolutions import apply_country_resolutions, upstream_raw_identity


@pytest.fixture
def cache_case(tmp_path):
    metadata = {'raw_json': '{"name":"station","latitude":45.0,"longitude":-63.0}',
                'row_json': '{"generated_licence_projection":"not an upstream field"}'}
    record = {'source': 'chs-iwls', 'source_record_id': 'upstream-id', 'source_version': 'v1',
              'licence_id': 'restricted', 'published_set_ids': [], 'detail': 'Original metadata only.',
              'original_metadata': metadata, 'provenance': {'input_sha256': ['a' * 64]}}
    release = {'stations': [{'station_id': 'OTC-test', 'country': None, 'lat': 45.0, 'lon': -63.0,
                             'source_records': [record], 'constant_sets': []}]}
    evidence = tmp_path / 'evidence.json'
    evidence.write_text('{"ISO_TER1":"CAN","ISO_SOV1":"CAN"}')
    descriptor = {'path': evidence.name, 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest(),
                  'source_url': 'https://official.example.test/zone'}
    entry = {'source': record['source'], 'source_record_id': record['source_record_id'],
             'source_version': 'v1', 'upstream_raw_sha256': upstream_raw_identity(record)['sha256'],
             'upstream_raw_hash_fields': ['raw_json'], 'raw_values': {'country': None, 'latitude': 45.0, 'longitude': -63.0},
             'assembled_position': {'latitude': 45.0, 'longitude': -63.0},
             'lookup_position': {'latitude': 45.0, 'longitude': -63.0},
             'status': 'resolved', 'resolved_country': 'CAN', 'maritime_zone': {'ISO_TER1': 'CAN', 'ISO_SOV1': 'CAN'},
             'resolved_sovereign': 'CAN', 'corrected_position': None,
             'source_url': descriptor['source_url'], 'method': 'marine_zone_territory',
             'lookup_date': '2026-10-11', 'evidence': [descriptor],
             'lookup_attempts': [{'method': 'point-in-zone', 'result': 'supported territory CAN'}]}
    return tmp_path, release, entry, evidence


def apply_case(case, mutate=None):
    tmp_path, release, entry, evidence = case
    entry = copy.deepcopy(entry)
    if mutate is not None:
        mutate(entry)
    table = tmp_path / 'country-resolutions.json'
    table.write_text(json.dumps({'format_version': 1, 'entries': [entry]}))
    sha = hashlib.sha256(table.read_bytes()).hexdigest()
    return apply_country_resolutions(release, path=table, sha256=sha)


def test_raw_selector_excludes_generated_licence_wrapper(cache_case):
    record = cache_case[1]['stations'][0]['source_records'][0]
    identity = upstream_raw_identity(record)
    record['original_metadata']['row_json'] = '{"different_generated_licence":"ignored"}'
    assert upstream_raw_identity(record) == identity
    assert identity['fields'] == ['raw_json']


def test_country_projection_preserves_original_metadata_and_science(cache_case):
    before = copy.deepcopy(cache_case[1])
    result = apply_case(cache_case)
    station = cache_case[1]['stations'][0]
    assert station['country'] == 'CAN'
    assert station['constant_sets'] == before['stations'][0]['constant_sets']
    record = station['source_records'][0]
    original = before['stations'][0]['source_records'][0]
    assert record['provenance'] == original['provenance']
    assert all(record['original_metadata'][k] == v for k, v in original['original_metadata'].items())
    assert result['country_updates'] == 1


def test_changed_upstream_hash_requires_refresh_without_projection(cache_case):
    record = cache_case[1]['stations'][0]['source_records'][0]
    record['original_metadata']['raw_json'] = '{"name":"changed upstream","latitude":45.0,"longitude":-63.0}'
    result = apply_case(cache_case)
    assert cache_case[1]['stations'][0]['country'] is None
    assert result['refresh_required'][0]['reason'] == 'upstream_raw_changed'


def test_tampered_evidence_refuses_before_mutation(cache_case):
    before = copy.deepcopy(cache_case[1])
    cache_case[3].write_text('tampered actual response')
    with pytest.raises(ValueError, match='evidence SHA-256'):
        apply_case(cache_case)
    assert cache_case[1] == before


def test_unresolved_requires_documented_lookup_attempts(cache_case):
    def missing_attempt(entry):
        entry.update(status='unresolved', resolved_country=None, lookup_attempts=[])
    with pytest.raises(ValueError, match='lookup_attempts'):
        apply_case(cache_case, missing_attempt)


def test_established_country_is_retained_and_conflict_reported(cache_case):
    cache_case[1]['stations'][0]['country'] = 'USA'
    result = apply_case(cache_case)
    assert cache_case[1]['stations'][0]['country'] == 'USA'
    assert result['conflicts'][0]['reason'] == 'existing_country_conflict'


def test_null_territory_cannot_blindly_use_sovereign(cache_case):
    def blind_sovereign(entry):
        entry['maritime_zone']['ISO_TER1'] = None
    with pytest.raises(ValueError, match='official country code'):
        apply_case(cache_case, blind_sovereign)


def test_unlabelled_position_correction_refuses(cache_case):
    def correction(entry):
        entry['corrected_position'] = {'latitude': 45.1, 'longitude': -63.1}
    with pytest.raises(ValueError, match='position.*precision'):
        apply_case(cache_case, correction)
