"""A format 1.0 release document from GESLA fit documents (the GESLA side of the release build).

    build_release(docs, ...) -> (release, BuildReport)   the release is valid input for write_release;
                                                         the report lists retained findings and licence omissions

One active reference station per OTC station id, with one gesla-fit set per record and one set
recommended (recommend()). The pieces another slot owns come in as arguments, never made up here:

- `station_ids` (record id -> OTC station id): the committed station map (otc_pipeline.station_map,
  data/otc/station_ids.json), which also merges records at one gauge into one station.
- `epoch_hours` (record id -> {"member_sha256", "hours"}): each record's good hours in 2002-2020
  (data/otc/gesla_epoch_hours.csv), a ranking input; counted from the same GESLA member.
- `licences` and `licence_ids` (record id -> licence id): the per-contributor licences (L-NC).
  Required for every record: a record with no licence id stops the build (no default licence).
- `withheld` (licence id -> reason, gesla_licences.withheld_licences; required, {} for none): records
  under these licences are left out until the owner permits, each refused with the reason; a station
  left with no published set is reported in BuildReport.withheld_stations.
- `astro_tables` and `congen_run`: the per-year V0+u and f tables (KP0b: astro_tables.astro_table)
  and the congen run they come from (astro_tables.load_congen_run's CongenRun, the only accepted
  form). The release holds deep copies of the tables; write_release checks them against the run again.
  Format 1.0 requires the tables whenever a convention predicts with f and u, and the gesla-fit
  convention does, so a release cannot be built without them. Every table must be exactly that run's
  table (astro_tables.release_table_errors: the generator, 1700-2100, every value), so a hand-edited,
  stale or TEST-ONLY table stops the build. The build runs check 7 (astro_tables.astro_table_errors)
  on the document it makes and stops on an error.
- `ticon` (ticon_sets.TiconCarry, optional): the TICON-4 fallback. A withheld record whose TICON-4 record
  (same file name, else a site within 1 km) can be carried is published as a TICON-4 analysis set at the
  record's station (ticon_sets.ticon_set); the OTC fit itself stays withheld and is used only for the
  convention canary. BuildReport.carried and .not_carried say what happened to each withheld record.
- datums beyond Z0 (DT4a/DT5f/DT5g/DT6): `gesla_sets.source_set` writes only `msl_offset_m`.

The build checks the schema cannot express (its top description, checks 1-14) are not all here:
`release_writer.validate_release` runs the cross-reference checks and check 7 (the astronomical
tables); the datum checks 9-13 are DT7's.
"""
from __future__ import annotations

import copy
import json
import os
import secrets
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from gesla.tcd_table import TcdTable

from otc_pipeline.astro_tables import CongenRun, astro_table_errors, release_table_errors
from otc_pipeline.gesla_sets import (CONVENTION_ID, FIT_MIN_GOOD_HOURS, SOURCE, FitIntegrityError, NotPublishable,
                                     label_fallback, licence_of, refusal, source_set)
from otc_pipeline.release_writer import FORMAT_VERSION, BuiltRelease
from otc_pipeline import ticon_sets as ts

__all__ = ["BuildReport", "Candidate", "build_release", "gesla_convention", "rank", "recommend", "set_order",
           "station_timezone"]

ROLES_PATH = Path(__file__).resolve().parents[3] / "data" / "gesla" / "contributor_roles.csv"
INVENTORY_PATH = Path(__file__).resolve().parents[3] / "data" / "gesla" / "4.1" / "inventory.csv"

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


TIER = {"official": 0, "gauge": 1, "analysis": 2, "model": 3}   # format 1.0 source_type order (23aedbe)
RANK_STEPS = ("fallback", "tier", "time_base", "short_record", "instantaneous", "sub_hourly", "current", "originator", "commercial",
              "epoch_hours", "recency", "research_quality", "record_id")
CURRENT_S = 2 * 365.25 * 86400     # "current": ends within 2 years of the newest set
EPOCH_TIE = 0.02                   # epoch hours closer than 2 % of the larger count are a tie


def _is_short(cs: Mapping) -> bool:
    return any(f.get("flag") == "short_record" for f in cs.get("qc_flags", []))


def _good(cs: Mapping) -> float:
    return float(cs.get("record_span", {}).get("good_hours") or 0.0)


@dataclass
class Candidate:
    """One set competing for a station's recommendation, with the inputs of the rank steps."""
    cs: dict
    epoch_hours: float
    last_s: int
    originator: bool = True
    commercial: bool = True
    suspect: bool = False      # station_map sibling flag (time-base suspect, split-off): ranks with the disputed sets
    interval_s: int = 3600     # the record's sampling interval
    fast_delivery: bool = False  # a UHSLC fast-delivery (FD) copy: after a research-quality one on an exact tie

    @property
    def disputed(self) -> bool:
        return self.suspect or (self.cs.get("time_base") or {}).get("code") == "disputed"

    @property
    def averaged(self) -> bool:
        return str((self.cs.get("time_base") or {}).get("published", "")).startswith("utc_mean")

    def tide(self) -> dict:
        return {c["name"]: (c["amplitude_m"], c["phase_deg"]) for c in self.cs.get("constituents", [])}


