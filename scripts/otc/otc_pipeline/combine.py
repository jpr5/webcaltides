"""C1: the agency sets in the release, next to the GESLA build (RELEASE-AGENCY).

    uv run python -m otc_pipeline.combine mint --agency-cache ROOT <gesla_build inputs> [--check] [--report F]

The combined build (otc_pipeline.build_otc, C2a) builds the GESLA release (gesla_build.build_parts: GESLA fits and
the TICON-4 carry, unchanged) and then merges the official agency sets into it (`merge`):

- sources: AGENCY_SOURCES (kartverket, linz, jma, noaa tide with its subordinates, noaa-currents with its
  subordinates), read from the agency cache with the network blocked (`load_agency`);
- positions: the committed corrections (data/otc/position_corrections.json, `apply_corrections`);
- station ids: every agency record `<source>:<upstream id>` has its id in the committed station map
  (data/otc/station_ids.json), under the map's stability rules (station_map: an id is never removed or reused, a
  record keeps its station). `mint_agency` gives the new ones their ids by the join rule; the build never writes
  the map, and an agency record with no id stops it;
- the join rule (owner-delegated decision 1, 2026-10-11; same-gauge first, fix round 1): a water-level reference
  set with constants joins (1) the GESLA station that publishes a GESLA record of the same agency gauge
  (`-<id>-usa-noaa`, Kartverket's NHS codes) within SAME_ID_RADIUS_KM; else (2) the GESLA station of the release
  within JOIN_RADIUS_KM when there is one; else (3) of two or more (ambiguous), the one whose recommended set's M2
  is nearest the agency M2 as a vector (A e^{ig}). Current stations and subordinate stations
  are always stations of their own. One station holds one alias id per alias system (the schema's `aliases.noaa` is
  a string), so of two agency records of one alias system that would join one station, the better one (M2, then
  distance) joins and the other gets its own station;
- recommendation (decision 2): official sets first, as gesla_release.recommend() ranks them (the tier step;
  REC-SKILL replaces it later). At a joined station the GESLA and TICON-4 sets stay exactly as built (their dicts
  and the station's aliases): the agency set is recommended and written first, then the station's sets in their
  built order; the agency alias is added. A station joined by the same-gauge rule publishes the agency's official
  gauge name, position and time zone (G2); the GESLA ones stay in the build report (agency.joined gesla_place)
  and the GESLA record ids in aliases.gesla. Other joined stations keep the GESLA name and position;
- subordinates: `reference_source_record_id` -> the station that holds the source's reference set in this release;
  a subordinate whose reference is not published is refused (reported);
- canaries (D8; D11 as decided for this release: every INCLUDED source passes its canary, else the build stops):
  kartverket, linz, jma and noaa against this build's GESLA fits (canary.canary); noaa-currents against NOAA's
  published current predictions (currents_canary).
"""
from __future__ import annotations

import argparse
import cmath
import copy
import json
import math
import socket
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from otc_pipeline import gesla_release as gr
from otc_pipeline import station_map as sm
from otc_pipeline.adapter import InputFile, SourceSet, adapter_licences
from otc_pipeline.canary import distance_km
from otc_pipeline.release_writer import pinned_schema

__all__ = ["AGENCY_SOURCES", "JOIN_RADIUS_KM", "AgencySource", "CombineError", "Place", "agency_speeds",
           "apply_corrections", "gesla_places", "join", "load_agency", "load_corrections", "merge", "mint_agency",
           "record_id"]

AGENCY_SOURCES = ("kartverket", "linz", "jma", "noaa", "noaa-currents")
JOIN_RADIUS_KM = 1.0
SAME_ID_RADIUS_KM = 5.0      # a GESLA record of the same agency gauge joins up to this far (positions differ)
# (country, contributor) of the GESLA records whose site code is the agency's own station id
# (record `<site>-<code>-<country>-<contributor>`: `-8443970-usa-noaa`, `-bgo-nor-nhs`)
SAME_ID_CONTRIBUTORS = {"noaa": (("usa", "noaa"),), "kartverket": (("nor", "nhs"),)}
CORRECTIONS_PATH = sm.DATA / "position_corrections.json"
POSITION_TOL_DEG = 1e-9      # a correction applies only to the position it was written for


class CombineError(ValueError):
    """The agency sets cannot be combined with the GESLA build as they are (see the message)."""


