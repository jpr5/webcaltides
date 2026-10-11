"""GESLA fits as `SourceSet`s (format 1.0 constant sets): the fit side of the GESLA build.

A fit document (DOC_VERSION 2) is one GESLA record's fit plus what its constant set needs: the
record's position and name from the GESLA metadata, the member SHA-256, the TCD SHA-256, each
constituent's TCD speed, the hours of good data, and the record's time-base annotation (format
1.0 `time_base`). The fit is `gesla.select_constituents.fit_record` on the `qc.clean` series,
cleaned with the record's published time base, then `sampling_corrected`: a time base whose
samples are hourly means (utc_mean_start60) attenuates every constituent by its boxcar response
(M2 by about 1.1 %), and the published amplitudes have it divided out. The proof is the fit's
own stamp, stats["sampling_corrected"] (kind, label, factors), which sampling_corrected writes;
nothing else is taken as proof. A fit whose time base attenuates the tide and has no matching
stamp is refused (FitIntegrityError), wherever it comes from.

Two ways to make documents:

    fit_records(...)        fits from the verified GESLA cache (the in-repo fit path)
    docs_from_fit_run(...)  reads a complete in-repo fit run (results.jsonl + fits/<record>.json
                            + timebase_manifest.json): the run's manifest must be complete and
                            clean, and its TCD dump, GESLA manifest and audit csv must be the
                            ones this build uses

Both gate the audit first (check_audit_outputs). Errors come in two kinds: FitIntegrityError (the
inputs cannot be trusted: stop the build) and NotPublishable (this record cannot be published
yet; `reason` says why, and the build reports it).

The documents of one build live in `$OTC_WORK/builds/<D>/gesla/fits/` (a local cache of the same
prefix in the work bucket; never in git) with a MANIFEST.json, which `load_gesla_sets` checks
file by file before it reads anything.

    uv run python -m otc_pipeline.gesla_sets show --datestamp D

What a set does not carry yet (each is another slot's, and is left out rather than guessed):
datum zero and named levels (datums spec DT5f/DT5g/DT6: `datum` has only `msl_offset_m`),
per-contributor licences (L-NC: the caller passes one licence id per record; none is assumed),
short-record sets (expected_accuracy
and the Rayleigh pairs), no_constants sets for unusable records (record_issues), and the
served-set flags (microtidal, non_tidal_signal).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from gesla.read_gesla import GeslaRelease, otc_work
from gesla import short_accuracy
from gesla.select_constituents import CANDIDATES, RecordFit
from gesla.tcd_table import TcdTable
from gesla.timebase_audit import check_audit_outputs, correction_of, declared_correction
from gesla.timebase_verdict import Correction

from otc_pipeline.adapter import SourceSet

DOC_VERSION = 3
SOURCE = "gesla-fit"
CONVENTION_ID = "otc-greenwich-schureman-v1"
FIT_MIN_GOOD_HOURS = 15 * 24.0   # release.fit_min_good_hours: below it a record gets no constants
MAX_AMP_M = 20.0                 # an amplitude above this is an unusable fit
MAIN = ("M2", "S2", "K1", "O1")  # at least one must survive the noise rule
REPO = Path(__file__).resolve().parents[3]
OTC_WORK_CLI = REPO / "bin" / "otc-work"
CONSTITUENT_NAME_RE = re.compile(r"[A-Z0-9]{1,15}")
REASON_MAX = 800                 # schema maxLength of time_base.reason
FITS_SHA256 = "fits_sha256"      # fit-run manifest key: {record id: sha256 of fits/<record>.json}
CUT = " [cut]"                   # marks a reason cut to REASON_MAX


class NotPublishable(ValueError):
    """A record whose set the format needs a value for that this build does not have yet, or whose
    inputs say it cannot be fitted. `reason` is the class the build report counts it under."""

    def __init__(self, message: str, reason: str = "other"):
        super().__init__(message)
        self.reason = reason


class FitIntegrityError(ValueError):
    """The fit inputs cannot be trusted (an unstamped attenuated fit, a run that is not complete or
    clean, a TCD or GESLA manifest other than the run's): the build stops."""


@dataclass(frozen=True)
class TcdDump:
    """The TCD dump a build uses, read once: the table and the SHA-256 of the same bytes."""
    table: TcdTable
    sha256: str
    path: Path


def load_tcd_dump(path: Path) -> TcdDump:
    data = Path(path).read_bytes()
    return TcdDump(TcdTable(json.loads(data)), hashlib.sha256(data).hexdigest(), Path(path))


def clip(text: str, limit: int = REASON_MAX) -> str:
    """text cut to `limit` characters, the cut marked."""
    return text if len(text) <= limit else text[:limit - len(CUT)] + CUT


def fits_prefix(datestamp: str) -> str:
    return f"builds/{datestamp}/gesla/fits/"


def fits_dir(datestamp: str) -> Path:
    return otc_work() / fits_prefix(datestamp)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(doc: Mapping) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=1, separators=(",", ": "), allow_nan=False) + "\n").encode()


# --- time base ------------------------------------------------------------------------------------

def declared_hours(meta: Mapping[str, str]) -> int:
    """GESLA's TIME ZONE HOURS: the stamps are UTC + n h. Missing or not a number raises (never UTC by default)."""
    raw = (meta.get("TIME ZONE HOURS") or "").strip()
    try:
        v = float(raw)
    except ValueError:
        raise NotPublishable(f"{meta.get('FILE NAME')}: TIME ZONE HOURS {raw!r} is missing or not a number",
                             "time_zone_missing") from None
    if not math.isfinite(v) or v != int(v):
        raise NotPublishable(f"{meta.get('FILE NAME')}: TIME ZONE HOURS {v} is not whole hours", "time_zone_missing")
    return int(v)


