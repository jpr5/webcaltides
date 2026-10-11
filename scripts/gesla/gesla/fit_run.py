"""Full GESLA-4.1 fit with the time-base corrections applied: python -m gesla.fit_run (the one runner; the
CR-dir copies fit-1/run_fits_tb.py and fit-2/run_fits_tb.py refuse and point here).

Environment: FIT_OUT (output dir), TIMEBASE_CSV (the audit's timebase.csv; its dir must pass
timebase_audit.check_audit_outputs), WORKERS (default 4), LIMIT (first N records), ONLY (a file of record ids).

Per record, the time base it is published with: in the audit pool, verified/corrected -> its correction (class +
bad months dropped); unverified/disputed -> GESLA's declared time base (owner rule: never dropped, worst case =
GESLA); not in the pool -> the declared time base (TIME ZONE HOURS). The series is cleaned with that correction
and the fit has the correction's sampling response divided out (select_constituents.sampling_corrected).
Outcomes: fitted | short | unusable | read_error | error. Resumable (results.jsonl). The manifest
(timebase_manifest.json) is "complete" only for a full run with no `error` outcome; exit codes: 0 complete,
4 the audit was refused, 5 a record ended as `error`, 6 FIT_OUT holds another commit's or inputs' run (a resume
across a change is refused before anything is written).
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from .fit import FitError
from .qc import clean
from .read_gesla import GeslaRelease, ShaMismatch, load_contributor_roles, otc_work
from .select_constituents import FIT_SPAN_YEARS, fit_record, in_tcd_years, last_years, record_hours, sampling_corrected
from .tcd_table import TcdTable
from .timebase_audit import check_audit_outputs, correction_of, declared_correction, write_manifest
from .timebase_verdict import Correction

WT = Path(__file__).resolve().parents[3]            # the checkout (the manifest names its commit)
ROLES = WT / "data/gesla/contributor_roles.csv"
OUT = Path(os.environ.get("FIT_OUT", "."))
TIMEBASE = Path(os.environ.get("TIMEBASE_CSV", "timebase.csv"))
RESULTS = OUT / "results.jsonl"
FITS = OUT / "fits"
SHORT_HOURS = 15 * 24
MAX_AMP_M = 20.0
MAIN = ("M2", "S2", "K1", "O1")

_rel = _roles = _table = _tb = None


def published(row: dict | None, n_decl: int) -> tuple[Correction, str]:
    """The Correction a record is published (and fitted) with, and where it came from."""
    if row is None:
        return declared_correction(n_decl), "not in the audit pool: declared"
    if row["time_base_status"] not in ("verified", "corrected"):
        return declared_correction(n_decl), f"{row['time_base_status']}: declared"
    label = row["published_time_base"]
    corr = Correction("utc_mean_start60") if label == "utc_mean_start60" else correction_of(label)
    if row["months_bad"]:
        corr = replace(corr, months_drop=tuple((int(m[:4]), int(m[5:7])) for m in row["months_bad"].split()))
    return corr, row["time_base_code"]


def load_timebase() -> dict:
    return {r["file_id"]: r for r in csv.DictReader(open(TIMEBASE, newline=""))}


def _init():
    global _rel, _roles, _table, _tb
    _rel = GeslaRelease()
    _rel.metadata()
    _roles = load_contributor_roles(ROLES)
    _table = TcdTable.load(otc_work() / "tcd_table.json")
    _tb = load_timebase()


def one(name: str) -> dict:
    t0 = time.time()
    meta = _rel.metadata()[name]
    n_decl = int(float(meta.get("TIME ZONE HOURS") or 0))
    corr, why = published(_tb.get(name), n_decl)
    out = {"record": name, "gauge_type_meta": meta.get("GAUGE TYPE"),
           "contributor": meta.get("CONTRIBUTOR (ABBREVIATED)"), "country": meta.get("COUNTRY"),
           "lat": float(meta["LATITUDE"]), "lon": float(meta["LONGITUDE"]),
           "time_base": corr.label, "months_dropped": len(corr.months_drop), "time_base_from": why,
           "time_base_status": (_tb.get(name) or {}).get("time_base_status", "not_in_pool")}
    # read_error only for a malformed record (the parser's own ValueError); a member whose bytes do not match the
    # release manifest (ShaMismatch), a record with no usable verdict (UnexplainedRecord, from clean) and any other
    # exception are errors of the run (exit 5), never a quiet read_error
    try:
        rec = _rel.record(name, _roles)
    except ShaMismatch as e:
        out.update(outcome="error", reason=f"ShaMismatch: {str(e)[:300]}", secs=round(time.time() - t0, 2))
        return out
    except ValueError as e:
        out.update(outcome="read_error", reason=f"{type(e).__name__}: {str(e)[:300]}", secs=round(time.time() - t0, 2))
        return out
    except Exception as e:  # noqa: BLE001
        out.update(outcome="error", reason=f"{type(e).__name__}: {str(e)[:300]}", tb=traceback.format_exc()[-800:],
                   secs=round(time.time() - t0, 2))
        return out
    try:
        out.update(gauge_type=rec.gauge_type, licence_class=rec.licence_class, originator=rec.originator,
                   interval_min=rec.interval_min, rows=len(rec), good=int(rec.good.sum()))
        series = clean(rec, corr)
    except Exception as e:  # noqa: BLE001
        out.update(outcome="error", reason=f"{type(e).__name__}: {str(e)[:300]}", tb=traceback.format_exc()[-800:],
                   secs=round(time.time() - t0, 2))
        return out
    win = in_tcd_years(_table, last_years(series, FIT_SPAN_YEARS))
    hrs = record_hours(win.times_s)
    w0 = int(win.times_s.min()) if len(win) else None
    w1 = int(win.times_s.max()) if len(win) else None
    # first/last: the window here; for a fitted record, the span the fit used (after despiking), as in its fit file
    out.update(hourly=len(series), window_samples=len(win), data_hours=round(hrs, 1),
               window_first=w0, window_last=w1, first=w0, last=w1)
    if hrs < SHORT_HOURS:
        out.update(outcome="short", reason=("no good data" if len(win) == 0 else f"{hrs / 24:.1f} days of good hourly data"),
                   secs=round(time.time() - t0, 2))
        return out
    try:
        rf = fit_record(_table, series)
        out.update(first=int(rf.primary.first_s), last=int(rf.primary.last_s))
        # the sampling response of the time base (utc_mean_start60: an hourly mean) divided out per constituent,
        # over the span the fit used (the consumer recomputes the stamp's factors from that same span)
        rf = sampling_corrected(rf, _table, corr, (int(rf.primary.first_s), int(rf.primary.last_s) + 3600))
    except FitError as e:
        out.update(outcome="unusable", reason=f"FitError: {str(e)[:300]}", secs=round(time.time() - t0, 2))
        return out
    except Exception as e:  # noqa: BLE001
        out.update(outcome="error", reason=f"{type(e).__name__}: {str(e)[:300]}",
                   tb=traceback.format_exc()[-800:], secs=round(time.time() - t0, 2))
        return out
    p = rf.primary.fit
    amps = p.amplitude_m
    out.update(n_cons=len(p.names), rms_m=round(p.rms_m, 5), snr_dropped=len(rf.primary.snr_dropped),
               main={n: [round(amps[n], 5), round(p.phase_deg[n], 3), round(rf.u.get(n, math.nan), 5)]
                     for n in MAIN if n in amps},
               half_errors=len(rf.half_errors))
    bad_amp = [n for n, a in amps.items() if not math.isfinite(a) or a > MAX_AMP_M]
    kept_main = [n for n in MAIN if n in amps]
    if bad_amp:
        out.update(outcome="unusable", reason=f"absurd amplitude {bad_amp[:5]}")
    elif not kept_main:
        out.update(outcome="unusable", reason=f"none of M2/S2/K1/O1 above the noise ({len(p.names)} kept)")
    else:
        out.update(outcome="fitted", reason=f"{len(p.names)} constituents")
        try:
            _write_fit(name, rf.to_json())
        except Exception as e:  # noqa: BLE001 - the record is an error of the run, its file gone
            out.update(outcome="error", reason=f"fit file write failed: {type(e).__name__}: {str(e)[:200]}",
                       write_error=str(e)[:200])
    out["secs"] = round(time.time() - t0, 2)
    return out


def _write_fit(name: str, text: str) -> None:
    """fits/<name>.json written atomically (tmp + os.replace); any earlier file is removed first, so a failed
    write never leaves an older run's (or a partial) file behind to be hashed as this run's fit."""
    final = FITS / f"{name}.json"
    tmp = FITS / f".{name}.json.tmp{os.getpid()}"
    final.unlink(missing_ok=True)
    try:
        tmp.write_text(text)
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise


def _identity(inputs: dict) -> dict:
    """The commit, dirty flag and input sha256s a run is bound to (what a resume must not change)."""
    from .timebase_audit import _git
    from .read_gesla import sha256_file
    st = _git(WT, "status", "--porcelain")
    return {"commit": (_git(WT, "rev-parse", "HEAD") or "unknown").strip(),
            "dirty": bool(st and st.strip()) if st is not None else True,
            "inputs": {k: (sha256_file(v) if v is not None and Path(v).is_file() else None)
                       for k, v in sorted(inputs.items())}}


def resume_refusal(out: Path, inputs: dict) -> str | None:
    """None when out may be (re)used: empty, or holding a run of the same commit, clean state and inputs. Else why
    not (results without a manifest; another commit, a dirty tree, or another input): the caller refuses (exit 6)
    and leaves that run's files, its manifest included, untouched."""
    res, mp = out / "results.jsonl", out / "timebase_manifest.json"
    if not res.exists() and not mp.exists():
        return None
    if not mp.is_file():
        return f"{out}: results.jsonl without a timebase_manifest.json: not resumable"
    old = json.loads(mp.read_text())
    cur = _identity(inputs)
    if old.get("commit") != cur["commit"] or old.get("dirty") is not False or cur["dirty"]:
        return (f"{out}: holds a run of commit {str(old.get('commit'))[:12]} (dirty {old.get('dirty')}), this is "
                f"{cur['commit'][:12]} (dirty {cur['dirty']}): use a fresh FIT_OUT")
    was = {k: (v or {}).get("sha256") for k, v in (old.get("inputs") or {}).items()}
    if was != cur["inputs"]:
        bad = sorted(k for k in set(was) | set(cur["inputs"]) if was.get(k) != cur["inputs"].get(k))
        return f"{out}: holds a run with other inputs ({', '.join(bad)}): use a fresh FIT_OUT"
    return None