@dataclass
class AgencySource:
    """One official source as its adapter serves it from the cache."""
    source: str
    source_version: str
    convention: dict
    licences: list[dict]
    sets: list[SourceSet]
    alias_system: str
    corrections: list[dict] = field(default_factory=list)   # the position corrections applied (apply_corrections)
    tcd_dump_sha256: str | None = None   # the SHA-256 of the TCD dump the adapter's speeds came from (load_agency)


def upstream_id(ss: SourceSet, alias_system: str) -> str:
    """A set's upstream id: constant_set.source_record_id, else (a subordinate-only set) its alias (adapter.py)."""
    if ss.constant_set is not None:
        return str(ss.constant_set["source_record_id"])
    return str(ss.station_hint["aliases"][alias_system])


def record_id(source: str, upstream: str) -> str:
    """The station map's record id of an agency record."""
    return f"{source}:{upstream}"


# --- loading ----------------------------------------------------------------------------------------

@contextmanager
def no_network():
    """Every socket connection raises while inside: the build reads cached inputs only."""
    saved = socket.socket.connect, socket.create_connection

    def refuse(*a, **k):
        raise CombineError(f"network access during the build: {a[1:2] or a[:1]}; the build reads the cache only")
    socket.socket.connect = refuse
    socket.create_connection = refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.create_connection = saved


def _cached_input(cache_dir: Path, name: str) -> InputFile:
    """The InputFile of a cached file as PoliteFetcher recorded it (`<file>.meta.json`: url, fetched_at, sha256),
    which is what the adapter's own fetch hands to its loader (Fetched.input_file)."""
    meta = json.loads((cache_dir / (name + ".meta.json")).read_text())
    return InputFile(path=name, sha256=meta["sha256"], size=(cache_dir / name).stat().st_size,
                     source_url=meta["url"], fetched_at=meta["fetched_at"])


def _linz_cached(cache_dir: Path):
    """LINZ from its cache: LinzAdapter.fetch always re-reads three pages live, so the cached zip, pages and port
    files are read from the cache instead, with the inputs fetch() would pass (load_fetched checks every file
    against its SHA-256)."""
    from otc_pipeline.adapters.linz import load_fetched
    zips = sorted(p.name for p in (cache_dir / "zip").iterdir() if not p.name.endswith(".meta.json"))
    if len(zips) != 1:
        raise CombineError(f"{cache_dir}/zip: expected one cached zip, found {zips}")
    ports = sorted(p.name for p in (cache_dir / "ports").iterdir() if not p.name.endswith(".meta.json"))
    pages = ("pages/tide-constituents.html", "pages/tide-predictions-list-view.html")
    return load_fetched(cache_dir, _cached_input(cache_dir, f"zip/{zips[0]}"),
                        [_cached_input(cache_dir, f"ports/{n}") for n in ports],
                        page_files=tuple(_cached_input(cache_dir, p) for p in pages),
                        levels_file=_cached_input(cache_dir, "pages/standard-port-tidal-levels.html"))


def agency_speeds(source: str, tcd_path: Path):
    """The speeds table `source`'s adapter takes, read from the TCD dump at `tcd_path`; a dump of another TCD than
    the release's (astro_tables.TCD_SHA256, the OTC TCD) is a CombineError: the agency sets, the fits and the astro
    table are on one table, and an agency constituent the release has a row for (MA2, MB2) is never dropped."""
    from otc_pipeline.adapters import jma, linz
    from otc_pipeline.astro_tables import TABLE_ID, TCD_SHA256
    from otc_pipeline.constituent_speeds import TableSpeeds
    readers = {"noaa": TableSpeeds, "noaa-currents": TableSpeeds, "kartverket": TableSpeeds,
               "linz": linz.Speeds, "jma": jma.Speeds}
    if source not in readers:
        raise CombineError(f"{source}: no speeds table is defined for this source")
    speeds = readers[source].from_tcd_dump(Path(tcd_path))
    if speeds.tcd_sha256 != TCD_SHA256:
        raise CombineError(f"{tcd_path}: a dump of TCD {speeds.tcd_sha256}; the agency sets are read on the release's "
                           f"{TABLE_ID} ({TCD_SHA256}), the dump the fits and the astro table use")
    return speeds


