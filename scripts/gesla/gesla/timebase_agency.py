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


# --- FIX-LST-3: small diurnals, open water, a uniform shift -------------------------------------------------
#
# Small diurnals: a constituent of AG_DIURNAL_FLOOR..2 cm counts when its amplitude ratio is within the pair's limits
# (the semidiurnals keep the 2 cm floor; a 1 cm diurnal is enough to fix the semidiurnal cycle of an M2-only canal tide,
# SFWMD mbts / mdts / s197_t, but a 1 cm M2 must not decide a diurnal marsh tide, CRMS).
# Residual: a reference within AG_LEAD_RESID_KM may differ from the joint shift by up to AG_LEAD_RESID_MIN per
# constituent when its shift is a lead of AG_LEAD_MIN or more over open water (s29_t: Haulover 4.2 km, K1/O1 45.3 min;
# a lead of hours is no frictional distortion). Farther, it would admit Middle Hoopers Island against a station 14.9 km
# up the Chesapeake shore with constituents 59.5 min apart.
# Open water (false-positive review of 7c78442: CRMS 0535 / 0541 lead NOAA stations in inland water that lags the open
# coast by +240..+332): every reference gets its own lag behind open water, open_lag (open_lags()). A record judged
# against a reference in OTHER water than its own is judged against open water through it: the effective shift is
# tau + max(open_lag, 0). In the SAME water (M2 ratio AG_WATER_RATIO and every constituent within AG_WATER_RESID_MIN:
# the record and the reference see one tide) the shift is the record's own: tau. A reference whose open_lag is unknown
# counts only in the same water, or when an undisputed record at it (2 km) confirms its constants (AG_CONFIRM_*).
# Uniform shift (Settlement Point 257: M2 +86, S2 +89, K1 +98, O1 +112 with M2 0.84 is the relocated gauge's response,
# not a clock): a dispute of a shift under AG_LEAD_MIN needs a source whose constituents agree within
# AG_UNIFORM_SPREAD_MIN and whose amplitudes are undamped (first ratio >= AG_UNIFORM_RATIO). A lead of AG_LEAD_MIN or
# more over open water is its own documented reason (no physical response leads the open sea by hours).
AG_DIURNAL_FLOOR = 0.005
AG_LEAD_RESID_MIN = 60.0
AG_LEAD_RESID_KM = 5.0           # the lead residual allowance only for a reference this near (s29_t: Haulover 4.2 km)
AG_WATER_RATIO = (0.9, 1.1)
AG_WATER_RESID_MIN = 15.0
AG_OPEN_KM = 100.0
AG_OPEN_COAST_MIN = 30.0
AG_OPEN_RATIO = (0.1, 10.0)
AG_OPEN_RESID_MIN = 60.0
AG_OPEN_CELL_KM = 25.0
AG_CONFIRM_KM = 2.0
AG_CONFIRM_MIN = 15.0
AG_UNIFORM_SPREAD_MIN = 15.0
AG_UNIFORM_RATIO = 0.9


def joint_lag(a: Mapping[str, tuple[float, float]], b: Mapping[str, tuple[float, float]], speeds: Mapping[str, float],
              *, ratio: tuple[float, float], bands: Sequence[str] = tw.CORE, diurnal_floor: float = AG_DIURNAL_FLOOR,
              semi_floor: float = tw.MIN_AMP, need_both: bool = True) -> dict | None:
    """The joint clock shift of a against b (timebase_weak.lag_est), with the small-diurnal rule above (a constituent
    under 2 cm whose amplitude ratio is outside `ratio` is left out): {"tau", "resid" (the largest constituent offset
    from tau, min), "spread" (max - min of them), "ratio" (the first constituent's amplitude ratio), "res"}; None when
    a band is missing (need_both: >= 3 constituents, a semidiurnal and a diurnal) or the first ratio is outside."""
    dg, w, rat = {}, {}, {}
    for k in bands:
        if k not in a or k not in b:
            continue
        fl = semi_floor if k in tw.SEMI else diurnal_floor
        (af, gf), (ar, gr) = a[k], b[k]
        if af < fl or ar < fl:
            continue
        dg[k], w[k], rat[k] = tw._wrap(gf - gr), ar, af / ar
    for k in [k for k in dg if min(a[k][0], b[k][0]) < tw.MIN_AMP and not ratio[0] <= rat[k] <= ratio[1]]:
        del dg[k], w[k], rat[k]          # a small constituent counts only when its amplitude ratio is consistent
    if not dg:
        return None
    if need_both and (len(dg) < 3 or not any(k in dg for k in tw.SEMI) or not any(k in dg for k in tw.DIURNAL)):
        return None
    first = next(k for k in ("M2", "S2", "K1", "O1") if k in dg)
    if not ratio[0] <= rat[first] <= ratio[1]:
        return None
    tau, res = tw.lag_est(dg, w, speeds)
    v = list(res.values())
    return {"tau": float(tau), "resid": max(abs(x) for x in v), "spread": max(v) - min(v), "ratio": rat[first],
            "res": {k: round(x, 1) for k, x in res.items()}}


