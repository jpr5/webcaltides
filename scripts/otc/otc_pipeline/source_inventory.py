"""Pinned original-input accounting, independent of scientific publication selection.

An inventory describes acquired source units, not distinct places. It never supplies
constants or station mappings: published lineage is projected from the actual release.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class SourceInventory:
    document: dict
    provenance: dict

    def verify(self) -> None:
        """Refuse changed inventory or acquired inputs, including changes during a build."""
        if hashlib.sha256(Path(self.provenance["path"]).read_bytes()).hexdigest() != self.provenance["sha256"]:
            raise ValueError("source inventory changed during the build")
        for entry in self.document["input_files"]:
            path = Path(entry["path"])
            digest = hashlib.sha256()
            try:
                with path.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
                size = path.stat().st_size
            except OSError as exc:
                raise ValueError(f"inventory input unavailable: {path}: {exc}") from exc
            if digest.hexdigest() != entry["sha256"] or size != entry["size"]:
                raise ValueError(f"inventory input integrity mismatch: {path}")


def load_source_inventory(path: Path, sha256: str) -> SourceInventory:
    """Load an explicitly pinned census and verify every referenced acquired input."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError(f"source inventory SHA-256 mismatch: {path}")
    doc = json.loads(raw)
    if not isinstance(doc, dict) or doc.get("inventory_version") != 1:
        raise ValueError("unsupported source inventory version")
    if not isinstance(doc.get("input_files"), list) or not doc["input_files"]:
        raise ValueError("source inventory requires acquired input files")
    if not isinstance(doc.get("identities"), list) or not doc["identities"]:
        raise ValueError("source inventory requires source identities")
    if not isinstance(doc.get("limitations"), list):
        raise ValueError("source inventory requires explicit limitations")
    hashes, paths = set(), set()
    for entry in doc["input_files"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or not Path(entry["path"]).is_absolute()
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or type(entry.get("size")) is not int or entry["size"] < 0):
            raise ValueError("invalid acquired input descriptor")
        if entry["path"] in paths:
            raise ValueError(f"duplicate acquired input path: {entry['path']}")
        paths.add(entry["path"])
        hashes.add(entry["sha256"])
    keys = set()
    for row in doc["identities"]:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k]
                                           for k in ("source", "version", "upstream_record_id", "granularity")):
            raise ValueError("invalid source/version/record identity")
        key = (row["source"], row["version"], row["upstream_record_id"])
        if key in keys:
            raise ValueError(f"duplicate source identity: {key}")
        keys.add(key)
        refs = row.get("input_sha256")
        if not isinstance(refs, list) or not refs or any(not isinstance(h, str) or h not in hashes for h in refs):
            raise ValueError(f"source identity references an unknown acquired input: {key}")
    inventory = SourceInventory(doc, {"path": str(path), "sha256": sha256,
                                      "input_files": doc["input_files"]})
    inventory.verify()
    return inventory


def inventory_arguments(parser) -> None:
    parser.add_argument("--source-inventory", type=Path, help="pinned original-input identity census (report only)")
    parser.add_argument("--source-inventory-sha256", help="required SHA-256 of --source-inventory")


def inventory_from_arguments(args) -> SourceInventory | None:
    if (args.source_inventory is None) != (args.source_inventory_sha256 is None):
        raise ValueError("--source-inventory and --source-inventory-sha256 must be supplied together")
    return (load_source_inventory(args.source_inventory, args.source_inventory_sha256)
            if args.source_inventory is not None else None)