def declared_label(n_hours: int) -> str:
    """The format's time_base_label of GESLA's declared time base."""
    if n_hours == 0:
        return "utc_instant"
    return f"utc_{'plus' if n_hours > 0 else 'minus'}_{abs(n_hours)}"


def _applied(row: Mapping | None) -> bool:
    """The audit's class is applied only when it is verified or corrected (owner rule: else GESLA's)."""
    return row is not None and row["time_base_status"] in ("verified", "corrected")


def published_correction(row: Mapping | None, n_decl: int) -> Correction:
    """The Correction a record is fitted and published with: the audit's (class and bad months)
    when verified or corrected, else GESLA's declared time base."""
    if not _applied(row):
        return declared_correction(n_decl)
    label = row["published_time_base"]
    try:
        corr = Correction("utc_mean_start60") if label == "utc_mean_start60" else correction_of(label)
    except ValueError as e:
        raise NotPublishable(f"{row.get('file_id')}: time base {label!r}: {e}", "time_base_unsupported") from None
    if row["months_bad"]:
        corr = replace(corr, months_drop=tuple((int(m[:4]), int(m[5:7])) for m in row["months_bad"].split()))
    return corr


_INSTANT_RE = re.compile(r"utc_instant|utc_(plus|minus)_[0-9]+|local_dst:.+")


def needs_stamp(label: str) -> bool:
    """True unless the time base is an instant class (whose sampling response is 1): an hourly mean,
    a step, or anything else needs the fit's sampling_corrected stamp before it is published."""
    return not _INSTANT_RE.fullmatch(label)


def check_stamp(rid: str, fit: Mapping, label: str, *, corr: Correction, speed, span: tuple[int, int]) -> None:
    """The fit's sampling_corrected stamp agrees with the published time base `label` and its Correction:
    required when the time base needs it, and, when present, of the same label and kind, with the factors
    the correction gives at each constituent's speed (`speed(name)`) over `span`. Raises FitIntegrityError."""
    mark = _mark(fit)
    if mark is None:
        if needs_stamp(label):
            raise FitIntegrityError(f"{rid}: time base {label} attenuates the tide and the fit has no "
                                    "sampling_corrected stamp (its amplitudes would be published about 1 % low)")
        return
    if mark.get("label") != label:
        raise FitIntegrityError(f"{rid}: the fit's sampling_corrected stamp is for {mark.get('label')}, "
                                f"the published time base is {label}")
    if mark.get("kind") != corr.kind:
        raise FitIntegrityError(f"{rid}: stamp kind {mark.get('kind')}, the correction is {corr.kind}")
    factors = mark.get("factors") or {}
    for n in sorted(set(factors) | ({"M2"} & set(fit.get("constituents", {})))):
        want = corr.amplitude_factor(speed(n), span)
        got = factors.get(n, 1.0)
        if not math.isclose(got, want, rel_tol=1e-8, abs_tol=1e-9):
            raise FitIntegrityError(f"{rid}: stamp factor {n} {got}, the correction gives {want}")


def _num(v: str) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# The audit's comparator_kind column (timebase.csv) -> format 1.0 comparator_kind.
_PRIMARY_KIND = {"a": "sibling_record", "b": "official_predictions", "c": "official_constants",
                 "inherit": "provider"}
_WEAK = (("weak_eot20", "model"), ("weak_served", "served_constants"), ("weak_dst", "dst_selftest"))
_PAREN_RE = re.compile(r"\(([+-]?[0-9.]+)\)")
_SERVED_ID_RE = re.compile(r"\bT[0-9a-f]{6,}\b")


def _comparator_id(kind: str, raw: str) -> str:
    if kind == "sibling_record":
        return raw.removesuffix("~corrected")       # a GESLA file id (taken after its own correction)
    if kind == "official_predictions":
        return raw.removeprefix("official:")         # e.g. kartverket:AES, bsh:103P
    if kind == "provider":
        return raw.removeprefix("provider:")         # e.g. UNAM_HIST:3600 (provider and interval)
    return raw                                       # official_constants: harcon:<NOAA id>


# TB-SIB's sibling_constants column (timebase_audit.csv_row; the rule's summary text, f5a026a):
# site:       "fitted constants +39.0 min against 2 record(s) of the site (a, b); side by eot20 (EOT20 +51.5 here, ...)"
# family:     "EOT20 -484.5 min (all four) = -8 h, as 6 records of provider family bom/aus/aaaaa99 (...)"
# whole hour: "EOT20 lag +542.5 min (all four) is +9 h, a whole number of hours ..."
_SIB_SITE_RE = re.compile(r"fitted constants ([+-]?[0-9.]+) min against \d+ record\(s\) of the site \(([^)]*)\)")
_SIB_HERE_RE = re.compile(r"\(EOT20 ([+-]?[0-9.]+) here")
_SIB_EOT_RE = re.compile(r"EOT20(?: lag)? ([+-]?[0-9.]+) min")
_SIB_FAMILY_RE = re.compile(r"provider family (\S+?)(?: \(|$|,|;)")


