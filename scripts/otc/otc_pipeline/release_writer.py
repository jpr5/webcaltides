"""OTC release writer and validator (format 1.0).

The release files of one release share the stem OTC_{DATESTAMP} (publication
plan §2.3, §2.4):

    .json        the release document (primary format)
    .json.gz     gzip of the .json file
    .jsonl       one station document per line
    .jsonl.gz    optional: gzip of the .jsonl file
    .meta.json   the document without `stations` (valid against #/$defs/meta): conventions,
                 licences, the constituent table and the astro tables
    .csv         one long table with a `record_type` column (RFC 4180, UTF-8, LF)
    .parquet     optional: the CSV table, typed
    .app.json    optional: the webcaltides `app` export
    .sha256      SHA-256 of every other file, in `sha256sum` format; written last

Identical input gives byte-identical files: JSON keys are sorted, separators are
fixed, floats use Python's shortest round-trip repr, and gzip carries no time
stamp or file name. A dated file is never overwritten.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import secrets
import sys
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError, validators

from otc_pipeline.astro_tables import CongenRun, astro_table_errors, release_table_errors

__all__ = ["BuiltRelease", "CSV_COLUMNS", "FORMAT_VERSION", "SCHEMA_SHA256", "SchemaPinError", "check_release_dir",
           "next_datestamp", "pinned_schema", "validate_release", "write_release"]

# Use with fullmatch: in Python `$` also matches before a trailing "\n".
DATESTAMP_RE = re.compile(r"[0-9]{8}(\.[2-9]|\.[1-9][0-9]+)?")
# OTC_<8 digits>[.<counter>].<extension starting with a letter>
STEM_RE = re.compile(r"^OTC_([0-9]{8})(?:\.([0-9]+))?\.[A-Za-z]")

# --- CSV layout ---------------------------------------------------------------
#
# One row per record. `record_type` is one of station, tombstone, offset,
# current_offset, constant_set, constituent, current_bin, current_constituent,
# convention, validation. A column that does not
# apply to a row type is empty. Columns marked JSON hold the value as a compact
# JSON string with sorted keys (nested data such as provenance).
#
# The release-level tables (licences, constituents, astro_tables) are not rows: they are in the
# .meta.json file. A datum (a constant set's, or subordinate_offsets') is split into the datum_*
# columns of its row (datums spec §3.7).
#
# Row order: every convention (document order), then for each station (document
# order): its station or tombstone row, its offset row or current_offset rows,
# then each constant set followed by its constituents (a current set: each
# current_bin row followed by its current_constituent rows), then its validation
# rows. A current_constituent row names its bin in `bin`.

CSV_COLUMNS = [
    "record_type",
    # keys shared by several row types
    "station_id", "set_id", "name", "status", "convention_id", "licence_id",
    # the licence's commercial_use (true/false), on every row that has a licence_id (L-NC)
    "commercial_use",
    # station
    "country", "lat", "lon", "timezone", "type", "aliases", "recommended_set_id", "recommended_is_fallback",
    "default_current_bin",
    # tombstone
    "removed_in", "removed_reason", "merged_into", "detail",
    # offset (subordinate_offsets)
    "reference_station_id", "time_offset_high_min", "time_offset_low_min",
    "height_offset_high", "height_offset_low", "height_adjusted_type",
    # datum (constant_set and offset rows)
    "datum_msl_offset_m", "datum_zero", "datum_zero_name", "datum_chart_datum", "datum_named", "datum_basis",
    # current_offset (one per bin of a subordinate current station)
    "bin", "depth_m", "depth_type", "reference_bin", "mean_flood_dir_deg", "mean_ebb_dir_deg",
    "time_adj_max_flood_min", "time_adj_slack_before_ebb_min", "time_adj_max_ebb_min",
    "time_adj_slack_before_flood_min", "flood_amp_ratio", "ebb_amp_ratio",
    # current_bin (also bin, depth_m, depth_type, mean_flood_dir_deg, mean_ebb_dir_deg)
    "azimuth_deg", "mean_major_ms", "mean_minor_ms",
    # constant_set
    "source", "source_type", "quantity", "source_record_id", "source_version",
    "record_span_start", "record_span_end", "good_samples", "good_hours",
    "qc_status", "qc_flags", "no_constants_reason", "time_base", "record_issues", "fit_segment",
    "expected_accuracy", "dropped_constituents", "provenance", "fallback",
    # constituent
    "source_name", "doodson", "speed_deg_per_hour", "amplitude_m", "phase_deg",
    "amp_uncertainty_m", "phase_uncertainty_deg", "kept_reason",
    # current_constituent (also name, source_name, speed_deg_per_hour)
    "major_amplitude_ms", "major_phase_deg", "minor_amplitude_ms", "minor_phase_deg",
    # convention
    "phase_reference", "utc_offset_hours", "v0_model", "nodal_handling", "nodal_formula_ids",
    "constituent_table_version", "tables_sha256", "astro_table_id", "canary",
    # validation
    "reference_source", "reference_station", "reference_distance_km", "window",
    "time_mae_min", "time_p95_min", "time_bias_min", "height_mae_m", "range_error_m",
    "missed_events", "extra_events", "previous_release",
]

JSON_COLUMNS = {"aliases", "datum_named", "datum_basis", "qc_flags", "dropped_constituents", "provenance",
                "nodal_formula_ids", "canary", "previous_release", "time_base", "record_issues",
                "fit_segment", "expected_accuracy", "fallback"}
FLOAT_COLUMNS = {"lat", "lon", "time_offset_high_min", "time_offset_low_min", "height_offset_high",
                 "height_offset_low", "datum_msl_offset_m", "speed_deg_per_hour", "amplitude_m",
                 "phase_deg", "amp_uncertainty_m", "phase_uncertainty_deg", "utc_offset_hours",
                 "reference_distance_km", "time_mae_min", "time_p95_min", "time_bias_min",
                 "height_mae_m", "range_error_m", "depth_m", "mean_flood_dir_deg",
                 "mean_ebb_dir_deg", "time_adj_max_flood_min", "time_adj_slack_before_ebb_min",
                 "time_adj_max_ebb_min", "time_adj_slack_before_flood_min", "flood_amp_ratio",
                 "ebb_amp_ratio", "azimuth_deg", "major_amplitude_ms", "major_phase_deg",
                 "minor_amplitude_ms", "minor_phase_deg", "mean_major_ms", "mean_minor_ms", "good_hours"}
INT_COLUMNS = {"good_samples", "missed_events", "extra_events", "default_current_bin", "bin",
               "reference_bin"}

# Keys of each record that map one-to-one onto a column of the same name.
# Keys handled specially (children, split objects) are listed in _CHILD_KEYS.
_ROW_KEYS = {
    "station": ["station_id", "status", "name", "country", "lat", "lon", "timezone", "type",
                "aliases", "recommended_set_id", "recommended_is_fallback", "default_current_bin"],
    "tombstone": ["station_id", "status", "name", "removed_in", "removed_reason", "merged_into", "detail"],
    "offset": ["reference_station_id", "time_offset_high_min", "time_offset_low_min",
               "height_offset_high", "height_offset_low", "height_adjusted_type", "licence_id"],
    "current_offset": ["bin", "depth_m", "depth_type", "reference_station_id", "reference_bin",
                       "mean_flood_dir_deg", "mean_ebb_dir_deg", "time_adj_max_flood_min",
                       "time_adj_slack_before_ebb_min", "time_adj_max_ebb_min",
                       "time_adj_slack_before_flood_min", "flood_amp_ratio", "ebb_amp_ratio",
                       "licence_id"],
    "current_bin": ["bin", "depth_m", "depth_type", "azimuth_deg", "mean_flood_dir_deg",
                    "mean_ebb_dir_deg", "mean_major_ms", "mean_minor_ms"],
    "current_constituent": ["name", "source_name", "speed_deg_per_hour", "major_amplitude_ms",
                            "major_phase_deg", "minor_amplitude_ms", "minor_phase_deg"],
    "constant_set": ["set_id", "source", "source_type", "quantity", "source_record_id",
                     "source_version", "convention_id", "licence_id", "qc_status", "qc_flags",
                     "no_constants_reason", "time_base", "record_issues", "fit_segment",
                     "expected_accuracy", "dropped_constituents", "provenance", "fallback"],
    "constituent": ["name", "source_name", "doodson", "speed_deg_per_hour", "amplitude_m",
                    "phase_deg", "amp_uncertainty_m", "phase_uncertainty_deg", "kept_reason"],
    "convention": ["convention_id", "phase_reference", "utc_offset_hours", "v0_model",
                   "nodal_handling", "nodal_formula_ids", "constituent_table_version",
                   "tables_sha256", "astro_table_id", "canary"],
    "validation": ["set_id", "reference_source", "reference_station", "reference_distance_km",
                   "window", "time_mae_min", "time_p95_min", "time_bias_min", "height_mae_m",
                   "range_error_m", "missed_events", "extra_events", "previous_release"],
}
_CHILD_KEYS = {
    "station": {"constant_sets", "subordinate_offsets", "current_offsets", "validation"},
    "offset": {"datum"},
    "constant_set": {"constituents", "current_bins", "record_span", "datum"},
    "current_bin": {"constituents"},
}

FORMAT_VERSION = "1.0"
SCHEMA_NAME = f"otc-{FORMAT_VERSION}.schema.json"
# SHA-256 of the site's src/schema/otc-1.0.schema.json at the commit sync_schema.sh pins
# (ed10d6c, approved labelled publication fallback contract). sync_schema.sh holds the same value; a test keeps the two equal.
SCHEMA_SHA256 = "d03e887cf122dfe5f693d0816e4261825f242923dc1d78039ea86bc840c62534"
_MISSING = object()  # "key not present"; never equal to a value read from JSON


class SchemaPinError(RuntimeError):
    """The schema copy is not the pinned one: the writer does not validate against it."""


def _schema_file() -> Path:
    """Where the schema copy is: inside the package in a wheel (force-included), else scripts/otc/schema/."""
    here = Path(__file__).resolve().parent
    packaged = here / "schema" / SCHEMA_NAME
    return packaged if packaged.is_file() else here.parent / "schema" / SCHEMA_NAME


def pinned_schema() -> dict:
    """The schema copy, parsed from the same bytes whose SHA-256 is checked against SCHEMA_SHA256 (one
    read). Raises SchemaPinError otherwise. This is the only schema the writer validates against."""
    path = _schema_file()
    try:
        data = path.read_bytes()
    except OSError as e:
        raise SchemaPinError(f"{path}: cannot read the schema copy: {e}") from e
    got = hashlib.sha256(data).hexdigest()
    if got != SCHEMA_SHA256:
        raise SchemaPinError(f"{path} has SHA-256 {got}, the pin is {SCHEMA_SHA256} (run sync_schema.sh)")
    return json.loads(data.decode("utf-8"))


# --- datestamps -----------------------------------------------------------------

def _stems(out_dir: Path) -> set[tuple[str, int]]:
    """(date, counter) of every OTC_{DATESTAMP}.* file in out_dir; no counter = 1."""
    found = set()
    if not out_dir.is_dir():
        return found
    for p in out_dir.iterdir():
        m = STEM_RE.match(p.name)
        if m:
            found.add((m.group(1), int(m.group(2)) if m.group(2) else 1))
    return found


def _parse_datestamp(datestamp: str) -> tuple[str, int]:
    day, _, counter = datestamp.partition(".")
    return day, int(counter) if counter else 1


def next_datestamp(out_dir: Path, utc_date: date) -> str:
    """'20261008', or '20261008.2', '20261008.3' ... if that stem already exists in out_dir. Never overwrites."""
    day = utc_date.strftime("%Y%m%d")
    used = [n for d, n in _stems(Path(out_dir)) if d == day]
    if not used:
        return day
    return f"{day}.{max(used) + 1}"


# --- validation ------------------------------------------------------------------

def _leaf_errors(error):
    """Expand a oneOf/anyOf error into the errors of the branch that applies.

    A branch that fails a `const` (the station `status` discriminator) does not
    apply. If no branch is left, every branch's errors are reported.
    """
    if not error.context:
        yield error
        return
    branches: dict[int, list] = {}
    for sub in error.context:
        branches.setdefault(sub.schema_path[0], []).append(sub)
    applicable = [errs for errs in branches.values() if not any(e.validator == "const" for e in errs)]
    for errs in (applicable or list(branches.values())):
        for sub in errs:
            yield from _leaf_errors(sub)


def _ecma_pattern(patrn: str) -> str:
    """JSON Schema patterns are ECMA-262: without the multiline flag `$` matches only at the end of
    the string. In Python `$` also matches before a trailing "\n", so a final unescaped `$` becomes `\\Z`.
    (Schema 0.2 anchored every pattern as ^...$; 0.3 ends them with $(?![\\s\\S]), which needs no rewrite.)"""
    if patrn.endswith("$") and (len(patrn) - len(patrn[:-1].rstrip("\\")) - 1) % 2 == 0:
        return patrn[:-1] + r"\Z"
    return patrn


def _pattern_keyword(validator, patrn, instance, schema):
    if validator.is_type(instance, "string") and not re.search(_ecma_pattern(patrn), instance):
        yield ValidationError(f"{instance!r} does not match {patrn!r}")


_Validator = validators.extend(Draft202012Validator, {"pattern": _pattern_keyword})


def _path(parts) -> str:
    return "/".join(str(p) for p in parts) or "(root)"


def _non_json_values(node, path, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                out.append(f"{_path(path)}: key {k!r} is not a string")
            _non_json_values(v, path + [k], out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _non_json_values(v, path + [i], out)
    elif isinstance(node, float):
        if not math.isfinite(node):
            out.append(f"{_path(path)}: {node!r} is not a finite number")
    elif node is not None and not isinstance(node, (str, int, bool)):
        out.append(f"{_path(path)}: {type(node).__name__} is not a JSON value")


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _dicts(value) -> list[dict]:
    return [x for x in _list(value) if isinstance(x, dict)]


def _cross_reference_errors(release: dict) -> list[str]:
    """Checks the schema cannot express. Schema-invalid parts are skipped, not trusted: an id is
    only looked up when it is a string and a container is only walked when it has the right type
    (the schema pass reports the rest)."""
    errors: list[str] = []

    def ids(items, key, what):
        seen = {}
        for item in _dicts(items):
            value = item.get(key)
            if isinstance(value, str):
                if value in seen:
                    errors.append(f"duplicate {what} {value!r}")
                seen.setdefault(value, item)
        return seen

    conventions = ids(release.get("conventions"), "convention_id", "convention_id")
    licences = ids(release.get("licences"), "licence_id", "licence_id")
    table: set[tuple[str, str]] = set()
    for row in _dicts(release.get("constituents")):
        key = (row.get("constituent_table_version"), row.get("name"))
        if not all(isinstance(k, str) for k in key):
            continue
        if key in table:
            errors.append(f"duplicate constituent table row {key[1]!r} (version {key[0]!r})")
        table.add(key)

    stations = _dicts(release.get("stations"))
    by_id = ids(stations, "station_id", "station_id")
    set_ids: set[str] = set()
    alias_owner: dict[tuple[str, str], object] = {}
    current_refs: list[tuple[int, dict, str]] = []

    for si, st in enumerate(stations):
        sid = st.get("station_id")
        where = f"stations/{si} ({sid})"
        if st.get("status") != "active":
            continue
        station_sets = _dicts(st.get("constant_sets"))
        own_by_id = {cs["set_id"]: cs for cs in station_sets if isinstance(cs.get("set_id"), str)}
        own_sets = set()
        for ki, cs in enumerate(station_sets):
            sw = f"{where} constant_sets/{ki} ({cs.get('set_id')})"
            set_id = cs.get("set_id")
            if isinstance(set_id, str):
                if set_id in set_ids:
                    errors.append(f"duplicate set_id {set_id!r}")
                set_ids.add(set_id)
                own_sets.add(set_id)
            fallback = cs.get("fallback")
            preferred_id = fallback.get("preferred_set_id") if isinstance(fallback, dict) else None
            if isinstance(preferred_id, str):
                preferred = own_by_id.get(preferred_id)
                if preferred is None or "fallback" in preferred:
                    errors.append(f"{sw}: fallback preferred_set_id {preferred_id!r} must name a "
                                  "non-fallback set of this station")
            conv_id = cs.get("convention_id")
            conv = conventions.get(conv_id) if isinstance(conv_id, str) else None
            if isinstance(conv_id, str) and conv is None:
                errors.append(f"{sw}: convention_id {conv_id!r} is not defined in conventions")
            lic_id = cs.get("licence_id")
            if isinstance(lic_id, str) and lic_id not in licences:
                errors.append(f"{sw}: licence_id {lic_id!r} is not defined in licences")
            version = conv.get("constituent_table_version") if conv is not None else None
            _current_bin_errors(cs, sw, version, table, errors)
            names_seen = set()
            # Only the constituents a set uses need a table row; a dropped one (format 1.0: the
            # table has the constituents the release uses) is checked by the schema's name rule.
            for c in _dicts(cs.get("constituents")):
                name = c.get("name")
                if not isinstance(name, str):
                    continue
                if name in names_seen:
                    errors.append(f"{sw}: duplicate constituent {name!r}")
                names_seen.add(name)
                if isinstance(version, str) and (version, name) not in table:
                    errors.append(f"{sw}: constituents name {name!r} is not in the constituents "
                                  f"table for constituent_table_version {version!r}")
        rec = st.get("recommended_set_id", _MISSING)
        off = st.get("subordinate_offsets")
        cur_off = _dicts(st.get("current_offsets"))
        if isinstance(off, dict) and cur_off:
            errors.append(f"{where}: a station has subordinate_offsets or current_offsets, not both")
        if rec is None:
            sets = _dicts(st.get("constant_sets"))
            if st.get("type") != "subordinate":
                if not sets or any(cs.get("qc_status") != "no_constants" for cs in sets):
                    errors.append(f"{where}: recommended_set_id is null but the station is not subordinate "
                                  "and not every set is no_constants (build check 6)")
            elif not isinstance(off, dict) and not cur_off:
                errors.append(f"{where}: recommended_set_id is null but the station has no "
                              "subordinate_offsets or current_offsets (it cannot be predicted)")
        elif isinstance(rec, str) and rec not in own_sets:
            errors.append(f"{where}: recommended_set_id {rec!r} is not a set of this station")
        recommended = own_by_id.get(rec) if isinstance(rec, str) else None
        if recommended is not None and "fallback" in recommended:
            if st.get("recommended_is_fallback") is not True:
                errors.append(f"{where}: recommended_is_fallback must be true for a fallback recommendation")
        elif "recommended_is_fallback" in st:
            errors.append(f"{where}: recommended_is_fallback must be omitted for a non-fallback recommendation")
        if isinstance(off, dict):
            ref = off.get("reference_station_id")
            target = by_id.get(ref) if isinstance(ref, str) else None
            if isinstance(ref, str) and ref == sid:
                errors.append(f"{where}: subordinate_offsets reference_station_id {ref!r} is the station itself")
            elif not isinstance(target, dict) or target.get("status") != "active":
                errors.append(f"{where}: subordinate_offsets reference_station_id {ref!r} "
                              "is not an active station in this release")
            elif target.get("type") != "reference":
                errors.append(f"{where}: subordinate_offsets reference_station_id {ref!r} "
                              "is not a reference station")
            off_lic = off.get("licence_id")
            if isinstance(off_lic, str) and off_lic not in licences:
                errors.append(f"{where}: subordinate_offsets licence_id {off_lic!r} "
                              "is not defined in licences")
        if cur_off or "default_current_bin" in st:
            current_refs.append((si, st, where))
        for vi, v in enumerate(_dicts(st.get("validation"))):
            v_set = v.get("set_id")
            if isinstance(v_set, str) and v_set not in own_sets:
                errors.append(f"{where} validation/{vi}: set_id {v_set!r} is not a set of this station")
        aliases = st.get("aliases")
        if isinstance(aliases, dict):
            for system, values in sorted(aliases.items()):
                for alias in values if isinstance(values, list) else [values]:
                    if not isinstance(alias, str):
                        continue
                    owner = alias_owner.setdefault((system, alias), sid)
                    if owner != sid:
                        errors.append(f"duplicate alias {system}:{alias} ({owner}, {sid})")
    for _si, st, where in current_refs:
        _current_station_errors(st, where, by_id, licences, errors)
    return errors


def _bins_of(st: dict) -> set:
    """Bin numbers of a station's recommended set (a current set), or empty."""
    rec = st.get("recommended_set_id")
    for cs in _dicts(st.get("constant_sets")):
        if isinstance(rec, str) and cs.get("set_id") == rec:
            return {b.get("bin") for b in _dicts(cs.get("current_bins"))}
    return set()


