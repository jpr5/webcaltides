"""The adapter contract (manifest §2.4, GATE-ADP). Frozen once accepted.

An adapter turns one upstream source into either

- `SourceSet`s (official and model sources, and the GESLA-fit build output), or
- `GaugeRecord`s (gauge sources that serve observations; the fit pipeline makes their sets).

The registry (`sources.py`) says which of the two each of the 16 sources emits.

`check_contract(adapter, fetched)` returns every way the adapter's output breaks the contract;
`assert_contract` raises `ContractError` with them. Each adapter's tests run it on saved real
responses, and `tests/test_adapter_contract.py` runs it live on every registered adapter.

Conventions for a `SourceSet` (the build assigns OTC ids, adapters never do):

- `constant_set` carries no `set_id` and no `provenance.build`; the build sets `<station_id>/<source>`
  and the build fields.
- `subordinate_offsets` names its reference by upstream id, `reference_source_record_id`,
  and carries no `reference_station_id`; the build maps it to the OTC id.
- `station_hint` has name, country (alpha-3), lat, lon, type, timezone and aliases; a current
  station's hint also has `default_current_bin`.
- A subordinate current station's `subordinate_offsets` is `{"current_offsets": [...]}`: one
  schema `current_offset` per bin, each naming its reference by `reference_source_record_id`
  (not `reference_station_id`); the build maps them and writes the station's `current_offsets`.
- A set's upstream id is `constant_set.source_record_id`; a subordinate-only set (no
  constant_set) is identified by `station_hint.aliases[<alias system>]`, where the alias
  system is the adapter's optional `alias_system` attribute (default: its `source`; the
  noaa-currents source uses the `noaa` system). Every id is unique and has a
  watch key.
- `provenance.input_sha256`, when given, names fetched files (checked against their bytes).
- Licences: `licence()` returns the adapter's one licence. A source whose records carry different
  licences (GESLA: one per contributor, L-NC) defines `licences()` instead, returning every licence
  object it uses; each set's `licence_id` must be one of them (ids unique).
- Each licence states `commercial_use` (and, when it is false, `restriction` and `terms_url`):
  nothing defaults it.
- The `convention` carries no `astro_table_id`: the astro tables (KP0b) are the build's, and the
  build names its table on each convention that predicts with f and u. Without `astro_tables` the
  contract shell has none, and exactly the two errors that say so (BUILD_OWNED_ASTRO_ERRORS) are
  not the adapter's; with `astro_tables` the shell carries them and is checked whole.
- A gauge record's `declared_time_base` is a format 1.0 time-base class (`time_base_label` of the
  pinned schema): the fit's time-base annotation publishes it as `declared`.
- A gauge adapter's `record_id` is `<source>:<upstream id>`.
- `check_contract` calls only the accessor the registry names for the source: `records()` for
  gauge observation sources, `sets()` for every other source. Any exception an adapter raises
  becomes a contract error; the check never aborts.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import importlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Protocol, runtime_checkable

from gesla.record import GaugeRecord

from otc_pipeline.release_writer import FORMAT_VERSION, pinned_schema, validate_release

__all__ = [
    "Adapter", "ContractError", "FetchResult", "InputFile", "SourceSet",
    "assemble_release", "assert_contract", "check_contract", "is_relative_inside", "sha256_file",
]

SourceType = Literal["official", "gauge", "analysis", "model"]
Quantity = Literal["water_level", "current"]
SOURCE_TYPES = ("official", "gauge", "analysis", "model")
QUANTITIES = ("water_level", "current")
STATION_HINT_KEYS = ("name", "country", "lat", "lon", "type", "timezone", "aliases")
OPTIONAL_HINT_KEYS = ("default_current_bin",)
SOURCE_RE = re.compile(r"[a-z0-9-]+")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
EMPTY_TABLE_ERROR = "release: constituents: [] should be non-empty"
# Format 1.0 needs astro_tables, and an astro_table_id on a convention that predicts with f and u.
# Both are the build's (KP0b); a contract shell built without astro_tables is missing exactly these.
BUILD_OWNED_ASTRO_ERRORS = ("release: (root): 'astro_tables' is a required property",
                            "release: conventions/0: 'astro_table_id' is a required property")
# assemble_release's one-source shell is checked, never published: its release fields are fixed.
CONTRACT_FIT_MIN_GOOD_HOURS = 360
CONTRACT_BUILD_COMMIT = "0000000"


def is_relative_inside(name: object) -> bool:
    """True for a POSIX relative path with no empty, '.' or '..' segment (stays inside its root)."""
    return isinstance(name, str) and bool(name) and not any(
        seg in ("", ".", "..") or "\\" in seg for seg in name.split("/"))


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in 1 MiB chunks (model grids are several GB)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class InputFile:
    """One fetched file, as a MANIFEST.json entry (manifest §0.3). `path` is relative to the cache."""

    path: str
    sha256: str
    size: int
    source_url: str
    fetched_at: str  # ISO 8601 UTC

    def as_dict(self) -> dict:
        return {"path": self.path, "sha256": self.sha256, "size": self.size,
                "source_url": self.source_url, "fetched_at": self.fetched_at}


@dataclass(frozen=True)
class FetchResult:
    """What `fetch` returns: the cached files and the parsed payload, keyed by upstream record id."""

    source: str
    source_version: str          # upstream version, or the fetch datestamp YYYYMMDD
    cache_dir: Path
    files: tuple[InputFile, ...]
    # not part of the hash (a dict is unhashable); equality still compares it
    items: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "items", MappingProxyType(dict(self.items)))
        for f in self.files:
            if not is_relative_inside(f.path):
                raise ValueError(f"FetchResult file path {f.path!r} is not inside the cache directory")

    def manifest(self, **set_fields) -> dict:
        """MANIFEST.json for the fetched files: `files` sorted by path plus the set-level fields."""
        return {**set_fields, "files": [f.as_dict() for f in sorted(self.files, key=lambda f: f.path)]}


@dataclass(frozen=True)
class SourceSet:
    station_hint: dict          # name, country (alpha-3), lat, lon, type, timezone, aliases
    constant_set: dict | None   # OTC constant_set (format 1.0) without set_id and provenance.build; None only for a subordinate
    subordinate_offsets: dict | None = None

    def __post_init__(self) -> None:
        if self.constant_set is None and self.subordinate_offsets is None:
            raise ValueError("a SourceSet needs a constant_set, subordinate_offsets, or both")


@runtime_checkable
class Adapter(Protocol):
    source: str                                      # schema `source`, e.g. "noaa", "pegelonline"
    source_type: SourceType
    quantity: Quantity
    adapter_version: str
    contract_sample: tuple[str, ...]                 # upstream ids the live contract test fetches

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult: ...
    def convention(self) -> dict: ...                # OTC `convention` object
    def licence(self) -> dict: ...                   # OTC `licence` object (or `licences()`, see above)
    def watch_keys(self, fetched: FetchResult) -> dict[str, str]: ...   # record id -> normalised-content SHA-256
    def records(self, fetched: FetchResult) -> Iterable[GaugeRecord]: ...  # gauge observation sources only
    def sets(self, fetched: FetchResult) -> Iterable[SourceSet]: ...        # every other source


class ContractError(AssertionError):
    def __init__(self, source: str, errors: list[str]):
        self.errors = list(errors)
        super().__init__(f"adapter {source!r} breaks the contract:\n  " + "\n  ".join(self.errors))


def _sources():
    """otc_pipeline.sources, the source registry. Imported when first used: it imports the adapters,
    which import this module."""
    return importlib.import_module("otc_pipeline.sources")


@functools.lru_cache(maxsize=1)
def _time_base_label_re() -> re.Pattern:
    """The pinned schema's time_base_label pattern (its ECMA end guard reads the same in Python)."""
    return re.compile(pinned_schema()["$defs"]["time_base_label"]["pattern"])


