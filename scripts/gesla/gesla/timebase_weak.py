"""Weak time-base evidence for records the comparators leave without a verdict (G04d).

Owner rule (2026-10-08): no record is dropped. A record the audit cannot verify is published with
GESLA's declared time base (TIME ZONE HOURS) and says so. Weak evidence is used to mark a record
verified, or to correct it when two independent kinds of evidence agree. In doubt, the declared
value is kept. The rule below was written before any G04d scoring (the thresholds are G04c's
model-comparator measurement or set from first principles) and is not tuned on the results.

Three checks, all from the record's own trial fit over its audit span, on the stamps as recorded:

E  EOT20 (nearest valid 1/8-degree cell; G04c STRICT gates): >= 3 of M2/S2/K1/O1 with amplitude
   >= 2 cm in the fit and the model, a semidiurnal and a diurnal one; M2 amplitude ratio 0.95..1.05;
   cell <= 15 km; every constituent within 30 min of the joint lag tau. Gate fails -> n/a.
   |tau - 60 n| <= 10 min -> n; else "off". EOT20 cannot resolve sub-hour offsets, and harbour
   propagation can fake whole hours (G04c: 2.4 %), so E alone never verifies or corrects.
S  served constants: EVERY webcaltides station of the record (all G07a station-map rows ->
   ticon.json, in station-id order). Gates: as E but M2 ratio 0.90..1.10 and every constituent within
   10 min of tau_S. |tau_S - 60 n| <= 6 min -> n; else "off". Each station is classed (served_kind):
     independent  - its origin record is of another data lineage (lineage_reason: two originators of
                    different lineage groups) AND that origin record has a strong verified time base
                    (official, harcon, sibling or provider; origin_status "verified");
     dependent    - same lineage, or a lineage that cannot be established (a redistributor on either
                    side, an unknown role): a consistency check only, never evidence to verify or correct;
     unqualified  - another lineage, but the origin's own time base is not verified (unverified,
                    disputed, corrected, not audited): its constants are not used at all (S n/a).
   All determined S values of the independent and dependent stations must agree, else the record is
   disputed (served_stations_disagree). The station used is an independent determined one if any,
   else a dependent one; its id, origin and class are in the evidence.
D  DST self-test (no model): M2 phase of a summer fit minus a winter fit (May-Aug vs Nov-Feb,
   swapped south of the equator, >= 45 days of data each, counted as a duration from the record's
   sampling interval), as a lag in minutes, only when M2 >= 0.20 m. The zone (timezonefinder) must
   be known, else n/a. |s| <= 20 -> no_step; 40 <= s <= 80 and the zone has DST -> step; else
   ambiguous; too little tide, a season too short, or a failed seasonal fit (dst_fit_failed) -> n/a.

Combination (n_decl: the declared whole hours, 0 = UTC):
 1. D = step: hypothesis local_dst:<zone>; the corrected series is checked again (E', independent
    S'). corrected iff E' = 0 or S' = 0 and neither says another value; else disputed.
 2. Otherwise: contradiction (E and S both determined and different; S = off; S = n != n_decl
    without rule c) -> disputed.
    verified iff D != ambiguous, no contradiction and E = S = n_decl with S INDEPENDENT (both weak
    checks agree and the served one is of another, verified lineage). E = S = n_decl with a dependent
    S is unverified:eot20_only_served_dependent (amendment 2, 2026-10-09: a dependent S is about 0 by
    construction, so that was EOT20 alone).
    Amended 2026-10-08 (before the first full G04d run was accepted): the second verification path
    "S = n_decl independent with D = no_step" is removed. On the controls it verified 2 of 568 records
    shifted +17 min (served lag near the 6-min band edge; the DST test says nothing about a constant
    offset); precision first, 21 correct verifications on the controls are the accepted cost.
    corrected to utc_plus_n iff E = S = n != n_decl, S independent, D = no_step.
    Else unverified.
Lineage groups (lineage_group): X_HIST = X; UHSLC_FD = UHSLC_RQ = UHSLC; JODC_* = JODC; MI_C = MI_R
= MI; DA_* = DA. A redistributor (inventory role: CMEMS, UHSLC_*, JODC_*, CDWR, CRMS, BfG, CM; and the
aggregators GLOSS and ESEAS, which the inventory lists as originators) is never
independent of anything: it may carry any national network's data (CMEMS aggregates RWS, BODC, ...;
UHSLC ingests JODC/JMA), so whose lineage it is cannot be established from the inventory.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from .fit import FitError, lsq
from typing import Sequence

from .qc import clean, identity_correction
from .record import GaugeRecord
from .select_constituents import analyse, between, in_tcd_years
from .tcd_table import TcdTable

CORE = ("M2", "S2", "K1", "O1")
SEASON_NAMES = ("M2", "S2", "N2", "K1", "O1")
SEMI, DIURNAL = ("M2", "S2"), ("K1", "O1")
MIN_AMP = 0.02                   # m, fit and reference
E_RATIO, E_MAX_KM, E_RESID, E_BAND = (0.95, 1.05), 15.0, 30.0, 10.0
S_RATIO, S_RESID, S_BAND = (0.90, 1.10), 10.0, 6.0
D_MIN_M2, D_NO_STEP, D_STEP, D_MIN_HOURS = 0.20, 20.0, (40.0, 80.0), 45 * 24
TAU_RANGE = 780.0                # minutes searched (+-13 h)

NA, OFF = "n/a", "off"


# --- inputs --------------------------------------------------------------------------------------

def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _suffix(file_id: str) -> str:
    return file_id.rsplit("-", 1)[-1].lower()


@dataclass(frozen=True)
class Served:
    station: str
    origin: str                                   # the GESLA record the served station came from
    consts: dict                                  # name -> (amplitude m, Greenwich phase deg)


def load_served(ticon_json: str | Path, station_map_csv: str | Path) -> dict[str, tuple[Served, ...]]:
    """Per GESLA file id: EVERY webcaltides (ticon.json) station of its G07a station-map rows, sorted by
    station id (a record can sit on several stations; the row order of the map decides nothing)."""
    stations = {s["id"]: s for s in json.loads(Path(ticon_json).read_text())["stations"]}
    out: dict[str, dict[str, Served]] = {}
    with open(station_map_csv, newline="") as fh:
        for r in csv.DictReader(fh):
            s = stations.get(r["id"])
            if s is None or not r.get("file_id"):
                continue
            consts = {c["name"]: (float(c["amp"]), float(c["phase"])) for c in s.get("constituents", [])}
            out.setdefault(r["file_id"], {})[r["id"]] = Served(r["id"], r.get("origin_40") or "", consts)
    return {f: tuple(v[k] for k in sorted(v)) for f, v in out.items()}


@dataclass(frozen=True)
class Lineage:
    contributor: str
    role: str                                     # originator | redistributor | "" (unknown)


REDISTRIBUTOR = "redistributor"
# Listed as originators in data/gesla/contributor_roles.csv, but they aggregate national agencies' gauges
# (GLOSS: the global core network; ESEAS: European sea-level data), so for lineage they are redistributors.
AGGREGATORS = frozenset({"GLOSS", "ESEAS"})
LINEAGE_GROUPS = {"UHSLC_FD": "UHSLC", "UHSLC_RQ": "UHSLC", "JODC_GIAJ": "JODC", "JODC_JCG": "JODC",
                  "JODC_JMA": "JODC", "JODC_PAHB": "JODC", "MI_C": "MI", "MI_R": "MI", "DA_IDH": "DA",
                  "DA_MM": "DA", "DA_SAT": "DA"}


def lineage_group(contributor: str) -> str:
    c = contributor.upper()
    c = c[:-5] if c.endswith("_HIST") else c
    return LINEAGE_GROUPS.get(c, c)


def _lineage(file_id: str, lineages: Mapping[str, Lineage] | None) -> Lineage:
    got = (lineages or {}).get(file_id)
    return got if got is not None else Lineage(_suffix(file_id).upper(), "")


def lineage_reason(file_id: str, served: Served, lineages: Mapping[str, Lineage] | None = None) -> str | None:
    """None when the served station's origin record is of another data lineage than the record; else
    why it is not (or cannot be shown to be). Unknown means dependent."""
    if not served.origin:
        return "served station has no origin record"
    if served.origin == file_id:
        return "origin is the record itself"
    a, b = _lineage(file_id, lineages), _lineage(served.origin, lineages)
    for x, fid in ((a, file_id), (b, served.origin)):
        if not x.role:
            return f"lineage of {fid} unknown (not in the inventory)"
        if x.role == REDISTRIBUTOR or x.contributor.upper() in AGGREGATORS:
            return f"{fid} is from a redistributor ({x.contributor}): its originator cannot be established"
    if lineage_group(a.contributor) == lineage_group(b.contributor):
        return f"same lineage group {lineage_group(a.contributor)} ({a.contributor}, {b.contributor})"
    return None


def independent(file_id: str, served: Served, lineages: Mapping[str, Lineage] | None = None) -> bool:
    """The served station's origin is of another data lineage (lineage_reason is None)."""
    return lineage_reason(file_id, served, lineages) is None