def _current_bin_errors(cs: dict, sw: str, version, table: set, errors: list[str]) -> None:
    """A current set's bins: unique bin numbers, unique constituent names per bin, names in the table."""
    bins_seen = set()
    for b in _dicts(cs.get("current_bins")):
        n = b.get("bin")
        bw = f"{sw} bin {n}"
        if n in bins_seen:
            errors.append(f"{sw}: duplicate current bin {n!r}")
        bins_seen.add(n)
        names = set()
        for c in _dicts(b.get("constituents")):
            name = c.get("name")
            if not isinstance(name, str):
                continue
            if name in names:
                errors.append(f"{bw}: duplicate constituent {name!r}")
            names.add(name)
            if isinstance(version, str) and (version, name) not in table:
                errors.append(f"{bw}: constituent name {name!r} is not in the constituents table "
                              f"for constituent_table_version {version!r}")


def _current_station_errors(st: dict, where: str, by_id: dict, licences: dict,
                            errors: list[str]) -> None:
    """current_offsets name an active reference station and one of its bins; default_current_bin
    names a bin of the recommended set (reference) or of current_offsets (subordinate)."""
    sid = st.get("station_id")
    cur_off = _dicts(st.get("current_offsets"))
    own_bins = set()
    for co in cur_off:
        n = co.get("bin")
        cw = f"{where} current_offsets bin {n}"
        if n in own_bins:
            errors.append(f"{where}: duplicate current_offsets bin {n!r}")
        own_bins.add(n)
        ref = co.get("reference_station_id")
        target = by_id.get(ref) if isinstance(ref, str) else None
        if isinstance(ref, str) and ref == sid:
            errors.append(f"{cw}: reference_station_id {ref!r} is the station itself")
        elif not isinstance(target, dict) or target.get("status") != "active":
            errors.append(f"{cw}: reference_station_id {ref!r} is not an active station in this release")
        elif target.get("type") != "reference":
            errors.append(f"{cw}: reference_station_id {ref!r} is not a reference station")
        elif co.get("reference_bin") not in _bins_of(target):
            errors.append(f"{cw}: reference_bin {co.get('reference_bin')!r} is not a bin of "
                          f"{ref!r}'s recommended set")
        lic = co.get("licence_id")
        if isinstance(lic, str) and lic not in licences:
            errors.append(f"{cw}: licence_id {lic!r} is not defined in licences")
    if "default_current_bin" in st:
        dcb = st["default_current_bin"]
        bins = own_bins if cur_off else _bins_of(st)
        if dcb not in bins:
            errors.append(f"{where}: default_current_bin {dcb!r} is not a bin of the "
                          f"{'current_offsets' if cur_off else 'recommended set'}")