def _sibling_evidence(text: str) -> list[dict]:
    """format 1.0 time_base_evidence from TB-SIB's sibling_constants summary: a sibling_record per site record
    the fitted constants were compared with (lag = the shift), the EOT20 lag (model), the provider family
    (provider). Each item carries the summary as its detail."""
    out = []
    m = _SIB_SITE_RE.search(text)
    if m:
        for rid in (x.strip() for x in m.group(2).split(",")):
            if rid:
                out.append({"comparator_kind": "sibling_record", "comparator_id": rid, "lag_min": float(m.group(1)),
                            "detail": text})
        here = _SIB_HERE_RE.search(text)
        if here:
            out.append({"comparator_kind": "model", "comparator_id": "eot20", "lag_min": float(here.group(1)), "detail": text})
    else:
        e = _SIB_EOT_RE.search(text)
        if e:
            out.append({"comparator_kind": "model", "comparator_id": "eot20", "lag_min": float(e.group(1)), "detail": text})
        f = _SIB_FAMILY_RE.search(text)
        if f:
            out.append({"comparator_kind": "provider", "comparator_id": f.group(1), "detail": text})
    return out


def _evidence(row: Mapping, record_id: str) -> list[dict]:
    """The comparisons behind an audit row, as format 1.0 time_base_evidence."""
    out = []
    if row["comparator_kind"] not in (*_PRIMARY_KIND, "w", "none"):
        raise NotPublishable(f"{record_id}: unknown comparator_kind {row['comparator_kind']!r}", "time_base_row")
    kind = _PRIMARY_KIND.get(row["comparator_kind"])
    if kind and row["comparator"]:
        e = {"comparator_kind": kind, "comparator_id": _comparator_id(kind, row["comparator"])}
        lag = _num(row["official_lag"]) if kind == "official_predictions" else None
        lag = lag if lag is not None else _num(row["lag_median"])
        if lag is not None:
            e["lag_min"] = lag
        if row["reason"]:
            e["detail"] = row["reason"]
        out.append(e)
    if row["comparator_kind"] == "w":       # the weak checks decided it: EOT20, served constants, DST
        for col, wkind in _WEAK:
            v = (row.get(col) or "").strip()
            if not v or v.startswith("n/a"):
                continue
            if wkind == "model":
                cid = "eot20"
            elif wkind == "served_constants":
                m = _SERVED_ID_RE.search(v)
                if not m:
                    raise NotPublishable(f"{record_id}: served-constants check {v!r} names no served station",
                                         "time_base_row")
                cid = m.group(0)
            else:
                cid = record_id
            e = {"comparator_kind": wkind, "comparator_id": cid, "detail": v}
            m = _PAREN_RE.search(v)
            if m and wkind != "dst_selftest":
                e["lag_min"] = float(m.group(1))
            out.append(e)
        sib = (row.get("sibling_constants") or "").strip()
        if sib:                              # TB-SIB: the site siblings' constants, the provider family, EOT20
            have = {(e["comparator_kind"], e["comparator_id"]) for e in out}
            out += [e for e in _sibling_evidence(sib) if (e["comparator_kind"], e["comparator_id"]) not in have]
    return out


def time_base_annotation(row: Mapping | None, n_decl: int, record_id: str, *, decided_in: str,
                         decided_build_commit: str | None = None) -> dict:
    """The format 1.0 time_base of a record: from its timebase.csv row, or, for a record outside the
    audit pool, unverified with GESLA's declared time base. Raises NotPublishable when the row is
    inconsistent (a code with no evidence, a correction that changes nothing)."""
    declared = declared_label(n_decl)
    tb: dict = {"declared": declared, "published": declared, "decided_in": decided_in}
    if decided_build_commit:
        tb["decided_build_commit"] = decided_build_commit
    if row is None:
        tb.update(code="unverified", reason=f"declared {declared} kept; the record is not in the time-base audit pool")
        return tb
    code = row["time_base_status"]
    if code not in ("verified", "corrected", "unverified", "disputed"):
        raise NotPublishable(f"{record_id}: time_base_status {code!r}", "time_base_row")
    reason = (row["time_base_reason"] or row["reason"] or "").strip()
    if not reason:
        raise NotPublishable(f"{record_id}: the audit row has no reason", "time_base_row")
    tb.update(code=code, reason=clip(reason))
    if code in ("verified", "corrected"):
        tb["published"] = row["published_time_base"]
        if row["months_bad"]:
            tb["months_dropped"] = sorted(set(row["months_bad"].split()))
    if code == "corrected":
        if tb["published"] == declared:
            raise NotPublishable(f"{record_id}: corrected, but the published time base is the declared {declared}",
                                 "time_base_row")
        tb["correction"] = {"from": declared, "to": tb["published"]}
    elif code == "verified" and tb["published"] != declared:
        raise NotPublishable(f"{record_id}: verified {tb['published']}, but GESLA declares {declared}", "time_base_row")
    ev = _evidence(row, record_id)
    if ev:
        tb["evidence"] = ev
    elif code != "unverified":
        raise NotPublishable(f"{record_id}: time base {code} with no evidence in the audit row", "time_base_row")
    return tb


@dataclass(frozen=True)
class Audit:
    """A time-base audit's outputs, as the fit consumes them: timebase.csv keyed by GESLA file id, and the
    day (UTC) and commit of the run that decided them (format 1.0 decided_in, decided_build_commit)."""
    rows: Mapping[str, Mapping]
    decided_in: str
    decided_build_commit: str
    csv_sha256: str


def load_audit(audit_dir: Path) -> Audit:
    """The audit in audit_dir, after timebase_audit.check_audit_outputs: an audit that is incomplete,
    crashed (weak_crashes), made before the crash check, or whose timebase.csv changed since raises
    ValueError, and is never fitted. A run on a dirty tree is refused too (its commit decides nothing)."""
    m = check_audit_outputs(audit_dir)
    if m.get("dirty") or not m.get("finished"):
        raise ValueError(f"{audit_dir}: the audit ran on a dirty tree or has no finish time")
    with open(Path(audit_dir) / "timebase.csv", newline="", encoding="utf-8") as f:
        rows = {r["file_id"]: r for r in csv.DictReader(f)}
    day = datetime.fromisoformat(m["finished"]).astimezone(timezone.utc).strftime("%Y%m%d")
    return Audit(rows, day, m["commit"], m["outputs"]["timebase.csv"])


