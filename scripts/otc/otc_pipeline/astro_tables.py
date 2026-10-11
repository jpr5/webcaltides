"""The release's per-year astronomical tables (KP0b; prediction spec §3.3 item 1, §3.4) and build check 7.

    load_congen_run(dir) -> (congen, tcd)            the two dumps scripts/otc/tools/congen_tables.sh wrote
    astro_table(congen, tcd, names) -> dict          one schema astro_table for the constituents a release uses
    release_table_errors(table, congen, tcd) -> [str] a table given to the build is that run's table, exactly
    astro_table_errors(release) -> [str]             build check 7 (the schema's top description)

The values come from a congen run (congen_tables.sh: congen 1.7-r2, run as a tool, never vendored),
not from the TCD file. Before a table is made, every congen value is checked equal to the TCD's
for every year (§3.4 rule 2): V0+u modulo 360, min(d, 360 - d) < 0.005 degree with d = |a - b| mod
360, and |f_a - f_b| < 0.00005. A mismatch stops the build (AstroTableMismatch). So the release, the
reference engine (which reads the TCD) and the GESLA fit (which reads the TCD dump) use the same numbers.

The tables are published under the release's licence, CC BY 4.0, with the credit in GENERATOR
(§3.4 rule 3): Schureman, US C&GS Special Publication 98, and congen (David Flater, XTide).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from gesla.tcd_table import TcdTable

__all__ = ["AstroTableMismatch", "CHECK7_SPEED_TOL", "CongenRun", "F_TOL", "FIRST_YEAR", "GENERATOR", "LAST_YEAR", "TABLE_ID",
           "V0U_TOL", "astro_table", "astro_table_errors", "load_congen_run", "release_table_errors",
           "tcd_equality_errors"]

TABLE_ID = "tcd-20251228"   # data/harmonics-dwf-20251228-free.tcd (data/latest-xtide.tcd)
TCD_SHA256 = "33f97cb73ac25a0fec273950c0b85ff55ddbe7edf4448e80ea8d5b3d59b0aeb1"
# The tool tarballs scripts/otc/tools/congen_tables.sh pins (the same values; a test checks they agree).
CONGEN_SHA256 = "1dbae015e338e5780a505ebf233058f5e91628319a11bfbc465cb556cfb4b0d7"   # congen-1.7-r2.tar.xz
LIBTCD_SHA256 = "e1dde9aafb771eab57c676a99b4b79d61c6800990a0e72782bc20057a8a2d877"   # libtcd-2.2.7-r3.tar.xz
# Prediction spec, "Decisions (settled 2026-10-09)" item 2: "The tables cover 1700-2100, the full range
# of the TCD"; §3.3 item 1 shows first_year 1700, last_year 2100. The build requires exactly this range.
FIRST_YEAR, LAST_YEAR = 1700, 2100
V0U_TOL = 0.005      # degrees, half the TCD's 0.01 step (§3.4 rule 2: min(d, 360 - d) < 0.005)
F_TOL = 0.00005      # half the TCD's 0.0001 step (§3.4 rule 2: |a - b| < 0.00005)
# Speeds are stored to 7 decimals. congen against the TCD: equal (exact, the same stored digits).
# Check 7, "within 1e-7 degrees per hour": |a - b| <= 1e-7, plus a float-only epsilon of 1e-12 of the
# speeds' size, so that a difference of exactly 1e-7 between two stored values (computed as 9.99e-8 or
# 1.0000001e-7 depending on the constituent) is within, and 1.4e-7 is not.
CHECK7_SPEED_TOL = 1e-7
CHECK7_SPEED_EPS = 1e-12
GENERATOR = ("congen 1.7-r2 (XTide, David Flater) after Schureman, Manual of Harmonic Analysis and Prediction "
             "of Tides, US C&GS Special Publication 98")
V0_MODEL = "schureman_tcd"
# Release constituent names that the TCD (and congen) spell differently. A table row is keyed by the
# name the release uses (the schema has no aliases in astro_tables), with the values of the TCD's row.
# NOAA CO-OPS harcon names LAM2 and RHO are the TCD's LDA2 and RHO1 (same Doodson numbers; NOAA gives
# speeds 29.455626 and 13.471515 deg/h, the TCD 29.4556253 and 13.4715145). SIGMA1 and THETA1 (NOAA
# 9455920 Anchorage harcon, 12.92714 and 15.512589 deg/h) are the TCD's SIG1 (12.9271398) and THE1
# (15.5125897). The TICON spellings (EP2, LM2, MI2, MKS, NI2) come with the TCD dump's own aliases.
# This map is the authoritative one; the adapters' copies follow it.
NAME_TO_TCD = {"LAM2": "LDA2", "RHO": "RHO1", "SIGMA1": "SIG1", "THETA1": "THE1"}
CONGEN_RUN_FILES = {"congen_dump": f"congen-{FIRST_YEAR}-{LAST_YEAR}.json",
                    "tcd_dump": f"tcd-{FIRST_YEAR}-{LAST_YEAR}.json"}
CONGEN_TCD = f"congen-{FIRST_YEAR}-{LAST_YEAR}.tcd"


class AstroTableMismatch(ValueError):
    """The congen run and the TCD disagree, or the run cannot be the congen run: the build stops."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_LOADER = object()   # only load_congen_run makes a CongenRun


