"""TICON-4's published constants as format 1.0 analysis sets: the fallback for a GESLA record whose own
OTC fit the build may not publish (owner decision: "we use the best data until we can't, then fall back
to the best data we can use").

    load_ticon(csv_path) -> {TICON record name: TiconRecord}    the SEANOE download, checked by SHA-256
    TiconCarry.from_audit(records, audit, gesla_metadata)        the records with OTC's time-base annotations
    ticon_set(record, time_base=..., table=..., ...) -> dict      one constant set, or NotCarried

The constants are read at build time from the pinned download (SEANOE doi:10.17882/109129, file
122848.csv); they are never committed. Rules (site 23aedbe, sources/ticon-4 and method#third-party):

- source ticon-4, source_type analysis, licence ticon-4 (CC BY 4.0): the schema binds the three together,
  so a CMEMS-relayed TICON record (gesla_source gesla4.CMEMS, CC BY-NC) is never carried.
- Every set carries OTC's time-base annotation of the same GESLA record. verified and unverified: the
  constants as published (unverified with the time_base flag). corrected: OTC moves the phases by the
  correction (and undoes the hourly-mean attenuation for utc_mean_start60), and says so in
  provenance.modified; a correction that is not one constant shift (local_dst, step), or one with bad
  months the publisher's fit still holds, is not carried. disputed: not carried.
- verified holds only where OTC's checked window covers TICON-4's span to within 2 years
  (COVERAGE_SLACK_S); otherwise a verified record's set is annotated unverified, and a corrected one is
  not carried (the correction is known only for the window).
- Names map by line, not by the TICON-4 manual's Table 1. A constituent whose TICON-4 phase convention
  differs from the TCD's (LEFT_OUT, measured against OTC's own fits of the same records), or that the TCD
  the release predicts with does not define, is left out and listed in dropped_constituents (convention).
- Quality gates (quality_gate; owner rule: the best data we can use, never bad data). A set is refused
  (ticon_quality) when OTC's half-record fits of the record disagree (SR-ACC's rule); when TICON-4 lacks a line
  OTC's fit determines well and that moves high/low waters by more than 15 min or 5 cm; when its seasonal terms
  (kept, or SA / SA+SSA dropped only where that is closer) are over 5 cm from an official series' monthly means or
  from OTC's fit; when it is over 5 cm RMS from OTC's withheld fit over the lines both carry and no independent
  reference clearly sides with it (official predictions in the time domain; a constants-only reference only where
  it can see the difference; a neighbour that discriminates by 1 cm); or when a carried major line's TICON-4
  standard error is over 2 cm and neither agreement with OTC's fit nor the deciding reference supports that line.
  OTC's withheld fit is a check only; its lines never enter a TICON-4 set. References come from refs.json
  (otc_pipeline.ticon_refs), whose SHA-256 is in every carried set's provenance.
- TICON-4 amp_std is read in cm, as amp (the file states no unit; the values scale with amp, e.g. N2 58.7 +/- 7.8).
"""
from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from gesla.tcd_table import TcdTable
from gesla.timebase_verdict import boxcar_response

from otc_pipeline.gesla_sets import NotPublishable, clip, declared_hours, time_base_annotation

__all__ = ["CONVENTION_ID", "LEFT_OUT", "LICENCE", "LICENCE_ID", "NAME_MAP", "NotCarried", "SOURCE", "SOURCE_VERSION",
           "TICON_SHA256", "TiconCarry", "TiconRecord", "carried_names", "inconsistent_halves", "load_ticon", "match",
           "neighbour_references", "omission_effect", "quality_gate", "ticon_convention", "ticon_set"]

SOURCE = "ticon-4"
SOURCE_VERSION = "4"
LICENCE_ID = "ticon-4"
RESTRICTED_POLICIES = {"gesla-bodc-nc": "ticon-4-bodc-restricted",
                       "gesla-cmems-nc": "ticon-4-cmems-restricted"}
CONVENTION_ID = "ticon4-greenwich-schureman-v1"
TICON_SHA256 = "cea4a8d11cea3d7a5db02d3b75662c32a1459c262c4427b0eb28bb4fca5de366"   # SEANOE 122848.csv
TICON_COLUMNS = ("lat", "lon", "con", "amp", "pha", "amp_std", "pha_std", "missing_obs", "no_of_obs",
                 "years_of_obs", "start_date", "end_date", "gesla_source", "tide_gauge_name", "type", "country",
                 "record_quality", "datum_information")
CMEMS = "gesla4.CMEMS"         # CMEMS-relayed records: CC BY-NC, never under the ticon-4 licence
NO_ISSUES = "No obvious issues"
SITE_KM = 1.0                  # a site match: a TICON record within 1 km of the GESLA gauge
ATTRIBUTION = ("Contains constants from TICON-4 (DGFI-TUM), CC BY 4.0. Hart-Davis, M., Dettmering, D., Seitz, F. "
               "(2025). TICON-4: TIdal CONstants based on GESLA-4 sea-level records. SEANOE. doi:10.17882/109129.")
LICENCE = {"licence_id": LICENCE_ID, "spdx": "CC-BY-4.0", "provider": "TICON-4 (DGFI-TUM)",
           "citation": "Hart-Davis, M., Dettmering, D., Seitz, F. (2025). TICON-4: TIdal CONstants based on GESLA-4 "
                       "sea-level records. SEANOE. doi:10.17882/109129",
           "attribution": ATTRIBUTION, "url": "https://doi.org/10.17882/109129", "commercial_use": True}

# Source name -> OTC (TCD) name, for the names that differ (site conventions#definitions).
NAME_MAP = {"SGM": "SIG1", "EP2": "EPS2", "LAMBDA2": "LDA2", "LM2": "LDA2", "MI2": "MU2", "NI2": "NU2"}
# TICON-4 source names whose phase convention differs from the TCD's, measured against OTC's own fits of the
# same GESLA records (fit-4, 4,024 records on the declared utc_instant base, both amplitudes > 5 mm; median of
# TICON minus OTC). Left out rather than converted: the schema allows provenance.modified only for a
# time-base correction, so no other change to the publisher's constants can be declared. J1, LAMBDA2 and OO1
# (amplitude 0.92 / 0.93 of OTC's; OO1 on short records) are carried as published: dropping them costs more
# than their bias (TICON-FB data review (4): 2026 RMS over 856 sets, J1 2.2 mm carried against 15.0 dropped).
LEFT_OUT = {
    "M1": "TICON-4 M1 is another definition than the TCD's Schureman composite (phase -112 deg from OTC's fit, "
          "median of 1,310 gauges)",
    "SGM": "TICON-4 SGM (SIG1) phase is on another phase convention (+173 deg from OTC's fit, median of 990 gauges; "
           "-0.4 deg after 180 deg on records of 19 years or more)",
    "2MK5": "TICON-4 2MK5 phase is on another compound convention (-88 deg from OTC's fit, median of 689 gauges)",
    "2MO5": "TICON-4 2MO5 phase is on another compound convention (+90 deg from OTC's fit, median of 516 gauges)",
}