_STEPS = (  # (name, key: smaller is better) applied in order; current and epoch_hours are tolerance filters
    ("fallback", lambda c: bool(c.cs.get("fallback"))),
    ("tier", lambda c: TIER[c.cs["source_type"]]),
    ("time_base", lambda c: c.disputed),
    ("short_record", lambda c: _is_short(c.cs)),
    ("instantaneous", lambda c: c.averaged),
    ("sub_hourly", lambda c: c.interval_s >= 3600),
    ("current", None),
    ("originator", lambda c: not c.originator),
    ("commercial", lambda c: not c.commercial),
    ("epoch_hours", None),
    ("recency", lambda c: -c.last_s),
    ("research_quality", lambda c: c.fast_delivery),
    ("record_id", lambda c: c.cs["source_record_id"]),
)
assert tuple(n for n, _ in _STEPS) == RANK_STEPS


def _best(cands: list[Candidate]) -> tuple[Candidate, str]:
    """The first set by fallback group, then the existing published rank steps:
    source tier; not disputed (nor flagged against its siblings); not short;
    instantaneous before averaged; sub-hourly before hourly; current (ends within 2 years of the newest
    remaining set); the originating agency before a copy; a commercial-use licence; good hours in
    2002-2020 (a difference under 2 % is a tie); the most recent; research quality before fast delivery;
    the record id. Returns the set and the step that decided."""
    rest = list(cands)
    for name, key in _STEPS:
        if len(rest) == 1:
            break
        before = len(rest)
        if name == "current":
            newest = max(c.last_s for c in rest)
            rest = [c for c in rest if c.last_s >= newest - CURRENT_S]
        elif name == "epoch_hours":
            top = max(c.epoch_hours for c in rest)
            rest = [c for c in rest if top - c.epoch_hours < EPOCH_TIE * top or top == 0]
        else:
            k = min(key(c) for c in rest)
            rest = [c for c in rest if key(c) == k]
        if len(rest) == 1 and before > 1:
            return rest[0], name
    return rest[0], "single"


def rank(cands: list[Candidate]) -> list[tuple[Candidate, str]]:
    """All sets in recommendation order, each with the step that put it before the next ones."""
    rest, out = list(cands), []
    while rest:
        c, how = _best(rest)
        out.append((c, how if len(rest) > 1 else "single"))
        rest = [x for x in rest if x is not c]
    return out


def _verdict(a: Candidate, b: Candidate) -> str | None:
    from otc_pipeline.station_map import compare_tides
    return compare_tides(a.tide(), b.tide())[0]


def _agreement(c: Candidate, others: list[Candidate]) -> tuple[int, int]:
    v = [_verdict(c, o) for o in others]
    return sum(x == "agree" for x in v), sum(x in ("disagree", "amplitude_only", "clock_offset") for x in v)


def _error(c: Candidate, ref: Candidate) -> float:
    """Vector difference of M2 and K1 (m) against a reference set."""
    import cmath
    import math
    a, b = c.tide(), ref.tide()
    return sum(abs(cmath.rect(a[n][0], math.radians(a[n][1])) - cmath.rect(b[n][0], math.radians(b[n][1])))
               for n in ("M2", "K1") if n in a and n in b)


def recommend(cands: list[Candidate]) -> tuple[Candidate | None, dict]:
    """The recommended set of one station, and how it was decided. Only sets with constants compete
    (none: null, which build check 6 allows only then), of one quantity (the first set's by tier).
    Build check 2 holds whatever the order: a short_record set is never recommended over a set with
    more good hours. Then the disagreement rule (method.html#selection): when the first set disagrees
    with an official set of the station, the eligible set with the lowest error against the official
    reference is chosen (`official_reference`); with no official set and 3 or more sets, a first set
    that disagrees with most of the others gives way to the best-ranked set that agrees with most
    (`sibling_agreement`). Neither ever picks a disputed or flagged set over a clean one (A2)."""
    live = [c for c in cands if c.cs.get("qc_status") != "no_constants"]
    if not live:
        return None, {"recommended": None, "decided_by": "no_constants", "overridden": None, "candidates": []}
    # Fallback groups precede every existing criterion and reference override.
    live = [c for c in live if not c.cs.get("fallback")] or live
    # a third party's analysis never outranks an OTC fit or an official set of the station, by rank or by the
    # override rules below (site sources/ticon-4: "below official constants and any OTC gauge fit"): with any
    # other set present, the analysis sets do not compete
    live = [c for c in live if c.cs["source_type"] != "analysis"] or live
    q = rank(live)[0][0].cs["quantity"]
    live = [c for c in live if c.cs["quantity"] == q]
    eligible = [c for c in live if not (_is_short(c.cs) and any(_good(o.cs) > _good(c.cs) for o in live))]
    order = rank(eligible)
    win, how = order[0]
    if win is not rank(live)[0][0]:
        how = "check2_guard"
    overridden = None
    usable = [c for c in eligible if c.tide().keys() >= {"M2", "K1"}]
    allowed = [c for c, _ in order if c in usable and (win.disputed or not c.disputed)]
    officials = [c for c in live if c.cs["source_type"] == "official"]
    # an official set the winner cannot be compared with (no M2 and K1 on either side) settles nothing
    officials = [o for o in officials if o is not win and _verdict(win, o) is not None]
    if officials and win in usable and win.cs["source_type"] != "official" and \
            any(_verdict(win, o) != "agree" for o in officials):
        best = min(allowed, key=lambda c: (min(_error(c, o) for o in officials), [x for x, _ in order].index(c)))
        if best is not win:
            overridden, win, how = win, best, "official_reference"
    elif not officials and len(usable) >= 3 and win in usable:
        ag, dis = _agreement(win, [o for o in usable if o is not win])
        if dis > ag:
            for c in allowed:
                if c is win:
                    continue
                a2, d2 = _agreement(c, [o for o in usable if o is not c])
                if a2 > d2:
                    overridden, win, how = win, c, "sibling_agreement"
                    break
    return win, {"recommended": win.cs["set_id"], "decided_by": how,
                 "overridden": overridden.cs["set_id"] if overridden else None,
                 "candidates": [c.cs["set_id"] for c, _ in order]}