@dataclass(frozen=True)
class CongenRun:
    """A congen_tables.sh run as load_congen_run read and checked it: the congen dump, the TCD dump, the
    run's PROVENANCE and its directory. Only load_congen_run makes one, so a build that takes a
    CongenRun takes a run whose files, tool tarballs and command were checked. Unpacks as
    (congen, tcd)."""
    congen: TcdTable
    tcd: TcdTable
    provenance: Mapping
    run_dir: Path
    _made_by: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._made_by is not _LOADER:
            raise TypeError("a CongenRun comes from astro_tables.load_congen_run(run_dir) only")

    def __iter__(self):
        return iter((self.congen, self.tcd))


def _entry(prov: Mapping, key: str, run_dir: Path) -> Mapping:
    entry = prov["files"][key]
    if not isinstance(entry, Mapping):
        raise AstroTableMismatch(f"{run_dir}/PROVENANCE.json: files.{key} is {entry!r}, not an object")
    return entry


def load_congen_run(run_dir: Path, *, tcd_sha256: str = TCD_SHA256) -> CongenRun:
    """The congen run of a congen_tables.sh run directory. PROVENANCE.json must name the pinned congen
    and libtcd tarballs (CONGEN_SHA256, LIBTCD_SHA256) and the congen command for FIRST_YEAR-LAST_YEAR.
    Every file (both dumps and the congen .tcd itself) is hashed on disk and checked against it; the
    congen dump must be of that .tcd, the TCD dump of the TCD `tcd_sha256`, and the run and both dumps
    must cover FIRST_YEAR-LAST_YEAR. Any other content raises AstroTableMismatch."""
    run_dir = Path(run_dir)
    try:
        prov = json.loads((run_dir / "PROVENANCE.json").read_text())
        files = {key: _entry(prov, key, run_dir) for key in (*CONGEN_RUN_FILES, "congen_tcd")}
        years, tool, command = prov["years"], prov["tool"], prov["command"]
        tools = {k: tool[k]["sha256"] for k in ("congen", "libtcd")}
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise AstroTableMismatch(f"{run_dir}/PROVENANCE.json: missing or malformed ({type(e).__name__}: {e})") from None
    if tools != {"congen": CONGEN_SHA256, "libtcd": LIBTCD_SHA256}:
        raise AstroTableMismatch(f"{run_dir}: the run's tools are {tools}, not the pinned congen 1.7-r2 "
                                 f"{CONGEN_SHA256} and libtcd 2.2.7-r3 {LIBTCD_SHA256}")
    want_cmd = f"congen -b {FIRST_YEAR} -e {LAST_YEAR} -tcd {CONGEN_TCD} "
    if not (isinstance(command, str) and command.startswith(want_cmd)):
        raise AstroTableMismatch(f"{run_dir}: the run's command is {command!r}, expected {want_cmd!r}...")
    if years != [FIRST_YEAR, LAST_YEAR]:
        raise AstroTableMismatch(f"{run_dir}: the run covers {years}, expected [{FIRST_YEAR}, {LAST_YEAR}]")
    for key, name in (*CONGEN_RUN_FILES.items(), ("congen_tcd", CONGEN_TCD)):
        entry = files[key]
        if entry.get("path") != name:
            raise AstroTableMismatch(f"{run_dir}: PROVENANCE {key} is {entry.get('path')!r}, expected {name!r}")
        got = _sha256(run_dir / name)
        if got != entry.get("sha256"):
            raise AstroTableMismatch(f"{run_dir / name}: SHA-256 {got}, PROVENANCE says {entry.get('sha256')}")
    if files["congen_tcd"]["sha256"] == tcd_sha256:
        raise AstroTableMismatch(f"{run_dir / CONGEN_TCD} is the TCD itself, not a congen run's output")
    congen, tcd = (TcdTable.load(run_dir / CONGEN_RUN_FILES[k]) for k in CONGEN_RUN_FILES)
    if tcd.tcd_sha256 != tcd_sha256:
        raise AstroTableMismatch(f"the TCD dump is of TCD {tcd.tcd_sha256}, expected {tcd_sha256}")
    if congen.tcd_sha256 != files["congen_tcd"]["sha256"]:
        raise AstroTableMismatch(f"the congen dump is of TCD {congen.tcd_sha256}, not the run's "
                                 f"{files['congen_tcd']['sha256']}")
    for what, t in (("congen", congen), ("TCD", tcd)):
        if (t.first_year, t.last_year) != (FIRST_YEAR, LAST_YEAR):
            raise AstroTableMismatch(f"the {what} dump covers {t.first_year}-{t.last_year}, expected "
                                     f"{FIRST_YEAR}-{LAST_YEAR}")
    return CongenRun(congen, tcd, prov, run_dir, _LOADER)