class NotCarried(ValueError):
    """A TICON-4 record that is not carried for a withheld GESLA record; `reason` is the report class."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class TiconRecord:
    name: str                       # tide_gauge_name: the GESLA file name TICON-4 analysed
    lat: float
    lon: float
    gesla_source: str               # e.g. gesla4.MEDS; gesla4.CMEMS is CC BY-NC
    start: str                      # dd/mm/yyyy as published
    end: str
    quality: str                    # record_quality
    constituents: dict = field(default_factory=dict)   # source name -> (amp cm, phase deg)
    amp_std: dict = field(default_factory=dict)        # source name -> TICON-4's amplitude standard error (cm)
    gauge_type: str = ""                               # TICON-4's `type`: Coastal, River, Lake
    n_obs: int = 0                                     # TICON-4's no_of_obs: the samples its analysis used
    country: str = ""                                # original CSV country, including TICON-only identities
    original_rows: list = field(default_factory=list)  # exact original CSV cells, not predictive constituents


def load_ticon(path: Path, sha256: str = TICON_SHA256) -> dict[str, TiconRecord]:
    """The TICON-4 CSV, refused unless its SHA-256 is the pinned download's. One record per tide_gauge_name;
    a name given twice for one record, or a record whose position or source changes between rows, raises."""
    data = Path(path).read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise ValueError(f"{path}: SHA-256 {got}, the pinned TICON-4 download is {sha256}")
    out: dict[str, dict] = {}
    for r in csv.DictReader(data.decode("utf-8").splitlines()):
        name = r["tide_gauge_name"]
        head = (float(r["lat"]), float(r["lon"]), r["gesla_source"], r["start_date"], r["end_date"], r["record_quality"])
        rec = out.setdefault(name, {"head": head, "cons": {}, "std": {}, "type": r.get("type", ""),
                                    "n": int(float(r.get("no_of_obs") or 0)), "country": r["country"], "rows": []})
        if rec["head"] != head:
            raise ValueError(f"{path}: {name}: rows disagree on position, source, span or quality")
        if r["con"] in rec["cons"]:
            raise ValueError(f"{path}: {name}: constituent {r['con']} given twice")
        rec["cons"][r["con"]] = (float(r["amp"]), float(r["pha"]))
        rec["std"][r["con"]] = float(r["amp_std"])
        rec["rows"].append([r[column] for column in TICON_COLUMNS])
    return {n: TiconRecord(n, *v["head"], constituents=v["cons"], amp_std=v["std"], gauge_type=v["type"], n_obs=v["n"],
                            country=v["country"], original_rows=v["rows"])
            for n, v in out.items()}


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def match(record_id: str, lat: float, lon: float, records: Mapping[str, TiconRecord],
          gesla_records: set[str]) -> tuple[TiconRecord | None, str]:
    """The TICON-4 record for one withheld GESLA record: the one of the same file name, else the nearest one
    within SITE_KM whose own file is not a record of this build (that record decides for itself). Returns
    (record or None, how): how is id, site, or the reason there is none (no_ticon, cmems_relayed)."""
    t = records.get(record_id)
    if t is not None:
        return (None, "cmems_relayed") if t.gesla_source == CMEMS else (t, "id")
    near = sorted((_km((lat, lon), (r.lat, r.lon)), n) for n, r in records.items() if abs(r.lat - lat) < 0.05)
    near = [(d, n) for d, n in near if d <= SITE_KM and n not in gesla_records]
    if not near:
        return None, "no_ticon"
    usable = [n for _, n in near if records[n].gesla_source != CMEMS]
    return (records[usable[0]], "site") if usable else (None, "cmems_relayed")


@dataclass(frozen=True)
class TiconCarry:
    """What build_release needs to carry TICON-4: the records, and OTC's time-base annotation of each GESLA
    record a TICON record analysed (file id -> format 1.0 time_base, from the audit the build names)."""
    records: Mapping[str, TiconRecord]
    time_bases: Mapping[str, Mapping]
    audit: str                       # which audit decided time_bases, for the report
    windows: Mapping[str, tuple[str, str]] = field(default_factory=dict)   # file id -> audit's checked span (dates)
    references: Mapping[str, list] = field(default_factory=dict)          # file id -> independent references
    references_sha256: str | None = None                                  # of the refs.json they come from

    @classmethod
    def from_audit(cls, records: Mapping[str, TiconRecord], audit, gesla_metadata: Mapping[str, Mapping],
                   references_path: Path | None = None) -> "TiconCarry":
        """Annotations from a loaded gesla_sets.Audit for every TICON record that is a GESLA record of this
        release (gesla_metadata: the release's metadata rows); a record outside the audit pool gets
        time_base_annotation's unverified. A record whose annotation cannot be made is left out (no time base,
        no set). `references_path`: a refs.json from ticon_refs (the official references the gate uses); its
        SHA-256 goes into every carried set's provenance.input_sha256."""
        tbs, windows = {}, {}
        for name in records:
            meta = gesla_metadata.get(name)
            if meta is None:
                continue
            try:
                tbs[name] = time_base_annotation(audit.rows.get(name), declared_hours(meta), name,
                                                 decided_in=audit.decided_in,
                                                 decided_build_commit=audit.decided_build_commit)
            except NotPublishable:
                continue
            row = audit.rows.get(name) or {}
            if row.get("span_start") and row.get("span_end"):
                windows[name] = (row["span_start"], row["span_end"])
        refs, sha = ({}, None)
        if references_path is not None:
            from otc_pipeline.ticon_refs import load_references
            refs, sha = load_references(references_path)
        return cls(records, tbs, f"{audit.decided_in} {audit.decided_build_commit[:7]}", windows, refs, sha)


