"""A format 1.0 release document from GESLA fit documents (the GESLA side of the release build).

    build_release(docs, ...) -> (release, BuildReport)   the release is valid input for write_release;
                                                         the report lists every record left out, by reason

One active reference station per record, its one gesla-fit set recommended. The pieces another
slot owns come in as arguments, never made up here:

- `station_ids` (record id -> OTC station id): the frozen registry and station map (G07a/G07c,
  A-GES), which also merges records at one gauge into one station.
- `licences` and `licence_ids` (record id -> licence id): the per-contributor licences (L-NC).
  Required for every record: a record with no licence id stops the build (no default licence).
- `astro_tables`: the per-year V0+u and f tables (KP0b). Format 1.0 requires them whenever a
  convention predicts with f and u, and the gesla-fit convention does, so a release cannot be
  built without them.
- datums beyond Z0 (DT4a/DT5f/DT5g/DT6): `gesla_sets.source_set` writes only `msl_offset_m`.

The build checks the schema cannot express (its top description, checks 1-14) are not all here:
`release_writer.validate_release` runs the cross-reference checks; the astronomical-table checks
are KP0b's and the datum checks 9-13 are DT7's.
"""
from __future__ import annotations

import json
import os
import secrets
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from gesla.tcd_table import TcdTable

from otc_pipeline.gesla_sets import (CONVENTION_ID, FIT_MIN_GOOD_HOURS, SOURCE, FitIntegrityError, NotPublishable,
                                     licence_of, refusal, source_set)
from otc_pipeline.release_writer import FORMAT_VERSION

__all__ = ["BuildReport", "build_release", "gesla_convention", "station_timezone"]

_TZ = None


def station_timezone(lat: float, lon: float) -> str:
    """IANA zone at the gauge (timezonefinder; the ocean zones Etc/GMT±N at sea). Raises when there is none."""
    global _TZ
    if _TZ is None:
        from timezonefinder import TimezoneFinder
        _TZ = TimezoneFinder()
    tz = _TZ.timezone_at(lng=lon, lat=lat)
    if not tz:
        raise ValueError(f"no time zone at {lat}, {lon}")
    return tz


def gesla_convention(table_id: str, table: TcdTable) -> dict:
    """The gesla-fit convention: Greenwich phases fitted with the TCD's V0+u and f (fit.lsq), so a
    reader predicts with f and u from the astro table `table_id` made from the same TCD."""
    return {"convention_id": CONVENTION_ID, "phase_reference": "greenwich_utc", "v0_model": "schureman_tcd",
            "nodal_handling": "f_u_at_prediction", "constituent_table_version": table_id,
            "tables_sha256": table.tcd_sha256, "astro_table_id": table_id}


@dataclass
class BuildReport:
    """What a build published and what it left out, and why: every input record is in exactly one list.
    `refused` entries are gesla_sets.refusal() dicts (record, reason class, message, good hours)."""
    published: list[str] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(r["reason"] for r in self.refused).items()))

    def as_dict(self) -> dict:
        return {"published": len(self.published), "refused": len(self.refused), "refused_by_reason": self.counts(),
                "records": {"published": sorted(self.published),
                            "refused": sorted(self.refused, key=lambda r: r["record"])}}

    def write(self, path: Path) -> Path:
        """The report as JSON, written next to the release files (never inside the dated stem)."""
        path = Path(path)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with open(tmp, "xb") as fh:
                fh.write((json.dumps(self.as_dict(), sort_keys=True, indent=1) + "\n").encode())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    def summary(self) -> str:
        return f"published {len(self.published)}, refused {len(self.refused)} {self.counts()}"


def build_release(docs: Iterable[Mapping], *, table: TcdTable, table_id: str, datestamp: str, created: str,
                  build_commit: str, station_ids: Mapping[str, str], licences: list[dict],
                  licence_ids: Mapping[str, str], astro_tables: list[dict] | None,
                  refused: Iterable[Mapping]) -> tuple[dict, BuildReport]:
    """The release document for these fit documents, and the build report. A record this build cannot
    publish yet (gesla_sets.NotPublishable: a short record, a bad constituent name) is left out and
    reported by reason; `refused` (required, [] when there are none) carries the fit stage's refusals
    (fit_records, docs_from_fit_run) into the same report. Raises ValueError when an input the build needs is missing (astro tables, a
    record with no station id or no licence id: no licence is assumed), and FitIntegrityError when a
    document was fitted with another TCD than `table` or carries an unstamped attenuated fit."""
    if not astro_tables:
        raise ValueError("astro_tables is required: the gesla-fit convention predicts with f and u "
                         "(format 1.0, astro_tables_when_used); the per-year tables come from KP0b")
    lic_by_id = {lic["licence_id"]: lic for lic in licences}
    report = BuildReport(refused=[dict(r) for r in refused])
    stations, used_names, versions = [], set(), set()
    for doc in sorted(docs, key=lambda d: d["record_id"]):
        rid = doc["record_id"]
        if rid not in station_ids:
            raise ValueError(f"{rid}: no station id (the station map assigns it)")
        lic_id = licence_of(licence_ids, rid)
        if lic_id not in lic_by_id:
            raise ValueError(f"{rid}: licence {lic_id!r} is not in licences")
        if doc["tcd_sha256"] != table.tcd_sha256:
            raise FitIntegrityError(f"{rid}: fitted with TCD {doc['tcd_sha256']}, the release's tables are "
                                    f"{table.tcd_sha256}")
        try:
            ss = source_set(doc, licence_id=lic_id)
        except NotPublishable as e:
            report.refused.append(refusal(rid, e.reason, str(e), doc.get("good_hours")))
            continue
        versions.add(doc["source_version"])
        sid = station_ids[rid]
        cs = {**ss.constant_set, "set_id": f"{sid}/{SOURCE}"}
        cs["provenance"] = {**cs["provenance"], "build": {"build_commit": build_commit}}
        used_names.update(c["name"] for c in cs["constituents"])
        hint = ss.station_hint
        stations.append({"station_id": sid, "status": "active", "name": hint["name"], "country": hint["country"],
                         "lat": hint["lat"], "lon": hint["lon"], "timezone": station_timezone(hint["lat"], hint["lon"]),
                         "type": "reference", "aliases": hint["aliases"], "recommended_set_id": cs["set_id"],
                         "constant_sets": [cs]})
        report.published.append(rid)
    seen = [r["record"] for r in report.refused] + report.published
    dup = sorted({r for r in seen if seen.count(r) > 1})
    if dup:
        raise ValueError(f"records both published and refused, or refused twice: {dup[:10]}")
    if not stations:
        raise ValueError(f"no record is publishable ({report.summary()})")
    if len(versions) != 1:
        raise ValueError(f"the documents come from GESLA versions {sorted(versions)}: one build, one version")
    licence_rows = [lic_by_id[i] for i in sorted({s["constant_sets"][0]["licence_id"] for s in stations})]
    release = {
        "format_version": FORMAT_VERSION,
        "release": {"datestamp": datestamp, "created": created, "min_reader_version": FORMAT_VERSION,
                    "fit_min_good_hours": FIT_MIN_GOOD_HOURS, "concept_doi": None,
                    "source_versions": {"gesla": versions.pop()}, "build_commit": build_commit},
        "conventions": [gesla_convention(table_id, table)],
        "licences": licence_rows,
        "constituents": [{"constituent_table_version": table_id, "name": n, "speed_deg_per_hour": table.speed(n)}
                         for n in sorted(used_names)],
        "astro_tables": list(astro_tables),
        "stations": stations,
    }
    return release, report
