"""GESLA fits as `SourceSet`s: the comparator side of the per-source canary (manifest F3).

A fit document is one GESLA record's G03c fit (`gesla.select_constituents.fit_record` on the
`qc.clean` series) plus what a set needs around it: the record's position and name from the
GESLA metadata, the member SHA-256, the TCD SHA-256 and each constituent's TCD speed. The
documents of one build live in `$OTC_WORK/builds/<D>/gesla/fits/` (a local cache of the same
prefix in the work bucket; never in git) with a MANIFEST.json, which `load_gesla_sets` checks
file by file before it reads anything.

    uv run python -m otc_pipeline.gesla_sets fit --datestamp D RECORD [RECORD ...]
    uv run python -m otc_pipeline.gesla_sets show --datestamp D

`fit` writes the documents (fitting from the verified GESLA cache); `load_gesla_sets` first
syncs the prefix down from the bucket (`bin/otc-work sync-down`) when the local directory has
no MANIFEST.json. G07c's public product replaces the fitting side; the set shape stays.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from gesla.read_gesla import GeslaRelease, otc_work
from gesla.select_constituents import RecordFit
from gesla.tcd_table import TcdTable

from otc_pipeline.adapter import SourceSet

DOC_VERSION = 1
SOURCE = "gesla-fit"
CONVENTION_ID = "otc-greenwich-schureman-v1"
LICENCE_ID = "gesla-derived"
REPO = Path(__file__).resolve().parents[3]
OTC_WORK_CLI = REPO / "bin" / "otc-work"


def fits_prefix(datestamp: str) -> str:
    return f"builds/{datestamp}/gesla/fits/"


def fits_dir(datestamp: str) -> Path:
    return otc_work() / fits_prefix(datestamp)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(doc: Mapping) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=1, separators=(",", ": "), allow_nan=False) + "\n").encode()


# --- fit documents --------------------------------------------------------------------------------

def fit_document(record_id: str, source_version: str, member_sha256: str, meta: Mapping[str, str],
                 table: TcdTable, fit: RecordFit) -> dict:
    """One record's fit document (DOC_VERSION). `meta` is the record's GESLA metadata row."""
    f = fit.to_dict()
    return {"doc_version": DOC_VERSION, "record_id": record_id, "source_version": source_version,
            "member_sha256": member_sha256, "tcd_sha256": table.tcd_sha256,
            "site_name": meta["SITE NAME"].replace("_", " "), "site_code": meta.get("SITE CODE", ""),
            "country": meta["COUNTRY"], "lat": float(meta["LATITUDE"]), "lon": float(meta["LONGITUDE"]),
            "contributor": meta.get("CONTRIBUTOR (ABBREVIATED)", ""),
            "speeds_deg_per_hour": {n: table.speed(n) for n in sorted(f["constituents"])},
            "fit": f}


def fit_records(table: TcdTable, release: GeslaRelease, record_ids: Iterable[str]) -> list[dict]:
    """Fit each record with the G03c default (default QC, last 19 years) from the verified cache."""
    from gesla.qc import clean
    from gesla.select_constituents import fit_record
    docs = []
    for rid in record_ids:
        rec = release.record(rid, roles={})
        docs.append(fit_document(rid, release.version, rec.member_sha256, release.metadata()[rid], table,
                                 fit_record(table, clean(rec))))
    return docs


def write_fits(docs: Iterable[dict], out_dir: Path) -> Path:
    """Writes <record_id>.json per document, then MANIFEST.json (last). Returns the manifest path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for doc in docs:
        data = _canonical(doc)
        name = f"{doc['record_id']}.json"
        (out_dir / name).write_bytes(data)
        files.append({"path": name, "sha256": _sha256(data), "size": len(data),
                      "source_url": f"gesla:{doc['source_version']}/{doc['record_id']}",
                      "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    manifest = {"dataset": "gesla-fit documents", "doc_version": DOC_VERSION, "licence": LICENCE_ID,
                "files": sorted(files, key=lambda f: f["path"])}
    path = out_dir / "MANIFEST.json"
    path.write_bytes(_canonical(manifest))
    return path


def read_fits(in_dir: Path) -> list[dict]:
    """Every document named in MANIFEST.json, each checked against its size and SHA-256."""
    manifest = json.loads((in_dir / "MANIFEST.json").read_text())
    if manifest.get("doc_version") != DOC_VERSION:
        raise ValueError(f"{in_dir}/MANIFEST.json: doc_version {manifest.get('doc_version')!r}, "
                         f"expected {DOC_VERSION}")
    docs = []
    for f in manifest["files"]:
        data = (in_dir / f["path"]).read_bytes()
        if _sha256(data) != f["sha256"] or len(data) != f["size"]:
            raise ValueError(f"{in_dir / f['path']}: sha256 {_sha256(data)} size {len(data)}, "
                             f"manifest has {f['sha256']} size {f['size']}")
        docs.append(json.loads(data))
    return docs


def sync_down(datestamp: str) -> Path:
    """Fetch builds/<D>/gesla/fits/ from the work bucket into the local cache."""
    subprocess.run([str(OTC_WORK_CLI), "sync-down", fits_prefix(datestamp)], check=True)
    return fits_dir(datestamp)


# --- documents -> SourceSet -----------------------------------------------------------------------

def _iso(t: int) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_set(doc: Mapping) -> SourceSet:
    """A fit document as a gesla-fit `SourceSet` (schema 0.3 constant_set without set_id)."""
    if doc.get("doc_version") != DOC_VERSION:
        raise ValueError(f"{doc.get('record_id')}: doc_version {doc.get('doc_version')!r}, expected {DOC_VERSION}")
    f = doc["fit"]
    speeds = doc["speeds_deg_per_hour"]
    cons = []
    for name in sorted(f["constituents"]):
        c = f["constituents"][name]
        entry = {"name": name, "speed_deg_per_hour": speeds[name], "amplitude_m": c["amp_m"],
                 "phase_deg": c["phase_deg"]}
        if "u_m" in c:
            entry["amp_uncertainty_m"] = c["u_m"]
        cons.append(entry)
    return SourceSet(
        station_hint={"name": doc["site_name"], "country": doc["country"], "lat": doc["lat"], "lon": doc["lon"],
                      "type": "reference", "timezone": None, "aliases": {"gesla": [doc["record_id"]]}},
        constant_set={
            "source": SOURCE, "source_type": "gauge", "quantity": "water_level",
            "source_record_id": doc["record_id"], "source_version": doc["source_version"],
            "record_span": {"start": _iso(f["first_s"]), "end": _iso(f["last_s"]), "good_samples": f["n"]},
            "datum": {"msl_offset_m": f["z0_m"]},
            "convention_id": CONVENTION_ID, "licence_id": LICENCE_ID, "qc_status": "accepted",
            "constituents": cons,
            "provenance": {"input_sha256": [doc["member_sha256"]], "tcd_sha256": doc["tcd_sha256"]},
        })


def load_gesla_sets(datestamp: str | None = None, *, in_dir: Path | None = None,
                    only: Iterable[str] | None = None) -> list[SourceSet]:
    """The gesla-fit sets of one build (or of in_dir), sorted by record id; `only` keeps those records.
    Syncs from the work bucket first when the local cache has no MANIFEST.json."""
    if in_dir is None:
        if datestamp is None:
            raise ValueError("load_gesla_sets needs a datestamp or in_dir")
        in_dir = fits_dir(datestamp)
        if not (in_dir / "MANIFEST.json").exists():
            sync_down(datestamp)
    keep = set(only) if only is not None else None
    sets = [source_set(d) for d in read_fits(in_dir) if keep is None or d["record_id"] in keep]
    if keep is not None and len(sets) != len(keep):
        have = {s.constant_set["source_record_id"] for s in sets}
        raise KeyError(f"{in_dir}: no fit document for {sorted(keep - have)}")
    return sorted(sets, key=lambda s: s.constant_set["source_record_id"])


# --- CLI ------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.gesla_sets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fit", help="fit GESLA records from the verified cache and write their documents")
    fp.add_argument("--datestamp", required=True)
    fp.add_argument("--tcd-table", type=Path, default=None, help="TCD dump (default $OTC_WORK/tcd_table.json)")
    fp.add_argument("--tcd", type=Path, default=REPO / "data" / "latest-xtide.tcd")
    fp.add_argument("records", nargs="+")
    sp = sub.add_parser("show", help="print the sets of a build as JSON")
    sp.add_argument("--datestamp", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "fit":
        dump = a.tcd_table or otc_work() / "tcd_table.json"
        table = TcdTable.load(dump, tcd_sha256=_sha256(a.tcd.read_bytes()))
        path = write_fits(fit_records(table, GeslaRelease(), a.records), fits_dir(a.datestamp))
        print(path)
        return 0
    for s in load_gesla_sets(a.datestamp):
        print(json.dumps({"station_hint": s.station_hint, "constant_set": s.constant_set}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