def _attr(adapter, name: str):
    """An adapter attribute, or None when it is missing or raises."""
    try:
        return getattr(adapter, name, None)
    except Exception:  # noqa: BLE001 - a broken property is reported as a missing attribute
        return None


def _expected_output(source, source_type) -> str:
    EXPECTED = _sources().EXPECTED
    spec = EXPECTED.get(source) if isinstance(source, str) else None
    if spec is not None:
        return spec.output
    return "records" if source_type == "gauge" else "sets"


_FAILED = object()


def _call(errors: list[str], adapter, name: str, *args, materialise: bool = False):
    """adapter.name(*args), or _FAILED with the exception recorded as a contract error."""
    try:
        method = getattr(adapter, name)
        value = method(*args)
        if materialise:
            value = list(value or ())
        return value
    except Exception as e:  # noqa: BLE001 - the contract reports every adapter failure
        errors.append(f"{name}() raised {type(e).__name__}: {e}")
        return _FAILED


BOTH_LICENCE_METHODS = "the adapter defines both licence() and licences(): define one"


def _defines(adapter, name: str) -> bool:
    """True when the adapter itself defines `name` (on the instance or a class in its MRO), not when it
    only inherits the Adapter Protocol's stub."""
    if name in getattr(adapter, "__dict__", {}):
        return callable(adapter.__dict__[name])
    for klass in type(adapter).__mro__:
        if name in vars(klass):
            return klass is not Adapter and callable(vars(klass)[name])
    return False


