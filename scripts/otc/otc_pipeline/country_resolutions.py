"""Apply explicitly refreshed, evidence-pinned country/position interpretations offline.

The cache never supplies constants, rights or a provider-country default. Actual
upstream response rows (or labelled historical native captures) remain unchanged.
New or changed raw keys require an explicit refresh, never a build-time request.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re


RAW_FIELDS = ('raw_json', 'raw_metadata_rows_json', 'original_row_json', 'captured_rows_json')
SHA = re.compile(r'[0-9a-f]{64}')
COUNTRY = re.compile(r'[A-Z]{3}')
NON_COUNTRY_CODES = frozenset({'UNK', 'XXX', 'ZZZ'})


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def upstream_raw_identity(record: dict) -> dict:
    """Stable identity excludes our dates, licences and OTC interpretation fields."""
    metadata = record.get('original_metadata', {})
    for field in RAW_FIELDS:
        raw = metadata.get(field)
        if not isinstance(raw, str):
            continue
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f'invalid upstream JSON field: {field}') from exc
        if isinstance(value, (dict, list)) and value:
            return {'sha256': hashlib.sha256(_json({field: raw}).encode()).hexdigest(),
                    'fields': [field], 'role': ('historical_native_capture_row' if field == 'captured_rows_json'
                                              else 'upstream_raw_response_row')}
    raise ValueError('no nonempty hash-bound upstream payload')


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _point(value: dict, context: str) -> tuple:
    lat, lon = value.get('latitude'), value.get('longitude')
    if (not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))
            or isinstance(lat, bool) or isinstance(lon, bool)):
        raise ValueError(f'{context}: invalid coordinate basis')
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90:
        raise ValueError(f'{context}: invalid coordinate basis')
    return lat, lon


def _same_point(a, b) -> bool:
    return a == b


def apply_country_resolutions(release: dict, *, path: Path, sha256: str) -> dict:
    """Verify cache/evidence before projecting country, labelled position and provenance.

    Failed lookups retain null only with documented attempts. Missing/stale keys
    and coordinate-basis mismatches are returned as explicit refresh requirements.
    Existing country values survive conflicting cache interpretations. The caller
    owns timezone display updates for returned corrected_positions.
    """
    path = Path(path).resolve()
    if not SHA.fullmatch(sha256) or _hash(path) != sha256:
        raise ValueError('country resolution cache SHA-256 mismatch')
    table = json.loads(path.read_bytes())
    if table.get('format_version') != 1 or not isinstance(table.get('entries'), list):
        raise ValueError('unsupported country resolution cache format')
    verified_files = {}

    def verify_evidence(descriptor: dict) -> None:
        name, digest = descriptor.get('path', descriptor.get('evidence_file')), descriptor.get('sha256')
        if (not isinstance(name, str) or Path(name).is_absolute() or not isinstance(digest, str)
                or not SHA.fullmatch(digest)):
            raise ValueError('invalid country resolution evidence descriptor')
        evidence = (path.parent / name).resolve()
        if not evidence.is_relative_to(path.parent):
            raise ValueError('country resolution evidence must be table-relative')
        if evidence in verified_files and verified_files[evidence] != digest:
            raise ValueError('conflicting country resolution evidence SHA-256')
        if evidence not in verified_files:
            if not evidence.is_file() or _hash(evidence) != digest:
                raise ValueError(f'country resolution evidence SHA-256 mismatch: {name}')
            verified_files[evidence] = digest

    def verify_all_evidence(value) -> None:
        if isinstance(value, dict):
            if ('path' in value or 'evidence_file' in value) and 'sha256' in value:
                verify_evidence(value)
            for child in value.values():
                verify_all_evidence(child)
        elif isinstance(value, list):
            for child in value:
                verify_all_evidence(child)

    # Includes attempt logs and correction evidence, with each body hashed once.
    verify_all_evidence(table)

    dataset = table.get('maritime_dataset')
    if dataset is not None:
        verify_evidence(dataset)
        for field in ('attribute_evidence', 'capabilities_evidence', 'version_evidence'):
            if dataset.get(field) is not None:
                verify_evidence(dataset[field])
    if table.get('manifest_evidence') is not None:
        verify_evidence(table['manifest_evidence'])
        if table.get('manifest_sha256') != table['manifest_evidence']['sha256']:
            raise ValueError('source manifest evidence SHA-256 differs from cache basis')
    cache, pairs = {}, set()
    for entry in table['entries']:
        key = entry.get('source'), entry.get('source_record_id'), entry.get('upstream_raw_sha256')
        if (any(not isinstance(v, str) or not v for v in key) or not SHA.fullmatch(key[2])
                or key in cache):
            raise ValueError('invalid or duplicate source/raw-hash resolution key')
        status = entry.get('status')
        if status not in ('resolved', 'unresolved'):
            raise ValueError(f'{key}: invalid resolution status')
        if not isinstance(entry.get('lookup_attempts'), list) or not entry['lookup_attempts']:
            raise ValueError(f'{key}: actual lookup_attempts are required')
        if any(not isinstance(attempt, dict) or not attempt for attempt in entry['lookup_attempts']):
            raise ValueError(f'{key}: invalid lookup_attempts')
        if any(not isinstance(entry.get(field), str) or not entry[field]
               for field in ('source_url', 'method', 'lookup_date')):
            raise ValueError(f'{key}: source_url/method/lookup_date are required')
        evidence = entry.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f'{key}: actual lookup evidence is required')
        for descriptor in evidence:
            verify_evidence(descriptor)
        chains = {}
        for field in ('code_registry_evidence', 'station_registry_evidence', 'official_source_evidence'):
            chain = entry.get(field, [])
            if not isinstance(chain, list):
                raise ValueError(f'{key}: invalid {field}')
            for descriptor in chain:
                verify_evidence(descriptor)
            chains[field] = chain
        country = entry.get('resolved_country')
        if status == 'resolved':
            if not isinstance(country, str) or not COUNTRY.fullmatch(country) or country in NON_COUNTRY_CODES:
                raise ValueError(f'{key}: supported ISO alpha-3 country required')
            zone = entry.get('maritime_zone') or {}
            candidates = entry.get('zone_candidates') or [zone]
            attributes = [candidate.get('properties', candidate) for candidate in candidates if isinstance(candidate, dict)]
            territories = {value for candidate in attributes
                           for name, value in candidate.items() if re.fullmatch(r'iso_ter[0-9]+', name.lower())
                           and isinstance(value, str) and value}
            method = entry['method']
            if method == 'marine_zone_plus_station_registry_disambiguation':
                if (country not in territories or not chains['station_registry_evidence']
                        or not chains['code_registry_evidence']):
                    raise ValueError(f'{key}: territory disambiguation requires station and official country code evidence')
            elif method == 'official_source_plus_country_code_registry':
                if not chains['official_source_evidence'] or not chains['code_registry_evidence']:
                    raise ValueError(f'{key}: official source and official country code evidence required')
            elif territories:
                if territories != {country}:
                    raise ValueError(f'{key}: resolved country differs from unique explicit territory code')
            elif method != 'marine_zone_plus_official_country_code_crosswalk' or not chains['code_registry_evidence']:
                raise ValueError(f'{key}: null territory requires verified official country code evidence')
        elif country is not None:
            raise ValueError(f'{key}: unresolved lookup must not supply a country')
        raw_values = entry.get('raw_values')
        if not isinstance(raw_values, dict):
            raise ValueError(f'{key}: raw coordinate basis required')
        # Publisher values remain strings/nulls where actually supplied that way.
        assembled = _point(entry.get('assembled_position', raw_values), str(key))
        lookup = _point(entry.get('lookup_position', entry.get('assembled_position', raw_values)), str(key))
        correction = entry.get('corrected_position')
        if correction is not None:
            for descriptor in correction.get('evidence', []):
                verify_evidence(descriptor)
            lat, lon = _point(correction, str(key))
            if not -180 <= lon <= 180 or not correction.get('precision') or not correction.get('detail'):
                raise ValueError(f'{key}: corrected position requires precision and detail')
            if status != 'resolved':
                raise ValueError(f'{key}: unresolved lookup cannot correct position')
            if not _same_point(lookup, (lat, lon)):
                raise ValueError(f'{key}: lookup position differs from labelled corrected position')
        elif not _same_point(lookup, assembled):
            raise ValueError(f'{key}: lookup position differs from assembled coordinate basis')
        cache[key] = entry
        pairs.add(key[:2])

    report = {'path': str(path), 'sha256': sha256, 'verified_evidence_files': len(verified_files),
              'cache_entries': len(cache), 'matched_units': 0, 'country_updates': 0,
              'corrected_positions': [], 'unresolved': [], 'refresh_required': [], 'conflicts': []}
    plans = []
    for station in release['stations']:
        matches = []
        for record in station.get('source_records', []):
            source, rid = record['source'], record['source_record_id']
            request = {'station_id': station['station_id'], 'source': source, 'source_record_id': rid}
            relevant = (source, rid) in pairs or not station.get('country') or 'position_unreliable' in record.get('detail', '')
            if not relevant:
                continue
            try:
                identity = upstream_raw_identity(record)
            except ValueError as exc:
                report['refresh_required'].append({**request, 'reason': str(exc)})
                continue
            key = source, rid, identity['sha256']
            entry = cache.get(key)
            if entry is None:
                reason = 'upstream_raw_changed' if key[:2] in pairs else 'missing_country_resolution'
                report['refresh_required'].append({**request, 'upstream_raw_sha256': key[2], 'reason': reason})
                continue
            if entry.get('upstream_raw_hash_fields') != identity['fields']:
                report['refresh_required'].append({**request, 'reason': 'upstream_raw_hash_fields_changed'})
                continue
            expected = _point(entry.get('assembled_position') or entry['raw_values'], str(key))
            actual = station.get('lat'), station.get('lon')
            valid_basis = _same_point(actual, expected)
            correction = entry.get('corrected_position')
            if not valid_basis and correction is not None:
                valid_basis = _same_point(actual, _point(correction, str(key)))
            if not valid_basis:
                report['refresh_required'].append({**request, 'reason': 'coordinate_basis_changed'})
                continue
            matches.append((record, key, entry))
            report['matched_units'] += 1
            if entry['status'] == 'unresolved':
                report['unresolved'].append({**request, 'upstream_raw_sha256': key[2],
                                             'lookup_attempts': entry['lookup_attempts']})
        countries = {entry['resolved_country'] for _, _, entry in matches if entry['status'] == 'resolved'}
        country = next(iter(countries)) if len(countries) == 1 else None
        if len(countries) > 1:
            report['conflicts'].append({'station_id': station['station_id'], 'reason': 'source_country_conflict',
                                        'countries': sorted(countries)})
        if country and station.get('country') and station['country'] != country:
            report['conflicts'].append({'station_id': station['station_id'], 'reason': 'existing_country_conflict',
                                        'existing': station['country'], 'cached': country})
            country = None
        positions = {(e['corrected_position']['latitude'], e['corrected_position']['longitude'])
                     for _, _, e in matches if e.get('corrected_position') is not None}
        position = next(iter(positions)) if len(positions) == 1 else None
        if len(positions) > 1:
            report['conflicts'].append({'station_id': station['station_id'], 'reason': 'source_position_conflict'})
        plans.append((station, matches, country, position))

    for station, matches, country, position in plans:
        if country and not station.get('country'):
            station['country'] = country
            report['country_updates'] += 1
        if position is not None and (station.get('lat'), station.get('lon')) != position:
            correction_entry = next(e for _, _, e in matches if e.get('corrected_position') is not None)
            report['corrected_positions'].append({
                'station_id': station['station_id'], 'original_lat': station.get('lat'), 'original_lon': station.get('lon'),
                'lat': position[0], 'lon': position[1], 'precision': correction_entry['corrected_position']['precision'],
                'detail': correction_entry['corrected_position']['detail'], 'source': correction_entry['source'],
                'source_record_id': correction_entry['source_record_id'],
                'upstream_raw_sha256': correction_entry['upstream_raw_sha256'], 'evidence': correction_entry['evidence']})
            station['lat'], station['lon'] = position
        for record, key, entry in matches:
            interpretation = {'table_sha256': sha256, 'resolution': entry,
                              'observed_source_version': record['source_version'],
                              'observed_input_provenance': record.get('provenance', {})}
            record.setdefault('original_metadata', {})['OTC_country_resolution_json'] = _json(interpretation)
            note = f" Country interpretation: {entry['status']} from pinned cache {sha256} ({entry['method']})."
            correction = entry.get('corrected_position')
            if correction is not None and (station.get('lat'), station.get('lon')) == _point(correction, str(key)):
                note += ' Cached position interpretation supersedes the earlier raw-source position label; '
                note += str(entry['corrected_position']['detail']) + ' Predictive admission and original values are unchanged.'
            if note not in record.get('detail', ''):
                record['detail'] = record.get('detail', '') + note
    return report