def _otc_name(src: str) -> str:
    return NAME_MAP.get(src, src)


def carried_names(records: Mapping[str, TiconRecord], table: TcdTable) -> set[str]:
    """Every OTC name a carried set can hold (for the astro table): TICON names that are not left out and
    that the TCD defines."""
    out = set()
    for r in records.values():
        for src in r.constituents:
            if src not in LEFT_OUT and _otc_name(src) in table:
                out.add(_otc_name(src))
    return out


_SHIFT_S = {"utc_instant": 0}


def _shift_s(label: str) -> int | None:
    """Seconds from a stamp to the UTC instant it stands for (gesla Correction._shift), for the classes that
    are one constant shift; None for local_dst and step."""
    if label in _SHIFT_S:
        return _SHIFT_S[label]
    if label == "utc_mean_start60":
        return 1800
    for pre, sign in (("utc_plus_", -1), ("utc_minus_", 1)):
        if label.startswith(pre):
            return sign * int(label[len(pre):]) * 3600
    return None


def _date(dmy: str, end: bool) -> str:
    d = datetime.strptime(dmy, "%d/%m/%Y").replace(tzinfo=timezone.utc)
    return d.strftime("%Y-%m-%dT23:59:59Z" if end else "%Y-%m-%dT00:00:00Z")


COVERAGE_SLACK_S = 2 * 365.25 * 86400   # OTC's checked window must cover TICON-4's span to within 2 years


def _ts(day: str) -> float:
    return datetime.fromisoformat(day[:10]).replace(tzinfo=timezone.utc).timestamp()


def _covers(window: tuple[str, str] | None, rec: TiconRecord) -> bool:
    if not window:
        return False
    a = datetime.strptime(rec.start, "%d/%m/%Y").replace(tzinfo=timezone.utc).timestamp()
    b = datetime.strptime(rec.end, "%d/%m/%Y").replace(tzinfo=timezone.utc).timestamp()
    return _ts(window[0]) <= a + COVERAGE_SLACK_S and _ts(window[1]) >= b - COVERAGE_SLACK_S


def _uncovered(tb: Mapping, window: tuple[str, str] | None, rec: TiconRecord) -> dict:
    """A verified annotation whose checked window does not cover TICON-4's span, as unverified: the declared
    time base is kept and the reason says what was checked."""
    w = f"{window[0]} to {window[1]}" if window else "no stated window"
    out = {k: tb[k] for k in ("declared", "decided_in", "decided_build_commit", "evidence") if k in tb}
    out.update(code="unverified", published=tb["declared"],
               reason=clip(f"declared {tb['declared']} kept; OTC's time-base check verified it over {w}, which does not "
                           f"cover the span TICON-4 analysed ({rec.start} to {rec.end}) to within 2 years"))
    return out


def ticon_set(rec: TiconRecord, *, time_base: Mapping | None, table: TcdTable, licence_id: str,
              record_licence: str, checked_window: tuple[str, str] | None = None,
              retain_source_values: bool = False) -> dict:
    """One analysis constant set (format 1.0, without set_id and provenance.build, which the build adds) for
    a TICON-4 record, or NotCarried (cmems_relayed, no_time_base, time_base_disputed, time_base_not_shift,
    time_base_months_dropped, no_constituents). `record_licence` is the GESLA record's own licence id, named in
    selection_reason."""
    restricted = retain_source_values and licence_id == RESTRICTED_POLICIES.get(record_licence)
    if retain_source_values and rec.gesla_source == CMEMS and not restricted:
        raise NotCarried(f"{rec.name}: CMEMS-relayed constants need an explicit restricted policy for "
                         f"the actual resolved record licence {record_licence}", "cmems_relayed")
    if rec.gesla_source == CMEMS and not restricted:
        raise NotCarried(f"{rec.name}: CMEMS-relayed (CC BY-NC); the ticon-4 licence does not cover it", "cmems_relayed")
    if licence_id != LICENCE_ID and not restricted:
        raise ValueError(f"{rec.name}: a ticon-4 set has licence {LICENCE_ID}, not {licence_id}")
    if time_base is None:
        raise NotCarried(f"{rec.name}: OTC has no time-base annotation of the record", "no_time_base")
    tb = dict(time_base)
    code = tb["code"]
    if retain_source_values and code == "corrected":
        # The scientifically corrected set remains separate. Original source
        # phases describe the publisher's declared axis, not OTC's correction.
        tb = {k: tb[k] for k in ("declared", "decided_in", "decided_build_commit", "evidence") if k in tb}
        tb.update(code="unverified", published=tb["declared"],
                  reason=clip(f"original TICON-4 phases retained on the declared axis; OTC's time-base "
                              f"correction is not applied to this source set: {dict(time_base)}"))
        code = "unverified"
    if code in ("verified", "corrected") and not _covers(checked_window, rec):
        if code == "corrected":
            raise NotCarried(f"{rec.name}: OTC's correction is known for {checked_window}, not for the span TICON-4 "
                             f"analysed ({rec.start} to {rec.end})", "time_base_not_covered")
        tb = _uncovered(tb, checked_window, rec)
        code = "unverified"
    if code == "disputed" and not retain_source_values:
        raise NotCarried(f"{rec.name}: OTC disputes the record's time base: {tb['reason']}", "time_base_disputed")
    if tb.get("months_dropped") and not retain_source_values:
        raise NotCarried(f"{rec.name}: OTC drops months {tb['months_dropped']}, which the publisher's fit holds",
                         "time_base_months_dropped")
    shift = 0.0
    mean = False
    if code == "corrected":
        a, b = _shift_s(tb["correction"]["from"]), _shift_s(tb["correction"]["to"])
        if a is None or b is None or tb["correction"]["from"] == "utc_mean_start60":
            raise NotCarried(f"{rec.name}: the correction {tb['correction']['from']} -> {tb['correction']['to']} is "
                             "not one constant shift of the phases", "time_base_not_shift")
        shift = (b - a) / 3600.0        # hours from the publisher's time axis to UTC
        mean = tb["correction"]["to"] == "utc_mean_start60"
    cons, dropped = [], []
    for src in sorted(rec.constituents):
        name = _otc_name(src)
        if src in LEFT_OUT and not retain_source_values:
            dropped.append({"name": name, "dropped_reason": "convention", "detail": LEFT_OUT[src]})
            continue
        if name not in table:
            dropped.append({"name": name, "dropped_reason": "convention",
                            "detail": f"TICON-4 {src} has no definition in the TCD the release predicts with"})
            continue
        amp_cm, pha = rec.constituents[src]
        speed = table.speed(name)
        amp = amp_cm / 100.0
        if mean:
            amp /= boxcar_response(speed)
        entry = {"name": name, "speed_deg_per_hour": speed, "amplitude_m": round(amp, 6),
                 "phase_deg": round((pha + speed * shift) % 360.0, 6) % 360.0}
        if retain_source_values:
            entry["amplitude_m"] = amp
            entry["phase_deg"] = pha % 360.0
            entry["amp_uncertainty_m"] = rec.amp_std[src] / 100.0
        if src != name:
            entry["source_name"] = src
        cons.append(entry)
    if not cons:
        raise NotCarried(f"{rec.name}: no constituent the release can predict", "no_constituents")
    flags = []
    if code == "unverified" or (retain_source_values and code == "disputed"):
        flags.append({"flag": "time_base", "reason": clip(f"time base {code}: {tb['reason']}")})
    if rec.quality != NO_ISSUES:
        flags.append({"flag": "source_quality_note", "reason": f"TICON-4 record quality: {rec.quality}"})
    prov = {"input_sha256": [TICON_SHA256],
            "selection_reason": f"the record's licence ({record_licence}) does not allow publishing an OTC fit, "
                                "so the publisher's constants are used"}
    if retain_source_values:
        prov["selection_reason"] = ("original published TICON-4 source values retained separately from "
                                    "OTC's scientific selection; unsupported definitions remain original source data")
    if code == "corrected":
        prov["modified"] = (f"phases corrected for the record's time base ({tb['correction']['to']}, declared "
                            f"{tb['correction']['from']}): each phase changed by the constituent's speed times "
                            f"{shift:+g} h" + ("; amplitudes divided by the hourly-mean response" if mean else ""))
    cs = {"source": SOURCE, "source_type": "analysis", "quantity": "water_level", "source_record_id": rec.name,
          "source_version": SOURCE_VERSION,
          "record_span": {"start": _date(rec.start, False), "end": _date(rec.end, True)},
          "convention_id": CONVENTION_ID, "licence_id": licence_id, "qc_status": "flagged" if flags else "ok",
          "time_base": tb, "constituents": cons, "provenance": prov}
    if flags:
        cs["qc_flags"] = flags
    if dropped:
        cs["dropped_constituents"] = dropped
    return cs


