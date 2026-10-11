"""Intra-site constants comparison (TB-SIB): a clock offset between records at one gauge, from their FITTED constants.

The audit compares a record's series month by month only with an overlapping sibling of another contributor, and
checks the declared base of a record outside the pool not at all (TB-OFFSETS: the BOM WA DoT records ~-8 h, Apia
401A +2.15 h, Dakar 223C, Wewak 56070 -1 h after a wrong declared +1 h, all unflagged). Fitted constants need no
overlap: two records of one gauge, years apart, have the same M2, S2, K1 and O1 phases when their clocks agree.

Pass 1 is a fit of every record with the time base it is published with (gesla.fit_run: the audit's correction
when verified or corrected, else GESLA's declared one); this module compares those constants and the audit
classifies (timebase_audit.apply_sibling_findings).

Pairs: two Coastal records within SITE_KM, or two records the station map links to one station (any distance).
A pair's shift tau (min, + = the first record's stamps later) is ONE clock shift fitted jointly to the phase gaps of
M2, S2, K1 and O1 (timebase_weak.phase_check against the other record: constituents >= 2 cm in both, a semidiurnal
and a diurnal one; M2 amplitude ratio within PAIR_RATIO; every constituent's phase within PAIR_RESID_DEG of the
shift, in degrees, so a 3 cm diurnal is held to the same phase accuracy as M2). A pair outside those gates is no
evidence either way (the tide differs between the two gauges, not only the clock).

Clock groups: pairs within ALIGN_MIN are on one clock (union-find). Two groups joined by pairs whose shifts all lie
within ALIGN_MIN of their median d, with |d| >= OFFSET_MIN, disagree by d: one of them is offset. Which one:
  1. EOT20 (relaxed gate as TB-OFFSETS: M2 ratio EOT_RATIO; else timebase_weak's E gates), on the constants of
     pass 1: a group's tau_E is the median of its members' determined lags. A group is "on UTC" when
     |tau_E| <= EOT_ZERO_MIN and "shifted by x" when |tau_E - x| <= EOT_MATCH_MIN. G1 is offset by d when G1 is
     shifted by d or G2 is on UTC, and neither G1 is on UTC nor G2 shifted by -d (both sides: "eot20"; one side
     known: "eot20_one_side").
  2. Else the majority: G1 is offset when G2 holds at least 2 records and more than G1, and EOT20 does not point the
     other way.
  A group holding a record a strong comparator verified (official, harcon, sibling series) is never the offset one:
  such a resolution is a conflict and flags nobody (precision first; the official comparator measured the gauge).
A record is offset when its group is offset against at least one group; against several, the shifts must agree
within ALIGN_MIN (else `inconsistent`). The finding says whether the evidence is UNAMBIGUOUS: resolved by EOT20 on
both sides, no other group of the site contrary (not offset itself, not a strong anchor of the offset group), the
records of the offset group not outnumbering the reference groups', and the shift within HOUR_TOL_MIN of a whole
non-zero number of hours. Only then may the audit correct (and then only after pass 2: timebase_audit).

Provider family (eot20_family): a record with no site finding, Coastal, not strongly verified, whose EOT20 lag
(the relaxed amplitude gate; the gate may leave out ONE constituent whose lag disagrees, the other three passing)
is within FAMILY_TOL_MIN of a non-zero whole hour h, is offset when its provider family (family_of: contributor,
country, site-code shape) holds at least FAMILY_MIN records whose EOT20 lag is the same h, at least one of them an
unambiguous site finding of h, and more of them than family members EOT20 puts on UTC. EOT20 alone never verifies
or corrects (timebase_weak): here the family and the site finding are the second evidence. Unambiguous (the audit
may correct) when the lag used all four constituents and no family member on UTC is strongly verified. A
DECISION of TB-SIB (a new evidence path), reported as such; BOM WA DoT is the case it was written for.

EOT20 whole hour (eot20_whole_hour; owner rule "no bad data ships", 2026-10-10): a Coastal record no strong
comparator verified and no site or family finding names, whose EOT20 lag (eot_lag_alone: the relaxed amplitude gate,
one disagreeing constituent may be left out) is within FAMILY_TOL_MIN of a whole number n of hours with
|n| >= WHOLE_HOUR_MIN_H, is offset evidence on EOT20 alone: never corrected, only disputed. The 1-2 h band is left
to the other rules (an estuary or a bay can lag the open-sea tide by an hour or two).

Thresholds (calibrated on fit-4 / full-4 in the TB-SIB calib dir, before the audit was run with them):
  OFFSET_MIN 25 min: clock-like Coastal pairs whose two records an official comparator (official, harcon) verified
    reach 16 min (Brazos Santiago / South Padre CG 1.3 km -16.0, Sand Heads / Steveston -13.5, Mayport +11.0); the
    clock classes start at 30 (mean60) and 60. A real hydraulic lag can be larger and clock-like (Naples Bay City
    Dock / Naples pier 1.5 km, 48.5 min, both strongly verified): such a pair is held by the strong-anchor rule and,
    for unverified records, by the side-picker; a pair like it with no verified record is disputed, never corrected
    unless it is within HOUR_TOL_MIN of a whole hour with EOT20 on both sides.
  PAIR_RATIO 0.75..1.33, PAIR_RESID_DEG 5: the both-official maximum stays 16 min from 0.9..1.1 / 4 deg to these;
    they admit Apia 401A (K1 2.6 deg, O1 4.0 deg off at 4 and 3 cm) and Dakar 223C (M2 0.77..0.81 of the other
    Dakar records over its 200 days).
  FAMILY_TOL_MIN 10 min (EOT20's own whole-hour band), FAMILY_MIN 3: see the calib dir (family.txt) for every
    family the rule reaches and its count on records an official comparator verified.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from . import timebase_weak as tw

SITE_KM = 2.0
PAIR_RATIO = (0.75, 1.33)
PAIR_RESID_DEG = 5.0
ALIGN_MIN = 10.0
OFFSET_MIN = 25.0
HOUR_TOL_MIN = 3.0               # timebase_verdict.BAND_MIN: a clean whole hour
EOT_RATIO = (0.80, 1.25)
EOT_ZERO_MIN = 12.0
EOT_MATCH_MIN = 15.0
FAMILY_TOL_MIN = 10.0            # timebase_weak.E_BAND: EOT20's whole-hour band
FAMILY_MIN = 3
WHOLE_HOUR_MIN_H = 3                 # eot20_whole_hour: |n| >= 3 h (owner rule)
COASTAL = "Coastal"


@dataclass(frozen=True)
class SiteRecord:
    """One fitted record: its pass-1 constants (name -> (amplitude m, phase deg)) on its published base."""
    file_id: str
    lat: float
    lon: float
    gauge_type: str
    consts: Mapping[str, tuple[float, float]]
    eot: Mapping | None = None              # timebase_weak.WeakInputs.eot(): EOT20 at the nearest cell
    strong: bool = False                    # a strong comparator (official, harcon, sibling series) verified it
    stations: frozenset = frozenset()       # station-map ids


@dataclass(frozen=True)
class Finding:
    """A record whose clock is offset from the other records of its site by shift_min (its stamps later: +)."""
    file_id: str
    shift_min: float
    group: tuple[str, ...]                  # the records on its clock (it included)
    references: tuple[str, ...]             # the records of the groups it is offset against
    resolved_by: str                        # eot20 | eot20_one_side | majority
    eot_group: float | None                 # tau_E of its group (min)
    eot_reference: float | None             # tau_E of the reference groups (median, min)
    whole_hours: int | None                 # round(shift / 60) when within HOUR_TOL_MIN of it (non-zero)
    unambiguous: bool
    why: str = ""                           # what keeps it from being unambiguous
    pairs: tuple[tuple[str, float], ...] = field(default_factory=tuple)   # (other record, tau) of the offset pairs

    def summary(self) -> str:
        e = lambda x: "-" if x is None else f"{x:+.1f}"
        refs = ", ".join(self.references[:4]) + (f" +{len(self.references) - 4}" if len(self.references) > 4 else "")
        return (f"fitted constants {self.shift_min:+.1f} min against {len(self.references)} record(s) of the site "
                f"({refs}); side by {self.resolved_by} (EOT20 {e(self.eot_group)} here, {e(self.eot_reference)} "
                f"there)" + (f"; not unambiguous: {self.why}" if not self.unambiguous else ""))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, a)))


def pair_shift(a: Mapping, b: Mapping, speeds: Mapping[str, float]) -> float | None:
    """tau (min) of a's constants against b's: one clock shift, or None when they are not clock-like."""
    c = tw.phase_check(a, b, speeds, ratio=PAIR_RATIO, resid=math.inf, band=0.0)
    if c.get("value") == tw.NA or "tau" not in c:
        return None
    if max(abs(r) * speeds[k] / 60.0 for k, r in c["res"].items()) > PAIR_RESID_DEG:
        return None
    return float(c["tau"])


def _eot_check(consts: Mapping, e: Mapping, speeds: Mapping[str, float]) -> dict:
    ref = {k: tuple(e[k]) for k in tw.CORE if k in e}
    return tw.phase_check(consts, ref, speeds, ratio=EOT_RATIO, resid=tw.E_RESID, band=tw.E_BAND,
                          dist_km=e.get("dist_km"), max_km=tw.E_MAX_KM)


def eot_lag(r: SiteRecord, speeds: Mapping[str, float]) -> float | None:
    """The record's joint lag against EOT20 (min), relaxed amplitude gate; None when the gates fail."""
    if not r.eot:
        return None
    c = _eot_check(r.consts, r.eot, speeds)
    return None if c.get("value") == tw.NA or "tau" not in c else float(c["tau"])