ORIGIN_VERIFIED = "verified"


@dataclass(frozen=True)
class ServedRow:
    served: Served
    kind: str                                     # independent | dependent | unqualified
    why: str = ""


def served_kind(file_id: str, served: Served, lineages: Mapping[str, Lineage] | None,
                origin_status: Mapping[str, str] | None) -> ServedRow:
    """Class one served station (module docstring): lineage first, then the origin's own status."""
    why = lineage_reason(file_id, served, lineages)
    if why is not None:
        return ServedRow(served, "dependent", why)
    st = (origin_status or {}).get(served.origin, "not audited")
    if st != ORIGIN_VERIFIED:
        return ServedRow(served, "unqualified", f"origin {served.origin} time base is {st}, not verified")
    return ServedRow(served, "independent", "")


@dataclass
class WeakInputs:
    """Prepared per-record inputs (prepare()): EOT20 at the nearest valid cell and the zone."""
    records: dict
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "WeakInputs":
        d = json.loads(Path(path).read_text())
        meta = dict(d.get("meta", {}), sha256=sha256_file(path))
        return cls(d["records"], meta)

    def has(self, file_id: str) -> bool:
        return file_id in self.records

    def eot(self, file_id: str) -> dict | None:
        return (self.records.get(file_id) or {}).get("eot")

    def zone(self, file_id: str) -> str | None:
        return (self.records.get(file_id) or {}).get("zone")