def _defines_both(adapter) -> bool:
    return _defines(adapter, "licences") and _defines(adapter, "licence")


def adapter_licences(adapter) -> list[dict]:
    """The adapter's licence objects: `licences()` when it defines one, else `[licence()]`. An adapter
    that defines both raises ValueError (licence() is never silently ignored)."""
    if _defines_both(adapter):
        raise ValueError(BOTH_LICENCE_METHODS)
    if callable(getattr(adapter, "licences", None)):
        return list(adapter.licences() or ())
    return [adapter.licence()]


def _licences(errors: list[str], adapter):
    """adapter_licences, or _FAILED with the reason recorded as a contract error."""
    if _defines_both(adapter):
        errors.append(BOTH_LICENCE_METHODS)
        return _FAILED
    name = "licences" if callable(getattr(adapter, "licences", None)) else "licence"
    got = _call(errors, adapter, name, materialise=(name == "licences"))
    if got is _FAILED:
        return _FAILED
    lics = got if name == "licences" else [got]
    if not lics or not all(isinstance(x, dict) for x in lics):
        errors.append(f"{name}() must return {'one or more licence objects' if name == 'licences' else 'a licence object'}")
        return _FAILED
    ids = [x.get("licence_id") for x in lics]
    dup = sorted({i for i in ids if ids.count(i) > 1}, key=str)
    if dup:
        errors.append(f"licences() lists licence_id {dup} more than once")
        return _FAILED
    return lics


def _station_id(i: int) -> str:
    return f"OTC-CONTRACT-{i:05d}"