def load_agency(cache_root: Path, sources: Iterable[str] = AGENCY_SOURCES, *, jma_year: int, tcd_path: Path,
                corrections: list[dict] | None = None) -> dict[str, AgencySource]:
    """Each source's sets from `cache_root/<source>`, with the network blocked, positions corrected
    (apply_corrections with `corrections`, default the committed file). Every adapter reads its speeds from the
    TCD dump at `tcd_path` (agency_speeds: the OTC TCD's, the dump the build's fits use); each source records the
    dump's SHA-256 (AgencySource.tcd_dump_sha256)."""
    from otc_pipeline.sources import SOURCES
    corrections = load_corrections() if corrections is None else corrections
    out = {}
    with no_network():
        for src in sources:
            adapter = SOURCES[src](**({"year": jma_year} if src == "jma" else {}),
                                   speeds=agency_speeds(src, tcd_path))
            cache = Path(cache_root) / src
            fetched = _linz_cached(cache) if src == "linz" else adapter.fetch(cache)
            sets = list(adapter.sets(fetched))
            out[src] = AgencySource(src, fetched.source_version, adapter.convention(), adapter_licences(adapter),
                                    sets, getattr(adapter, "alias_system", None) or src,
                                    tcd_dump_sha256=adapter.speeds.dump_sha256)
    for a in out.values():
        apply_corrections(a, [c for c in corrections if c["source"] == a.source])
    unused = [c for c in corrections if c["source"] in out
              and not any(x["upstream_id"] == c["upstream_id"] for x in out[c["source"]].corrections)]
    if unused:
        raise CombineError(f"position corrections for records the source no longer serves: "
                           f"{[(c['source'], c['upstream_id']) for c in unused]}")
    return out


# --- position corrections ---------------------------------------------------------------------------

def load_corrections(path: Path = CORRECTIONS_PATH) -> list[dict]:
    d = json.loads(Path(path).read_text())
    if d.get("format") != 1:
        raise CombineError(f"{path}: format {d.get('format')!r}, expected 1")
    seen = set()
    for c in d["corrections"]:
        key = (c["source"], c["upstream_id"])
        if key in seen:
            raise CombineError(f"{path}: two corrections for {key}")
        seen.add(key)
    return list(d["corrections"])


def apply_corrections(agency: AgencySource, corrections: list[dict]) -> None:
    """Replace the position of each corrected record (in place). A row applies only while the source serves the
    position it was written for (else CombineError: the upstream changed; review the row); the time zone and
    country of the corrected position must be the row's (gesla_release.station_timezone)."""
    by_up = {c["upstream_id"]: c for c in corrections}
    for i, ss in enumerate(agency.sets):
        up = upstream_id(ss, agency.alias_system)
        c = by_up.get(up)
        if c is None:
            continue
        h = ss.station_hint
        pub = c["published"]
        if abs(float(h["lat"]) - pub["lat"]) > POSITION_TOL_DEG or abs(float(h["lon"]) - pub["lon"]) > POSITION_TOL_DEG:
            raise CombineError(f"{agency.source} {up}: the source now serves ({h['lat']}, {h['lon']}), the correction "
                               f"was written for ({pub['lat']}, {pub['lon']}); review data/otc/position_corrections.json")
        lat, lon = float(c["corrected"]["lat"]), float(c["corrected"]["lon"])
        tz = gr.station_timezone(lat, lon)
        if tz != c["timezone"] or h.get("country") != c["country"]:
            raise CombineError(f"{agency.source} {up}: the corrected position is in {tz} (the row says {c['timezone']}) "
                               f"and the hint's country is {h.get('country')} (the row says {c['country']})")
        agency.sets[i] = SourceSet({**h, "lat": lat, "lon": lon, "timezone": tz}, ss.constant_set,
                                   ss.subordinate_offsets)
        agency.corrections.append({"upstream_id": up, "from": {"lat": h["lat"], "lon": h["lon"]},
                                   "to": {"lat": lat, "lon": lon}, "timezone": tz, "reason": c["reason"]})


# --- the join rule ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Place:
    """An active station of the GESLA release: position and its recommended set's M2 (None without one)."""
    station_id: str
    lat: float
    lon: float
    m2: complex | None
    records: tuple[str, ...] = ()     # the GESLA record ids the station publishes (aliases.gesla)


def _m2(cs: Mapping | None) -> complex | None:
    for c in (cs or {}).get("constituents") or []:
        if c["name"] == "M2":
            return cmath.rect(float(c["amplitude_m"]), math.radians(float(c["phase_deg"])))
    return None


