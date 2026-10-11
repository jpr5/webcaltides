"""Time-base audit: comparators, audit order, provider inheritance and outputs (BP §2 steps 1, 5-7).

G04a measures and G04b classifies one record against one comparator (timebase_verdict.evaluate).
This module chooses the comparators, runs the audit in the plan's order, applies provider
inheritance, writes timebase.csv / timebase_providers.csv, and sends the cases tier 1 cannot
settle to the decision model's residue (residue.py, case "timebase", rule D5).

Comparators (BP §2.1):
  (b) official predictions from the harness reference cache (NOAA, CHS, BSH, RWS, IMI, Kartverket,
      LINZ), the station chosen as the F3 canary pairs (230c8ea): an alias first (a record of a
      contributor that numbers its gauges by NOAA station number names that NOAA station), else the
      nearest within REF_MAX_KM (the same gauge, any country: NOAA runs gauges abroad), else the
      nearest within PORT_KM (the same port) in the source's own country; a same-port station can
      confirm utc_instant but never makes a record unexplained (the tide itself can lag across a
      port or up a tidal river). A trial fit of the record over its audit span is
      predicted at 1 min over the harness windows W1-W3 and its extrema are matched to the official
      extrema; lag = median (model - official).
  (c) NOAA-numbered records (NOAA, DA_SAT): NOAA's harmonic constants (harcon.json, from the harness
      cache or else A-NOAA's harcon cache: NOAA's published constants); per-constituent phase lags of the trial
      fit against them; lag = that of the largest of M2, S2, K1, O1. When (c) gives no lag, (b) is
      tried (both notes are kept).
  (a) siblings: other GESLA records within SIBLING_DEG, from another contributor, overlapping,
      used on their CORRECTED series (only after their own passing verdict), over their verdict
      span; a sibling corrected from utc_mean_start60 keeps its boxcar M2 response. Monthly
      cross-correlation, the hour-mean test and the M2 ratio (timebase_verdict.evaluate).

(b) and (c) are RECORD-LEVEL (measured, G04c probe_b.txt): against a constant-constituent
prediction the monthly lag of a correct record wanders by the season and the weather (Bremerhaven
CMEMS against its own fit: 5th-95th percentile -7..+6 min, 104 of 228 months beyond +-3; Trondheim
2 of 228), so a +-3 min monthly test against a prediction is noise, not a time-base test. And an
official extrema prediction differs from a harmonic prediction by the agency's method: correct-UTC
records lag BSH by -2.7 (Helgoland), -3.5 (Bremerhaven) and -9.3..-9.6 min (Cuxhaven, four records
of three contributors that agree with each other within 1 min month by month); Kartverket at
Trondheim -1.0. So a (b)/(c) lag is classed with OFFICIAL_BAND_MIN = 12 min [unverified; S5
calibrates], under half the 30-min spacing of the classes. A record-level lag establishes only
utc_instant: in estuaries the method offset reaches ~20 min (WSV Elbe gauges -50..-55 min against
BSH: mean60 plus the offset), so a whole-hour class, a mean60-like lag, DST and anything else are
INCONCLUSIVE and left to the siblings. The harness's NOAA subordinate (kind S) events are not used as
a prediction of the gauge: they are time and height offsets from another station.

Confirm-only references (G04f, CONFIRM_ONLY_SOURCES), tried in that order after the harness references:
  DMI's official tidewater high/low predictions (dmi_reference.py; DMI's own predictions sit ~16 min
      off DMI's observations at three gauges, A-dmi-a), paired like the others (same gauge, any
      country, else same port in Denmark), band OFFICIAL_BAND_MIN;
  NOAA subordinate stations (noaa_subordinate.py): the reference station's official high/low
      predictions moved by the station's published time offsets (per-event averages), paired by alias,
      else same gauge (never same port: subordinate stations 2-5 km off sit in other water), band
      SUB_BAND_MIN.
Such a reference confirms utc_instant only when the high-water lag, the low-water lag and the median
lag all fall within its band (two independent offsets for a subordinate station); otherwise it gives
no verdict (the record keeps its declared time base, excluded, with the measured lag in its reason):
it never makes a record unexplained.
The month-by-month pass rule (BP §2.4) is applied against siblings: an officially verdicted record
is cross-checked against its officially verdicted siblings (round 0); a same-class sibling result
refines the verdict (months_bad, span); if every sibling comparison fails the pass rule the record
is unexplained. An officially verdicted record without such a sibling keeps a record-level verdict
(no audited month): its bad months can only be caught by a sibling or by the K-checks (G05).

What a sibling can test (the G04b facts): utc_mean_start60 needs the hour-mean test, so only a
sibling with interval < 60 min can establish it. A result that the comparator could not test is
INCONCLUSIVE, never `unexplained`: a comparator that cannot test the hypothesis never condemns a
record by itself (Cuxhaven WSV against the hourly BfG record).

Span (BP §4, §2.4): the audit span is the span the fit uses, the last FIT_SPAN_YEARS of good data.
A run of two or more adjacent off months is not isolated (G04b), so the record is unexplained on
that span; it is then re-tested on the trailing span after the last such run (the plan's "use only
the part after the step"), when that holds >= MIN_TRAILING_MONTHS audited months. A pass there
gives the verdict with that shorter span, and the fit may use only that span (timebase.csv
span_start/span_end). Helgoland WSV-4.1 against CMEMS: 2014-03/04 off -> 2014-05.. (G04b).

Audit order (BP §2.1): round 0 = every record with a (b) or (c) comparator, with the cross-check
above. Then rounds over the records with a sibling verdicted in an earlier round, until a round adds
no verdict. A conclusive (b)/(c) class beats a sibling's. Among siblings a passing verdict resolves
the record (another sibling's `unexplained` does not condemn it; the cleanest passing one is taken);
passing siblings that disagree on the class send the record to the decision model.

A record whose verdict a sibling supported, when that sibling's verdict is later withdrawn (the cross-checks can make
it unexplained, or a task of it can crash), is re-judged without that support, to a fixpoint. A
record any of whose measurement tasks raised is residue (the failure in its reason): it is never
used, never a comparator and never inherits.

Provider inheritance (BP §2.5): a record with no comparator inherits its provider's verdict only
when >= INHERIT_MIN records of the same contributor, GESLA version and sampling interval were
audited against a comparator, all of them got the same verdict, >= INHERIT_MIN of them are in the
record's own country (country_of; a record whose country is unknown never inherits), and the
record's span overlaps the audited span of at least one of those. A record whose task crashed has no verdict and is left out of
that count and agreement (a crash is no evidence about its provider). Otherwise the weak evidence of
timebase_weak (EOT20, the served constants, the DST self-test; G04d) is tried; without a result it
stays `unverified`.

No record is dropped (owner rule 2026-10-08). Every record carries a time-base annotation
(annotate()): verified (the declared GESLA time base is confirmed, and by what), corrected (from
what, to what, on what evidence), unverified (no usable evidence) or disputed (the evidence
disagrees, unexplained; it also goes to D5). Only verified and corrected records publish the
audit's correction; unverified and disputed records publish GESLA's DECLARED time base (TIME ZONE
HOURS: 0 = UTC for every 4.1 record but one), and are never a comparator for another record.

Outputs: timebase.csv (one row per audited record), timebase_providers.csv (per provider group),
and residue rows for every `unexplained` record and every provider verdict that differs from the
previous GESLA release's (BP §2.7; rule D5 decides them). An unexplained record is never used.
The positive controls (timebase_verdict.run_positive_controls) run first: a wrong control stops
the build (non-zero exit) before anything is written.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from . import dmi_reference, noaa_subordinate
from . import timebase_agency as tag
from . import timebase_sibling as tsib
from .fit import FitError, design
from .qc import clean
from .read_gesla import GeslaRelease, otc_work
from .record import GaugeRecord
from .residue import ResidueCase
from .select_constituents import FIT_SPAN_YEARS, SECONDS_PER_YEAR, analyse, between, in_tcd_years, load_noaa_harcon
from .tcd_table import TcdTable, dump_path
from .timebase_measure import Samples, monthly_lags, samples
from .timebase_weak import (ORIGIN_VERIFIED, Lineage, Served, WeakInputs, assess, fit_features, load_served,
                            served_kind, sha256_file)
from .timebase_weak import speeds as weak_speeds
from .timebase_weak import E_MAX_KM as tw_E_MAX_KM
from .timebase_verdict import (BAND_MIN, LAG_CLASSES, MAX_BAD_FRACTION, MEAN60_LAG, NOT_USABLE, Correction, Verdict,
                               boxcar_response, evaluate, isolated, month_key)

SIBLING_DEG = 0.01
REF_MAX_KM = 2.0                 # the harness's MAX_KM: gauge to official station (the same gauge, any country)
PORT_KM = 5.0                    # F3 canary PAIR_RADIUS_KM: the same port, in the source's own country only
# GESLA country codes each source's own stations are in (NOAA: the US and its territories).
SOURCE_COUNTRIES = {"noaa": {"usa", "pri", "vir", "gum", "asm", "mnp", "umi"}, "chs": {"can"}, "rws": {"nld"},
                    "bsh": {"deu"}, "imi": {"irl"}, "kartverket": {"nor"}, "linz": {"nzl"}, "dmi": {"dnk"}}
SOURCE_COUNTRIES[noaa_subordinate.SRC] = SOURCE_COUNTRIES["noaa"]
# References that can confirm utc_instant but never make a record unexplained (module docstring).
CONFIRM_ONLY_SOURCES = {dmi_reference.SRC, noaa_subordinate.SRC}
# A subordinate station's confirmation band, set before any comparison: NOAA publishes the offsets as
# whole minutes of per-event averages and calls the result an approximation (no stated accuracy), so
# the band is set by what must stay apart: utc_mean_start60 is 30 min from utc_instant, and 10 min
# leaves 20 min of offset error before a mean60 record could be confirmed, with the high- and the
# low-water offsets (independent averages) each required within it.
SUB_BAND_MIN = 10.0
# GESLA contributors whose site code is the NOAA station number (DA_SAT: eastport_maine-8410140-usa-da_sat).
NOAA_NUMBERED = {"noaa", "da_sat"}
KM_PER_DEG = 111.19              # km per degree of latitude (and of longitude at the equator)
GRID_S = 600                     # comparator series step for (b) and (c)
PAD_S = 4 * 3600
MATCH_S = 3 * 3600               # an official extremum matches the nearest model extremum of its type within 3 h
MIN_EVENTS = 50
MIN_MATCHED = 0.8
INHERIT_MIN = 3
REF_WINDOWS = (("2026-11-01", "2027-01-01"), ("2027-03-01", "2027-05-01"), ("2027-06-01", "2027-08-01"))
REASON_MAX = 240
OFFICIAL_BAND_MIN = 12.0         # (b)/(c) record-level class band [unverified; S5 calibrates]: see the docstring
MIN_TRAILING_MONTHS = 12
M2 = "M2"

# Countries with one civil time zone (for the local_dst class). Others: local_dst is not tested.
ZONES = {"deu": "Europe/Berlin", "nor": "Europe/Oslo", "swe": "Europe/Stockholm", "dnk": "Europe/Copenhagen",
         "fin": "Europe/Helsinki", "nld": "Europe/Amsterdam", "bel": "Europe/Brussels", "fra": "Europe/Paris",
         "gbr": "Europe/London", "irl": "Europe/Dublin", "ita": "Europe/Rome", "pol": "Europe/Warsaw",
         "est": "Europe/Tallinn", "lva": "Europe/Riga", "ltu": "Europe/Vilnius", "grc": "Europe/Athens",
         "hrv": "Europe/Zagreb", "svn": "Europe/Ljubljana", "mlt": "Europe/Malta", "cyp": "Asia/Nicosia",
         "mne": "Europe/Podgorica", "alb": "Europe/Tirane", "bgr": "Europe/Sofia", "rou": "Europe/Bucharest",
         "isr": "Asia/Jerusalem", "mco": "Europe/Monaco"}


# --- inputs ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RecordInfo:
    file_id: str
    contributor: str
    interval_s: int
    lat: float
    lon: float
    start_s: int
    end_s: int
    country: str | None = None      # the GESLA country code of the file id (country_of); None: unknown


def _iso_s(s: str) -> int:
    """Unix seconds of an ISO time: an explicit offset (or Z) is kept; no zone means UTC, never the
    machine's local zone."""
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    t = t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)
    return int(t.timestamp())


def load_inventory(path: str | Path) -> dict[str, RecordInfo]:
    """data/gesla/<version>/inventory.csv (G02). A record without a sampling interval is an error."""
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not (r.get("interval_s") or "").strip():
                raise ValueError(f"{path}: {r['file_id']}: no interval_s")
            out[r["file_id"]] = RecordInfo(r["file_id"], r["contributor"], int(r["interval_s"]),
                                           float(r["lat"]), float(r["lon"]), _iso_s(r["start"]), _iso_s(r["end"]),
                                           country_of(r["file_id"]))
    return out


def load_candidates(path: str | Path) -> list[str]:
    """Candidate records: the file ids of a station map (G07a station_map.csv: id,file_id,...)."""
    with open(path, newline="") as fh:
        return sorted({r["file_id"] for r in csv.DictReader(fh) if r.get("file_id")})


def siblings(inv: Mapping[str, RecordInfo], ids: Iterable[str] | None = None) -> dict[str, list[str]]:
    """Records within SIBLING_DEG (lat and lon), another contributor, overlapping spans."""
    pool = sorted(inv) if ids is None else sorted(set(ids))
    cell: dict[tuple[int, int], list[str]] = defaultdict(list)
    for f in sorted(inv):
        r = inv[f]
        cell[(math.floor(r.lat / SIBLING_DEG), math.floor(r.lon / SIBLING_DEG))].append(f)
    out = {}
    for f in pool:
        r = inv[f]
        ci, cj = math.floor(r.lat / SIBLING_DEG), math.floor(r.lon / SIBLING_DEG)
        sib = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for g in cell.get((ci + di, cj + dj), ()):
                    s = inv[g]
                    if g == f or s.contributor == r.contributor:
                        continue
                    if abs(s.lat - r.lat) <= SIBLING_DEG + 1e-9 and abs(s.lon - r.lon) <= SIBLING_DEG + 1e-9 \
                            and s.start_s < r.end_s and r.start_s < s.end_s:
                        sib.append(g)
        out[f] = sorted(sib)
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, a)))


@dataclass(frozen=True)
class OfficialRef:
    src: str
    ref: str
    dist_km: float      # gauge to official station (upper bound: via the app station)
    path: str
    method: str = "distance"    # "alias" (the record names the station) or "distance"
    events: tuple[tuple[int, int], ...] | None = None   # built extrema (subordinate); else read from path
    via: str = ""               # how the events were built (subordinate: reference station and offsets)

    @property
    def confirm_only(self) -> bool:
        return self.src in CONFIRM_ONLY_SOURCES

    @property
    def band_min(self) -> float:
        return SUB_BAND_MIN if self.src == noaa_subordinate.SRC else OFFICIAL_BAND_MIN


class _Points:
    """Official stations (lat, lon, dist to the station, src, ref, path) with the nearest-station search."""

    def __init__(self, pts: list[tuple[float, float, float, str, str, str]]):
        self.points = pts
        self.by_ref: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i, p in enumerate(pts):
            self.by_ref[(p[3], p[4])].append(i)
        self.lat = np.array([p[0] for p in pts]) if pts else np.zeros(0)
        self.lon = np.array([p[1] for p in pts]) if pts else np.zeros(0)

    def alias(self, lat: float, lon: float, src: str, ref: str) -> OfficialRef | None:
        if (src, ref) not in self.by_ref:
            return None
        plat, plon, d, s, r, path = min((self.points[i] for i in self.by_ref[(src, ref)]),
                                        key=lambda p: haversine_km(lat, lon, p[0], p[1]) + p[2])
        return OfficialRef(s, r, round(haversine_km(lat, lon, plat, plon) + d, 3), path, "alias")

    def nearest(self, lat: float, lon: float, radius_km: float, country: str | None) -> OfficialRef | None:
        if not len(self.lat):
            return None
        # Prefilter by distance (km): latitude, and longitude scaled by cos(lat) with the +-180 wrap.
        dlon = np.abs((self.lon - lon + 180.0) % 360.0 - 180.0)
        cos = max(math.cos(math.radians(min(abs(lat) + 0.05, 90.0))), 0.0)
        near = np.nonzero((np.abs(self.lat - lat) * KM_PER_DEG < radius_km + 0.1)
                          & (dlon * KM_PER_DEG * cos < radius_km + 0.1))[0]
        best = None
        for i in near:
            plat, plon, d, src, ref, path = self.points[i]
            if country is not None and country not in SOURCE_COUNTRIES.get(src, ()):
                continue
            total = haversine_km(lat, lon, plat, plon) + d
            if total <= radius_km:
                key = (round(total, 6), src, ref)
                if best is None or key < best[0]:
                    best = (key, OfficialRef(src, ref, round(total, 3), path))
        return best[1] if best else None

    def pair(self, lat: float, lon: float, file_id: str | None, alias: tuple[str, str] | None) -> OfficialRef | None:
        """The F3 pairing: an alias, else the nearest within REF_MAX_KM (any country), else (with the
        record id) the nearest within PORT_KM in the source's own country."""
        got = self.alias(lat, lon, *alias) if alias else None
        if got is None:
            got = self.nearest(lat, lon, REF_MAX_KM, None)
        if got is None and file_id:
            parts = file_id.split("-")
            country = parts[-2] if len(parts) >= 3 else None
            got = self.nearest(lat, lon, PORT_KM, country) if country else None
        return got


def default_dmi_dir() -> Path:
    """The DMI tidewater events (dmi_reference) the audit reads unless told otherwise."""
    return otc_work() / "cache/dmi-tidewater/events"


def default_noaa_cache() -> Path:
    """A-NOAA's cache (stations.json, offsets/, harcon/) the audit reads unless told otherwise."""
    return otc_work() / "cache/noaa"