def assemble_release(adapter, sets: list[SourceSet], fetched: FetchResult,
                     constituent_table: list[dict] | None = None, astro_tables: list[dict] | None = None) -> dict:
    """A one-source release around the adapter's sets, as the build would lay it out, so the F1
    validator can check it. Without `constituent_table`, the table is the sets' own constituents.
    With `astro_tables`, the release carries them and a convention that predicts with f and u names
    the first one, as the build does; without them the release has none (the build's, KP0b)."""
    conv = adapter.convention()
    if astro_tables and isinstance(conv, dict) and conv.get("nodal_handling") == "f_u_at_prediction" \
            and "astro_table_id" not in conv:
        conv = {**conv, "astro_table_id": astro_tables[0].get("astro_table_id")}
    lics = adapter_licences(adapter)
    source = _attr(adapter, "source")
    upstream_sid: dict[str, str] = {}
    for i, ss in enumerate(sets, 1):
        cs = ss.constant_set if isinstance(ss, SourceSet) else None
        if isinstance(cs, dict) and isinstance(cs.get("source_record_id"), str):
            upstream_sid.setdefault(cs["source_record_id"], _station_id(i))
    stations = []
    table: dict[str, dict] = {}
    version = conv.get("constituent_table_version") if isinstance(conv, dict) else None
    for i, ss in enumerate(sets, 1):
        if not isinstance(ss, SourceSet):
            continue
        sid = _station_id(i)
        hint = ss.station_hint if isinstance(ss.station_hint, dict) else {}
        st = {"station_id": sid, "status": "active",
              **{k: hint[k] for k in STATION_HINT_KEYS + OPTIONAL_HINT_KEYS if k in hint}}
        st["constant_sets"] = []
        st["recommended_set_id"] = None
        if isinstance(ss.constant_set, dict):
            cs = {**ss.constant_set, "set_id": f"{sid}/{ss.constant_set.get('source', source)}"}
            if isinstance(cs.get("provenance"), dict) and "build" not in cs["provenance"]:
                # the build adds provenance.build, as it adds set_id (this shell is a check, not a release)
                cs["provenance"] = {**cs["provenance"], "build": {"build_commit": CONTRACT_BUILD_COMMIT}}
            st["constant_sets"].append(cs)
            st["recommended_set_id"] = cs["set_id"]
            bins = cs.get("current_bins") if isinstance(cs.get("current_bins"), list) else []
            for c in (cs.get("constituents") or []) + [c for b in bins if isinstance(b, dict)
                                                       for c in b.get("constituents") or []]:
                if isinstance(c, dict) and isinstance(c.get("name"), str):
                    table.setdefault(c["name"], {"constituent_table_version": version, "name": c["name"],
                                                 "speed_deg_per_hour": c.get("speed_deg_per_hour")})
        if isinstance(ss.subordinate_offsets, dict) and "current_offsets" in ss.subordinate_offsets:
            st["current_offsets"] = []
            for co in ss.subordinate_offsets["current_offsets"] or []:
                co = dict(co) if isinstance(co, dict) else {}
                ref = co.pop("reference_source_record_id", None)
                co.setdefault("reference_station_id", upstream_sid.get(ref, f"OTC-UNRESOLVED-{ref}"))
                st["current_offsets"].append(co)
        elif isinstance(ss.subordinate_offsets, dict):
            off = dict(ss.subordinate_offsets)
            ref = off.pop("reference_source_record_id", None)
            off.setdefault("reference_station_id", upstream_sid.get(ref, f"OTC-UNRESOLVED-{ref}"))
            st["subordinate_offsets"] = off
        stations.append(st)
    # The first speed per name wins here; check_contract reports sets that disagree. A source
    # with no constants (gauge records, a station list) gives an empty table: the release-level
    # table is the build's, and check_contract does not fault the adapter for it.
    rows = constituent_table if constituent_table is not None else [table[n] for n in sorted(table)]
    release = {
        "format_version": FORMAT_VERSION,
        "release": {"datestamp": "20000101", "created": "2000-01-01T00:00:00Z", "concept_doi": None,
                    "min_reader_version": FORMAT_VERSION, "fit_min_good_hours": CONTRACT_FIT_MIN_GOOD_HOURS,
                    "source_versions": {source if isinstance(source, str) else "?": fetched.source_version}},
        "conventions": [conv], "licences": lics, "constituents": rows, "stations": stations,
    }
    if astro_tables:
        release["astro_tables"] = list(astro_tables)
    return release


def _attribute_errors(attrs: dict, errors: list[str]) -> None:
    EXPECTED = _sources().EXPECTED
    source, st, q = attrs["source"], attrs["source_type"], attrs["quantity"]
    if not isinstance(source, str) or not SOURCE_RE.fullmatch(source):
        errors.append(f"source {source!r} must match [a-z0-9-]+")
    if st not in SOURCE_TYPES:
        errors.append(f"source_type {st!r} is not one of {SOURCE_TYPES}")
    if q not in QUANTITIES:
        errors.append(f"quantity {q!r} is not one of {QUANTITIES}")
    v = attrs["adapter_version"]
    if not isinstance(v, str) or not v:
        errors.append(f"adapter_version {v!r} must be a non-empty string")
    sample = attrs["contract_sample"]
    if not isinstance(sample, tuple) or not sample or not all(isinstance(s, str) and s for s in sample):
        errors.append(f"contract_sample {sample!r} must be a non-empty tuple of upstream ids")
    spec = EXPECTED.get(source) if isinstance(source, str) else None
    if spec is None:
        errors.append(f"source {source!r} is not one of the {len(EXPECTED)} registered sources "
                      f"({', '.join(sorted(EXPECTED))})")
    else:
        if st != spec.source_type:
            errors.append(f"source_type {st!r} differs from the registry ({spec.source_type!r})")
        if q != spec.quantity:
            errors.append(f"quantity {q!r} differs from the registry ({spec.quantity!r})")