def prepare(inventory_csv: str | Path, eot_dir: str | Path, out: str | Path, extra: Mapping[str, tuple] = ()) -> Path:
    """EOT20 amplitude/phase (m, deg) at the nearest valid ocean cell of every inventory record, and
    the record's time zone at its position. Needs h5py and timezonefinder (not in the fit's
    dependencies: run with `uv run --with h5py --with timezonefinder==9.0.0`)."""
    import h5py
    from timezonefinder import TimezoneFinder

    eot_dir = Path(eot_dir)
    grids, files = {}, {}
    for n in SEASON_NAMES:
        p = eot_dir / f"{n}_ocean_eot20.nc"
        files[p.name] = sha256_file(p)
        with h5py.File(p) as f:
            lat, lon = f["lat"][:], f["lon"][:]
            grids[n] = (f["amplitude"][:], f["phase"][:])
    valid = np.ones_like(grids["M2"][0], dtype=bool)
    for n in SEASON_NAMES:
        valid &= np.isfinite(grids[n][0]) & (grids[n][0] > 0)
    dlat, dlon = float(lat[1] - lat[0]), float(lon[1] - lon[0])

    def nearest(la: float, lo: float):
        lo = lo % 360.0
        i0, j0 = int(round((la - lat[0]) / dlat)), int(round((lo - lon[0]) / dlon))
        for r in (2, 4, 8, 16, 32, 64, 128):
            ii = np.arange(max(0, i0 - r), min(len(lat), i0 + r + 1))
            jj = np.arange(j0 - r, j0 + r + 1) % len(lon)
            sub = valid[np.ix_(ii, jj)]
            if not sub.any():
                continue
            I, J = np.nonzero(sub)
            la2, lo2 = np.radians(lat[ii[I]]), np.radians(lon[jj[J]])
            p1, l1 = math.radians(la), math.radians(lo)
            a = np.sin((la2 - p1) / 2) ** 2 + math.cos(p1) * np.cos(la2) * np.sin((lo2 - l1) / 2) ** 2
            d = 2 * 6371.0 * np.arcsin(np.sqrt(a))
            k = int(np.argmin(d))
            if d[k] <= r * dlat * 111.0 * 0.9 or r == 128:    # the radius surely holds the nearest
                return ii[I[k]], jj[J[k]], float(d[k])
        return None

    tf = TimezoneFinder()
    pos = {}
    with open(inventory_csv, newline="") as fh:
        for r in csv.DictReader(fh):
            pos[r["file_id"]] = (float(r["lat"]), float(r["lon"]))
    pos.update({k: tuple(v) for k, v in dict(extra).items()})
    recs = {}
    for fid, (la, lo) in sorted(pos.items()):
        nb = nearest(la, lo)
        eot = None
        if nb is not None:
            i, j, d = nb
            eot = {"dist_km": round(d, 3), "cell": [float(lat[i]), float(lon[j])],
                   **{n: [float(grids[n][0][i, j]) / 100.0, float(grids[n][1][i, j]) % 360.0] for n in SEASON_NAMES}}
        lo180 = (lo + 180.0) % 360.0 - 180.0
        recs[fid] = {"eot": eot, "zone": tf.timezone_at(lat=la, lng=lo180)}
    import timezonefinder
    meta = {"eot20_files": files, "timezonefinder": getattr(timezonefinder, "__version__", "?"),
            "inventory_sha256": sha256_file(inventory_csv)}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "records": recs}, sort_keys=True))
    return out