def open_lags(refs: Sequence[AgencyRef], sites: Sequence[dict], speeds: Mapping[str, float]) -> dict[str, dict]:
    """Per reference id: {"lag": its lag behind open water (min) or None, "how", "confirmed": the undisputed record that
    confirms its constants, or None}. sites: the bound records, {"id", "lat", "lon", "consts", "eot", "disputed"}.
    The lag: against EOT20 at the nearest site within AG_CONFIRM_KM (its cell within AG_OPEN_CELL_KM; wide gates
    AG_OPEN_RATIO, AG_OPEN_RESID_MIN); a reference within AG_OPEN_COAST_MIN of EOT20 is open coast. Else against the
    nearest open-coast reference within AG_OPEN_KM (the same gates). Else unknown."""
    lat = np.array([x["lat"] for x in sites]) if sites else np.zeros(0)
    lon = np.array([x["lon"] for x in sites]) if sites else np.zeros(0)

    def near_sites(r: AgencyRef) -> list[tuple[float, dict]]:
        if not len(lat):
            return []
        m = np.nonzero((np.abs(lat - r.lat) < 0.03) & (np.abs(lon - r.lon) < 0.03 / max(math.cos(math.radians(r.lat)), 0.1)))[0]
        got = [(haversine_km(r.lat, r.lon, sites[j]["lat"], sites[j]["lon"]), sites[j]) for j in m]
        return sorted([(d, x) for d, x in got if d <= AG_CONFIRM_KM], key=lambda t: (t[0], t[1]["id"]))

    out: dict[str, dict] = {}
    for r in refs:
        ns = near_sites(r)
        lag, how = None, "unknown"
        for d, x in ns:
            e = x.get("eot")
            if not e or e.get("dist_km") is None or float(e["dist_km"]) > AG_OPEN_CELL_KM:
                continue
            j = joint_lag(r.consts, {k: tuple(e[k]) for k in tw.CORE if k in e}, speeds, ratio=AG_OPEN_RATIO)
            if j is not None and j["resid"] <= AG_OPEN_RESID_MIN:
                lag, how = j["tau"], f"eot20 at {x['id']}"
            break
        conf = None
        for d, x in ns:
            if x.get("disputed"):
                continue
            j = joint_lag(x["consts"], r.consts, speeds, ratio=AG_WATER_RATIO)
            if j is not None and abs(j["tau"]) <= AG_CONFIRM_MIN and j["resid"] <= AG_CONFIRM_MIN:
                conf = x["id"]
                break
        out[r.id] = {"lag": lag, "how": how, "confirmed": conf}
    coast = RefIndex([r for r in refs if out[r.id]["lag"] is not None and abs(out[r.id]["lag"]) <= AG_OPEN_COAST_MIN])
    for r in refs:
        if out[r.id]["lag"] is not None:
            continue
        for o, km in coast.near(r.lat, r.lon, AG_OPEN_KM):
            if o.id == r.id:
                continue
            j = joint_lag(r.consts, o.consts, speeds, ratio=AG_OPEN_RATIO)
            if j is not None and j["resid"] <= AG_OPEN_RESID_MIN:
                out[r.id].update(lag=j["tau"], how=f"noaa:{o.id} {km:.0f} km")
                break
    return out