# --- fit documents --------------------------------------------------------------------------------

def fit_document(record_id: str, source_version: str, member_sha256: str, meta: Mapping[str, str],
                 table: TcdTable, fit: Mapping, *, good_hours: float, time_base: Mapping,
                 calibration: Mapping | None = None) -> dict:
    """One record's fit document (DOC_VERSION 3). `fit` is RecordFit.to_dict(), with its
    sampling_corrected stamp; `meta` the record's GESLA metadata row; good_hours is kept to 0.1 h on both
    paths (the fit run's data_hours, fit_records' record_hours). The v3 fields come from
    gesla.short_accuracy.fit_document_v3_fields and `calibration` (read_calibration): rayleigh_drops,
    expected_accuracy, accuracy_by_constituent, microtidal and refusal (implausible_fit, or
    short_record_uncalibrated for a short record the calibration does not cover). They never raise:
    source_set refuses the record instead."""
    names = sorted(set(fit["constituents"]) | set(fit.get("snr_dropped", ())))
    return {"doc_version": DOC_VERSION, "record_id": record_id, "source_version": source_version,
            "member_sha256": member_sha256, "tcd_sha256": table.tcd_sha256,
            "site_name": meta["SITE NAME"].replace("_", " "), "site_code": meta.get("SITE CODE", ""),
            "country": meta["COUNTRY"], "lat": float(meta["LATITUDE"]), "lon": float(meta["LONGITUDE"]),
            "contributor": meta.get("CONTRIBUTOR (ABBREVIATED)", ""),
            "datum_information": meta.get("DATUM INFORMATION", ""),
            "good_hours": round(float(good_hours), 1), "time_base": dict(time_base),
            "speeds_deg_per_hour": {n: table.speed(n) for n in names},
            "fit": dict(fit),
            **short_accuracy.fit_document_v3_fields(table, fit, record_id=record_id, good_hours=float(good_hours),
                                                    calibration=calibration)}


def _mark(fit: Mapping) -> dict | None:
    """The sampling_corrected mark a fit carries (stats, from G04-int c6f2fac on), or None."""
    return (fit.get("stats") or {}).get("sampling_corrected")


def _unusable(rf: RecordFit) -> str | None:
    amps = rf.primary.fit.amplitude_m
    bad = [n for n, a in amps.items() if not math.isfinite(a) or a > MAX_AMP_M]
    if bad:
        return f"absurd amplitude {bad[:5]}"
    if not any(n in amps for n in MAIN):
        return f"none of M2/S2/K1/O1 above the noise ({len(amps)} kept)"
    return None


def fit_records(table: TcdTable, release: GeslaRelease, record_ids: Iterable[str], *, audit_dir: Path,
                roles: Mapping | None = None, calibration: Mapping | None = None) -> tuple[list[dict], list[dict]]:
    """Fit each record from the verified cache with its published time base (G03c default: QC, the
    last 19 years), from the audit in audit_dir (load_audit: the gate runs before any fit). Returns
    (documents, refused); a refused entry (build-report shape) names the record, the reason class
    (too_short, fit_unusable, or a NotPublishable reason), the message and its good hours. The
    sampling response of the time base is divided out after fit_record, once per record; any error
    from that step (a correction other than the series', a second pass) stops the build."""
    from gesla.fit import FitError
    from gesla.qc import clean
    from gesla.select_constituents import (FIT_SPAN_YEARS, fit_record, in_tcd_years, last_years, record_hours,
                                           sampling_corrected)
    audit = load_audit(audit_dir)
    docs, skipped = [], []
    meta_all = release.metadata()
    for rid in record_ids:
        meta = meta_all[rid]
        row = audit.rows.get(rid)
        try:
            n_decl = declared_hours(meta)
            corr = published_correction(row, n_decl)
            tb = time_base_annotation(row, n_decl, rid, decided_in=audit.decided_in,
                                      decided_build_commit=audit.decided_build_commit)
        except NotPublishable as e:
            skipped.append(refusal(rid, e.reason, str(e)))
            continue
        rec = release.record(rid, roles or {})
        series = clean(rec, corr)
        win = in_tcd_years(table, last_years(series, FIT_SPAN_YEARS))
        hours = record_hours(win.times_s)
        if hours < FIT_MIN_GOOD_HOURS:
            skipped.append(refusal(rid, "too_short", f"{rid}: {hours:.0f} good hours, under {FIT_MIN_GOOD_HOURS:.0f}",
                                   hours))
            continue
        try:
            rf = fit_record(table, series)
        except FitError as e:
            skipped.append(refusal(rid, "fit_unusable", f"{rid}: FitError: {e}", hours))
            continue
        span = (int(win.times_s.min()), int(win.times_s.max()) + 3600)
        rf = sampling_corrected(rf, table, corr, span)
        why = _unusable(rf)
        if why:
            skipped.append(refusal(rid, "fit_unusable", f"{rid}: {why}", hours))
            continue
        fit = rf.to_dict()
        if _mark(fit) is None:
            raise FitIntegrityError(f"{rid}: sampling_corrected left no stamp on the fit")
        check_stamp(rid, fit, tb["published"], corr=corr, speed=table.speed, span=span)
        docs.append(fit_document(rid, release.version, rec.member_sha256, meta, table, fit,
                                 good_hours=hours, time_base=tb, calibration=calibration))
    return docs, skipped


