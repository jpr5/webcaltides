"""OTC release writer and validator (format 0.2).

The release files of one release share the stem OTC_{DATESTAMP} (publication
plan §2.3, §2.4):

    .json        the release document (primary format)
    .json.gz     gzip of the .json file
    .jsonl       one station document per line
    .jsonl.gz    optional: gzip of the .jsonl file
    .meta.json   the document without `stations` (valid against #/$defs/meta)
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
import sys
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator

__all__ = ["CSV_COLUMNS", "next_datestamp", "validate_release", "write_release"]

DATESTAMP_RE = re.compile(r"^[0-9]{8}(\.[2-9]|\.[1-9][0-9]+)?$")
# OTC_<8 digits>[.<counter>].<extension starting with a letter>
STEM_RE = re.compile(r"^OTC_([0-9]{8})(?:\.([0-9]+))?\.[A-Za-z]")

# --- CSV layout ---------------------------------------------------------------
#
# One row per record. `record_type` is one of station, tombstone, offset,
# constant_set, constituent, convention, validation. A column that does not
# apply to a row type is empty. Columns marked JSON hold the value as a compact
# JSON string with sorted keys (nested data such as provenance).
#
# Row order: every convention (document order), then for each station (document
# order): its station or tombstone row, its offset row, then each constant set
# followed by its constituents, then its validation rows.

CSV_COLUMNS = [
    "record_type",
    # keys shared by several row types
    "station_id", "set_id", "name", "status", "convention_id", "licence_id",
    # station
    "country", "lat", "lon", "timezone", "type", "aliases", "recommended_set_id",
    # tombstone
    "removed_in", "removed_reason",
    # offset (subordinate_offsets)
    "reference_station_id", "time_offset_high_min", "time_offset_low_min",
    "height_offset_high", "height_offset_low", "height_adjusted_type",
    # constant_set
    "source", "source_type", "quantity", "source_record_id", "source_version",
    "record_span_start", "record_span_end", "good_samples",
    "datum_msl_offset_m", "datum_named", "qc_status", "qc_flags", "dropped_constituents",
    "provenance",
    # constituent
    "source_name", "doodson", "speed_deg_per_hour", "amplitude_m", "phase_deg",
    "amp_uncertainty_m", "phase_uncertainty_deg", "kept_reason",
    # convention
    "phase_reference", "utc_offset_hours", "v0_model", "nodal_handling", "nodal_formula_ids",
    "constituent_table_version", "tables_sha256", "canary",
    # validation
    "reference_source", "reference_station", "reference_distance_km", "window",
    "time_mae_min", "time_p95_min", "time_bias_min", "height_mae_m", "range_error_m",
    "missed_events", "extra_events", "previous_release",
]

JSON_COLUMNS = {"aliases", "datum_named", "qc_flags", "dropped_constituents", "provenance",
                "nodal_formula_ids", "canary", "previous_release"}
FLOAT_COLUMNS = {"lat", "lon", "time_offset_high_min", "time_offset_low_min", "height_offset_high",
                 "height_offset_low", "datum_msl_offset_m", "speed_deg_per_hour", "amplitude_m",
                 "phase_deg", "amp_uncertainty_m", "phase_uncertainty_deg", "utc_offset_hours",
                 "reference_distance_km", "time_mae_min", "time_p95_min", "time_bias_min",
                 "height_mae_m", "range_error_m"}
INT_COLUMNS = {"good_samples", "missed_events", "extra_events"}

# Keys of each record that map one-to-one onto a column of the same name.
# Keys handled specially (children, split objects) are listed in _CHILD_KEYS.
_ROW_KEYS = {
    "station": ["station_id", "status", "name", "country", "lat", "lon", "timezone", "type",
                "aliases", "recommended_set_id"],
    "tombstone": ["station_id", "status", "name", "removed_in", "removed_reason"],
    "offset": ["reference_station_id", "time_offset_high_min", "time_offset_low_min",
               "height_offset_high", "height_offset_low", "height_adjusted_type", "licence_id"],
    "constant_set": ["set_id", "source", "source_type", "quantity", "source_record_id",
                     "source_version", "convention_id", "licence_id", "qc_status", "qc_flags",
                     "dropped_constituents", "provenance"],
    "constituent": ["name", "source_name", "doodson", "speed_deg_per_hour", "amplitude_m",
                    "phase_deg", "amp_uncertainty_m", "phase_uncertainty_deg", "kept_reason"],
    "convention": ["convention_id", "phase_reference", "utc_offset_hours", "v0_model",
                   "nodal_handling", "nodal_formula_ids", "constituent_table_version",
                   "tables_sha256", "canary"],
    "validation": ["set_id", "reference_source", "reference_station", "reference_distance_km",
                   "window", "time_mae_min", "time_p95_min", "time_bias_min", "height_mae_m",
                   "range_error_m", "missed_events", "extra_events", "previous_release"],
}
_CHILD_KEYS = {
    "station": {"constant_sets", "subordinate_offsets", "validation"},
    "constant_set": {"constituents", "record_span", "datum"},
}

_PARQUET_TYPES = None  # filled lazily: column -> pyarrow type


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


def _cross_reference_errors(release: dict) -> list[str]:
    errors: list[str] = []

    def ids(items, key, what):
        seen = {}
        for i, item in enumerate(items if isinstance(items, list) else []):
            if isinstance(item, dict) and key in item:
                if item[key] in seen:
                    errors.append(f"duplicate {what} {item[key]!r}")
                seen.setdefault(item[key], item)
        return seen

    conventions = ids(release.get("conventions"), "convention_id", "convention_id")
    licences = ids(release.get("licences"), "licence_id", "licence_id")
    table: set[tuple[str, str]] = set()
    for row in release.get("constituents") or []:
        if isinstance(row, dict):
            key = (row.get("constituent_table_version"), row.get("name"))
            if key in table:
                errors.append(f"duplicate constituent table row {key[1]!r} (version {key[0]!r})")
            table.add(key)

    stations = [s for s in (release.get("stations") or []) if isinstance(s, dict)]
    by_id = ids(stations, "station_id", "station_id")
    set_ids: set[str] = set()
    alias_owner: dict[tuple[str, str], str] = {}

    for si, st in enumerate(stations):
        sid = st.get("station_id")
        where = f"stations/{si} ({sid})"
        if st.get("status") != "active":
            continue
        own_sets = set()
        for ki, cs in enumerate(st.get("constant_sets") or []):
            if not isinstance(cs, dict):
                continue
            sw = f"{where} constant_sets/{ki} ({cs.get('set_id')})"
            if "set_id" in cs:
                if cs["set_id"] in set_ids:
                    errors.append(f"duplicate set_id {cs['set_id']!r}")
                set_ids.add(cs["set_id"])
                own_sets.add(cs["set_id"])
            conv = conventions.get(cs.get("convention_id")) if "convention_id" in cs else None
            if "convention_id" in cs and conv is None:
                errors.append(f"{sw}: convention_id {cs['convention_id']!r} is not defined in conventions")
            if "licence_id" in cs and cs["licence_id"] not in licences:
                errors.append(f"{sw}: licence_id {cs['licence_id']!r} is not defined in licences")
            version = conv.get("constituent_table_version") if isinstance(conv, dict) else None
            names_seen = set()
            for field in ("constituents", "dropped_constituents"):
                for c in cs.get(field) or []:
                    if not isinstance(c, dict) or "name" not in c:
                        continue
                    if field == "constituents":
                        if c["name"] in names_seen:
                            errors.append(f"{sw}: duplicate constituent {c['name']!r}")
                        names_seen.add(c["name"])
                    if conv is not None and (version, c["name"]) not in table:
                        errors.append(f"{sw}: {field} name {c['name']!r} is not in the constituents "
                                      f"table for constituent_table_version {version!r}")
        rec = st.get("recommended_set_id", "absent")
        if rec is None:
            if st.get("type") != "subordinate":
                errors.append(f"{where}: recommended_set_id is null but the station is not subordinate")
        elif rec != "absent" and rec not in own_sets:
            errors.append(f"{where}: recommended_set_id {rec!r} is not a set of this station")
        off = st.get("subordinate_offsets")
        if isinstance(off, dict):
            ref = off.get("reference_station_id")
            target = by_id.get(ref)
            if ref == sid:
                errors.append(f"{where}: subordinate_offsets reference_station_id {ref!r} is the station itself")
            elif not isinstance(target, dict) or target.get("status") != "active":
                errors.append(f"{where}: subordinate_offsets reference_station_id {ref!r} "
                              "is not an active station in this release")
            elif target.get("type") != "reference":
                errors.append(f"{where}: subordinate_offsets reference_station_id {ref!r} "
                              "is not a reference station")
            if "licence_id" in off and off["licence_id"] not in licences:
                errors.append(f"{where}: subordinate_offsets licence_id {off['licence_id']!r} "
                              "is not defined in licences")
        for vi, v in enumerate(st.get("validation") or []):
            if isinstance(v, dict) and "set_id" in v and v["set_id"] not in own_sets:
                errors.append(f"{where} validation/{vi}: set_id {v['set_id']!r} is not a set of this station")
        aliases = st.get("aliases")
        if isinstance(aliases, dict):
            for system, values in sorted(aliases.items()):
                for alias in values if isinstance(values, list) else [values]:
                    if not isinstance(alias, str):
                        continue
                    owner = alias_owner.setdefault((system, alias), sid)
                    if owner != sid:
                        errors.append(f"duplicate alias {system}:{alias} ({owner}, {sid})")
    return errors


def validate_release(release: dict, schema_path: Path) -> list[str]:
    """JSON Schema (format 0.2) errors + cross-reference errors (convention_id, licence_id,
    recommended_set_id, reference_station_id) + duplicate alias ids + name maxLength. Empty list = valid."""
    if not isinstance(release, dict):
        return [f"(root): release must be a JSON object, not {type(release).__name__}"]
    errors: list[str] = []
    _non_json_values(release, [], errors)
    if errors:
        return errors
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    schema_errors = set()
    for top in validator.iter_errors(release):
        for e in _leaf_errors(top):
            schema_errors.add((tuple(str(p) for p in e.absolute_path), f"{_path(e.absolute_path)}: {e.message}"))
    errors += [msg for _, msg in sorted(schema_errors)]
    errors += _cross_reference_errors(release)
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


def _csv_rows(release: dict) -> list[dict]:
    rows = [_row("convention", c) for c in release["conventions"]]
    for st in release["stations"]:
        sid = st["station_id"]
        if st["status"] == "removed":
            rows.append(_row("tombstone", st))
            continue
        rows.append(_row("station", st))
        if "subordinate_offsets" in st:
            rows.append(_row("offset", st["subordinate_offsets"], {"station_id": sid}))
        for cs in st["constant_sets"]:
            extra = {"station_id": sid}
            span = cs.get("record_span", {})
            for key, col in (("start", "record_span_start"), ("end", "record_span_end"),
                             ("good_samples", "good_samples")):
                if key in span:
                    extra[col] = span[key]
            datum = cs.get("datum", {})
            if "msl_offset_m" in datum:
                extra["datum_msl_offset_m"] = datum["msl_offset_m"]
            if "named" in datum:
                extra["datum_named"] = datum["named"]
            rows.append(_row("constant_set", cs, extra))
            for c in cs["constituents"]:
                rows.append(_row("constituent", c, {"station_id": sid, "set_id": cs["set_id"]}))
        for v in st.get("validation", []):
            rows.append(_row("validation", v, {"station_id": sid}))
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

def write_release(release: dict, out_dir: Path, *, datestamp: str,
                  app_export: list[dict] | None, parquet: bool = False,
                  jsonl_gz: bool = False) -> list[Path]:
    """Writes OTC_{D}.json, .json.gz, .jsonl, .meta.json, .csv (RFC 4180, UTF-8, LF, record_type column),
    optional .parquet, optional .app.json, then OTC_{D}.sha256 last. Refuses if validate_release is non-empty.
    Byte-identical output for identical input (sorted keys, fixed float formatting).

    `jsonl_gz=True` also writes the optional OTC_{D}.jsonl.gz. Every file is built in memory first and
    then linked into place without replacing anything; on any failure the files of this call are removed.
    Raises ValueError (invalid release or arguments) or FileExistsError (the stem is already used).
    """
    out_dir = Path(out_dir)
    schema_path = Path(__file__).resolve().parent.parent / "schema" / "otc-0.2.schema.json"
    if not DATESTAMP_RE.match(datestamp or ""):
        raise ValueError(f"datestamp {datestamp!r} is not YYYYMMDD or YYYYMMDD.N (N >= 2)")
    errors = validate_release(release, schema_path)
    if errors:
        raise ValueError(f"release is not valid ({len(errors)} errors):\n  " + "\n  ".join(errors))
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
    try:
        for name, data in payloads:
            final = out_dir / name
            tmp = out_dir / f".{name}.tmp-{os.getpid()}"
            with open(tmp, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, final)  # fails if final exists: never replaces a file
            finally:
                tmp.unlink()
            written.append(final)
    except BaseException:
        for p in written:
            p.unlink(missing_ok=True)
        raise
    return written


def main(argv: list[str]) -> int:
    """python -m otc_pipeline.release_writer validate OTC_YYYYMMDD.json"""
    if len(argv) != 2 or argv[0] != "validate":
        print(main.__doc__, file=sys.stderr)
        return 2
    schema_path = Path(__file__).resolve().parent.parent / "schema" / "otc-0.2.schema.json"
    errors = validate_release(json.loads(Path(argv[1]).read_text(encoding="utf-8")), schema_path)
    for e in errors:
        print(e)
    print(f"{argv[1]}: {'valid' if not errors else f'{len(errors)} errors'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