def ref_shift(consts: Mapping[str, tuple[float, float]], ref: AgencyRef, speeds: Mapping[str, float],
              open_lag: Mapping | None = None) -> dict | None:
    """The joint shift of the record against one reference, with its regime numbers and its effective shift against
    open water (eff; see above); None outside the limits or when the reference cannot judge the record."""
    j = joint_lag(consts, ref.consts, speeds, ratio=AG_RATIO)
    if j is None:
        return None
    tau = j["tau"]
    if j["resid"] > (AG_LEAD_RESID_MIN if tau <= -AG_LEAD_MIN else AG_RESID_MIN):
        return None
    same = AG_WATER_RATIO[0] <= j["ratio"] <= AG_WATER_RATIO[1] and j["resid"] <= AG_WATER_RESID_MIN
    ol = dict(open_lag or {"lag": 0.0, "how": "assumed open", "confirmed": None})
    if same:
        eff, via = tau, "same water"
    elif ol.get("lag") is not None:
        eff, via = tau + max(float(ol["lag"]), 0.0), f"open lag {float(ol['lag']):+.1f} ({ol.get('how')})"
    elif ol.get("confirmed"):
        eff, via = tau, f"confirmed by {ol['confirmed']}"
    else:
        return None
    if j["resid"] > AG_RESID_MIN and eff > -AG_LEAD_MIN:
        return None                      # the lead residual allowance is for leads over open water only
    return {"ref": f"{ref.src}:{ref.id}", "tau": tau, "eff": eff, "via": via, "ratio": j["ratio"], "resid": j["resid"],
            "spread": j["spread"]}


def shift(consts: Mapping[str, tuple[float, float]], near: Sequence[tuple[AgencyRef, float]],
          speeds: Mapping[str, float], lags: Mapping[str, Mapping] | None = None) -> dict | None:
    """The record's agency shift (module docstring) over the effective shifts: None without a qualifying reference.
    lags: open_lags() by reference id (None: every reference is taken as open water, the FIX-LST-1 behaviour)."""
    qs = []
    for ref, km in near:
        x = ref_shift(consts, ref, speeds, None if lags is None else lags.get(ref.id, {"lag": None, "confirmed": None}))
        if x is not None and x["resid"] > AG_RESID_MIN and km > AG_LEAD_RESID_KM:
            x = None                     # a far reference with constituents that far apart is other water
        if x is not None:
            qs.append({**x, "km": round(km, 2)})
    if not qs:
        return None
    taus = [q["eff"] for q in qs]
    if all(t < 0 for t in taus):
        tau = max(taus)
    elif all(t > 0 for t in taus):
        tau = min(taus)
    else:
        tau = 0.0
    return {"tau": tau, "refs": qs, "mixed": not (all(t < 0 for t in taus) or all(t > 0 for t in taus))}


def uniform(x: Mapping) -> bool:
    """A uniform, undamped shift (see above): constituents within AG_UNIFORM_SPREAD_MIN, first ratio >= AG_UNIFORM_RATIO."""
    return x.get("spread") is not None and x["spread"] <= AG_UNIFORM_SPREAD_MIN and x.get("ratio", 0.0) >= AG_UNIFORM_RATIO


def classify(sh: dict | None, eot_lag: float | None) -> dict:
    """{"kind": none | lead | agreed, "hours": n or None (a correction candidate), "why": ...}."""
    if sh is None:
        return {"kind": "none", "hours": None, "why": "no agency station within the limits"}
    tau = sh["tau"]
    # EOT20 sees the open water, so an inland gauge lags EOT20 and the open-water stations alike: a lag both see is
    # an offset only at the reference's own site (AG_AGREE_KM), and only a uniform, undamped one (uniform())
    agree = (eot_lag is not None and abs(eot_lag - tau) <= AG_AGREE_MIN
             and any(q["km"] <= AG_AGREE_KM and uniform(q) for q in sh["refs"]))
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
    refs = ", ".join(f"{q['ref']} {q['km']:.1f} km {q['eff']:+.1f}" + ("" if q["via"] == "same water" else
                                                                       f" ({q['tau']:+.1f}, {q['via']})")
                     for q in sh["refs"][:4])
    e = "-" if eot_lag is None else f"{eot_lag:+.1f}"
    why = (f"agency constants: the record {'leads' if tau < 0 else 'lags'} every agency station within "
           f"{AG_MAX_KM:g} km{' (over open water)' if kind == 'lead' else ''} by {abs(tau):.1f} min or more ({refs}); "
           f"EOT20 {e} min" + ("" if kind == "lead" else ", the same offset"))
    return {"kind": kind, "hours": n if whole else None, "why": why,
            "same_ref": min(same, key=lambda q: q["km"])["ref"] if whole else None}