def validate_release(release: dict) -> list[str]:
    """Errors against the pinned schema (pinned_schema: raises SchemaPinError off the pin; no other
    schema can be passed) + cross-reference errors (convention_id, licence_id,
    recommended_set_id, reference_station_id, astro_table_id) + duplicate alias ids + name maxLength
    + build check 7 (astro_tables.astro_table_errors). Empty list = valid."""
    schema = pinned_schema()
    if not isinstance(release, dict):
        return [f"(root): release must be a JSON object, not {type(release).__name__}"]
    errors: list[str] = []
    _non_json_values(release, [], errors)
    if errors:
        return errors
    validator = _Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    schema_errors = set()
    for top in validator.iter_errors(release):
        for e in _leaf_errors(top):
            schema_errors.add((tuple(str(p) for p in e.absolute_path), f"{_path(e.absolute_path)}: {e.message}"))
    errors += [msg for _, msg in sorted(schema_errors)]
    errors += _cross_reference_errors(release)
    errors += astro_table_errors(release)
    return errors


# --- serialisation ---------------------------------------------------------------

def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _gzip(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def _row(record_type: str, record: dict, extra: dict | None = None) -> dict:
    row = dict(extra or {})
    row["record_type"] = record_type
    known = set(_ROW_KEYS[record_type]) | _CHILD_KEYS.get(record_type, set())
    unknown = sorted(set(record) - known)
    if unknown:
        raise ValueError(f"CSV layout has no column for {record_type} keys {unknown}")
    for key in _ROW_KEYS[record_type]:
        if key in record:
            row[key] = record[key]
    return row


_DATUM_COLUMNS = {"msl_offset_m": "datum_msl_offset_m", "zero": "datum_zero", "zero_name": "datum_zero_name",
                  "chart_datum": "datum_chart_datum", "named": "datum_named", "basis": "datum_basis"}


def _datum_cells(datum: dict, extra: dict) -> dict:
    """A datum object as the datum_* columns of its row; a key with no column raises."""
    unknown = sorted(set(datum) - set(_DATUM_COLUMNS))
    if unknown:
        raise ValueError(f"CSV layout has no column for datum keys {unknown}")
    for key, col in _DATUM_COLUMNS.items():
        if key in datum:
            extra[col] = datum[key]
    return extra


def _csv_rows(release: dict) -> list[dict]:
    rows = [_row("convention", c) for c in release["conventions"]]
    for st in release["stations"]:
        sid = st["station_id"]
        if st["status"] == "removed":
            rows.append(_row("tombstone", st))
            continue
        rows.append(_row("station", st))
        if "subordinate_offsets" in st:
            off = st["subordinate_offsets"]
            rows.append(_row("offset", off, _datum_cells(off.get("datum", {}), {"station_id": sid})))
        for co in st.get("current_offsets", []):
            rows.append(_row("current_offset", co, {"station_id": sid}))
        for cs in st["constant_sets"]:
            extra = {"station_id": sid}
            span = cs.get("record_span", {})
            for key, col in (("start", "record_span_start"), ("end", "record_span_end"),
                             ("good_samples", "good_samples"), ("good_hours", "good_hours")):
                if key in span:
                    extra[col] = span[key]
            _datum_cells(cs.get("datum", {}), extra)
            rows.append(_row("constant_set", cs, extra))
            for c in cs["constituents"]:
                rows.append(_row("constituent", c, {"station_id": sid, "set_id": cs["set_id"]}))
            for b in cs.get("current_bins", []):
                ids = {"station_id": sid, "set_id": cs["set_id"]}
                rows.append(_row("current_bin", b, ids))
                for c in b["constituents"]:
                    rows.append(_row("current_constituent", c, {**ids, "bin": b["bin"]}))
        for v in st.get("validation", []):
            rows.append(_row("validation", v, {"station_id": sid}))
    commercial = {lic["licence_id"]: lic["commercial_use"] for lic in release["licences"]}
    for row in rows:
        if "licence_id" in row:
            if row["licence_id"] not in commercial:
                raise ValueError(f"{row.get('set_id') or row.get('station_id')}: licence_id {row['licence_id']!r} "
                                 "is not defined in licences")
            row["commercial_use"] = commercial[row["licence_id"]]
    return rows


def _csv_cell(column: str, value) -> str:
    if column in JSON_COLUMNS:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)
    return str(value)


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([_csv_cell(c, row[c]) if c in row else "" for c in CSV_COLUMNS])
    return buf.getvalue().encode("utf-8")


