"""OTC station ids for GESLA records: which records are one station, and ids that never change.

    uv run python -m otc_pipeline.station_map mint --fit-run R --audit-dir A --weak-inputs W [--check]
    uv run python -m otc_pipeline.station_map epoch-hours --audit-dir A   # data/otc/gesla_epoch_hours.csv

Committed files (data/otc/):

- station_ids.json: the station map. Every GESLA record of the metadata belongs to exactly one
  station id, published or not, so a record that is fitted later finds its id already there.
- station_review.json: what the mint did not decide alone. `proposals`: held-apart groups;
  `review`: merged groups with a reason to look at them; `time_base_defects`: joined record pairs
  whose phases differ by one clock offset (for the time-base audit); `time_base_suspects` and
  `split_flags`: the records gesla_release flags sibling_disagreement (StationMap.sibling_flags).
  Made by every mint.
- station_decisions.json: reviewed decisions that override the rule below (`merge`, `separate`).
- gesla_epoch_hours.csv: per record, the good hours in the epoch 2002-2020 (a ranking input of
  gesla_release.build_release), with the member SHA-256 they were counted from and the SHA-256 of the
  audit's timebase.csv (the mint stops when it is not the fit run's audit).

Derived at mint time, never committed (they hold or come from fitted constants, some of which may
not be published): the tide summary (per fitted record, its good hours and M2, S2, K1 and O1
amplitude and Greenwich phase from the release's fit run: the tide evidence of the rule) and the
EOT20 lag (per fitted record, the joint lag against EOT20 and its whole-hour class,
gesla.timebase_weak.phase_check with the amplitude gate 0.80-1.25). The mint reads the fit run through
gesla_sets.docs_from_fit_run (check_fit_run first: the build's own gate), the audit (--audit-dir) and
the G04d weak inputs (--weak-inputs), and pins the run, the audit, the weak inputs, both derived tables
and the epoch-hours CSV by SHA-256 in the map's `inputs`, so `mint --check` fails when any of them
changes and gesla_release refuses a build from another fit run. `--derived-out DIR` writes the two
tables as CSV for inspection, never inside a git work tree.

Links (orchestrator decisions R1, D2, C1). Two records are linked when:
- coord: they are within 0.01 degree in latitude and in longitude (exact, every pair);
- alias: one registry id holds both (G07a data/gesla/4.1/station_map.csv), at any distance;
- uhslc: they are UHSLC records of one station number (site code without its version letter),
  within 0.1 degree;
- name_near: their names are equal token for token (not containment), they are within 0.05 degree,
  their tides agree, and they have one site code or are at most 1 km apart.
The transitive closure of the links is the "raw group".

Joining (D1, C1-C3, C6). Records join along a link when their names agree (site_names_agree; on a
name_near link, names_equal) and compare_tides does not object. compare_tides uses M2 and K1 (K1
only when both records have at least 6 months of good data, K1_MIN_HOURS): amplitudes within AMP_REL
of the larger (AMP_REL_FAR beyond 1 km unless one UHSLC number links them), or within AMP_ABS_M
(IDENTITY_ABS_M within 1 km for one agency or one site code); phases within PHASE_DEG.
- tides agree: they join. Countries may differ (GESLA files some foreign gauges under USA).
- phases differ by one clock offset (every constituent's phase by speed x the same hours), the
  amplitudes agree: they join only at one position (SAME_POS_DEG) or on a registry/UHSLC link at
  most 1 km long, and the pair is listed in `time_base_defects`; otherwise they are cut and flagged.
- amplitudes disagree, phases agree or fit one clock offset: they join only on a UHSLC link at most
  1 km long, flagged amplitude_disagrees.
- an agreement that rests on the amplitude floor alone (microtidal) joins only within 1 km or with
  one site code.
- either record has no usable fit: they join by position and name only when the countries agree;
  the station is listed for review (no_fit).
- tides disagree, names disagree, or countries differ with no tide evidence: no join.
A raw group cut into several stations is listed under `proposals`; a `merge` decision joins them.
A record cut away from same-name records within 1 km whose tides disagree, or cut because their
clock offset is not accepted at that distance (also a same-name pair within 0.05 degree), goes to
`split_flags`: the smaller part, both parts when equal (C4) - except that for a refused clock offset
EOT20 flags only the offset side when its verdict is clear (eot20_clear_side, fix-r5). A merged station is listed under
`review` with its reasons: different_agencies, agency_codes_differ (one agency, several site codes;
CMEMS codes do not count), span_over_0.01deg, names_differ_by_decision_or_chain, no_fit,
clock_offset, amplitude_disagrees, tides_disagree_in_group (two records of the station that are
joined through others disagree). A `separate` decision takes the records it names out of their
station into one station of their own; the rest of the station stays together.

Clock-offset sides (offset_sides). Inside one station, records whose tides agree form a class. The
reference class is the one EOT20 calls on time (records with EOT20 class 0 count for it, records
with a whole-hour offset against it), but EOT20 counts only when two classes meet at one position
(C8); else the larger class wins; a tie decides nothing (clock_side_undecided). Every record outside
the reference is judged against every reference member (its most common verdict: clock_offset,
amplitude_only or disagree) and becomes a time-base suspect: gesla_release never recommends it over
a clean set, and flags it sibling_disagreement. This is not a time-base verdict (the audit's job).

Ids. A new station's id is "OTC-" + the first 8 hex digits of SHA-256 of its founder, the
smallest record id among its records when it is minted (2 more digits while the id is already
taken, active or retired). The same input gives the same ids, in any input order.

Stability, when the inputs change (a re-mint reads the committed map first):
1. An id is never removed and never reused. It is active, or retired with `merged_into`.
2. A record keeps its station. New records that the rule puts with records of one existing
   station join it; new records alone get a new id. A record missing from the input stays where
   it was.
3. The rule never joins two existing stations and never divides one by itself: either stops the
   mint (MintConflict). A `merge` decision joins them (the station with the smallest founder keeps
   its id; the others are retired, merged_into it). A `separate` decision divides one: the records
   it names leave; the rest keeps the id (if the founder left, the smallest remaining record of
   the station becomes its founder). A division that moves a record no decision names stops the mint.
4. A decision that names a record which is neither in the input nor in the committed map is a
   typo and stops the mint; so does a record named in two decisions.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "data" / "otc"
MAP_PATH = DATA / "station_ids.json"
REVIEW_PATH = DATA / "station_review.json"
DECISIONS_PATH = DATA / "station_decisions.json"
EPOCH_PATH = DATA / "gesla_epoch_hours.csv"
REGISTRY_MAP_PATH = REPO / "data" / "gesla" / "4.1" / "station_map.csv"

MAP_FORMAT = 1
NEAR_DEG = 0.01        # coord link
UHSLC_DEG = 0.1        # uhslc link
NAME_NEAR_DEG = 0.05   # name_near link
ID_RE = re.compile(r"OTC-[A-Za-z0-9_-]+")
ID_HEX = 8
EPOCH_START_S = 1009843200   # 2002-01-01T00:00:00Z
EPOCH_END_S = 1609459200     # 2021-01-01T00:00:00Z (end exclusive: the epoch is 2002-2020)
EPOCH_DEFINITION = ("hourly samples of qc.clean (QC steps 1-5) with the record's published time base and "
                    "bad months dropped (gesla_sets.published_correction, as the fit), "
                    "2002-01-01T00:00:00Z <= t < 2021-01-01T00:00:00Z; a record with no usable time base: "
                    "its declared time base (column time_base = declared_fallback)")

# --- tide agreement (D1), calibrated on review C's labelled stations (fix-r1/calibrate.py) ---------
TIDE_NAMES = ("M2", "S2", "K1", "O1")
SPEED = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356}
AMP_REL = {"M2": 0.12, "K1": 0.25}  # |dA| / max(A): labelled true merges <= 0.104 (M2), 0.199 (K1)
AMP_REL_FAR = {"M2": 0.075, "K1": 0.25}  # beyond NEAR_KM without a registry/UHSLC link (C2: river gauges 8-12 %)
AMP_ABS_M = 0.01           # an amplitude difference this small agrees (microtidal records)
IDENTITY_ABS_M = 0.03      # the same within NEAR_KM for one agency or one site code (C2: Antalya, Zuidland)
K1_MIN_HOURS = 182.625 * 24  # K1 is compared only when both records are at least 6 months (K1/P1)
SAME_POS_DEG = 0.002       # "one position": a clock offset is accepted, and EOT20 picks the side (C1, C8)
NEAR_KM = 1.0
EARTH_KM = 6371.0
PHASE_DEG = 10.0           # phase difference of M2 and K1 when their amplitude is >= PHASE_MIN_AMP_M
PHASE_MIN_AMP_M = 0.05
CLOCK_MIN_AMP_M = 0.02     # constituents that must fit one clock offset
CLOCK_MAX_H = 13.0
CLOCK_SUPPORT_MIN_M = 0.01  # constituents that may support a clock offset (C2: Neustadt's S2/K1/O1 are 1-2 cm)
CLOCK_MIN_AGREE = 2        # a clock offset needs at least 2 constituents agreeing on the shift

__all__ = ["MintConflict", "RecordInfo", "StationMap", "compare_tides", "epoch_good_hours", "load",
           "load_decisions", "load_epoch_hours", "load_tides", "mint", "new_id", "site_names_agree"]


class MintConflict(ValueError):
    """The rule would join two existing stations, or divide one, with no decision that says so."""


@dataclass(frozen=True)
class RecordInfo:
    record_id: str
    name: str
    contributor: str
    country: str
    lat: float
    lon: float
    code: str = ""     # GESLA SITE CODE


@dataclass
class StationMap:
    """stations: id -> {"founder", "records"}; retired: id -> {"merged_into", "removed_in", ...};
    proposals, review and time_base_defects: what the mint lists for people (see the module text)."""
    stations: dict[str, dict]
    retired: dict[str, dict] = field(default_factory=dict)
    proposals: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    registry: dict[str, list[str]] = field(default_factory=dict)   # station id -> registry ids
    inputs: dict = field(default_factory=dict)
    time_base_defects: list[dict] = field(default_factory=list)
    time_base_suspects: dict[str, dict] = field(default_factory=dict)
    split_flags: dict[str, dict] = field(default_factory=dict)

    def record_ids(self) -> dict[str, str]:
        return {r: sid for sid, st in self.stations.items() for r in st["records"]}

    def release_aliases(self) -> dict[str, dict]:
        """station id -> extra format 1.0 aliases: `webcaltides` when the station has exactly one
        registry id and no other station has it (the alias is a single id, unique per release)."""
        owners = defaultdict(set)
        for sid, tids in self.registry.items():
            for t in tids:
                owners[t].add(sid)
        return {sid: {"webcaltides": tids[0]} for sid, tids in sorted(self.registry.items())
                if len(tids) == 1 and len(owners[tids[0]]) == 1}

    def tombstones(self) -> list[dict]:
        """The retired ids as format 1.0 tombstone stations."""
        return [{"station_id": sid, "status": "removed", "removed_in": v["removed_in"],
                 "removed_reason": v["removed_reason"], "merged_into": v["merged_into"],
                 **({"detail": v["detail"]} if v.get("detail") else {})} for sid, v in sorted(self.retired.items())]

    def as_dict(self) -> dict:
        return {"format": MAP_FORMAT, "rule": _RULE,
                "inputs": self.inputs,
                "stations": {sid: {"founder": st["founder"], "records": sorted(st["records"]),
                                   **({"registry": self.registry[sid]} if self.registry.get(sid) else {})}
                             for sid, st in sorted(self.stations.items())},
                "retired": dict(sorted(self.retired.items()))}

    def review_dict(self) -> dict:
        """What the mint lists for review (station_review.json; made again by every mint)."""
        return {"format": MAP_FORMAT, "rule": _RULE, "inputs": self.inputs,
                "proposals": self.proposals, "review": self.review, "time_base_defects": self.time_base_defects,
                "time_base_suspects": dict(sorted(self.time_base_suspects.items())),
                "split_flags": dict(sorted(self.split_flags.items()))}

    def sibling_flags(self) -> dict[str, list[dict]]:
        """record -> every station-map flag gesla_release turns into a sibling_disagreement flag (and ranks
        with the disputed sets): the split-off flag (C4) and the time-base suspect flag, both kept when a
        record has both, so clock-offset evidence never hides behind another flag (fix-r6)."""
        out: dict[str, list[dict]] = {}
        for part in (self.split_flags, self.time_base_suspects):
            for r, f in part.items():
                out.setdefault(r, []).append(f)
        return out


_RULE = ("Linked: within 0.01 deg, one registry id, one UHSLC number within 0.1 deg, or equal names with agreeing "
         "tides and one site code or 1 km within 0.05 deg. Joined along a link when names agree and M2/K1 agree "
         "(12 %/25 %, 7.5 % beyond 1 km without a UHSLC link; K1 only for 6-month records; 10 deg); a clock offset "
         "joins only at one position or on a registry/UHSLC link within 1 km; no fit: position, name and country. "
         "Cut groups: proposals; cut same-name records: flagged. Id: OTC- + sha256(founder)[:8] (+2 hex while "
         "taken). Ids never removed or reused; a record keeps its station; joining or dividing existing stations "
         "needs a decision. See otc_pipeline/station_map.py.")


# --- names ----------------------------------------------------------------------------------------

# words that tell two gauges at one structure apart (either side of a lock, weir or barrier)
SIDE_WORDS = frozenset({
    "north", "south", "east", "west", "noord", "zuid", "oost", "nord", "sud", "ost", "upper", "lower",
    "inner", "outer", "amont", "aval", "oberwasser", "unterwasser", "ow", "uw", "binnen", "buiten",
    "zeezijde", "landzijde", "upstream", "downstream", "above", "below", "ab", "bl", "inside", "outside",
    "flotador", "radar", "sjov", "old", "new"})
# words GESLA contributors add to a site name that say nothing about the site
NOISE_WORDS = frozenset({"tg", "minute", "minutes", "hourly", "the", "de", "at", "of", "and", "nr", "near"})


_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "đ": "d",
                           "Đ": "D", "ł": "l", "Ł": "L", "ð": "d", "þ": "th", "Þ": "Th"})
_DIGRAPH = re.compile(r"[oua]e")
MAX_NAME_VARIANTS = 64


def _ascii(name: str) -> str:
    """Diacritics transliterated: ä/ö/ü -> ae/oe/ue (read as the vowel or as dropped, _token_variants),
    å -> a, é -> e, ø -> o, ß -> ss, ..."""
    import unicodedata
    text = unicodedata.normalize("NFKD", name.translate(_TRANSLIT))
    return "".join(c for c in text if not unicodedata.combining(c))


def _tokens(name: str) -> list[str]:
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", _ascii(name))
    text = re.sub(r"\d+\s*_?minutes?", " ", text, flags=re.I)
    return [t for t in re.findall(r"[a-z]+|\d+", text.lower()) if t not in NOISE_WORDS]


def _token_variants(name: str) -> list[list[str]]:
    """The name's tokens, and every reading of an umlaut written as a digraph (oe, ue, ae): the plain vowel,
    or nothing (GESLA files some WSV names with the umlaut dropped: Hörnum is "Hrnum", Büsum "Bsum")."""
    import itertools
    per_token = []
    for t in _tokens(name):
        spots = [m.start() for m in _DIGRAPH.finditer(t)][:3]
        opts = set()
        for choice in itertools.product((1, 2), repeat=len(spots)):   # 1: keep the vowel, 2: drop the digraph
            out, last = [], 0
            for i, c in zip(spots, choice):
                out.append(t[last:i] + (t[i] if c == 1 else ""))
                last = i + 2
            out.append(t[last:])
            opts.add("".join(out))
        per_token.append(sorted(opts) if spots else [t])
    combos = list(itertools.islice(itertools.product(*per_token), MAX_NAME_VARIANTS))
    return [list(c) for c in combos] or [[]]


def site_names_agree(a: str, b: str) -> bool:
    """Two GESLA site names name one site: their words (diacritics transliterated, umlaut digraphs read as
    the plain vowel or as dropped, case, punctuation and contributor noise such as "TG" and "60minute" left
    out) are equal, or one is contained in the other (at least 4 letters); and they have the same side words
    (Noord/Zuid, Amont/Aval, ...) and numbers."""
    return any(_tokens_agree(ta, tb) for ta in _token_variants(a) for tb in _token_variants(b))


def _tokens_agree(ta: list[str], tb: list[str]) -> bool:
    if {t for t in ta if t in SIDE_WORDS} != {t for t in tb if t in SIDE_WORDS}:
        return False
    if {t for t in ta if t.isdigit()} != {t for t in tb if t.isdigit()}:
        return False
    ca, cb = "".join(t for t in ta if not t.isdigit()), "".join(t for t in tb if not t.isdigit())
    return ca == cb or (min(len(ca), len(cb)) >= 4 and (ca in cb or cb in ca))


# --- tides ----------------------------------------------------------------------------------------

Tide = Mapping[str, tuple[float, float]]   # constituent -> (amplitude m, Greenwich phase deg)


def _dph(a: float, b: float) -> float:
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def usable_tide(t: Tide | None) -> bool:
    return bool(t) and "M2" in t and "K1" in t


def phase_verdict(a: Tide, b: Tide, *, names: tuple[str, ...] = ("M2", "K1")) -> tuple[str, float | None]:
    """("agree" | "clock_offset" | "disagree", hours): `names` (M2 and K1, or M2 alone for a short
    record) where >= PHASE_MIN_AMP_M within PHASE_DEG; else a clock offset tau (|tau| <= CLOCK_MAX_H,
    from the larger of `names`) that brings every constituent >= CLOCK_MIN_AMP_M within PHASE_DEG and
    that at least CLOCK_MIN_AGREE of M2, S2, K1 and O1 (each >= CLOCK_SUPPORT_MIN_M) agree on: one
    constituent alone never defines a clock offset (C2, fix-r6). Of the shifts that fit, the one most
    constituents agree on wins, then the smaller one. K1 is left out when it is not compared."""
    if all(_dph(a[n][1], b[n][1]) <= PHASE_DEG for n in names if max(a[n][0], b[n][0]) >= PHASE_MIN_AMP_M):
        return "agree", None
    key = max(names, key=lambda n: max(a[n][0], b[n][0]))
    usable = [n for n in TIDE_NAMES if n in a and n in b and (n != "K1" or "K1" in names)]
    sig = [n for n in usable if max(a[n][0], b[n][0]) >= CLOCK_MIN_AMP_M]
    support = [n for n in usable if max(a[n][0], b[n][0]) >= CLOCK_SUPPORT_MIN_M]
    best = None
    for k in range(-3, 4):
        tau = (b[key][1] - a[key][1] + 360.0 * k) / SPEED[key]
        if abs(tau) > CLOCK_MAX_H:
            continue
        if max(_dph(b[n][1] - SPEED[n] * tau, a[n][1]) for n in sig) > PHASE_DEG:
            continue
        n_ok = sum(_dph(b[n][1] - SPEED[n] * tau, a[n][1]) <= PHASE_DEG for n in support)
        if best is None or (n_ok, -abs(tau)) > (best[1], -abs(best[0])):
            best = (tau, n_ok)
    if best is not None and best[1] >= CLOCK_MIN_AGREE:
        return "clock_offset", round(best[0], 2)
    return "disagree", None


def amplitude_basis(a: Tide, b: Tide, *, names: tuple[str, ...] = ("M2", "K1"), rel: Mapping[str, float] = AMP_REL,
                    floor: float = AMP_ABS_M) -> str:
    """"rel" (every amplitude in `names` within rel of the larger), "floor" (agreeing only because M2's
    difference is under `floor`: a microtidal agreement), or "disagree"."""
    out = "rel"
    for n in names:
        d, hi = abs(a[n][0] - b[n][0]), max(a[n][0], b[n][0])
        if d <= rel[n] * hi:
            continue
        if d <= floor:
            out = "floor" if n == "M2" else out
            continue
        return "disagree"
    return out


def compare_tides(a: Tide | None, b: Tide | None, *, k1: bool = True, rel: Mapping[str, float] = AMP_REL,
                  floor: float = AMP_ABS_M) -> tuple[str | None, float | None]:
    """("agree" | "clock_offset" | "amplitude_only" | "disagree", clock offset in hours or None), or
    (None, None) when either record has no usable fit (M2 and K1). agree / clock_offset need the
    amplitudes to agree (amplitude_basis) and the phases to agree or fit one clock offset
    (phase_verdict); amplitude_only: the phases do, the amplitudes do not. k1=False compares M2 only
    (a record shorter than K1_MIN_HOURS cannot separate K1 from P1)."""
    if not (usable_tide(a) and usable_tide(b)):
        return None, None
    names = ("M2", "K1") if k1 else ("M2",)
    ph, tau = phase_verdict(a, b, names=names)
    if ph == "disagree":
        return "disagree", None
    if amplitude_basis(a, b, names=names, rel=rel, floor=floor) == "disagree":
        return "amplitude_only", tau
    return ph, tau


def distance_km(a: RecordInfo, b: RecordInfo) -> float:
    p = math.radians
    h = math.sin(p(b.lat - a.lat) / 2) ** 2 + math.cos(p(a.lat)) * math.cos(p(b.lat)) * math.sin(p(b.lon - a.lon) / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


# --- grouping -------------------------------------------------------------------------------------

class _Union:
    def __init__(self, items: Iterable[str]):
        self.p = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def join(self, a: str, b: str) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)

    def groups(self) -> list[list[str]]:
        g = defaultdict(list)
        for x in self.p:
            g[self.find(x)].append(x)
        return sorted(sorted(v) for v in g.values())


def _within(a: RecordInfo, b: RecordInfo, deg: float) -> bool:
    dlon = abs(a.lon - b.lon) % 360.0
    dlon = min(dlon, 360.0 - dlon)
    return round(abs(a.lat - b.lat), 9) <= deg and round(dlon, 9) <= deg


def _near(a: RecordInfo, b: RecordInfo) -> bool:
    return _within(a, b, NEAR_DEG)


def _pairs_within(recs: Mapping[str, RecordInfo], deg: float) -> Iterable[tuple[str, str]]:
    """Every pair (a < b) within `deg` in lat and lon: exact (a sweep over latitude, then _within)."""
    order = sorted(recs, key=lambda r: (recs[r].lat, r))
    lats = [recs[r].lat for r in order]
    for i, r in enumerate(order):
        j_end = bisect.bisect_right(lats, lats[i] + deg + 1e-9)
        for s in order[i + 1:j_end]:
            if _within(recs[r], recs[s], deg):
                yield (r, s) if r < s else (s, r)


def uhslc_number(x: RecordInfo) -> str | None:
    """The UHSLC station number of a UHSLC record: its site code without the version letter."""
    if not x.contributor.startswith("UHSLC"):
        return None
    m = re.fullmatch(r"(\d+)[A-Za-z]?", x.code.strip())
    return m.group(1) if m else None


def _agency(x: RecordInfo) -> str:
    return "UHSLC" if x.contributor.startswith("UHSLC") else x.contributor.removesuffix("_HIST")


def codes_match(a: RecordInfo, b: RecordInfo) -> bool:
    """One agency site code: equal codes (a UHSLC number without its version letter). CMEMS codes are
    abbreviations of the name, not station codes, so they never match."""
    if "CMEMS" in (a.contributor, b.contributor) or not a.code.strip() or not b.code.strip():
        return False
    na, nb = uhslc_number(a), uhslc_number(b)
    if na is not None or nb is not None:
        return na is not None and na == nb
    return a.code.strip().upper() == b.code.strip().upper()


def names_equal(a: str, b: str) -> bool:
    """Whole-token equality after normalisation, umlaut readings included (the name_near rule: no containment)."""
    return any(set(ta) == set(tb) for ta in _token_variants(a) for tb in _token_variants(b))


def _k1(ha: float | None, hb: float | None) -> bool:
    return ha is None or hb is None or min(ha, hb) >= K1_MIN_HOURS


def _join_verdict(a: RecordInfo, b: RecordInfo, ta: Tide | None, tb: Tide | None, kinds: set[str],
                  ha: float | None = None, hb: float | None = None) -> tuple[bool, str, float | None]:
    """(join, why, clock offset h) for one linked pair (see the module text, "Joining")."""
    near_only = kinds == {"name_near"}
    if not (names_equal(a.name, b.name) if near_only else site_names_agree(a.name, b.name)):
        return False, "names_disagree", None
    d = distance_km(a, b)
    strong = bool(kinds & {"alias", "uhslc"})
    far = d > NEAR_KM and "uhslc" not in kinds     # C2: only one UHSLC number keeps the near thresholds
    rel = AMP_REL_FAR if far else AMP_REL
    same_code = a.code.strip().upper() == b.code.strip().upper() != ""
    floor = IDENTITY_ABS_M if d <= NEAR_KM and (_agency(a) == _agency(b) or same_code) else AMP_ABS_M
    k1 = _k1(ha, hb)
    t, tau = compare_tides(ta, tb, k1=k1, rel=rel, floor=floor)
    if t is None:
        if near_only:
            return False, "name_near_no_fit", None
        return (True, "no_fit", None) if a.country == b.country else (False, "countries_disagree", None)
    if t == "disagree":
        return False, "tides_disagree", None
    if t == "amplitude_only":
        if "uhslc" in kinds and d <= NEAR_KM:
            return True, "amplitude_disagrees", tau
        return False, "tides_disagree", tau
    if t == "clock_offset" and not (_within(a, b, SAME_POS_DEG) or (strong and d <= NEAR_KM)):
        return False, "clock_offset_far", tau
    names = ("M2", "K1") if k1 else ("M2",)
    if amplitude_basis(ta, tb, names=names, rel=rel, floor=floor) == "floor" and not (d <= NEAR_KM or codes_match(a, b)):
        return False, "microtidal_far", None
    # a name_near link is never a registry/UHSLC link nor at one position, so a clock offset has been refused above:
    # what is left must agree, and the pair needs one site code or at most NEAR_KM (C1)
    if near_only and not (codes_match(a, b) or d <= NEAR_KM):
        return False, "name_near_weak", None
    return True, t, tau


def _links(recs: Mapping[str, RecordInfo], registry: Mapping[str, Iterable[str]], tides: Mapping[str, Tide],
           hours: Mapping[str, float], near_cuts: list | None = None) -> dict[tuple[str, str], set[str]]:
    """(a, b) with a < b -> link kinds (coord, alias, uhslc, name_near). A name_near link is added only
    where it joins (_join_verdict): names equal token for token, tides that agree, and one site code or
    at most NEAR_KM apart. `near_cuts` collects the same-name pairs within NAME_NEAR_DEG that are not
    linked although their phases fit one clock offset (flagged, C4)."""
    near_cuts = near_cuts if near_cuts is not None else []
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for p in _pairs_within(recs, NEAR_DEG):
        out[p].add("coord")

    def clique(groups, kind, deg=None):
        for rs in groups.values():
            rs = sorted(rs)
            for i, a in enumerate(rs):
                for b in rs[i + 1:]:
                    if deg is None or _within(recs[a], recs[b], deg):
                        out[(a, b)].add(kind)
    by_tid = defaultdict(list)
    for r in recs:
        for t in registry.get(r, ()):
            by_tid[t].append(r)
    clique(by_tid, "alias")
    by_num = defaultdict(list)
    for r, x in recs.items():
        n = uhslc_number(x)
        if n is not None:
            by_num[n].append(r)
    clique(by_num, "uhslc", UHSLC_DEG)
    for a, b in _pairs_within(recs, NAME_NEAR_DEG):
        if (a, b) in out or not names_equal(recs[a].name, recs[b].name):
            continue
        ok, why, _ = _join_verdict(recs[a], recs[b], tides.get(a), tides.get(b), {"name_near"}, hours.get(a), hours.get(b))
        if ok:
            out[(a, b)].add("name_near")
        elif why in ("clock_offset_far", "name_near_weak") and compare_tides(tides.get(a), tides.get(b))[0] == "clock_offset":
            near_cuts.append((a, b))
    return out


def _span(recs: list[RecordInfo]) -> float:
    lats, lons = [x.lat for x in recs], [x.lon for x in recs]
    return round(max(max(lats) - min(lats), max(lons) - min(lons)), 6)


def _describe(rids: list[str], recs: Mapping[str, RecordInfo]) -> list[dict]:
    return [{"record": r, "name": recs[r].name, "contributor": recs[r].contributor, "code": recs[r].code,
             "country": recs[r].country, "lat": recs[r].lat, "lon": recs[r].lon} for r in rids]


def _agency_code(x: RecordInfo) -> tuple[str, str] | None:
    """(agency, station code) for D3; CMEMS codes are not station codes, and UHSLC versions are one number."""
    ag = x.contributor
    if ag == "CMEMS":
        return None
    if ag.startswith("UHSLC"):
        return "UHSLC", uhslc_number(x) or x.code.upper()
    return ag.removesuffix("_HIST"), x.code.upper()


def check_decisions(decisions: Mapping, known: set[str]) -> None:
    """A6: every record a decision names is known (input or committed map), and in one decision only."""
    seen: dict[str, str] = {}
    for kind in ("merge", "separate"):
        for i, d in enumerate(decisions.get(kind, [])):
            for r in d["records"]:
                if r not in known:
                    raise ValueError(f"{kind} decision {i} names {r!r}, which is neither a GESLA record nor in the map")
                if r in seen:
                    raise ValueError(f"{r!r} is in two decisions ({seen[r]} and {kind} {i})")
                seen[r] = f"{kind} {i}"


SPLIT_FLAG_WHY = ("tides_disagree", "clock_offset_far")


def _groups(recs: Mapping[str, RecordInfo], registry: Mapping[str, Iterable[str]], decisions: Mapping,
            tides: Mapping[str, Tide], hours: Mapping[str, float] | None = None, eot20: Mapping | None = None):
    """The station groups by the rule and the decisions, what the mint lists for review, and the
    split-off records that must carry a flag in the release (split_flags, C4)."""
    hours = hours or {}
    eot20 = eot20 or {}
    near_cuts: list = []
    links = _links(recs, registry, tides, hours, near_cuts)
    raw = _Union(recs)
    named = _Union(recs)
    flags: dict[str, set[str]] = defaultdict(set)   # record -> join flags (no_fit, clock_offset)
    cut_why: dict[str, set[str]] = defaultdict(set)  # raw root -> reasons of cut links
    defects, cut_pairs = [], []
    for (a, b), kinds in sorted(links.items()):
        raw.join(a, b)
        ok, why, tau = _join_verdict(recs[a], recs[b], tides.get(a), tides.get(b), kinds, hours.get(a), hours.get(b))
        if ok:
            named.join(a, b)
            if why in ("no_fit", "clock_offset", "amplitude_disagrees"):
                flags[a].add(why)
                flags[b].add(why)
            if why == "clock_offset":
                defects.append({"records": [a, b], "clock_offset_h": tau, "links": sorted(kinds)})
        else:
            cut_why[a].add(why)
            d = distance_km(recs[a], recs[b])
            if why in SPLIT_FLAG_WHY and (why == "clock_offset_far" or d <= NEAR_KM):
                cut_pairs.append((a, b, why, tau, d))
    for a, b in near_cuts:
        cut_pairs.append((a, b, "clock_offset_far", compare_tides(tides[a], tides[b])[1], distance_km(recs[a], recs[b])))
    for m in decisions.get("merge", []):
        ms = sorted(r for r in m["records"] if r in recs)
        for r in ms[1:]:
            named.join(ms[0], r)
            raw.join(ms[0], r)
    groups = named.groups()
    # A2: a separate decision takes out the records it names; the rest of their group stays one group
    sep = [sorted(r for r in s["records"] if r in recs) for s in decisions.get("separate", [])]
    sep_all = {r for s in sep for r in s}
    if sep_all:
        rest = [[r for r in g if r not in sep_all] for g in groups]
        groups = sorted([g for g in rest if g] + [s for s in sep if s])
    proposals, review = [], []
    by_raw = defaultdict(list)
    for g in groups:
        by_raw[raw.find(g[0])].append(g)
    cut_by_raw = defaultdict(set)
    for r, why in cut_why.items():
        cut_by_raw[raw.find(r)] |= why
    for root, parts in sorted(by_raw.items(), key=lambda kv: kv[1]):
        if len(parts) > 1:
            proposals.append({"reason": sorted(cut_by_raw[root]) or ["separate_decision"],
                              "span_deg": _span([recs[r] for p in parts for r in p]),
                              "parts": [_describe(p, recs) for p in sorted(parts)]})
    # C4: a record split away from a same-name group it disagrees with (within NEAR_KM), or cut only
    # because a clock offset is not accepted at that distance, is flagged: the smaller part, both if equal
    group_of = {r: i for i, g in enumerate(groups) for r in g}
    split_flags: dict[str, dict] = {}
    for a, b, why, tau, d in sorted(cut_pairs):
        ga, gb = group_of[a], group_of[b]
        if ga == gb:
            continue
        la, lb = len(groups[ga]), len(groups[gb])
        clear = eot20_clear_side(groups[ga], groups[gb], eot20, tau) if why == "clock_offset_far" else None
        for gi, other in ((ga, gb), (gb, ga)):
            if clear is not None:
                if gi != (ga, gb)[clear]:
                    continue
            elif len(groups[gi]) > min(la, lb):
                continue
            for r in groups[gi]:
                split_flags.setdefault(r, {"kind": "split_off", "verdict": why, "against": groups[other][:5],
                                           "distance_km": round(d, 2),
                                           "clock_offset_h": tau if why == "clock_offset_far" else None,
                                           "decided_by": "eot20" if clear is not None else "size"})
    for g in groups:
        if len(g) < 2:
            continue
        why = []
        if len({recs[r].contributor for r in g}) > 1:
            why.append("different_agencies")
        codes = defaultdict(set)
        for r in g:
            ac = _agency_code(recs[r])
            if ac:
                codes[ac[0]].add(ac[1])
        if any(len(v) > 1 for v in codes.values()):
            why.append("agency_codes_differ")
        if _span([recs[r] for r in g]) > NEAR_DEG:
            why.append("span_over_0.01deg")
        if not all(site_names_agree(recs[a].name, recs[b].name) for i, a in enumerate(g) for b in g[i + 1:]):
            why.append("names_differ_by_decision_or_chain")
        for f in ("no_fit", "clock_offset", "amplitude_disagrees"):
            if any(f in flags[r] for r in g):
                why.append(f)
        if any(compare_tides(tides.get(a), tides.get(b), k1=_k1(hours.get(a), hours.get(b)))[0] in ("disagree", "amplitude_only")
               for i, a in enumerate(g) for b in g[i + 1:]):
            why.append("tides_disagree_in_group")
        if why:
            review.append({"reason": why, "span_deg": _span([recs[r] for r in g]), "records": _describe(g, recs)})
    return groups, proposals, review, sorted(defects, key=lambda d: d["records"]), split_flags


EOT_CLEAR_MIN = 10.0   # fix-r5: EOT20 lag within 10 min of 0 (on time) or of a non-zero whole hour (offset)


def _eot_hours(e) -> int | None:
    return e if e is None or isinstance(e, int) else e[0]


def _eot_tau(e) -> float | None:
    return None if e is None or isinstance(e, int) else e[1]


def eot20_clear_side(p: list[str], q: list[str], eot20: Mapping, tau_h: float | None) -> int | None:
    """Of two sides of a clock-offset split, the index (0 = p, 1 = q) of the one EOT20 clearly calls offset,
    or None (fix-r5, fix-r6). `tau_h`: the pair's measured clock offset, q relative to p (hours). Clear means:
    both sides have records whose lag passed the EOT20 amplitude gate (a side without one never qualifies);
    on one side every such lag is within EOT_CLEAR_MIN of 0, and on the other every one is within
    EOT_CLEAR_MIN of one non-zero whole hour; and the EOT20 difference between the sides equals the pair's
    measured offset within EOT_CLEAR_MIN. Records whose lag did not pass the gate are left out."""
    if tau_h is None:
        return None
    tp = [_eot_tau(eot20.get(r)) for r in p]
    tq = [_eot_tau(eot20.get(r)) for r in q]
    tp, tq = [t for t in tp if t is not None], [t for t in tq if t is not None]
    if not tp or not tq:
        return None
    if abs((sum(tq) / len(tq) - sum(tp) / len(tp)) - 60.0 * tau_h) > EOT_CLEAR_MIN:
        return None

    def on_time(ts):
        return all(abs(t) <= EOT_CLEAR_MIN for t in ts)

    def offset(ts):
        hours = {round(t / 60.0) for t in ts}
        return len(hours) == 1 and 0 not in hours and all(abs(t - 60.0 * round(t / 60.0)) <= EOT_CLEAR_MIN for t in ts)
    if on_time(tp) and offset(tq):
        return 1
    if on_time(tq) and offset(tp):
        return 0
    return None


def offset_sides(stations: Mapping[str, Mapping], tides: Mapping[str, Tide], eot20: Mapping[str, int | None],
                 review: list[dict], recs: Mapping[str, RecordInfo] | None = None,
                 hours: Mapping[str, float] | None = None) -> dict[str, dict]:
    """record -> {"station_id", "kind", "clock_offset_h", "against", "decided_by"} for every record outside
    a station's reference class (see the module text). Classes: records whose tides agree. The reference
    is the class EOT20 calls on time, but EOT20 counts only when the classes meet at one position
    (SAME_POS_DEG, C8); else the larger class; a tie is listed (clock_side_undecided) and decides nothing.
    Each record is judged against every member of the reference (A6): kind is its most common verdict
    (clock_offset, amplitude_only or disagree), clock_offset_h the median offset of its clock-offset pairs."""
    hours = hours or {}
    out = {}
    for sid, st in sorted(stations.items()):
        rs = sorted(r for r in st["records"] if usable_tide(tides.get(r)))
        if len(rs) < 2:
            continue

        def v(a, b):
            return compare_tides(tides[a], tides[b], k1=_k1(hours.get(a), hours.get(b)))
        u = _Union(rs)
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                if v(a, b)[0] == "agree":
                    u.join(a, b)
        classes = u.groups()
        if len(classes) < 2:
            continue
        cls_of = {r: i for i, c in enumerate(classes) for r in c}
        eot_ok = recs is not None and any(cls_of[a] != cls_of[b] and _within(recs[a], recs[b], SAME_POS_DEG)
                                          for i, a in enumerate(rs) for b in rs[i + 1:])
        has_eot = eot_ok and any(_eot_hours(eot20.get(r)) is not None for r in rs)

        def score(c):
            hs = [_eot_hours(eot20.get(r)) for r in c]
            return sum(h == 0 for h in hs) - sum(h is not None and h != 0 for h in hs)

        def on_time(c):   # fix-r6: only a class EOT20 calls on time (a class-0 record) can win by EOT20
            return any(_eot_hours(eot20.get(r)) == 0 for r in c)
        cands = [c for c in classes if on_time(c)] if has_eot else []
        if cands:
            rank = sorted(cands, key=lambda c: (score(c), len(c)), reverse=True)
            tied = len(rank) > 1 and (score(rank[0]), len(rank[0])) == (score(rank[1]), len(rank[1]))
            how = "eot20"
        else:
            rank = sorted(classes, key=len, reverse=True)
            tied = len(rank[0]) == len(rank[1])
            how = "majority"
        if tied:
            review.append({"reason": ["clock_side_undecided"], "station_id": sid, "classes": [list(c) for c in classes]})
            continue
        ref = rank[0]
        for r in rs:
            if r in ref:
                continue
            vs = [v(m, r) for m in ref]
            bad = [x for x, _ in vs if x != "agree"]
            order = ("clock_offset", "amplitude_only", "disagree")
            kind = max(order, key=lambda k: (bad.count(k), -order.index(k)))
            taus = sorted(t for x, t in vs if x == "clock_offset" and t is not None)
            out[r] = {"station_id": sid, "kind": kind,
                      "clock_offset_h": taus[len(taus) // 2] if kind == "clock_offset" and taus else None,
                      "against": ref, "decided_by": how}
    return out


def new_id(founder: str, taken: set[str]) -> str:
    h = hashlib.sha256(founder.encode()).hexdigest()
    n = ID_HEX
    while f"OTC-{h[:n]}" in taken:
        n += 2
    return f"OTC-{h[:n]}"


def mint(records: Iterable[RecordInfo], registry: Mapping[str, Iterable[str]], *, tides: Mapping[str, Tide] | None = None,
         hours: Mapping[str, float] | None = None, eot20: Mapping[str, int | None] | None = None, decisions: Mapping | None = None, existing: StationMap | None = None, removed_in: str | None = None,
         inputs: Mapping | None = None) -> StationMap:
    """The station map for these records (see the module text for the rule and the stability rules).
    `tides`: record id -> Tide (records without one join by position and name only).
    `removed_in` (a datestamp) is required only when a merge decision retires an existing id."""
    decisions = decisions or {}
    tides = tides or {}
    recs = {x.record_id: x for x in records}
    if len(recs) == 0:
        raise ValueError("no records")
    old = existing or StationMap(stations={})
    old_of = old.record_ids()
    check_decisions(decisions, set(recs) | set(old_of))
    groups, proposals, review, defects, split_flags = _groups(recs, registry, decisions, tides, hours, eot20)
    taken = set(old.stations) | set(old.retired)
    stations = {sid: {"founder": st["founder"], "records": set(st["records"])} for sid, st in old.stations.items()}
    retired = {sid: dict(v) for sid, v in old.retired.items()}
    decided = _Union(stations)   # existing stations that a merge decision joins (through any of their records)
    for m in decisions.get("merge", []):
        sids = sorted({old_of[r] for r in m["records"] if r in old_of})
        for s2 in sids[1:]:
            decided.join(sids[0], s2)
    separated = {r for s in decisions.get("separate", []) for r in s["records"]}
    group_of = {r: i for i, g in enumerate(groups) for r in g}
    for sid, st in sorted(old.stations.items()):
        gs = {group_of[r] for r in st["records"] if r in group_of}
        if len(gs) < 2:
            continue
        stay = {group_of[r] for r in st["records"] if r in group_of and r not in separated}
        if len(stay) > 1:
            raise MintConflict(f"{sid}: the rule now divides its records {sorted(st['records'])[:6]} "
                               "(a `separate` decision naming the records that leave is needed)")
        keep = stay.pop() if stay else group_of.get(st["founder"], min(gs))
        st_new = stations[sid]
        for r in list(st_new["records"]):
            if r in group_of and group_of[r] != keep:
                st_new["records"].discard(r)
                old_of.pop(r)
        if st_new["founder"] not in st_new["records"]:
            st_new["founder"] = min(st_new["records"])
    for g in groups:
        olds = sorted({old_of[r] for r in g if r in old_of})
        if len(olds) > 1:
            founders = {stations[s]["founder"]: s for s in olds}
            if len({decided.find(s) for s in olds}) > 1:
                raise MintConflict(f"the rule now joins existing stations {olds} (a `merge` decision is needed)")
            if removed_in is None:
                raise ValueError(f"a merge decision retires {olds[1:]}: removed_in (the release datestamp) is required")
            keep = founders[min(founders)]
            for s in olds:
                if s != keep:
                    retired[s] = {"merged_into": keep, "removed_in": removed_in, "removed_reason": "merged",
                                  "detail": "merge decision (data/otc/station_decisions.json)"}
                    stations[keep]["records"] |= stations.pop(s)["records"]
            olds = [keep]
        if olds:
            stations[olds[0]]["records"] |= set(g)
        else:
            sid = new_id(g[0], taken)
            taken.add(sid)
            stations[sid] = {"founder": g[0], "records": set(g)}
    for sid, v in retired.items():
        while v["merged_into"] in retired:   # a later merge of the station it was merged into
            v["merged_into"] = retired[v["merged_into"]]["merged_into"]
    reg = {}
    for sid, st in stations.items():
        tids = sorted({t for r in st["records"] for t in registry.get(r, ())})
        if tids:
            reg[sid] = tids
    suspects = offset_sides(stations, tides, eot20 or {}, review, recs, hours)
    sid_of = {r: s for s, v in stations.items() for r in v["records"]}
    split_flags = {r: {**f, "station_id": sid_of[r]} for r, f in split_flags.items()}
    sm = StationMap(stations={s: {"founder": v["founder"], "records": sorted(v["records"])} for s, v in stations.items()},
                    retired=retired, proposals=proposals, review=review, registry=reg, inputs=dict(inputs or {}),
                    time_base_defects=defects, time_base_suspects=suspects, split_flags=split_flags)
    _check(sm)
    return sm


def _check(sm: StationMap) -> None:
    seen = {}
    for sid, st in sm.stations.items():
        if not ID_RE.fullmatch(sid):
            raise ValueError(f"{sid!r} is not an OTC station id")
        if sid in sm.retired:
            raise ValueError(f"{sid} is active and retired")
        if st["founder"] not in st["records"]:
            raise ValueError(f"{sid}: its founder {st['founder']} is not one of its records")
        for r in st["records"]:
            if r in seen:
                raise ValueError(f"record {r} is in {seen[r]} and {sid}")
            seen[r] = sid
    for sid, v in sm.retired.items():
        if not ID_RE.fullmatch(sid) or v["merged_into"] not in sm.stations:
            raise ValueError(f"retired {sid}: merged_into {v.get('merged_into')!r} is not an active station")


# --- files ----------------------------------------------------------------------------------------

def load(path: Path = MAP_PATH, review_path: Path | None = REVIEW_PATH) -> StationMap:
    d = json.loads(Path(path).read_text())
    if review_path is not None:
        rv = json.loads(Path(review_path).read_text())   # required: the release needs its suspects and flags
        d["proposals"], d["review"] = rv.get("proposals", []), rv.get("review", [])
        d["time_base_defects"] = rv.get("time_base_defects", [])
        d["time_base_suspects"] = rv.get("time_base_suspects", {})
        d["split_flags"] = rv.get("split_flags", {})
    if d.get("format") != MAP_FORMAT:
        raise ValueError(f"{path}: format {d.get('format')!r}, expected {MAP_FORMAT}")
    sm = StationMap(stations={s: {"founder": v["founder"], "records": list(v["records"])} for s, v in d["stations"].items()},
                    retired=d.get("retired", {}), proposals=d.get("proposals", []), review=d.get("review", []),
                    registry={s: v["registry"] for s, v in d["stations"].items() if v.get("registry")},
                    inputs=d.get("inputs", {}), time_base_defects=d.get("time_base_defects", []),
                    time_base_suspects=d.get("time_base_suspects", {}), split_flags=d.get("split_flags", {}))
    _check(sm)
    return sm


def dump(sm: StationMap) -> str:
    return json.dumps(sm.as_dict(), indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def dump_review(sm: StationMap) -> str:
    return json.dumps(sm.review_dict(), indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def load_decisions(path: Path = DECISIONS_PATH) -> dict:
    d = json.loads(Path(path).read_text())
    for k in d:
        if k not in ("merge", "separate", "note"):
            raise ValueError(f"{path}: unknown decision kind {k!r}")
    return d


def load_registry(path: Path = REGISTRY_MAP_PATH) -> dict[str, list[str]]:
    """record id -> registry ids (G07a station_map.csv)."""
    out = defaultdict(set)
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["file_id"]:
                out[row["file_id"]].add(row["id"])
    return {r: sorted(v) for r, v in out.items()}


def gesla_records(meta: Mapping[str, Mapping[str, str]]) -> list[RecordInfo]:
    return [RecordInfo(r, m["SITE NAME"], m["CONTRIBUTOR (ABBREVIATED)"], m["COUNTRY"], float(m["LATITUDE"]),
                       float(m["LONGITUDE"]), m.get("SITE CODE", "")) for r, m in sorted(meta.items())]


EPOCH_COLUMNS = ["record_id", "member_sha256", "epoch_good_hours", "time_base", "audit_timebase_sha256"]


def load_epoch_hours(path: Path = EPOCH_PATH) -> dict[str, dict]:
    """record id -> {"member_sha256", "hours"}."""
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = {}
    for row in rows:
        if row["record_id"] in out:
            raise ValueError(f"{path}: {row['record_id']} twice")
        out[row["record_id"]] = {"member_sha256": row["member_sha256"], "hours": int(row["epoch_good_hours"]),
                                 "audit_timebase_sha256": row.get("audit_timebase_sha256")}
    return out


TIDE_COLUMNS = (["record_id", "member_sha256", "fit_sha256", "good_hours"]
                + [f"{n}_{k}" for n in TIDE_NAMES for k in ("amp_m", "phase_deg")])


def load_tides(path: Path) -> tuple[dict[str, dict], dict[str, float]]:
    """(record id -> Tide, record id -> good hours) from a tide summary written by --derived-out."""
    tides, hours = {}, {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            rid = row["record_id"]
            if rid in tides:
                raise ValueError(f"{path}: {rid} twice")
            tides[rid] = {n: (float(row[f"{n}_amp_m"]), float(row[f"{n}_phase_deg"])) for n in TIDE_NAMES
                          if row[f"{n}_amp_m"] != ""}
            hours[rid] = float(row["good_hours"])
    return tides, hours


def load_eot20(path: Path) -> dict[str, tuple]:
    """record id -> (EOT20 whole-hour class or None, lag in minutes when it passed the amplitude gate or None)."""
    with open(path, newline="") as fh:
        return {r["record_id"]: ((int(r["eot20_hours"]) if r["eot20_hours"] != "" else None),
                                 (float(r["eot20_gated_tau_min"]) if r.get("eot20_gated_tau_min") else None))
                for r in csv.DictReader(fh)}


def eot20_rows(tides: Mapping[str, Tide], weak_inputs: Path) -> list[list]:
    """One row per record with a tide: EOT20 joint lag (min) and whole-hour class, phase_check with the
    relaxed amplitude gate 0.80-1.25 (the G04d E gate is 0.95-1.05) at the record's weak-inputs cell."""
    from gesla import timebase_weak as tw
    wi = json.loads(Path(weak_inputs).read_text())["records"]
    rows = []
    for rid, t in sorted(tides.items()):
        e = (wi.get(rid) or {}).get("eot")
        ref = {k: tuple(e[k]) for k in tw.CORE if e and k in e} if e else None
        c = tw.phase_check(dict(t), ref, SPEED, ratio=(0.80, 1.25), resid=tw.E_RESID, band=tw.E_BAND,
                           dist_km=(e or {}).get("dist_km"), max_km=tw.E_MAX_KM)
        rows.append([rid, c.get("tau", ""), c["value"] if isinstance(c["value"], int) else "",
                     c["tau"] if "tau" in c and c["value"] != "n/a" else ""])
    return rows