def gesla_places(release: Mapping) -> list[Place]:
    """The active water-level stations of a GESLA release, as join candidates."""
    out = []
    for st in release["stations"]:
        if st["status"] != "active":
            continue
        rec = next((cs for cs in st["constant_sets"] if cs["set_id"] == st["recommended_set_id"]), None)
        if rec is not None and rec["quantity"] != "water_level":
            continue
        out.append(Place(st["station_id"], float(st["lat"]), float(st["lon"]), _m2(rec),
                         tuple((st.get("aliases") or {}).get("gesla", ()))))
    return out


def _joins(ss: SourceSet) -> bool:
    cs = ss.constant_set
    return (cs is not None and cs.get("quantity") == "water_level" and cs.get("qc_status") != "no_constants"
            and ss.station_hint.get("type") == "reference" and ss.subordinate_offsets is None)


def _agency_codes(source: str, p: Place) -> set[str]:
    """The site codes of the station's GESLA records that are gauges of this agency (country and contributor of
    SAME_ID_CONTRIBUTORS), lower case."""
    agencies = SAME_ID_CONTRIBUTORS.get(source, ())
    out = set()
    for rid in p.records:
        parts = rid.rsplit("-", 3)
        if len(parts) == 4 and (parts[2].lower(), parts[3].split("_")[0].lower()) in agencies:
            out.add(parts[1].lower())
    return out


def _same_id(source: str, upstream: str, p: Place) -> bool:
    """The station publishes a GESLA record of this agency gauge: country and contributor of the source, site
    code = the id."""
    return upstream.lower() in _agency_codes(source, p)


def _other_gauge(source: str, upstream: str, p: Place) -> bool:
    """The station publishes a GESLA record of ANOTHER gauge of this agency: rules 2 and 3 never join it."""
    return bool(_agency_codes(source, p) - {upstream.lower()})


def join(ss: SourceSet, places: list[Place], radius_km: float = JOIN_RADIUS_KM, *,
         source: str = "") -> tuple[str | None, dict]:
    """(GESLA station id or None, evidence) for one agency set by the join rule, in this order: (1) a station
    holding a GESLA record of the same agency gauge (SAME_ID_CONTRIBUTORS) within SAME_ID_RADIUS_KM; (2) the one
    station within radius_km; (3) of several within radius_km, the best M2 vector match."""
    if not _joins(ss):
        return None, {"how": "own_station", "why": "current or subordinate stations are stations of their own"}
    lat, lon = float(ss.station_hint["lat"]), float(ss.station_hint["lon"])
    up = str(ss.constant_set["source_record_id"])
    same = sorted((distance_km(lat, lon, p.lat, p.lon), p.station_id, p) for p in places
                  if abs(p.lat - lat) < 0.1 and _same_id(source, up, p))
    same = [c for c in same if c[0] <= SAME_ID_RADIUS_KM]
    if same:
        d, s, p = same[0]
        m2 = _m2(ss.constant_set)
        return s, {"how": "same_id", "distance_km": round(d, 4),
                   "m2_vector_diff_m": None if m2 is None or p.m2 is None else round(abs(m2 - p.m2), 5),
                   "candidates": [{"station_id": x, "distance_km": round(dd, 4)} for dd, x, _ in same]}
    near = [p for p in places if abs(p.lat - lat) < 0.05 and distance_km(lat, lon, p.lat, p.lon) <= radius_km]
    other = sorted(p.station_id for p in near if _other_gauge(source, up, p))
    cands = sorted((distance_km(lat, lon, p.lat, p.lon), p.station_id, p) for p in near
                   if not _other_gauge(source, up, p))
    if not cands:
        return None, {"how": "no_station_within_radius",
                      **({"other_gauge_stations": other} if other else {})}
    m2 = _m2(ss.constant_set)

    def diff(p: Place) -> float | None:
        return None if m2 is None or p.m2 is None else round(abs(m2 - p.m2), 5)
    ev = {"candidates": [{"station_id": s, "distance_km": round(d, 4), "m2_vector_diff_m": diff(p)}
                         for d, s, p in cands], **({"other_gauge_stations": other} if other else {})}
    if len(cands) == 1:
        d, s, p = cands[0]
        return s, {"how": "unambiguous", "distance_km": round(d, 4), "m2_vector_diff_m": diff(p), **ev}
    scored = [(diff(p), d, s) for d, s, p in cands if diff(p) is not None]
    if scored:
        dm2, d, s = min(scored)
        return s, {"how": "m2_vector", "distance_km": round(d, 4), "m2_vector_diff_m": dm2, **ev}
    d, s, _ = cands[0]
    return s, {"how": "nearest_no_m2", "distance_km": round(d, 4), "m2_vector_diff_m": None, **ev}