def refusal(record: str, reason: str, message: str, good_hours: float | None = None) -> dict:
    """One build-report entry: a record left out of the release, and why."""
    out = {"record": record, "reason": reason, "message": message}
    if good_hours is not None:
        out["good_hours"] = round(float(good_hours), 1)
    return out


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_fit_run(run_dir: Path, tcd: TcdDump, release: GeslaRelease, audit: Audit, *,
                  overlay: bool = False) -> tuple[dict, dict[str, dict]]:
    """The fit run's timebase_manifest.json, fit to consume: status complete, a clean tree, results.jsonl
    as the manifest recorded it, `fits_sha256` (record id -> sha256 of fits/<record>.json) listing exactly
    the fitted rows and every file in fits/, and the inputs this build uses: the same TCD dump (its
    SHA-256), the same verified GESLA manifest and the same audit csv. Any other state raises
    FitIntegrityError, and so does a record with more than one row. Returns (manifest, rows by record), the
    rows parsed from the same bytes that were hashed. Each fit's bytes are checked against the map when it
    is read (docs_from_fit_run)."""
    mp = Path(run_dir) / "timebase_manifest.json"
    if not mp.is_file():
        raise FitIntegrityError(f"{run_dir}: no timebase_manifest.json: not a fit run")
    m = json.loads(mp.read_text())
    if m.get("status") != ("partial" if overlay else "complete"):
        raise FitIntegrityError(f"{mp}: status {m.get('status')!r}: only a complete run is published"
                                + (" (an overlay is a partial run: ONLY)" if overlay else ""))
    if m.get("dirty") is not False:
        raise FitIntegrityError(f"{mp}: dirty {m.get('dirty')!r}: the run's commit does not describe its code")
    want = (m.get("outputs") or {}).get("results.jsonl")
    data = (Path(run_dir) / "results.jsonl").read_bytes()      # one read: hashed, then parsed
    if not want or want != hashlib.sha256(data).hexdigest():
        raise FitIntegrityError(f"{run_dir}/results.jsonl is not the one the manifest recorded")
    rows: dict[str, dict] = {}
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["record"] in rows:
            raise FitIntegrityError(f"{run_dir}/results.jsonl: record {r['record']} has more than one row")
        rows[r["record"]] = r
    fmap = m.get(FITS_SHA256)
    if not isinstance(fmap, dict) or not fmap:
        raise FitIntegrityError(f"{mp}: no {FITS_SHA256} (record id -> sha256 of fits/<record>.json): the fit "
                                "files are not bound to the run")
    fitted = {rid for rid, r in rows.items() if r.get("outcome") == "fitted"}
    if set(fmap) != fitted:
        raise FitIntegrityError(f"{mp}: {FITS_SHA256} does not list exactly the fitted rows of results.jsonl "
                                f"(only in the map {sorted(set(fmap) - fitted)[:5]}, "
                                f"fitted but not in it {sorted(fitted - set(fmap))[:5]})")
    on_disk = {p.name for p in (Path(run_dir) / "fits").iterdir()} if (Path(run_dir) / "fits").is_dir() else set()
    stray = sorted(on_disk - {f"{r}.json" for r in fmap})
    if stray:
        raise FitIntegrityError(f"{run_dir}/fits: {stray[:5]} not in the run's {FITS_SHA256} (another run's files?)")
    gone = sorted(r for r in fmap if f"{r}.json" not in on_disk)
    if gone:
        raise FitIntegrityError(f"{run_dir}/fits: fits of {gone[:5]} are missing")
    inputs = m.get("inputs") or {}

    def sha(key):
        return (inputs.get(key) or {}).get("sha256")
    checks = (("tcd_table", tcd.sha256, f"the TCD dump {tcd.path}"),
              ("gesla_manifest", _sha256_file(Path(release.root) / "MANIFEST.json"), "the verified GESLA manifest"),
              ("timebase_csv", audit.csv_sha256, "the gated audit's timebase.csv"))
    for key, have, what in checks:
        if sha(key) != have:
            raise FitIntegrityError(f"{mp}: inputs.{key} sha256 {sha(key)}, this build uses {what} ({have})")
    return m, rows