# --- the checks ----------------------------------------------------------------------------------

def _wrap(x: float) -> float:
    return (x + 180.0) % 360.0 - 180.0


def lag_est(dg: Mapping[str, float], w: Mapping[str, float], speed: Mapping[str, float]) -> tuple[float, dict]:
    """tau (min, stamps later = positive) minimising sum w (1 - cos(dg - speed tau)); residuals in min."""
    taus = np.arange(-TAU_RANGE, TAU_RANGE + 0.25, 0.5)
    cost = np.zeros_like(taus)
    for k, d in dg.items():
        cost += w[k] * (1 - np.cos(np.radians(d - speed[k] * taus / 60.0)))
    tau = float(taus[int(np.argmin(cost))])
    return tau, {k: _wrap(d - speed[k] * tau / 60.0) / speed[k] * 60.0 for k, d in dg.items()}


def phase_check(fit: Mapping[str, tuple[float, float]], ref: Mapping[str, tuple[float, float]] | None,
                speed: Mapping[str, float], *, ratio: tuple[float, float], resid: float, band: float,
                dist_km: float | None = None, max_km: float | None = None) -> dict:
    """{"value": n | "off" | "n/a", ...}: the whole-hour class of the joint lag of fit against ref."""
    if not ref:
        return {"value": NA, "why": "no reference"}
    if max_km is not None and (dist_km is None or dist_km > max_km):
        return {"value": NA, "why": f"reference {dist_km} km away (> {max_km})"}
    dg, w, rat = {}, {}, {}
    for k in CORE:
        if k not in fit or k not in ref:
            continue
        (af, gf), (ar, gr) = fit[k], ref[k]
        if af < MIN_AMP or ar < MIN_AMP:
            continue
        dg[k], w[k], rat[k] = _wrap(gf - gr), ar, af / ar
    semi = [k for k in SEMI if k in dg]
    if len(dg) < 3 or not semi or not any(k in dg for k in DIURNAL):
        return {"value": NA, "why": f"too few constituents ({','.join(dg) or '-'})"}
    r = rat[semi[0]]
    if not ratio[0] <= r <= ratio[1]:
        return {"value": NA, "why": f"{semi[0]} amplitude ratio {r:.3f} outside {ratio[0]}..{ratio[1]}", "ratio": r}
    tau, res = lag_est(dg, w, speed)
    out = {"tau": round(tau, 1), "ratio": round(r, 4), "res": {k: round(v, 1) for k, v in res.items()}}
    if max(abs(v) for v in res.values()) > resid:
        return {**out, "value": NA, "why": f"constituent lags disagree (> {resid} min from tau)"}
    n = int(round(tau / 60.0))
    if abs(tau - 60.0 * n) <= band:
        return {**out, "value": n}
    return {**out, "value": OFF, "why": f"lag {tau:+.1f} min is not within {band} min of a whole hour"}


def zone_has_dst(zone: str | None, year: int) -> bool | None:
    """True / False; None when the zone is missing or not a known IANA zone (unknown, never "no DST")."""
    if not zone:
        return None
    try:
        z = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    jan = dt.datetime(year, 1, 15, 12, tzinfo=z).utcoffset()
    jul = dt.datetime(year, 7, 15, 12, tzinfo=z).utcoffset()
    return jan != jul


