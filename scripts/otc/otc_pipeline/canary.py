"""The per-source convention canary (manifest §2.5, PP §2.5, D8).

Every source is compared with an independent comparator at the same gauges: our own GESLA fit
of the gauge. `canary()` pairs each of the source's sets with one GESLA-fit set (`pair_sets`: an
alias link first, else the nearest within PAIR_RADIUS_KM great-circle km, the build plan's "same
port"), takes |dA| and |dg| of M2, S2, K1 and O1 per pair (the G9 numbers,
`gesla.select_constituents.g9_canary`), and passes when at least `min_gauges` distinct GESLA gauges
are paired, no pair misses one of the four constituents, and the median |dA| and the median |dg|
over every pair and constituent are within 1 cm and 2 deg. Every pair records its distance and how
it was made; a distance pair with a second GESLA candidate almost as near is reported as ambiguous
(reported, not failed).

Both sides carry published phases, which are always Greenwich (UTC). An adapter whose source
serves phases in local time converts them with `to_greenwich` under the convention it declares;
the canary is what confirms that declaration (a wrong `phase_reference` or `utc_offset_hours`
shifts every phase by speed x offset and fails it).

D8 hook: `CanaryResult.convention_canary()` is the `convention.canary` object of the release
(schema 0.3); a failed canary makes every set of the source fall back (tier 3), and in the first
release it fails the whole release (D11). The caller acts on `passed`; the canary never decides
per station.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

from gesla.select_constituents import G9_AMP_TOL_M, G9_CONSTITUENTS, G9_PHASE_TOL_DEG, G9Result, g9_canary

from otc_pipeline.adapter import SourceSet

CANARY_CONSTITUENTS = G9_CONSTITUENTS
PAIR_RADIUS_KM = 5.0        # build plan §4 / A-LINZ: a GESLA gauge within 5 km is the same port
AMBIGUITY_MARGIN_KM = 1.0   # a second candidate within nearest + this is reported as ambiguous
EARTH_RADIUS_KM = 6371.0088
MIN_GAUGES = 3


# --- convention -> Greenwich ----------------------------------------------------------------------

def to_greenwich(phase_deg: float, speed_deg_per_hour: float, convention: Mapping) -> float:
    """A source phase lag in [0, 360) under `convention`, as a Greenwich (UTC) phase lag.

    phase_reference "local" with utc_offset_hours Z (east positive) means the source's time
    origin is local zone time: g_greenwich = g_local - speed * Z. (Checked against NOAA, which
    serves both: Boston M2 phase_local 324.2, phase_GMT 109.2, speed 28.984104, Z = -5.)"""
    ref = convention.get("phase_reference")
    if ref == "greenwich_utc":
        g = float(phase_deg)
    elif ref == "local":
        z = convention.get("utc_offset_hours")
        if not isinstance(z, (int, float)) or isinstance(z, bool) or not math.isfinite(z):
            raise ValueError(f"convention {convention.get('convention_id')!r}: phase_reference 'local' "
                             f"needs a numeric utc_offset_hours (got {z!r})")
        g = float(phase_deg) - float(speed_deg_per_hour) * float(z)
    else:
        raise ValueError(f"convention {convention.get('convention_id')!r}: unknown phase_reference {ref!r}")
    g %= 360.0
    return 0.0 if g >= 360.0 else g     # -1e-17 % 360 is 360.0 in floating point


# --- result ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CanaryPair:
    source_record_id: str
    gesla_record_id: str
    distance_km: float          # great-circle distance between the two station positions
    method: str                 # "alias" (an id link) or "distance" (nearest within the radius)
    ambiguous_with: tuple[str, ...]   # other GESLA candidates within nearest + AMBIGUITY_MARGIN_KM
    g9: G9Result                # per-pair |dA| / |dg| and their medians (the G9 numbers)

    def to_dict(self) -> dict:
        return {"source_record_id": self.source_record_id, "gesla_record_id": self.gesla_record_id,
                "distance_km": round(self.distance_km, 3), "method": self.method,
                "ambiguous_with": list(self.ambiguous_with),
                **{k: v for k, v in self.g9.to_dict().items() if k != "station"}}


@dataclass(frozen=True)
class CanaryResult:
    source: str
    pairs: tuple[CanaryPair, ...]
    unpaired: tuple[str, ...]   # source sets with no alias link and no GESLA set within the radius
    n_gauges: int               # distinct GESLA gauges paired
    median_da_m: float          # over every pair and constituent; NaN with no pair
    median_dg_deg: float
    min_gauges: int
    amp_tol_m: float
    phase_tol_deg: float
    passed: bool
    reasons: tuple[str, ...]    # why it failed; empty when passed

    radius_km: float = PAIR_RADIUS_KM

    @property
    def n_pairs(self) -> int:
        return len(self.pairs)

    @property
    def ambiguous(self) -> tuple[str, ...]:
        """Source sets whose distance pair had a second GESLA candidate almost as near."""
        return tuple(p.source_record_id for p in self.pairs if p.ambiguous_with)

    def convention_canary(self) -> dict:
        """The schema 0.3 `convention.canary` object for this source."""
        d: dict = {"status": "pass" if self.passed else "fail", "gauges": self.n_gauges}
        if math.isfinite(self.median_da_m):
            d["median_amp_diff_m"] = round(self.median_da_m, 5)
            d["median_phase_diff_deg"] = round(self.median_dg_deg, 3)
        return d

    def to_dict(self) -> dict:
        """Strict-JSON safe (a median that does not exist is None)."""
        def r(x: float, nd: int) -> float | None:
            return round(x, nd) if math.isfinite(x) else None
        return {"source": self.source, "passed": self.passed, "reasons": list(self.reasons),
                "n_pairs": self.n_pairs, "n_gauges": self.n_gauges, "min_gauges": self.min_gauges,
                "amp_tol_m": self.amp_tol_m, "phase_tol_deg": self.phase_tol_deg,
                "pair_radius_km": self.radius_km, "ambiguous": list(self.ambiguous),
                "median_da_cm": r(self.median_da_m * 100, 3), "median_dg_deg": r(self.median_dg_deg, 3),
                "unpaired": list(self.unpaired), "pairs": [p.to_dict() for p in self.pairs]}


# --- pairing and the canary -----------------------------------------------------------------------

def _amp_phase(ss: SourceSet) -> dict[str, tuple[float, float]]:
    cs = ss.constant_set or {}
    return {c["name"]: (float(c["amplitude_m"]), float(c["phase_deg"])) for c in cs.get("constituents", [])}


def _rid(ss: SourceSet) -> str:
    return str((ss.constant_set or {}).get("source_record_id"))


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance in km on a sphere of EARTH_RADIUS_KM."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _gesla_id_parts(rid: str) -> tuple[str, str] | None:
    """A GESLA record id `<site>-<code>-<country>-<contributor>` -> (code, contributor)."""
    parts = rid.rsplit("-", 3)
    return (parts[1], parts[3]) if len(parts) == 4 else None


def _alias_ids(ss: SourceSet, source: str) -> tuple[set[str], set[str]]:
    """(the source's own upstream ids, GESLA record ids the set names) from its constant_set and aliases."""
    own = {_rid(ss)}
    named: set[str] = set()
    aliases = ss.station_hint.get("aliases") if isinstance(ss.station_hint, dict) else None
    for k, v in (aliases or {}).items():
        vals = {str(x) for x in (v if isinstance(v, (list, tuple)) else [v])}
        if k == source:
            own |= vals
        elif k == "gesla":
            named |= vals
    return own, named


def _alias_link(ss: SourceSet, source: str, gs: SourceSet) -> bool:
    """True when the source set names this GESLA record (`aliases.gesla`), or the GESLA record is the
    source's own record: contributor `<source>` or `<source>_*` and site code = one of the set's ids
    (NOAA 8443970 <-> boston-8443970-usa-noaa)."""
    own, named = _alias_ids(ss, source)
    grid = _rid(gs)
    if grid in named:
        return True
    parts = _gesla_id_parts(grid)
    if parts is None or not source:
        return False
    code, contributor = parts
    return contributor.split("_")[0].lower() == source.lower() and code in own


def _country(ss: SourceSet) -> str | None:
    c = ss.station_hint.get("country") if isinstance(ss.station_hint, dict) else None
    return c.upper() if isinstance(c, str) and c else None


@dataclass(frozen=True)
class Match:
    source_set: SourceSet
    gesla_set: SourceSet
    distance_km: float
    method: str                       # "alias" or "distance"
    ambiguous_with: tuple[str, ...]   # other candidates within nearest + AMBIGUITY_MARGIN_KM


def pair_sets(source_sets: Sequence[SourceSet], gesla_sets: Sequence[SourceSet], *, source: str = "",
              radius_km: float = PAIR_RADIUS_KM,
              ambiguity_margin_km: float = AMBIGUITY_MARGIN_KM) -> tuple[list[Match], list[str]]:
    """Each source set with a constant_set -> one GESLA set (subordinate-only sets are skipped):
    1. an alias link (`_alias_link`), whatever the distance (the distance is still recorded);
       several links -> the nearest;
    2. else the nearest GESLA set within radius_km great-circle km whose country (alpha-3) is the
       source set's (a cross-border neighbour is a different water body as far as the canary can tell
       cheaply; a side with no country is not checked). Ties: the lower GESLA record id. Other
       candidates within nearest + ambiguity_margin_km are recorded in `ambiguous_with`.
    Returns (matches, unpaired source_record_ids)."""
    matches, unpaired = [], []
    for ss in source_sets:
        if ss.constant_set is None:
            continue
        lat, lon = float(ss.station_hint["lat"]), float(ss.station_hint["lon"])
        cands = sorted((distance_km(lat, lon, float(gs.station_hint["lat"]), float(gs.station_hint["lon"])),
                        _rid(gs), gs) for gs in gesla_sets)
        linked = [c for c in cands if _alias_link(ss, source, c[2])]
        if linked:
            d, _, gs = linked[0]
            matches.append(Match(ss, gs, d, "alias", ()))
            continue
        country = _country(ss)
        near = [c for c in cands if c[0] <= radius_km
                and (country is None or _country(c[2]) is None or _country(c[2]) == country)]
        if not near:
            unpaired.append(_rid(ss))
            continue
        d, _, gs = near[0]
        others = tuple(rid for dd, rid, _ in near[1:] if dd <= d + ambiguity_margin_km)
        matches.append(Match(ss, gs, d, "distance", others))
    return matches, unpaired


def canary(source: str, source_sets: list[SourceSet], gesla_sets: list[SourceSet], *,
           min_gauges: int = MIN_GAUGES, amp_tol_m: float = G9_AMP_TOL_M,
           phase_tol_deg: float = G9_PHASE_TOL_DEG, radius_km: float = PAIR_RADIUS_KM) -> CanaryResult:
    """PP §2.5: pair sets (`pair_sets`); median |dA| and |dg| of M2, S2, K1, O1 over the pairs.
    pass = n_pairs >= min_gauges and both medians within tolerance. Result carries every pair's numbers.

    Phases on both sides must already be Greenwich (see `to_greenwich`)."""
    raw, unpaired = pair_sets(source_sets, gesla_sets, source=source, radius_km=radius_km)
    pairs = tuple(CanaryPair(_rid(m.source_set), _rid(m.gesla_set), m.distance_km, m.method, m.ambiguous_with,
                             g9_canary(_rid(m.source_set), _amp_phase(m.gesla_set), _amp_phase(m.source_set)))
                  for m in raw)
    da = [abs(v) for p in pairs for v in p.g9.da_m.values()]
    dg = [abs(v) for p in pairs for v in p.g9.dg_deg.values()]
    mda = statistics.median(da) if da else math.nan
    mdg = statistics.median(dg) if dg else math.nan
    n_gauges = len({p.gesla_record_id for p in pairs})
    reasons = []
    if n_gauges < min_gauges:
        reasons.append(f"{n_gauges} GESLA gauge(s) paired, at least {min_gauges} needed")
    for p in pairs:
        if p.g9.missing:
            reasons.append(f"{p.source_record_id} ~ {p.gesla_record_id}: missing {', '.join(p.g9.missing)}")
    if da and not mda <= amp_tol_m:
        reasons.append(f"median |dA| {mda * 100:.3f} cm exceeds {amp_tol_m * 100:g} cm")
    if dg and not mdg <= phase_tol_deg:
        reasons.append(f"median |dg| {mdg:.3f} deg exceeds {phase_tol_deg:g} deg")
    return CanaryResult(source, pairs, tuple(unpaired), n_gauges, mda, mdg, min_gauges, amp_tol_m,
                        phase_tol_deg, not reasons, tuple(reasons), radius_km)


__all__ = ["CANARY_CONSTITUENTS", "PAIR_RADIUS_KM", "AMBIGUITY_MARGIN_KM", "MIN_GAUGES", "to_greenwich",
           "distance_km", "CanaryPair", "CanaryResult", "Match", "pair_sets", "canary"]