def docs_from_fit_run(run_dir: Path, tcd: TcdDump, release: GeslaRelease, record_ids: Iterable[str], *,
                      audit_dir: Path, calibration: Mapping | None = None,
                      overlay_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Fit documents from a complete in-repo fit run (results.jsonl + fits/<record>.json +
    timebase_manifest.json), with the time bases of the audit in audit_dir (load_audit, gated). The
    run is checked first (check_fit_run): its TCD dump, GESLA manifest and audit csv must be this
    build's, so the documents' tcd_sha256 and member hashes describe what the run used. A fit whose
    time base attenuates the tide is taken only with its own sampling_corrected stamp (check_stamp);
    an unstamped one, or a run row whose time base is not the published one, raises
    FitIntegrityError. Returns (documents, refused): a record the run did not fit is refused."""
    audit = load_audit(audit_dir)
    manifest, base = check_fit_run(run_dir, tcd, release, audit)
    fits_map = manifest[FITS_SHA256]
    src = {rid: Path(run_dir) for rid in base}
    if overlay_dir is not None:                 # a partial refit (same TCD, GESLA and audit) replaces its records
        om, orows = check_fit_run(overlay_dir, tcd, release, audit, overlay=True)
        for rid, r in orows.items():
            if r.get("outcome") != "fitted":
                raise FitIntegrityError(f"{overlay_dir}: {rid} is {r.get('outcome')} in the overlay")
            base[rid], src[rid] = r, Path(overlay_dir)
            fits_map = {**fits_map, rid: om[FITS_SHA256][rid]}
    table = tcd.table
    meta_all = release.metadata()
    docs, refused = [], []
    for rid in record_ids:
        meta = meta_all[rid]
        row = audit.rows.get(rid)
        try:
            n_decl = declared_hours(meta)
            corr = published_correction(row, n_decl)
            tb = time_base_annotation(row, n_decl, rid, decided_in=audit.decided_in,
                                      decided_build_commit=audit.decided_build_commit)
        except NotPublishable as e:
            refused.append(refusal(rid, e.reason, str(e)))
            continue
        r = base.get(rid)
        if r is None:
            raise FitIntegrityError(f"{rid}: not in {run_dir}/results.jsonl")
        if r.get("outcome") != "fitted":
            reason = {"short": "too_short", "unusable": "fit_unusable"}.get(r.get("outcome"), "not_fitted")
            refused.append(refusal(rid, reason, f"{rid}: {r.get('outcome')}: {r.get('reason')}", r.get("data_hours")))
            continue
        if r["time_base"] != corr.label or r["months_dropped"] != len(corr.months_drop):
            raise FitIntegrityError(f"{rid}: the run fitted {r['time_base']} ({r['months_dropped']} months dropped), "
                                    f"this build publishes {corr.label} ({len(corr.months_drop)})")
        data = (src[rid] / "fits" / f"{rid}.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != fits_map[rid]:
            raise FitIntegrityError(f"{rid}: fits/{rid}.json is not the file the run recorded ({FITS_SHA256})")
        fit = json.loads(data)
        if (fit.get("first_s"), fit.get("last_s")) != (r["first"], r["last"]):
            raise FitIntegrityError(f"{rid}: the fit's span {fit.get('first_s')}-{fit.get('last_s')} is not the "
                                    f"run row's {r['first']}-{r['last']}")
        check_stamp(rid, fit, tb["published"], corr=corr, speed=table.speed,
                    span=(int(r["first"]), int(r["last"]) + 3600))
        docs.append(fit_document(rid, release.version, release.members[rid]["sha256"], meta, table, fit,
                                 good_hours=r["data_hours"], time_base=tb, calibration=calibration))
    # R8 on the whole run, with or without a calibration: an implausible fit never publishes
    fitted = {rid: {"lat": float(meta_all[rid]["LATITUDE"]), "lon": float(meta_all[rid]["LONGITUDE"])}
              for rid, r in base.items() if r.get("outcome") == "fitted" and rid in meta_all}
    run_fits = {rid: json.loads((src[rid] / "fits" / f"{rid}.json").read_bytes()) for rid in fitted}
    for rid, why in short_accuracy.run_implausible(fitted, run_fits, table).items():
        for d in docs:
            if d["record_id"] == rid and not d.get("refusal"):
                d["refusal"] = {"reason": why["reason"], "detail": f"{rid}: {why['detail']}"}
    return docs, refused


def make_docs(run_dir: Path, audit_dir: Path, out_dir: Path, *, calibration_dir: Path | None,
              overlay_dir: Path | None = None, tcd_path: Path | None = None) -> Path:
    """The fit documents of a build, from a complete fit run (and the R7 overlay refit), with the short-record
    calibration (gesla.short_accuracy.read_calibration of calibration_dir), written to out_dir (write_fits). The
    entry point of the build's fit stage. Returns the MANIFEST path; the stage's refusals go beside out_dir, to
    <out_dir>.refused.json (out_dir holds only the doc set)."""
    tcd = load_tcd_dump(tcd_path or otc_work() / "tcd_table.json")
    release = GeslaRelease()
    cal = short_accuracy.read_calibration(calibration_dir) if calibration_dir is not None else None
    ids = sorted(json.loads(x)["record"] for x in (Path(run_dir) / "results.jsonl").read_text().splitlines() if x.strip())
    docs, refused = docs_from_fit_run(run_dir, tcd, release, ids, audit_dir=audit_dir, calibration=cal,
                                      overlay_dir=overlay_dir)
    path = write_fits(docs, out_dir)
    out_dir.with_name(out_dir.name + ".refused.json").write_text(json.dumps(refused, indent=1, sort_keys=True) + "\n")
    return path


def write_fits(docs: Iterable[dict], out_dir: Path) -> Path:
    """Writes <record_id>.json per document, then MANIFEST.json (last), each through a temp file and an
    atomic rename. Refuses a directory that already holds a MANIFEST.json or any other file (one doc set
    per directory).
    On failure the files of this call are removed. Returns the manifest path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "MANIFEST.json"
    if path.exists():
        raise FileExistsError(f"{path}: the directory already holds a doc set")
    if any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir}: not empty; a doc set is written into an empty directory")
    written: list[Path] = []

    def put(name: str, data: bytes) -> None:
        final = out_dir / name
        if final.exists():
            raise FileExistsError(f"{final}: already exists")
        tmp = out_dir / f".{name}.{secrets.token_hex(8)}.tmp"
        try:
            with open(tmp, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)
        written.append(final)

    try:
        files = []
        for doc in docs:
            data = _canonical(doc)
            name = f"{doc['record_id']}.json"
            put(name, data)
            files.append({"path": name, "sha256": _sha256(data), "size": len(data),
                          "source_url": f"gesla:{doc['source_version']}/{doc['record_id']}",
                          "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        put("MANIFEST.json", _canonical({"dataset": "gesla-fit documents", "doc_version": DOC_VERSION,
                                         "files": sorted(files, key=lambda f: f["path"])}))
    except BaseException:
        for p in written:
            p.unlink(missing_ok=True)
        raise
    return path