def dst_check(season: Mapping[str, dict], speed_m2: float, zone: str | None, year: int) -> dict:
    failed = {k: v["error"] for k, v in season.items() if isinstance(v, dict) and "error" in v}
    if failed:
        return {"value": NA, "code": "dst_fit_failed",
                "why": "dst_fit_failed: " + "; ".join(f"{k} {e}" for k, e in sorted(failed.items()))}
    if "win" not in season or "sum" not in season:
        return {"value": NA, "code": "dst_too_little_data",
                "why": f"a season has less than {D_MIN_HOURS // 24} days of data"}
    zd = zone_has_dst(zone, year)
    if zd is None:
        return {"value": NA, "code": "dst_zone_unknown", "why": f"time zone {zone!r} unknown"}
    aw, gw = season["win"]["M2"]
    as_, gs = season["sum"]["M2"]
    if min(aw, as_) < D_MIN_M2:
        return {"value": NA, "code": "dst_microtidal", "why": f"M2 {min(aw, as_):.3f} m < {D_MIN_M2} m"}
    s = _wrap(gs - gw) / speed_m2 * 60.0
    out = {"season_m2_min": round(s, 1), "zone": zone, "zone_has_dst": zd}
    if abs(s) <= D_NO_STEP:
        return {**out, "value": "no_step"}
    if D_STEP[0] <= s <= D_STEP[1] and out["zone_has_dst"]:
        return {**out, "value": "step"}
    return {**out, "value": "ambiguous", "why": f"summer-winter M2 lag {s:+.1f} min"}


# --- fits ----------------------------------------------------------------------------------------

def _months(t: np.ndarray) -> np.ndarray:
    return (t.astype("datetime64[s]").astype("datetime64[M]").astype(np.int64) % 12) + 1


def season_fits(table: TcdTable, t: np.ndarray, h: np.ndarray, *, lat: float, interval_min: int,
                record_id: str = "") -> dict:
    """The winter (Nov-Feb) and summer (May-Aug) M2 of a cleaned series (swapped south of the equator).
    A season needs D_MIN_HOURS of data, counted as a duration: QC keeps at most one sample per hour, so
    each sample stands for max(interval, 60) minutes. A failed fit is kept as {"error": ...}."""
    m = _months(t)
    win, summ = np.isin(m, (11, 12, 1, 2)), np.isin(m, (5, 6, 7, 8))
    if lat < 0:
        win, summ = summ, win
    season: dict = {}
    names = [n for n in SEASON_NAMES if n in table]
    step_h = max(int(interval_min), 60) / 60.0
    for key, mk in (("win", win), ("sum", summ)):
        if mk.sum() * step_h < D_MIN_HOURS:
            continue
        try:
            f = lsq(table, names, t[mk], h[mk], record_id=f"{record_id}:{key}")
        except (FitError, ValueError, np.linalg.LinAlgError) as e:
            season[key] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            continue
        tn = table.name("M2")
        season[key] = {"M2": (float(f.amplitude_m[tn]), float(f.phase_deg[tn])), "n": int(mk.sum())}
    return season


def fit_features(record: GaugeRecord, table: TcdTable, span: tuple[int, int], correction=identity_correction) -> dict:
    """The trial fit's M2/S2/K1/O1 (amplitude m, phase deg) over span, and the seasonal M2 fits."""
    ser = in_tcd_years(table, between(clean(record, correction), span[0], span[1]))
    try:
        an = analyse(table, ser)
    except FitError:
        raise
    except (ValueError, np.linalg.LinAlgError) as e:   # a data limit of the trial fit (not a bug elsewhere)
        raise FitError(f"{record.record_id}: trial fit: {type(e).__name__}: {str(e)[:200]}") from e
    consts = {}
    for n in CORE:
        tn = table.name(n) if n in table else None
        if tn and tn in an.fit.amplitude_m:
            consts[n] = (float(an.fit.amplitude_m[tn]), float(an.fit.phase_deg[tn]))
    season = season_fits(table, ser.times_s, ser.heights_m, lat=record.lat,
                         interval_min=record.interval_min, record_id=record.record_id)
    year = int(np.datetime64(int(span[1]) - 1, "s").astype("datetime64[Y]").astype(int) + 1970)
    return {"consts": consts, "season": season, "year": year}


def speeds(table: TcdTable) -> dict[str, float]:
    return {n: table.speed(table.name(n)) for n in CORE if n in table}


