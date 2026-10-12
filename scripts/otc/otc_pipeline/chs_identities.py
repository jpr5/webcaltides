"""Hash-bound CHS metadata ingress, preserving separately acquired source and licence domains.

This loader reads the acquired bundle only. It makes no station joins, observations, constants,
time zones or datum interpretations. Nested upstream values remain in named ``*_json`` metadata
fields; ``row_json`` preserves the entire acquired row for a lossless round trip.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CHSIdentities:
    records: list[dict]
    provenance: dict


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_inventory(bundle: Path) -> dict[str, str]:
    hashes = {}
    for line in (bundle / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or name in hashes:
            raise ValueError(f"invalid CHS inventory path {name!r}")
        actual = _sha(bundle / path)
        if actual != expected:
            raise ValueError(f"CHS SHA256 mismatch for {name}: {actual} != {expected}")
        hashes[name] = actual
    return hashes


def _acquisitions(bundle: Path, hashes: dict[str, str]) -> list[dict]:
    verified = []
    for acquired in _json(bundle / "request-census.json"):
        name = acquired["name"]
        response = f"{name}.response"
        request = f"{name}.request.json"
        provenance = _json(bundle / f"{name}.provenance.json")
        descriptor = _json(bundle / request)
        encoded = json.dumps(descriptor, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_sha = hashlib.sha256(encoded).hexdigest()
        if (hashes.get(response) != acquired["response_sha256"]
                or provenance["response_sha256"] != acquired["response_sha256"]
                or (bundle / response).stat().st_size != acquired["response_bytes"]
                or request_sha != acquired["request_sha256"]
                or provenance["request_sha256"] != request_sha
                or acquired["status"] != 200 or provenance["status"] != 200):
            raise ValueError(f"CHS acquisition provenance mismatch for {name}")
        verified.append({"name": name, "path": response, "sha256": hashes[response],
                         "request_sha256": request_sha, "source_url": acquired["url"]})
    return verified


def _licences(bundle: Path) -> tuple[dict, dict]:
    evidence = _json(bundle / "licence-evidence.json")
    ogl, iwls = evidence["open_data_csv"], evidence["iwls"]
    open_licence = {"licence_id": "chs-dfo-ogl", "spdx": "OGL-Canada-2.0", "commercial_use": True,
                    "provider": ogl["publisher"], "attribution": ogl["attribution_as_published"],
                    "url": ogl["licence_url"]}
    restrictive_licence = {
        "licence_id": "chs-iwls-restricted", "spdx": "LicenseRef-CHS-IWLS-Restricted", "commercial_use": False,
        "provider": "Canadian Hydrographic Service (CHS)",
        "attribution": (bundle / "iwls-derivative-notice.raw.txt").read_text(encoding="utf-8"),
        "url": iwls["terms_url"], "terms_url": iwls["terms_url"],
        "restriction": " ".join(iwls[key] for key in ("terms_fact", "copyright_fact", "restriction_survival")),
    }
    return open_licence, restrictive_licence


def _original_metadata(row: dict) -> dict:
    metadata = {}
    for key, value in row.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            metadata[key] = value
        else:
            metadata[f"{key}_json"] = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    metadata["row_json"] = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return metadata


def load_chs_identities(bundle_dir: Path) -> CHSIdentities:
    """Load all three acquired CHS cohorts without merging their identities or licensing scopes.

    Returns source metadata records and verified input/inventory provenance. ``country`` and
    unknown upstream fields stay null. CSV rows are metadata_only; separately restrictive IWLS
    catalogue/current metadata rows are licence_restricted. Advertised phenomena describe roles,
    not downloaded numerical series. The caller owns station IDs, registry and publication.
    """
    bundle = Path(bundle_dir)
    hashes = _verify_inventory(bundle)
    acquired = _acquisitions(bundle, hashes)
    response_hashes = {entry["sha256"] for entry in acquired}
    census = _json(bundle / "census.json")
    metadata_census = _json(bundle / "metadata-census.json")
    comparisons = _json(bundle / "source-number-comparison.json")
    conflicts = sum(bool(row.get("position_differs")) for row in comparisons)
    if conflicts != census["same_number_different_positions"]:
        raise ValueError("CHS comparison count differs from the acquired census")
    ogl, iwls = _licences(bundle)
    cohorts = (
        ("chs-dfo-ogl", "open-data-identities.jsonl", census["open_data_rows"], ogl),
        ("chs-iwls", "iwls-identities.jsonl", census["iwls_rows"], iwls),
        ("chs-current-metadata", "current-identities.jsonl", metadata_census["current_station_identities"], iwls),
    )
    records, counts = [], {}
    for source, name, expected, licence in cohorts:
        rows = [json.loads(line) for line in (bundle / name).read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != expected:
            raise ValueError(f"CHS {source} count {len(rows)} != acquired census {expected}")
        counts[source] = len(rows)
        ids = set()
        for row in sorted(rows, key=lambda row: row["source_record_id"]):
            record_id = row["source_record_id"]
            if record_id in ids:
                raise ValueError(f"CHS {source} repeats source_record_id {record_id}")
            ids.add(record_id)
            source_sha = row["source_sha256"]
            if source_sha not in response_hashes:
                raise ValueError(f"CHS {record_id}: source response SHA256 is not an acquired input")
            input_hashes = [source_sha, hashes[name], hashes["licence-evidence.json"]]
            version_sha = source_sha
            metadata = row.get("metadata_evidence")
            if metadata:
                metadata_name = Path(metadata["path"]).name
                if hashes.get(metadata_name) != metadata["sha256"] or metadata["sha256"] not in response_hashes:
                    raise ValueError(f"CHS {record_id}: metadata response SHA256 is not an acquired input")
                input_hashes.append(metadata["sha256"])
                version_sha = metadata["sha256"]
            evidence = _json(bundle / "licence-evidence.json")
            scope = evidence["open_data_csv"] if source == "chs-dfo-ogl" else evidence["iwls"]
            input_hashes += [hashes[path] for path in scope.get("evidence_files", [])]
            if source != "chs-dfo-ogl":
                input_hashes.append(hashes["iwls-derivative-notice.raw.txt"])
            records.append({
                "source": source, "source_version": f"capture:{version_sha}", "source_record_id": record_id,
                "name": row["name"], "lat": row["latitude"], "lon": row["longitude"], "country": row["country"],
                "licence": dict(licence), "input_sha256": sorted(set(input_hashes)),
                "original_metadata": _original_metadata(row),
                "disposition": "metadata_only" if source == "chs-dfo-ogl" else "licence_restricted",
                "detail": ("Acquired station metadata only; no observations, predictions or constants acquired. "
                           + row.get("redistribution_basis", "")),
            })
    provenance = {
        "bundle_dir": str(bundle.resolve()), "inventory_path": str(bundle / "SHA256SUMS"),
        "inventory_sha256": _sha(bundle / "SHA256SUMS"),
        "input_files": [{"path": name, "sha256": sha} for name, sha in sorted(hashes.items())],
        "acquisitions": acquired, "verified_acquisitions": len(acquired), "counts": {**counts,
            "same_number_different_positions": conflicts}, "comparison_evidence": comparisons,
        "acquired_census": census, "acquired_metadata_census": metadata_census,
        "licence_evidence": _json(bundle / "licence-evidence.json"),
    }
    return CHSIdentities(records, provenance)