def read_fits(in_dir: Path) -> list[dict]:
    """Every document named in MANIFEST.json, each checked against its size and SHA-256. A file in the
    directory the manifest does not name (a stray doc, a leftover temp file) raises."""
    manifest = json.loads((in_dir / "MANIFEST.json").read_text())
    stray = sorted({p.name for p in in_dir.iterdir()} - {f["path"] for f in manifest["files"]} - {"MANIFEST.json"})
    if stray:
        raise ValueError(f"{in_dir}: {stray[:5]} not in MANIFEST.json")
    if manifest.get("doc_version") != DOC_VERSION:
        raise ValueError(f"{in_dir}/MANIFEST.json: doc_version {manifest.get('doc_version')!r}, "
                         f"expected {DOC_VERSION}")
    docs = []
    for f in manifest["files"]:
        data = (in_dir / f["path"]).read_bytes()
        if _sha256(data) != f["sha256"] or len(data) != f["size"]:
            raise ValueError(f"{in_dir / f['path']}: sha256 {_sha256(data)} size {len(data)}, "
                             f"manifest has {f['sha256']} size {f['size']}")
        docs.append(json.loads(data))
    return docs


def sync_down(datestamp: str) -> Path:
    """Fetch builds/<D>/gesla/fits/ from the work bucket into the local cache."""
    subprocess.run([str(OTC_WORK_CLI), "sync-down", fits_prefix(datestamp)], check=True)
    return fits_dir(datestamp)


# --- documents -> SourceSet -----------------------------------------------------------------------

def _label_correction(rid: str, label: str) -> Correction:
    """The Correction a published time-base label stands for (the stamp check needs its kind and factors)."""
    if label == "utc_mean_start60":
        return Correction("utc_mean_start60")
    try:
        return correction_of(label)
    except ValueError:
        raise FitIntegrityError(f"{rid}: no correction for the published time base {label!r}; "
                                "its sampling stamp cannot be checked") from None


def licence_of(licence_ids: Mapping[str, str] | None, record_id: str) -> str:
    """The record's licence id from the map; a record with none raises (no default licence)."""
    if licence_ids is None or not licence_ids.get(record_id):
        raise ValueError(f"{record_id}: no licence id (the per-contributor licences, L-NC, assign it)")
    return licence_ids[record_id]


def _iso(t: int) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_name(record_id: str, name: str) -> str:
    if not CONSTITUENT_NAME_RE.fullmatch(name):
        raise NotPublishable(f"{record_id}: constituent {name!r} has no OTC canonical name", "constituent_name")
    return name


def source_set(doc: Mapping, *, licence_id: str) -> SourceSet:
    """A fit document as a gesla-fit `SourceSet` (format 1.0 constant_set without set_id and
    provenance.build, which the build adds). Raises NotPublishable for a record this build cannot
    publish yet: a short record (a candidate dropped by the Rayleigh rule needs short_record,
    dropped_constituents with not_separable_from and expected_accuracy). Raises FitIntegrityError
    for a time base that attenuates the tide on a fit with no matching sampling_corrected stamp.
    `licence_id` is required: no licence is assumed."""
    if not isinstance(licence_id, str) or not licence_id:
        raise ValueError(f"{doc.get('record_id')}: no licence id (the per-contributor licences, L-NC, assign it)")
    ss = comparator_set(doc)
    return SourceSet(station_hint=ss.station_hint, constant_set={**ss.constant_set, "licence_id": licence_id})