def ticon_convention(table_id: str, table: TcdTable, canary: Mapping | None = None) -> dict:
    """TICON-4's convention: Greenwich phases that a reader predicts with the TCD's V0+u and f (the astro table
    `table_id`), as OTC's fits; `canary` is the per-source check against OTC's own fits."""
    conv = {"convention_id": CONVENTION_ID, "phase_reference": "greenwich_utc", "v0_model": "schureman_tcd",
            "nodal_handling": "f_u_at_prediction", "constituent_table_version": table_id,
            "tables_sha256": table.tcd_sha256, "astro_table_id": table_id}
    if canary is not None:
        conv["canary"] = dict(canary)
    return conv


# --- quality gates (TICON-FB fix rounds; owner rule: the best data we can use, never bad data) ------------

MAJOR_AMP_CM = 5.0           # a major constituent: TICON-4 amplitude >= 5 cm ...
MAX_AMP_STD_CM = 2.0         # ... whose own standard error is over 2 cm needs support (std gate)
MAX_RMS_M = 0.05             # the 5 cm rule: TICON-4 against OTC's withheld fit, a seasonal error, a coverage gap
REF_MARGIN = 0.8             # a reference sides with TICON-4 only when d(TICON) < 0.8 d(OTC)
MIN_DISCRIM_M = 0.01         # a neighbour decides only when its two distances differ by 1 cm or more
SEASONAL_SIGNAL_M = 0.02     # a series' seasonal signal: an annual term (or, from few months, a span) of 2 cm or more
SEASONAL_FIT_MONTHS = 6      # from 6 months on, an annual + semiannual fit to the monthly means decides
SA_ABSENT_M = 0.01           # ... and an annual term under 1 cm says the agency's constants have no SA (r6 proxy)
SEASONAL_SLACK_M = 0.01      # N1: TICON-4's seasonal error may be OTC's own (fitted SA) + 1 cm
YEAR_HOURS = 8766.0          # OTC's SA counts as fitted from at least 1 year of record
NEIGHBOUR_KM = 3.0
EOT20_KM = 10.0              # EOT20 is valid at a gauge within 10 km of its cell ...
EOT20_MAX_M = 0.10           # ... that reproduces one of the two sets to 10 cm (it resolves the tide there)
QUALITY_YEAR = 2026
REF_RANK = {"official_series": 0, "official_constants": 0, "neighbour_fit": 1, "eot20": 2}
SEASONAL = ("SA", "SSA")
LONG = frozenset({"SA", "SSA", "MM", "MF", "MSF", "MSM", "MTM", "MSQM"})
# omission check: a line TICON-4 lacks that OTC's fit determines well (>= 2 cm and >= 3 x its uncertainty) and
# whose absence moves high and low waters by more than 15 min (median) or 5 cm refuses the set
OMIT_MIN_AMP_M, OMIT_SNR, OMIT_MAX_MIN, OMIT_MAX_M = 0.02, 3.0, 15.0, 0.05
# the halves rule is SR-ACC's self-consistency rule (blitz/otc-gesla/SR-ACC d4ed0a3,
# short_accuracy.inconsistent_halves), with the same thresholds; keep the two in step
HALVES_MAIN = ("M2", "S2", "K1", "O1")
HALVES_MIN_AMP_M, HALVES_MISSING_AMP_M, HALVES_M2, HALVES_K1_S2, HALVES_K1_S2_SHARE = 0.02, 0.05, 0.5, 1.0, 0.6


def _vec(a: float, g: float) -> complex:
    return complex(a * math.cos(math.radians(g)), a * math.sin(math.radians(g)))