def _v0u_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _tcd_name(tcd: TcdTable, raw: str) -> str | None:
    """The TCD name of a release constituent name (NAME_TO_TCD, then the TCD's aliases), or None."""
    mapped = NAME_TO_TCD.get(raw, raw)
    return tcd.name(mapped) if mapped in tcd else None


def tcd_equality_errors(congen: TcdTable, tcd: TcdTable, names: Iterable[str], *,
                        first_year: int = FIRST_YEAR, last_year: int = LAST_YEAR) -> list[str]:
    """§3.4 rule 2 for these constituents (release names, mapped by NAME_TO_TCD and the TCD's aliases), every
    year first_year..last_year. Empty list = equal. Speeds must be equal (the same stored 7 decimals)."""
    errors = []
    for raw in sorted(set(names)):
        n = _tcd_name(tcd, raw)
        if n is None:
            errors.append(f"{raw}: not a constituent of the TCD")
            continue
        if n not in congen.constituents:
            errors.append(f"{raw}: the congen run has no constituent {n!r}")
            continue
        c, t = congen.constituents[n], tcd.constituents[n]
        if c.speed != t.speed:
            errors.append(f"{raw}: speed {c.speed} (congen) != {t.speed} (TCD)")
        for y in range(first_year, last_year + 1):
            try:
                cv, cf = congen.v0u(n, y), congen.f(n, y)
                tv, tf = tcd.v0u(n, y), tcd.f(n, y)
            except ValueError as e:   # a year outside a dump
                errors.append(f"{raw}: {e}")
                break
            if not _v0u_diff(cv, tv) < V0U_TOL:
                errors.append(f"{raw} {y}: V0+u {cv} (congen) != {tv} (TCD)")
            if not abs(cf - tf) < F_TOL:
                errors.append(f"{raw} {y}: f {cf} (congen) != {tf} (TCD)")
    return errors


def astro_table(congen: TcdTable, tcd: TcdTable, names: Iterable[str], *, table_id: str = TABLE_ID,
                first_year: int = FIRST_YEAR, last_year: int = LAST_YEAR) -> dict:
    """The schema astro_table for `names` (the constituent names a release uses, OTC canonical), from
    the congen run, after the TCD equality check. V0+u is rounded to 0.01 and reduced to [0, 360),
    f to 0.0001. Raises AstroTableMismatch on any mismatch, and when `congen` is not a congen run
    (it is the TCD itself: §3.4 rule 1, the tables come from the run, not from the TCD file)."""
    names = sorted(set(names))
    if not names:
        raise AstroTableMismatch("no constituents: a table has one row per constituent the release uses")
    if congen.tcd_sha256 == tcd.tcd_sha256:
        raise AstroTableMismatch(f"the congen dump is of the TCD itself ({tcd.tcd_sha256}): the table must "
                                 "come from a congen run")
    errors = tcd_equality_errors(congen, tcd, names, first_year=first_year, last_year=last_year)
    if errors:
        raise AstroTableMismatch(f"congen != TCD in {len(errors)} values: " + "; ".join(errors[:10]))
    rows = {}
    for name in names:
        c = congen.constituents[_tcd_name(tcd, name)]
        years = range(first_year, last_year + 1)
        rows[name] = {"speed_deg_per_hour": c.speed,
                      "v0u_deg": [round(congen.v0u(c.name, y) % 360.0, 2) % 360.0 for y in years],
                      "f": [round(congen.f(c.name, y), 4) for y in years]}
    return {"astro_table_id": table_id, "v0_model": V0_MODEL, "tables_sha256": tcd.tcd_sha256,
            "generator": GENERATOR, "first_year": first_year, "last_year": last_year, "constituents": rows}