def eot_lag_alone(r: SiteRecord, speeds: Mapping[str, float]) -> tuple[float, str] | None:
    """(lag min, how) for the EOT20-alone rule: eot_lag, or, when only the constituent lags disagree, the lag
    without the one constituent farthest from it (the other three must pass every gate)."""
    if not r.eot:
        return None
    c = _eot_check(r.consts, r.eot, speeds)
    if c.get("value") != tw.NA and "tau" in c:
        return float(c["tau"]), "all four"
    if "res" not in c:
        return None
    worst = max(c["res"], key=lambda k: abs(c["res"][k]))
    c2 = _eot_check({k: v for k, v in r.consts.items() if k != worst}, r.eot, speeds)
    if c2.get("value") != tw.NA and "tau" in c2:
        return float(c2["tau"]), f"without {worst} ({c['res'][worst]:+.1f} min off)"
    return None


def family_of(file_id: str) -> tuple[str, str, str] | None:
    """A provider family: the contributor, the country and the SHAPE of the site code (letters -> a, digits -> 9):
    fremantle_fishing_boat_harbour-fffbh01-aus-bom is (bom, aus, aaaaa99), the WA DoT numbering, apart from BOM's
    national 5-digit gauges (99999). None when the id has no site code."""
    parts = file_id.split("-")
    if len(parts) < 4:
        return None
    shape = "".join("9" if c.isdigit() else "a" if c.isalpha() else c for c in parts[-3])
    return parts[-1], parts[-2], shape