def inconsistent_halves(fit: Mapping) -> str | None:
    """Why the two halves of OTC's fit of the record (RecordFit.to_dict()) say it is not one record, or None
    (SR-ACC's rule): M2, or the dominant main constituent, at A >= 5 cm missing from a half; M2 at A >= 2 cm with
    |z1 - z2| / A > 0.5; K1 or S2 at A >= 2 cm and >= 0.6 x the dominant amplitude with |z1 - z2| / A > 1.0. A half
    that could not be fitted says nothing."""
    h = fit.get("halves") or {}
    h1, h2 = h.get("half1"), h.get("half2")
    if not h1 or not h2:
        return None
    amps = {c: float(fit["constituents"][c]["amp_m"]) for c in HALVES_MAIN if c in fit["constituents"]}
    if not amps:
        return None
    dom = max(amps, key=lambda c: (amps[c], c))
    why = []
    for c, a in amps.items():
        c1, c2 = h1["constituents"].get(c), h2["constituents"].get(c)
        if c1 is None or c2 is None:
            if (c == "M2" or c == dom) and a >= HALVES_MISSING_AMP_M:
                why.append(f"{c} ({a:.3f} m) is missing from {'the first' if c1 is None else 'the second'} half")
            continue
        if a < HALVES_MIN_AMP_M:
            continue
        d = abs(_vec(c1["amp_m"], c1["phase_deg"]) - _vec(c2["amp_m"], c2["phase_deg"])) / a
        if c == "M2" and d > HALVES_M2 or c in ("K1", "S2") and a >= HALVES_K1_S2_SHARE * amps[dom] and d > HALVES_K1_S2:
            why.append(f"the halves' {c} differ by {d:.2f} x its amplitude ({c1['amp_m']:.3f} m {c1['phase_deg']:.0f} deg "
                       f"against {c2['amp_m']:.3f} m {c2['phase_deg']:.0f} deg)")
    return "; ".join(why) or None


def _predict(cons: Mapping[str, tuple[float, float]], table: TcdTable, year: int, hours):
    """Heights at `hours` after 1 January `year` 00:00 UTC, with the year's f and V0+u (the reader's predictor)."""
    import numpy as np
    x = np.zeros(len(hours))
    for n, (a, g) in cons.items():
        x += table.f(n, year) * a * np.cos(np.radians(table.speed(n) * hours + table.v0u(n, year) - g))
    return x


def _dist(a: Mapping, b: Mapping, names, table: TcdTable, year: int) -> float:
    """RMS of the prediction difference of two sets over `names` (a line one set lacks counts as zero there), from
    the constituent vector differences."""
    z = lambda d, n: _vec(*d[n]) if n in d else 0j
    return math.sqrt(sum((table.f(n, year) * abs(z(a, n) - z(b, n))) ** 2 / 2 for n in names))


def _year_start(t: float) -> tuple[int, float]:
    y = datetime.fromtimestamp(t, timezone.utc).year
    return y, datetime(y, 1, 1, tzinfo=timezone.utc).timestamp()


class _Series:
    """An official prediction series (month segments) against which a set is judged in the time domain."""

    def __init__(self, ref: Mapping, table: TcdTable):
        import numpy as np
        self.ref, self.table, self.segs = ref, table, []
        for s in ref["segments"]:
            y, t0 = _year_start(s["start_s"])
            if not table.first_year <= y <= table.last_year:
                continue
            v = np.asarray(s["values"], dtype=float)
            self.segs.append((y, (s["start_s"] - t0) / 3600.0 + np.arange(len(v)), v))

    def _pairs(self, cons: Mapping, tidal: bool):
        sel = {n: c for n, c in cons.items() if not (tidal and n in LONG)}
        for y, h, v in self.segs:
            yield _predict(sel, self.table, y, h), v

    def full(self, cons: Mapping) -> float:
        """RMS with every line, seasonal included, one overall mean removed."""
        import numpy as np
        d = np.concatenate([p - v for p, v in self._pairs(cons, False)])
        return float(np.sqrt(np.mean((d - d.mean()) ** 2)))

    def tidal(self, cons: Mapping) -> float:
        """RMS without the long-period lines, each month's mean removed from both."""
        import numpy as np
        d = np.concatenate([(p - p.mean()) - (v - v.mean()) for p, v in self._pairs(cons, True)])
        return float(np.sqrt(np.mean(d ** 2)))

    def seasonal_range(self) -> float:
        """The span (m) of the series' own monthly mean levels."""
        m = [float(v.mean()) for _, _, v in self.segs]
        return max(m) - min(m) if m else 0.0

    def seasonal_harmonics(self) -> tuple[float, float]:
        """(annual, semiannual) amplitudes (m) of a two-harmonic fit (with a mean) to the series' monthly mean levels,
        each at the middle of its segment. Only meaningful from SEASONAL_FIT_MONTHS months."""
        import numpy as np
        t = np.array([(y + (h[0] + h[-1]) / 2 / 8766.0) for y, h, _ in self.segs])
        m = np.array([float(v.mean()) for _, _, v in self.segs])
        w = 2 * np.pi * t
        A = np.vstack([np.ones_like(t), np.cos(w), np.sin(w), np.cos(2 * w), np.sin(2 * w)]).T
        c, *_ = np.linalg.lstsq(A, m, rcond=None)
        return float(math.hypot(c[1], c[2])), float(math.hypot(c[3], c[4]))

    def has_seasonal_signal(self) -> bool:
        """Whether the series can see the seasonal cycle, as evidence about SA/SSA: with SEASONAL_FIT_MONTHS months or
        more, an annual + semiannual fit to its monthly means of 2 cm or more combined, and an annual term of 1 cm
        or more (under it, the agency's constants have no SA, so the series never decides SA); with fewer months,
        monthly means spanning 2 cm or more."""
        if len(self.segs) >= SEASONAL_FIT_MONTHS:
            a, s = self.seasonal_harmonics()
            return math.hypot(a, s) >= SEASONAL_SIGNAL_M and a >= SA_ABSENT_M
        return self.seasonal_range() >= SEASONAL_SIGNAL_M

    def seasonal(self, cons: Mapping) -> float | None:
        """RMS error of the monthly mean levels (the seasonal cycle), the overall datum removed; None from fewer
        than two months."""
        import numpy as np
        m = np.array([p.mean() - v.mean() for p, v in self._pairs(cons, False)])
        return None if len(m) < 2 else float(np.sqrt(np.mean((m - m.mean()) ** 2)))