def _file_errors(fetched: FetchResult, errors: list[str]) -> set[str]:
    """Checks each fetched file against the cache; returns the SHA-256s that match their bytes."""
    good: set[str] = set()
    seen: set[str] = set()
    for f in fetched.files:
        if f.path in seen:
            errors.append(f"fetched file {f.path} is listed twice (duplicate path)")
            continue
        seen.add(f.path)
        p = Path(fetched.cache_dir) / f.path
        if not p.is_file():
            errors.append(f"fetched file {f.path} is not in the cache directory")
            continue
        if not SHA256_RE.fullmatch(f.sha256 or ""):
            errors.append(f"fetched file {f.path}: sha256 {f.sha256!r} is not a lower-case SHA-256")
        if sha256_file(p) != f.sha256 or p.stat().st_size != f.size:
            errors.append(f"fetched file {f.path}: sha256/size differ from the cache")
        else:
            good.add(f.sha256)
    return good


def _set_id(ss: SourceSet, alias_system) -> tuple[str, object]:
    """(kind, upstream id) of a set: its constant_set's source_record_id, or for a
    subordinate-only set its station_hint alias for the source."""
    if isinstance(ss.constant_set, dict):
        return "source_record_id", ss.constant_set.get("source_record_id")
    aliases = ss.station_hint.get("aliases") if isinstance(ss.station_hint, dict) else None
    return "station_hint.aliases", aliases.get(alias_system) if isinstance(aliases, dict) else None