def _parquet_bytes(rows: list[dict]) -> bytes:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:  # pragma: no cover - depends on the install
        raise RuntimeError("parquet=True needs pyarrow (install the 'parquet' extra)") from e

    def typ(col):
        if col in FLOAT_COLUMNS:
            return pa.float64()
        if col in INT_COLUMNS:
            return pa.int64()
        return pa.string()

    def cell(col, row):
        if col not in row:
            return None
        v = row[col]
        if col in JSON_COLUMNS:
            return _csv_cell(col, v)
        if v is None:
            return None
        if col in FLOAT_COLUMNS:
            return float(v)
        if col in INT_COLUMNS:
            return int(v)
        return _csv_cell(col, v)

    schema = pa.schema([pa.field(c, typ(c)) for c in CSV_COLUMNS])
    arrays = [pa.array([cell(c, r) for r in rows], type=typ(c)) for c in CSV_COLUMNS]
    table = pa.Table.from_arrays(arrays, schema=schema)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    return sink.getvalue().to_pybytes()


# --- writing -----------------------------------------------------------------------

class BuiltRelease(dict):
    """A release document made by a release build (gesla_release.build_release). It is a plain dict for
    every reader and serialises exactly like one; the type is the marker, never written. write_release
    refuses congen_run=None for it, so a built release's astro tables are always checked against the
    congen run before they are written. Copies keep it (copy.deepcopy); dict(release) drops it."""


