"""Build the prediction vectors' NOAA subset offline; never publish station data."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from otc_pipeline import astro_tables as at
from otc_pipeline.adapter import FetchResult, InputFile
from otc_pipeline.adapters.noaa import NoaaAdapter
from otc_pipeline.adapters.noaa_currents import NoaaCurrentsAdapter
from otc_pipeline.constituent_speeds import TableSpeeds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from astro_entry import build_entry  # noqa: E402

TIDE_STATIONS = ("1820000", "8443838", "8443970", "8444351", "9413878", "9414290", "9447110", "9447130", "TPT2705")
CURRENT_BINS = {"ACT1591": (1,), "ACT1616": (1,), "BOS1101": (1, 15), "PUG1501": (1, 6)}
SYNTHETIC_ID = "OTC-V-SYNTHETIC-BOSTON"
SYNTHETIC_REASON = "synthetic vectors station: NOAA 8443970 constants plus made-up MA2 0.05 m and MB2 0.03 m; not data"
ADAPTER_COMMIT = "28d09ef2c1c1bb5b44732b6f09ddcb1d80d9b459"


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def load_offline(cache, source, wanted):
    """Reconstitute fetch results from verified cache bytes without using a fetcher."""
    cache = Path(cache)
    manifest = json.loads((cache / "MANIFEST.json").read_text())
    listed = {entry["path"]: entry for entry in manifest["files"]}
    files = {}

    def read(name):
        path = cache / name
        raw = path.read_bytes()
        entry = listed.get(name)
        if entry is None:
            # DT5a's datums were fetched after the original agency MANIFEST.
            # Their immutable cache receipts carry the response hash and URL.
            receipt = json.loads(Path(str(path) + ".meta.json").read_text())
            if receipt["status"] != 200:
                raise ValueError(f"{path}: cached response is not HTTP 200")
            entry = {"path": name, "sha256": receipt["sha256"], "size": receipt["size"],
                     "source_url": receipt["url"], "fetched_at": receipt["fetched_at"]}
        if hashlib.sha256(raw).hexdigest() != entry["sha256"] or len(raw) != entry["size"]:
            raise ValueError(f"{path}: cached SHA-256 or size differs from its source receipt")
        files[name] = InputFile(**entry)
        return json.loads(raw), entry["sha256"]

    listing, listing_sha = read("stations.json")
    items = {}
    for sid in wanted:
        matches = [row for row in listing["stations"] if row["id"] == sid]
        if not matches:
            raise ValueError(f"{source}: selected station {sid} is missing from the cache")
        if source == "noaa":
            station = matches[0]
            key = "harcon" if station["type"] == "R" else "offsets"
            data, data_sha = read(f"{key}/{sid}.json")
            datums, datum_sha = read(f"datums/{sid}.json")
            items[sid] = {"station": station, key: data, "datums": datums,
                          "input_sha256": [listing_sha, data_sha, datum_sha]}
        elif source == "noaa-currents":
            rows = [row for row in matches if row["currbin"] in CURRENT_BINS[sid]]
            if {row["currbin"] for row in rows} != set(CURRENT_BINS[sid]):
                raise ValueError(f"{sid}: a requested current bin is missing")
            item = {"rows": rows, "offsets": {}, "input_sha256": [listing_sha]}
            if any(row["type"] == "H" for row in rows):
                item["harcon"], sha = read(f"harcon/{sid}.json")
                item["input_sha256"].append(sha)
            for row in rows:
                number = row["currbin"]
                item["offsets"][number], sha = read(f"offsets/{sid}_{number}.json")
                item["input_sha256"].append(sha)
            items[sid] = item
        else:
            raise ValueError(f"not a vectors source: {source}")
    return FetchResult(source, manifest["source_version"], cache, tuple(files.values()), items)


def station_id(upstream):
    return f"OTC-V-NOAA-{upstream}"


def build_release(cache_dir, run_dir, schema_path):
    cache_dir, run_dir, schema_path = map(Path, (cache_dir, run_dir, schema_path))
    run = at.load_congen_run(run_dir)
    speeds = TableSpeeds.from_tcd_dump(run_dir / run.provenance["files"]["tcd_dump"]["path"])
    adapters = (NoaaAdapter(speeds=speeds), NoaaCurrentsAdapter(speeds=speeds))
    selected = (TIDE_STATIONS, tuple(CURRENT_BINS))
    stations, conventions, versions = [], [], {}
    for adapter, wanted in zip(adapters, selected):
        fetched = load_offline(cache_dir / adapter.source, adapter.source, wanted)
        sets = list(adapter.sets(fetched))
        emitted = {ss.station_hint["aliases"]["noaa"] for ss in sets}
        if emitted != set(wanted):
            raise ValueError(f"{adapter.source}: requested stations were skipped: {adapter.skipped(fetched)}")
        versions[adapter.source] = fetched.source_version
        convention = adapter.convention()
        convention.update(astro_table_id=at.TABLE_ID, tables_sha256=at.TCD_SHA256)
        conventions.append(convention)
        for ss in sets:
            hint = copy.deepcopy(ss.station_hint)
            upstream = hint["aliases"]["noaa"]
            sid = station_id(upstream)
            station = {"station_id": sid, "status": "active", **hint,
                       "constant_sets": [], "recommended_set_id": None}
            if ss.constant_set is not None:
                cs = copy.deepcopy(ss.constant_set)
                cs["set_id"] = f"{sid}/{adapter.source}"
                cs["provenance"].update(tcd_sha256=at.TCD_SHA256, build={"build_commit": ADAPTER_COMMIT})
                station["constant_sets"] = [cs]
                station["recommended_set_id"] = cs["set_id"]
            if ss.subordinate_offsets is not None:
                offsets = copy.deepcopy(ss.subordinate_offsets)
                if "current_offsets" in offsets:
                    for offset in offsets["current_offsets"]:
                        offset["reference_station_id"] = station_id(offset.pop("reference_source_record_id"))
                    station.update(offsets)
                else:
                    offsets["reference_station_id"] = station_id(offsets.pop("reference_source_record_id"))
                    station["subordinate_offsets"] = offsets
            stations.append(station)
    synthetic = copy.deepcopy(next(s for s in stations if s["aliases"]["noaa"] == "8443970"))
    synthetic.update(station_id=SYNTHETIC_ID, aliases={}, name="SYNTHETIC: Boston 8443970 + MA2/MB2 (vectors only)")
    cs = synthetic["constant_sets"][0]
    cs["set_id"] = f"{SYNTHETIC_ID}/noaa"
    cs["source_record_id"] = "synthetic-boston-ma2-mb2"
    cs["provenance"]["selection_reason"] = SYNTHETIC_REASON
    cs["constituents"] = [{"name": name, "amplitude_m": amp, "phase_deg": 0.0,
                           "speed_deg_per_hour": speeds.speed(name)} for name, amp in (("MA2", 0.05), ("MB2", 0.03))] + cs["constituents"]
    synthetic["recommended_set_id"] = cs["set_id"]
    stations.append(synthetic)
    names = {c["name"] for s in stations for cs in s["constant_sets"]
             for c in cs["constituents"] + [c for b in cs.get("current_bins", []) for c in b["constituents"]]}
    entry = build_entry(run_dir, names, at.TABLE_ID, at.TCD_SHA256)
    table = [{"constituent_table_version": conv["constituent_table_version"], "name": name,
              "speed_deg_per_hour": entry["constituents"][name]["speed_deg_per_hour"]}
             for conv in conventions for name in sorted(names)]
    document = {"format_version": "1.0", "release": {"datestamp": "20261011", "created": "2026-10-11T00:00:00Z",
                 "source_versions": versions, "min_reader_version": "1.0", "fit_min_good_hours": 360, "concept_doi": None},
                "conventions": conventions, "licences": [adapters[0].licence()], "constituents": table,
                "astro_tables": [entry], "stations": sorted(stations, key=lambda s: s["station_id"])}
    ids = {s["station_id"] for s in stations}
    for station in stations:
        offsets = ([station["subordinate_offsets"]] if "subordinate_offsets" in station else station.get("current_offsets", []))
        if any(o["reference_station_id"] not in ids for o in offsets):
            raise ValueError(f"{station['station_id']}: reference omitted from the vectors subset")
    schema = json.loads(schema_path.read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
    verify_entry(document, run_dir)
    return document


def verify_entry(document, run_dir):
    run = at.load_congen_run(Path(run_dir))
    if len(document["astro_tables"]) != 1:
        raise ValueError("vectors have exactly one own-TCD astro table")
    entry = document["astro_tables"][0]
    errors = at.release_table_errors(entry, *run)
    if entry["astro_table_id"] != at.TABLE_ID:
        errors.append("astro_table_id is not the pinned OTC table id")
    if errors:
        raise ValueError("entry differs from own TCD: " + "; ".join(errors[:10]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-entry", type=Path)
    args = parser.parse_args()
    try:
        if args.verify_entry:
            verify_entry(json.loads(args.verify_entry.read_text()), args.run_dir)
            print("PASS: own-TCD entry, name mappings and file SHA-256")
        else:
            if not all((args.cache_dir, args.schema, args.output)):
                parser.error("building requires --cache-dir, --schema and --output")
            doc = build_release(args.cache_dir, args.run_dir, args.schema)
            args.output.write_bytes(encode(doc))
            print(f"PASS: {len(doc['stations'])} vectors stations, schema valid, own-TCD verified")
    except (ValueError, OSError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
