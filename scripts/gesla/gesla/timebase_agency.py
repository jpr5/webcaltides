"""Agency constants (FIX-LST): a record's fitted phases against the published harmonic constants of nearby agency stations.

TB-SIB gate C found records published with their declared UTC base although their stamps are local standard time
(SFWMD: EST, -5 h; two USGS Puerto Rico gauges: AST, -4 h) or otherwise offset (Settlement Point 257/257b +86 min).
The audit had no comparator for them: harcon was used only for a record that names its NOAA station. This module
compares a record's pass-1 constants (M2, S2, K1, O1 on the base it publishes, the same binding as timebase_sibling)
with every NOAA station that has published harmonic constants (A-NOAA's harcon cache) within AG_MAX_KM.

One reference: the joint clock shift tau (min, + = the record's tide later) of the four constituents
(timebase_weak.phase_check: >= 2 cm in both, a semidiurnal and a diurnal one; the diurnal pair resolves the
semidiurnal cycle), kept only inside the tide-regime limits: M2 amplitude ratio within AG_RATIO and every constituent
within AG_RESID_MIN of tau. A reference outside them is in other water and says nothing.
The record: the qualifying references must agree on the SIGN of tau; the record's agency shift is then the one of
smallest magnitude (the reference nearest in time). References of both signs give no shift: the record lies between
them in the tide's propagation, as a physical lag does.

Classes (EOT20 is the second, independent source: timebase_sibling.eot_lag_alone, the relaxed gate):
  lead     tau <= -AG_LEAD_MIN: the record leads EVERY agency station within AG_MAX_KM by two hours or more. A gauge
           up a river, inside a lagoon or behind a structure lags the open water; it does not lead all of it by hours.
           Offset evidence on the agency constants alone: disputed, never corrected on it alone.
  agreed   AG_PAIR_MIN <= |tau|, EOT20 within AG_AGREE_MIN of tau and a qualifying reference within AG_AGREE_KM:
           two sources agree on one offset at the record's own site; disputed. EOT20 sees the open water, so an inland
           gauge 14 km up a waterway lags it and the coast station alike (AIW at Nixons Crossroads SC: +54 against
           NOAA 13.8 km off, EOT20 +52; a physical lag, never flagged).
           A lag (tau > 0) is never offset evidence on the agency constants alone: rivers lag (Hudson, the WSV Ems,
           Hunte and Wümme gauges), and so does the upper Elbe low water.
  correct  an agreed or lead offset whose every qualifying reference is within AG_HOUR_MIN of a whole number n != 0 of
           hours, EOT20 within timebase_sibling.FAMILY_TOL_MIN of the same n, and a reference within AG_SAME_KM in the
           same water (M2 ratio within AG_SAME_RATIO, every constituent within AG_SAME_RESID_MIN of tau). Only then
           the audit refits at the corrected hours (pass 2: the near reference within timebase_sibling.ALIGN_MIN, and
           the record's own weak checks, timebase_audit's rule) and corrects; else disputed. Both sources see the open
           water: a physical lag of an inland gauge shifts both alike (SFWMD Miami S25: EST, -5 h, reads -4 h against
           the farthest references with EOT20 agreeing), so the whole hour must hold at the same gauge.
A record a strong comparator verified (official, harcon, sibling series) is an anchor and is not judged here. A
record already disputed keeps its own code (the agency evidence is added to its reason). Every verdict is the
record's own: no family or provider is disputed because some of its members are.

Thresholds (calibrated on fit-4 / full-5d before the overlay run; FIX-LST calib): every fitted record bound to its
published base against every NOAA harcon station within 30 km (13,331 pairs). Known-good records are those a strong
comparator verified or corrected. At 15 km, ratio 0.5..2 and residual 45 min, 673 of them have a qualifying reference,
and their agency shift lies in -48.5 (Vienna MD, Nanticoke River) .. +35.0 min (Cooper River SC). Pairs alone are
much wider: within 15 km a known-good record and one station differ by up to -357 / +324 min, and within 2 km by
up to +-99 (rivers, inlets). This is why the rule takes all references and their sign. AG_LEAD_MIN 120 leaves 71 min
to the worst known-good lead. Lags are never evidence alone (rivers). Records of the gate's named providers that lead
by <= -120 min: 30 at 10 km, 38 at 15 km, 40 at 20 km. 20 km admits known-good lags to +73.5, so 15 km is used.
AG_RESID_MIN 45: canal and lagoon friction moves K1/O1 by up to ~45 min at the SFWMD gauges (s21, s20f). At 30 min,
the known-good range is -48.5..+87, so 45 is not looser at the edges. AG_PAIR_MIN 45 with EOT20 within 15 min:
Settlement Point 257 is +88.5 with EOT20 +85.0; no known-good record has both.
Official extrema (CHS, BSH, RWS, Kartverket, DMI, IMI, LINZ) are not used. Constants fitted to a year of their
predicted high and low waters (heights and zero slope) reproduce NOAA harcon within 16 min (median 4.5, 60 NOAA
stations). But anchored on strongly verified records, the known-good leads reach -108 min (Dordrecht, RWS). That is
past Tennesseehaven (-82), the one gate record they reach, so they would add river false positives and no case.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from . import timebase_weak as tw
from .select_constituents import load_noaa_harcon

AG_MAX_KM = 15.0
AG_RATIO = (0.5, 2.0)
AG_RESID_MIN = 45.0
AG_LEAD_MIN = 120.0
AG_PAIR_MIN = 45.0
AG_AGREE_MIN = 15.0
AG_AGREE_KM = 3.0
AG_HOUR_MIN = 15.0
AG_SAME_KM = 2.0
AG_SAME_RATIO = (0.9, 1.1)
AG_SAME_RESID_MIN = 10.0
FAMILY_TOL_MIN = 10.0            # timebase_sibling.FAMILY_TOL_MIN: EOT20's whole-hour band
KM_PER_DEG = 111.19


@dataclass(frozen=True)
class AgencyRef:
    src: str
    id: str
    lat: float
    lon: float
    consts: Mapping[str, tuple[float, float]]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, a)))


def load_noaa_refs(noaa_cache: str | Path) -> tuple[list[AgencyRef], list[str]]:
    """Every NOAA station of A-NOAA's stations.json with a harcon/<id>.json (metric): (references, skipped notes).
    A missing cache is no references (an empty list), not an error; a bad harcon file is skipped and named."""
    root = Path(noaa_cache)
    sp = root / "stations.json"
    if not sp.is_file():
        return [], [f"{sp}: missing"]
    out, skipped = [], []
    for s in json.loads(sp.read_text()).get("stations", []):
        p = root / "harcon" / f"{s['id']}.json"
        if not p.is_file() or s.get("lat") is None or s.get("lng") is None:
            continue
        try:
            hc = load_noaa_harcon(p)
        except ValueError as e:
            skipped.append(str(e)[:160])
            continue
        consts = {k: hc[k] for k in tw.CORE if k in hc}
        if consts:
            out.append(AgencyRef("noaa", str(s["id"]), float(s["lat"]), float(s["lng"]), consts))
    return out, skipped


class RefIndex:
    """The references with a distance search (a latitude / longitude prefilter, then haversine)."""

    def __init__(self, refs: Sequence[AgencyRef]):
        self.refs = list(refs)
        self.lat = np.array([r.lat for r in self.refs]) if self.refs else np.zeros(0)
        self.lon = np.array([r.lon for r in self.refs]) if self.refs else np.zeros(0)

    def __len__(self) -> int:
        return len(self.refs)

    def near(self, lat: float, lon: float, km: float = AG_MAX_KM) -> list[tuple[AgencyRef, float]]:
        if not self.refs:
            return []
        dlon = np.abs((self.lon - lon + 180.0) % 360.0 - 180.0)
        cos = max(math.cos(math.radians(min(abs(lat) + 0.2, 90.0))), 1e-6)
        idx = np.nonzero((np.abs(self.lat - lat) * KM_PER_DEG <= km + 0.1) & (dlon * KM_PER_DEG * cos <= km + 0.1))[0]
        got = [(self.refs[i], haversine_km(lat, lon, self.refs[i].lat, self.refs[i].lon)) for i in idx]
        return sorted([(r, d) for r, d in got if d <= km], key=lambda x: (x[1], x[0].id))


def ref_shift(consts: Mapping[str, tuple[float, float]], ref: AgencyRef, speeds: Mapping[str, float]) -> dict | None:
    """The joint shift of the record against one reference, with its regime numbers; None outside the limits."""
    c = tw.phase_check(consts, ref.consts, speeds, ratio=AG_RATIO, resid=AG_RESID_MIN, band=0.0)
    if "tau" not in c or c.get("why", "").startswith("constituent lags disagree"):
        return None
    return {"ref": f"{ref.src}:{ref.id}", "tau": float(c["tau"]), "ratio": float(c["ratio"]),
            "resid": max(abs(v) for v in c["res"].values())}


def shift(consts: Mapping[str, tuple[float, float]], near: Sequence[tuple[AgencyRef, float]],
          speeds: Mapping[str, float]) -> dict | None:
    """The record's agency shift (module docstring): None without a qualifying reference."""
    qs = []
    for ref, km in near:
        x = ref_shift(consts, ref, speeds)
        if x is not None:
            qs.append({**x, "km": round(km, 2)})
    if not qs:
        return None
    taus = [q["tau"] for q in qs]
    if all(t < 0 for t in taus):
        tau = max(taus)
    elif all(t > 0 for t in taus):
        tau = min(taus)
    else:
        tau = 0.0
    return {"tau": tau, "refs": qs, "mixed": not (all(t < 0 for t in taus) or all(t > 0 for t in taus))}