class References:
    """The harness reference cache ($OTC_GATES_DIR, G09a): official extrema and NOAA harcon.
    An official station's position is known through the app stations the harness mapped to it
    (evid/testplan nearest_ref.json, with the station-to-reference distance).
    Confirm-only references (G04f): DMI tidewater events (dmi_dir, default
    $OTC_WORK/cache/dmi-tidewater/events, written by `python -m gesla.dmi_reference fetch`) and NOAA
    subordinate stations (A-NOAA's station list and offsets in noaa_cache_dir, default the parent of
    noaa_harcon_dir, with their reference station's harness events)."""

    def __init__(self, gates_dir: str | Path, noaa_harcon_dir: str | Path | None = None,
                 dmi_dir: str | Path | None = None):
        self.root = Path(gates_dir)
        # A-NOAA's cache of NOAA's published harmonic constants (<id>.json): (c) for NOAA stations
        # the harness never fetched a harcon for.
        self.noaa_harcon_dir = (Path(noaa_harcon_dir) if noaa_harcon_dir is not None
                                else otc_work() / "cache/noaa/harcon")
        hpath = self.root / "evid/testplan/harmonics_stations.json"
        npath = self.root / "evid/testplan/nearest_ref.json"
        meta = {}
        for s in json.loads(hpath.read_text()):
            if not isinstance(s, dict) or "id" not in s:
                raise ValueError(f"{hpath}: an entry without an id: {str(s)[:120]}")
            meta[s["id"]] = s
        near = json.loads(npath.read_text())
        pts = []
        for sid, r in near.items():
            m = meta.get(sid)
            if not m or m.get("lat") is None:
                continue
            try:
                if float(r.get("dist_km", 99)) > REF_MAX_KM:
                    continue
                src, ref = r["src"], str(r["ref"])
                lat, lon, dist = float(m["lat"]), float(m["lon"]), float(r["dist_km"])
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                raise ValueError(f"{npath} / {hpath.name}: station {sid}: bad reference entry "
                                 f"({type(e).__name__}: {e}): {str(r)[:120]}") from e
            if src == "noaa" and r.get("kind") != "R":
                continue        # NOAA subordinate (S) predictions are offsets from another station, not a prediction of this gauge
            p = self.root / "refs" / src / "events" / f"{ref}.json"
            if p.exists():
                pts.append((lat, lon, dist, src, ref, str(p)))
        self._harness = _Points(pts)
        self.points = pts
        dmi = Path(dmi_dir) if dmi_dir is not None else default_dmi_dir()
        self._dmi = _Points([(lat, lon, 0.0, dmi_reference.SRC, sid, p)
                             for lat, lon, sid, p in dmi_reference.load_points(dmi)])
        cache = self.noaa_harcon_dir.parent if noaa_harcon_dir is not None else default_noaa_cache()
        subs = noaa_subordinate.load_subordinates(cache, self.root / "refs/noaa/events")
        self._subs = {s.station_id: s for s in subs}
        self._sub_pts = _Points([(s.lat, s.lon, 0.0, noaa_subordinate.SRC, s.station_id, s.offsets_path) for s in subs])
        self._sub_events: dict[str, tuple[tuple[int, int], ...]] = {}

    def counts(self) -> dict[str, int]:
        """How many references of each kind were loaded (an empty confirm-only cache is legal, but visible)."""
        return {"harness": len(self.points), "dmi": len(self._dmi.points), "noaa_sub": len(self._subs)}

    def official(self, lat: float, lon: float, file_id: str | None = None) -> OfficialRef | None:
        """The record's official station: a harness reference (an alias, else the nearest within
        REF_MAX_KM in any country, else with the record id the nearest within PORT_KM in the source's
        own country); else, paired the same way, a DMI station; else a NOAA subordinate station of the
        same gauge (subordinate(); both confirm-only)."""
        nid = noaa_number(file_id) if file_id else None
        got = self._harness.pair(lat, lon, file_id, ("noaa", nid) if nid else None)
        if got is None:
            got = self._dmi.pair(lat, lon, file_id, None)
        if got is None:
            got = self.subordinate(lat, lon, file_id)
        return got

    def subordinate(self, lat: float, lon: float, file_id: str | None = None) -> OfficialRef | None:
        """The record's NOAA subordinate station with its built extrema: an alias, else the same gauge
        (<= REF_MAX_KM). Never the same port: across 2-5 km a subordinate station's own tide differed
        from a known-good record's by 45-97 min in 18 of 105 pairs (G04f control-knowngood.txt; inlets,
        lagoons, rivers), enough to confirm a record an hour off (fpr.txt)."""
        nid = noaa_number(file_id) if file_id else None
        got = self._sub_pts.alias(lat, lon, noaa_subordinate.SRC, nid) if nid else None
        if got is None:
            got = self._sub_pts.nearest(lat, lon, REF_MAX_KM, None)
        if got is None:
            return None
        s = self._subs[got.ref]
        if s.station_id not in self._sub_events:
            self._sub_events[s.station_id] = noaa_subordinate.subordinate_events(
                s, load_events(s.ref_events_path, pad_s=int(abs(s.high_min) + abs(s.low_min)) * 60 + 3600))
        return replace(got, events=self._sub_events[s.station_id],
                       via=f"noaa:{s.ref_id} high {s.high_min:+.0f} min, low {s.low_min:+.0f} min")

    def harcon(self, file_id: str) -> Path | None:
        nid = noaa_number(file_id)
        if nid is None:
            return None
        for p in (self.root / "refs/noaa/harcon" / f"harcon_{nid}.json", self.noaa_harcon_dir / f"{nid}.json"):
            if p.exists():
                return p
        return None


def noaa_id(file_id: str) -> str | None:
    parts = file_id.split("-")
    if len(parts) >= 4 and parts[-1] == "noaa" and parts[-2] == "usa" and parts[-3].isdigit():
        return parts[-3]
    return None


def country_of(file_id: str) -> str | None:
    """The GESLA country code of a file id (<site>-<code>-<country>-<contributor>: a121tg-a12-nld-cmems
    is nld), the field the same-port pairing and the zone lookup read; None when the id has none."""
    parts = file_id.split("-")
    c = parts[-2] if len(parts) >= 3 else ""
    return c if len(c) == 3 and c.isalpha() else None


def noaa_number(file_id: str) -> str | None:
    """The NOAA station number of a record whose contributor numbers its gauges by NOAA's (NOAA_NUMBERED)."""
    parts = file_id.split("-")
    if len(parts) >= 4 and parts[-1] in NOAA_NUMBERED and len(parts[-3]) == 7 and parts[-3].isdigit():
        return parts[-3]
    return None


def zone_for(file_id: str) -> str | None:
    parts = file_id.split("-")
    return ZONES.get(parts[-2]) if len(parts) >= 3 else None


def audit_span(s: Samples) -> tuple[int, int] | None:
    """[start, end) of the fit's span: the last FIT_SPAN_YEARS of good data (BP §4)."""
    if not len(s[0]):
        return None
    hi = int(s[0][-1]) + 1
    lo = max(int(s[0][0]), int(math.ceil(s[0][-1] - FIT_SPAN_YEARS * SECONDS_PER_YEAR)))
    return lo, hi


# --- prediction and the (b)/(c) comparators ------------------------------------------------------

def predict(table: TcdTable, consts: Mapping[str, tuple[float, float]], z0: float, times_s: np.ndarray,
            chunk: int = 50_000) -> np.ndarray:
    """z0 + sum f A cos(speed t + V0+u - g) (the fit's and the engine's convention)."""
    names = [n for n in consts if n in table]
    tn = [table.name(n) for n in names]
    coef = np.empty(2 * len(names))
    for j, n in enumerate(names):
        a, g = consts[n]
        coef[2 * j], coef[2 * j + 1] = a * math.cos(math.radians(g)), a * math.sin(math.radians(g))
    t = np.asarray(times_s, dtype=np.int64)
    out = np.empty(len(t))
    for i in range(0, len(t), chunk):
        out[i:i + chunk] = z0 + design(table, tn, t[i:i + chunk]) @ coef
    return out