@dataclass(frozen=True)
class FamilyFinding:
    """EOT20 puts a record a whole number of hours off, and its provider family says the same."""
    file_id: str
    lag_min: float                          # the record's EOT20 lag
    how: str                                # "all four" or "without <constituent>"
    hours: int
    family: tuple[str, str, str]
    agree: tuple[str, ...]                  # family members whose EOT20 lag is the same whole hour (it included)
    confirmed: tuple[str, ...]              # of those, the ones an unambiguous site finding gives the same hours
    on_utc: tuple[str, ...]                 # family members EOT20 puts on UTC
    unambiguous: bool
    why: str = ""

    def summary(self) -> str:
        return (f"EOT20 {self.lag_min:+.1f} min ({self.how}) = {self.hours:+d} h, as {len(self.agree)} records of "
                f"provider family {'/'.join(self.family)} ({len(self.confirmed)} confirmed by site siblings, "
                f"{len(self.on_utc)} on UTC)" + (f"; not unambiguous: {self.why}" if not self.unambiguous else ""))


def family_offsets(recs: Mapping[str, SiteRecord], speeds: Mapping[str, float],
                   site: Mapping[str, Finding]) -> dict[str, FamilyFinding]:
    """The provider-family rule (module docstring) on every record without a site finding."""
    lag = {f: eot_lag_alone(r, speeds) for f, r in recs.items() if r.gauge_type == COASTAL}
    hours: dict[str, int] = {}
    for f, x in lag.items():
        if x is None:
            continue
        h = round(x[0] / 60.0)
        if h != 0 and abs(x[0] - 60.0 * h) <= FAMILY_TOL_MIN:
            hours[f] = h
    fams: dict[tuple, list[str]] = defaultdict(list)
    for f in recs:
        k = family_of(f)
        if k is not None:
            fams[k].append(f)
    out = {}
    for f, h in sorted(hours.items()):
        r = recs[f]
        if f in site or r.strong:
            continue
        fam = family_of(f)
        mem = fams.get(fam, []) if fam else []
        agree = tuple(sorted(g for g in mem if hours.get(g) == h))
        confirmed = tuple(g for g in agree if g in site and site[g].unambiguous and site[g].whole_hours == h)
        on_utc = tuple(sorted(g for g in mem if lag.get(g) is not None and abs(lag[g][0]) <= EOT_ZERO_MIN))
        if len(agree) < FAMILY_MIN or not confirmed or len(agree) <= len(on_utc):
            continue
        why = []
        if lag[f][1] != "all four":
            why.append(f"EOT20 only {lag[f][1]}")
        if any(recs[g].strong for g in on_utc):
            why.append("a family member a strong comparator verified is on UTC")
        out[f] = FamilyFinding(f, lag[f][0], lag[f][1], h, fam, agree, confirmed, on_utc, not why, "; ".join(why))
    return out


