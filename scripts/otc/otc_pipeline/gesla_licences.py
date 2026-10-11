"""Per-contributor licences of the GESLA-fit sets (L-NC): the `licences` rows and the record ->
licence id map that gesla_release.build_release takes.

    licences, licence_ids = gesla_licences(docs)

`data/gesla/contributor_roles.csv` names one `licence_id` per GESLA contributor, and
`data/gesla/licences.json` holds the licence rows (format 1.0 `licence` objects, checked against the
pinned schema: a non-commercial licence carries `restriction` and `terms_url`). There is no default
licence: a record whose contributor is not in the roles table, a contributor with no licence id, or
a licence id with no row stops the build (ValueError). Each record is also checked against its GESLA
inventory `licence_class` (data/gesla/4.1/inventory.csv): a research_only record that maps to a
commercial licence, an unlisted record that maps to one without an `unlisted_open` entry in
licences.json (an unlisted contributor whose publisher documents an open licence), or a record not
in the inventory, stops the build too.

Withholding follows the ORIGINATOR, not the channel. `data/gesla/record_originators.csv` gives every GESLA
record its GESLA ORIGINATOR and, for UHSLC records, the UHSLC station metadata Data_Source (an FD record:
the RQ versions of its station number whose span overlaps its own). Each `withheld` switch in
licences.json lists the `originator_patterns` of its agency; a record whose originator text matches one
must map to a withheld licence, or the build stops. `data/gesla/licence_overrides.csv` gives single
records another licence than their contributor's (records of a withheld or JMA originator carried by
another channel); an override names an inventory record of that contributor, a licence with a row and
a non-empty reason, may not move a record out of a withheld licence, and may move it to a commercial
licence only when the record's own originator field names a documented originator of that licence
and the reason quotes the GESLA ORIGINATOR.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Mapping

from jsonschema import Draft202012Validator

from otc_pipeline.release_writer import pinned_schema

__all__ = [
    "INVENTORY_CSV",
    "KNOWN_CLASSES",
    "LICENCES_JSON",
    "NON_COMMERCIAL_CLASSES",
    "ORIGINATORS_CSV",
    "OVERRIDES_CSV",
    "ROLES_CSV",
    "contributor_licences",
    "gesla_licences",
    "inventory_classes",
    "licence_originators",
    "load_licences",
    "load_unlisted_open",
    "originator_licences",
    "record_licence_ids",
    "record_originators",
    "record_overrides",
    "withheld_licences",
    "withheld_patterns",
]

_DATA = Path(__file__).resolve().parents[3] / "data" / "gesla"
ROLES_CSV = _DATA / "contributor_roles.csv"
LICENCES_JSON = _DATA / "licences.json"
OVERRIDES_CSV = _DATA / "licence_overrides.csv"
ORIGINATORS_CSV = _DATA / "record_originators.csv"
INVENTORY_CSV = _DATA / "4.1" / "inventory.csv"
# GESLA licence classes whose records may never carry a commercial licence
NON_COMMERCIAL_CLASSES = ("research_only", "unlisted")
# every inventory licence_class known today (exact strings); any other value stops the build
KNOWN_CLASSES = ("research_consultancy", *NON_COMMERCIAL_CLASSES)


def load_licences(path: Path = LICENCES_JSON) -> list[dict]:
    """The licence rows of `path` ({"licences": [...]}), each valid against the pinned schema's
    `licence` definition, with unique ids. Raises ValueError otherwise."""
    rows = json.loads(Path(path).read_text(encoding="utf-8")).get("licences")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: no licences")
    validator = Draft202012Validator(pinned_schema()["$defs"]["licence"],
                                     format_checker=Draft202012Validator.FORMAT_CHECKER)
    seen, errors = set(), []
    for i, row in enumerate(rows):
        lid = row.get("licence_id") if isinstance(row, dict) else None
        errors += [f"licences[{i}] ({lid}): {e.message}" for e in validator.iter_errors(row)]
        if lid in seen:
            errors.append(f"licences[{i}]: licence_id {lid!r} is listed twice")
        seen.add(lid)
    if errors:
        raise ValueError(f"{path}: " + "; ".join(errors))
    return rows


def load_unlisted_open(path: Path = LICENCES_JSON) -> dict[str, str]:
    """contributor -> licence id: the unlisted contributors allowed a commercial licence (`unlisted_open`)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8")).get("unlisted_open") or {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def withheld_licences(path: Path = LICENCES_JSON) -> dict[str, str]:
    """licence id -> reason: the licences whose records the build leaves out until the owner permits
    (`withheld` in licences.json). Each must have a licence row and a reason."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = {r.get("licence_id") for r in raw.get("licences") or []}
    out = {}
    for lid, entry in (raw.get("withheld") or {}).items():
        if lid.startswith("_"):
            continue
        if lid not in ids:
            raise ValueError(f"{path}: withheld licence {lid!r} has no licence row")
        reason = entry.get("reason") if isinstance(entry, dict) else entry
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{path}: withheld licence {lid!r} has no reason")
        evidence = entry.get("evidence") if isinstance(entry, dict) else None
        if not isinstance(evidence, list) or not evidence or not all(isinstance(e, str) and e for e in evidence):
            raise ValueError(f"{path}: withheld licence {lid!r} has no evidence")
        pats = entry.get("originator_patterns") if isinstance(entry, dict) else None
        if not isinstance(pats, list) or not pats or not all(isinstance(x, str) and x for x in pats):
            raise ValueError(f"{path}: withheld licence {lid!r} has no originator_patterns")
        out[lid] = reason
    return out


def withheld_patterns(path: Path = LICENCES_JSON) -> dict[str, list[re.Pattern]]:
    """licence id -> compiled `originator_patterns` of each withheld switch (validated by withheld_licences)."""
    withheld_licences(path)
    raw = json.loads(Path(path).read_text(encoding="utf-8")).get("withheld") or {}
    return {lid: [re.compile(x, re.IGNORECASE) for x in e["originator_patterns"]] for lid, e in raw.items()
            if not lid.startswith("_")}


def originator_licences(path: Path = LICENCES_JSON) -> dict[str, tuple[set[str], list[re.Pattern]]]:
    """licence id -> (contributors, originator patterns) from `originator_licences`: a record of one of those
    contributors whose originator matches, and that no withheld switch takes, must carry that licence."""
    raw = json.loads(Path(path).read_text(encoding="utf-8")).get("originator_licences") or {}
    return {lid: (set(e["contributors"]), [re.compile(x, re.IGNORECASE) for x in e["originator_patterns"]])
            for lid, e in raw.items() if not lid.startswith("_")}


def record_originators(path: Path = ORIGINATORS_CSV) -> dict[str, tuple[str, str]]:
    """record id -> (GESLA ORIGINATOR, UHSLC Data_Source or ''). The table must be the one its sidecar
    (<name>.sources.json, written by otc_pipeline.gesla_originators) pins by SHA-256, and no record may have
    an empty GESLA ORIGINATOR; otherwise ValueError (a hand-edited table stops the build)."""
    path = Path(path)
    sidecar = path.with_suffix(".sources.json")
    if not sidecar.exists():
        raise ValueError(f"{path}: no sidecar {sidecar.name} pinning its sources")
    pinned = json.loads(sidecar.read_text(encoding="utf-8")).get("csv_sha256")
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != pinned:
        raise ValueError(f"{path}: SHA-256 {got[:12]} is not the {pinned and pinned[:12]!r} pinned in {sidecar.name}; "
                         "regenerate it with otc_pipeline.gesla_originators build")
    out: dict[str, tuple[str, str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        need = {"record_id", "gesla_originator", "uhslc_data_source"}
        if not need <= set(reader.fieldnames or []):
            raise ValueError(f"{path}: needs columns {sorted(need)}")
        for row in reader:
            rid = row["record_id"].strip()
            if rid in out:
                raise ValueError(f"{path}: record {rid!r} is listed twice")
            if not row["gesla_originator"].strip():
                raise ValueError(f"{path}: record {rid!r} has an empty gesla_originator")
            out[rid] = (row["gesla_originator"], row["uhslc_data_source"])
    return out


def licence_originators(path: Path = LICENCES_JSON) -> dict[str, list[str]]:
    """licence id -> the names of its documented originators (`originators`): an override to a commercial
    licence must quote the record's GESLA ORIGINATOR naming one of them."""
    raw = json.loads(Path(path).read_text(encoding="utf-8")).get("originators") or {}
    return {k: list(v) for k, v in raw.items() if not k.startswith("_")}