def comparator_set(doc: Mapping) -> SourceSet:
    """COMPARATOR ONLY: the fit document's set as `source_set` makes it, with the same checks, but with
    no licence_id. For comparisons inside the pipeline (the per-source canary), never for a release:
    the schema requires licence_id on every set, so a release holding a comparator fails
    validate_release, and the build (build_release) never takes one."""
    if doc.get("doc_version") != DOC_VERSION:
        raise ValueError(f"{doc.get('record_id')}: doc_version {doc.get('doc_version')!r}, expected {DOC_VERSION}")
    rid = doc["record_id"]
    f = doc["fit"]
    tb = doc["time_base"]
    speeds = doc["speeds_deg_per_hour"]

    def speed(n):
        if n not in speeds:
            raise FitIntegrityError(f"{rid}: the stamp names {n}, which has no speed in the document")
        return speeds[n]
    check_stamp(rid, f, tb["published"], corr=_label_correction(rid, tb["published"]), speed=speed,
                span=(int(f["first_s"]), int(f["last_s"]) + 3600))
    refused = doc.get("refusal")
    if refused:
        raise NotPublishable(refused["detail"], refused["reason"])
    missing = {n for n in CANDIDATES if n not in f["rayleigh_kept"] and n not in f["candidates_missing_from_tcd"]}
    drops = doc["rayleigh_drops"]
    if {d["name"] for d in drops} != missing:
        raise FitIntegrityError(f"{rid}: rayleigh_drops {sorted(d['name'] for d in drops)} are not the Rayleigh "
                                f"rule's drops {sorted(missing)}")
    if missing and not doc.get("expected_accuracy"):
        raise NotPublishable(f"{rid}: short record ({doc['good_hours']:.0f} good hours) with no expected_accuracy",
                             short_accuracy.UNCALIBRATED)
    speeds = doc["speeds_deg_per_hour"]
    cons = []
    for name in sorted(f["constituents"]):
        c = f["constituents"][name]
        entry = {"name": _canonical_name(rid, name), "speed_deg_per_hour": speeds[name], "amplitude_m": c["amp_m"],
                 "phase_deg": c["phase_deg"] % 360.0}
        if "u_m" in c:
            entry["amp_uncertainty_m"] = c["u_m"]
        cons.append(entry)
    cs = {
        "source": SOURCE, "source_type": "gauge", "quantity": "water_level",
        "source_record_id": rid, "source_version": doc["source_version"],
        "record_span": {"start": _iso(f["first_s"]), "end": _iso(f["last_s"]), "good_samples": f["n"],
                        "good_hours": doc["good_hours"]},
        # The zero and the named levels are the datums slots' (DT5f/DT5g/DT6); only Z0 is known here.
        "datum": {"msl_offset_m": f["z0_m"]},
        "convention_id": CONVENTION_ID,
        "time_base": dict(tb),
        "constituents": cons,
        "provenance": {"input_sha256": [doc["member_sha256"]], "tcd_sha256": doc["tcd_sha256"]},
    }
    dropped = [dict(d, name=_canonical_name(rid, d["name"]))
               for d in short_accuracy.dropped_constituents(drops, sorted(f.get("snr_dropped", ())))]
    if dropped:
        cs["dropped_constituents"] = dropped
    flags = []
    if drops:
        cs["expected_accuracy"] = dict(doc["expected_accuracy"])
        flags.append({"flag": "short_record",
                      "reason": clip(short_accuracy.short_record_reason(doc["good_hours"], drops,
                                                                        notes=doc.get("accuracy_notes") or ())),
                      "values": short_accuracy.flag_values(doc["accuracy_by_constituent"])})
        if doc.get("microtidal"):
            flags.append({"flag": "microtidal", "reason": "every main constituent (M2, S2, K1, O1) is under 5 mm: "
                                                          "the phases are not meaningful as tide phases"})
    if tb["code"] in ("unverified", "disputed"):
        flags.append({"flag": "time_base", "reason": clip(f"time base {tb['code']}: {tb['reason']}")})
    cs["qc_status"] = "flagged" if flags else "ok"
    if flags:
        cs["qc_flags"] = flags
    return SourceSet(
        station_hint={"name": doc["site_name"], "country": doc["country"], "lat": doc["lat"], "lon": doc["lon"],
                      "type": "reference", "timezone": None, "aliases": {"gesla": [rid]}},
        constant_set=cs)


def load_gesla_sets(datestamp: str | None = None, *, licence_ids: Mapping[str, str], in_dir: Path | None = None,
                    only: Iterable[str] | None = None) -> list[SourceSet]:
    """The gesla-fit sets of one build (or of in_dir), sorted by record id; `only` keeps those records.
    Every record needs its licence id in licence_ids (else ValueError). Syncs from the work bucket first
    when the local cache has no MANIFEST.json."""
    in_dir = _fits_in(datestamp, in_dir)
    return _load_sets(in_dir, only, lambda d: source_set(d, licence_id=licence_of(licence_ids, d["record_id"])))


def load_comparator_sets(datestamp: str | None = None, *, in_dir: Path | None = None,
                         only: Iterable[str] | None = None) -> list[SourceSet]:
    """COMPARATOR ONLY: the gesla-fit sets of one build (or of in_dir) as `comparator_set` makes them,
    with no licence: for the per-source canary, never for a release (see comparator_set)."""
    return _load_sets(_fits_in(datestamp, in_dir), only, comparator_set)


def _fits_in(datestamp: str | None, in_dir: Path | None) -> Path:
    if in_dir is None:
        if datestamp is None:
            raise ValueError("load_gesla_sets needs a datestamp or in_dir")
        in_dir = fits_dir(datestamp)
        if not (in_dir / "MANIFEST.json").exists():
            sync_down(datestamp)
    return in_dir


def _load_sets(in_dir: Path, only: Iterable[str] | None, make) -> list[SourceSet]:
    keep = set(only) if only is not None else None
    sets = [make(d) for d in read_fits(in_dir) if keep is None or d["record_id"] in keep]
    if keep is not None and len(sets) != len(keep):
        have = {s.constant_set["source_record_id"] for s in sets}
        raise KeyError(f"{in_dir}: no fit document for {sorted(keep - have)}")
    return sorted(sets, key=lambda s: s.constant_set["source_record_id"])


# --- CLI ------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.gesla_sets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    md = sub.add_parser("make-docs", help="write a build's fit documents from a fit run and the calibration")
    md.add_argument("--fit-run", type=Path, required=True)
    md.add_argument("--audit-dir", type=Path, required=True)
    md.add_argument("--calibration", type=Path, help="gesla.short_accuracy calibration dir (without it no short "
                    "record publishes)")
    md.add_argument("--overlay-run", type=Path, help="a partial refit replacing its records (the R7 overlay)")
    md.add_argument("--out", type=Path, required=True)
    sp = sub.add_parser("show", help="print the sets of a build as JSON")
    sp.add_argument("--datestamp", required=True)
    sp.add_argument("--licence-ids", type=Path, required=True, help="JSON {record id: licence id} (L-NC)")
    a = ap.parse_args(argv)
    if a.cmd == "make-docs":
        print(make_docs(a.fit_run, a.audit_dir, a.out, calibration_dir=a.calibration, overlay_dir=a.overlay_run))
        return 0
    for s in load_gesla_sets(a.datestamp, licence_ids=json.loads(a.licence_ids.read_text())):
        print(json.dumps({"station_hint": s.station_hint, "constant_set": s.constant_set}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