def _set_errors(attrs, fetched, sets, keys, conv, lics, good_sha: set[str], errors: list[str]) -> None:
    source = attrs["source"]
    alias_system = attrs.get("alias_system") or source
    seen: set[str] = set()
    refs: set[str] = set()
    for ss in sets:
        if isinstance(ss, SourceSet) and isinstance(ss.constant_set, dict) \
                and isinstance(ss.constant_set.get("source_record_id"), str):
            refs.add(ss.constant_set["source_record_id"])
    for i, ss in enumerate(sets):
        where = f"set {i}"
        if not isinstance(ss, SourceSet):
            errors.append(f"{where} ({type(ss).__name__}) is not a SourceSet")
            continue
        if ss.constant_set is None and ss.subordinate_offsets is None:
            errors.append(f"{where} has neither a constant_set nor subordinate_offsets")
        hint = ss.station_hint if isinstance(ss.station_hint, dict) else {}
        for k in STATION_HINT_KEYS:
            if k not in hint:
                errors.append(f"{where}: station_hint is missing {k}")
        kind, rid = _set_id(ss, alias_system)
        if ss.constant_set is not None and not isinstance(ss.constant_set, dict):
            errors.append(f"{where}: constant_set is not a dict")
        elif not isinstance(rid, str) or not rid:
            what = ("a subordinate-only set needs its upstream id in station_hint.aliases"
                    f"[{alias_system!r}]" if kind == "station_hint.aliases" else "source_record_id is missing")
            errors.append(f"{where}: {what} (got {rid!r})")
        else:
            where = f"set {i} ({rid})"
            if rid in seen:
                errors.append(f"duplicate source_record_id {rid!r}")
            seen.add(rid)
            if rid not in keys:
                errors.append(f"{where}: no watch key for {rid!r}")
        if ss.subordinate_offsets is not None and hint.get("type") != "subordinate":
            errors.append(f"{where}: a set with subordinate_offsets needs station_hint type 'subordinate'")
        cs = ss.constant_set
        if isinstance(cs, dict):
            if hint.get("type") == "subordinate" and ss.subordinate_offsets is None:
                errors.append(f"{where}: a subordinate station_hint needs subordinate_offsets")
            if "set_id" in cs:
                errors.append(f"{where}: set_id is assigned by the build, not the adapter")
            for k, want in (("source", source), ("source_type", attrs["source_type"]),
                            ("quantity", attrs["quantity"]), ("source_version", fetched.source_version)):
                if k in cs and cs[k] != want:
                    errors.append(f"{where}: {k} {cs[k]!r} differs from the adapter's {want!r}")
            if "convention_id" in cs and cs["convention_id"] != conv.get("convention_id"):
                errors.append(f"{where}: convention_id {cs['convention_id']!r} is not the adapter's "
                              f"convention {conv.get('convention_id')!r}")
            lic_ids = [x.get("licence_id") for x in lics]
            if "licence_id" in cs and cs["licence_id"] not in lic_ids:
                errors.append(f"{where}: licence_id {cs['licence_id']!r} is not one of the adapter's "
                              f"licences {lic_ids!r}")
            prov = cs.get("provenance")
            if not isinstance(prov, dict) or prov.get("adapter_version") != attrs["adapter_version"]:
                errors.append(f"{where}: provenance.adapter_version must be {attrs['adapter_version']!r}")
            if isinstance(prov, dict) and "input_sha256" in prov:
                inp = prov["input_sha256"]
                bad = [h for h in inp if h not in good_sha] if isinstance(inp, list) else inp
                if not isinstance(inp, list) or not inp or bad:
                    errors.append(f"{where}: provenance.input_sha256 {bad!r} does not name fetched "
                                  "files whose bytes match")
        off = ss.subordinate_offsets
        if off is not None:
            if not isinstance(off, dict):
                errors.append(f"{where}: subordinate_offsets is not a dict")
            elif "current_offsets" in off:
                cos = off["current_offsets"]
                if set(off) != {"current_offsets"} or not isinstance(cos, list) or not cos:
                    errors.append(f"{where}: current subordinate_offsets must be "
                                  "{'current_offsets': [one or more offsets]}")
                for co in cos if isinstance(cos, list) else ():
                    ref = co.get("reference_source_record_id") if isinstance(co, dict) else None
                    if isinstance(co, dict) and "reference_station_id" in co:
                        errors.append(f"{where}: current_offsets reference_station_id is assigned by the "
                                      "build; name the reference by reference_source_record_id")
                    elif not isinstance(ref, str) or not ref:
                        errors.append(f"{where}: current_offsets reference_source_record_id is missing "
                                      f"(got {ref!r})")
                    elif ref not in refs:
                        errors.append(f"{where}: current_offsets reference_source_record_id {ref!r} "
                                      "is not a set in this fetch")
            elif "reference_station_id" in off:
                errors.append(f"{where}: subordinate_offsets.reference_station_id is assigned by the "
                              "build; name the reference by reference_source_record_id")
            else:
                ref = off.get("reference_source_record_id")
                if not isinstance(ref, str) or not ref:
                    errors.append(f"{where}: subordinate_offsets reference_source_record_id is missing "
                                  f"(got {ref!r})")
                elif ref not in refs:
                    errors.append(f"{where}: subordinate_offsets reference_source_record_id {ref!r} "
                                  "is not a set in this fetch")


def _speed_errors(sets, errors: list[str]) -> None:
    """Sets that give one constituent two different speeds (the release table can hold one)."""
    first: dict[str, tuple[object, int]] = {}
    for i, ss in enumerate(sets):
        cs = ss.constant_set if isinstance(ss, SourceSet) else None
        cons = cs.get("constituents") if isinstance(cs, dict) else None
        for c in cons if isinstance(cons, list) else ():
            if not (isinstance(c, dict) and isinstance(c.get("name"), str)):
                continue
            speed = c.get("speed_deg_per_hour")
            if c["name"] not in first:
                first[c["name"]] = (speed, i)
            elif first[c["name"]][0] != speed:
                was, j = first[c["name"]]
                errors.append(f"set {i}: constituent {c['name']} speed {speed!r} conflicts with "
                              f"speed {was!r} in set {j}")