def eot_whole_hours(recs: Mapping[str, SiteRecord], speeds: Mapping[str, float],
                    skip: Iterable[str] = ()) -> dict[str, tuple[float, str, int]]:
    """The eot20_whole_hour rule (module docstring): file id -> (EOT20 lag min, how, n hours)."""
    skip = set(skip)
    out = {}
    for f in sorted(recs):
        r = recs[f]
        if f in skip or r.strong or r.gauge_type != COASTAL:
            continue
        got = eot_lag_alone(r, speeds)
        if got is None:
            continue
        n = round(got[0] / 60.0)
        if abs(n) >= WHOLE_HOUR_MIN_H and abs(got[0] - 60.0 * n) <= FAMILY_TOL_MIN:
            out[f] = (got[0], got[1], n)
    return out


def candidate_pairs(recs: Mapping[str, SiteRecord]) -> list[tuple[str, str]]:
    """Coastal pairs within SITE_KM, and pairs the station map links to one station (any distance, any type)."""
    cell: dict[tuple[int, int], list[str]] = defaultdict(list)
    for f, r in recs.items():
        cell[(math.floor(r.lat * 20), math.floor(r.lon * 20))].append(f)
    out = set()
    for f, r in recs.items():
        if r.gauge_type != COASTAL:
            continue
        ci, cj = math.floor(r.lat * 20), math.floor(r.lon * 20)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for g in cell.get((ci + di, cj + dj), ()):
                    s = recs[g]
                    if g > f and s.gauge_type == COASTAL and haversine_km(r.lat, r.lon, s.lat, s.lon) <= SITE_KM:
                        out.add((f, g))
    by_station: dict[str, list[str]] = defaultdict(list)
    for f, r in recs.items():
        for s in r.stations:
            by_station[s].append(f)
    for fs in by_station.values():
        for a in fs:
            for b in fs:
                if a < b:
                    out.add((a, b))
    return sorted(out)


class _Union:
    def __init__(self, items: Iterable[str]):
        self.p = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


@dataclass
class SiteResult:
    findings: dict[str, Finding]
    pairs: dict[tuple[str, str], float]          # clock-like pairs and their shift
    unresolved: list[str]                        # group pairs that disagree with no side (log lines)
    conflicts: list[str]                         # resolutions refused (a strong anchor, inconsistent shifts)


def _state(e: float | None, x: float) -> str:
    if e is None:
        return "unknown"
    if abs(e) <= EOT_ZERO_MIN:
        return "utc"
    if abs(e - x) <= EOT_MATCH_MIN:
        return "shifted"
    return "other"