# --- two independent sources (FIX-LST-2) ------------------------------------------------------------
#
# A dispute-only rule, judged per record: the record is disputed when at least two independent sources put it off by
# the same sign, each by TS_LEAD_MIN (a lead) or TS_LAG_MIN (a lag) or more. The sources:
#   E  EOT20 at the record (cell <= timebase_weak.E_MAX_KM; M2 ratio TS_EOT_RATIO; every constituent within
#      TS_EOT_RESID_MIN of the joint shift);
#   S  published sets of OTHER contributors within TS_SITE_KM in the same water (M2 ratio TS_SET_RATIO, every
#      constituent within TS_SET_RESID_MIN), each fitted on the base it publishes, none itself disputed for an offset
#      (TS_OFFSET_CODES), of ANY gauge type: sets at one river site share its river lag, so a River record is not exempt;
#      the median of their shifts is one source;
#   H  the agency shift (shift() above), when its references agree in sign.
# When the record or a reference has no semidiurnal constituent of 2 cm, K1 and O1 alone give the shift (both >= 2 cm,
# K1 ratio in the same limits): a diurnal tide (Alicante, M2 0.02 m).
# A lag needs S among the agreeing sources: EOT20 and the agency stations both see the open water, which an inland gauge
# lags alike (the AIW case above). It never corrects: corrections keep the full standard (classify, pass 2).
#
# Thresholds (calibrated on fit-4 / full-lst before the overlay run; FIX-LST calib3): of 1,289 strongly verified or
# corrected records, 502 have two or more sources. With the rule they flag none. Their largest second-largest same-sign
# shifts: +45.0 (Naples Bay City Dock: EOT20 +45.0, sets +48.5, the known 48.5-min harbour lag), +42.5 (Corte Madera
# Creek), then -10.5 (Peace River). So a lead of 45 min leaves 34 min, and a lag needs 55 min (10 above Naples Bay).
# Without TS_OFFSET_CODES, Boynton Beach NOAA (verified) is flagged against SFWMD s41_t, an EST record: an offset record
# is never a reference.
TS_LEAD_MIN = 45.0
TS_LAG_MIN = 55.0
TS_SITE_KM = 3.0
TS_EOT_RATIO = (0.80, 1.25)
TS_EOT_RESID_MIN = 30.0
TS_SET_RATIO = (0.75, 1.33)
TS_SET_RESID_MIN = 15.0
TS_OFFSET_CODES = ("agency_constants_offset", "eot20_whole_hour", "sibling_constants_offset", "eot20_family_offset",
                   "two_source_offset", "eot20_regional_offset")
TS_LEAD_SET_KM = 5.0             # FIX-LST-3: a verified set may judge a lead of AG_LEAD_MIN or more from this far
TS_LOCK_KM = 1.0                 # FIX-LST-3: a set across a structure (lock_pair)
TS_SAME_SITE_KM = 5.0            # FIX-LST-3: a set this near an H reference, in its water, is the same source
DIURNAL = ("K1", "O1")


def pair(a: Mapping[str, tuple[float, float]], b: Mapping[str, tuple[float, float]], speeds: Mapping[str, float], *,
         ratio: tuple[float, float], resid: float) -> dict | None:
    """joint_lag within `resid`, with "how" all; or, when neither record has a semidiurnal of 2 cm, K1 and O1 alone
    ("diurnal": both >= 2 cm, K1 ratio within `ratio`). None outside the limits."""
    j = joint_lag(a, b, speeds, ratio=ratio)
    if j is not None:
        return {**j, "how": "all"} if j["resid"] <= resid else None
    big = lambda k: k in a and k in b and a[k][0] >= tw.MIN_AMP and b[k][0] >= tw.MIN_AMP
    if any(big(k) for k in tw.SEMI) or not all(big(k) for k in DIURNAL):
        return None
    j = joint_lag(a, b, speeds, ratio=ratio, bands=DIURNAL, need_both=False, diurnal_floor=tw.MIN_AMP)
    if j is None or j["resid"] > resid or len(j["res"]) < 2:
        return None
    return {**j, "how": "diurnal"}