def mint_agency(smap: sm.StationMap, agency: Mapping[str, AgencySource], places: list[Place]
                ) -> tuple[sm.StationMap, list[dict]]:
    """The station map with an id for every agency record (stability rules of station_map: a record already in
    the map keeps its station; a new one joins a GESLA station by `join` or gets a new id, founder = its record
    id, never one taken by an active or retired station). Returns (map, one row per new record)."""
    stations = {sid: {"founder": st["founder"], "records": list(st["records"])} for sid, st in smap.stations.items()}
    old_of = smap.record_ids()
    taken = set(stations) | set(smap.retired)
    # the sources whose records a station already holds (one record per source and station)
    held: set[tuple[str, str]] = set()
    for sid, st in stations.items():
        for r in st["records"]:
            if r.split(":", 1)[0] in AGENCY_SOURCES:
                held.add((sid, r.split(":", 1)[0]))
    proposals = []
    for a in agency.values():
        for ss in a.sets:
            rid = record_id(a.source, upstream_id(ss, a.alias_system))
            if rid in old_of:
                continue
            sid, ev = join(ss, places, source=a.source)
            proposals.append((a, ss, rid, sid, ev))
    # one record per source and station (one alias id per alias system: the schema's aliases.noaa is a string):
    # the best claimant joins (M2 vector difference, then distance, then record id); the others get their own
    claims = defaultdict(list)
    for p in proposals:
        a, ss, rid, sid, ev = p
        if sid is not None:
            claims[(sid, a.source)].append(p)
    losers = set()
    for key, ps in claims.items():
        def rank_key(p):
            dm2 = p[4].get("m2_vector_diff_m")
            return (p[4]["how"] != "same_id", math.inf if dm2 is None else dm2, p[4]["distance_km"], p[2])
        ranked = sorted(ps, key=rank_key)
        start = 0 if key in held else 1
        losers |= {p[2] for p in ranked[start:]}
    rows = []
    for a, ss, rid, sid, ev in sorted(proposals, key=lambda p: p[2]):
        if rid in losers:
            ev = {**ev, "how": "source_already_joined", "would_join": sid}
            sid = None
        if sid is None:
            sid = sm.new_id(rid, taken)
            taken.add(sid)
            stations[sid] = {"founder": rid, "records": [rid]}
            joined = False
        else:
            stations[sid]["records"] = sorted(set(stations[sid]["records"]) | {rid})
            joined = True
        rows.append({"record": rid, "station_id": sid, "joined": joined, **ev})
    out = sm.StationMap(stations=stations, retired=smap.retired, proposals=smap.proposals, review=smap.review,
                        registry=smap.registry, inputs=smap.inputs, time_base_defects=smap.time_base_defects,
                        time_base_suspects=smap.time_base_suspects, split_flags=smap.split_flags)
    sm._check(out)
    return out, rows


# --- merging into the release -----------------------------------------------------------------------

@dataclass
class AgencyReport:
    """What the merge did per source: sets offered and published, stations joined and new, and refusals."""
    sources: dict = field(default_factory=dict)
    refused: list[dict] = field(default_factory=list)
    joined: list[dict] = field(default_factory=list)
    canaries: dict = field(default_factory=dict)
    corrections: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"sources": self.sources, "canaries": self.canaries, "position_corrections": self.corrections,
                "refused": sorted(self.refused, key=lambda r: r["record"]),
                "joined": sorted(self.joined, key=lambda r: r["record"])}


def _alias_systems() -> set[str]:
    return set(pinned_schema()["$defs"]["active_station"]["properties"]["aliases"]["properties"])


def _span_end_s(cs: Mapping) -> int:
    from datetime import datetime
    end = (cs.get("record_span") or {}).get("end")
    if not end:
        return 0
    return int(datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp())


def _candidate(cs: dict, commercial: bool) -> gr.Candidate:
    return gr.Candidate(cs, 0.0, _span_end_s(cs), originator=True, commercial=commercial)


def _set_names(cs: Mapping) -> set[str]:
    names = {c["name"] for c in cs.get("constituents") or []}
    for b in cs.get("current_bins") or []:
        names |= {c["name"] for c in b.get("constituents") or []}
    return names