def find_offsets(recs: Mapping[str, SiteRecord], speeds: Mapping[str, float]) -> SiteResult:
    """The module docstring's rule on every site of recs."""
    pairs: dict[tuple[str, str], float] = {}
    for a, b in candidate_pairs(recs):
        t = pair_shift(recs[a].consts, recs[b].consts, speeds)
        if t is not None:
            pairs[(a, b)] = t
    uf = _Union(recs)
    for (a, b), t in pairs.items():
        if abs(t) <= ALIGN_MIN:
            uf.join(a, b)
    members: dict[str, list[str]] = defaultdict(list)
    for f in sorted(recs):
        members[uf.find(f)].append(f)
    # shifts between groups, oriented g1 -> g2 (g1's clock minus g2's)
    between: dict[tuple[str, str], list[tuple[str, str, float]]] = defaultdict(list)
    for (a, b), t in pairs.items():
        ga, gb = uf.find(a), uf.find(b)
        if ga == gb:
            continue
        if ga < gb:
            between[(ga, gb)].append((a, b, t))
        else:
            between[(gb, ga)].append((b, a, -t))
    eot = {f: eot_lag(recs[f], speeds) for f in {x for k in between for g in k for x in members[g]}}

    def g_eot(g: str) -> float | None:
        xs = [eot[f] for f in members[g] if eot.get(f) is not None]
        return statistics.median(xs) if xs else None

    unresolved, conflicts = [], []
    # resolutions: offset group -> [(reference group, d, how)]
    offs: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
    for (g1, g2), es in sorted(between.items()):
        d = statistics.median(t for _, _, t in es)
        if abs(d) < OFFSET_MIN:
            continue
        name = f"{members[g1][0]} (+{len(members[g1]) - 1}) vs {members[g2][0]} (+{len(members[g2]) - 1}) {d:+.1f} min"
        if any(abs(t - d) > ALIGN_MIN for _, _, t in es):
            conflicts.append(f"{name}: pair shifts {sorted(round(t, 1) for _, _, t in es)} do not agree")
            continue
        e1, e2 = g_eot(g1), g_eot(g2)
        s1, s2 = _state(e1, d), _state(e2, -d)
        one = (s1 == "shifted" or s2 == "utc") and not (s1 == "utc" or s2 == "shifted")
        two = (s2 == "shifted" or s1 == "utc") and not (s2 == "utc" or s1 == "shifted")
        pick = None
        if one != two:
            off, ref = (g1, g2) if one else (g2, g1)
            dd = d if one else -d
            both = (s1, s2) in (("shifted", "utc"), ("utc", "shifted"))
            pick = (off, ref, dd, "eot20" if both else "eot20_one_side")
        elif not one and not two and "utc" not in (s1, s2) and "shifted" not in (s1, s2):
            n1, n2 = len(members[g1]), len(members[g2])
            if n2 >= 2 and n2 > n1:
                pick = (g1, g2, d, "majority")
            elif n1 >= 2 and n1 > n2:
                pick = (g2, g1, -d, "majority")
        if pick is None:
            unresolved.append(f"{name}: EOT20 {e1} / {e2}, groups of {len(members[g1])} and {len(members[g2])}")
            continue
        off, ref, dd, how = pick
        if any(recs[f].strong for f in members[off]):
            conflicts.append(f"{name}: {how} makes {members[off][0]}'s group offset, but it holds a record a strong "
                             f"comparator verified")
            continue
        offs[off].append((ref, dd, how))
    findings: dict[str, Finding] = {}
    for g, res in offs.items():
        ds = [d for _, d, _ in res]
        d = statistics.median(ds)
        hows = {h for _, _, h in res}
        how = "eot20" if hows == {"eot20"} else ("eot20_one_side" if "majority" not in hows else
                                                 ("majority" if hows == {"majority"} else "+".join(sorted(hows))))
        refs = sorted({f for r, _, _ in res for f in members[r]})
        why = []
        if any(abs(x - d) > ALIGN_MIN for x in ds):
            why.append(f"shifts against the reference groups disagree ({', '.join(f'{x:+.1f}' for x in ds)})")
        if hows != {"eot20"}:
            why.append(f"side by {how}, not EOT20 on both sides")
        if any(r in offs for r, _, _ in res):
            why.append("a reference group is itself offset")
        if len(members[g]) > len(refs):
            why.append(f"the offset group ({len(members[g])}) outnumbers the reference ({len(refs)})")
        h = round(d / 60.0)
        whole = h if h != 0 and abs(d - 60.0 * h) <= HOUR_TOL_MIN else None
        if whole is None:
            why.append(f"{d:+.1f} min is not a whole number of hours (+-{HOUR_TOL_MIN:g} min)")
        refe = [g_eot(r) for r, _, _ in res if g_eot(r) is not None]
        for f in members[g]:
            prs = tuple(sorted((b if a == f else a, t if a == f else -t) for (a, b), t in pairs.items()
                               if f in (a, b) and (b if a == f else a) in refs))
            findings[f] = Finding(f, round(d, 1), tuple(members[g]), tuple(refs), how,
                                  None if g_eot(g) is None else round(g_eot(g), 1),
                                  round(statistics.median(refe), 1) if refe else None, whole, not why,
                                  "; ".join(why), prs)
    return SiteResult(findings, pairs, unresolved, conflicts)