def release_table_errors(table: Mapping, congen: TcdTable, tcd: TcdTable) -> list[str]:
    """Why `table` (an astro table given to the build) is not this congen run's table: a TEST ONLY or
    other generator, a year range other than FIRST_YEAR-LAST_YEAR, another TCD, or any row (speed,
    every V0+u, every f) other than astro_table(congen, tcd, its names) gives. Empty list = it is.
    This is what keeps a hand-edited, stale or stand-in table out of a release (§3.4 rules 1-2)."""
    tid = table.get("astro_table_id")
    where = f"astro table {tid!r}"
    gen = table.get("generator")
    if isinstance(gen, str) and gen.lstrip().upper().startswith("TEST ONLY"):
        return [f"{where}: generator {gen!r} marks a TEST ONLY table, which is never released"]
    errors = []
    if gen != GENERATOR:
        errors.append(f"{where}: generator {gen!r}, expected {GENERATOR!r}")
    if (table.get("first_year"), table.get("last_year")) != (FIRST_YEAR, LAST_YEAR):
        errors.append(f"{where}: years {table.get('first_year')}-{table.get('last_year')}, the release's tables "
                      f"cover {FIRST_YEAR}-{LAST_YEAR} (prediction spec, settled decision 2)")
    if table.get("v0_model") != V0_MODEL:
        errors.append(f"{where}: v0_model {table.get('v0_model')!r}, expected {V0_MODEL!r}")
    if table.get("tables_sha256") != tcd.tcd_sha256:
        errors.append(f"{where}: tables_sha256 {table.get('tables_sha256')}, the run's TCD is {tcd.tcd_sha256}")
    rows = table.get("constituents")
    if errors or not isinstance(rows, dict) or not rows:
        return errors or [f"{where}: no constituents"]
    try:
        want = astro_table(congen, tcd, rows, table_id=tid)["constituents"]
    except AstroTableMismatch as e:
        return [f"{where}: {e}"]
    years = range(FIRST_YEAR, LAST_YEAR + 1)
    for name in sorted(rows):
        row, w = rows[name], want[name]
        if not isinstance(row, dict):
            errors.append(f"{where} {name}: not a row")
            continue
        if row.get("speed_deg_per_hour") != w["speed_deg_per_hour"]:
            errors.append(f"{where} {name}: speed {row.get('speed_deg_per_hour')} != the run's {w['speed_deg_per_hour']}")
        for key in ("v0u_deg", "f"):
            got = row.get(key)
            if not isinstance(got, list) or len(got) != len(w[key]):
                errors.append(f"{where} {name}: {key} is not {len(w[key])} values")
                continue
            bad = [(y, a, b) for y, a, b in zip(years, got, w[key], strict=True) if a != b]
            errors += [f"{where} {name} {y}: {key} {a} != the congen run's {b}" for y, a, b in bad[:3]]
            if len(bad) > 3:
                errors.append(f"{where} {name}: {len(bad) - 3} more {key} values differ")
    return errors


# --- build check 7 ---------------------------------------------------------------------------------

def _dicts(items) -> list[dict]:
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _speed_within(a: float, b: float) -> bool:
    return abs(a - b) <= CHECK7_SPEED_TOL + CHECK7_SPEED_EPS * max(1.0, abs(a), abs(b))