def set_order(win: Candidate | None, cands: list[Candidate]) -> list[Candidate]:
    """A station's sets in the order the release writes them (fix-r6, for the SDK): the recommended set
    first, then every other set with constants in the ranking order (rank: the published steps, whose last
    step, the record id, makes the order total), then the sets with no constants by record id."""
    live = [c for c in cands if c.cs.get("qc_status") != "no_constants"]
    rest = [c for c, _ in rank(live) if c is not win]
    dead = sorted((c for c in cands if c.cs.get("qc_status") == "no_constants"), key=lambda c: c.cs["source_record_id"])
    return ([win] if win is not None else []) + rest + dead


def reconcile_recommendation(station: dict, win: Candidate | None) -> None:
    """Reconcile marker and preferred links with the actual chosen set after every merge."""
    station["recommended_set_id"] = win.cs["set_id"] if win else None
    station.pop("recommended_is_fallback", None)
    if win is not None and win.cs.get("fallback"):
        station["recommended_is_fallback"] = True
    for cs in station["constant_sets"]:
        if cs.get("fallback"):
            cs["fallback"].pop("preferred_set_id", None)
            if win is not None and not win.cs.get("fallback") and win.cs["quantity"] == cs["quantity"]:
                cs["fallback"]["preferred_set_id"] = win.cs["set_id"]


def _flag(cs: dict, reason: str, **values: float) -> None:
    cs.setdefault("qc_flags", []).append({"flag": "sibling_disagreement", "reason": reason,
                                          **({"values": values} if values else {})})
    if cs.get("qc_status") == "ok":
        cs["qc_status"] = "flagged"


EOT20_WHOLE_HOUR = "eot20_whole_hour"


def _time_base_disputed(cs: Mapping, disputed_reasons: Mapping[str, str]) -> bool:
    """The audit disputes this set's time base: its own time_base code, or the audit's disputed reasons."""
    return (cs.get("time_base") or {}).get("code") == "disputed" or cs["source_record_id"] in disputed_reasons


def disputed_reasons_of(audit_rows: Mapping[str, Mapping]) -> dict[str, str]:
    """record -> the audit's reasons for a disputed time base (TB-SIB, space-separated, the code's own reason
    first): the `disputed_reason` column, or the part of time_base_code after "disputed:"; only disputed rows."""
    out = {}
    for rid, row in audit_rows.items():
        if row.get("time_base_status") != "disputed":
            continue
        why = (row.get("disputed_reason") or "").strip()
        code = row.get("time_base_code") or ""
        if not why and code.startswith("disputed:"):
            why = code.split(":", 1)[1].strip()
        if why:
            out[rid] = why
    return out


def _flag_list(x) -> list[dict]:
    """A record's station-map flags: StationMap.sibling_flags() gives a list; a single dict is one flag."""
    if x is None:
        return []
    return [x] if isinstance(x, Mapping) else list(x)


def sibling_flag(info: Mapping) -> tuple[str, dict]:
    """(reason, values) of the sibling_disagreement flag for one station_map sibling flag: the reason
    names every value it carries (A7)."""
    who = ", ".join(list(info.get("against", []))[:3])
    tau, d = info.get("clock_offset_h"), info.get("distance_km")
    if info.get("kind") == "split_off":
        if info.get("verdict") == "clock_offset_far" and tau is not None:
            return (f"split from the same-name records {who}, {d:.2f} km away (distance_km), whose phases fit a "
                    f"clock offset of {tau:+.2f} h (clock_offset_h)", {"distance_km": float(d), "clock_offset_h": float(tau)})
        return (f"split from the same-name records {who}, {d:.2f} km away (distance_km): the constants disagree",
                {"distance_km": float(d)})
    side = f"; side by {info.get('decided_by')}"
    if info.get("kind") == "clock_offset" and tau is not None:
        return (f"phases fit a clock offset of {tau:+.2f} h (clock_offset_h) against the other sets of this station "
                f"({who}){side}", {"clock_offset_h": float(tau)})
    if info.get("kind") == "amplitude_only":
        return f"amplitudes disagree with the other sets of this station ({who}){side}", {}
    return f"constants disagree with the other sets of this station ({who}){side}", {}


