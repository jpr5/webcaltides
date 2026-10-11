"""A format 1.0 release document from GESLA fit documents (the GESLA side of the release build).

    build_release(docs, ...) -> (release, BuildReport)   the release is valid input for write_release;
                                                         the report lists every record left out, by reason

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


TIER = {"official": 0, "gauge": 1, "model": 2}
RANK_STEPS = ("tier", "time_base", "short_record", "instantaneous", "sub_hourly", "current", "originator", "commercial",
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
    """The first set by the rank steps, in the order the method page (method.html#selection) and the
    sources page publish: source tier; not disputed (nor flagged against its siblings); not short;
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

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(r["reason"] for r in self.refused).items()))

    def as_dict(self) -> dict:
        return {"published": len(self.published), "refused": len(self.refused), "refused_by_reason": self.counts(),
                "withheld_stations": dict(sorted(self.withheld_stations.items())),
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
                  refused: Iterable[Mapping], epoch_hours: Mapping[str, Mapping],
                  sibling_flags: Mapping[str, Mapping], tombstones: Iterable[Mapping],
                  station_map_inputs: Mapping[str, str], fit_run_results_sha256: str,
                  station_aliases: Mapping[str, Mapping] | None = None,
                  contributor_roles: Mapping[str, str] | None = None,
                  disputed_reasons: Mapping[str, str],
                  sampling_s: Mapping[str, int] | None = None) -> tuple[dict, BuildReport]:
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
    another TCD than `table` or carries an unstamped attenuated fit."""
    if not astro_tables:
        raise ValueError("astro_tables is required: the gesla-fit convention predicts with f and u "
                         "(format 1.0, astro_tables_when_used); the per-year tables come from KP0b")
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
    withheld_sids: dict[str, list[str]] = {}
    by_station, used_names, versions = {}, set(), set()
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
        if lic_id in withheld:
            report.refused.append(refusal(rid, "withheld", f"{rid}: licence {lic_id} is withheld: {withheld[lic_id]}",
                                          doc.get("good_hours")))
            reasons = withheld_sids.setdefault(station_ids[rid], [])
            if withheld[lic_id] not in reasons:
                reasons.append(withheld[lic_id])
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
        # decision fix-r4 (2): a station whose every set is on the offset side of a clock offset is not published
        # until the time-base audit corrects it; its records are refused and reported, as withheld records are
        # the offset evidence itself: any of a record's flags that carries a clock offset (fix-r6)
        offsets = [next((f for f in _flag_list(suspects.get(c.cs["source_record_id"]))
                         if f.get("clock_offset_h") is not None), None) for c, _ in by_station[sid]]
        # TB-SIB: a record the audit disputes for a whole-hour EOT20 lag of 3 h or more is unresolved too
        whole = [EOT20_WHOLE_HOUR in (disputed_reasons.get(c.cs["source_record_id"]) or "").split()
                 for c, _ in by_station[sid]]
        # owner rule: a disputed time base is not carried, as the recommended set or as an alternate
        disp = [_time_base_disputed(c.cs, disputed_reasons) for c, _ in by_station[sid]]
        withheld = all(f is not None or w or d for f, w, d in zip(offsets, whole, disp))
        keep = []
        for (c, h), f, w, d in zip(by_station[sid], offsets, whole, disp):
            rid = c.cs["source_record_id"]
            if not (withheld or d):
                keep.append((c, h))
                continue
            report.published.remove(rid)
            if w:
                report.refused.append(refusal(rid, "eot20_whole_hour_unresolved",
                                              f"{rid}: station {sid}: this set is disputed by the time-base audit for "
                                              f"a whole-hour EOT20 lag ({EOT20_WHOLE_HOUR}); "
                                              "withheld until the audit corrects it", _good(c.cs)))
            elif f is not None and withheld:
                report.refused.append(refusal(rid, "clock_offset_unresolved",
                                              f"{rid}: station {sid}: every set is flagged for a clock offset or disputed "
                                              f"({f['clock_offset_h']:+.2f} h against {', '.join(list(f.get('against', []))[:3])}); "
                                              "withheld until the time-base audit corrects it", _good(c.cs)))
            else:
                why = disputed_reasons.get(rid) or "no reason given"
                report.refused.append(refusal(rid, "time_base_disputed",
                                              f"{rid}: station {sid}: the time-base audit disputes this set's time base "
                                              f"({why}); a disputed time base is not published", _good(c.cs)))
        if keep:
            by_station[sid] = keep
        else:
            del by_station[sid]
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
                   "gesla": sorted(r for _, h in cands for r in h["aliases"]["gesla"])}
        stations.append({"station_id": sid, "status": "active", "name": hint["name"], "country": hint["country"],
                         "lat": hint["lat"], "lon": hint["lon"], "timezone": station_timezone(hint["lat"], hint["lon"]),
                         "type": "reference", "aliases": dict(sorted(aliases.items())),
                         "recommended_set_id": win.cs["set_id"] if win else None,
                         "constant_sets": [c.cs for c in set_order(win, [c for c, _ in cands])]})
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