def contributor_licences(path: Path = ROLES_CSV) -> dict[str, str]:
    """contributor -> licence_id from the roles table. Every row needs a licence id (no default)."""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "licence_id" not in (reader.fieldnames or []):
            raise ValueError(f"{path}: no licence_id column (L-NC)")
        for row in reader:
            name = (row.get("contributor") or "").strip()
            lid = (row.get("licence_id") or "").strip()
            if not name or not lid:
                raise ValueError(f"{path}: contributor {name!r} has no licence_id (no licence is assumed)")
            if name in out:
                raise ValueError(f"{path}: contributor {name!r} is listed twice")
            out[name] = lid
    return out


def inventory_classes(path: Path = INVENTORY_CSV) -> dict[str, str]:
    """GESLA file id -> licence_class from the inventory (G02)."""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["file_id"] in out:
                raise ValueError(f"{path}: file_id {row['file_id']!r} is listed twice (its licence class is ambiguous)")
            out[row["file_id"]] = row["licence_class"]
    return out


def record_overrides(path: Path = OVERRIDES_CSV) -> dict[str, tuple[str, str, str]]:
    """record id -> (contributor, licence id, reason): single records whose licence is not their contributor's."""
    out: dict[str, tuple[str, str, str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        need = {"record_id", "contributor", "licence_id", "reason"}
        if not need <= set(reader.fieldnames or []):
            raise ValueError(f"{path}: needs columns {sorted(need)}")
        for row in reader:
            rid, contributor, lid = (row[k].strip() for k in ("record_id", "contributor", "licence_id"))
            if not rid or not contributor or not lid or not row["reason"].strip():
                raise ValueError(f"{path}: record {rid!r} has no licence_id, contributor or reason")
            if rid in out:
                raise ValueError(f"{path}: record {rid!r} is listed twice")
            out[rid] = (contributor, lid, row["reason"].strip())
    return out


def record_licence_ids(docs: Iterable[Mapping], by_contributor: Mapping[str, str],
                       commercial: Mapping[str, bool], licence_class: Mapping[str, str],
                       unlisted_open: Mapping[str, str] | None = None,
                       overrides: Mapping[str, tuple[str, str, str]] | None = None,
                       withheld: Mapping[str, list[re.Pattern]] | None = None,
                       originators: Mapping[str, list[str]] | None = None,
                       record_origin: Mapping[str, tuple[str, str]] | None = None,
                       required: Mapping[str, tuple[set[str], list[re.Pattern]]] | None = None) -> dict[str, str]:
    """record id -> licence id for every document, by its GESLA contributor. `commercial` is licence id
    -> commercial_use, `licence_class` the inventory's file id -> licence_class. Raises ValueError for a
    contributor not in the table, a licence id with no row, a record seen twice, a record not in the
    inventory, a research_only record mapped to a commercial licence, or an unlisted record mapped to
    a commercial licence other than its `unlisted_open` (contributor -> licence id) entry. `withheld` is
    licence id -> originator patterns, `record_origin` record id -> (GESLA ORIGINATOR, UHSLC Data_Source):
    a record missing from it, or whose originator matches a withheld switch's patterns while its licence
    is not withheld, stops the build."""
    unlisted_open = dict(unlisted_open or {})
    overrides = dict(overrides or {})
    withheld = dict(withheld or {})
    originators = dict(originators or {})
    record_origin = record_origin if record_origin is not None else {}
    unknown = sorted(r for r in overrides if r not in licence_class)
    if unknown:
        raise ValueError(f"licence overrides name records not in the GESLA inventory: {unknown[:10]}")
    known = set(commercial)
    out: dict[str, str] = {}
    for d in docs:
        rid, contributor = d["record_id"], d.get("contributor")
        if contributor not in by_contributor:
            raise ValueError(f"{rid}: contributor {contributor!r} is not in contributor_roles.csv "
                             "(no licence is assumed)")
        lid = by_contributor[contributor]
        if rid in overrides:
            want, over, reason = overrides[rid]
            if want != contributor:
                raise ValueError(f"{rid}: the licence override names contributor {want!r}, the record's is "
                                 f"{contributor!r}")
            if over not in known:
                raise ValueError(f"{rid}: licence {over!r} (override) has no licence row")
            if lid in withheld and over not in withheld:
                raise ValueError(f"{rid}: the override to {over!r} moves it out of the withheld licence {lid!r}")
            if commercial[over]:
                actual = " ".join(record_origin.get(rid, ("", "")))
                if not any(n in actual for n in originators.get(over, ())):
                    raise ValueError(f"{rid}: the override to the commercial licence {over!r}: the record's own "
                                     f"originator {actual.strip()!r} is no documented originator of {over!r}")
                if "GESLA ORIGINATOR" not in reason:
                    raise ValueError(f"{rid}: the override to the commercial licence {over!r} does not quote the "
                                     "GESLA ORIGINATOR in its reason")
            lid = over
        if lid not in known:
            raise ValueError(f"{rid}: licence {lid!r} (contributor {contributor}) has no licence row")
        if rid in out:
            raise ValueError(f"{rid}: record listed twice")
        if rid not in licence_class:
            raise ValueError(f"{rid}: not in the GESLA inventory (its licence class cannot be checked)")
        cls = licence_class[rid]
        if cls not in KNOWN_CLASSES:
            raise ValueError(f"{rid}: unknown GESLA licence class {cls!r} (contributor {contributor}); "
                             f"known: {list(KNOWN_CLASSES)}")
        allowed = cls == "unlisted" and unlisted_open.get(contributor) == lid
        if cls in NON_COMMERCIAL_CLASSES and commercial[lid] and not allowed:
            raise ValueError(f"{rid}: GESLA licence class {licence_class[rid]!r} (contributor {contributor}) maps to "
                             f"the commercial licence {lid!r}")
        if rid not in record_origin:
            raise ValueError(f"{rid}: not in record_originators.csv (its originator cannot be checked)")
        matched = {wlid: next(t for t in record_origin[rid] for p in pats if t and p.search(t))
                   for wlid, pats in withheld.items()
                   if any(t and p.search(t) for t in record_origin[rid] for p in pats)}
        if matched and lid not in matched:
            _, hit = next(iter(sorted(matched.items())))
            raise ValueError(f"{rid}: its originator {hit[:120]!r} names the withheld agency of {sorted(matched)}, "
                             f"but its licence {lid!r} is not {'that one' if lid in withheld else 'withheld'}")
        for rlid, (contribs, pats) in (required or {}).items():
            if (contributor in contribs and not matched and lid != rlid
                    and any(t and p.search(t) for t in record_origin[rid] for p in pats)):
                raise ValueError(f"{rid}: its originator calls for the licence {rlid!r} (originator_licences), "
                                 f"but it has {lid!r}")
        out[rid] = lid
    return out


def gesla_licences(docs: Iterable[Mapping], *, roles_path: Path = ROLES_CSV, licences_path: Path = LICENCES_JSON,
                   inventory_path: Path = INVENTORY_CSV,
                   overrides_path: Path = OVERRIDES_CSV,
                   originators_path: Path = ORIGINATORS_CSV) -> tuple[list[dict], dict[str, str]]:
    """(licence rows, record id -> licence id) for build_release(licences=, licence_ids=)."""
    rows = load_licences(licences_path)
    by_contributor = contributor_licences(roles_path)
    ids = {r["licence_id"] for r in rows}
    missing = sorted({lid for lid in by_contributor.values() if lid not in ids})
    if missing:
        raise ValueError(f"{roles_path}: licence ids with no row in {licences_path}: {missing}")
    commercial = {r["licence_id"]: r["commercial_use"] for r in rows}
    return rows, record_licence_ids(docs, by_contributor, commercial, inventory_classes(inventory_path),
                                    load_unlisted_open(licences_path), record_overrides(overrides_path),
                                    withheld_patterns(licences_path), licence_originators(licences_path),
                                    record_originators(originators_path), originator_licences(licences_path))