@dataclass
class BuildReport:
    """What a build published and what it left out, and why: every input record is in exactly one list.
    `refused` entries are gesla_sets.refusal() dicts (record, reason class, message, good hours).
    `recommendations`: per station with 2+ sets, the recommended set and the rank step that decided it."""
    published: list[str] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    # station id -> reason: stations not published because every record they hold is withheld
    withheld_stations: dict[str, str] = field(default_factory=dict)
    # TICON-4 fallback (ticon): per withheld record, the carried TICON-4 set or why there is none
    carried: list[dict] = field(default_factory=list)
    not_carried: list[dict] = field(default_factory=list)
    ticon_audit: str | None = None
    retained: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(r["reason"] for r in self.refused).items()))

    def as_dict(self) -> dict:
        return {"published": len(self.published), "refused": len(self.refused), "refused_by_reason": self.counts(),
                "retained": sorted(self.retained, key=lambda r: r["record"]),
                "withheld_stations": dict(sorted(self.withheld_stations.items())),
                "ticon": {"audit": self.ticon_audit, "carried": sorted(self.carried, key=lambda r: r["record"]),
                          "not_carried": sorted(self.not_carried, key=lambda r: r["record"])},
                "records": {"published": sorted(self.published),
                            "refused": sorted(self.refused, key=lambda r: r["record"])},
                "recommendations": [r for r in self.recommendations if r["decided_by"] != "single"]}

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
                  licence_ids: Mapping[str, str], withheld: Mapping[str, str], astro_tables: list[dict] | None,
                  congen_run: CongenRun,
                  refused: Iterable[Mapping], epoch_hours: Mapping[str, Mapping],
                  sibling_flags: Mapping[str, "Mapping | Iterable[Mapping]"], tombstones: Iterable[Mapping],
                  station_map_inputs: Mapping[str, str], fit_run_results_sha256: str,
                  station_aliases: Mapping[str, Mapping] | None = None,
                  contributor_roles: Mapping[str, str] | None = None,
                  disputed_reasons: Mapping[str, str],
                  sampling_s: Mapping[str, int] | None = None,
                  ticon: "ts.TiconCarry | None" = None) -> tuple[dict, BuildReport]:
    """The release document for these fit documents, and the build report. A record this build cannot
    publish yet (gesla_sets.NotPublishable: a short record, a bad constituent name) is left out and
    reported by reason; `refused` (required, [] when there are none) carries the fit stage's refusals
    (fit_records, docs_from_fit_run) into the same report. Records with one station id are one station:
    one set per record (set_id "<station id>/gesla-fit/<record id>"), written in recommendation order
    (set_order: the recommended set first, then the ranking, ties broken by record id), the station's name and position
    from its recommended set (rank, recommend), aliases gesla = its published records plus
    `station_aliases[station id]`. Required, never defaulted (A5): `sibling_flags`
    (StationMap.sibling_flags(): time-base suspects and split-off records), which rank with the disputed
    sets and get a sibling_disagreement flag; `tombstones` (StationMap.tombstones()), written as removed
    stations, each merged into a station of this release; `station_map_inputs` (StationMap.inputs),
    whose fit-run pin must equal `fit_run_results_sha256`, the SHA-256 of this build's results.jsonl.
    A set whose time base the audit disputes (time_base code "disputed", or the record in
    `disputed_reasons`, TB-SIB; see disputed_reasons_of) is never published, as the recommended set or as an
    alternate: it is refused (eot20_whole_hour_unresolved for a whole-hour EOT20 lag, "eot20_whole_hour"
    among its reasons, else time_base_disputed). A station whose every set is disputed or carries a
    clock-offset sibling flag (clock_offset_h) is not published: its flagged records are refused too
    (clock_offset_unresolved) until the time-base audit corrects them.
    `sampling_s` (record -> sampling interval, default data/gesla/4.1/inventory.csv interval_s) decides
    the sub-hourly rank step.
    `contributor_roles` (contributor -> originator | redistributor, default data/gesla/contributor_roles.csv)
    decides the originator rank step. Raises ValueError when an input the build needs is missing (astro
    tables, a record with no station id, no licence id: no licence is assumed, or no epoch hours, or
    epoch hours counted from another member), and FitIntegrityError when a document was fitted with
    another TCD than `table` or carries an unstamped attenuated fit. Check 7
    (astro_tables.astro_table_errors: a row for every published constituent, speeds, array lengths,
    tables_sha256) runs on the document; an error raises ValueError. So does an astro table that is not
    exactly `congen_run`'s (release_table_errors), and a congen run checked against another TCD than
    `table` raises FitIntegrityError. The release is a release_writer.BuiltRelease, so write_release
    will not write it without its congen run."""
    if not astro_tables:
        raise ValueError("astro_tables is required: the gesla-fit convention predicts with f and u "
                         "(format 1.0, astro_tables_when_used); the per-year tables come from KP0b")
    if not isinstance(congen_run, CongenRun):
        raise ValueError(f"congen_run must be astro_tables.load_congen_run's CongenRun (a checked congen run), "
                         f"not {type(congen_run).__name__}")
    congen, run_tcd = congen_run
    astro_tables = copy.deepcopy(list(astro_tables))   # the release owns its tables: later caller edits cannot reach it
    if run_tcd.tcd_sha256 != table.tcd_sha256:
        raise FitIntegrityError(f"the congen run was checked against TCD {run_tcd.tcd_sha256}, the fit's TCD is "
                                f"{table.tcd_sha256}")
    for t in astro_tables:
        bad = release_table_errors(t, congen, run_tcd)
        if bad:
            raise ValueError(f"astro table {t.get('astro_table_id')!r} is not the congen run's table "
                             f"({len(bad)} errors): " + "; ".join(bad[:10]))
    lic_by_id = {lic["licence_id"]: lic for lic in licences}
    if contributor_roles is None:
        from gesla.read_gesla import load_contributor_roles
        contributor_roles = {k: ("originator" if v.originator else "redistributor")
                             for k, v in load_contributor_roles(ROLES_PATH).items()}
    pin = station_map_inputs.get("fit_run_results_sha256")
    if pin != fit_run_results_sha256:
        raise ValueError(f"the station map was minted from fit run results {pin}, this build's are "
                         f"{fit_run_results_sha256}: mint again from this fit run")
    if sampling_s is None:
        import csv as _csv
        with open(INVENTORY_PATH, newline="") as fh:
            sampling_s = {r["file_id"]: int(r["interval_s"]) for r in _csv.DictReader(fh)}
    suspects = sibling_flags
    for lid in withheld:
        if lid not in lic_by_id:
            raise ValueError(f"withheld licence {lid!r} is not in licences")
    report = BuildReport(refused=[dict(r) for r in refused])
    docs = sorted(docs, key=lambda d: d["record_id"])
    if ticon is not None:
        if lic_by_id.setdefault(ts.LICENCE_ID, ts.LICENCE) != ts.LICENCE:
            raise ValueError(f"licence {ts.LICENCE_ID} is given, and differs from ticon_sets.LICENCE")
        report.ticon_audit = ticon.audit
    build_records = {d["record_id"] for d in docs} | {r["record"] for r in report.refused}
    # the OTC fits a TICON-4 set may be judged against as a neighbour: ones this build may publish
    neighbour_pool = [d for d in docs if licence_of(licence_ids, d["record_id"]) not in withheld
                      and d["time_base"]["code"] != "disputed" and d["record_id"] not in disputed_reasons
                      and not suspects.get(d["record_id"])] if ticon is not None else []
    carried_ticon: set[str] = set()
    canary_pairs: list[tuple[dict, dict]] = []          # (TICON set, OTC's withheld fit of the record): check only
    withheld_sids: dict[str, list[str]] = {}
    by_station, used_names, versions = {}, set(), set()
    for doc in docs:
        rid = doc["record_id"]
        if rid not in station_ids:
            raise ValueError(f"{rid}: no station id (the station map assigns it)")
        lic_id = licence_of(licence_ids, rid)
        if lic_id not in lic_by_id:
            raise ValueError(f"{rid}: licence {lic_id!r} is not in licences")
        if doc["tcd_sha256"] != table.tcd_sha256:
            raise FitIntegrityError(f"{rid}: fitted with TCD {doc['tcd_sha256']}, the release's tables are "
                                    f"{table.tcd_sha256}")
        if lic_id in withheld:
            report.refused.append(refusal(rid, "withheld", f"{rid}: licence {lic_id} is withheld: {withheld[lic_id]}",
                                          doc.get("good_hours")))
            reasons = withheld_sids.setdefault(station_ids[rid], [])
            if withheld[lic_id] not in reasons:
                reasons.append(withheld[lic_id])
            if ticon is not None:
                _carry_ticon(doc, lic_id, ticon=ticon, table=table, build_records=build_records,
                             all_docs=neighbour_pool,
                             carried_ticon=carried_ticon, station_ids=station_ids, epoch_hours=epoch_hours,
                             suspects=suspects, contributor_roles=contributor_roles, sampling_s=sampling_s,
                             build_commit=build_commit, by_station=by_station, report=report,
                             canary_pairs=canary_pairs)
            continue
        ep = epoch_hours.get(rid)
        if ep is None:
            raise ValueError(f"{rid}: no epoch hours (data/otc/gesla_epoch_hours.csv)")
        if ep["member_sha256"] != doc["member_sha256"]:
            raise ValueError(f"{rid}: epoch hours counted from member {ep['member_sha256']}, the fit is of "
                             f"{doc['member_sha256']}")
        try:
            ss = source_set(doc, licence_id=lic_id)
        except NotPublishable as e:
            report.refused.append(refusal(rid, e.reason, str(e), doc.get("good_hours")))
            continue
        versions.add(doc["source_version"])
        sid = station_ids[rid]
        cs = {**ss.constant_set, "set_id": f"{sid}/{SOURCE}/{rid}"}
        cs["provenance"] = {**cs["provenance"], "build": {"build_commit": build_commit}}
        sus = _flag_list(suspects.get(rid))
        for info in sus:                       # every station-map flag of the record, none overwritten (fix-r6)
            reason, values = sibling_flag(info)
            _flag(cs, reason, **values)
        role = contributor_roles.get(doc["contributor"], "unlisted")   # G02: unlisted is not an originator
        if rid not in sampling_s:
            raise ValueError(f"{rid}: no sampling interval (data/gesla/4.1/inventory.csv)")
        cand = Candidate(cs, ep["hours"], int(doc["fit"]["last_s"]), originator=(role == "originator"),
                         commercial=bool(lic_by_id[lic_id].get("commercial_use")), suspect=bool(sus),
                         interval_s=int(sampling_s[rid]), fast_delivery=doc["contributor"] == "UHSLC_FD")
        by_station.setdefault(sid, []).append((cand, ss.station_hint))
        report.published.append(rid)
    stations = []
    for sid in sorted(by_station):
        entries = by_station[sid]
        offsets = [next((f for f in _flag_list(suspects.get(c.cs["source_record_id"]))
                         if f.get("clock_offset_h") is not None), None) for c, _ in entries]
        whole = [EOT20_WHOLE_HOUR in (disputed_reasons.get(c.cs["source_record_id"]) or "").split()
                 for c, _ in entries]
        disputed = [_time_base_disputed(c.cs, disputed_reasons) for c, _ in entries]
        all_unresolved = all(f is not None or w or d for (c, _), f, w, d in zip(entries, offsets, whole, disputed)
                             if c.cs["source_type"] != "analysis")
        for (candidate, _hint), offset, hour, is_disputed in zip(entries, offsets, whole, disputed):
            cs = candidate.cs
            rid = cs["source_record_id"]
            if not is_disputed and not (all_unresolved and (offset is not None or hour)):
                continue
            why = disputed_reasons.get(rid) or (cs.get("time_base") or {}).get("reason", "")
            if hour:
                reason = "time_base_unresolved"
                finding = f"{rid}: whole-hour EOT20 lag remains unresolved ({why})"
            elif offset is not None and all_unresolved:
                reason = "time_base_unresolved"
                finding = (f"{rid}: unresolved clock offset {offset['clock_offset_h']:+.2f} h against "
                           f"{', '.join(list(offset.get('against', []))[:3])}; {why}")
            else:
                reason = "reliability_withheld" if "reviewer_evidence" in why.split() else "time_base_disputed"
                finding = f"{rid}: time-base audit disputes this record ({why})"
            label_fallback(cs, reason, finding, basis="Retained existing fitted/source values; no time-base correction invented")
    report.retained = [{"record": c.cs["source_record_id"], "set_id": c.cs["set_id"], **c.cs["fallback"]}
                       for entries in by_station.values() for c, _ in entries if c.cs.get("fallback")]
    for sid, cands in sorted(by_station.items()):
        win, how = recommend([c for c, _ in cands])
        report.recommendations.append({"station_id": sid, **how})
        if how.get("overridden"):
            over = next(c.cs for c, _ in cands if c.cs["set_id"] == how["overridden"])
            _flag(over, "constants disagree with the official reference of this station; another set is recommended"
                  if how["decided_by"] == "official_reference" else
                  "constants disagree with most sets of this station; another set is recommended")
        hint = next(h for c, h in cands if c is win) if win else cands[0][1]
        aliases = {**(station_aliases or {}).get(sid, {}),
                   "gesla": sorted({r for _, h in cands for r in h["aliases"]["gesla"]})}
        tic = sorted({r for _, h in cands for r in h["aliases"].get("ticon", [])})
        if tic:
            aliases["ticon"] = tic
        stations.append({"station_id": sid, "status": "active", "name": hint["name"], "country": hint["country"],
                         "lat": hint["lat"], "lon": hint["lon"], "timezone": station_timezone(hint["lat"], hint["lon"]),
                         "type": "reference", "aliases": dict(sorted(aliases.items())),
                         "recommended_set_id": win.cs["set_id"] if win else None,
                         "constant_sets": [c.cs for c in set_order(win, [c for c, _ in cands])]})
        reconcile_recommendation(stations[-1], win)
    active = {st["station_id"] for st in stations}
    report.withheld_stations = {sid: "; ".join(why) for sid, why in withheld_sids.items() if sid not in active}
    for t in tombstones:
        if t["merged_into"] not in active:
            raise ValueError(f"tombstone {t['station_id']}: merged_into {t['merged_into']} is not a station of this "
                             "release (all its records refused?)")
        stations.append(dict(t))
    seen = [r["record"] for r in report.refused] + report.published
    dup = sorted({r for r in seen if seen.count(r) > 1})
    if dup:
        raise ValueError(f"records both published and refused, or refused twice: {dup[:10]}")
    if not stations:
        raise ValueError(f"no record is publishable ({report.summary()})")
    if len(versions) != 1:
        raise ValueError(f"the documents come from GESLA versions {sorted(versions)}: one build, one version")
    used_names = {c["name"] for st in stations for cs in st.get("constant_sets", []) for c in cs["constituents"]}
    licence_rows = [lic_by_id[i] for i in sorted({cs["licence_id"] for s in stations for cs in s.get("constant_sets", [])})]
    conventions = [gesla_convention(table_id, table)]
    source_versions = {"gesla": versions.pop()}
    if any(cs["source"] == ts.SOURCE for s in stations for cs in s.get("constant_sets", [])):
        live = {cs["set_id"] for s in stations for cs in s.get("constant_sets", [])}
        canary = ticon_canary([(cs, d) for cs, d in canary_pairs if cs["set_id"] in live])
        if canary["status"] != "pass":
            raise ValueError(f"TICON-4 fails its convention check against OTC's own fits: {canary}")
        conventions.append(ts.ticon_convention(table_id, table, canary))
        source_versions["ticon"] = ts.SOURCE_VERSION
    release = BuiltRelease({
        "format_version": FORMAT_VERSION,
        "release": {"datestamp": datestamp, "created": created, "min_reader_version": FORMAT_VERSION,
                    "fit_min_good_hours": FIT_MIN_GOOD_HOURS, "concept_doi": None,
                    "source_versions": source_versions, "build_commit": build_commit},
        "conventions": conventions,
        "licences": licence_rows,
        "constituents": [{"constituent_table_version": table_id, "name": n, "speed_deg_per_hour": table.speed(n)}
                         for n in sorted(used_names)],
        "astro_tables": astro_tables,
        "stations": stations,
    })
    astro = astro_table_errors(release)
    if astro:
        raise ValueError(f"build check 7 (astro tables): {len(astro)} errors: " + "; ".join(astro[:10]))
    return release, report