def configure() -> None:
    """Re-read the environment (main() calls it; tests set the variables first)."""
    global OUT, TIMEBASE, RESULTS, FITS
    OUT = Path(os.environ["FIT_OUT"])
    TIMEBASE = Path(os.environ["TIMEBASE_CSV"])
    RESULTS, FITS = OUT / "results.jsonl", OUT / "fits"


def main() -> int:
    configure()
    workers = int(os.environ.get("WORKERS", "4"))
    limit = int(os.environ.get("LIMIT", "0"))
    try:                                    # never fit a crashed, killed or changed audit
        audit_m = check_audit_outputs(TIMEBASE.parent)
    except ValueError as e:
        print(f"refusing to fit: {e}", file=sys.stderr, flush=True)
        return 4
    FITS.mkdir(parents=True, exist_ok=True)
    rel = GeslaRelease()
    names = [n for n in sorted(rel.metadata()) if n in rel.members]
    tb = load_timebase()
    # every used row's correction must rebuild to its CSV correction (else stop before fitting anything)
    for f, r in tb.items():
        if r["time_base_status"] in ("verified", "corrected"):
            c, _ = published(r, 0)
            if c.label != r["correction"]:
                raise SystemExit(f"{f}: rebuilt correction {c.label} != CSV {r['correction']}")
    inputs = {"timebase_csv": TIMEBASE, "tcd_table": otc_work() / "tcd_table.json", "roles": ROLES,
              "gesla_metadata": Path(rel.root) / rel.metadata_path, "gesla_manifest": Path(rel.root) / "MANIFEST.json",
              "runner": Path(__file__)}
    argv = [f"WORKERS={workers}", f"LIMIT={limit}", f"ONLY={os.environ.get('ONLY', '')}"]
    extra = {"audit": {"dir": str(TIMEBASE.parent), "commit": audit_m.get("commit"), "status": audit_m["status"],
                       "weak_crashes": audit_m["weak_crashes"]},
             "config": {"otc_work": str(otc_work()), "release_root": str(rel.root), "out": str(OUT),
                        "short_hours": SHORT_HOURS, "max_amp_m": MAX_AMP_M, "fit_span_years": FIT_SPAN_YEARS,
                        "records": len(names), "in_audit_pool": sum(1 for n in names if n in tb)}}
    why = resume_refusal(OUT, inputs)
    if why:
        print(f"refusing to resume: {why}", file=sys.stderr, flush=True)
        return 6
    run_m = write_manifest(OUT, WT, argv, inputs, status="running", extra=extra)
    done = set()
    if RESULTS.exists():
        for line in RESULTS.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["record"])
    todo = [n for n in names if n not in done]
    only = os.environ.get("ONLY")
    if only:
        want = set(Path(only).read_text().split())
        todo = [n for n in todo if n in want]
    if limit:
        todo = todo[:limit]
    total = len(names)
    print(f"{time.strftime('%H:%M:%S')} records {total}, done {len(done)}, to do {len(todo)}, workers {workers}, "
          f"commit {run_m['commit'][:12]}{' DIRTY' if run_m['dirty'] else ''}", flush=True)
    t0 = last = time.time()
    n = len(done)
    with _executor(workers) as ex, RESULTS.open("a") as f:
        futs = {ex.submit(one, nm): nm for nm in todo}
        for fu in as_completed(futs):
            try:
                r = fu.result()
            except Exception as e:  # noqa: BLE001
                r = {"record": futs[fu], "outcome": "error", "reason": f"worker: {type(e).__name__}: {e}"[:300]}
            f.write(json.dumps(r) + "\n")
            f.flush()
            n += 1
            if time.time() - last > 60:
                last = time.time()
                rate = (n - len(done)) / (last - t0)
                print(f"{time.strftime('%H:%M:%S')} {n}/{total} done, {rate * 60:.1f}/min, "
                      f"eta {(total - n) / rate / 60 if rate else float('nan'):.0f} min", flush=True)
    rows = [json.loads(x) for x in RESULTS.read_text().splitlines() if x.strip()]
    from collections import Counter
    outcomes = Counter(r["outcome"] for r in rows)
    # every fitted record's fit file, hashed (the consumer refuses a fits/<rid>.json that does not match);
    # a fitted record without its file is an error of the run
    from .read_gesla import sha256_file
    fits_sha = {r["record"]: sha256_file(FITS / f"{r['record']}.json") for r in rows
                if r["outcome"] == "fitted" and not r.get("write_error") and (FITS / f"{r['record']}.json").is_file()}
    errors = sorted({r["record"] for r in rows if r["outcome"] == "error" or r.get("write_error")}
                    | {r["record"] for r in rows if r["outcome"] == "fitted" and r["record"] not in fits_sha})
    status = "incomplete" if errors else ("complete" if not (limit or os.environ.get("ONLY")) else "partial")
    write_manifest(OUT, WT, argv, inputs, status=status, started=run_m["started"], outputs=[RESULTS], run=run_m,
                   extra={**extra, "outcomes": dict(outcomes), "n_results": len(rows), "errors": errors,
                          "fits_sha256": dict(sorted(fits_sha.items()))})
    print(f"{time.strftime('%H:%M:%S')} finished {n}/{total} in {(time.time() - t0) / 60:.1f} min", flush=True)
    if errors:
        print(f"ERROR: {len(errors)} record(s) ended as outcome error: {', '.join(errors[:20])}", file=sys.stderr,
              flush=True)
        return 5
    return 0


class _Serial:
    """In-process executor (WORKERS=1, and the tests): same submit/as_completed shape as the pool."""

    def __init__(self):
        _init()

    def submit(self, fn, *a):
        from concurrent.futures import Future
        fu = Future()
        try:
            fu.set_result(fn(*a))
        except Exception as e:  # noqa: BLE001 - surfaced by fu.result() in main
            fu.set_exception(e)
        return fu

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _executor(workers: int):
    return _Serial() if workers <= 1 else ProcessPoolExecutor(max_workers=workers, initializer=_init)


if __name__ == "__main__":
    sys.exit(main())