def lock_pair(a: Mapping[str, tuple[float, float]], b: Mapping[str, tuple[float, float]], speeds: Mapping[str, float], *,
              ratio: tuple[float, float], resid: float) -> dict | None:
    """Across a structure (FIX-LST-3, s79_t): when the full comparison fails on its semidiurnal amplitude ratio (the
    semidiurnal does not pass the lock: aliased), K1 and O1 alone (both >= 2 cm, K1 ratio within `ratio`)."""
    if pair(a, b, speeds, ratio=ratio, resid=resid) is not None:
        return None
    j = joint_lag(a, b, speeds, ratio=ratio, bands=DIURNAL, need_both=False, diurnal_floor=tw.MIN_AMP)
    if j is None or j["resid"] > resid or len(j["res"]) < 2:
        return None
    return {**j, "how": "diurnal across a structure"}


def joint_shift(a: Mapping[str, tuple[float, float]], b: Mapping[str, tuple[float, float]], speeds: Mapping[str, float],
                *, ratio: tuple[float, float], resid: float) -> tuple[float, str] | None:
    """(tau min, "all" | "diurnal") of a against b (pair()), or None outside the limits."""
    j = pair(a, b, speeds, ratio=ratio, resid=resid)
    return None if j is None else (float(j["tau"]), j["how"])


def two_sources(sources: Mapping[str, float]) -> tuple[int, list[str]] | None:
    """(sign, the agreeing sources) when two or more put the record off by the same sign (a lag: S among them)."""
    for sign in (-1, 1):
        lim = TS_LEAD_MIN if sign < 0 else TS_LAG_MIN
        agree = sorted(k for k, v in sources.items() if v * sign >= lim)
        if len(agree) >= 2 and (sign < 0 or "S" in agree):
            return sign, agree
    return None


# --- EOT20 confirmed by the sea's other gauges (FIX-LST-3) ------------------------------------------------------------
#
# A microtidal sea (the Black Sea: M2 2 cm) has no agency station and few same-site sets, and a clock offset there makes
# the prediction worse than none (TB-SIB gate C re-run: the Bulgarian CMEMS sets ptm3066, pomorie and balchik +2.5..2.9 h
# against EOT20). A Coastal record is disputed when EOT20 puts it off by RB_MIN or more in BOTH bands (the semidiurnal
# M2/S2 and the diurnal K1/O1 shift each within RB_RESID_MIN, the semidiurnal cycle resolved by the diurnal, the two
# within RB_BAND_MIN of each other) and at least one gauge of another provider (timebase_sibling.family_of: contributor,
# country, site-code shape) within RB_CONTROL_KM, Coastal, not itself disputed for an offset, agrees with EOT20 within
# RB_CONTROL_MIN on its semidiurnal band: EOT20 is right in that sea, so the record is not. Dispute only, per record.
# Constituents down to AG_DIURNAL_FLOOR count (both bands) when their amplitude ratio is within RB_RATIO.
# Only a microtidal record (RB_MAX_AMP) in the open water EOT20 models (each band's amplitude ratio RB_REGIME_RATIO).
# Vetoes (EOT20, not the record, is off there): a record a strong comparator verified within RB_CONTROL_KM that EOT20
# puts off the same way; or another provider's record within RB_SITE_KM that EOT20 puts off by the same amount (within
# RB_SAME_MIN): two providers agreeing with each other against EOT20 is the site, not a clock.
# Calibration (fit-4 / full-lst, every bound record judged, FIX-LST-3 calib4d): without the amplitude cap the rule
# flags 41-56 records outside the gate's list, nearly all macrotidal bays and estuaries (Bay of Fundy, Dollard, Wadden,
# Charlotte Harbor) whose physical lag behind the EOT20 cell is 2-4 h, and known-good Lox River; with RB_MAX_AMP 0.10 m,
# RB_REGIME_RATIO and the strong-record veto, 0 of 1,289 known-good records and the three Bulgarian sets plus Messina
# (ISPRA, CMEMS RMN) and Porquerolles before the same-site veto. RB_RESID_MIN 90: the Black Sea's 1-cm diurnals
# (balchik K1 +69, O1 -83 min around the band shift; pomorie +40 / -65).
RB_MIN = 120.0
RB_RESID_MIN = 90.0
RB_BAND_MIN = 60.0
RB_RATIO = (0.5, 2.0)
RB_CONTROL_KM = 100.0
RB_CONTROL_MIN = 24.0
RB_CELL_KM = 15.0
RB_MAX_AMP = 0.10                # m: microtidal only (M2 and K1 of the record and of EOT20 below it)
RB_REGIME_RATIO = (0.67, 1.5)    # each band's first amplitude ratio against EOT20: the open water EOT20 models
RB_SITE_KM = 5.0
RB_SAME_MIN = 30.0
M2_PERIOD_MIN = 745.236          # 360 deg / 28.9841042 deg/h, in minutes