def served_checks(feat: dict, table: TcdTable, rows: Sequence[ServedRow]) -> list[dict]:
    """S against every served station (unqualified ones are not compared: S n/a, why)."""
    sp = speeds(table)
    out = []
    for r in rows:
        base = {"station": r.served.station, "origin": r.served.origin, "kind": r.kind}
        if r.kind == "unqualified":
            out.append({**base, "value": NA, "why": r.why})
            continue
        c = phase_check(feat["consts"], r.served.consts, sp, ratio=S_RATIO, resid=S_RESID, band=S_BAND)
        out.append({**base, **c, **({"kind_why": r.why} if r.why else {})})
    return out


def select_served(results: Sequence[dict]) -> tuple[dict, bool, str | None]:
    """(S used, S independent, conflict). All determined values of independent and dependent stations
    must agree (else conflict); an independent determined station is used first, then a dependent one."""
    usable = [r for r in results if r.get("kind") in ("independent", "dependent")]
    det = [r for r in usable if r.get("value", NA) != NA]
    if len({str(r["value"]) for r in det}) > 1:
        why = ", ".join(f"{r['station']} ({r['kind']}) {r['value']}" for r in det)
        return {"value": NA, "why": f"served stations disagree: {why}"}, False, why
    for pick in ([r for r in det if r["kind"] == "independent"], det, usable, list(results)):
        if pick:
            return pick[0], pick[0].get("kind") == "independent", None
    return {"value": NA, "why": "no served station"}, False, None


def checks(feat: dict, table: TcdTable, eot: dict | None, served: Sequence[ServedRow], zone: str | None,
           *, missing_inputs: bool = False) -> dict:
    sp = speeds(table)
    if missing_inputs:
        E = {"value": NA, "why": "record missing from the weak inputs (EOT20 / zone)"}
    else:
        e_ref = {k: tuple(eot[k]) for k in CORE if eot and k in eot} if eot else None
        E = phase_check(feat["consts"], e_ref, sp, ratio=E_RATIO, resid=E_RESID, band=E_BAND,
                        dist_km=(eot or {}).get("dist_km"), max_km=E_MAX_KM)
    rows = served_checks(feat, table, served)
    S, s_indep, conflict = select_served(rows)
    D = ({"value": NA, "code": "dst_inputs_missing", "why": "record missing from the weak inputs (zone)"}
         if missing_inputs else dst_check(feat["season"], sp["M2"], zone, feat["year"]))
    return {"E": E, "S": S, "S_independent": s_indep, "S_conflict": conflict, "S_all": rows, "D": D}


# --- the rule ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WeakResult:
    annotation: str          # verified | corrected | disputed | unverified
    label: str               # the class published: n hours as utc_instant / utc_plus_n, or local_dst:<zone>
    code: str                # machine-readable evidence code
    reason: str
    evidence: dict = field(default_factory=dict)


def _hours_label(n: int) -> str:
    return "utc_instant" if n == 0 else (f"utc_plus_{n}" if n > 0 else f"utc_minus_{-n}")


def _v(c: dict | None):
    return (c or {}).get("value", NA)