def report_inventory(inventory: SourceInventory, release: dict, report) -> dict:
    """Every acquired unit receives actual publication and retained-record lineage."""
    inventory.verify()
    sets, versions, aliases = defaultdict(list), defaultdict(list), defaultdict(set)
    source_records = defaultdict(list)
    for station in release["stations"]:
        sid = station["station_id"]
        for record in station.get("source_records", []):
            key = (record["source"], record["source_version"], record["source_record_id"])
            # Link to the emitted record without duplicating its raw metadata or
            # harmonic payload. Its licence, role and disposition stay verbatim.
            source_records[key].append({"station_id": sid, **{k: v for k, v in record.items()
                                                             if k not in {"original_metadata", "original_harmonics"}}})
        for source, values in station.get("aliases", {}).items():
            for rid in ([values] if isinstance(values, str) else values):
                aliases[(source, rid)].add(sid)
                # Current offsets retain the upstream NOAA station alias. Its
                # existing release representation identifies the source branch.
                if source == "noaa" and station.get("current_offsets"):
                    aliases[("noaa-currents", rid)].add(sid)
        for cs in station.get("constant_sets", []):
            key = (cs["source"], cs["source_version"], cs["source_record_id"])
            sets[key].append((sid, cs["set_id"]))
            versions[(cs["source"], cs["source_record_id"])].append(
                (cs["source_version"], sid, cs["set_id"]))
    refused = {r["record"]: r for r in report.refused}
    carried = {r["record"]: r for r in report.carried}
    not_carried = {r["record"]: r for r in report.not_carried}
    source_names = {"gesla": "gesla-fit", "ticon": "ticon-4"}
    rows = []
    for original in sorted(inventory.document["identities"],
                           key=lambda r: (r["source"], r["version"], r["upstream_record_id"])):
        row = dict(original)
        source, version, rid = row["source"], row["version"], row["upstream_record_id"]
        native_source = source_names.get(source, source)
        role = row["granularity"]
        # Response filenames, grids and bin IDs are not station identifiers.
        auxiliary = role in {"reference_response_file", "official_prediction_response_file", "model_grid_artifact",
                             "comparator_station_metadata", "raw_station_bin_metadata"}
        exact = [] if auxiliary else sets[(native_source, version, rid)]
        other = [] if auxiliary else versions[(native_source, rid)]
        station_ids = set(sid for sid, _ in exact)
        alias_ids = set() if auxiliary else aliases[(source, rid)]
        row["published_set_ids"] = sorted(set(set_id for _, set_id in exact))
        row["station_ids"] = sorted(station_ids | alias_ids)
        if exact:
            disposition = "published_source_set"
        elif other:
            disposition = "source_identity_published_other_version"
            row["other_published_versions"] = sorted(set(v for v, _, _ in other))
            row["station_ids"] = sorted(set(s for _, s, _ in other) | alias_ids)
        elif alias_ids:
            disposition = "published_source_identity"
        elif role == "actual_observation_acquisition_record":
            disposition = "acquired_observations_pending_publication"
        elif role in {"reference_response_file", "official_prediction_response_file", "comparator_station_metadata"}:
            disposition = "retained_comparison_input"
        elif role == "model_grid_artifact":
            disposition = "retained_model_input"
        elif role == "raw_station_bin_metadata":
            disposition = "bin_metadata_pending_lineage"
        elif role == "native_source_set":
            disposition = "acquired_source_set_pending_publication"
        elif role == "upstream_published_harmonic_record":
            disposition = "acquired_harmonics_pending_publication"
        elif role == "upstream_gauge_record":
            disposition = "reported_unpublished_record"
        else:
            disposition = "acquired_identity_pending_publication"
        retained = source_records[(source, version, rid)]
        if retained:
            row["source_record_lineage"] = sorted(retained, key=lambda r: r["station_id"])
            row["station_ids"] = sorted(set(row["station_ids"]) | {r["station_id"] for r in retained})
            if "pending" in disposition:
                disposition = "retained_source_record"
        if source == "gesla" and rid in refused:
            row["fit_publication_disposition"] = refused[rid]
        if source in {"gesla", "ticon"}:
            if rid in carried:
                row["ticon_publication_disposition"] = carried[rid]
            elif rid in not_carried:
                row["ticon_publication_disposition"] = not_carried[rid]
        row["disposition"] = disposition
        rows.append(row)
    original_keys = {(r["source"], r["version"], r["upstream_record_id"]) for r in rows}
    additional = [record for key in sorted(source_records) if key not in original_keys
                  for record in sorted(source_records[key], key=lambda r: r["station_id"])]
    retained_records = [record for records in source_records.values() for record in records]
    return {"inventory_version": 1, "provenance": inventory.provenance,
            "identity_count": len(rows), "identities": rows,
            "counts_by_source": dict(sorted(Counter(r["source"] for r in rows).items())),
            "counts_by_disposition": dict(sorted(Counter(r["disposition"] for r in rows).items())),
            "additional_source_records": additional,
            "source_record_summary": {"total": len(retained_records),
                                      "original": len(retained_records) - len(additional),
                                      "additional": len(additional),
                                      "counts_by_disposition": dict(sorted(Counter(r["disposition"] for r in retained_records).items())),
                                      "count_unit": "source/version/upstream record identities; not distinct places or predictive sets"},
            "count_unit": "source/version/upstream record identities; not distinct places",
            "limitations": inventory.document["limitations"]}
