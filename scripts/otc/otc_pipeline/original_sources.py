"""Retain original acquired source identities and scientific payloads after selection.

This collection changes publication metadata only. It does not fit observations,
invent missing constants, rerank existing sets or infer source permissions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re

from gesla.read_gesla import GeslaRelease
from otc_pipeline import gesla_release as gr, station_map as sm
from otc_pipeline.gesla_licences import gesla_licences, withheld_licences
from otc_pipeline.source_inventory import SourceInventory

NONSPATIAL = {"reference_response_file", "model_grid_artifact", "official_prediction_response_file"}
ALIASES = {"marine-institute": "mi", "shom-refmar": "shom", "uhslc-fd": "uhslc",
           "noaa-currents": "noaa"}
NATIVE_SOURCE = {"gesla": "gesla-fit", "ticon": "ticon-4"}
FINDING_ROWS = {"noaa-currents/BOS1101:1/events", "noaa-currents/BOS1101:15/events",
                "noaa-currents/PUG1501:1/events", "noaa-currents/PUG1501:6/events",
                "noaa-currents/ACT0091:1/events", "kartverket/BGO/extremes"}


def scalar_metadata(row: dict) -> dict:
    """Keep every original field; preserve nested structures as literal JSON strings."""
    out = {key: value if value is None or isinstance(value, (str, int, float, bool))
           else json.dumps(value, ensure_ascii=False, separators=(",", ":")) for key, value in row.items()}
    out["original_row_json"] = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    return out


def _licence(rows: dict, licence: dict | None) -> str | None:
    if licence is None:
        return None
    lid = licence["licence_id"]
    if lid in rows and rows[lid] != licence:
        raise ValueError(f"original source gives inconsistent licence {lid}")
    rows[lid] = copy.deepcopy(licence)
    return lid


def retain_original_sources(release: dict, inventory: SourceInventory, *, fit_run: Path,
                            docs: list, report: gr.BuildReport, source_metadata: dict,
                            chs=None) -> dict:
    """Publish every spatial original tuple, preserving the existing selected science."""
    inventory.verify()
    licences = {row["licence_id"]: row for row in release["licences"]}
    stations = {row["station_id"]: row for row in release["stations"] if row["status"] == "active"}
    taken = {row["station_id"] for row in release["stations"]}
    record_station, alias_station = {}, {}
    for sid, station in stations.items():
        for source, values in station.get("aliases", {}).items():
            for rid in ([values] if isinstance(values, str) else values):
                alias_station.setdefault((source, rid), set()).add(sid)
        for record in station.get("source_records", []):
            record_station[(record["source"], record["source_record_id"])] = sid
        for cs in station["constant_sets"]:
            source = {"gesla-fit": "gesla", "ticon-4": "ticon"}.get(cs["source"], cs["source"])
            record_station[(source, cs["source_record_id"])] = sid
    station_ids = sm.load().record_ids()
    original_gesla = [row for row in inventory.document["identities"] if row["source"] == "gesla"]
    gesla = GeslaRelease()
    metadata = gesla.metadata()
    contributors = [{"record_id": row["upstream_record_id"],
                     "contributor": metadata[row["upstream_record_id"]]["CONTRIBUTOR (ABBREVIATED)"]}
                    for row in original_gesla]
    original_licences, gesla_ids = gesla_licences(contributors)
    for row in original_licences:
        _licence(licences, row)
    fit_manifest = json.loads((Path(fit_run) / "timebase_manifest.json").read_text())
    fit_hashes = fit_manifest["fits_sha256"]
    doc_by_id = {doc["record_id"]: doc for doc in docs}
    refusals = {row["record"]: row for row in report.refused}
    withheld = withheld_licences()
    added, nonspatial = [], []

    def station_for(source, rid, data):
        parent = rid.split("#bin:", 1)[0] if source == "noaa-currents" else rid
        sid = record_station.get((source, parent))
        if sid is None:
            sid = station_ids.get(parent if source == "gesla" else f"{source}:{parent}")
        if sid is None:
            candidates = alias_station.get((ALIASES.get(source, source), parent), set())
            if len(candidates) > 1:
                raise ValueError(f"{source}:{rid}: original identity aliases resolve to multiple stations")
            sid = next(iter(candidates)) if candidates else None
        if sid is None:
            sid = sm.new_id(f"{source}:{parent}", taken)
        if sid not in stations:
            lat, lon = data.get("lat"), data.get("lon")
            if (not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))
                    or not math.isfinite(lat) or not math.isfinite(lon)
                    or not -90 <= lat <= 90 or not -180 <= lon <= 180):
                raise ValueError(f"{source}:{rid}: acquired spatial identity has no usable original position")
            country = data.get("country")
            if not isinstance(country, str) or not re.fullmatch(r"[A-Z]{3}", country):
                country = None
            station = {"station_id": sid, "status": "active", "name": data.get("name") or parent,
                       "country": country, "lat": lat, "lon": lon, "timezone": gr.station_timezone(lat, lon),
                       "type": "reference", "aliases": {}, "constant_sets": [], "recommended_set_id": None}
            stations[sid] = station
            release["stations"].append(station)
            taken.add(sid)
        station = stations[sid]
        system = ALIASES.get(source, source)
        values = station.setdefault("aliases", {}).setdefault(system, [])
        if isinstance(values, str):
            values = [values]
        station["aliases"][system] = sorted(set(values) | {parent})
        alias_station.setdefault((system, parent), set()).add(sid)
        record_station[(source, parent)] = sid
        return station

    for original in sorted(inventory.document["identities"],
                           key=lambda row: (row["source"], row["version"], row["upstream_record_id"])):
        source, version, rid = (original[key] for key in ("source", "version", "upstream_record_id"))
        if original["granularity"] in NONSPATIAL:
            nonspatial.append((source, version, rid))
            continue
        if source == "ticon":
            sid = record_station.get((source, rid))
            if sid is None or not any(r["source"] == source and r["source_version"] == version
                                      and r["source_record_id"] == rid for r in stations[sid].get("source_records", [])):
                raise ValueError(f"{source}:{rid}: upstream harmonic publication collection missing")
            continue
        if source == "gesla":
            meta = metadata[rid]
            data = {"name": meta["SITE NAME"], "lat": float(meta["LATITUDE"]),
                    "lon": float(meta["LONGITUDE"]), "country": meta["COUNTRY"]}
            lid = gesla_ids[rid]
            outcome = original["fit_outcome"]
            disposition = "not_fitted" if outcome != "fitted" else "licence_restricted" if lid in withheld else "retained"
            detail = f"Actual original GESLA fit outcome: {outcome}; {original.get('fit_reason', '')}."
            if rid in refusals:
                detail += " Actual publication finding: " + json.dumps(refusals[rid], ensure_ascii=False)
            raw_metadata = scalar_metadata(meta)
            raw_metadata["fit_outcome"] = outcome
            raw_metadata["fit_reason"] = original.get("fit_reason")
            raw_metadata["original_member_sha256"] = gesla.members[rid]["sha256"]
            if disposition == "licence_restricted":
                path = Path(fit_run) / "fits" / (rid + ".json")
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if digest != fit_hashes[rid] or rid not in doc_by_id:
                    raise ValueError(f"{rid}: original restricted fit differs from the accepted native fit input")
                raw_metadata.update({"original_fit_path": str(path.resolve()), "original_fit_sha256": digest,
                                     "original_fit_json": raw.decode("utf-8")})
                detail += " Full original scientific fit retained under its existing restricted licence; not an admitted predictive set."
        else:
            data = source_metadata.get((source, rid))
            if data is None:
                raise ValueError(f"{source}:{version}:{rid}: acquired original source metadata is missing")
            lid = _licence(licences, data.get("licence"))
            raw_metadata = copy.deepcopy(data.get("original_metadata", {}))
            for key in ("original_constant_set_json", "original_offsets_json"):
                if key in data:
                    raw_metadata[key] = data[key]
            detail = data.get("detail") or "Original upstream identity retained with its actual acquired inputs."
            disposition = data.get("disposition") or (
                "not_fitted" if original["granularity"] in {"gauge_record", "actual_observation_acquisition_record"}
                else "metadata_only")
            if "original_constant_set_json" in data:
                source_set = json.loads(data["original_constant_set_json"])
                if source_set.get("provenance", {}).get("redistribution") == "comparator_only":
                    disposition = "licence_restricted"
                    detail += " Original comparator-only scientific payload retained without an admitted predictive set or inherited publication permission."
        station = station_for(source, rid, data)
        native_source = NATIVE_SOURCE.get(source, source)
        parent = rid.split("#bin:", 1)[0] if source == "noaa-currents" else rid
        sets = [cs["set_id"] for cs in station["constant_sets"]
                if cs["source"] == native_source and cs["source_record_id"] == parent]
        if source != "gesla" and sets and disposition == "metadata_only":
            disposition = "retained"
            detail += " The existing native source set remains available; metadata does not replace its values."
        record = {"source": source, "source_version": version, "source_record_id": rid,
                  "role": "published_harmonic_record" if original["granularity"] == "native_source_set" and (
                      sets or "original_constant_set_json" in raw_metadata) else "gauge_station",
                  "disposition": disposition, "detail": detail, "licence_id": lid, "published_set_ids": sets,
                  "provenance": {"input_sha256": list(original["input_sha256"]),
                                 "inventory_sha256": inventory.provenance["sha256"]},
                  "original_metadata": raw_metadata}
        station.setdefault("source_records", []).append(record)
        added.append((source, version, rid))
    if chs is not None:
        for data in chs.records:
            station = station_for(data["source"], data["source_record_id"], data)
            lid = _licence(licences, data["licence"])
            station.setdefault("source_records", []).append({
                "source": data["source"], "source_version": data["source_version"],
                "source_record_id": data["source_record_id"], "role": "gauge_station",
                "disposition": data["disposition"], "detail": data["detail"], "licence_id": lid,
                "published_set_ids": [], "original_metadata": data["original_metadata"],
                "provenance": {"input_sha256": data["input_sha256"],
                               "inventory_sha256": chs.provenance["inventory_sha256"]}})
            added.append((data["source"], data["source_version"], data["source_record_id"]))
    release["licences"] = [licences[lid] for lid in sorted(licences)]
    release["stations"] = sorted(stations.values(), key=lambda station: station["station_id"]) + [
        station for station in release["stations"] if station["status"] != "active"]
    return {"retained_original_spatial_units": len(added), "original_nonspatial_units": len(nonspatial),
            "nonspatial_roles": sorted(NONSPATIAL), "inventory_sha256": inventory.provenance["sha256"],
            "chs": chs.provenance if chs is not None else None, "unique_place_claim": False}


def apply_quality_findings(release: dict, inventory: SourceInventory, *, path: Path, sha256: str) -> dict:
    """Attach the six approved metric findings without changing numerical results or bounds."""
    raw = Path(path).read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("source quality findings SHA-256 mismatch")
    document = json.loads(raw)
    facts = document["label_facts"]
    if len(facts) != len(FINDING_ROWS) or {row["finding"]["row_id"] for row in facts} != FINDING_ROWS:
        raise ValueError("source quality findings do not cover the complete approved six-row scope")
    if document["inventory_sha256"] != inventory.provenance["sha256"]:
        raise ValueError("source quality findings refer to another original source inventory")
    originals = {(row["source"], row["version"], row["upstream_record_id"]): row for row in inventory.document["identities"]}
    stations = {station["station_id"]: station for station in release["stations"]}
    actions = []
    for row in facts:
        finding, identity = row["finding"], row["identity"]
        detail = row["detail_line"]
        if not detail.startswith("OTC metric finding: ") or json.loads(detail.removeprefix("OTC metric finding: ")) != finding:
            raise ValueError("source quality detail does not preserve the exact supplied finding")
        if any(finding[key] != identity[key] for key in ("source", "source_version", "source_record_id")):
            raise ValueError("source quality finding identity differs from its actual source tuple")
        key = (identity["source"], identity["source_version"], identity["source_record_id"])
        original = originals.get(key)
        if original is None or row["inventory_provenance"] != {
                "inventory_sha256": inventory.provenance["sha256"], "input_sha256": original["input_sha256"]}:
            raise ValueError("source quality finding has incorrect acquired source provenance")
        if (finding["result"] != "FAIL" or finding["window_start"] != "2026-10-01T00:00:00Z"
                or finding["window_end"] != "2026-11-01T00:00:00Z"):
            raise ValueError("source quality finding changes the approved comparison window or result")
        if identity["source"] == "noaa-currents":
            expected = f"noaa-currents/{identity['source_record_id']}:{finding['bin_id']}/events"
            if finding["row_id"] != expected or finding["metric"] != "event_count" or finding["bound"] != 0:
                raise ValueError("source quality event finding changes its row, bin, metric or bound")
        elif (finding["metric"] != "time_max_s" or finding["bound"] != 180
              or finding["native_value"] != 182 or finding["official_value"] is not None):
            raise ValueError("source quality tide finding changes its timing metric or bound")
        station = stations.get(finding["station_id"])
        if station is None:
            raise ValueError("source quality finding has no actual published station")
        records = [record for record in station.get("source_records", [])
                   if (record["source"], record["source_version"], record["source_record_id"]) == key]
        if len(records) != 1 or records[0]["provenance"] != row["inventory_provenance"]:
            raise ValueError("source quality finding has no unique correctly bound published source record")
        if identity["source"] == "noaa-currents":
            if finding["set_id"] is None:
                offsets = [offset for offset in station.get("current_offsets", []) if offset["bin"] == finding["bin_id"]]
                if (identity["source_record_id"] != "ACT0091" or len(offsets) != 1
                        or offsets[0]["reference_station_id"] != finding["reference_station_id"]
                        or offsets[0]["reference_bin"] != finding["reference_bin_id"]):
                    raise ValueError("source quality offset finding changes its actual bin/reference")
            else:
                sets = [cs for cs in station["constant_sets"] if cs["set_id"] == finding["set_id"]]
                if len(sets) != 1 or not any(bin_row["bin"] == finding["bin_id"] for bin_row in sets[0].get("current_bins", [])):
                    raise ValueError("source quality current finding has no actual published bin")
        elif (finding["metric"] != "time_max_s" or finding["bound"] != 180
              or finding["native_value"] != 182 or finding["official_value"] is not None
              or not any(cs["set_id"] == finding["set_id"] for cs in station["constant_sets"])):
            raise ValueError("source quality tide finding changes its timing metric, bound or actual set")
        actions.append((records[0], detail))
    for record, detail in actions:
        record["detail"] += "\n" + detail
    return {"path": str(Path(path).resolve()), "sha256": sha256, "labelled_failed_rows": len(actions),
            "scientific_results_changed": False}