def retain_nofit(release: dict, report: BuildReport, source_sets: list, *, station_ids: Mapping,
                 licences: list[dict]) -> None:
    """Append unavailable-fit identities after the unchanged fitted/TICON selection."""
    by_station = {s["station_id"]: s for s in release["stations"] if s["status"] == "active"}
    known_licences = {lic["licence_id"]: lic for lic in release["licences"]}
    offered_licences = {lic["licence_id"]: lic for lic in licences}
    added = set()
    for source_set in sorted(source_sets, key=lambda ss: ss.constant_set["source_record_id"]):
        cs = copy.deepcopy(source_set.constant_set)
        rid = cs["source_record_id"]
        if cs["qc_status"] != "no_constants" or cs["constituents"]:
            raise FitIntegrityError(f"{rid}: non-fit publication received fitted constants")
        if rid in added or rid in report.published:
            raise ValueError(f"{rid}: duplicate non-fit publication")
        added.add(rid)
        sid = station_ids[rid]
        lid = cs["licence_id"]
        licence = offered_licences[lid]
        if lid in known_licences and known_licences[lid] != licence:
            raise ValueError(f"{rid}: inconsistent source licence {lid}")
        known_licences[lid] = licence
        cs["set_id"] = f"{sid}/{SOURCE}/{rid}"
        cs["provenance"]["build"] = {"build_commit": release["release"]["build_commit"]}
        hint = source_set.station_hint
        if sid not in by_station:
            station = {"station_id": sid, "status": "active", "name": hint["name"], "country": hint["country"],
                       "lat": hint["lat"], "lon": hint["lon"], "timezone": station_timezone(hint["lat"], hint["lon"]),
                       "type": "reference", "aliases": {}, "recommended_set_id": None, "constant_sets": []}
            by_station[sid] = station
            release["stations"].append(station)
        station = by_station[sid]
        station["constant_sets"].append(cs)
        station.setdefault("aliases", {})["gesla"] = sorted(set(station.get("aliases", {}).get("gesla", [])) | {rid})
        report.published.append(rid)
        report.retained.append({"record": rid, "set_id": cs["set_id"], "reason": cs["no_constants_reason"],
                                "finding": next(f["reason"] for f in cs["qc_flags"] if f["flag"] != "time_base"),
                                "representation": "no_constants", "good_hours": cs["record_span"]["good_hours"]})
        report.withheld_stations.pop(sid, None)
    report.refused[:] = [r for r in report.refused if r["record"] not in added]
    release["licences"] = [known_licences[lid] for lid in sorted(known_licences)]
    active = sorted((s for s in release["stations"] if s["status"] == "active"), key=lambda s: s["station_id"])
    release["stations"] = active + [s for s in release["stations"] if s["status"] != "active"]