def agency_names(agency: Mapping[str, AgencySource]) -> set[str]:
    """Every constituent name of the agency sets (the astro table must have them)."""
    return {n for a in agency.values() for ss in a.sets if ss.constant_set for n in _set_names(ss.constant_set)}


def merge(release: dict, agency: Mapping[str, AgencySource], *, station_ids: Mapping[str, str], build_commit: str,
          table_id: str, canaries: Mapping[str, dict]) -> tuple[dict, AgencyReport]:
    """The release with the agency sets merged in (module text), and the agency report. `release` (the GESLA
    build's document) is not changed: the result is a new document. `canaries`: source -> its
    convention.canary object ({"status": "pass" | "fail", ...}); a source whose canary did not pass stops the
    build (D11). Raises CombineError when an agency record has no station id, when an alias id would be held by
    two stations, or when licences of one id differ."""
    failed = sorted(s for s in agency if (canaries.get(s) or {}).get("status") != "pass")
    if failed:
        raise CombineError(f"D11: the canary of {failed} did not pass "
                           f"({ {s: canaries.get(s) for s in failed} }); the release is not built")
    out = copy.deepcopy(dict(release))
    rep = AgencyReport()
    systems = _alias_systems()
    licences = {lic["licence_id"]: lic for lic in out["licences"]}
    commercial = {}
    for a in agency.values():
        for lic in a.licences:
            have = licences.setdefault(lic["licence_id"], lic)
            if have != lic:
                raise CombineError(f"licence {lic['licence_id']!r}: {a.source} gives another object than the release has")
            commercial[lic["licence_id"]] = bool(lic.get("commercial_use"))
    by_id = {st["station_id"]: st for st in out["stations"]}
    gesla_active = {sid for sid, st in by_id.items() if st["status"] == "active"}
    # pass 1: reference sets (sets with constants) -> per station
    entries: dict[str, list[tuple[AgencySource, SourceSet, dict | None]]] = defaultdict(list)
    ref_station: dict[str, str] = {}          # record id -> the station that publishes its constants
    counts = {s: Counter() for s in agency}
    for a in agency.values():
        for ss in a.sets:
            up = upstream_id(ss, a.alias_system)
            rid = record_id(a.source, up)
            sid = station_ids.get(rid)
            if sid is None:
                raise CombineError(f"{rid}: no station id; run `python -m otc_pipeline.combine mint` (the committed "
                                   "station map assigns it)")
            counts[a.source]["offered"] += 1
            cs = None
            if ss.constant_set is not None:
                cs = {**copy.deepcopy(ss.constant_set), "set_id": f"{sid}/{a.source}/{up}"}
                cs["provenance"] = {**cs["provenance"], "build": {"build_commit": build_commit}}
                ref_station[rid] = sid
            entries[sid].append((a, ss, cs))
    # pass 2: subordinates map their references; one whose reference is not published is refused
    keep: dict[str, list] = {}
    for sid, items in entries.items():
        ok = []
        for a, ss, cs in items:
            rid = record_id(a.source, upstream_id(ss, a.alias_system))
            so = copy.deepcopy(ss.subordinate_offsets)
            if so is not None:
                refs = ([co.get("reference_source_record_id") for co in so["current_offsets"]]
                        if "current_offsets" in so else [so.get("reference_source_record_id")])
                miss = sorted({r for r in refs if ref_station.get(record_id(a.source, str(r))) is None})
                if miss:
                    rep.refused.append({"record": rid, "reason": "reference_not_published",
                                        "message": f"{rid}: its reference {miss} has no published set in this release"})
                    counts[a.source]["refused"] += 1
                    if cs is not None:
                        ref_station.pop(rid, None)
                    continue
                if "current_offsets" in so:
                    for co in so["current_offsets"]:
                        co["reference_station_id"] = ref_station[record_id(a.source, str(co.pop("reference_source_record_id")))]
                else:
                    so["reference_station_id"] = ref_station[record_id(a.source, str(so.pop("reference_source_record_id")))]
            ok.append((a, ss, cs, so))
        if ok:
            keep[sid] = ok
    new_stations = []
    for sid in sorted(keep):
        items = keep[sid]
        sets = [cs for _, _, cs, _ in items if cs is not None]
        aliases = {}
        for a, ss, _, _ in items:
            for k, v in (ss.station_hint.get("aliases") or {}).items():
                if k not in systems:
                    raise CombineError(f"{sid}: alias system {k!r} is not in the schema")
                if aliases.setdefault(k, v) != v:
                    raise CombineError(f"{sid}: two {k} aliases ({aliases[k]!r}, {v!r})")
        if sid in gesla_active:
            st = by_id[sid]
            subs = [so for _, _, _, so in items if so is not None]
            if subs or any(cs is None or cs["quantity"] != "water_level" for cs in sets):
                raise CombineError(f"{sid}: only water-level reference sets join a GESLA station "
                                   f"({[record_id(a.source, upstream_id(ss, a.alias_system)) for a, ss, _, _ in items]})")
            for k, v in aliases.items():
                if k in (st.get("aliases") or {}):
                    raise CombineError(f"{sid}: already has a {k} alias {st['aliases'][k]!r}; {v!r} cannot join")
            built = list(st["constant_sets"])
            cands = [_candidate(cs, commercial[cs["licence_id"]]) for cs in sets]
            others = [_candidate(cs, True) for cs in built]
            win, how = gr.recommend(cands + others)
            if win not in cands:
                raise CombineError(f"{sid}: recommend() chose {win.cs['set_id'] if win else None}, not an official set")
            order = [win.cs] + [c.cs for c, _ in gr.rank(cands) if c is not win]
            st["constant_sets"] = order + built
            was = st["recommended_set_id"]
            st["recommended_set_id"] = win.cs["set_id"]
            st["aliases"] = dict(sorted({**(st.get("aliases") or {}), **aliases}.items()))
            built_place = {k: st[k] for k in ("name", "lat", "lon", "timezone")}
            # G2: a station that holds the agency's own gauge (the same-gauge rule) publishes the agency's official
            # gauge position and name; the GESLA ones stay in its aliases (the records) and in the build report
            here = Place(sid, float(st["lat"]), float(st["lon"]), None, tuple((st.get("aliases") or {}).get("gesla", ())))
            gauge = [(a, ss, cs) for a, ss, cs, _ in items if cs is win.cs
                     and _same_id(a.source, upstream_id(ss, a.alias_system), here)]
            if gauge:
                h = gauge[0][1].station_hint
                st.update({"name": h["name"], "lat": h["lat"], "lon": h["lon"], "timezone": h["timezone"]})
            for a, ss, cs, _ in items:
                rid = record_id(a.source, upstream_id(ss, a.alias_system))
                counts[a.source]["joined"] += 1
                counts[a.source]["published"] += 1
                counts[a.source]["official_position"] += bool(gauge and ss is gauge[0][1])
                rep.joined.append({"record": rid, "station_id": sid, "set_id": cs["set_id"],
                                   "decided_by": how["decided_by"], "was_recommended": was,
                                   "same_gauge": bool(gauge and ss is gauge[0][1]),
                                   "gesla_place": built_place,
                                   "distance_km": round(distance_km(float(ss.station_hint["lat"]),
                                                                    float(ss.station_hint["lon"]),
                                                                    float(built_place["lat"]),
                                                                    float(built_place["lon"])), 4)})
            continue
        if sid in by_id:
            raise CombineError(f"{sid}: a removed station (tombstone) of this release; agency records cannot use it")
        cands = [_candidate(cs, commercial[cs["licence_id"]]) for cs in sets]
        win, _how = gr.recommend(cands) if cands else (None, {})
        lead = next((ss for a, ss, cs, _ in items if win is not None and cs is win.cs), items[0][1])
        h = lead.station_hint
        st = {"station_id": sid, "status": "active", "name": h["name"], "country": h["country"],
              "lat": h["lat"], "lon": h["lon"], "timezone": h["timezone"], "type": h["type"],
              "aliases": dict(sorted(aliases.items())),
              "recommended_set_id": win.cs["set_id"] if win else None,
              "constant_sets": [c.cs for c in gr.set_order(win, cands)]}
        if "default_current_bin" in h:
            st["default_current_bin"] = h["default_current_bin"]
        subs = [so for _, _, _, so in items if so is not None]
        if len(subs) > 1:
            raise CombineError(f"{sid}: {len(subs)} subordinate offsets for one station")
        if subs:
            if "current_offsets" in subs[0]:
                st["current_offsets"] = subs[0]["current_offsets"]
            else:
                st["subordinate_offsets"] = subs[0]
        for a, *_ in items:
            counts[a.source]["new_station_sets"] += 1
            counts[a.source]["published"] += 1
        new_stations.append(st)
    active = [st for st in out["stations"] if st["status"] == "active"] + new_stations
    tombs = [st for st in out["stations"] if st["status"] != "active"]
    out["stations"] = sorted(active, key=lambda s: s["station_id"]) + tombs
    # release level: conventions (with the build's astro table and the canary), licences, versions, constituents
    used = {cs["licence_id"] for st in active for cs in st["constant_sets"]}
    used |= {st["subordinate_offsets"]["licence_id"] for st in active if "licence_id" in st.get("subordinate_offsets", {})}
    used |= {co["licence_id"] for st in active for co in st.get("current_offsets", []) if "licence_id" in co}
    out["licences"] = [licences[i] for i in sorted(used)]
    rows = {(r["constituent_table_version"], r["name"]): r for r in out["constituents"]}
    astro = {t["astro_table_id"]: t for t in out["astro_tables"]}[table_id]["constituents"]
    for a in agency.values():
        conv = {**a.convention, "astro_table_id": table_id, "canary": dict(canaries[a.source])}
        if any(c["convention_id"] == conv["convention_id"] for c in out["conventions"]):
            raise CombineError(f"convention {conv['convention_id']!r} is defined twice")
        out["conventions"].append(conv)
        out["release"]["source_versions"][a.source] = a.source_version
        version = a.convention["constituent_table_version"]
        for ss in a.sets:
            for n in _set_names(ss.constant_set or {}):
                if n not in astro:
                    raise CombineError(f"{a.source}: constituent {n!r} has no astro table row")
                rows.setdefault((version, n), {"constituent_table_version": version, "name": n,
                                               "speed_deg_per_hour": astro[n]["speed_deg_per_hour"]})
    out["constituents"] = [rows[k] for k in sorted(rows)]
    out["release"]["source_versions"] = dict(sorted(out["release"]["source_versions"].items()))
    for a in agency.values():
        rep.sources[a.source] = {"source_version": a.source_version, **dict(sorted(counts[a.source].items()))}
        rep.canaries[a.source] = dict(canaries[a.source])
        rep.corrections[a.source] = list(a.corrections)
    return out, rep