def astro_table_errors(release: Mapping) -> list[str]:
    """Build check 7 of the schema's top description, plus the astro_table_id cross-reference:
    - a convention's astro_table_id names a table of astro_tables;
    - every constituent of every set with constants has a row in its convention's table: every convention
      that names an astro table (astro_table_id), whatever its nodal_handling, as the schema's text says;
    - each row's speed equals the set's speed and the constituents table speed within 1e-7 deg/h
      (_speed_within: 1e-7 plus a float-only epsilon);
    - every v0u_deg and f array has last_year - first_year + 1 entries;
    - a convention's tables_sha256, when present, equals its table's.
    Parts that are not of the schema's types are skipped (the schema pass reports them)."""
    errors: list[str] = []
    tables: dict[str, dict] = {}
    for ti, t in enumerate(_dicts(release.get("astro_tables"))):
        tid = t.get("astro_table_id")
        if not isinstance(tid, str):
            continue
        if tid in tables:
            errors.append(f"duplicate astro_table_id {tid!r}")
        tables.setdefault(tid, t)
        first, last = t.get("first_year"), t.get("last_year")
        if not (isinstance(first, int) and isinstance(last, int)):
            continue
        want = last - first + 1
        if want < 1:
            errors.append(f"astro_tables/{ti} ({tid}): last_year {last} is before first_year {first}")
            continue
        rows = t.get("constituents")
        for name, row in sorted(rows.items()) if isinstance(rows, dict) else []:
            for key in ("v0u_deg", "f"):
                arr = row.get(key) if isinstance(row, dict) else None
                if isinstance(arr, list) and len(arr) != want:
                    errors.append(f"astro_tables/{ti} ({tid}) {name}: {key} has {len(arr)} entries, "
                                  f"expected {want} ({first}-{last})")
    table_speed = {}
    for row in _dicts(release.get("constituents")):
        key = (row.get("constituent_table_version"), row.get("name"))
        if all(isinstance(k, str) for k in key) and _num(row.get("speed_deg_per_hour")):
            table_speed[key] = row["speed_deg_per_hour"]
    conventions = {}
    for ci, conv in enumerate(_dicts(release.get("conventions"))):
        cid = conv.get("convention_id")
        if isinstance(cid, str):
            conventions[cid] = conv
        tid = conv.get("astro_table_id")
        if isinstance(tid, str):
            if tid not in tables:
                errors.append(f"conventions/{ci} ({cid}): astro_table_id {tid!r} is not defined in astro_tables")
            elif isinstance(conv.get("tables_sha256"), str) and conv["tables_sha256"] != tables[tid].get("tables_sha256"):
                errors.append(f"conventions/{ci} ({cid}): tables_sha256 {conv['tables_sha256']} != astro table "
                              f"{tid!r}'s {tables[tid].get('tables_sha256')}")
    for si, st in enumerate(_dicts(release.get("stations"))):
        for ki, cs in enumerate(_dicts(st.get("constant_sets"))):
            conv = conventions.get(cs.get("convention_id")) if isinstance(cs.get("convention_id"), str) else None
            if conv is None:
                continue
            table = tables.get(conv.get("astro_table_id")) if isinstance(conv.get("astro_table_id"), str) else None
            if table is None:
                continue   # reported with the convention
            rows = table.get("constituents") if isinstance(table.get("constituents"), dict) else {}
            version = conv.get("constituent_table_version")
            sw = f"stations/{si} ({st.get('station_id')}) constant_sets/{ki} ({cs.get('set_id')})"
            cons = [(c.get("name"), c.get("speed_deg_per_hour")) for c in _dicts(cs.get("constituents"))]
            for b in _dicts(cs.get("current_bins")):
                cons += [(c.get("name"), c.get("speed_deg_per_hour")) for c in _dicts(b.get("constituents"))]
            for name, speed in cons:
                if not isinstance(name, str):
                    continue
                row = rows.get(name)
                if not isinstance(row, dict):
                    errors.append(f"{sw}: constituent {name!r} has no row in astro table "
                                  f"{conv.get('astro_table_id')!r}")
                    continue
                rs = row.get("speed_deg_per_hour")
                if not _num(rs):
                    continue
                if _num(speed) and not _speed_within(rs, speed):
                    errors.append(f"{sw}: {name} speed {speed} != astro table row speed {rs}")
                ts = table_speed.get((version, name)) if isinstance(version, str) else None
                if ts is not None and not _speed_within(rs, ts):
                    errors.append(f"{sw}: {name} astro table row speed {rs} != constituents table speed {ts} "
                                  f"(version {version!r})")
    return list(dict.fromkeys(errors))