def _uncarry(report: BuildReport, cs: Mapping, reason: str) -> None:
    """Move a TICON-4 set that the station rules leave out from report.carried to report.not_carried."""
    entry = next(e for e in report.carried if e["set_id"] == cs["set_id"])
    report.carried.remove(entry)
    report.not_carried.append({"record": entry["record"], "ticon": entry["ticon"], "reason": reason,
                               "message": f"{entry['record']}: station {entry['station_id']}: every set is unresolved "
                                          f"({reason}); the TICON-4 set of the same record is not carried either"})


def _carry_ticon(doc: Mapping, lic_id: str, *, ticon: "ts.TiconCarry", table: TcdTable, build_records: set[str],
                 all_docs: list[Mapping],
                 carried_ticon: set[str], station_ids: Mapping[str, str], epoch_hours: Mapping[str, Mapping],
                 suspects: Mapping, contributor_roles: Mapping[str, str], sampling_s: Mapping[str, int],
                 build_commit: str, by_station: dict, report: BuildReport, canary_pairs: list) -> None:
    """The TICON-4 fallback for one withheld record: its analysis set joins the record's station as a
    candidate (ranked as the record would be, after any gauge fit by tier), or the reason it does not."""
    rid = doc["record_id"]
    t, how = ts.match(rid, float(doc["lat"]), float(doc["lon"]), ticon.records, build_records)
    if t is None:
        report.not_carried.append({"record": rid, "reason": how})
        return
    if t.name in carried_ticon:
        report.not_carried.append({"record": rid, "ticon": t.name, "reason": "ticon_already_carried"})
        return
    sid = station_ids[rid]
    try:
        cs = ts.ticon_set(t, time_base=ticon.time_bases.get(t.name), table=table, licence_id=ts.LICENCE_ID,
                          record_licence=lic_id, checked_window=ticon.windows.get(t.name))
        # the gates read OTC's own fit of the record (of the co-located withheld record for a site match): a check only
        refs = list(ticon.references.get(t.name, [])) + ts.neighbour_references(
            rid, float(doc["lat"]), float(doc["lon"]), sid, all_docs, station_ids)
        cs = ts.quality_gate(cs, rec=t, fit_doc=doc, references=refs, table=table,
                             interval_s=int(sampling_s.get(rid, 3600)))
    except ts.NotCarried as e:
        report.not_carried.append({"record": rid, "ticon": t.name, "reason": e.reason, "message": str(e)})
        return
    ep = epoch_hours.get(rid)
    if ep is None or ep["member_sha256"] != doc["member_sha256"]:
        raise ValueError(f"{rid}: no epoch hours of this member (data/otc/gesla_epoch_hours.csv)")
    if rid not in sampling_s:
        raise ValueError(f"{rid}: no sampling interval (data/gesla/4.1/inventory.csv)")
    cs = {**cs, "set_id": f"{sid}/{ts.SOURCE}/{t.name}"}
    cs["provenance"] = {**cs["provenance"], "build": {"build_commit": build_commit}}
    if ticon.references_sha256:
        cs["provenance"]["input_sha256"] = [*cs["provenance"]["input_sha256"], ticon.references_sha256]
    sus = _flag_list(suspects.get(rid)) if how == "id" else []
    for info in sus:                    # the same data as the record's OTC fit: the same station-map flags
        reason, values = sibling_flag(info)
        _flag(cs, reason, **values)
    role = contributor_roles.get(doc["contributor"], "unlisted")
    cand = Candidate(cs, ep["hours"], int(doc["fit"]["last_s"]), originator=(role == "originator"),
                     commercial=bool(ts.LICENCE["commercial_use"]), suspect=bool(sus),
                     interval_s=int(sampling_s[rid]), fast_delivery=doc["contributor"] == "UHSLC_FD")
    hint = {"name": doc["site_name"], "country": doc["country"], "lat": doc["lat"], "lon": doc["lon"],
            "aliases": {"gesla": [rid], "ticon": [t.name]}}
    by_station.setdefault(sid, []).append((cand, hint))
    carried_ticon.add(t.name)
    report.carried.append({"record": rid, "ticon": t.name, "how": how, "station_id": sid, "set_id": cs["set_id"],
                           "time_base": cs["time_base"]["code"], "record_licence": lic_id,
                           "contributor": doc["contributor"]})
    if how == "id" and doc["time_base"]["published"] == cs["time_base"]["published"]:
        canary_pairs.append((cs, doc))


