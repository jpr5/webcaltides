"""Lossless metadata projection; the full pinned-input proof lives with the handoff."""
import hashlib
import json
from pathlib import Path

import pytest

from otc_pipeline.original_metadata import load_original_metadata
from otc_pipeline.source_inventory import SourceInventory


def inventory(tmp_path, payloads, identities):
    descriptors = []
    for name, doc in payloads.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(doc).encode()
        path.write_bytes(raw)
        descriptors.append({'path': str(path), 'size': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest()})
    return SourceInventory({'input_files': descriptors, 'identities': identities}, {})


def identity(rid, version='v1'):
    return {'source': 'dmi', 'version': version, 'upstream_record_id': rid,
            'granularity': 'gauge_station', 'input_sha256': ['a' * 64]}


def test_duplicate_captures_and_windows_are_retained(tmp_path):
    rows = [{'id': 'dmi:42', 'name': 'first', 'lat': 55, 'lon': 10, 'country': 'DNK'},
            {'id': 'dmi:42', 'name': 'second', 'lat': 55.1, 'lon': 10.1, 'country': 'DNK'}]
    inv = inventory(tmp_path, {'gauge_dmi.json': {'source': 'dmi', 'rows': rows}},
                    [identity('42'), identity('42', 'v2')])
    result = load_original_metadata(inv, tcd_path=Path('unused'), datestamp='20261011')
    row = result['dmi', '42']
    assert json.loads(row['original_metadata']['captured_rows_json']) == rows
    assert json.loads(row['original_metadata']['inventory_rows_json']) == inv.document['identities']
    assert row['name'] is None
    assert 'conflicting' in row['detail']


def test_missing_metadata_is_explicit_and_nonspatial_units_are_excluded(tmp_path):
    inv = inventory(tmp_path, {}, [identity('missing'),
                    {**identity('artifact'), 'granularity': 'reference_response_file'}])
    row = load_original_metadata(inv, tcd_path=Path('unused'), datestamp='20261011')['dmi', 'missing']
    assert row['lat'] is None and row['name'] is None
    assert 'missing' in row['detail']
    assert json.loads(row['original_metadata']['inventory_rows_json']) == [identity('missing')]


def test_changed_consumed_capture_is_refused(tmp_path):
    inv = inventory(tmp_path, {'gauge_dmi.json': {'source': 'dmi', 'rows': []}}, [identity('42')])
    (tmp_path / 'gauge_dmi.json').write_text('{}')
    with pytest.raises(ValueError, match='integrity mismatch'):
        load_original_metadata(inv, tcd_path=Path('unused'), datestamp='20261011')