def band_lags(consts: Mapping[str, tuple[float, float]], eot: Mapping | None, speeds: Mapping[str, float]) -> dict | None:
    """{"semi", "diurnal"} band shifts (min) of a record against EOT20 at its cell (each a joint_lag dict), the
    semidiurnal one moved by whole M2 cycles to the one nearest the diurnal shift; None without a valid cell."""
    if not eot or eot.get("dist_km") is None or float(eot["dist_km"]) > RB_CELL_KM:
        return None
    ref = {k: tuple(eot[k]) for k in tw.CORE if k in eot}
    kw = dict(ratio=RB_RATIO, need_both=False, diurnal_floor=AG_DIURNAL_FLOOR, semi_floor=AG_DIURNAL_FLOOR)
    s = joint_lag(consts, ref, speeds, bands=tw.SEMI, **kw)
    d = joint_lag(consts, ref, speeds, bands=DIURNAL, **kw)
    if s is not None and d is not None:
        k = round((d["tau"] - s["tau"]) / M2_PERIOD_MIN)
        s = {**s, "tau": s["tau"] + k * M2_PERIOD_MIN}
    return {"semi": s, "diurnal": d}


def microtidal(consts: Mapping[str, tuple[float, float]], eot: Mapping | None) -> bool:
    """M2 and K1 below RB_MAX_AMP in the record and in EOT20 at its cell."""
    amps = [consts.get(k, (0.0, 0.0))[0] for k in ("M2", "K1")] + [float((eot or {}).get(k, [0.0])[0]) for k in ("M2", "K1")]
    return max(amps) < RB_MAX_AMP


def regional_offset(bl: dict | None) -> float | None:
    """The record's EOT20 shift when both bands put it off by RB_MIN or more, the same way, each band undamped and
    unamplified against EOT20 (RB_REGIME_RATIO; see above); else None."""
    if not bl or bl["semi"] is None or bl["diurnal"] is None:
        return None
    s, d = bl["semi"], bl["diurnal"]
    if not all(RB_REGIME_RATIO[0] <= x["ratio"] <= RB_REGIME_RATIO[1] for x in (s, d)):
        return None
    if s["resid"] > RB_RESID_MIN or d["resid"] > RB_RESID_MIN:
        return None
    if abs(s["tau"]) < RB_MIN or abs(d["tau"]) < RB_MIN or (s["tau"] > 0) != (d["tau"] > 0):
        return None
    if abs(s["tau"] - d["tau"]) > RB_BAND_MIN:
        return None
    return (s["tau"] + d["tau"]) / 2.0


def control_ok(bl: dict | None) -> float | None:
    """A control gauge's semidiurnal shift against EOT20 when it is within RB_CONTROL_MIN (the cycle nearest 0); else None."""
    if not bl or bl["semi"] is None or bl["semi"]["resid"] > RB_RESID_MIN:
        return None
    t = bl["semi"]["tau"]
    t -= round(t / M2_PERIOD_MIN) * M2_PERIOD_MIN
    return t if abs(t) <= RB_CONTROL_MIN else None