def _record_errors(attrs, fetched, records, keys, errors: list[str]) -> None:
    source = attrs["source"]
    seen: set[str] = set()
    prefix = f"{source}:"
    for i, r in enumerate(records):
        if not isinstance(r, GaugeRecord):
            errors.append(f"record {i} ({type(r).__name__}) is not a GaugeRecord")
            continue
        where = f"record {r.record_id}"
        if r.source != source:
            errors.append(f"{where}: record source {r.source!r} is not the adapter's {source!r}")
        if r.source_version != fetched.source_version:
            errors.append(f"{where}: source_version {r.source_version!r} differs from the fetch's "
                          f"{fetched.source_version!r}")
        if r.record_id in seen:
            errors.append(f"duplicate record_id {r.record_id!r}")
        seen.add(r.record_id)
        if not r.record_id.startswith(prefix) or len(r.record_id) == len(prefix):
            errors.append(f"{where}: record_id must start with {prefix!r} and name the upstream id")
        else:
            upstream = r.record_id[len(prefix):]
            if upstream not in keys:
                errors.append(f"{where}: no watch key for {upstream!r}")
        if len(r) == 0:
            errors.append(f"{where}: no samples")
        label = r.declared_time_base
        if not isinstance(label, str) or not _time_base_label_re().search(label):
            errors.append(f"{where}: declared_time_base {label!r} is not a format 1.0 time_base_label")


@dataclass
class ContractRun:
    """What `run_contract` checked: the errors and the output it was checked on."""

    errors: list[str]
    sets: list = field(default_factory=list)
    records: list = field(default_factory=list)
    keys: dict = field(default_factory=dict)


def run_contract(adapter, fetched, *, constituent_table: list[dict] | None = None,
                 astro_tables: list[dict] | None = None) -> ContractRun:
    """`check_contract`, keeping the sets, records and watch keys it checked (each called once)."""
    errors: list[str] = []
    attrs = {k: _attr(adapter, k) for k in
             ("source", "source_type", "quantity", "adapter_version", "contract_sample", "alias_system")}
    _attribute_errors(attrs, errors)
    if not isinstance(fetched, FetchResult):
        errors.append(f"fetch returned {type(fetched).__name__}, not a FetchResult")
        return ContractRun(errors)
    if fetched.source != attrs["source"]:
        errors.append(f"FetchResult.source {fetched.source!r} is not the adapter's {attrs['source']!r}")
    if not fetched.source_version:
        errors.append("FetchResult.source_version is empty")
    if not fetched.files:
        errors.append("FetchResult lists no fetched files")
    good_sha = _file_errors(fetched, errors)

    keys = _call(errors, adapter, "watch_keys", fetched)
    if keys is not _FAILED and (not isinstance(keys, dict) or not keys):
        errors.append("watch_keys returned no keys")
    keys = keys if isinstance(keys, dict) else {}
    for k, v in sorted(keys.items(), key=lambda kv: str(kv[0])):
        if not isinstance(k, str) or not k:
            errors.append(f"watch key {k!r} is not a non-empty string")
        if not isinstance(v, str) or not SHA256_RE.fullmatch(v):
            errors.append(f"watch key {k!r}: {v!r} is not a lower-case SHA-256")

    conv = _call(errors, adapter, "convention")
    lics = _licences(errors, adapter)
    conv_ok, lic_ok = conv is not _FAILED, lics is not _FAILED
    conv = conv if isinstance(conv, dict) else {}
    lics = lics if lics is not _FAILED else []
    if "astro_table_id" in conv:
        errors.append(f"convention astro_table_id {conv['astro_table_id']!r} is assigned by the build "
                      "(its astro tables, KP0b), not the adapter")
    sets: list = []
    records: list = []
    if _expected_output(attrs["source"], attrs["source_type"]) == "records":
        got = _call(errors, adapter, "records", fetched, materialise=True)
        if got is not _FAILED:
            records = got
            if not records:
                errors.append("no records: the fetch produced no GaugeRecord")
        _record_errors(attrs, fetched, records, keys, errors)
    else:
        got = _call(errors, adapter, "sets", fetched, materialise=True)
        if got is not _FAILED:
            sets = got
            if not sets:
                errors.append("no sets: the fetch produced no SourceSet")
        _set_errors(attrs, fetched, sets, keys, conv, lics, good_sha, errors)
        _speed_errors(sets, errors)
    if conv_ok and lic_ok:
        class _Fixed:   # the convention and licence already fetched (each adapter method runs once)
            source = attrs["source"]
            convention = staticmethod(lambda: conv)
            licences = staticmethod(lambda: lics)
        release = assemble_release(_Fixed, sets, fetched, constituent_table, astro_tables)
        rel_errors = [f"release: {e}" for e in
                      validate_release(release)]
        if not release["constituents"] and constituent_table is None:
            rel_errors = [e for e in rel_errors if e != EMPTY_TABLE_ERROR]
        if not astro_tables and "astro_table_id" not in conv:
            rel_errors = [e for e in rel_errors if e not in BUILD_OWNED_ASTRO_ERRORS]
        errors += rel_errors
    return ContractRun(errors, sets, records, keys)