def _events(x, step_min: float):
    """(minutes, height, +1 high / -1 low) of the extremes of a sampled curve (parabolic refinement)."""
    import numpy as np
    d = np.diff(x)
    idx = np.where((d[:-1] > 0) & (d[1:] <= 0) | (d[:-1] < 0) & (d[1:] >= 0))[0] + 1
    out = []
    for i in idx:
        a, b, c = x[i - 1], x[i], x[i + 1]
        den = a - 2 * b + c
        off = 0.0 if den == 0 else 0.5 * (a - c) / den
        out.append(((i + off) * step_min, b - 0.25 * (a - c) * off, 1 if den < 0 else -1))
    return out


def omission_effect(otc: Mapping, missing: list[str], table: TcdTable, year: int = QUALITY_YEAR) -> tuple[float, float]:
    """(median |dt| min, median |dh| m) of high and low waters over `year` between OTC's fit with and without the
    `missing` lines, each event matched to the nearest one of the same kind within 3 h."""
    import numpy as np
    step = 6.0
    h = np.arange(0, 365 * 24, step / 60.0)
    full = _predict(otc, table, year, h)
    less = _predict({n: v for n, v in otc.items() if n not in missing}, table, year, h)
    ea, eb = _events(full, step), _events(less, step)
    if not ea or not eb:
        return 0.0, 0.0
    tb = np.array([e[0] for e in eb])
    dts, dhs = [], []
    for t, z, k in ea:
        j = int(np.searchsorted(tb, t))
        best = None
        for jj in (j - 2, j - 1, j, j + 1, j + 2):
            if 0 <= jj < len(eb) and eb[jj][2] == k and abs(eb[jj][0] - t) <= 180 and \
                    (best is None or abs(eb[jj][0] - t) < abs(best[0] - t)):
                best = eb[jj]
        if best is not None:
            dts.append(abs(best[0] - t)); dhs.append(abs(best[1] - z))
    return (float(np.median(dts)), float(np.median(dhs))) if dts else (0.0, 0.0)


def _drop(cs: dict, names: tuple[str, ...], detail: str) -> dict:
    """The set without `names` (declared in dropped_constituents, dropped_reason other)."""
    out = {**cs, "constituents": [c for c in cs["constituents"] if c["name"] not in names]}
    out["dropped_constituents"] = sorted(cs.get("dropped_constituents", []) +
                                         [{"name": n, "dropped_reason": "other", "detail": detail} for n in names],
                                         key=lambda d: d["name"])
    return out


def _note(cs: dict, text: str) -> dict:
    prov = dict(cs["provenance"])
    prov["selection_reason"] = f"{prov['selection_reason']}; quality check: {text}"
    return {**cs, "provenance": prov}