def combine(E: dict, S: dict, s_indep: bool, D: dict, n_decl: int, zone: str | None,
            E2: dict | None = None, S2: dict | None = None, *, s2_indep: bool | None = None,
            served_conflict: str | None = None, served_all: Sequence[dict] = ()) -> WeakResult:
    """The rule (module docstring). E2/S2: the checks again on the local_dst-corrected series (rule 1
    only); s2_indep: whether S2 is independent (default: as S)."""
    ev = {"E": E, "S": S, "S_independent": s_indep, "D": D}
    if served_all:
        ev["S_all"] = list(served_all)
    decl = _hours_label(n_decl)
    e, s, d = _v(E), _v(S), _v(D)
    if served_conflict:
        return WeakResult("disputed", decl, "disputed:served_stations_disagree",
                          f"the served stations of this record disagree: {served_conflict}", ev)
    if d == "step":
        s_indep = s_indep if s2_indep is None else s2_indep
        ev.update(E_corrected=E2, S_corrected=S2)
        e2, s2 = _v(E2), (_v(S2) if s_indep else NA)
        vals = [x for x in (e2, s2) if x != NA]
        lab = f"local_dst:{zone}"
        if 0 in vals and all(x == 0 for x in vals):
            src = "+".join(n for n, x in (("eot20", e2), ("served", s2)) if x == 0)
            return WeakResult("corrected", lab, f"corrected:{decl}->{lab}:dst_selftest+{src}",
                              f"summer-winter M2 lag {D.get('season_m2_min')} min (DST step); after {lab} the "
                              f"record agrees with {src.replace('+', ' and ')}", ev)
        return WeakResult("disputed", decl, "disputed:dst_step_unconfirmed",
                          f"summer-winter M2 lag {D.get('season_m2_min')} min (DST step) but {lab} is not confirmed "
                          f"(EOT20 {e2}, independent served {s2})", ev)
    det = [x for x in (e, s) if x != NA]
    if (len(det) == 2 and e != s) or s == OFF:
        return WeakResult("disputed", decl, "disputed:weak_checks_disagree",
                          f"EOT20 {e}, served constants {s} (whole hours / off)", ev)
    if isinstance(s, int) and s != n_decl:
        if e == s and s_indep and d == "no_step":
            lab = _hours_label(s)
            return WeakResult("corrected", lab, f"corrected:{decl}->{lab}:eot20+served_independent+dst_selftest",
                              f"EOT20 and independent served constants both lag {s:+d} h; no DST step", ev)
        return WeakResult("disputed", decl, "disputed:served_offset_unconfirmed",
                          f"served constants lag {s:+d} h but that is not confirmed (EOT20 {e}, independent {s_indep}, DST {d})", ev)
    if d != "ambiguous" and e == n_decl and s == n_decl:
        if s_indep:
            return WeakResult("verified", decl, "verified:eot20+served_independent",
                              f"EOT20 (tau {E.get('tau')} min) and the independent served station {S.get('station')} "
                              f"(origin {S.get('origin')}, tau {S.get('tau')} min) agree with {decl}", ev)
        return WeakResult("unverified", decl, "unverified:eot20_only_served_dependent",
                          f"EOT20 (tau {E.get('tau')} min) agrees with {decl}; the served station {S.get('station')} "
                          f"is not independent ({S.get('kind_why') or S.get('kind')}), so it confirms nothing and "
                          f"EOT20 alone cannot verify", ev)
    why = "; ".join(f"{k} {_v(c)}" + (f" ({c['why']})" if c.get("why") else "") for k, c in (("EOT20", E), ("served", S), ("DST", D)))
    return WeakResult("unverified", decl, "unverified:weak_insufficient", f"weak evidence insufficient: {why}"[:400], ev)


def assess(record: GaugeRecord, table: TcdTable, span: tuple[int, int], *, eot: dict | None,
           served: Sequence[ServedRow], zone: str | None, n_decl: int = 0, missing_inputs: bool = False,
           independent_served_only: bool = False) -> WeakResult:
    """Fit, the three checks and the rule for one record (served: every served station, classed).
    independent_served_only: judge only against independent served stations (with EOT20). For an inherited
    class other than the declared one: a dependent served station agrees with the DECLARED base by
    construction, and an unqualified one is not evidence, so neither can test the inherited hours."""
    from .timebase_verdict import Correction

    excluded = []
    if independent_served_only:
        excluded = [{"station": r.served.station, "origin": r.served.origin, "kind": r.kind} for r in served
                    if r.kind != "independent"]
        served = [r for r in served if r.kind == "independent"]

    feat = fit_features(record, table, span)
    c = checks(feat, table, eot, served, zone, missing_inputs=missing_inputs)
    E2 = S2 = None
    s2_indep = None
    if c["D"]["value"] == "step":
        try:
            f2 = fit_features(record, table, span, Correction("local_dst", zone=zone))
            c2 = checks(f2, table, eot, served, zone)
            E2, S2, s2_indep = c2["E"], c2["S"], c2["S_independent"]
            if c2["S_conflict"]:
                S2, s2_indep = {"value": NA, "why": c2["S"]["why"]}, False
        except (FitError, ValueError, np.linalg.LinAlgError) as e:
            E2 = S2 = {"value": NA, "why": f"corrected fit failed: {e}"}
            s2_indep = False
    res = combine(c["E"], c["S"], c["S_independent"], c["D"], n_decl, zone, E2, S2, s2_indep=s2_indep,
                  served_conflict=c["S_conflict"], served_all=c["S_all"])
    if excluded:
        res.evidence["S_excluded"] = excluded
    return res