# --- CLI: mint ---------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.combine", description=(__doc__ or "").split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    mp = sub.add_parser("mint", help="give every agency record its station id in data/otc/station_ids.json")
    mp.add_argument("--agency-cache", type=Path, required=True)
    mp.add_argument("--check", action="store_true", help="write nothing; exit 1 when the committed map would change")
    mp.add_argument("--report", type=Path, help="write the new records' join evidence (JSON) here")
    for name in ("fit-run", "audit-dir", "verdicts-dir", "calibration", "ticon-csv", "ticon-refs", "congen-run"):
        mp.add_argument(f"--{name}", type=Path, required=True)
    mp.add_argument("--overlay-run", type=Path)
    mp.add_argument("--tcd", type=Path)
    mp.add_argument("--datestamp", required=True)
    a = ap.parse_args(argv)
    from otc_pipeline import gesla_build as gb
    from gesla.tcd_table import dump_path
    tcd = a.tcd or dump_path()
    agency = load_agency(a.agency_cache, jma_year=int(a.datestamp[:4]), tcd_path=tcd)
    parts = gb.build_parts(fit_run=a.fit_run, audit_dir=a.audit_dir, verdicts_dir=a.verdicts_dir,
                           calibration_dir=a.calibration, overlay_run=a.overlay_run, ticon_csv=a.ticon_csv,
                           ticon_refs=a.ticon_refs, congen_run=a.congen_run, datestamp=a.datestamp,
                           created=f"{a.datestamp[:4]}-{a.datestamp[4:6]}-{a.datestamp[6:]}T00:00:00Z",
                           build_commit="0000000", tcd_path=tcd, extra_names=agency_names(agency))
    smap = sm.load()
    new, rows = mint_agency(smap, agency, gesla_places(parts.release))
    how = Counter(r["how"] for r in rows)
    print(f"{len(rows)} new agency records: {dict(sorted(how.items()))}; joined {sum(r['joined'] for r in rows)}, "
          f"new stations {sum(not r['joined'] for r in rows)}")
    if a.report is not None:
        a.report.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    text = sm.dump(new)
    if a.check:
        same = sm.MAP_PATH.read_text() == text
        print("station map unchanged" if same else f"would change: {sm.MAP_PATH}")
        return 0 if same else 1
    sm.MAP_PATH.write_text(text)
    print(f"{len(new.stations)} stations, {len(new.record_ids())} records -> {sm.MAP_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
