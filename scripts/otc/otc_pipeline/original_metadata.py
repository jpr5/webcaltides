"""Lossless metadata for original acquired supplemental spatial units.

The caller supplies an already verified SourceInventory. Only metadata files read
here are rechecked; observation bytes retain their accepted inventory bindings.
Captured output rows are summaries, not freshly computed GaugeRecords/SourceSets.
JMA's original native sets are reconstructed with its unchanged cached parsers.
No network, fitting, ID assignment or publication takes place here.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path

from otc_pipeline.adapter import FetchResult, InputFile
from otc_pipeline.adapters.dmi import DmiAdapter
from otc_pipeline.adapters.fmi import FmiAdapter
from otc_pipeline.adapters.jma import JmaAdapter, Speeds, parse_harms60, parse_station_table
from otc_pipeline.adapters.mi import MarineInstituteAdapter
from otc_pipeline.adapters.rws import RwsAdapter
from otc_pipeline.adapters.shom_refmar import ShomRefmarAdapter
from otc_pipeline.adapters.smhi import SmhiAdapter
from otc_pipeline.adapters.uhslc import UhslcFdAdapter
from otc_pipeline.source_inventory import SourceInventory
from otc_pipeline.sources import SOURCES


SPATIAL_ROLES = frozenset({'native_source_set', 'gauge_station', 'gauge_record',
                          'actual_observation_acquisition_record', 'raw_station_metadata',
                          'raw_station_bin_metadata', 'comparator_station_metadata'})
EXCLUDED_SOURCES = frozenset({'gesla', 'ticon', 'chs'})


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _read(path: Path, descriptor: dict) -> bytes:
    raw = path.read_bytes()
    if len(raw) != descriptor['size'] or hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
        raise ValueError(f'original metadata input integrity mismatch: {path}')
    return raw


def _licences() -> dict:
    classes = {**SOURCES, 'dmi': DmiAdapter, 'fmi': FmiAdapter, 'marine-institute': MarineInstituteAdapter,
               'rws': RwsAdapter, 'shom-refmar': ShomRefmarAdapter, 'smhi': SmhiAdapter,
               'uhslc-fd': UhslcFdAdapter}
    return {source: cls().licence() for source, cls in classes.items() if hasattr(cls, 'licence')}


def _jma(path: Path, manifest: dict, tcd_path: Path) -> dict:
    files = tuple(InputFile(**row) for row in manifest['files'])
    tables = [f for f in files if Path(f.path).name.startswith('station')]
    if len(tables) != 1:
        raise ValueError('original JMA manifest requires one station table')
    table = tables[0]
    stations = parse_station_table(_read(path.parent / table.path, table.as_dict()))
    by_path = {f.path: f for f in files}
    items = {}
    for code, station in stations.items():
        item = {'station': station, 'harms60': None, 'input_sha256': [table.sha256]}
        if station['harms60_href']:
            matches = [f for name, f in by_path.items() if name.startswith('harms60/')
                       and Path(name).stem == code]
            if len(matches) != 1:
                raise ValueError(f'original JMA manifest requires one harms60 page for {code}')
            child = matches[0]
            harmonics = parse_harms60(_read(path.parent / child.path, child.as_dict()), code)
            if harmonics['name'] != station['name']:
                raise ValueError(f'original JMA station/harms60 name mismatch: {code}')
            main = {c['name']: c for c in harmonics['constituents']}
            for name, value in station['main4'].items():
                if any(main[name][k] != value[k] for k in ('amplitude_cm', 'phase_deg')):
                    raise ValueError(f'original JMA station/harms60 value mismatch: {code}/{name}')
            item['harms60'] = harmonics
            item['input_sha256'].append(child.sha256)
        items[code] = item
    fetched = FetchResult(source='jma', source_version=min(f.fetched_at for f in files)[:10].replace('-', ''),
                          cache_dir=path.parent, files=files, items=items)
    adapter = JmaAdapter(speeds=Speeds.from_tcd_dump(tcd_path))
    output = {}
    for ss in adapter.comparator_sets(fetched):
        if ss.constant_set is None:
            raise ValueError('original JMA native adapter returned no constants')
        code = ss.constant_set['source_record_id']
        output[code] = ss, items[code]
    return output


def load_original_metadata(inventory: SourceInventory, *, tcd_path: Path, datestamp: str) -> dict:
    """Return every supplemental spatial key, including explicit unresolved metadata.

    Multiple captured rows and inventory versions remain ordered JSON arrays in
    scalar original_metadata fields. Conflicting values are never last-write-wins.
    Observation manifests are retained without reading/reprocessing observations.
    """
    grouped = defaultdict(list)
    for row in inventory.document['identities']:
        if row['granularity'] in SPATIAL_ROLES and row['source'] not in EXCLUDED_SOURCES:
            grouped[row['source'], row['upstream_record_id']].append(row)
    captured, raw_rows, input_bindings = defaultdict(list), defaultdict(list), defaultdict(list)
    jma_native = {}
    jma_inputs = []
    tcd_dump_sha256 = None
    pegel_stations, pegel_days = {}, defaultdict(list)
    seen_jma, seen_pegel = set(), set()
    descriptors = sorted(inventory.document['input_files'], key=lambda d: d['path'])
    for descriptor in descriptors:
        path = Path(descriptor['path'])
        source = path.parent.name
        if path.name.startswith(('gauge_', 'otc_')) and path.suffix == '.json':
            doc = json.loads(_read(path, descriptor))
            if not isinstance(doc, dict) or 'rows' not in doc or 'source' not in doc:
                continue
            source = doc['source']
            for row in doc['rows']:
                rid = str(row.get('id', '')).removeprefix(source + ':')
                if rid in ('None', ''):
                    rid = str(row.get('aliases', {}).get('noaa', '')) if source == 'noaa-currents' else ''
                key = source, rid
                if key in grouped:
                    captured[key].append(row)
                    input_bindings[key].append(descriptor)
        elif path.name == 'stations.json' and source in ('noaa', 'noaa-currents'):
            doc = json.loads(_read(path, descriptor))
            for row in doc['stations']:
                parent = str(row['id'])
                keys = [(source, parent)]
                if source == 'noaa-currents' and row.get('currbin') is not None:
                    keys.append((source, f"{parent}#bin:{row['currbin']}"))
                for key in keys:
                    if key in grouped:
                        raw_rows[key].append(row)
                        input_bindings[key].append(descriptor)
        elif source == 'bom' and path.name == 'sites.json':
            doc = json.loads(_read(path, descriptor))
            for row in doc['features']:
                key = 'bom', str(row['properties']['AAC'])
                if key in grouped:
                    raw_rows[key].append(row)
                    input_bindings[key].append(descriptor)
        elif path.name == 'MANIFEST.json' and source == 'jma' and 'jma' in {s for s, _ in grouped}:
            if descriptor['sha256'] not in seen_jma:
                manifest = json.loads(_read(path, descriptor))
                jma_native.update(_jma(path, manifest, tcd_path))
                tcd_dump_sha256 = hashlib.sha256(Path(tcd_path).read_bytes()).hexdigest()
                jma_inputs.append({'manifest': descriptor, 'files': manifest['files']})
                seen_jma.add(descriptor['sha256'])
        elif path.name == 'MANIFEST.json' and 'pegelonline' in path.parts:
            if descriptor['sha256'] in seen_pegel:
                continue
            manifest = json.loads(_read(path, descriptor))
            seen_pegel.add(descriptor['sha256'])
            if path.parent.name == 'stations':
                # Preserve every acquired snapshot, not only the newest filename.
                for child in manifest['files']:
                    doc = json.loads(_read(path.parent / child['path'], child))
                    for station in doc['stations']:
                        pegel_stations.setdefault(station['uuid'], []).append(
                            {'station': station, 'input': child, 'manifest': descriptor})
            elif 'day_utc' in manifest:
                day = manifest['day_utc']
                for child in manifest['files']:
                    rid = Path(child['path']).name.removesuffix('.csv.gz')
                    key = 'pegelonline', rid
                    if key in grouped:
                        pegel_days[key].append({**child, 'path': f"pegelonline/{day.replace('-', '/')}/{child['path']}",
                                                'day_utc': day, 'manifest': descriptor})
    licences = _licences()
    result = {}
    for key, identities in sorted(grouped.items()):
        source, rid = key
        rows, originals = captured[key], raw_rows[key]
        metadata = {'inventory_rows_json': _json(identities), 'captured_rows_json': _json(rows),
                    'raw_metadata_rows_json': _json(originals),
                    'metadata_inputs_json': _json(input_bindings[key]),
                    'inventory_sha256': inventory.provenance.get('sha256'), 'load_datestamp': datestamp}
        notes = ['Original acquired metadata; captured output rows are summaries, not recomputed observations.']
        candidates = []
        for row in rows:
            candidates.append((row.get('name'), row.get('lat'), row.get('lon'), row.get('country')))
        if not candidates:
            for row in originals:
                if source == 'bom':
                    p, xy = row['properties'], row['geometry']['coordinates']
                    candidates.append((p.get('PORT_NAME'), xy[1], xy[0], None))
                else:
                    candidates.append((row.get('name'), row.get('lat'), row.get('lng'), None))
        licence = licences.get(source)
        metadata['accepted_licence_json'] = _json(licence)
        if licence is not None and type(licence.get('commercial_use')) is not bool:
            notes.append('Accepted source rights lack the modern explicit commercial-use field; no licence grant projected.')
            licence = None
        extra = {}
        if source == 'jma' and rid in jma_native:
            ss, parsed = jma_native[rid]
            hint = ss.station_hint
            candidates = [(hint['name'], hint['lat'], hint['lon'], hint['country'])]
            metadata['native_station_hint_json'] = _json(hint)
            metadata['native_parsed_input_json'] = _json(parsed)
            metadata['native_input_manifests_json'] = _json(jma_inputs)
            metadata['native_tcd_dump_sha256'] = tcd_dump_sha256
            extra['original_constant_set_json'] = _json(ss.constant_set)
            if ss.constant_set['provenance']['redistribution'] == 'comparator_only':
                licence = None
                notes.append('Original JMA comparator-only third-party constants; no redistribution permission projected.')
            else:
                notes.append('Original JMA agency constants reconstructed from hash-bound cached inputs using the native adapter.')
        if source == 'pegelonline':
            snapshots = pegel_stations.get(rid, [])
            metadata['station_snapshots_json'] = _json(snapshots)
            days = sorted(pegel_days[key], key=lambda row: (row['day_utc'], row['path']))
            metadata['ordered_daily_inputs_json'] = _json(days)
            binding = [[d['path'], d['sha256']] for d in days]
            metadata['original_member_sha256'] = hashlib.sha256(_json(binding).encode()).hexdigest()
            if snapshots:
                names = {s['station'].get('longname') or s['station'].get('shortname') for s in snapshots}
                if len(names) == 1:
                    candidates = [(next(iter(names)), c[1], c[2], c[3]) for c in candidates]
            notes.append('Original captured GaugeRecord statistics and ordered raw-day/member binding retained; no series refit or reread.')
        unique = set(candidates)
        if len(unique) == 1:
            name, lat, lon, country = next(iter(unique))
        else:
            name, lat, lon, country = None, None, None, None
            notes.append('Explicit metadata gap: conflicting captured/raw place fields.' if unique
                         else 'Explicit metadata gap: missing captured/raw place fields.')
        if name is None or lat is None or lon is None:
            notes.append('Explicit metadata gap: name or coordinates unresolved; original rows retained.')
        if licence is None:
            notes.append('No compatible accepted licence row is projected; original rights remain in retained input metadata.')
        result[key] = {'name': name, 'lat': lat, 'lon': lon, 'country': country,
                       'original_metadata': metadata, 'licence': licence, 'detail': ' '.join(notes), **extra}
    return result