def extrema(t: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local maxima (+1) and minima (-1) of a 1-min series, refined by a parabola."""
    d = np.diff(h)
    hi = np.nonzero((d[:-1] > 0) & (d[1:] <= 0))[0] + 1
    lo = np.nonzero((d[:-1] < 0) & (d[1:] >= 0))[0] + 1
    idx = np.concatenate([hi, lo])
    kind = np.concatenate([np.ones(len(hi)), -np.ones(len(lo))])
    a, b, c = h[idx - 1], h[idx], h[idx + 1]
    den = a - 2 * b + c
    off = np.where(den != 0, 0.5 * (a - c) / np.where(den != 0, den, 1), 0.0)
    step = float(t[1] - t[0]) if len(t) > 1 else 60.0
    tt = t[idx] + off * step
    order = np.argsort(tt)
    return tt[order], kind[order]


def load_events(path: str | Path, pad_s: int = 0) -> list[tuple[int, int]]:
    """Official extrema in the harness windows (widened by pad_s each side): (unix s, +1 High / -1 Low)."""
    d = json.loads(Path(path).read_text())
    wins = [(_iso_s(a) - pad_s, _iso_s(b) + pad_s) for a, b in REF_WINDOWS]
    out = []
    for when, typ, _h in d.get("events", []):
        s = _iso_s(when)       # no zone: UTC (the harness writes Z); never the machine's zone
        if any(a <= s < b for a, b in wins):
            out.append((s, 1 if typ.lower().startswith("h") else -1))
    return out


def in_windows(events: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    wins = [(_iso_s(a), _iso_s(b)) for a, b in REF_WINDOWS]
    return [e for e in events if any(a <= e[0] < b for a, b in wins)]


def match_offset(model_t: np.ndarray, model_k: np.ndarray, events: Sequence[tuple[int, int]]) -> dict:
    """Median (model - official) time of matched extrema, minutes."""
    lags = []
    for s, k in events:
        sel = model_k == k
        mt = model_t[sel]
        if not len(mt):
            continue
        i = int(np.searchsorted(mt, s))
        best = min((abs(mt[j] - s), mt[j] - s) for j in (i - 1, i) if 0 <= j < len(mt))
        if best[0] <= MATCH_S:
            lags.append(best[1] / 60.0)
    n = len(events)
    if not lags:
        return {"n_events": n, "n_matched": 0, "lag_min": None, "iqr_min": None}
    q = np.percentile(lags, [25, 50, 75])
    return {"n_events": n, "n_matched": len(lags), "lag_min": round(float(q[1]), 2),
            "iqr_min": round(float(q[2] - q[0]), 2)}


def _grid(span: tuple[int, int]) -> np.ndarray:
    lo = (span[0] - PAD_S) // GRID_S * GRID_S
    return np.arange(lo, span[1] + PAD_S, GRID_S, dtype=np.int64)


@dataclass
class Comparator:
    """A series comparator for evaluate(): an (a) sibling's corrected series."""
    kind: str
    id: str
    samples: Samples
    m2_response: float = 1.0
    can_mean60: bool = False     # an observed sub-hourly series (the hour-mean test)
    evidence: dict = field(default_factory=dict)


def _trial_fit(record: GaugeRecord, table: TcdTable, span: tuple[int, int]):
    return analyse(table, in_tcd_years(table, between(clean(record), span[0], span[1])))


def official_lag(record: GaugeRecord, table: TcdTable, ref: OfficialRef, span: tuple[int, int]) -> dict | str:
    """(b): trial fit of the record over its audit span, predicted at 1 min over W1-W3 and matched to
    the official extrema. lag_min = median (model - official). A string says why there is none."""
    try:
        an = _trial_fit(record, table, span)
    except (FitError, ValueError, np.linalg.LinAlgError) as e:
        return f"trial fit failed: {e}"
    consts = an.consts()
    events = in_windows(ref.events) if ref.events is not None else load_events(ref.path)
    if len(events) < MIN_EVENTS:
        return f"official:{ref.src}:{ref.ref}: {len(events)} official extrema in the windows (< {MIN_EVENTS})"
    mt, mk = [], []
    for a, b in REF_WINDOWS:
        t = np.arange(_iso_s(a) - 6 * 3600, _iso_s(b) + 6 * 3600, 60, dtype=np.int64)
        x, k = extrema(t, predict(table, consts, an.fit.z0, t))
        mt.append(x)
        mk.append(k)
    mt, mk = np.concatenate(mt), np.concatenate(mk)
    m = match_offset(mt, mk, events)
    m.update(kind="b", id=f"official:{ref.src}:{ref.ref}", dist_km=ref.dist_km, method=ref.method,
             n_constituents=len(consts))
    if ref.confirm_only:
        # high and low waters separately: a subordinate station's two offsets are independent averages
        m.update(confirm_only=True, band_min=ref.band_min,
                 lag_high=match_offset(mt, mk, [e for e in events if e[1] > 0])["lag_min"],
                 lag_low=match_offset(mt, mk, [e for e in events if e[1] < 0])["lag_min"])
        if ref.via:
            m["via"] = ref.via
    if m["lag_min"] is None or m["n_matched"] < MIN_MATCHED * m["n_events"]:
        return f"{m['id']}: {m['n_matched']} of {m['n_events']} official extrema matched"
    return m


def harcon_lag(record: GaugeRecord, table: TcdTable, path: Path, span: tuple[int, int]) -> dict | str:
    """(c): per-constituent phase lags of a trial fit against NOAA's harmonic constants. lag_min is
    the lag of the largest of M2, S2, K1, O1 in the harcon."""
    try:
        hc = load_noaa_harcon(path)
    except ValueError as e:
        return f"harcon: {e}"
    try:
        an = _trial_fit(record, table, span)
    except (FitError, ValueError, np.linalg.LinAlgError) as e:
        return f"trial fit failed: {e}"
    lags = {}
    for n in ("M2", "S2", "K1", "O1"):
        if n in hc and n in table and table.name(n) in an.fit.amplitude_m and hc[n][0] > 0:
            tn = table.name(n)
            dg = (an.fit.phase_deg[tn] - hc[n][1] + 180.0) % 360.0 - 180.0
            lags[n] = {"lag_min": round(dg / table.speed(tn) * 60.0, 2),
                       "amp_ratio": round(an.fit.amplitude_m[tn] / hc[n][0], 4), "amp_m": round(hc[n][0], 4)}
    if not lags:
        return f"harcon {path.name}: no M2, S2, K1 or O1 in both the fit and the harcon"
    main = max(lags, key=lambda n: lags[n]["amp_m"])
    return {"kind": "c", "id": f"harcon:{path.stem.removeprefix('harcon_')}", "lag_min": lags[main]["lag_min"],
            "by": main, "constituents": lags}


# Only utc_instant is concluded from a record-level lag. A whole-hour offset is not: the agency's method
# offset can reach ~20 min in estuaries (WSV Elbe gauges against BSH: -50..-55 min = mean60 -30 plus
# ~-20), which a +-12 min band would take for utc_minus_1. Those go to the siblings' monthly test.
OFFICIAL_CLASSES = [(0.0, "utc_instant", 0)]


def _fmt_lag(x) -> str:
    return "-" if x is None else f"{float(x):+.1f}"


def official_outcome(record_id: str, ev: dict, span: tuple[int, int]) -> "Outcome":
    """A record-level verdict from one (b)/(c) lag. Conclusive only for OFFICIAL_CLASSES (utc_instant)
    within OFFICIAL_BAND_MIN; anything else (a whole-hour offset, a mean60-like lag, DST, an off-class
    lag) is inconclusive and left to the siblings' monthly comparison."""
    lag = float(ev["lag_min"])
    base = {"lags": {"median": lag, "n_months": 0}, "official": ev}
    band = float(ev.get("band_min", OFFICIAL_BAND_MIN))
    if ev.get("confirm_only"):
        # a confirm-only reference: the median, the high-water and the low-water lag all within its band
        parts = [ev.get("lag_high"), ev.get("lag_low")]
        if any(x is None for x in parts) or not all(abs(float(x)) <= band for x in parts + [lag]):
            why = (f"record-level lag {lag:+.1f} min (high {_fmt_lag(parts[0])}, low {_fmt_lag(parts[1])}) "
                   f"not all within +-{band:g} min")
            v = Verdict(record_id, ev["id"], "unexplained", "unexplained", NOT_USABLE, False, span=span,
                        evidence=base, reasons=[why])
            return Outcome(ev["kind"], ev["id"], v, False, why, ev)
    for centre, kind, n in OFFICIAL_CLASSES:
        if abs(lag - centre) <= band:
            corr = Correction(kind, n=n)
            v = Verdict(record_id, ev["id"], corr.label, corr.label, corr, True, span=span,
                        residual={"n_months": 0, "max_abs": abs(lag - centre), "off": {}}, evidence=base)
            return Outcome(ev["kind"], ev["id"], v, True, f"record-level lag {lag:+.1f} min", ev)
    near = min(OFFICIAL_CLASSES + [(MEAN60_LAG, "utc_mean_start60", 0)] + [(60.0 * n, "utc_plus_n", n) for n in (1, 2, -1, -2)],
               key=lambda c: abs(lag - c[0]))
    why = (f"record-level lag {lag:+.1f} min fits utc_mean_start60, which an official comparator cannot test"
           if near[1] == "utc_mean_start60" and abs(lag - near[0]) <= OFFICIAL_BAND_MIN else
           f"record-level lag {lag:+.1f} min matches no class an official comparator can test")
    v = Verdict(record_id, ev["id"], "unexplained", "unexplained", NOT_USABLE, False, span=span, evidence=base,
                reasons=[why])
    return Outcome(ev["kind"], ev["id"], v, False, why, ev)


def sibling_comparator(sib: GaugeRecord, verdict: "AuditRow", table: TcdTable | None = None) -> Comparator:
    """(a): the sibling's corrected series over its verdict span."""
    t, h = verdict.correction(*samples(sib))
    t, h = np.asarray(t, dtype=np.int64), np.asarray(h, dtype=np.float64)
    order = np.argsort(t, kind="stable")
    t, h = t[order], h[order]
    if verdict.span:
        lo, hi = verdict.span
        m = (t >= lo) & (t < hi)
        t, h = t[m], h[m]
    m2 = 1.0
    if verdict.base == "utc_mean_start60":
        m2 = boxcar_response(table.speed(M2) if table else 28.9841042)
    return Comparator("a", f"{sib.record_id}~corrected", (t, h), m2_response=m2,
                      can_mean60=sib.interval_min < 60, evidence={"sibling_verdict": verdict.verdict})


# --- judging one sibling comparator --------------------------------------------------------------

@dataclass
class Outcome:
    kind: str
    comparator_id: str
    verdict: Verdict
    conclusive: bool
    note: str = ""
    comparator_evidence: dict = field(default_factory=dict)
    monthly: dict = field(default_factory=dict)        # raw lag per month over the audit span (siblings)
    audit_span: tuple[int, int] | None = None


def _lag_median(v: Verdict) -> float | None:
    return (v.evidence.get("lags") or {}).get("median")


def _month_start(y: int, m: int) -> int:
    return int(np.datetime64(f"{y:04d}-{m:02d}", "M").astype("datetime64[s]").astype(np.int64))


def trailing_span(rows: Sequence, span: tuple[int, int]) -> tuple[int, int] | None:
    """The span after the last run of >= 2 adjacent off months (|lag - class| > BAND_MIN, class = the
    lag class nearest the median), when it holds at least MIN_TRAILING_MONTHS audited months. This
    is the plan's "use only the part after" (BP §2.3 step) for a run the months_bad rule cannot drop."""
    if not rows:
        return None
    med = statistics.median(r.lag_min for r in rows)
    centre = min([c for c, _, _ in LAG_CLASSES], key=lambda c: abs(med - c))
    off = sorted(r.year * 12 + r.month - 1 for r in rows if abs(r.lag_min - centre) > BAND_MIN)
    last_run_end = None
    for a, b in zip(off, off[1:]):
        if b - a == 1:
            last_run_end = b
    if last_run_end is None:
        return None
    nxt = last_run_end + 1
    lo = _month_start(nxt // 12, nxt % 12 + 1)
    if sum(1 for r in rows if r.year * 12 + r.month - 1 >= nxt) < MIN_TRAILING_MONTHS:
        return None
    return max(lo, span[0]), span[1]


def judge(record: GaugeRecord, comp: Comparator, table: TcdTable, span: tuple[int, int]) -> Outcome:
    """evaluate() against one sibling series, then decide whether the comparator could test the result.
    A conclusive `unexplained` caused by a run of adjacent off months is re-tested on the trailing
    span after the run (the verdict then carries that shorter span: the fit may use only it)."""
    zone = zone_for(record.record_id)
    v = evaluate(record, comp.samples, comparator_id=comp.id, table=table, zone=zone, span=span,
                 comparator_m2_response=comp.m2_response)
    o = _classify(v, comp)
    o.monthly, o.audit_span = dict(v.evidence.get("monthly") or {}), span
    if o.verdict.verdict == "unexplained" and o.conclusive:
        s = samples(record)
        m = (s[0] >= span[0]) & (s[0] < span[1])
        tail = trailing_span(monthly_lags((s[0][m], s[1][m]), comp.samples), span)
        if tail is not None:
            v2 = evaluate(record, comp.samples, comparator_id=comp.id, table=table, zone=zone, span=tail,
                          comparator_m2_response=comp.m2_response)
            o2 = _classify(v2, comp)
            if o2.verdict.passed and o2.conclusive:
                o2.monthly, o2.audit_span = o.monthly, span
                v2.evidence["span_limited"] = {"audit_span": [_iso_day(span[0]), _iso_day(span[1])],
                                               "full_span_reasons": [r[:REASON_MAX] for r in v.reasons][:4]}
                o2.note = (f"span limited to {_iso_day(tail[0])}..: adjacent off months before it "
                           f"({'; '.join(r[:120] for r in v.reasons if 'not isolated' in r)[:200]})")
                return o2
    return o


def _classify(v: Verdict, comp: Comparator) -> Outcome:
    if v.verdict == "no_comparator":
        return Outcome(comp.kind, comp.id, v, False, "no overlap", comp.evidence)
    med = _lag_median(v)
    mean60_like = med is not None and abs(med - MEAN60_LAG) <= BAND_MIN
    if v.passed:
        if v.base == "utc_mean_start60" and not comp.can_mean60:
            return Outcome(comp.kind, comp.id, v, False, "the comparator cannot run the hour-mean test", comp.evidence)
        return Outcome(comp.kind, comp.id, v, True, "", comp.evidence)
    untestable = any("sampling test not possible" in r for r in v.reasons)
    if mean60_like and (not comp.can_mean60 or untestable):
        return Outcome(comp.kind, comp.id, v, False,
                       "lag fits utc_mean_start60, which this comparator cannot test", comp.evidence)
    return Outcome(comp.kind, comp.id, v, True, "", comp.evidence)


# --- rows ----------------------------------------------------------------------------------------

@dataclass
class AuditRow:
    file_id: str
    candidate: bool
    contributor: str
    interval_s: int
    status: str                  # used | residue (disputed, D5) | unverified (no comparator)
    verdict: str
    base: str
    correction: Correction
    round: int | None = None
    comparator_kind: str = ""    # b | c | a | inherit | none
    comparator_id: str = ""
    span: tuple[int, int] | None = None
    months_bad: list[str] = field(default_factory=list)
    outcome: Outcome | None = None
    tried: list[str] = field(default_factory=list)
    reason: str = ""
    official: dict = field(default_factory=dict)
    official_verdict: str = ""   # the class a conclusive (b)/(c) comparator gave, also when a sibling refined it
    task_failed: bool = False    # a measurement task raised: the verdict is not known (not `unexplained` by evidence)
    weak: dict = field(default_factory=dict)        # the weak checks' evidence (comparator kind "w")
    annotation: str = ""         # verified | corrected | unverified | disputed (annotate())
    annotation_code: str = ""    # machine-readable: <annotation>:<evidence>, corrected:<from>-><to>:<evidence>
    annotation_reason: str = ""
    published: Correction | None = None     # the correction published: the audit's, or the declared one
    sibling: dict = field(default_factory=dict)     # the intra-site constants / provider-family evidence (TB-SIB)
    in_pool: bool = True         # False: a record outside the pool (no served station, no sibling of one)
    agency: dict = field(default_factory=dict)      # the agency-constants evidence (FIX-LST, timebase_agency)

    @property
    def usable(self) -> bool:
        """The audit's own verdict is usable (verified or corrected): also a comparator for others."""
        return self.status == "used" and self.correction.usable


def _row_from(info: RecordInfo, cand: bool, o: Outcome, rnd: int, tried: list[str], reason: str = "") -> AuditRow:
    v = o.verdict
    status = "used" if v.passed else "residue"
    return AuditRow(info.file_id, cand, info.contributor, info.interval_s, status, v.verdict, v.base,
                    v.correction if v.passed else NOT_USABLE, rnd, o.kind, o.comparator_id, v.span,
                    list(v.months_bad), o, tried, reason or (o.note or (v.reasons[0] if (not v.passed and v.reasons) else "")),
                    o.comparator_evidence if o.kind in ("b", "c") else {})


def _cleanest(outs: Sequence[Outcome]) -> Outcome:
    """The passing sibling outcome with the fewest dropped months, the longest span, then the lowest id."""
    return sorted(outs, key=lambda o: (len(o.verdict.months_bad),
                                       -((o.verdict.span[1] - o.verdict.span[0]) if o.verdict.span else 0),
                                       o.comparator_id))[0]


MONTH_CENTRES = {"utc_instant": 0.0, "utc_mean_start60": MEAN60_LAG}


def _centre(o: Outcome) -> float | None:
    c = o.verdict.correction
    if c.kind == "utc_plus_n":
        return 60.0 * c.n
    return MONTH_CENTRES.get(c.kind)


def _midx(key: str) -> int:
    y, m = key.split("-")
    return int(y) * 12 + int(m) - 1


def month_vote(passing: Sequence[Outcome], sib: Sequence[Outcome]) -> tuple[Outcome, str]:
    """Several siblings audit a record month by month: a month is off for the RECORD only when it is
    off (|raw lag - class centre| > BAND_MIN) against more than half of the comparators that audited
    it (or against the only one). A month off against one comparator but fine against the others is that comparator's fault
    (Cuxhaven UHSLC_FD 2024 against CMEMS). The off months are then dropped if isolated, else the
    record keeps the trailing span after the last run (as judge); else it is unexplained.
    With one comparator (or a class without a fixed centre: local_dst, step) the cleanest passing
    outcome stands. Only comparators compatible with the record's class vote: one that passed with
    another class (or failed with its median nearest another class) disagrees on the class, not on
    the months; that disagreement is decide()'s, not a month vote's."""
    best = _cleanest(passing)
    centre = _centre(best)
    voters = [o for o in sib if o.conclusive and o.monthly and _compatible(o, best, centre)] \
        if centre is not None else []
    if len(voters) < 2:
        return best, ""
    votes: dict[int, list[bool]] = defaultdict(list)
    for o in voters:
        for k, lag in o.monthly.items():
            votes[_midx(k)].append(abs(lag - centre) > BAND_MIN)
    audited = sorted(votes)
    # off for the record: against the only comparator of that month, or against more than half of them
    bad = [m for m in audited if (len(votes[m]) == 1 and votes[m][0]) or sum(votes[m]) * 2 > len(votes[m])]
    v = best.verdict
    span = best.audit_span or v.span
    who = ", ".join(o.comparator_id for o in voters)
    months = [(m // 12, m % 12 + 1) for m in bad]
    label_of = lambda ms: (f"months_bad:{','.join(month_key(x) for x in ms)}" if ms else v.base)
    if isolated(months, [(m // 12, m % 12 + 1) for m in audited]):
        nv = replace(v, verdict=label_of(months), months_bad=[month_key(x) for x in months], span=span,
                     correction=replace(v.correction, months_drop=tuple(months)))
        return replace(best, verdict=nv), f"months voted over {len(voters)} comparators ({who}): {len(bad)} off"
    runs = [b for a, b in zip(bad, bad[1:]) if b - a == 1]
    if not runs:        # not isolated only by count: no run to cut after
        return replace(best, verdict=_unexplained(v)), (
            f"months voted over {len(voters)} comparators ({who}): {len(bad)} off months of {len(audited)} "
            f"(> {MAX_BAD_FRACTION:.0%}), not isolated")
    nxt = runs[-1] + 1
    tail_months = [m for m in audited if m >= nxt]
    tail_bad = [(m // 12, m % 12 + 1) for m in bad if m >= nxt]
    if len(tail_months) >= MIN_TRAILING_MONTHS and isolated(tail_bad, [(m // 12, m % 12 + 1) for m in tail_months]):
        lo = _month_start(nxt // 12, nxt % 12 + 1)
        nv = replace(v, verdict=label_of(tail_bad), months_bad=[month_key(x) for x in tail_bad],
                     span=(max(lo, span[0]), span[1]), correction=replace(v.correction, months_drop=tuple(tail_bad)))
        return replace(best, verdict=nv), (f"months voted over {len(voters)} comparators ({who}): span limited to "
                                           f"{_iso_day(lo)}.. after adjacent off months")
    return replace(best, verdict=_unexplained(v)), (f"months voted over {len(voters)} comparators ({who}): "
                                                    f"adjacent off months not isolated")


def _compatible(o: Outcome, best: Outcome, centre: float) -> bool:
    if o.verdict.passed:
        return o.verdict.base == best.verdict.base
    med = _lag_median(o.verdict)
    return med is not None and min((c for c, _, _ in LAG_CLASSES), key=lambda c: abs(med - c)) == centre


def decide(outcomes: Sequence[Outcome], *, final: bool) -> tuple[Outcome | None, str]:
    """The record's verdict from its comparator outcomes (None: undecided yet).

    1. Conclusive official (b)/(c) outcomes must agree; their class decides (official beats sibling).
       Passing sibling outcomes of the SAME class then refine it month by month (the cleanest is
       taken); if siblings were compared and none passes, the pass rule failed on the audited months.
    2. Otherwise a passing sibling resolves the record (another sibling's `unexplained` does not
       condemn it); passing siblings must agree on the class.
    3. At the end, a record with outcomes but no verdict is `unexplained`."""
    off = [o for o in outcomes if o.kind in ("b", "c") and o.conclusive]
    sib = [o for o in outcomes if o.kind == "a"]
    if off:
        labels = {o.verdict.verdict for o in off}
        if len(labels) > 1:
            return replace(off[0], verdict=_unexplained(off[0].verdict)), \
                "official comparators disagree: " + ", ".join(f"{o.comparator_id}={o.verdict.verdict}" for o in off)
        anchor = off[0]
        same = [o for o in sib if o.conclusive and o.verdict.passed and o.verdict.base == anchor.verdict.base]
        if same:
            o, vote = month_vote(same, sib)
            return o, (vote + "; " if vote else "") + f"class from {anchor.comparator_id} (record-level lag {_lag_median(anchor.verdict):+.1f} min); months audited against {o.comparator_id}"
        other = [o for o in sib if o.conclusive and o.verdict.passed]
        if other:
            return anchor, "official beats sibling(s) " + ", ".join(f"{o.comparator_id}={o.verdict.verdict}" for o in other)
        failed = [o for o in sib if o.conclusive]
        if failed and (final or len(failed) == len(sib)):
            o = sorted(failed, key=lambda o: o.comparator_id)[0]
            return o, (f"{anchor.comparator_id} gives {anchor.verdict.verdict} at record level, but the pass rule "
                       f"fails on the months audited against {', '.join(x.comparator_id for x in failed)}")
        return anchor, ""
    good = [o for o in sib if o.conclusive and o.verdict.passed]
    if good:
        bases = {o.verdict.base for o in good}
        if len(bases) == 1:
            o, vote = month_vote(good, sib)
            return o, vote
        o = sorted(good, key=lambda o: o.comparator_id)[0]
        return replace(o, verdict=_unexplained(o.verdict)), \
            "siblings disagree: " + ", ".join(f"{o.comparator_id}={o.verdict.verdict}" for o in good)
    if not final:
        return None, ""
    tested = [o for o in outcomes if o.verdict.verdict != "no_comparator"]
    if not tested:
        return None, ""
    conc = [o for o in tested if o.conclusive]
    if conc:
        return sorted(conc, key=lambda o: o.comparator_id)[0], ""
    o = sorted(tested, key=lambda o: o.comparator_id)[0]
    return replace(o, verdict=_unexplained(o.verdict)), \
        "no comparator could test the record: " + "; ".join(f"{o.comparator_id}: {o.note}" for o in tested)


def _unexplained(v: Verdict) -> Verdict:
    return replace(v, verdict="unexplained", correction=NOT_USABLE, passed=False)


# --- workers -------------------------------------------------------------------------------------

_W: dict = {}


@dataclass(frozen=True)
class WeakConfig:
    """The weak evidence's inputs (G04d): timebase_weak.prepare() output, every served station of
    each record (timebase_weak.load_served) and each record's lineage (contributor, inventory role)."""
    inputs: WeakInputs
    served: Mapping[str, Sequence[Served]]
    lineages: Mapping[str, Lineage]


@dataclass(frozen=True)
class SiblingConfig:
    """The intra-site constants comparison's inputs (TB-SIB, timebase_sibling): pass 1, a gesla.fit_run results.jsonl
    of every record fitted with the time base it is published with, by record; and each record's station-map ids."""
    fits: Mapping[str, Mapping]
    stations: Mapping[str, frozenset]


def _init(release_root: str, table_path: str, gates_dir: str, transforms: Mapping[str, tuple[str, int]],
          weak: WeakConfig | None = None) -> None:
    _W["release"] = GeslaRelease(release_root)
    _W["table"] = TcdTable.load(table_path)
    _W["refs"] = References(gates_dir)
    _W["transforms"] = dict(transforms)
    _W["cache"] = {}
    _W["weak"] = weak


def _record(rid: str) -> GaugeRecord:
    cache = _W["cache"]
    if rid not in cache:
        if len(cache) > 8:
            cache.pop(next(iter(cache)))
        tf = _W["transforms"].get(rid)
        if tf:
            src = _record(tf[0])
            cache[rid] = replace(src, record_id=rid, times_s=src.times_s + int(tf[1]))
        else:
            cache[rid] = _W["release"].record(rid, roles={})
    return cache[rid]


def _safe(fn):
    """A record whose measurement raises gets no outcome from that task (named in its notes, extra
    {"failed": True}), so one odd record cannot stop the audit; audit() makes it residue."""
    import functools

    @functools.wraps(fn)
    def run(rid, *a):
        try:
            return fn(rid, *a)
        except Exception as e:      # noqa: BLE001 - recorded per record, never silent
            return rid, [], [f"{fn.__name__} failed: {type(e).__name__}: {str(e)[:200]}"], \
                {"failed": True, "error_type": type(e).__name__}
    return run


@_safe
def _official_task(rid: str, lat: float, lon: float) -> tuple[str, list[Outcome], list[str], dict]:
    rec, table, refs = _record(rid), _W["table"], _W["refs"]
    span = audit_span(samples(rec))
    if span is None:
        return rid, [], ["no good data"], {}
    notes: list[str] = []
    tried: list[str] = []                # every official comparator attempted, also one that gave no lag
    ev: dict | str | None = None
    src_id = _W["transforms"].get(rid, (rid,))[0]
    hp = refs.harcon(src_id)
    if hp is not None:
        tried.append(f"harcon:{hp.stem.removeprefix('harcon_')}")
        ev = harcon_lag(rec, table, hp, span)
        if isinstance(ev, str):          # (c) failed: try (b), keep the note
            notes.append(ev)
            ev = None
    if ev is None:
        ref = refs.official(lat, lon, src_id)
        if ref is not None:
            tried.append(f"official:{ref.src}:{ref.ref}")
        ev = official_lag(rec, table, ref, span) if ref is not None else "no official reference"
    if isinstance(ev, str):
        return rid, [], notes + [ev], {"span": span, "tried": tried}
    o = official_outcome(rid, ev, span)
    if not o.conclusive and ev.get("confirm_only"):
        # DMI predictions / NOAA subordinate offsets confirm utc_instant but never dispute a record.
        via = f", {ev['via']}" if ev.get("via") else ""
        return rid, [], notes + [f"{ev['id']} ({ev['dist_km']:.2f} km{via}, confirm-only): {o.note}; "
                                 f"so no verdict from it"], {"span": span, "tried": tried}
    if not o.conclusive and ev.get("method") == "distance" and float(ev.get("dist_km", 0.0)) > REF_MAX_KM:
        # A station of the same port, not the same gauge, can confirm utc_instant but not dispute
        # a record: across a port (or up a tidal river) the tide itself can lag by more than the band.
        return rid, [], notes + [f"{ev['id']} ({ev['dist_km']:.2f} km, same port): {o.note}; not the same gauge, "
                                 f"so no verdict from it"], {"span": span, "tried": tried}
    return rid, [o], notes, {"span": span, "tried": tried}


@_safe
def _sibling_task(rid: str, sibs: list[tuple[str, AuditRow]]) -> tuple[str, list[Outcome], list[str], dict]:
    rec, table = _record(rid), _W["table"]
    span = audit_span(samples(rec))
    out = []
    if span is None:
        return rid, out, ["no good data"], {}
    for sid, row in sibs:
        comp = sibling_comparator(_record(sid), row, table)
        if len(comp.samples[0]) == 0:
            continue
        out.append(judge(rec, comp, table, span))
    return rid, out, [], {"span": span}


@_safe
def _weak_task(rid: str, n_decl: int, origin_status: Mapping[str, str],
               independent_served_only: bool = False) -> tuple[str, list, list[str], dict]:
    """The weak checks and rule for one record without a comparator verdict, or an inherited one
    (timebase_weak). n_decl: the hours it is judged against. origin_status: the status of the origin record
    of each of its served stations. independent_served_only: see timebase_weak.assess."""
    rec, table, wk = _record(rid), _W["table"], _W["weak"]
    span = audit_span(samples(rec))
    if span is None:
        return rid, [], ["no good data"], {}
    src = _W["transforms"].get(rid, (rid,))[0]
    rows = [served_kind(src, sv, wk.lineages, origin_status) for sv in wk.served.get(src, ())]
    res = assess(rec, table, span, eot=wk.inputs.eot(src), served=rows, zone=wk.inputs.zone(src),
                 n_decl=n_decl, missing_inputs=not wk.inputs.has(src),
                 independent_served_only=independent_served_only)
    return rid, [], [], {"weak": res, "span": span}


@_safe
def _sibling_confirm_task(rid: str, n_new: int, refs: list, origin_status: Mapping[str, str],
                          independent_served_only: bool) -> tuple[str, list, list[str], dict]:
    """Pass 2 of a candidate correction (TB-SIB): the record fitted again with the corrected hours n_new, and
    compared with what it disagreed with: each reference record's pass-1 constants (a site finding; every comparable
    one within timebase_sibling.ALIGN_MIN, at least one), or EOT20 (a provider-family finding; within EOT_ZERO_MIN).
    Then the record's own weak checks at n_new (as an inherited class: independent_served_only when n_new is not the
    declared base), whose disagreement makes the correction yield (yield_inherited's rule)."""
    rec, table, wk = _record(rid), _W["table"], _W["weak"]
    span = audit_span(samples(rec))
    if span is None:
        return rid, [], ["no good data"], {}
    src = _W["transforms"].get(rid, (rid,))[0]
    sp = weak_speeds(table)
    feat = fit_features(rec, table, span, declared_correction(n_new))
    if refs:
        taus = [tsib.pair_shift(feat["consts"], c, sp) for c in refs]
        got = [t for t in taus if t is not None]
        ok = bool(got) and all(abs(t) <= tsib.ALIGN_MIN for t in got)
        p2 = {"against": "references", "taus": [None if t is None else round(t, 1) for t in taus], "ok": ok}
    else:
        lag = tsib.eot_lag(tsib.SiteRecord(rid, rec.lat, rec.lon, rec.gauge_type, feat["consts"], wk.inputs.eot(src)), sp)
        ok = lag is not None and abs(lag) <= tsib.EOT_ZERO_MIN
        p2 = {"against": "eot20", "lag": None if lag is None else round(lag, 1), "ok": ok}
    served = [served_kind(src, sv, wk.lineages, origin_status) for sv in wk.served.get(src, ())]
    res = assess(rec, table, span, eot=wk.inputs.eot(src), served=served, zone=wk.inputs.zone(src), n_decl=n_new,
                 missing_inputs=not wk.inputs.has(src), independent_served_only=independent_served_only)
    return rid, [], [], {"pass2": p2, "weak": res, "span": span}


def origin_status(row: "AuditRow | None", n_decl: int, pending_inherit: bool = False) -> str:
    """A served station's origin record, after the strong audit: "verified" only when a strong comparator
    (official, harcon, sibling, provider) confirmed its declared time base; else what it is."""
    if row is None:
        return "not audited"
    if row.status == "residue":
        return "disputed"               # also an inherited record its own weak checks disputed
    if row.comparator_kind == "w":
        return "weak only"
    if row.comparator_kind == "inherit" and pending_inherit:
        return "inherited, own weak checks pending"
    if row.status == "used" and row.correction.usable and row.comparator_kind in ("b", "c", "a", "inherit"):
        return ORIGIN_VERIFIED if row.correction.label == declared_correction(n_decl).label else "corrected"
    return {"residue": "disputed", "unverified": "unverified"}.get(row.status, row.status)


class _Serial:
    """In-process executor (workers=1, and the tests)."""

    def __init__(self, *args):
        _init(*args)

    def map(self, fn, *its):
        return map(fn, *its)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --- the audit -----------------------------------------------------------------------------------

@dataclass
class AuditResult:
    rows: dict[str, AuditRow]
    providers: list[dict]
    residue: list[ResidueCase]
    rounds: int                  # sibling rounds run after round 0
    weak_failures: list = field(default_factory=list)   # (record, failure note) of weak tasks that raised
    sibling_crashes: list = field(default_factory=list) # records whose sibling confirmation crashed in our code
    sibling_counts: dict = field(default_factory=dict)  # the sibling step's counts (logged, in the manifest)


def _log(msg: str) -> None:
    """The command line's log: flushed, so progress lines show while a round runs."""
    print(msg, flush=True)


def _progress(results: Iterable, total: int, label: str, log, every: int = 50, secs: float = 60.0):
    """Pass `results` through unchanged, logging progress every `every` records or `secs` seconds
    (and at the end): "round 0: 812/4123 records done (19.7%), elapsed 14m, est 46m left". Logging only."""
    t0 = last = time.monotonic()
    i = 0
    for i, x in enumerate(results, 1):
        yield x
        now = time.monotonic()
        if i % every == 0 or now - last >= secs or i == total:
            el = now - t0
            est = el / i * (total - i)
            log(f"{label}: {i}/{total} records done ({100.0 * i / max(total, 1):.1f}%), "
                f"elapsed {el / 60:.0f}m, est {est / 60:.0f}m left")
            last = now


def _failed_row(info: RecordInfo, cand: bool, why: Sequence[str], rnd: int | None, tried: list[str]) -> AuditRow:
    """A record a measurement task of which raised: residue, never used (its verdict is not known)."""
    return AuditRow(info.file_id, cand, info.contributor, info.interval_s, "residue", "unexplained", "unexplained",
                    NOT_USABLE, rnd, "none", "", tried=tried, reason="audit task failed: " + "; ".join(why),
                    task_failed=True)


def audit(inv: Mapping[str, RecordInfo], candidates: Sequence[str], *, release_root: str | Path,
          table_path: str | Path, gates_dir: str | Path, workers: int = 1,
          transforms: Mapping[str, tuple[str, int]] | None = None,
          previous_providers: Mapping[tuple[str, int], str] | None = None, version: str = "4.1",
          weak: WeakConfig | None = None, declared: Mapping[str, int] | None = None,
          sibling: SiblingConfig | None = None, out_of_pool: bool = False,
          provider_class: Sequence[str] = (), agency_refs: "Sequence[tag.AgencyRef] | None" = None,
          withhold: Sequence[dict] = (), log=None) -> AuditResult:
    """Audit the candidates and their siblings in the plan's order (module docstring).
    declared: GESLA's TIME ZONE HOURS by record (missing = 0, UTC: GESLA's declaration for all but
    one 4.1 record); weak: the weak evidence's inputs (None: records without a comparator verdict
    stay unverified). Every record of the pool gets a row and an annotation: none is dropped.
    out_of_pool (with weak): every other inventory record gets a row too (no comparator; the weak checks, EOT20
    first; never inherits: the provider rule is the pool's); sibling: the intra-site constants comparison and the
    provider-family rule on pass 1 (timebase_sibling; apply_sibling). provider_class: the contributors of the owner
    switch D-O3 (provider_class_inherit; main passes PROVIDER_CLASS_INHERIT). With sibling, the agency constants
    (timebase_agency; apply_agency) follow the sibling step; agency_refs None: A-NOAA's harcon cache.
    withhold: the reviewed withholds (load_withhold), applied last (apply_reviewed_withhold)."""
    log = log or (lambda *_: None)
    transforms = dict(transforms or {})
    inv = dict(inv)
    check_withhold(withhold, inv)        # an unknown id stops the audit before any work
    lost = sorted({src for rid, (src, _) in transforms.items() if rid not in inv and src not in inv})
    if lost:
        raise ValueError(f"transform source(s) not in the inventory: {', '.join(lost)}")
    for rid, (src, shift) in transforms.items():       # synthetic records (proofs): the source's metadata
        if rid not in inv:
            i = inv[src]
            inv[rid] = replace(i, file_id=rid, start_s=i.start_s + int(shift), end_s=i.end_s + int(shift))
    cands = sorted(set(candidates))
    lost = [c for c in cands if c not in inv]
    if lost:
        raise ValueError(f"{len(lost)} candidate id(s) not in the inventory: {', '.join(lost[:20])}")
    sib_all = siblings(inv, cands)
    pool = sorted(set(cands) | {s for c in cands for s in sib_all[c]})
    pool_set = set(pool)
    sib = {f: [s for s in v if s in pool_set] for f, v in siblings(inv, pool).items()}
    cand_set = set(cands)
    args = (str(release_root), str(table_path), str(gates_dir), transforms, weak)
    ex = _Serial(*args) if workers <= 1 else ProcessPoolExecutor(workers, initializer=_init, initargs=args)
    refs = References(gates_dir)
    log("references: " + ", ".join(f"{k} {v}" for k, v in refs.counts().items()))
    has_official = [f for f in pool
                    if refs.harcon(transforms.get(f, (f,))[0])
                    or refs.official(inv[f].lat, inv[f].lon, transforms.get(f, (f,))[0])]
    rows: dict[str, AuditRow] = {}
    outs: dict[str, list[Outcome]] = defaultdict(list)
    notes: dict[str, list[str]] = defaultdict(list)
    failed: dict[str, list[str]] = defaultdict(list)
    tried: dict[str, set[str]] = defaultdict(set)
    official_ev: dict[str, dict] = {}
    official_cls: dict[str, str] = {}

    def take(rid: str, o: list[Outcome], n: list[str], extra: dict) -> None:
        outs[rid] += o
        notes[rid] += n
        if extra.get("failed"):
            failed[rid] += n
        for x in o:
            tried[rid].add(x.comparator_id)
        tried[rid].update(extra.get("tried", ()))

    def settle(rid: str, rnd: int | None, final: bool) -> bool:
        """(Re)decide rid from its outcomes; True when it has a row now.

        A record with a crashed task is never used: the lost comparison (always a sibling's; an
        official crash leaves no outcome and no later task) could have failed the pass rule. It is
        `unexplained` by evidence only when no passing sibling could rescue it: no conclusive
        official outcome (a same-class sibling pass would refine it) and conclusive passing siblings
        of more than one base (decide rule 2: another pass never removes a base). Otherwise its
        verdict is unknown: a failed row (task_failed), whatever else it recorded, since a failed
        sibling yields to a passing one. Either way the row is never passing or usable."""
        if rid in failed:
            o = outs[rid]
            bases = {x.verdict.base for x in o if x.kind == "a" and x.conclusive and x.verdict.passed}
            got, why = None, ""
            if len(bases) > 1 and not any(x.kind in ("b", "c") and x.conclusive for x in o):
                got, why = decide(o, final=True)
            if got is not None and not got.verdict.passed:      # a crash never yields a used row
                rows[rid] = _row_from(inv[rid], rid in cand_set, got, rnd, sorted(tried[rid]), why)
                rows[rid].reason = f"{rows[rid].reason}; audit task failed: " + "; ".join(failed[rid])
            else:
                rows[rid] = _failed_row(inv[rid], rid in cand_set, failed[rid], rnd, sorted(tried[rid]))
            return True
        got, why = decide(outs[rid], final=final)
        if got is None:
            return False
        rows[rid] = _row_from(inv[rid], rid in cand_set, got, rnd, sorted(tried[rid]), why)
        return True

    def usable_now() -> set[str]:
        return {f for f, r in rows.items() if r.usable}

    def withdraw(before: set[str]) -> None:
        """A usable record whose verdict a withdrawn sibling supported (a passing outcome against its
        corrected series) is re-judged without that support, to a fixpoint. Only support is
        withdrawn: a record that failed against a sibling stays as it is (mutual failure is a
        disagreement for the decision model, not a withdrawn comparator), so this never turns a
        residue record into a used one."""
        gone = before - usable_now()
        while gone:
            drop = {f"{s}~corrected" for s in gone}
            was = usable_now()
            for f in sorted(outs):
                if f not in was or f in failed:
                    continue
                hit = sorted({o.comparator_id for o in outs[f] if o.verdict.passed} & drop)
                if not hit:
                    continue
                outs[f] = [o for o in outs[f] if not (o.verdict.passed and o.comparator_id in drop)]
                rnd = rows[f].round if f in rows else None
                rows.pop(f, None)
                if settle(f, rnd, True):
                    rows[f].reason = (f"re-judged without {', '.join(hit)} (verdict withdrawn); "
                                      + rows[f].reason)[:REASON_MAX * 2]
                log(f"re-judged {f} without {', '.join(hit)}: {rows[f].verdict if f in rows else 'no verdict'}")
            gone = was - usable_now()

    rounds = 0
    weak_failures: list[tuple[str, str]] = []
    sib_crashes: list[str] = []
    sib_counts: dict = {}
    with ex:
        log(f"round 0: {len(has_official)} of {len(pool)} records have an official comparator")
        res = _progress(ex.map(_official_task, has_official, [inv[f].lat for f in has_official],
                               [inv[f].lon for f in has_official]), len(has_official), "round 0", log)
        for rid, o, n, extra in res:
            take(rid, o, n, extra)
            for x in o:
                official_ev[rid] = x.comparator_evidence
                if x.conclusive:
                    official_cls[rid] = x.verdict.verdict
            settle(rid, 0, False)
        # Round 0, month by month: an officially verdicted record against its officially verdicted
        # siblings (each anchored to the reference on its own, so not circular). Same-class siblings
        # refine the verdict (bad months, span); a failed pass rule makes it unexplained.
        anchored = dict(rows)
        tasks = [(f, [(s, anchored[s]) for s in sib.get(f, []) if s in anchored and anchored[s].usable])
                 for f in sorted(anchored) if anchored[f].usable]
        tasks = [t for t in tasks if t[1]]
        log(f"round 0 cross-check: {len(tasks)} officially verdicted records with officially verdicted siblings")
        before = usable_now()
        for rid, o, n, extra in _progress(ex.map(_sibling_task, [t[0] for t in tasks], [t[1] for t in tasks]),
                                          len(tasks), "round 0 cross-check", log):
            take(rid, o, n, extra)
            settle(rid, 0, True)
        for t in tasks:
            tried[t[0]].update(f"{s}~corrected" for s, _ in t[1])
        withdraw(before)
        while True:
            tasks = []
            for f in pool:
                if f in rows:
                    continue
                vs = [(s, rows[s]) for s in sib.get(f, []) if s in rows and rows[s].usable
                      and f"{s}~corrected" not in tried[f]]
                if vs:
                    tasks.append((f, vs))
            if not tasks:
                break
            rounds += 1
            log(f"round {rounds}: {len(tasks)} records with a newly verdicted sibling")
            added = 0
            for rid, o, n, extra in _progress(ex.map(_sibling_task, [t[0] for t in tasks], [t[1] for t in tasks]),
                                              len(tasks), f"round {rounds}", log):
                take(rid, o, n, extra)
                added += settle(rid, rounds, False)
            for t in tasks:
                tried[t[0]].update(f"{s}~corrected" for s, _ in t[1])
            if added == 0:
                break
        # Final cross-check: every verdicted record against the verdicted siblings it has not met yet
        # (siblings verdicted in later rounds), so the month votes see every comparator.
        tasks = [(f, [(s, rows[s]) for s in sib.get(f, []) if s in rows and rows[s].usable
                      and f"{s}~corrected" not in tried[f]])
                 for f in sorted(rows) if rows[f].comparator_kind in ("a", "b", "c")]
        tasks = [t for t in tasks if t[1]]
        log(f"final cross-check: {len(tasks)} verdicted records with siblings verdicted later")
        before = usable_now()
        for rid, o, n, extra in _progress(ex.map(_sibling_task, [t[0] for t in tasks], [t[1] for t in tasks]),
                                          len(tasks), "final cross-check", log):
            take(rid, o, n, extra)
            settle(rid, rows[rid].round, True)
        for t in tasks:
            tried[t[0]].update(f"{s}~corrected" for s, _ in t[1])
        withdraw(before)
        for f in pool:
            if f not in rows:
                settle(f, None, True)
        providers = inherit(rows, inv, pool, cand_set, version=version)
        extra_ids: list[str] = []
        if weak is not None and out_of_pool:
            # TB-SIB: a record outside the pool used to keep its declared base unchecked (fit_run "not in the audit
            # pool"); it gets a row, no comparator and no inheritance, and the weak checks below
            extra_ids = [f for f in sorted(inv) if f not in pool_set]
            for f in extra_ids:
                info = inv[f]
                rows[f] = AuditRow(f, False, info.contributor, info.interval_s, "unverified", "no_comparator",
                                   "no_comparator", NOT_USABLE, None, "none", "", in_pool=False,
                                   reason="not in the audit pool (no served station, no sibling of one)")
            log(f"out of the pool: {len(extra_ids)} records get a row and the weak checks")
        allf = list(pool) + extra_ids
        dec = {f: int((declared or {}).get(transforms.get(f, (f,))[0], 0)) for f in allf}
        pci = provider_class_inherit(rows, inv, allf, provider_class, sibling.fits if sibling else None,
                                     weak_speeds(TcdTable.load(table_path)) if sibling else None, dec)
        if provider_class:
            log(f"{PROVIDER_CLASS_ID} ({', '.join(provider_class)}): {len(pci['inherited'])} records inherit their "
                f"provider's sibling-verified class, {len(pci['contradicted'])} kept (own constants disagree); groups "
                + "; ".join(f"{k} {v}" for k, v in pci["groups"].items()))
            for f, why in pci["contradicted"]:
                log(f"  {PROVIDER_CLASS_ID} not inherited: {f}: {why}")
            sib_counts["provider_class"] = {"inherited": len(pci["inherited"]), "contradicted": len(pci["contradicted"])}
        if weak is not None:
            # records without a comparator verdict, and inherited ones: a provider's verdict yields to the
            # record's own evidence when that disagrees (yield_inherited)
            # An inherited record is tested against its INHERITED class (the hours it is published with), not
            # the declared one; a class the weak checks cannot test (mean60, local_dst) is not tested at all.
            n_test = dict(dec)
            for f in allf:
                if rows[f].comparator_kind == "inherit":
                    h = inherited_hours(rows[f].correction)
                    if h is None:
                        rows[f].reason = (f"{rows[f].reason}; own weak checks not run: the weak checks cannot test the "
                                          f"inherited class {rows[f].verdict}, so the inheritance stands")[:REASON_MAX * 2]
                    else:
                        n_test[f] = h
            todo = [f for f in allf if rows[f].verdict == "no_comparator"
                    or (rows[f].comparator_kind == "inherit" and inherited_hours(rows[f].correction) is not None)]
            n_inh = sum(1 for f in todo if rows[f].comparator_kind == "inherit")
            log(f"weak evidence: {len(todo)} records without a comparator verdict or inherited ({n_inh} inherited)")
            # Two phases. The origins' status comes from the strong audit, but an inherited verdict is final only
            # after its record's own weak checks: phase 1 tests the inherited records (an inherited origin is
            # pending, never "verified"); phase 2 the records without a comparator verdict, with the origins'
            # status taken after phase 1 (an inherited origin its own evidence disputed is "disputed").
            def ostat_of(f, pending=False):
                src = transforms.get(f, (f,))[0]
                return {sv.origin: origin_status(rows.get(sv.origin), int((declared or {}).get(sv.origin, 0)),
                                                 pending_inherit=pending)
                        for sv in weak.served.get(src, ()) if sv.origin}

            for phase, part in (("inherited", [f for f in todo if rows[f].comparator_kind == "inherit"]),
                                ("no comparator", [f for f in todo if rows[f].comparator_kind != "inherit"])):
                ostat = [ostat_of(f, phase == "inherited") for f in part]
                # an inherited class other than the declared one: only EOT20 and independent served stations
                indep = [phase == "inherited" and n_test[f] != dec[f] for f in part]
                got: list[str] = []
                for rid, _o, n, extra in _progress(ex.map(_weak_task, part, [n_test[f] for f in part], ostat, indep),
                                                   len(part), f"weak evidence ({phase})", log):
                    got.append(rid)
                    notes[rid] += n
                    if extra.get("failed") or extra.get("weak") is None:     # raised, or no result (no good data)
                        weak_failures.append((rid, n[0] if n else "?"))
                    crashed = bool(extra.get("failed")) and extra.get("error_type") not in EXPECTED_WEAK_ERRORS
                    if phase == "inherited":
                        yield_inherited(rows[rid], extra.get("weak"), extra.get("span"), n, crashed=crashed)
                    else:
                        apply_weak(rows[rid], extra.get("weak"), extra.get("span"), n, crashed=crashed)
                if sorted(got) != sorted(part):         # one result per record sent, no stranger, none twice
                    lost, extra_ = sorted(set(part) - set(got)), sorted(set(got) - set(part))
                    raise RuntimeError(f"weak evidence ({phase}): results for {len(got)} of {len(part)} records "
                                       f"(missing {lost[:5]}, unexpected or repeated {extra_[:5] or 'duplicates'})")
            if sibling is not None:
                sib_crashes, sc = apply_sibling(rows, inv, sibling, weak, dec, transforms,
                                                        weak_speeds(TcdTable.load(table_path)), ex, ostat_of,
                                                        declared or {}, log)
                sib_counts.update(sc)
                if agency_refs is None:
                    agency_refs, skipped = tag.load_noaa_refs(refs.noaa_harcon_dir.parent)
                    for s in skipped[:10]:
                        log(f"agency constants: skipped {s}")
                sp_ = weak_speeds(TcdTable.load(table_path))
                bound = bound_fits(rows, inv, sibling, dec)
                listed = {e["file_id"] for e in withhold}        # FIX-LST-5 L3: never a reference for another record
                lags = tag.open_lags(agency_refs, agency_sites(bound, rows, inv, weak, transforms), sp_, exclude=listed)
                log("agency constants: open-water lag of the references: " + ", ".join(
                    f"{k} {v}" for k, v in sorted(Counter(
                        "unknown, confirmed" if x["lag"] is None and x["confirmed"] else "unknown" if x["lag"] is None
                        else "lags > 60 min" if x["lag"] > 60 else "open or near it" for x in lags.values()).items())))
                ag_crashes, ac = apply_agency(rows, inv, sibling, weak, dec, transforms, sp_, ex, ostat_of,
                                              declared or {}, tag.RefIndex(agency_refs), log, lags=lags)
                sib_crashes = sib_crashes + ag_crashes
                sib_counts["agency"] = ac
                sib_counts["two_source"] = apply_two_source(rows, inv, sibling, weak, dec, transforms, sp_,
                                                            tag.RefIndex(agency_refs), log, lags=lags, exclude=listed)
                sib_counts["regional"] = apply_regional(rows, inv, sibling, weak, dec, transforms, sp_, log,
                                                        exclude=listed)
    if withhold:
        sib_counts["reviewed_withhold"] = apply_reviewed_withhold(rows, withhold, inv, log)
    for f in allf:
        annotate(rows[f], dec[f])
    final_counts(providers, rows, inv)
    for f in allf:
        rows[f].official = rows[f].official or official_ev.get(f, {})
        rows[f].official_verdict = official_cls.get(f, "")
        if rows[f].comparator_kind in ("none", "inherit") and tried[f]:
            # a comparator was tried but gave no verdict; keep the weak checks already listed
            rows[f].tried = sorted(set(rows[f].tried) | tried[f])
        if not rows[f].reason and notes[f]:
            rows[f].reason = "; ".join(notes[f])
        elif rows[f].status == "unverified" and notes[f]:
            rows[f].reason = f"{'; '.join(notes[f])}; {rows[f].reason}"
    residue = residue_cases(rows, providers, previous_providers or {}, version=version)
    return AuditResult(rows, providers, residue, rounds, weak_failures, sib_crashes, sib_counts)


def correction_of(label: str) -> Correction:
    """The Correction of a whole-record class label (utc_instant, utc_plus_<n>/utc_minus_<n>, local_dst:<zone>)."""
    if label == "utc_instant":
        return Correction("utc_instant")
    if label.startswith("local_dst:"):
        return Correction("local_dst", zone=label.split(":", 1)[1])
    for pre, sign in (("utc_plus_", 1), ("utc_minus_", -1)):
        if label.startswith(pre):
            return Correction("utc_plus_n", n=sign * int(label[len(pre):]))
    raise ValueError(f"no whole-record correction for {label!r}")


def declared_correction(n_hours: int) -> Correction:
    """GESLA's declared time base: TIME ZONE HOURS n -> the stamps are UTC + n h."""
    return Correction("utc_instant") if n_hours == 0 else Correction("utc_plus_n", n=int(n_hours))


# A weak task that raises FitError hit a limit of the data (timebase_weak turns the trial fit's ValueError and
# LinAlgError into FitError at the fit call site); any other exception, a bare ValueError included, is a crash
# of our code (weak_crashed), never taken for a data limit.
EXPECTED_WEAK_ERRORS = frozenset({"FitError"})
WEAK_FAILED, WEAK_CRASHED = "unverified:weak_failed", "unverified:weak_crashed"


def apply_weak(row: AuditRow, res, span: tuple[int, int] | None, notes: Sequence[str] = (),
               crashed: bool = False) -> None:
    """The weak rule's result on a record without a comparator verdict (timebase_weak.combine):
    verified / corrected -> used (comparator kind "w"); disputed -> residue (D5); else unverified.
    res None: the task failed (weak_failed) or, with crashed, our code crashed (weak_crashed)."""
    if res is None:
        row.weak = {"code": WEAK_CRASHED if crashed else WEAK_FAILED, "notes": list(notes)}
        what = "crashed (weak_crashed)" if crashed else "failed"
        row.reason = (f"weak evidence {what}: " + "; ".join(notes))[:REASON_MAX * 2] if notes else row.reason
        return
    row.weak = {"code": res.code, "evidence": _num(res.evidence)}
    row.tried = list(row.tried) + ["weak:eot20", "weak:served", "weak:dst_selftest"]
    if res.annotation in ("verified", "corrected"):
        row.status, row.verdict, row.base = "used", res.label, res.label
        row.correction, row.comparator_kind, row.comparator_id = correction_of(res.label), "w", res.code
        row.span, row.reason = span, res.reason
    elif res.annotation == "disputed":
        row.status, row.verdict, row.base = "residue", "unexplained", "unexplained"
        row.correction, row.comparator_kind, row.comparator_id = NOT_USABLE, "w", res.code
        row.span, row.reason = span, res.reason
    else:
        row.reason = f"{row.reason}; {res.reason}"[:REASON_MAX * 2]


SIB_DISPUTED, FAM_DISPUTED = "disputed:sibling_constants_offset", "disputed:eot20_family_offset"
WHOLE_HOUR_DISPUTED = "disputed:eot20_whole_hour"


def _published_now(row: AuditRow, n_decl: int) -> Correction:
    """The correction a row publishes before the sibling step: its own when used, else the declared one."""
    return row.correction if row.status == "used" and row.correction.usable else declared_correction(n_decl)


def _sib_dispute(row: AuditRow, code: str, why: str) -> None:
    """Offset evidence on a record (TB-SIB): disputed (residue, D5); a record already disputed keeps its own code."""
    if row.status == "residue":
        row.reason = f"{row.reason}; {why}"[:REASON_MAX * 2]
        return
    prev = f" (was {row.verdict} by {row.comparator_kind or 'none'})" if row.status == "used" else ""
    row.status, row.verdict, row.base = "residue", "unexplained", "unexplained"
    row.correction, row.comparator_kind, row.comparator_id = NOT_USABLE, "w", code
    row.months_bad = []
    row.reason = f"{why}{prev}"[:REASON_MAX * 2]


def apply_sibling(rows: dict[str, AuditRow], inv: Mapping[str, RecordInfo], cfg: SiblingConfig, weak: WeakConfig,
                  dec: Mapping[str, int], transforms: Mapping[str, tuple[str, int]], sp: Mapping[str, float], ex,
                  ostat_of, declared: Mapping[str, int], log) -> tuple[list[str], dict]:
    """The intra-site constants comparison and the provider-family rule (timebase_sibling) on pass 1, into the verdicts.

    Pass 1 binds: a record's fitted constants are used only when the fit's time base is the one the record
    publishes now (the audit's correction when used, else declared); others are left out (counted).
    A strongly verified record (official, harcon, sibling series) is an anchor and never flagged. Every other
    record a finding names becomes disputed (SIB_DISPUTED / FAM_DISPUTED), unless the finding is unambiguous and
    the record publishes a whole-hour class: then the corrected hours (published + the shift) go to pass 2
    (_sibling_confirm_task), and the record is corrected only when pass 2 confirms them and its own weak checks at
    those hours do not disagree (yield_inherited's rule, independent_served_only when they are not the declared
    base). sp: the constituent speeds (deg/h). Returns (records whose pass 2 crashed in our code, counts)."""
    recs: dict[str, tsib.SiteRecord] = {}
    unbound = []
    for f, r in sorted(cfg.fits.items()):
        if r.get("outcome") != "fitted" or not r.get("main") or f not in inv:
            continue
        row = rows.get(f)
        n = dec.get(f, int(declared.get(transforms.get(f, (f,))[0], 0)))
        want = (_published_now(row, n) if row else declared_correction(n)).label
        if r.get("time_base") != want:
            unbound.append(f"{f} (fitted {r.get('time_base')}, publishes {want})")
            continue
        strong = bool(row and row.status == "used" and row.comparator_kind in ("b", "c", "a"))
        src = transforms.get(f, (f,))[0]
        recs[f] = tsib.SiteRecord(f, float(r["lat"]), float(r["lon"]), r.get("gauge_type") or "",
                                  {k: (float(v[0]), float(v[1])) for k, v in r["main"].items()},
                                  weak.inputs.eot(src), strong, frozenset(cfg.stations.get(src, ())))
    site = tsib.find_offsets(recs, sp)
    fam = tsib.family_offsets(recs, sp, site.findings)
    whole = tsib.eot_whole_hours(recs, sp, skip=set(site.findings) | set(fam))
    log(f"sibling constants: {len(recs)} fitted records on their published base ({len(unbound)} fitted on another: "
        f"{'; '.join(unbound[:5])}), {len(site.pairs)} clock-like pairs, {len(site.findings)} offset by the site "
        f"({sum(x.unambiguous for x in site.findings.values())} unambiguous), {len(fam)} by the provider family, "
        f"{len(site.unresolved)} site disagreements with no side, {len(site.conflicts)} refused")
    for line in site.conflicts:
        log(f"  sibling constants refused: {line}")
    for f, (lag, how, n) in sorted(whole.items()):
        if f not in rows:
            continue
        row = rows[f]
        why = (f"EOT20 lag {lag:+.1f} min ({how}) is {n:+d} h, a whole number of hours beyond what a harbour does to the "
               f"tide; disputed on EOT20 alone, never corrected (owner rule)")
        row.sibling = {"rule": "eot20_whole_hour", "summary": why, "lag_min": round(lag, 1), "hours": n, "how": how}
        _sib_dispute(row, WHOLE_HOUR_DISPUTED, why)
    cands = []
    for f, x in sorted({**{k: v for k, v in fam.items()}, **site.findings}.items()):
        if f not in rows:
            continue
        row = rows[f]
        fam_x = isinstance(x, tsib.FamilyFinding)
        row.sibling = ({"rule": "eot20_family", "summary": x.summary(), "lag_min": x.lag_min, "hours": x.hours,
                        "family": list(x.family), "agree": list(x.agree), "confirmed": list(x.confirmed),
                        "on_utc": list(x.on_utc), "unambiguous": x.unambiguous}
                       if fam_x else
                       {"rule": "site", "summary": x.summary(), "shift_min": x.shift_min, "group": list(x.group),
                        "references": list(x.references), "resolved_by": x.resolved_by, "eot_group": x.eot_group,
                        "eot_reference": x.eot_reference, "whole_hours": x.whole_hours, "unambiguous": x.unambiguous,
                        "pairs": [list(p) for p in x.pairs]})
        code = FAM_DISPUTED if fam_x else SIB_DISPUTED
        hours = x.hours if fam_x else x.whole_hours
        n_pub = inherited_hours(_published_now(row, dec[f]))
        if not x.unambiguous or hours is None or n_pub is None or row.status == "residue":
            why = x.summary() if n_pub is not None else f"{x.summary()}; its class cannot take a whole-hour shift"
            _sib_dispute(row, code, why)
            continue
        refs = [] if fam_x else [dict(recs[g].consts) for g in x.references if g in recs]
        cands.append((f, n_pub + hours, refs, code))
    crashes: list[str] = []
    n_corr = 0
    if cands:
        log(f"sibling constants: pass 2 on {len(cands)} candidate corrections")
        ost = [ostat_of(f) for f, *_ in cands]
        indep = [n != dec[f] for f, n, *_ in cands]
        got = {rid: (n, extra) for rid, _o, n, extra in ex.map(_sibling_confirm_task, [c[0] for c in cands],
                                                               [c[1] for c in cands], [c[2] for c in cands], ost, indep)}
        for f, n_new, _refs, code in cands:
            row = rows[f]
            notes, extra = got[f]
            label = declared_correction(n_new).label
            decl = declared_correction(dec[f]).label
            summ = row.sibling["summary"]
            if extra.get("failed"):
                if extra.get("error_type") not in EXPECTED_WEAK_ERRORS:
                    crashes.append(f)
                _sib_dispute(row, code, f"{summ}; pass 2 at {label} failed: {'; '.join(notes)}")
                continue
            p2, res = extra["pass2"], extra["weak"]
            row.sibling.update(pass2=p2, pass2_weak=res.code)
            conflict = res.annotation == "disputed" or (res.annotation in ("verified", "corrected")
                                                        and res.label != label)
            if not p2["ok"] or conflict:
                why = (f"pass 2 at {label} does not confirm it ({p2})" if not p2["ok"]
                       else f"its own weak checks at {label} disagree ({res.code}): {res.reason}")
                _sib_dispute(row, code, f"{summ}; {why}")
                continue
            ev = "eot20_family+sibling_constants" if code == FAM_DISPUTED else "sibling_constants+eot20"
            row.status, row.verdict, row.base = "used", label, label
            row.correction, row.comparator_kind = correction_of(label), "w"
            row.comparator_id = f"corrected:{decl}->{label}:{ev}"
            row.months_bad, row.span = [], extra.get("span") or row.span
            row.reason = f"{summ}; pass 2 at {label} confirms it ({p2['against']})"[:REASON_MAX * 2]
            n_corr += 1
    counts = {"fitted_bound": len(recs), "fitted_unbound": len(unbound), "clock_like_pairs": len(site.pairs),
              "site_findings": len(site.findings), "family_findings": len(fam), "pass2_candidates": len(cands),
              "corrected": n_corr, "unresolved": len(site.unresolved), "refused": len(site.conflicts),
              "eot20_whole_hour": sum(1 for f in whole if f in rows)}
    log("sibling constants: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return crashes, counts


AGENCY_DISPUTED = "disputed:agency_constants_offset"


def apply_agency(rows: dict[str, AuditRow], inv: Mapping[str, RecordInfo], cfg: SiblingConfig, weak: WeakConfig,
                 dec: Mapping[str, int], transforms: Mapping[str, tuple[str, int]], sp: Mapping[str, float], ex,
                 ostat_of, declared: Mapping[str, int], index: "tag.RefIndex", log,
                 lags: Mapping[str, Mapping] | None = None) -> tuple[list[str], dict]:
    """The agency constants (timebase_agency) on pass 1, into the verdicts, after apply_sibling. lags: the references'
    open-water lags (timebase_agency.open_lags; None: every reference taken as open water).

    Pass 1 binds as in apply_sibling: a record's constants are used only when its fit's time base is the one it
    publishes now. A record a strong comparator verified (official, harcon, sibling series) is not judged. A lead or
    an agreed offset makes the record disputed (AGENCY_DISPUTED; a record already disputed keeps its code), unless it is
    a correction candidate (timebase_agency.classify: every reference and EOT20 on one whole hour, a reference in the
    same water): then pass 2 (_sibling_confirm_task against that reference) and the record's own weak checks decide,
    as in apply_sibling. Each record is judged on its own evidence. Returns (records whose pass 2 crashed, counts)."""
    counts = {"references": len(index), "bound": 0, "with_reference": 0, "lead": 0, "agreed": 0,
              "pass2_candidates": 0, "corrected": 0, "disputed": 0, "already_disputed": 0}
    if not len(index):
        log("agency constants: no agency station loaded, nothing judged")
        return [], counts
    cands = []
    for f, r in sorted(cfg.fits.items()):
        row = rows.get(f)
        if row is None or r.get("outcome") != "fitted" or not r.get("main") or f not in inv:
            continue
        if row.status == "used" and row.comparator_kind in ("b", "c", "a"):
            continue
        if r.get("time_base") != _published_now(row, dec[f]).label:
            continue
        counts["bound"] += 1
        consts = {k: (float(v[0]), float(v[1])) for k, v in r["main"].items()}
        sh = tag.shift(consts, index.near(inv[f].lat, inv[f].lon), sp, lags)
        if sh is None:
            continue
        counts["with_reference"] += 1
        src = transforms.get(f, (f,))[0]
        el = tsib.eot_lag_alone(tsib.SiteRecord(f, inv[f].lat, inv[f].lon, r.get("gauge_type") or "", consts,
                                                weak.inputs.eot(src)), sp)
        e = None if el is None else el[0]
        cls = tag.classify(sh, e)
        if cls["kind"] == "none":
            continue
        counts[cls["kind"]] += 1
        row.agency = {"rule": cls["kind"], "summary": cls["why"], "tau_min": round(sh["tau"], 1),
                      "eot20_min": None if e is None else round(e, 1), "hours": cls["hours"],
                      "references": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in q.items()}
                                     for q in sh["refs"]]}
        n_pub = inherited_hours(_published_now(row, dec[f]))
        if row.status == "residue":
            counts["already_disputed"] += 1
            _sib_dispute(row, AGENCY_DISPUTED, cls["why"])
            continue
        if cls["hours"] is None or n_pub is None:
            counts["disputed"] += 1
            _sib_dispute(row, AGENCY_DISPUTED, cls["why"] + ("" if n_pub is not None else
                                                             "; its class cannot take a whole-hour shift"))
            continue
        ref = next(x for x in index.refs if f"{x.src}:{x.id}" == cls["same_ref"])
        cands.append((f, n_pub + cls["hours"], [dict(ref.consts)]))
    crashes: list[str] = []
    if cands:
        counts["pass2_candidates"] = len(cands)
        log(f"agency constants: pass 2 on {len(cands)} candidate corrections")
        ost = [ostat_of(f) for f, *_ in cands]
        indep = [n != dec[f] for f, n, _ in cands]
        got = {rid: (n, extra) for rid, _o, n, extra in ex.map(_sibling_confirm_task, [c[0] for c in cands],
                                                               [c[1] for c in cands], [c[2] for c in cands], ost, indep)}
        for f, n_new, _refs in cands:
            row = rows[f]
            notes, extra = got[f]
            label = declared_correction(n_new).label
            decl = declared_correction(dec[f]).label
            summ = row.agency["summary"]
            if extra.get("failed"):
                if extra.get("error_type") not in EXPECTED_WEAK_ERRORS:
                    crashes.append(f)
                counts["disputed"] += 1
                _sib_dispute(row, AGENCY_DISPUTED, f"{summ}; pass 2 at {label} failed: {'; '.join(notes)}")
                continue
            p2, res = extra["pass2"], extra["weak"]
            row.agency.update(pass2=p2, pass2_weak=res.code)
            conflict = res.annotation == "disputed" or (res.annotation in ("verified", "corrected")
                                                        and res.label != label)
            if not p2["ok"] or conflict:
                why = (f"pass 2 at {label} does not confirm it ({p2})" if not p2["ok"]
                       else f"its own weak checks at {label} disagree ({res.code}): {res.reason}")
                counts["disputed"] += 1
                _sib_dispute(row, AGENCY_DISPUTED, f"{summ}; {why}")
                continue
            row.status, row.verdict, row.base = "used", label, label
            row.correction, row.comparator_kind = correction_of(label), "w"
            row.comparator_id = f"corrected:{decl}->{label}:agency_constants+eot20"
            row.months_bad, row.span = [], extra.get("span") or row.span
            row.reason = f"{summ}; pass 2 at {label} confirms it ({p2['against']})"[:REASON_MAX * 2]
            counts["corrected"] += 1
    log("agency constants: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return crashes, counts


TWO_SOURCE_DISPUTED = "disputed:two_source_offset"


def bound_fits(rows: Mapping[str, AuditRow], inv: Mapping[str, RecordInfo], cfg: SiblingConfig,
               dec: Mapping[str, int]) -> dict[str, dict]:
    """Every fitted record of the inventory whose pass-1 fit is on the base it publishes now (a record without a row
    publishes its declared base): file id -> constants."""
    bound: dict[str, dict] = {}
    for f, r in cfg.fits.items():
        if r.get("outcome") != "fitted" or not r.get("main") or f not in inv:
            continue
        pub = _published_now(rows[f], dec[f]) if f in rows else declared_correction(int(dec.get(f, 0)))
        if r.get("time_base") == pub.label:
            bound[f] = {k: (float(v[0]), float(v[1])) for k, v in r["main"].items()}
    return bound


def agency_sites(bound: Mapping[str, dict], rows: Mapping[str, AuditRow], inv: Mapping[str, RecordInfo],
                 weak: WeakConfig, transforms: Mapping[str, tuple[str, int]]) -> list[dict]:
    """The bound records as timebase_agency.open_lags sites (EOT20 at their cell; disputed: residue now)."""
    return [{"id": f, "lat": inv[f].lat, "lon": inv[f].lon, "consts": c, "eot": weak.inputs.eot(transforms.get(f, (f,))[0]),
             "disputed": f in rows and rows[f].status == "residue"} for f, c in sorted(bound.items())]


class _Near:
    """A true-radius search over records (FIX-LST-3 L1: the 1/30-degree grid missed east-west neighbours above ~36 deg)."""

    def __init__(self, ids: Sequence[str], inv: Mapping[str, RecordInfo]):
        self.ids = list(ids)
        self.lat = np.array([inv[f].lat for f in self.ids]) if self.ids else np.zeros(0)
        self.lon = np.array([inv[f].lon for f in self.ids]) if self.ids else np.zeros(0)

    def within(self, lat: float, lon: float, km: float) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        dlon = np.abs((self.lon - lon + 180.0) % 360.0 - 180.0)
        cos = max(math.cos(math.radians(min(abs(lat) + 0.5, 90.0))), 1e-6)
        m = np.nonzero((np.abs(self.lat - lat) * KM_PER_DEG <= km + 0.1) & (dlon * KM_PER_DEG * cos <= km + 0.1))[0]
        got = [(self.ids[j], tag.haversine_km(lat, lon, self.lat[j], self.lon[j])) for j in m]
        return sorted([(g, d) for g, d in got if d <= km], key=lambda x: (x[1], x[0]))


def _noaa_id_of(file_id: str) -> str | None:
    """The NOAA station number in a record id (7 digits as the site code: settlement_point-9710441-usa-noaa)."""
    parts = file_id.split("-")
    return parts[-3] if len(parts) >= 4 and len(parts[-3]) == 7 and parts[-3].isdigit() else None


def apply_two_source(rows: dict[str, AuditRow], inv: Mapping[str, RecordInfo], cfg: SiblingConfig, weak: WeakConfig,
                     dec: Mapping[str, int], transforms: Mapping[str, tuple[str, int]], sp: Mapping[str, float],
                     index: "tag.RefIndex", log, lags: Mapping[str, Mapping] | None = None,
                     judge: Iterable[str] | None = None, dry: bool = False, exclude: Iterable[str] = ()) -> dict:
    """The two-source dispute rule (timebase_agency.two_sources; FIX-LST-2/3), after apply_agency. Dispute only, per
    record: a record that publishes its declared base (unverified or disputed; a verified or corrected one is not
    judged here), fitted on the base it publishes, whose EOT20 (E), other contributors' sets (S) and agency shift (H)
    put it off by the same sign (two of them at least) is disputed (TWO_SOURCE_DISPUTED; a record already disputed
    keeps its code, the evidence is added to its reason).
    S: sets within TS_SITE_KM in the same water; for a LEAD of AG_LEAD_MIN or more also a set a comparator verified
    within TS_LEAD_SET_KM (residual AG_LEAD_RESID_MIN; judged over open water through the set's own EOT20 lag, or in
    the same water), and a set across a structure within TS_LOCK_KM (timebase_agency.lock_pair). Independence: a set
    that is an H reference (its NOAA number) or at an H reference's site (within TS_SAME_SITE_KM, M2 ratio
    AG_WATER_RATIO against the reference's constants) is the same source and is dropped from S.
    A shift under AG_LEAD_MIN needs an agreeing source with a uniform, undamped shift (timebase_agency.uniform).
    The references are judged as they stand before this step, so one record's dispute never makes another's.
    judge / dry (calibration): judge exactly these records, whatever their status; dry: change no row, return the
    hits in counts["hits"]. exclude: records never used as a set (the reviewed withholds)."""
    counts = {"bound": 0, "with_two_sources": 0, "disputed": 0, "already_disputed": 0, "not_uniform": 0,
              "sets_same_site_dropped": 0}
    judge = None if judge is None else set(judge)
    bound = bound_fits(rows, inv, cfg, dec)
    offset = {f for f, row in rows.items()
              if row.status == "residue" and any(c in row.comparator_id for c in tag.TS_OFFSET_CODES)} | set(exclude)
    near = _Near(sorted(bound), inv)
    refs_by = {f"{r.src}:{r.id}": r for r in index.refs}
    eot_cache: dict[str, float | None] = {}

    def eot_lag_of(g: str) -> float | None:
        if g not in eot_cache:
            e = weak.inputs.eot(transforms.get(g, (g,))[0])
            x = None
            if e and e.get("dist_km") is not None and float(e["dist_km"]) <= tw_E_MAX_KM:
                x = tag.pair(bound[g], {k: tuple(e[k]) for k in tag.tw.CORE if k in e}, sp, ratio=tag.TS_EOT_RATIO,
                             resid=tag.TS_EOT_RESID_MIN)
            eot_cache[g] = None if x is None else x["tau"]
        return eot_cache[g]

    hits = []
    for f in sorted(bound):
        row = rows.get(f)
        if judge is not None:
            if f not in judge:
                continue
        elif row is None or row.status == "used":
            continue                     # verified or corrected stays as it is: this rule only withholds the declared base
        counts["bound"] += 1
        a, info = bound[f], inv[f]
        src: dict[str, float] = {}
        ev: dict = {}
        det: dict[str, list] = defaultdict(list)
        e = weak.inputs.eot(transforms.get(f, (f,))[0])
        if e and e.get("dist_km") is not None and float(e["dist_km"]) <= tw_E_MAX_KM:
            x = tag.pair(a, {k: tuple(e[k]) for k in tag.tw.CORE if k in e}, sp, ratio=tag.TS_EOT_RATIO,
                         resid=tag.TS_EOT_RESID_MIN)
            if x is not None:
                src["E"], ev["E"] = x["tau"], {"tau": round(x["tau"], 1), "how": x["how"], "dist_km": e["dist_km"]}
                det["E"].append(x)
        sh = tag.shift(a, index.near(info.lat, info.lon), sp, lags) if len(index) else None
        h_refs = [refs_by[q["ref"]] for q in sh["refs"]] if sh is not None and not sh["mixed"] else []
        if h_refs:
            src["H"], ev["H"] = sh["tau"], {"tau": round(sh["tau"], 1), "refs": [q["ref"] for q in sh["refs"][:4]]}
            det["H"] += sh["refs"]
        sets = []
        for g, km in near.within(info.lat, info.lon, max(tag.TS_SITE_KM, tag.TS_LEAD_SET_KM)):
            if g == f or g in offset or inv[g].contributor == info.contributor:
                continue
            x, kind = None, ""
            if km <= tag.TS_SITE_KM:
                x, kind = tag.pair(a, bound[g], sp, ratio=tag.TS_SET_RATIO, resid=tag.TS_SET_RESID_MIN), "same water"
            if x is None and km <= tag.TS_LOCK_KM:
                x, kind = tag.lock_pair(a, bound[g], sp, ratio=tag.TS_SET_RATIO, resid=tag.TS_SET_RESID_MIN), "structure"
                if x is not None and x["tau"] > -tag.AG_LEAD_MIN:
                    x = None
            if x is None and rows.get(g) is not None and rows[g].status == "used" and rows[g].correction.usable:
                y = tag.pair(a, bound[g], sp, ratio=tag.AG_RATIO, resid=tag.AG_LEAD_RESID_MIN)
                if y is not None:
                    same = (tag.AG_WATER_RATIO[0] <= y["ratio"] <= tag.AG_WATER_RATIO[1]
                            and y["resid"] <= tag.AG_WATER_RESID_MIN)
                    lg = eot_lag_of(g)
                    if same or lg is not None:
                        eff = y["tau"] + (0.0 if same else max(lg, 0.0))
                        if eff <= -tag.AG_LEAD_MIN:
                            x, kind = {**y, "tau": eff}, "verified set over open water"
            if x is None:
                continue
            nid = _noaa_id_of(g)
            if any(r.src == "noaa" and r.id == nid for r in h_refs) or any(
                    tag.haversine_km(inv[g].lat, inv[g].lon, r.lat, r.lon) <= tag.TS_SAME_SITE_KM
                    and tag.joint_lag(bound[g], r.consts, sp, ratio=tag.AG_WATER_RATIO) is not None
                    for r in h_refs):
                counts["sets_same_site_dropped"] += 1
                continue
            sets.append({"set": g, "km": round(km, 2), "tau": round(x["tau"], 1), "how": x["how"], "kind": kind})
            det["S"].append(x)
        if sets:
            src["S"] = statistics.median(s_["tau"] for s_ in sets)
            ev["S"] = {"tau": round(src["S"], 1), "sets": sorted(sets, key=lambda s_: (s_["km"], s_["set"]))[:6]}
        got = tag.two_sources(src)
        if got is None:
            continue
        sign, agree = got
        if min(abs(src[k]) for k in agree) < tag.AG_LEAD_MIN or sign > 0:
            if not any(tag.uniform(x) for k in agree for x in det[k]):
                counts["not_uniform"] += 1
                continue
        counts["with_two_sources"] += 1
        hits.append((f, got, ev))
    if dry:
        counts["hits"] = hits
        return counts
    names = {"E": "EOT20", "S": "other contributors' sets", "H": "agency constants"}
    for f, (sign, agree), ev in hits:
        row = rows[f]
        why = (f"two independent sources put the record {'early' if sign < 0 else 'late'}: "
               + "; ".join(f"{names[k]} {ev[k]['tau']:+.1f} min" + (f" ({', '.join(x['set'] for x in ev['S']['sets'][:3])})"
                                                                    if k == 'S' else '') for k in agree)
               + "; disputed, never corrected on it")
        row.agency = {**(row.agency or {}), "two_source": {"sign": sign, "agree": agree, **ev}, "summary":
                      "; ".join(x for x in ((row.agency or {}).get("summary"), why) if x)}
        if row.status == "residue":
            counts["already_disputed"] += 1
        else:
            counts["disputed"] += 1
        _sib_dispute(row, TWO_SOURCE_DISPUTED, why)
    log("two sources: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts


REGIONAL_DISPUTED = "disputed:eot20_regional_offset"


def apply_regional(rows: dict[str, AuditRow], inv: Mapping[str, RecordInfo], cfg: SiblingConfig, weak: WeakConfig,
                   dec: Mapping[str, int], transforms: Mapping[str, tuple[str, int]], sp: Mapping[str, float],
                   log, judge: Iterable[str] | None = None, dry: bool = False, exclude: Iterable[str] = ()) -> dict:
    """EOT20 confirmed by the sea's other gauges (timebase_agency.regional_offset / control_ok; FIX-LST-3), after
    apply_two_source. Dispute only, per record: a Coastal record that publishes its declared base, fitted on it, whose
    EOT20 bands both put it off by RB_MIN or more, the same way, while a gauge of another provider family within
    RB_CONTROL_KM agrees with EOT20, is disputed (REGIONAL_DISPUTED; an already disputed record keeps its code).
    judge / dry / exclude: as apply_two_source (an excluded record is never a control nor a veto)."""
    counts = {"judged": 0, "offset_by_eot20": 0, "disputed": 0, "already_disputed": 0, "no_control": 0}
    judge = None if judge is None else set(judge)
    bound = bound_fits(rows, inv, cfg, dec)
    gt = {f: (cfg.fits[f].get("gauge_type") or "") for f in bound}
    offset = {f for f, row in rows.items()
              if row.status == "residue" and any(c in row.comparator_id for c in tag.TS_OFFSET_CODES)}
    ex = set(exclude)
    near = _Near(sorted(f for f in bound if gt[f] == tsib.COASTAL and f not in ex), inv)
    bl: dict[str, dict | None] = {}

    def bands(g: str):
        if g not in bl:
            bl[g] = tag.band_lags(bound[g], weak.inputs.eot(transforms.get(g, (g,))[0]), sp)
        return bl[g]

    hits = []
    for f in sorted(bound):
        row = rows.get(f)
        if gt[f] != tsib.COASTAL or (f not in judge if judge is not None else (row is None or row.status == "used")):
            continue
        counts["judged"] += 1
        if not tag.microtidal(bound[f], weak.inputs.eot(transforms.get(f, (f,))[0])):
            continue
        off = tag.regional_offset(bands(f))
        if off is None:
            continue
        counts["offset_by_eot20"] += 1
        fam = tsib.family_of(f)
        nb = near.within(inv[f].lat, inv[f].lon, tag.RB_CONTROL_KM)
        veto = []
        for g, km in nb:
            if g == f:
                continue
            o = tag.regional_offset(bands(g))
            if o is None or (o > 0) != (off > 0):
                continue
            if rows.get(g) is not None and rows[g].status == "used" and rows[g].comparator_kind in ("b", "c", "a"):
                veto.append(f"{g} (verified, EOT20 {o:+.0f})")
            elif km <= tag.RB_SITE_KM and tsib.family_of(g) != fam and abs(o - off) <= tag.RB_SAME_MIN:
                veto.append(f"{g} ({km:.1f} km, EOT20 {o:+.0f})")
        if veto:
            counts["vetoed"] = counts.get("vetoed", 0) + 1
            continue
        ctl = []
        for g, km in nb:
            if g == f or g in offset or tsib.family_of(g) == fam or (rows.get(g) and rows[g].status == "residue"):
                continue
            c = tag.control_ok(bands(g))
            if c is not None:
                ctl.append((g, round(km, 1), round(c, 1)))
        if not ctl:
            counts["no_control"] += 1
            continue
        hits.append((f, off, ctl))
    if dry:
        counts["hits"] = hits
        return counts
    for f, off, ctl in hits:
        row = rows[f]
        b = bl[f]
        why = (f"EOT20 puts the record {abs(off):.0f} min {'late' if off > 0 else 'early'} in both bands (semidiurnal "
               f"{b['semi']['tau']:+.1f}, diurnal {b['diurnal']['tau']:+.1f} min), and {len(ctl)} gauge(s) of other "
               f"providers in the same sea agree with EOT20 ({', '.join(f'{g} {km:g} km {c:+.1f}' for g, km, c in ctl[:3])});"
               f" disputed, never corrected on it")
        row.agency = {**(row.agency or {}), "regional": {"offset_min": round(off, 1), "semi": round(b["semi"]["tau"], 1),
                                                          "diurnal": round(b["diurnal"]["tau"], 1),
                                                          "controls": [list(x) for x in ctl[:6]]},
                      "summary": "; ".join(x for x in ((row.agency or {}).get("summary"), why) if x)}
        counts["already_disputed" if row.status == "residue" else "disputed"] += 1
        _sib_dispute(row, REGIONAL_DISPUTED, why)
    log("EOT20 regional: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts


# --- reviewed withholds (FIX-LST-4) -------------------------------------------------------------------------------------

REVIEWER_DISPUTED = "disputed:reviewer_evidence"
WITHHOLD_PATH = Path(__file__).resolve().parent / "timebase_reviewed_withhold.json"
WITHHOLD_FIELDS = ("file_id", "short", "reason", "evidence", "date")
WITHHOLD_EVIDENCE = ("reviewer", "report", "numbers")


class WithholdError(ValueError):
    """The reviewed-withhold list is unusable: the audit stops (never a silent override)."""


def load_withhold(path: str | Path = WITHHOLD_PATH) -> list[dict]:
    """The committed reviewed-withhold list: entries with file_id, short, reason, evidence {reviewer, report, numbers}
    and date (YYYY-MM-DD). A missing field, a bad date or a repeated id raises WithholdError."""
    d = json.loads(Path(path).read_text())
    out, seen = [], set()
    for i, e in enumerate(d.get("entries", [])):
        miss = [k for k in WITHHOLD_FIELDS if not e.get(k)] + [f"evidence.{k}" for k in WITHHOLD_EVIDENCE
                                                                if not (e.get("evidence") or {}).get(k)]
        if miss:
            raise WithholdError(f"{path}: entry {i} ({e.get('file_id', '?')}) lacks {', '.join(miss)}")
        try:
            dt.date.fromisoformat(e["date"])
        except ValueError as x:
            raise WithholdError(f"{path}: entry {i} ({e['file_id']}): date {e['date']!r}: {x}") from x
        if e["file_id"] in seen:
            raise WithholdError(f"{path}: {e['file_id']} listed twice")
        seen.add(e["file_id"])
        out.append(dict(e))
    return out


def check_withhold(withhold: Sequence[dict], inv: Mapping[str, RecordInfo]) -> None:
    """Every listed id must be an inventory record (a typo never withholds nothing silently)."""
    lost = [e["file_id"] for e in withhold if e["file_id"] not in inv]
    if lost:
        raise WithholdError(f"reviewed withhold: {len(lost)} id(s) not in the inventory: {', '.join(lost)}")


def apply_reviewed_withhold(rows: dict[str, AuditRow], withhold: Sequence[dict], inv: Mapping[str, RecordInfo],
                            log) -> dict:
    """Each listed record with a row becomes disputed (REVIEWER_DISPUTED, reason "reviewer_evidence: <short>"), never
    corrected; a record already disputed takes this code too (the review is its explicit reason), its earlier reason
    kept after it. A listed record the audit verified or corrected, by any evidence (official, harcon, sibling series,
    provider inheritance, the weak checks or the sibling-constants rule; FIX-LST-5 L2), stops the audit: the list
    never overrides evidence."""
    check_withhold(withhold, inv)
    strong = [e["file_id"] for e in withhold if e["file_id"] in rows and rows[e["file_id"]].status == "used"
              and rows[e["file_id"]].correction.usable]
    if strong:
        raise WithholdError("reviewed withhold: listed record(s) the audit verified or corrected: "
                            + ", ".join(f"{f} ({rows[f].comparator_kind}: {rows[f].comparator_id})" for f in strong))
    counts = {"listed": len(withhold), "applied": 0, "was_disputed": 0, "not_in_run": 0}
    for e in withhold:
        row = rows.get(e["file_id"])
        if row is None:
            counts["not_in_run"] += 1
            continue
        was = row.reason if row.status == "residue" else ""
        counts["was_disputed"] += row.status == "residue"
        row.status, row.verdict, row.base = "residue", "unexplained", "unexplained"
        row.correction, row.comparator_kind, row.comparator_id = NOT_USABLE, "w", REVIEWER_DISPUTED
        row.months_bad = []
        row.reason = (f"reviewer_evidence: {e['short']}" + (f"; was disputed: {was}" if was else ""))[:REASON_MAX * 2]
        row.agency = {**(row.agency or {}), "reviewed_withhold": {k: e[k] for k in WITHHOLD_FIELDS},
                      "summary": "; ".join(x for x in ((row.agency or {}).get("summary"),
                                                       f"reviewed withhold ({e['date']}, {e['evidence']['reviewer']}): "
                                                       f"{e['short']}") if x)}
        counts["applied"] += 1
    log("reviewed withhold: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts


def inherited_hours(corr: Correction) -> int | None:
    """The whole hours an inherited class publishes (utc_instant 0, utc_plus_n n), the n_decl its record's
    weak checks are judged against; None for a class they cannot test (utc_mean_start60, local_dst)."""
    if corr.kind == "utc_instant":
        return 0
    if corr.kind == "utc_plus_n":
        return int(corr.n)
    return None


def yield_inherited(row: AuditRow, res, span: tuple[int, int] | None, notes: Sequence[str] = (),
                    crashed: bool = False) -> None:
    """The weak rule's result on an INHERITED record, judged against the inherited class (timebase_weak.combine):
    a weak `disputed`, or a `verified` / `corrected` of another class than the inherited one -> disputed
    (residue, D5), the reason naming both; verified as the inherited class, or unverified -> the inheritance
    stands (the weak evidence is kept on the row). So only evidence combine can conclude on yields: EOT20
    alone never disputes (it can fake whole hours), and an inherited class other than the declared one is
    judged without same-lineage served stations, so with EOT20 and no independent served station its
    inheritance stands even when EOT20 disagrees (a rule choice, in the backlog)."""
    if res is None and crashed:
        # our code crashed: the record's own checks never ran because of a bug, not a data limit; the
        # provider's verdict is not published for it as verified (unverified, GESLA's declared time base)
        row.weak = {"code": WEAK_CRASHED, "notes": list(notes)}
        row.reason = (f"{row.reason}; own weak checks crashed (weak_crashed), so not verified by the provider: "
                      f"{'; '.join(notes) or '?'}")[:REASON_MAX * 2]
        row.status, row.verdict, row.base, row.correction = "unverified", "no_comparator", "no_comparator", NOT_USABLE
        row.comparator_kind, row.comparator_id = "none", ""
        return
    if res is None:
        # visible in the reason and the CSV weak columns (and counted in weak_failures): never a silent pass
        row.weak = {"code": WEAK_FAILED, "notes": list(notes)}
        # the verdict first, the (long) error last: the CSV cuts the reason at REASON_MAX
        row.reason = (f"{row.reason}; the inheritance stands, own weak checks failed (weak_failed): "
                      f"{'; '.join(notes) or '?'}")[:REASON_MAX * 2]
        return
    row.weak = {"code": res.code, "evidence": _num(res.evidence)}
    row.tried = list(row.tried) + ["weak:eot20", "weak:served", "weak:dst_selftest"]
    conflict = res.annotation == "disputed" or (res.annotation in ("verified", "corrected")
                                                and res.label != row.verdict)
    if not conflict:
        return
    code = res.code if res.annotation == "disputed" else f"disputed:inherited_{row.verdict}_vs_weak_{res.label}"
    row.reason = (f"{row.reason}; the record's own weak evidence disagrees ({res.code}): {res.reason}")[:REASON_MAX * 2]
    row.status, row.verdict, row.base = "residue", "unexplained", "unexplained"
    row.correction, row.comparator_kind, row.comparator_id = NOT_USABLE, "w", code
    row.span = span


EVIDENCE = {"b": "official", "c": "harcon", "a": "sibling", "inherit": "provider"}


def annotate(row: AuditRow, n_decl: int = 0) -> AuditRow:
    """The record's time-base annotation and the correction it is published with (owner rule: no
    record is dropped; unverified and disputed records keep GESLA's declared time base)."""
    decl = declared_correction(n_decl)
    if row.status == "used" and row.correction.usable:
        label = row.correction.label
        if row.comparator_kind == "w":
            ev = row.comparator_id.rsplit(":", 1)[-1]
        else:
            ev = EVIDENCE.get(row.comparator_kind, row.comparator_kind or "none")
        dropped = f"; months dropped {','.join(row.months_bad)}" if row.months_bad else ""
        if label == decl.label:
            row.annotation, row.annotation_code = "verified", f"verified:{ev}"
            row.annotation_reason = f"declared {decl.label} confirmed by {ev} ({row.comparator_id}){dropped}"
        else:
            row.annotation, row.annotation_code = "corrected", f"corrected:{decl.label}->{label}:{ev}"
            row.annotation_reason = f"declared {decl.label} corrected to {label} by {ev} ({row.comparator_id}){dropped}"
        row.published = row.correction
    elif row.status == "residue" and (row.task_failed or row.reason.startswith("audit task failed")):
        # a crash is no evidence: nothing disagreed, nothing was checked (still a D5 row, to look at)
        row.annotation, row.annotation_code = "unverified", "unverified:audit_task_failed"
        row.annotation_reason = f"declared {decl.label} kept; {row.reason}"
        row.published = decl
    elif row.status == "residue":
        why = row.comparator_id.split(":", 2)[1] if row.comparator_kind == "w" else "comparators_disagree"
        row.annotation, row.annotation_code = "disputed", f"disputed:{why}"
        row.annotation_reason = f"declared {decl.label} kept; evidence disagrees: {row.reason}"
        row.published = decl
    else:
        code = (row.weak or {}).get("code") or "unverified:no_comparator"
        row.annotation, row.annotation_code = "unverified", code
        row.annotation_reason = f"declared {decl.label} kept; {row.reason}"
        row.published = decl
    row.annotation_reason = row.annotation_reason[:REASON_MAX * 2]
    return row


ANNOTATIONS = ("verified", "corrected", "unverified", "disputed")


def _family(row: AuditRow) -> str:
    """A verdict's class for provider agreement (months_bad counts as its base class)."""
    if row.verdict.startswith("months_bad:"):
        return row.base
    return row.verdict


def inherit(rows: dict[str, AuditRow], inv: Mapping[str, RecordInfo], pool: Sequence[str], cand_set: set[str],
            *, version: str) -> list[dict]:
    """Rule 5 on every record without a comparator verdict; returns the provider table."""
    groups: dict[tuple[str, int], list[str]] = defaultdict(list)
    for f in pool:
        groups[(inv[f].contributor, inv[f].interval_s)].append(f)
    providers = []
    for key in sorted(groups):
        members = groups[key]
        audited = [f for f in members if f in rows]
        # A record whose task crashed has no verdict: it is no evidence about the provider either way,
        # so it neither counts towards INHERIT_MIN nor breaks the agreement (it stays residue itself).
        judged = [f for f in audited if not rows[f].task_failed]
        fams = sorted({_family(rows[f]) for f in judged})
        passing = sorted({_family(rows[f]) for f in judged if rows[f].usable})
        # The provider's verdict: the one class its passing records agree on. Inheritance needs more:
        # every judged record passed with that class (an unexplained one breaks it).
        verdict = passing[0] if len(passing) == 1 else ("mixed" if passing else "none")
        ok = len(judged) >= INHERIT_MIN and len(fams) == 1 and passing == fams \
            and not verdict.startswith("step:")
        # Only judged records of the record's own country vouch for it (precision first: 3 Pacific BOM
        # records say nothing about BOM's Australian gauges).
        by_country: dict[str, list[str]] = defaultdict(list)
        for f in judged:
            if inv[f].country:
                by_country[inv[f].country].append(f)
        n_inh = n_exc = 0
        for f in members:
            if f in rows:
                continue
            info = inv[f]
            local = by_country.get(info.country, []) if info.country else []
            spans = [rows[g].span or (inv[g].start_s, inv[g].end_s) for g in local]
            # an inherited local_dst takes its zone from the judged records only when they all name one zone
            zones = sorted({rows[g].correction.zone or "" for g in local if rows[g].correction.kind == "local_dst"})
            if ok and len(local) >= INHERIT_MIN and len(zones) <= 1 \
                    and any(a < info.end_s and info.start_s < b for a, b in spans):
                # ok: every judged record passed with `verdict`, so any of them carries its correction
                # (with the zone, for local_dst); a crashed record has none.
                corr = replace(rows[local[0]].correction, months_drop=(), first=None, parts=())
                rows[f] = AuditRow(f, f in cand_set, info.contributor, info.interval_s, "used", verdict, verdict,
                                   corr, None, "inherit", f"provider:{key[0]}:{key[1]}",
                                   reason=f"inherits {verdict} from {len(local)} judged records of {key[0]} "
                                          f"in {info.country} (interval {key[1]} s)")
                n_inh += 1
            else:
                if not ok:
                    why = (f"no comparator; provider {key[0]} (interval {key[1]} s) has {len(judged)} judged records"
                           f" with verdicts {fams or '-'}")
                elif not info.country:
                    why = (f"no comparator; provider {key[0]} (interval {key[1]} s) verdict {verdict} from "
                           f"{len(judged)} judged records, but the record's country is unknown, "
                           f"so none vouches for it")
                elif len(zones) > 1:
                    why = (f"no comparator; provider {key[0]} (interval {key[1]} s) verdict {verdict}, but its judged "
                           f"records in {info.country} name zones {', '.join(zones)}, so no zone to inherit")
                elif len(local) < INHERIT_MIN:
                    why = (f"no comparator; provider {key[0]} (interval {key[1]} s) verdict {verdict} from "
                           f"{len(judged)} judged records, but {len(local)} judged records in {info.country} "
                           f"(needs {INHERIT_MIN}), so it does not inherit")
                else:
                    why = (f"no comparator; span does not overlap the audited records of {key[0]} "
                           f"in {info.country}")
                rows[f] = AuditRow(f, f in cand_set, info.contributor, info.interval_s, "unverified", "no_comparator",
                                   "no_comparator", NOT_USABLE, None, "none", "", reason=why)
                n_exc += 1
        counts = defaultdict(int)       # audited records only: they were listed before inheritance added rows
        for f in audited:
            counts[rows[f].verdict] += 1
        providers.append({"contributor": key[0], "version": version, "interval_s": key[1],
                          "provider_verdict": verdict, "inheritable": ok, "n_records": len(members),
                          "n_audited": len(audited), "n_inherited": n_inh, "n_no_comparator": n_exc,
                          "n_residue": sum(1 for f in audited if rows[f].status == "residue"),
                          "verdicts": dict(sorted(counts.items()))})
    return providers


# OWNER SWITCH (D-O3, owner-pending; default ON): contributors whose records inherit the class their own records'
# sibling-series verdicts agree on (provider_class_inherit). Set to () to turn it off (or --no-provider-class-inherit).
PROVIDER_CLASS_INHERIT: tuple[str, ...] = ("WSV",)
PROVIDER_CLASS_ID = "D-O3"


def provider_class_inherit(rows: dict[str, AuditRow], inv: Mapping[str, RecordInfo], ids: Sequence[str],
                           contributors: Sequence[str], fits: Mapping[str, Mapping] | None = None,
                           speeds: Mapping[str, float] | None = None,
                           declared: Mapping[str, int] | None = None) -> dict:
    """D-O3 (owner switch PROVIDER_CLASS_INHERIT): a provider's verified class for its records no strong comparator
    judged. The rule 5 machinery (inherit) with one change of voters: in a group (contributor, interval) of a listed
    contributor, the voters are the records a SIBLING SERIES verified (comparator kind a: the monthly series test, the
    only one that can establish utc_mean_start60; an official or harcon lag is record-level and cannot, so those
    verdicts do not vote). With >= INHERIT_MIN voters in the record's country, all of one class (no step), every
    other record of the group in that country whose span overlaps a voter's inherits it (comparator kind
    "inherit", evidence "provider") unless a strong comparator (official, harcon, sibling) judged it, its task
    crashed, or its own pass-1 constants contradict the class: with fits, a Coastal record of another contributor
    within timebase_sibling.SITE_KM publishing utc_instant whose constants are clock-comparable must show the class's
    shift (utc_mean_start60: -30 min, within ALIGN_MIN); any other comparable shift keeps the record as it was.
    A disputed record (its comparators could not test mean60) is replaced too: that is the owner rule. Returns
    counts and the records each way."""
    out: dict = {"inherited": [], "contradicted": [], "groups": {}}
    if not contributors:
        return out
    want = {c.upper() for c in contributors}
    groups: dict[tuple[str, int], list[str]] = defaultdict(list)
    for f in ids:
        if inv[f].contributor.upper() in want:
            groups[(inv[f].contributor, inv[f].interval_s)].append(f)
    centre = {"utc_mean_start60": MEAN60_LAG, "utc_instant": 0.0}
    for key, members in sorted(groups.items()):
        voters = [f for f in members if f in rows and rows[f].status == "used" and rows[f].comparator_kind == "a"
                  and not rows[f].task_failed]
        by_c: dict[str, list[str]] = defaultdict(list)
        for f in voters:
            if inv[f].country:
                by_c[inv[f].country].append(f)
        for country, vs in sorted(by_c.items()):
            fams = sorted({_family(rows[f]) for f in vs})
            info = {"voters": len(vs), "classes": fams}
            out["groups"][f"{key[0]}:{key[1]}:{country}"] = info
            if len(vs) < INHERIT_MIN or len(fams) != 1 or fams[0].startswith("step:"):
                continue
            cls = fams[0]
            corr = replace(rows[vs[0]].correction, months_drop=(), first=None, parts=())
            spans = [rows[g].span or (inv[g].start_s, inv[g].end_s) for g in vs]
            for f in members:
                r = rows.get(f)
                if inv[f].country != country or r is None or f in vs or r.task_failed:
                    continue
                if r.status == "used" and r.comparator_kind in ("b", "c", "a"):
                    continue
                if not any(a < inv[f].end_s and inv[f].start_s < b for a, b in spans):
                    continue
                ev = _class_check(f, cls, centre.get(cls), rows, inv, fits, speeds, declared or {})
                if ev.get("contradicts"):
                    out["contradicted"].append((f, ev["why"]))
                    r.reason = f"{r.reason}; {PROVIDER_CLASS_ID}: not inherited ({ev['why']})"[:REASON_MAX * 2]
                    continue
                rows[f] = replace(r, status="used", verdict=cls, base=cls, correction=corr, round=None,
                                  comparator_kind="inherit", comparator_id=f"provider:{key[0]}:{key[1]}:{PROVIDER_CLASS_ID}",
                                  span=None, months_bad=[],
                                  reason=(f"{PROVIDER_CLASS_ID}: inherits {cls} from the {len(vs)} records of {key[0]} in "
                                          f"{country} a sibling series verified (interval {key[1]} s); "
                                          f"{ev.get('why', 'no comparable record of another contributor within 2 km')}; "
                                          f"was {r.status} ({r.reason[:120]})")[:REASON_MAX * 2])
                out["inherited"].append(f)
            info["inherited"] = sum(1 for f in out["inherited"] if f in members)
    return out


def _class_check(f: str, cls: str, centre: float | None, rows: Mapping[str, AuditRow], inv: Mapping[str, RecordInfo],
                 fits: Mapping[str, Mapping] | None, speeds: Mapping[str, float] | None,
                 declared: Mapping[str, int]) -> dict:
    """provider_class_inherit's own-evidence check: the record's pass-1 constants against every Coastal record of
    another contributor within SITE_KM that publishes utc_instant (both fitted on their published base)."""
    if not fits or not speeds or centre is None or f not in fits:
        return {}
    a = fits[f]
    if a.get("outcome") != "fitted" or a.get("gauge_type") != tsib.COASTAL \
            or a.get("time_base") != declared_correction(int(declared.get(f, 0))).label:
        return {}
    seen = []
    for g, b in fits.items():
        if g == f or g not in inv or b.get("outcome") != "fitted" or b.get("gauge_type") != tsib.COASTAL \
                or inv[g].contributor == inv[f].contributor:
            continue
        if abs(inv[g].lat - inv[f].lat) > 0.03 or tsib.haversine_km(inv[f].lat, inv[f].lon, inv[g].lat, inv[g].lon) > tsib.SITE_KM:
            continue
        rg = rows.get(g)
        pub = (_published_now(rg, int(declared.get(g, 0))) if rg else declared_correction(int(declared.get(g, 0)))).label
        if pub != "utc_instant" or b.get("time_base") != "utc_instant":
            continue
        t = tsib.pair_shift({k: (v[0], v[1]) for k, v in a["main"].items()}, {k: (v[0], v[1]) for k, v in b["main"].items()},
                            speeds)
        if t is not None:
            seen.append((g, t))
    if not seen:
        return {}
    bad = [(g, t) for g, t in seen if abs(t - centre) > tsib.ALIGN_MIN]
    txt = ", ".join(f"{g} {t:+.1f} min" for g, t in seen)
    if bad:
        return {"contradicts": True, "why": f"its constants against {txt}: not the {centre:+.0f} min of {cls}"}
    return {"why": f"its constants show the {centre:+.0f} min of {cls} against {txt}"}


def final_counts(providers: list[dict], rows: Mapping[str, AuditRow], inv: Mapping[str, RecordInfo]) -> None:
    """Add each provider group's FINAL annotation counts (after the weak evidence and annotate()).
    n_no_comparator is the count before the weak evidence: records left without a comparator verdict;
    n_inherited likewise (an inherited record the weak evidence disputes is counted there and in n_disputed)."""
    by: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for f, r in rows.items():
        by[(inv[f].contributor, inv[f].interval_s)][r.annotation] += 1
    for p in providers:
        c = by[(p["contributor"], p["interval_s"])]
        for a in ("verified", "corrected", "unverified", "disputed"):
            p[f"n_{a}"] = c.get(a, 0)


def _num(x):
    if isinstance(x, float):
        return round(x, 4) if math.isfinite(x) else None
    if isinstance(x, (np.floating, np.integer)):
        return _num(x.item())
    if isinstance(x, dict):
        return {str(k): _num(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_num(v) for v in x]
    return x


def row_evidence(row: AuditRow) -> dict:
    """The numbers behind a verdict (BP §7.1 tier-2 list), JSON-safe."""
    ev = {"verdict": row.verdict, "base": row.base, "comparator": row.comparator_id,
          "comparator_kind": row.comparator_kind, "contributor": row.contributor, "interval_s": row.interval_s,
          "comparators_tried": list(row.tried)}
    if row.outcome:
        v = row.outcome.verdict
        ev.update(lags=v.evidence.get("lags"), constituents=v.evidence.get("constituents"),
                  sampling=v.evidence.get("sampling"), residual=v.residual or None,
                  reasons=[r[:REASON_MAX] for r in v.reasons][:8])
        if v.span:
            ev["span"] = [_iso_day(v.span[0]), _iso_day(v.span[1])]
    if row.official:
        ev["official"] = row.official
    if row.weak:
        ev["weak"] = row.weak
    if row.sibling:
        ev["sibling"] = row.sibling
    if row.agency:
        ev["agency"] = row.agency
    return _num(ev)


def residue_cases(rows: Mapping[str, AuditRow], providers: Sequence[dict],
                  previous: Mapping[tuple[str, int], str], *, version: str) -> list[ResidueCase]:
    out = []
    for f in sorted(rows):
        r = rows[f]
        if r.status == "residue":
            out.append(ResidueCase(f, "timebase", "D5", f"unexplained: {r.reason}"[:REASON_MAX * 2], row_evidence(r)))
    for p in providers:
        key = (p["contributor"], p["interval_s"])
        old = previous.get(key)
        new = p["provider_verdict"]
        if old is None or old == new:
            continue
        members = [rows[f] for f in rows if (rows[f].contributor, rows[f].interval_s) == key]
        support = sum(1 for r in members if _family(r) == new and r.comparator_kind in ("b", "c", "a"))
        direct = sum(1 for r in members if _family(r) == new and (r.comparator_kind in ("b", "c")
                                                                  or (r.comparator_kind == "a" and r.official_verdict == new)))
        n_old = sum(1 for r in members if _family(r) == old)
        out.append(ResidueCase(f"provider:{p['contributor']}:{version}:{p['interval_s']}", "timebase", "D5",
                               f"provider verdict changed from {old} to {new}",
                               _num({"previous": old, "new": new, "n_audited": p["n_audited"],
                                     "n_support": support, "n_support_official": direct, "n_previous": n_old})))
    return out


# --- outputs -------------------------------------------------------------------------------------

CSV_FIELDS = ["file_id", "candidate", "contributor", "interval_s", "time_base_status", "time_base_code",
              "published_time_base", "verdict", "correction", "round",
              "comparator_kind", "comparator", "span_start", "span_end", "n_months", "lag_median", "lag_min",
              "lag_max", "lag_jan", "lag_jul", "resid_max", "months_bad", "m2_lag", "m2_ratio", "m4_ratio",
              "m6_ratio", "mean60_cm", "inst_t30_cm", "inst_t_cm", "official_lag", "official_matched",
              "weak_eot20", "weak_served", "weak_dst", "comparators_tried", "reason", "time_base_reason",
              "sibling_constants", "in_pool", "disputed_reason", "agency_constants"]


def _iso_day(s: int) -> str:
    return str(np.datetime64(int(s), "s").astype("datetime64[D]"))


def _f(x, nd=2):
    return "" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{nd}f}"


def disputed_reasons(r: AuditRow) -> str:
    """A disputed row's reasons for the station map (space-separated, the code's reason first): the annotation's
    own reason, and eot20_whole_hour when that rule also fired on a record already disputed for another reason."""
    if r.annotation != "disputed":
        return ""
    out = [r.annotation_code.split(":", 1)[1]]
    if (r.sibling or {}).get("rule") == "eot20_whole_hour" and "eot20_whole_hour" not in out:
        out.append("eot20_whole_hour")
    return " ".join(out)


def csv_row(r: AuditRow) -> dict:
    v = r.outcome.verdict if r.outcome else None
    ev = v.evidence if v else {}
    lags = ev.get("lags") or {}
    cons = ev.get("constituents") or {}
    smp = ev.get("sampling") or {}

    def con(n, k):
        c = cons.get(n)
        return c.get(k) if c else None

    if not r.annotation:
        annotate(r)
    wk = (r.weak or {}).get("evidence") or {}

    code = (r.weak or {}).get("code")
    failed = code in (WEAK_FAILED, WEAK_CRASHED)
    fail_note = f"{code.split(':', 1)[1] if failed else ''}: {'; '.join((r.weak or {}).get('notes') or ['?'])}"[:REASON_MAX]

    def chk(k):
        if failed:
            return fail_note
        c = wk.get(k) or {}
        if not c:
            return ""
        v = c.get("value", "")
        extra = c.get("tau", c.get("season_m2_min"))
        return f"{v}" + (f" ({extra:+.1f})" if isinstance(extra, (int, float)) else "")

    return {"file_id": r.file_id, "candidate": int(r.candidate), "contributor": r.contributor,
            "interval_s": r.interval_s, "time_base_status": r.annotation, "time_base_code": r.annotation_code,
            "published_time_base": r.published.label if r.published else "", "verdict": r.verdict,
            "correction": r.correction.label if r.correction.usable else "",
            "round": "" if r.round is None else r.round, "comparator_kind": r.comparator_kind,
            "comparator": r.comparator_id,
            "span_start": _iso_day(r.span[0]) if r.span else "", "span_end": _iso_day(r.span[1]) if r.span else "",
            "n_months": lags.get("n_months", ""), "lag_median": _f(lags.get("median")),
            "lag_min": _f(lags.get("min")), "lag_max": _f(lags.get("max")), "lag_jan": _f(lags.get("jan_median")),
            "lag_jul": _f(lags.get("jul_median")),
            "resid_max": _f((v.residual or {}).get("max_abs") if v else None, 1),
            "months_bad": " ".join(r.months_bad), "m2_lag": _f(con("M2", "lag_min")),
            "m2_ratio": _f(con("M2", "amp_ratio"), 3), "m4_ratio": _f(con("M4", "amp_ratio"), 3),
            "m6_ratio": _f(con("M6", "amp_ratio"), 3), "mean60_cm": _f(smp.get("mean60_cm")),
            "inst_t30_cm": _f(smp.get("inst_t30_cm")), "inst_t_cm": _f(smp.get("inst_t_cm")),
            "official_lag": _f(r.official.get("lag_min")),
            "official_matched": (f"{r.official['n_matched']}/{r.official['n_events']}" if "n_matched" in r.official
                                 else r.official.get("by", "")),
            "weak_eot20": chk("E"), "weak_served": fail_note if failed else (chk("S") + (f" {(wk.get('S') or {}).get('station', '')} "
                                                     f"{(wk.get('S') or {}).get('kind', '')}" if wk.get("S") else "")).strip(),
            "weak_dst": chk("D"), "comparators_tried": " ".join(r.tried), "reason": r.reason[:REASON_MAX],
            "time_base_reason": r.annotation_reason[:REASON_MAX * 2],
            "sibling_constants": (r.sibling or {}).get("summary", "")[:REASON_MAX * 2], "in_pool": int(r.in_pool),
            "disputed_reason": disputed_reasons(r),
            "agency_constants": (r.agency or {}).get("summary", "")[:REASON_MAX * 2]}


def write_csv(rows: Mapping[str, AuditRow], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for f in sorted(rows):
        w.writerow(csv_row(rows[f]))
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


PROVIDER_FIELDS = ["contributor", "version", "interval_s", "provider_verdict", "inheritable", "n_records",
                   "n_audited", "n_inherited", "n_no_comparator", "n_residue", "verdicts",
                   "n_verified", "n_corrected", "n_unverified", "n_disputed"]


def write_providers(providers: Sequence[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, PROVIDER_FIELDS, lineterminator="\n")
    w.writeheader()
    for p in providers:
        p = {**{f: "" for f in PROVIDER_FIELDS}, **p}
        w.writerow({**p, "inheritable": int(p["inheritable"]),
                    "verdicts": " ".join(f"{k}={v}" for k, v in p["verdicts"].items())})
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


def read_previous_providers(path: str | Path) -> dict[tuple[str, int], str]:
    with open(path, newline="") as fh:
        return {(r["contributor"], int(r["interval_s"])): r["provider_verdict"] for r in csv.DictReader(fh)}


def station_status(station_map: Iterable[tuple[str, str]], rows: Mapping[str, AuditRow]) -> dict[str, str]:
    """Per station id: "audited" when at least one of its records has a verified or corrected time
    base, else "unverified" (its records are published with GESLA's declared time base; no station
    falls back for its time base: owner rule, no record is dropped)."""
    usable: dict[str, bool] = {}
    for sid, fid in station_map:
        usable[sid] = usable.get(sid, False) or (fid in rows and rows[fid].usable)
    return {sid: ("audited" if ok else "unverified") for sid, ok in sorted(usable.items())}


def annotation_counts(rows: Mapping[str, AuditRow], *, candidates_only: bool = False) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows.values():
        if not candidates_only or r.candidate:
            out[r.annotation or annotate(r).annotation] += 1
    return dict(sorted(out.items()))


def class_counts(rows: Mapping[str, AuditRow], *, candidates_only: bool = False) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows.values():
        if candidates_only and not r.candidate:
            continue
        k = r.verdict
        if k.startswith("months_bad:"):
            k = f"months_bad({r.base})"
        elif k.startswith("step:"):
            k = "step"
        out[k] += 1
    return dict(sorted(out.items()))


MANIFEST = "timebase_manifest.json"


def _git(repo: Path, *args: str) -> str | None:
    """git in repo, or None when that fails or repo is not the checkout's own toplevel (git -C walks up, so an
    exported tree under $HOME would otherwise report the enclosing repository's commit)."""
    import subprocess

    def run(*a: str) -> str | None:
        try:
            p = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return p.stdout if p.returncode == 0 else None

    top = run("rev-parse", "--show-toplevel")
    if top is None or Path(top.strip()).resolve() != Path(repo).resolve():
        return None
    return run(*args)


def dir_fingerprint(path: str | Path) -> dict:
    """A cheap content fingerprint of a cache dir that links, permissions or the root's kind cannot fool.

    kind: missing | broken_link | not_dir | dir (the root, by lstat). For a dir: sha256 over the sorted lines
    "F name size mtime_ns" (a file), "L name -> target" (a link, with its target) and, for a link that
    resolves, its target's entries too (a linked subdir is walked; a linked file hashed by its own size and
    mtime); "B name -> target" for a dangling link. files / links / broken_links count them; realpath is the
    resolved root. An unreadable subdir raises PermissionError (never a smaller count)."""
    import hashlib
    d = Path(path)
    base = {"path": str(d), "realpath": None, "files": None, "links": None, "broken_links": None, "sha256": None}
    if not os.path.lexists(d):
        return {**base, "kind": "missing"}
    if d.is_symlink() and not d.exists():
        return {**base, "kind": "broken_link", "target": os.readlink(d)}
    if not d.is_dir():
        return {**base, "kind": "not_dir", "realpath": str(d.resolve())}
    lines: list[str] = []
    n = {"F": 0, "L": 0, "B": 0}

    def walk(p: Path, rel: str, seen: frozenset) -> None:
        try:
            entries = sorted(os.scandir(p), key=lambda e: e.name)
        except OSError as e:
            raise PermissionError(f"dir_fingerprint: cannot read {p}: {e}") from e
        for e in entries:
            name = f"{rel}{e.name}"
            if e.is_symlink():
                target = os.readlink(e.path)
                real = Path(e.path).resolve()
                if not real.exists():
                    lines.append(f"B {name} -> {target}")
                    n["B"] += 1
                    continue
                lines.append(f"L {name} -> {target}")
                n["L"] += 1
                if real.is_dir():
                    if real in seen:
                        lines.append(f"C {name}")          # a cycle: recorded, not walked again
                        continue
                    walk(real, name + "/", seen | {real})
                else:
                    st = real.stat()
                    lines.append(f"F {name} {st.st_size} {st.st_mtime_ns}")
                    n["F"] += 1
            elif e.is_dir(follow_symlinks=False):
                walk(Path(e.path), name + "/", seen | {Path(e.path).resolve()})
            else:
                st = e.stat(follow_symlinks=False)
                lines.append(f"F {name} {st.st_size} {st.st_mtime_ns}")
                n["F"] += 1

    walk(d, "", frozenset({d.resolve()}))
    h = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {**base, "kind": "dir", "realpath": str(d.resolve()), "files": n["F"], "links": n["L"],
            "broken_links": n["B"], "sha256": h}


def check_audit_outputs(out_dir: str | Path) -> dict:
    """The audit outputs in out_dir, fit to consume: its timebase_manifest.json says status complete (and is not
    a bench_timebase.py benchmark), lists
    weak_crashes (from 85c7076 on) and none, and records the sha256 of the timebase.csv that is there. Returns
    the manifest; any other state raises ValueError naming it (a crashed or killed audit is never fitted)."""
    out = Path(out_dir)
    mp = out / MANIFEST
    if not mp.is_file():
        raise ValueError(f"{out}: no {MANIFEST}: not an audit output dir, or a run older than the manifest")
    m = json.loads(mp.read_text())
    if m.get("status") == "benchmark" or "benchmark" in m:
        raise ValueError(f"{mp}: a bench_timebase.py benchmark ({m.get('benchmark')}), not an audit: never consumed")
    if m.get("status") != "complete":
        raise ValueError(f"{mp}: status {m.get('status')}: the audit did not complete")
    if "weak_crashes" not in m:
        raise ValueError(f"{mp}: no weak_crashes: made before the crash check (85c7076); rerun the audit")
    if m["weak_crashes"]:
        raise ValueError(f"{mp}: {len(m['weak_crashes'])} weak task(s) crashed: {', '.join(m['weak_crashes'][:10])}")
    want = (m.get("outputs") or {}).get("timebase.csv")
    got = sha256_file(out / "timebase.csv") if (out / "timebase.csv").is_file() else None
    if not want or want != got:
        raise ValueError(f"{out / 'timebase.csv'}: sha256 {got} is not the manifest's {want}")
    return m


def _utcnow() -> str:
    """The manifest clock (a function, so tests can step it)."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def write_manifest(out: Path, repo: Path, argv: Sequence[str], inputs: Mapping[str, "str | Path | None"], *,
                   status: str, started: str | None = None, outputs: Sequence[Path] = (),
                   extra: Mapping | None = None, run: Mapping | None = None) -> dict:
    """out/timebase_manifest.json: the code commit (and whether the tree was dirty), the arguments, each input's
    sha256, and (status "complete") each output's sha256. Written "running" before the audit and rewritten
    "complete" after the outputs, so outputs left by an earlier run never look like this run's. run: the
    running manifest, whose commit and dirty flag the complete one keeps (a commit made meanwhile is not the run's)."""
    now = _utcnow()
    if run is not None:
        commit, dirty_flag = run["commit"], run["dirty"]
    else:
        dirty = _git(repo, "status", "--porcelain")
        commit = (_git(repo, "rev-parse", "HEAD") or "unknown").strip()
        dirty_flag = bool(dirty and dirty.strip()) if dirty is not None else True
    m = {"status": status, "commit": commit, "dirty": dirty_flag,
         "argv": list(argv), "started": started or now,
         "inputs": dict(run["inputs"]) if run is not None else
                   {k: (None if v is None else {"path": str(v), "sha256": sha256_file(v) if Path(v).is_file() else None})
                    for k, v in sorted(inputs.items())}}
    if status != "running":
        m["finished"] = now
        m["outputs"] = {Path(p).name: sha256_file(p) for p in outputs}
    m.update(extra or {})
    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST).write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    return m


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from . import residue as residue_mod
    from .timebase_verdict import run_positive_controls

    repo = Path(__file__).resolve().parents[3]
    ap = argparse.ArgumentParser(prog="python -m gesla.timebase_audit")
    ap.add_argument("--version", default="4.1")
    ap.add_argument("--candidates", help="station map CSV (id,file_id,...); default every inventory record")
    ap.add_argument("--only", nargs="+", help="audit only these records (and their siblings)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out-dir", help="default $OTC_WORK/builds/<date>/gesla")
    ap.add_argument("--build-date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--previous-providers", help="the previous release's timebase_providers.csv")
    ap.add_argument("--gates-dir", default=os.environ.get("OTC_GATES_DIR", str(otc_work() / "gates")))
    ap.add_argument("--weak-inputs", help="timebase_weak.prepare() output (EOT20 + zones); needs --candidates")
    ap.add_argument("--served", default=str(repo / "data/ticon.json"), help="the served stations (ticon.json)")
    ap.add_argument("--no-provider-class-inherit", action="store_true",
                    help=f"turn the owner switch {PROVIDER_CLASS_ID} off (PROVIDER_CLASS_INHERIT {PROVIDER_CLASS_INHERIT})")
    ap.add_argument("--fit-results", help="pass 1 for the intra-site constants comparison (TB-SIB): a complete "
                    "gesla.fit_run results.jsonl of every record fitted with its published time base; needs --weak-inputs")
    ap.add_argument("--withhold", default=str(WITHHOLD_PATH),
                    help="the reviewed-withhold list (FIX-LST-4); applied with --fit-results")
    a = ap.parse_args(argv)
    argv = list(sys.argv[1:] if argv is None else argv)
    if a.weak_inputs and not a.candidates:
        ap.error("--weak-inputs needs --candidates (the station map gives each record's served station)")
    if a.fit_results and not a.weak_inputs:
        ap.error("--fit-results needs --weak-inputs (EOT20 picks the offset side)")
    work = otc_work()
    release = GeslaRelease(work / "inputs/gesla" / a.version)
    table_path = dump_path()       # the one table (tcd_table.dump_path): the fit's and the agency sets'
    table = TcdTable.load(table_path)
    run_positive_controls(release, table, log=_log)       # PositiveControlError: the build stops here
    inv = load_inventory(repo / "data/gesla" / a.version / "inventory.csv")
    cands = a.only or (load_candidates(a.candidates) if a.candidates else sorted(inv))
    prev = read_previous_providers(a.previous_providers) if a.previous_providers else None
    declared = {f: int(float(m.get("TIME ZONE HOURS") or 0)) for f, m in release.metadata().items()}
    weak = None
    if a.weak_inputs:
        wi = WeakInputs.load(a.weak_inputs)
        inv_path = repo / "data/gesla" / a.version / "inventory.csv"
        inv_sha = sha256_file(inv_path)
        if wi.meta.get("inventory_sha256") != inv_sha:
            raise SystemExit(f"weak inputs {a.weak_inputs} were prepared from inventory sha256 "
                             f"{wi.meta.get('inventory_sha256')}, this run's {inv_path} is {inv_sha}: re-run prepare()")
        missing = sorted(f for f in inv if not wi.has(f))
        if missing:
            raise SystemExit(f"weak inputs lack {len(missing)} inventory records: {', '.join(missing[:10])}")
        roles = {}
        with open(inv_path, newline="") as fh:
            import csv as _csv
            for r in _csv.DictReader(fh):
                roles[r["file_id"]] = Lineage(r["contributor"], r.get("role") or "")
        weak = WeakConfig(wi, load_served(a.served, a.candidates), roles)
        weak_meta = {"weak_inputs": str(a.weak_inputs), "weak_inputs_sha256": wi.meta["sha256"],
                     "inventory_sha256": inv_sha, "eot20_files": wi.meta.get("eot20_files"),
                     "timezonefinder": wi.meta.get("timezonefinder"), "served": str(a.served),
                     "served_sha256": sha256_file(a.served), "station_map": str(a.candidates),
                     "station_map_sha256": sha256_file(a.candidates)}
        print("weak inputs: " + json.dumps(weak_meta, sort_keys=True))
    sibling = None
    if a.fit_results:
        fm = Path(a.fit_results).parent / MANIFEST
        fit_m = json.loads(fm.read_text()) if fm.is_file() else {}
        if fit_m.get("status") != "complete":
            raise SystemExit(f"--fit-results {a.fit_results}: its {fm} says status {fit_m.get('status')!r}, not complete")
        fits = {}
        for line in Path(a.fit_results).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                fits[r["record"]] = r
        stations: dict[str, set] = defaultdict(set)
        with open(a.candidates, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("file_id"):
                    stations[r["file_id"]].add(r["id"])
        sibling = SiblingConfig(fits, {k: frozenset(v) for k, v in stations.items()})
        print(f"sibling constants: pass 1 {a.fit_results} ({len(fits)} records, fit commit "
              f"{str(fit_m.get('commit'))[:12]}, audit {(fit_m.get('audit') or {}).get('dir')})")
    out = Path(a.out_dir) if a.out_dir else work / "builds" / a.build_date / "gesla"
    inputs = {"inventory": repo / "data/gesla" / a.version / "inventory.csv", "station_map": a.candidates,
              "weak_inputs": a.weak_inputs, "served": a.served if a.weak_inputs else None,
              "tcd_table": table_path, "previous_providers": a.previous_providers,
              "fit_results": a.fit_results,
              "fit_results_manifest": (Path(a.fit_results).parent / MANIFEST) if a.fit_results else None,
              "reviewed_withhold": a.withhold if a.fit_results else None}
    gates = Path(a.gates_dir)
    inputs.update({"gates_harmonics_stations": gates / "evid/testplan/harmonics_stations.json",
                   "gates_nearest_ref": gates / "evid/testplan/nearest_ref.json",
                   "noaa_stations": default_noaa_cache() / "stations.json",
                   # the declared TIME ZONE HOURS of every record come from the release metadata
                   "gesla_metadata": Path(release.root) / release.metadata_path,
                   "gesla_manifest": Path(release.root) / "MANIFEST.json"})
    caches = {"gates_refs": dir_fingerprint(gates / "refs"), "dmi_events": dir_fingerprint(default_dmi_dir()),
              "noaa_cache": dir_fingerprint(default_noaa_cache())}
    config = {"args": {k: (str(v) if isinstance(v, Path) else v) for k, v in sorted(vars(a).items())},
              "otc_work": str(work), "release_root": str(release.root), "gates_dir": str(gates),
              "provider_class_inherit": [] if a.no_provider_class_inherit else list(PROVIDER_CLASS_INHERIT),
              "dmi_dir": str(default_dmi_dir()), "noaa_cache": str(default_noaa_cache())}
    run_m = write_manifest(out, repo, argv, inputs, status="running", extra={"config": config, "caches": caches})
    started = run_m["started"]
    res = audit(inv, cands, release_root=release.root, table_path=table_path, gates_dir=a.gates_dir,
                workers=a.workers, previous_providers=prev, version=a.version, weak=weak, declared=declared,
                sibling=sibling, out_of_pool=weak is not None and not a.only,
                provider_class=() if a.no_provider_class_inherit else PROVIDER_CLASS_INHERIT,
                withhold=load_withhold(a.withhold) if a.fit_results else (), log=_log)
    write_csv(res.rows, out / "timebase.csv")
    write_providers(res.providers, out / "timebase_providers.csv")
    if weak is not None:
        (out / "timebase_weak_inputs.json").write_text(json.dumps(weak_meta, indent=1, sort_keys=True) + "\n")
    rp = residue_mod.residue_path(work, a.build_date) if not a.out_dir else out / "residue.jsonl"
    n = residue_mod.replace_case(rp, "timebase", res.residue)     # a re-run replaces, never duplicates
    print(f"timebase.csv: {len(res.rows)} records ({sum(r.candidate for r in res.rows.values())} candidates), "
          f"{res.rounds} rounds; residue {n} -> {rp}")
    print("classes: " + ", ".join(f"{k} {v}" for k, v in class_counts(res.rows).items()))
    print("annotations (candidates): " + ", ".join(f"{k} {v}" for k, v in annotation_counts(res.rows, candidates_only=True).items()))
    print("annotations (all): " + ", ".join(f"{k} {v}" for k, v in annotation_counts(res.rows).items()))
    by_type: dict[str, int] = defaultdict(int)
    for _rid, note in res.weak_failures:
        by_type[note.split(":", 2)[1].strip() if note.count(":") >= 2 else note] += 1
    print(f"weak task failures: {len(res.weak_failures)}"
          + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())) + ")" if by_type else ""))
    crashes = sorted({f for f, r in res.rows.items() if (r.weak or {}).get("code") == WEAK_CRASHED}
                     | set(res.sibling_crashes))
    if crashes:
        print(f"WARNING: {len(crashes)} weak task(s) crashed in our code (not a data limit): {', '.join(crashes[:20])}")
    outs = [out / "timebase.csv", out / "timebase_providers.csv", rp]
    if weak is not None:
        outs.append(out / "timebase_weak_inputs.json")
    m = write_manifest(out, repo, argv, inputs, status="complete", started=started, outputs=outs, run=run_m,
                       extra={"config": config, "caches": caches, "weak_failures": [list(x) for x in res.weak_failures],
                              "weak_crashes": crashes,
                              "sibling": res.sibling_counts,
                              "counts": {"records": len(res.rows), "residue": n, "rounds": res.rounds}})
    print(f"manifest: {out / MANIFEST} (commit {m['commit'][:12]}{', dirty' if m['dirty'] else ''})")
    return 3 if crashes else 0              # a crashed weak pass stops a pipeline (outputs written, naming it)


if __name__ == "__main__":
    sys.exit(main())