def tide_summary_rows(docs: Iterable[Mapping], fits_sha256: Mapping[str, str]) -> list[list]:
    """One row per fit document of a checked fit run: the member it was fitted from, the fit file's
    hash (the run manifest's), the good hours and M2/S2/K1/O1."""
    rows = []
    for d in docs:
        cons = d["fit"]["constituents"]
        row = [d["record_id"], d["member_sha256"], fits_sha256[d["record_id"]], d["good_hours"]]
        for n in TIDE_NAMES:
            row += [cons[n]["amp_m"], cons[n]["phase_deg"]] if n in cons else ["", ""]
        rows.append(row)
    return sorted(rows)


EOT20_COLUMNS = ["record_id", "eot20_tau_min", "eot20_hours", "eot20_gated_tau_min"]


def _csv_text(header: list[str], rows: list[list]) -> str:
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def derived_inputs(fit_run: Path, weak_inputs: Path, release, *, audit_dir: Path, tcd=None):
    """(tides, hours, eot20, pins, texts). The fit run is checked first (gesla_sets.docs_from_fit_run,
    which runs check_fit_run: complete, clean, results.jsonl as recorded, one row per record, every fit
    file bound to the run by its hash, and this build's TCD dump, GESLA manifest and audit csv), so the
    member hashes are the ones the run fitted. The derived tables stay in memory; pins: the run's
    results and manifest, the weak inputs, and both tables (SHA-256)."""
    from gesla.read_gesla import otc_work
    from otc_pipeline import gesla_sets as gs
    tcd = tcd if tcd is not None else gs.load_tcd_dump(otc_work() / "tcd_table.json")
    audit = gs.load_audit(audit_dir)
    manifest, rows = gs.check_fit_run(fit_run, tcd, release, audit)
    fitted = sorted(r for r, x in rows.items() if x.get("outcome") == "fitted")
    docs, refused = gs.docs_from_fit_run(fit_run, tcd, release, fitted, audit_dir=audit_dir)
    # A record the build cannot publish yet for its audit row (NotPublishable, e.g. a verdict class the release
    # build does not read) is still a checked fit: its tide counts as evidence, read from the fit file the run's
    # manifest binds (hash checked). Anything else refused means the run and the audit do not belong together.
    other = [r for r in refused if r["reason"] != "time_base_row"]
    if other:
        raise gs.FitIntegrityError(f"{fit_run}: {len(other)} fitted records refused, e.g. {other[:2]}")
    fmap = manifest[gs.FITS_SHA256]
    for r in refused:
        rid = r["record"]
        raw = (Path(fit_run) / "fits" / f"{rid}.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != fmap[rid]:
            raise gs.FitIntegrityError(f"{rid}: fits/{rid}.json is not the file the run recorded")
        docs.append({"record_id": rid, "member_sha256": release.members[rid]["sha256"], "fit": json.loads(raw),
                     "good_hours": rows[rid]["data_hours"]})
    trows = tide_summary_rows(docs, fmap)
    tides = {r[0]: {n: (float(r[4 + 2 * i]), float(r[5 + 2 * i])) for i, n in enumerate(TIDE_NAMES) if r[4 + 2 * i] != ""}
             for r in trows}
    hours = {r[0]: float(r[3]) for r in trows}
    erows = eot20_rows(tides, weak_inputs)
    eot20 = {r[0]: ((int(r[2]) if r[2] != "" else None), (float(r[3]) if r[3] != "" else None)) for r in erows}
    texts = {"gesla_tide_summary.csv": _csv_text(TIDE_COLUMNS, trows), "gesla_eot20_lag.csv": _csv_text(EOT20_COLUMNS, erows)}
    pins = {"fit_run_results_sha256": _sha256_file(Path(fit_run) / "results.jsonl"),
            "fit_run_manifest_sha256": _sha256_file(Path(fit_run) / "timebase_manifest.json"),
            "audit_timebase_sha256": audit.csv_sha256,
            "weak_inputs_sha256": _sha256_file(weak_inputs),
            "tide_summary_sha256": hashlib.sha256(texts["gesla_tide_summary.csv"].encode()).hexdigest(),
            "eot20_lag_sha256": hashlib.sha256(texts["gesla_eot20_lag.csv"].encode()).hexdigest(),
            **({"tides_without_annotation": sorted(r["record"] for r in refused)} if refused else {})}
    return tides, hours, eot20, pins, texts


def inside_git_work_tree(path: Path) -> str | None:
    """The top of the git work tree that holds `path` (or its nearest existing parent), else None (A8)."""
    import subprocess
    p = Path(path).resolve()
    while not p.exists():
        p = p.parent
    r = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def epoch_good_hours(times_s) -> int:
    """Good hourly samples in the epoch 2002-2020 (see EPOCH_DEFINITION)."""
    import numpy as np
    t = np.asarray(times_s, dtype=np.int64)
    return int(((t >= EPOCH_START_S) & (t < EPOCH_END_S)).sum())


_W: dict = {}


def _epoch_init(audit_dir: str) -> None:
    from gesla.read_gesla import GeslaRelease
    from otc_pipeline.gesla_sets import load_audit
    _W["rel"], _W["audit"] = GeslaRelease(), load_audit(Path(audit_dir))


def epoch_hours_of(rec, meta: Mapping[str, str], row: Mapping | None) -> tuple[int, str]:
    """(epoch good hours, time base label) of one record, cleaned the way the fit cleans it: the
    published correction from its audit row (time base and bad months), else its declared time base."""
    from gesla.qc import clean
    from gesla.timebase_audit import declared_correction
    from gesla.timebase_verdict import UnexplainedRecord
    from otc_pipeline.gesla_sets import NotPublishable, declared_hours, published_correction
    n_decl = declared_hours(meta)
    try:
        corr = published_correction(row, n_decl)
        series = clean(rec, corr)
        label = getattr(corr, "label", "declared")
    except (NotPublishable, UnexplainedRecord):
        series, label = clean(rec, declared_correction(n_decl)), "declared_fallback"
    return epoch_good_hours(series.times_s), label


def _epoch_one(rid: str) -> tuple[str, str, int, str]:
    rel, audit = _W["rel"], _W["audit"]
    rec = rel.record(rid, {})
    hours, label = epoch_hours_of(rec, rel.metadata()[rid], audit.rows.get(rid))
    return rid, rec.member_sha256, hours, label


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.station_map")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mp = sub.add_parser("mint", help="write the station map from the GESLA metadata, registry map, tides and decisions")
    mp.add_argument("--check", action="store_true", help="write nothing; exit 1 when the committed map would change")
    mp.add_argument("--removed-in", help="release datestamp, needed only when a merge decision retires an id")
    mp.add_argument("--fresh", action="store_true", help="ignore the committed map (only before the first release)")
    mp.add_argument("--fit-run", type=Path, required=True, help="the release's fit run (results.jsonl, fits/)")
    mp.add_argument("--audit-dir", type=Path, required=True, help="the time-base audit the fit run used")
    mp.add_argument("--weak-inputs", type=Path, required=True, help="G04d weak_inputs.json (EOT20 at each gauge)")
    mp.add_argument("--derived-out", type=Path, help="also write the derived tables here (outside any git work tree)")
    ep = sub.add_parser("epoch-hours", help="count each record's good hours in 2002-2020 (reads the GESLA cache)")
    ep.add_argument("--workers", type=int, default=4)
    ep.add_argument("--audit-dir", type=Path, required=True, help="the time-base audit the fit used (bad months)")
    a = ap.parse_args(argv)
    from gesla.read_gesla import GeslaRelease
    rel = GeslaRelease()
    if a.cmd == "mint":
        meta = rel.metadata()
        inputs = {"gesla_version": rel.version, "gesla_metadata_sha256": rel.files[rel.metadata_path]["sha256"],
                  "registry_map": str(REGISTRY_MAP_PATH.relative_to(REPO)),
                  "registry_map_sha256": _sha256_file(REGISTRY_MAP_PATH),
                  "decisions_sha256": _sha256_file(DECISIONS_PATH)}
        if a.derived_out is not None:
            top = inside_git_work_tree(a.derived_out)
            if top:
                raise SystemExit(f"--derived-out {a.derived_out} is inside the git work tree {top}: "
                                 "derived constants are never committed")
        tides, hours, eot20, pins, texts = derived_inputs(a.fit_run, a.weak_inputs, rel, audit_dir=a.audit_dir)
        inputs.update(pins)
        epoch_audit = {e["audit_timebase_sha256"] for e in load_epoch_hours(EPOCH_PATH).values()}
        if epoch_audit != {pins["audit_timebase_sha256"]}:
            raise SystemExit(f"{EPOCH_PATH} was counted with audit(s) {sorted(epoch_audit)}, the fit run's audit is "
                             f"{pins['audit_timebase_sha256']}: run epoch-hours with the same --audit-dir")
        inputs["epoch_hours_sha256"] = _sha256_file(EPOCH_PATH)
        if a.derived_out is not None:
            out = a.derived_out.resolve()
            out.mkdir(parents=True, exist_ok=True)
            for name, text in texts.items():
                (out / name).write_text(text)
        existing = load(MAP_PATH, REVIEW_PATH) if MAP_PATH.exists() and not a.fresh else None
        if a.check and existing is not None:
            moved = {k: (existing.inputs.get(k), v) for k, v in inputs.items() if existing.inputs.get(k) != v}
            for k, (was, now) in sorted(moved.items()):
                print(f"input {k}: map pins {was}, now {now}")
        sm = mint(gesla_records(meta), load_registry(REGISTRY_MAP_PATH), tides=tides, hours=hours, eot20=eot20,
                  decisions=load_decisions(DECISIONS_PATH), existing=existing, removed_in=a.removed_in, inputs=inputs)
        text, review = dump(sm), dump_review(sm)
        if a.check:
            same = [p for p, t in ((MAP_PATH, text), (REVIEW_PATH, review)) if not (p.exists() and p.read_text() == t)]
            print("station map unchanged" if not same else f"would change: {[str(p) for p in same]}")
            return 0 if not same else 1
        MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        MAP_PATH.write_text(text)
        REVIEW_PATH.write_text(review)
        print(f"{len(sm.stations)} stations, {len(sm.record_ids())} records, {len(sm.retired)} retired, "
              f"{len(sm.proposals)} proposals, {len(sm.review)} review, {len(sm.time_base_defects)} time-base "
              f"defect pairs, {len(sm.time_base_suspects)} time-base suspects, {len(sm.split_flags)} split-off "
              f"flags -> {MAP_PATH}")
        return 0
    from concurrent.futures import ProcessPoolExecutor
    names = sorted(rel.metadata())
    from otc_pipeline.gesla_sets import load_audit
    pin = load_audit(a.audit_dir).csv_sha256
    with ProcessPoolExecutor(a.workers, initializer=_epoch_init, initargs=(str(a.audit_dir),)) as ex:
        rows = sorted(ex.map(_epoch_one, names, chunksize=16))
    _write_csv(EPOCH_PATH, EPOCH_COLUMNS, [list(r) + [pin] for r in rows])
    print(f"{len(rows)} records -> {EPOCH_PATH} ({EPOCH_DEFINITION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