def write_release(release: dict, out_dir: Path, *, datestamp: str,
                  app_export: list[dict] | None, congen_run: CongenRun | None, parquet: bool = False,
                  jsonl_gz: bool = False) -> list[Path]:
    """Writes OTC_{D}.json, .json.gz, .jsonl, .meta.json, .csv (RFC 4180, UTF-8, LF, record_type column),
    optional .parquet, optional .app.json, then OTC_{D}.sha256 last. Refuses if validate_release is non-empty.
    Byte-identical output for identical input (sorted keys, fixed float formatting).

    `jsonl_gz=True` also writes the optional OTC_{D}.jsonl.gz. Every file is built in memory first and
    then linked into place without replacing anything; on any failure the files of this call are removed.
    `congen_run` (required): the congen run (astro_tables.load_congen_run) the release's astro tables
    come from. Every table is checked against it again here (astro_tables.release_table_errors), so a
    table changed after build_release is not written. None is only for a document that is not a release
    build's (the schema example, test fixtures): its tables are then checked by validate_release only.
    A BuiltRelease (a document from build_release) with congen_run=None is refused.

    Raises ValueError (invalid release or arguments) or FileExistsError (the stem is already used).
    """
    out_dir = Path(out_dir)
    if not isinstance(datestamp, str) or not DATESTAMP_RE.fullmatch(datestamp):
        raise ValueError(f"datestamp {datestamp!r} is not YYYYMMDD or YYYYMMDD.N (N >= 2)")
    errors = validate_release(release)
    if errors:
        raise ValueError(f"release is not valid ({len(errors)} errors):\n  " + "\n  ".join(errors))
    if congen_run is None and isinstance(release, BuiltRelease):
        raise ValueError("this release was made by a release build: write it with congen_run (its congen run), "
                         "so its astro tables are checked again; congen_run=None is only for other documents")
    if congen_run is not None:
        if not isinstance(congen_run, CongenRun):
            raise ValueError(f"congen_run must be astro_tables.load_congen_run's CongenRun, not {type(congen_run).__name__}")
        bad_tables = [e for t in release.get("astro_tables", []) for e in release_table_errors(t, *congen_run)]
        if bad_tables:
            raise ValueError(f"the astro tables are not the congen run's ({len(bad_tables)} errors): "
                             + "; ".join(bad_tables[:10]))
    if release["release"]["datestamp"] != datestamp:
        raise ValueError(f"release.datestamp {release['release']['datestamp']!r} != datestamp {datestamp!r}")
    if app_export is not None:
        if not isinstance(app_export, list) or not all(isinstance(e, dict) for e in app_export):
            raise ValueError("app_export must be a list of objects")
        bad: list[str] = []
        _non_json_values(app_export, ["app_export"], bad)
        if bad:
            raise ValueError("app_export: " + "; ".join(bad))

    stem = f"OTC_{datestamp}"
    payloads: list[tuple[str, bytes]] = []
    doc = _json_bytes(release)
    payloads.append((f"{stem}.json", doc))
    payloads.append((f"{stem}.json.gz", _gzip(doc)))
    jsonl = b"".join(_json_bytes(s) for s in release["stations"])
    payloads.append((f"{stem}.jsonl", jsonl))
    if jsonl_gz:
        payloads.append((f"{stem}.jsonl.gz", _gzip(jsonl)))
    payloads.append((f"{stem}.meta.json", _json_bytes({k: v for k, v in release.items() if k != "stations"})))
    rows = _csv_rows(release)
    payloads.append((f"{stem}.csv", _csv_bytes(rows)))
    if parquet:
        payloads.append((f"{stem}.parquet", _parquet_bytes(rows)))
    if app_export is not None:
        payloads.append((f"{stem}.app.json", _json_bytes(app_export)))
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(payloads))
    payloads.append((f"{stem}.sha256", sums.encode("ascii")))

    out_dir.mkdir(parents=True, exist_ok=True)
    if _parse_datestamp(datestamp) in _stems(out_dir):
        raise FileExistsError(f"{out_dir}: files of release {stem} already exist; a dated file is never overwritten")

    written: list[Path] = []
    temps: list[Path] = []
    try:
        for i, (name, data) in enumerate(payloads):
            final = out_dir / name
            # A random suffix: a stale temp file of an earlier run cannot collide with this one.
            tmp = out_dir / f".{name}.{secrets.token_hex(8)}.tmp"
            temps.append(tmp)
            with open(tmp, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            if i == len(payloads) - 1:
                _fsync_dir(out_dir)  # every other file is durable before the .sha256 entry appears
            os.link(tmp, final)  # fails if final exists: never replaces a file
            written.append(final)
            tmp.unlink()
            temps.remove(tmp)
        _fsync_dir(out_dir)
    except BaseException:
        for p in written + temps:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass  # keep the original error; try to remove the other files
        raise
    return written


def check_release_dir(out_dir: Path) -> list[str]:
    """Every way the release files in out_dir are not complete. A stem is complete when its .sha256 exists,
    names every other file of the stem and matches their bytes; .sha256 is written last, so a stem
    without it is a write that was killed. A leftover .OTC_*.tmp file (a killed write_release leaves
    one) is reported too, and so is a directory with no stem. A missing directory raises FileNotFoundError.
    An uploader runs this first and refuses on any entry. Empty list = complete."""
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        raise FileNotFoundError(f"{out_dir}: no such release directory")
    problems: list[str] = []
    stems: dict[str, list[Path]] = {}
    for p in sorted(out_dir.iterdir()):
        if p.name.startswith(".OTC_") and p.name.endswith(".tmp"):
            problems.append(f"{p.name}: a temp file left by an interrupted write_release")
            continue
        m = STEM_RE.match(p.name)
        if m:
            stem = f"OTC_{m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")
            stems.setdefault(stem, []).append(p)
    if not stems and not problems:
        problems.append("no release stem in the directory")
    for stem, files in sorted(stems.items()):
        sums = out_dir / f"{stem}.sha256"
        if not sums.is_file():
            problems.append(f"{stem}: no {stem}.sha256: the write did not finish")
            continue
        listed = {}
        for line in sums.read_text(encoding="ascii").splitlines():
            digest, _, name = line.partition("  ")
            listed[name] = digest
        for p in files:
            if p == sums:
                continue
            if p.name not in listed:
                problems.append(f"{p.name}: not in {sums.name}")
            elif hashlib.sha256(p.read_bytes()).hexdigest() != listed[p.name]:
                problems.append(f"{p.name}: SHA-256 differs from {sums.name}")
        for name in sorted(set(listed) - {p.name for p in files}):
            problems.append(f"{name}: listed in {sums.name} but missing")
    return problems


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv: list[str]) -> int:
    """python -m otc_pipeline.release_writer validate OTC_YYYYMMDD.json"""
    if len(argv) != 2 or argv[0] != "validate":
        print(main.__doc__, file=sys.stderr)
        return 2
    try:
        release = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"{argv[1]}: cannot read a JSON document: {e}", file=sys.stderr)
        return 1
    try:
        errors = validate_release(release)
    except SchemaPinError as e:
        print(e, file=sys.stderr)
        return 2
    for e in errors:
        print(e)
    print(f"{argv[1]}: {'valid' if not errors else f'{len(errors)} errors'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