CANARY = ("M2", "S2", "K1", "O1")
CANARY_AMP_M, CANARY_PHASE_DEG, CANARY_GAUGES = 0.01, 2.0, 3      # method.html: per-source convention check


def ticon_canary(pairs: list[tuple[dict, dict]]) -> dict:
    """The TICON-4 convention check (method.html: median M2, S2, K1, O1 differences within 1 cm and 2 deg over at
    least 3 overlapping gauges) against OTC's own fits of the same records on the same time base. Those fits are
    withheld: they are the comparator only, never published."""
    import statistics
    da, dp, gauges = [], [], 0
    for cs, doc in pairs:
        mine = {c["name"]: c for c in cs["constituents"]}
        fit = doc["fit"]["constituents"]
        used = [n for n in CANARY if n in mine and n in fit]
        if not used:
            continue
        gauges += 1
        for n in used:
            da.append(abs(mine[n]["amplitude_m"] - fit[n]["amp_m"]))
            dp.append(abs((mine[n]["phase_deg"] - fit[n]["phase_deg"] + 180.0) % 360.0 - 180.0))
    if gauges < CANARY_GAUGES:
        return {"status": "fail", "gauges": gauges}
    amp, ph = statistics.median(da), statistics.median(dp)
    ok = amp <= CANARY_AMP_M and ph <= CANARY_PHASE_DEG
    return {"status": "pass" if ok else "fail", "gauges": gauges, "median_amp_diff_m": round(amp, 4),
            "median_phase_diff_deg": round(ph, 2)}