def check_contract(adapter, fetched: FetchResult, *, constituent_table: list[dict] | None = None,
                   astro_tables: list[dict] | None = None) -> list[str]:
    """Every way `adapter`'s output for `fetched` breaks the contract. Empty list = passes."""
    return run_contract(adapter, fetched, constituent_table=constituent_table, astro_tables=astro_tables).errors


def assert_contract(adapter, fetched: FetchResult, **kw) -> None:
    errors = check_contract(adapter, fetched, **kw)
    if errors:
        raise ContractError(getattr(adapter, "source", "?"), errors)


def _load(spec: str):
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def _fail(errors: list[str]) -> int:
    print(f"CONTRACT FAIL: {len(errors)} error(s)")
    for e in errors:
        print(f"  - {e}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m otc_pipeline.adapter",
                                 description="Run the adapter contract on one adapter's live fetch.")
    ap.add_argument("adapter", help="module:Class, or a source name registered in SOURCES")
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--only", nargs="+", metavar="ID", help="upstream ids to fetch (at least one)")
    a = ap.parse_args(argv)
    _src = _sources()
    EXPECTED, SOURCES = _src.EXPECTED, _src.SOURCES
    if a.adapter in SOURCES:
        cls = SOURCES[a.adapter]
    elif ":" not in a.adapter:
        known = "is not one of the registered sources" if a.adapter not in EXPECTED else \
            f"has no registered adapter yet (slot {EXPECTED[a.adapter].slot})"
        return _fail([f"source {a.adapter!r} {known}; no registered adapter. Pass module:Class "
                      f"to run an unregistered adapter (registered: {', '.join(sorted(SOURCES)) or 'none'})"])
    else:
        try:
            cls = _load(a.adapter)
        except Exception as e:  # noqa: BLE001
            return _fail([f"cannot load {a.adapter!r}: {type(e).__name__}: {e}"])
    try:
        adapter = cls()
        fetched = adapter.fetch(a.cache, only=a.only)
    except Exception as e:  # noqa: BLE001
        return _fail([f"fetch() raised {type(e).__name__}: {e}"])
    run = run_contract(adapter, fetched)
    if isinstance(fetched, FetchResult):
        print(f"adapter {_attr(adapter, 'source')} ({type(adapter).__name__} "
              f"{_attr(adapter, 'adapter_version')}) source_version {fetched.source_version}: "
              f"{len(fetched.files)} file(s), {len(run.sets)} set(s), {len(run.records)} record(s), "
              f"{len(run.keys)} watch key(s)")
        for f in fetched.files:
            print(f"  file {f.path} {f.size} B sha256 {f.sha256} from {f.source_url}")
    if run.errors:
        return _fail(run.errors)
    print("CONTRACT PASS")
    return 0


if __name__ == "__main__":
    # Run the imported module's main: under `python -m` this file is also `__main__`, whose
    # classes are not the ones adapters build (FetchResult would fail its own isinstance check).
    from otc_pipeline.adapter import main as _main
    sys.exit(_main())