def quality_gate(cs: dict, *, rec: TiconRecord, fit_doc: Mapping, references: list[Mapping], table: TcdTable,
                 interval_s: int = 3600) -> dict:
    """The carried set after the quality gates, or NotCarried(ticon_quality). `fit_doc` is OTC's withheld fit
    document of the same record (a check only, never published). `references` are independent references:
    {"kind": "official_series", "id", "km", "segments": [{"start_s", "values"}]} (official predictions, judged in the
    time domain), or {"kind": official_constants | neighbour_fit | eot20, "id", "km", "constituents": {name: [amp m,
    phase deg]}}. In order:

    1. OTC's half-record fits of the record disagree (SR-ACC's rule): refused, whatever the references say.
    2. Omission: a line OTC's fit determines well that TICON-4 lacks moves high/low waters by more than 15 min or
       5 cm (median over the year): refused. OTC's lines are never put into a TICON-4 set.
    3. Seasonal terms. The variants are the set, the set without SA, and without SA and SSA. With an official
       series that has a seasonal signal (monthly means spanning 2 cm or more), the variant whose monthly mean
       levels are closest to the series' is kept (a drop must be closer by 1 cm), and its seasonal error must be
       within 5 cm, or (owner decision N1) within OTC's own error + 1 cm where OTC's fit has its own SA from a year
       of record: then carried with a source_quality_note. Without such a series, the variant closest to OTC's
       full fit (SA and SSA counted where either set has them), and it must then be within 5 cm of it.
       Then, on every path: an official series that is more than 5 cm from TICON-4's tidal lines and sides with
       OTC's fit (d(OTC) < 0.8 d(TICON)) refuses the set.
    4. TICON-4 against OTC's fit over the lines both carry (with SA and SSA as in 3): within 5 cm passes. Over 5 cm,
       a reference must side with TICON-4 by d(TICON) < 0.8 d(OTC): an official series in the time domain on the
       whole set, and on the tidal lines too unless those agree with OTC's fit within 5 cm; a constants-only reference only when TICON-4 and OTC differ by 5 cm
       or less outside its lines (a neighbour also needs |d(OTC) - d(TICON)| >= 1 cm). Else refused.
    5. Standard error: a carried major line (>= 5 cm) whose TICON-4 standard error is over 2 cm is accepted when
       TICON-4 is within 5 cm of OTC's fit (the halves agree, by 1), or when the reference that decided 4 contains
       that line (an official series contains every line). Else refused."""
    year = min(QUALITY_YEAR, table.last_year)
    why = inconsistent_halves(fit_doc["fit"])
    if why:
        raise NotCarried(f"{rec.name}: OTC's half-record fits of the record disagree: {why}", "ticon_quality")
    fit = fit_doc["fit"]["constituents"]
    otc = {n: (c["amp_m"], c["phase_deg"]) for n, c in fit.items() if n in table}
    mine0 = {c["name"]: (c["amplitude_m"], c["phase_deg"]) for c in cs["constituents"]}
    missing = sorted(n for n, c in fit.items() if n in table and n not in mine0 and c["amp_m"] >= OMIT_MIN_AMP_M
                     and c["amp_m"] >= OMIT_SNR * float(c.get("u_m") or 0.0))
    if missing:
        dt, dh = omission_effect(otc, missing, table, year)
        if dt > OMIT_MAX_MIN or dh > OMIT_MAX_M:
            raise NotCarried(f"{rec.name}: TICON-4 lacks {', '.join(missing)}, which OTC's fit of the record determines; "
                             f"leaving them out moves high and low waters by {dt:.1f} min and {dh * 100:.1f} cm "
                             "(median, 2026)", "ticon_quality")
    series = sorted((r for r in references if r["kind"] == "official_series"), key=lambda r: r.get("km", 0.0))
    S = next((x for x in (_Series(r, table) for r in series) if x.segs), None)
    variants = [()]
    if "SA" in mine0:
        variants.append(("SA",))
        if "SSA" in mine0:
            variants.append(("SA", "SSA"))
    cut = lambda drop: {n: v for n, v in mine0.items() if n not in drop}
    # r6: a source is evidence about SA/SSA only if it can see the seasonal cycle. OTC's fit can when it comes from a
    # year of record or more; an official series when it has a seasonal signal of its own (Series.has_seasonal_signal)
    otc_sees = float(fit_doc.get("good_hours") or 0.0) >= YEAR_HOURS
    otc_sa = otc_sees and "SA" in fit
    sea_lines = lambda m: [n for n in SEASONAL if n in m or n in otc] if otc_sees else []
    common = lambda m: sorted(set(m) & set(otc) - set(SEASONAL)) + sea_lines(m)
    d_otc = lambda m: _dist(m, otc, common(m), table, year)
    seasonal_series = S is not None and S.seasonal(mine0) is not None and S.has_seasonal_signal()
    if seasonal_series:
        err = {v: S.seasonal(cut(v)) for v in variants}
        score = err
        err_otc = S.seasonal(otc)
    elif otc_sees:
        score = {v: d_otc(cut(v)) for v in variants}
    else:
        # no source can see the seasonal cycle: TICON-4's SA/SSA is kept unless its own standard error fails it, and
        # only when TICON-4's own analysis has a year of data (r7: the same test OTC's fit must meet)
        ticon_hours = rec.n_obs * interval_s / 3600.0
        fails = tuple(n for n in SEASONAL if n in mine0 and rec.constituents.get(n, (0.0,))[0] >= MAJOR_AMP_CM
                      and (rec.amp_std.get(n) or 0.0) > MAX_AMP_STD_CM)
        fails = ("SA", "SSA") if "SSA" in fails and "SA" in mine0 else fails
        if ticon_hours < YEAR_HOURS and "SA" in mine0:
            fails = tuple(n for n in SEASONAL if n in mine0)
        keep = () if not fails else next((v for v in variants if set(fails) <= set(v)), ())
        variants = [keep]
        score = {keep: 0.0}
    preferred = variants[0]
    for v in variants[1:]:
        if score[v] < score[preferred] - MIN_DISCRIM_M:
            preferred = v
    # r5 (N2): the variant is chosen among those that pass every later gate; the step-3 choice first, then the
    # others by the same score; when none passes, the step-3 choice's refusal stands
    order = [preferred] + sorted((v for v in variants if v != preferred), key=lambda v: (score[v], len(v)))

    def why_drop(drop):
        if not seasonal_series and not otc_sees:
            hours = rec.n_obs * interval_s / 3600.0
            if hours < YEAR_HOURS:
                return (f"TICON-4's own analysis has {hours:.0f} hours of data, under a year, and no source here can "
                        "see the seasonal cycle")
            return (f"its own standard error fails it ({', '.join(f'{n} {rec.amp_std.get(n, 0.0):.1f} cm' for n in drop)}),"
                    " and no source here can see the seasonal cycle")
        if seasonal_series:
            return (f"its monthly mean levels are closer to {S.ref['id']} ({err[drop] * 100:.1f} cm against "
                    f"{err[()] * 100:.1f} cm with it)")
        return (f"the set is closer to OTC's own fit of the record without it ({score[drop] * 100:.1f} cm against "
                f"{score[()] * 100:.1f} cm)")

    def attempt(drop, cs, why=None):
        limited = None
        if seasonal_series and err[drop] > MAX_RMS_M:
            # N1 (owner decision): OTC's own fit, with its own SA from a year or more, is as far from the series:
            # the site's seasonal levels are what limits every set; carried, with a note
            if otc_sa and err[drop] <= err_otc + SEASONAL_SLACK_M:
                limited = (err[drop], err_otc)
            else:
                raise NotCarried(f"{rec.name}: its monthly mean levels are {err[drop] * 100:.3f} cm RMS from "
                                 f"{S.ref['id']}'s" + (f" without {'+'.join(drop)}" if drop else "") +
                                 f" (OTC's fit of the record: {err_otc * 100:.1f} cm"
                                 + ("" if otc_sa else ", with no SA of its own from a year of record") +
                                 f"), over {MAX_RMS_M * 100:g} cm", "ticon_quality")
        mine = cut(drop)
        if drop:
            cs = _drop(cs, drop, f"TICON-4's {'+'.join(drop)} is left out: {why or why_drop(drop)}")
        if limited:
            flag = {"flag": "source_quality_note",
                    "reason": ("monthly mean levels at this river site can depart from predictions by several "
                               "decimetres with river flow; the same holds for the official predictions and for OTC's "
                               "own fit of the record" if rec.gauge_type == "River" else
                               "monthly mean levels at this site differ from the official predictions by about as much "
                               "for this set as for OTC's own fit of the record, so seasonal agreement is limited for "
                               "both"),
                    "values": {"seasonal_error_m": round(limited[0], 4), "otc_seasonal_error_m": round(limited[1], 4)}}
            cs = {**cs, "qc_status": "flagged", "qc_flags": [*cs.get("qc_flags", []), flag]}
        return gates_4_5(cs, mine)

    def gates_4_5(cs, mine):
        # r4 rule 1, on every path: an official series must not side with OTC against TICON-4
        if S is not None:
            vt, vo = S.tidal(mine), S.tidal(otc)
            if vt > MAX_RMS_M and vo < REF_MARGIN * vt:
                raise NotCarried(f"{rec.name}: the official predictions {S.ref['id']} side with OTC's fit of the record "
                                 f"(tidal {vo * 100:.1f} cm) against TICON-4 ({vt * 100:.1f} cm)", "ticon_quality")
            if vt > MAX_RMS_M and vo > MAX_RMS_M and REF_MARGIN * vo <= vt <= vo / REF_MARGIN:
                # r6: both sets are over 5 cm from the official predictions and neither is clearly closer: the
                # harmonic limit of the site, said so
                cs = {**cs, "qc_status": "flagged", "qc_flags": [*cs.get("qc_flags", []), {
                    "flag": "source_quality_note",
                    "reason": f"tidal agreement with the official predictions {S.ref['id']} is limited at this site: "
                              f"this set and OTC's own fit of the record are each over {MAX_RMS_M * 100:g} cm RMS from "
                              "them and neither is clearly closer; expect differences of that size from the official "
                              "tide tables",
                    "values": {"tidal_error_m": round(vt, 4), "otc_tidal_error_m": round(vo, 4)}}]}
        d = d_otc(mine)
        std = [(src, rec.amp_std[src]) for src, (amp, _) in sorted(rec.constituents.items())
               if _otc_name(src) in mine and amp >= MAJOR_AMP_CM and (rec.amp_std.get(src) or 0.0) > MAX_AMP_STD_CM]
        if d <= MAX_RMS_M:
            if std:
                cs = _note(cs, f"TICON-4's own standard error of {', '.join(f'{s} {e:.1f} cm' for s, e in std)} is over "
                               f"{MAX_AMP_STD_CM:g} cm, but the set is {d * 100:.1f} cm from OTC's own fit of the record, "
                               "whose half-record fits agree")
            return cs
        if S is not None:
            tt, to = S.tidal(mine), S.tidal(otc)
            ft, fo = S.full(mine), S.full(otc)
            # the tidal lines either agree with OTC's fit or the series sides with TICON-4 on them; the whole set,
            # seasonal lines included, must be clearly closer to the series than OTC's fit is
            d_tid = _dist(mine, otc, [n for n in common(mine) if n not in LONG], table, year)
            if (d_tid <= MAX_RMS_M or tt < REF_MARGIN * to) and ft < REF_MARGIN * fo:
                return _note(cs, f"differs from OTC's fit by {d * 100:.1f} cm RMS; the official predictions {S.ref['id']} "
                                 f"({S.ref.get('km', 0.0):.1f} km) are closer to TICON-4 (tidal {tt * 100:.1f} cm, all "
                                 f"{ft * 100:.1f} cm) than to OTC ({to * 100:.1f}, {fo * 100:.1f} cm)")
            raise NotCarried(f"{rec.name}: {d * 100:.1f} cm RMS from OTC's own fit of the record, and the official predictions "
                             f"{S.ref['id']} are not clearly closer to TICON-4 (tidal {tt * 100:.1f} cm, all {ft * 100:.1f} cm) "
                             f"than to OTC ({to * 100:.1f}, {fo * 100:.1f} cm)", "ticon_quality")
        lines = common(mine)
        best, seen = None, []
        for r in references:
            if r["kind"] == "official_series":
                continue
            ref = {n: tuple(v) for n, v in r["constituents"].items() if n in table}
            inside = [n for n in lines if n in ref]
            if not inside:
                continue
            outside = _dist(mine, otc, [n for n in lines if n not in ref], table, year)
            dt, do = _dist(mine, ref, inside, table, year), _dist(otc, ref, inside, table, year)
            if outside > MAX_RMS_M:
                seen.append(f"{r['id']} cannot see the difference ({outside * 100:.1f} cm outside its lines)")
                continue
            if r["kind"] == "eot20" and (r.get("km", 99) > EOT20_KM or min(dt, do) > EOT20_MAX_M):
                continue
            if r["kind"] == "neighbour_fit" and abs(do - dt) < MIN_DISCRIM_M:
                seen.append(f"{r['id']} does not discriminate ({dt * 100:.1f} against {do * 100:.1f} cm)")
                continue
            cand = (REF_RANK[r["kind"]], r.get("km", 0.0), r["id"], dt, do, set(inside))
            best = cand if best is None or cand[:2] < best[:2] else best
        if best is None:
            raise NotCarried(f"{rec.name}: {d * 100:.1f} cm RMS from OTC's own fit of the record, and no independent "
                             "reference decides between them" + (f" ({'; '.join(seen)})" if seen else ""), "ticon_quality")
        _, km, rid, dt, do, inside = best
        if not dt < REF_MARGIN * do:
            raise NotCarried(f"{rec.name}: {d * 100:.1f} cm RMS from OTC's own fit of the record, and {rid} ({km:.1f} km) "
                             f"is not clearly closer to TICON-4 ({dt * 100:.1f} cm) than to OTC ({do * 100:.1f} cm)",
                             "ticon_quality")
        unsupported = [(s, e) for s, e in std if _otc_name(s) not in inside]
        if unsupported:
            raise NotCarried(f"{rec.name}: TICON-4's own standard error of {', '.join(f'{s} {e:.1f} cm' for s, e in unsupported)}"
                             f" is over {MAX_AMP_STD_CM:g} cm, and {rid} does not contain the line", "ticon_quality")
        return _note(cs, f"differs from OTC's fit by {d * 100:.1f} cm RMS; {rid} ({km:.1f} km) is closer to TICON-4 "
                         f"({dt * 100:.1f} cm) than to OTC ({do * 100:.1f} cm) over its lines")

    first = None
    for v in order:
        why = None
        if first is not None and v:
            what = f"without {'+'.join(preferred)}" if preferred else "with all of its lines"
            why = f"the seasonal step's first choice, the set {what}, is refused ({str(first)[len(rec.name) + 2:]})"
        try:
            out = attempt(v, cs, why)
        except NotCarried as e:
            first = first or e
            continue
        if first is not None and not v:
            out = _note(out, f"the seasonal step's first choice, without {'+'.join(preferred)}, is refused "
                             f"({str(first)[len(rec.name) + 2:]}); the full set passes")
        return out
    raise first


NEIGHBOUR_NAMES = ("M2", "S2", "N2", "K1", "O1", "SA", "SSA")


def neighbour_references(rid: str, lat: float, lon: float, station_id: str, docs: list[Mapping],
                         station_ids: Mapping[str, str]) -> list[dict]:
    """OTC fits of other gauges (another station) within NEIGHBOUR_KM whose halves agree, as references. `docs`
    are the fits the build may publish (not withheld, not disputed, no station-map flag)."""
    out = []
    for d in docs:
        if d["record_id"] == rid or station_ids.get(d["record_id"]) == station_id or abs(float(d["lat"]) - lat) > 0.05:
            continue
        km = _km((lat, lon), (float(d["lat"]), float(d["lon"])))
        if km > NEIGHBOUR_KM or inconsistent_halves(d["fit"]):
            continue
        cons = {n: [d["fit"]["constituents"][n]["amp_m"], d["fit"]["constituents"][n]["phase_deg"]]
                for n in NEIGHBOUR_NAMES if n in d["fit"]["constituents"]}
        if cons:
            out.append({"kind": "neighbour_fit", "id": f"gesla-fit:{d['record_id']}", "km": round(km, 2),
                        "constituents": cons})
    return out
