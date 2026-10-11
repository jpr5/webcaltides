"""NOAA subordinate stations as a confirm-only (b) reference for the time-base audit (slot G04f).

A NOAA subordinate (type S) station has no harmonic constants of its own: its high/low predictions
are its reference station's high/low predictions with a time offset (and a height offset or ratio)
for highs and one for lows (NOAA Tide Tables, explanation of Table 2: "the hours and minutes to be
added to or subtracted from the time of high or low water at some reference station"). The audit
builds them the same way: the reference station's official NOAA high/low predictions from the
harness cache (refs/noaa/events/<ref>.json, UTC), each high moved by timeOffsetHighTide and each low
by timeOffsetLowTide (minutes, A-NOAA's cache/noaa/offsets/<id>.json, NOAA mdapi tidepredoffsets).
Heights are not needed (the comparison is of times).

The offsets act on UTC times directly. Measured (G04f offcheck): for all 156 subordinate stations
near an excluded US record with both files in the cache, reference events + offsets reproduce NOAA's
own published subordinate predictions event for event (100 % within 1 min, median 0.0), including
TWC0379, whose time zone differs from its reference's (a time-zone correction would be 120 min off).

Offsets are per-event averages over a station's history, published to the whole minute, and NOAA
calls the results approximations; so such a reference only CONFIRMS utc_instant within
timebase_audit.SUB_BAND_MIN and never makes a record unexplained (timebase_audit.CONFIRM_ONLY_SOURCES).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SRC = "noaa_sub"


@dataclass(frozen=True)
class Subordinate:
    station_id: str
    ref_id: str
    lat: float
    lon: float
    high_min: float         # timeOffsetHighTide
    low_min: float          # timeOffsetLowTide
    offsets_path: str
    ref_events_path: str


def load_subordinates(noaa_cache_dir: str | Path, ref_events_dir: str | Path) -> list[Subordinate]:
    """Every S station of A-NOAA's station list with an offsets file that names the same reference
    station, both time offsets, and official reference events in the harness cache."""
    root = Path(noaa_cache_dir)
    spath = root / "stations.json"
    if not spath.exists():
        # a missing list would silently drop every subordinate comparator: refuse instead
        raise FileNotFoundError(f"NOAA station list {spath} not found (A-NOAA's cache): the NOAA subordinate "
                                f"comparators need it; point OTC_WORK at the cache, or give an empty list")
    out = []
    for s in json.loads(spath.read_text()).get("stations", []):
        if s.get("type") != "S":
            continue
        sid = str(s["id"])
        op = root / "offsets" / f"{sid}.json"
        if not op.exists():
            continue
        o = json.loads(op.read_text())
        ref = str(o.get("refStationId") or "")
        if not ref or ref != str(s.get("reference_id") or ref):
            raise ValueError(f"{op}: reference {ref!r} differs from the station list's {s.get('reference_id')!r}")
        hi, lo = o.get("timeOffsetHighTide"), o.get("timeOffsetLowTide")
        rp = Path(ref_events_dir) / f"{ref}.json"
        if hi is None or lo is None or not rp.exists() or s.get("lat") is None or s.get("lng") is None:
            continue
        out.append(Subordinate(sid, ref, float(s["lat"]), float(s["lng"]), float(hi), float(lo), str(op), str(rp)))
    return out


def subordinate_events(sub: Subordinate, ref_events: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """The subordinate's (unix s, +1 High / -1 Low): each reference event moved by its type's offset."""
    out = [(int(round(t + 60.0 * (sub.high_min if k > 0 else sub.low_min))), k) for t, k in ref_events]
    return tuple(sorted(out))