def classify(sh: dict | None, eot_lag: float | None) -> dict:
    """{"kind": none | lead | agreed, "hours": n or None (a correction candidate), "why": ...}."""
    if sh is None:
        return {"kind": "none", "hours": None, "why": "no agency station within the limits"}
    tau = sh["tau"]
    # EOT20 sees the open water, so an inland gauge lags EOT20 and the open-water stations alike: a lag both see is
    # an offset only at the reference's own site (AG_AGREE_KM)
    agree = (eot_lag is not None and abs(eot_lag - tau) <= AG_AGREE_MIN
             and any(q["km"] <= AG_AGREE_KM for q in sh["refs"]))
    if tau <= -AG_LEAD_MIN:
        kind = "lead"
    elif abs(tau) >= AG_PAIR_MIN and agree:
        kind = "agreed"
    else:
        return {"kind": "none", "hours": None, "why": f"agency shift {tau:+.1f} min is no offset evidence"}
    n = round(tau / 60.0)
    same = [q for q in sh["refs"] if q["km"] <= AG_SAME_KM and AG_SAME_RATIO[0] <= q["ratio"] <= AG_SAME_RATIO[1]
            and q["resid"] <= AG_SAME_RESID_MIN]
    whole = (n != 0 and all(abs(q["tau"] - 60.0 * n) <= AG_HOUR_MIN for q in sh["refs"])
             and eot_lag is not None and abs(eot_lag - 60.0 * n) <= FAMILY_TOL_MIN and bool(same))
    refs = ", ".join(f"{q['ref']} {q['km']:.1f} km {q['tau']:+.1f}" for q in sh["refs"][:4])
    e = "-" if eot_lag is None else f"{eot_lag:+.1f}"
    why = (f"agency constants: the record {'leads' if tau < 0 else 'lags'} every agency station within "
           f"{AG_MAX_KM:g} km by {abs(tau):.1f} min or more ({refs}); EOT20 {e} min"
           + ("" if kind == "lead" else ", the same offset"))
    return {"kind": kind, "hours": n if whole else None, "why": why,
            "same_ref": min(same, key=lambda q: q["km"])["ref"] if whole else None}
