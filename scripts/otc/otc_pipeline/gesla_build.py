"""The GESLA release build, end to end from committed code: one command from a gated fit run to the release files.

    python -m otc_pipeline.gesla_build --fit-run RUN --audit-dir AUDIT --verdicts-dir FINAL \\
        --calibration CAL [--overlay-run R7] --ticon-csv TICON.csv --ticon-refs refs.json \\
        --congen-run CONGEN --datestamp YYYYMMDD --created ISO8601 --out OUT

Inputs (none of them is in git; each is checked where it is read):
- RUN: a complete in-repo fit run (gesla.fit_run: results.jsonl, fits/, timebase_manifest.json with fits_sha256),
  fitted against the audit in AUDIT (gesla_sets.check_fit_run); its results.jsonl is the one the committed station
  map was minted from (build_release checks the pin).
- FINAL: the newest finished time-base audit (the final verdicts). Its disputed reasons (disputed_reasons_of) decide
  what is never published (FIX-DISP, the whole-hour EOT20 rule), and it annotates the TICON-4 sets' time bases.
  The fit documents keep the annotation of AUDIT, the audit the fit used.
- AUDIT's weak inputs (EOT20 at each gauge, sha256-checked against its manifest): every fit is screened for a sign
  inversion (gesla_sets.screen_inverted).
- CAL: the short-record calibration (gesla.short_accuracy.read_calibration); R7: its partial overlay refit.
- TICON.csv: the pinned TICON-4 download; refs.json: ticon_refs' official references (its SHA-256 goes into every
  carried set's provenance).
- CONGEN: a congen_tables.sh run (astro_tables.load_congen_run); the astro table is built from it for every
  constituent the release can publish, and build_release checks it against the run. The release is built on the
  OTC TCD (astro_tables.TABLE_ID, TCD_SHA256: tcd-20251228 + MA2 and MB2): the fit's TCD dump must be of it (the
  run's tcd-1900-2100.json) and the run must hold the OTC TCD file; anything else is refused.

The station map, epoch hours, licences and withheld switches are the committed files under data/. build_commit is
the repository HEAD; a tree with uncommitted changes to tracked files is refused, so the output is the committed
code's. Writes the release files (release_writer.write_release) and BUILD_REPORT.json to OUT, which must be empty.

BUILD_REPORT.json carries `provenance` (the 1.0 release document has no place for it): the command line and working
directory, build_commit, and each input's path (resolved) and content hash: the fit run's and the overlay's
results.jsonl and timebase_manifest.json, each audit dir's timebase_manifest.json (which binds its outputs) and
timebase.csv with its commit, the calibration's short_accuracy.json, the congen run's PROVENANCE.json, the TCD dump,
the OTC TCD file (its id and SHA-256, from the run's PROVENANCE, which load_congen_run checked on disk), the
TICON-4 csv and refs.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from gesla.read_gesla import GeslaRelease, otc_work
from gesla.short_accuracy import read_calibration

from otc_pipeline import gesla_release as gr
from otc_pipeline import gesla_sets as gs
from otc_pipeline import station_map as sm
from otc_pipeline import ticon_sets as ts
from otc_pipeline.astro_tables import TABLE_ID, TCD_SHA256, AstroTableMismatch, astro_table, load_congen_run
from otc_pipeline.gesla_licences import gesla_licences, withheld_licences
from otc_pipeline.release_writer import validate_release, write_release

REPO = Path(__file__).resolve().parents[3]


def _log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)


def head_commit(repo: Path = REPO) -> str:
    """The repository HEAD; refuses a tree whose tracked files differ from it."""
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit(f"{repo}: uncommitted changes to tracked files; the release is built from committed code only:\n"
                         f"{dirty[:2000]}")
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _path(path: Path) -> str:
    return str(Path(path).resolve())


def _run_identity(run: Path) -> dict:
    return {"path": _path(run), "results_sha256": _sha256(run / "results.jsonl"),
            "manifest_sha256": _sha256(run / "timebase_manifest.json")}


def _audit_identity(audit_dir: Path, audit: gs.Audit) -> dict:
    return {"path": _path(audit_dir), "manifest_sha256": _sha256(audit_dir / "timebase_manifest.json"),
            "timebase_csv_sha256": audit.csv_sha256, "commit": audit.decided_build_commit}


def build(*, fit_run: Path, audit_dir: Path, verdicts_dir: Path, calibration_dir: Path, overlay_run: Path | None,
          ticon_csv: Path, ticon_refs: Path, congen_run: Path, datestamp: str, created: str, build_commit: str,
          tcd_path: Path | None = None, log=print):
    """(release document, build report, congen run, provenance) (see the module docstring); provenance is
    BUILD_REPORT.json's, without the command line (main adds it)."""
    rel = GeslaRelease()
    tcd_path = tcd_path or otc_work() / "tcd_table.json"
    dump = gs.load_tcd_dump(tcd_path)
    table = dump.table
    if table.tcd_sha256 != TCD_SHA256:
        raise SystemExit(f"{tcd_path}: a dump of TCD {table.tcd_sha256}; the release is built on the OTC TCD "
                         f"{TABLE_ID} ({TCD_SHA256}): fit and build with its dump (the congen run's tcd-1900-2100.json)")
    results = (fit_run / "results.jsonl").read_bytes()      # one read: hashed (the station map pin), then parsed
    rows = [json.loads(x) for x in results.decode("utf-8").splitlines() if x.strip()]
    fitted = sorted(r["record"] for r in rows if r["outcome"] == "fitted")
    cal = read_calibration(calibration_dir)
    docs, refused = gs.docs_from_fit_run(fit_run, dump, rel, fitted, audit_dir=audit_dir, calibration=cal,
                                         overlay_dir=overlay_run, eot20=gs.load_eot20(audit_dir))
    log(f"fit documents: {len(docs)} of {len(fitted)} fitted records ({len(rows)} rows), fit-stage refusals {len(refused)}")
    licences, licence_ids = gesla_licences(docs)
    smap = sm.load()
    audit = gs.load_audit(audit_dir)
    final = gs.load_audit(verdicts_dir)
    carry = ts.TiconCarry.from_audit(ts.load_ticon(ticon_csv), final, rel.metadata(), references_path=ticon_refs)
    names = {n for d in docs for n in d["fit"]["constituents"]} | ts.carried_names(carry.records, table)
    try:
        crun = load_congen_run(congen_run, tcd_sha256=table.tcd_sha256)
    except AstroTableMismatch as e:
        raise SystemExit(f"--congen-run: {e}") from None
    otc_file = crun.provenance["files"].get("otc_tcd")
    if not otc_file:
        raise SystemExit(f"{congen_run}: the congen run holds no OTC TCD file (files.otc_tcd): run congen_tables.sh")
    congen, tcd = crun
    astro = [astro_table(congen, tcd, sorted(names), table_id=TABLE_ID)]
    results_sha256 = hashlib.sha256(results).hexdigest()
    release, report = gr.build_release(
        docs, table=table, table_id=TABLE_ID, datestamp=datestamp, created=created, build_commit=build_commit,
        station_ids=smap.record_ids(), licences=licences, licence_ids=licence_ids, withheld=withheld_licences(),
        astro_tables=astro, congen_run=crun, refused=refused, epoch_hours=sm.load_epoch_hours(),
        sibling_flags=smap.sibling_flags(), tombstones=smap.tombstones(), station_map_inputs=smap.inputs,
        station_aliases=smap.release_aliases(),
        fit_run_results_sha256=results_sha256,
        disputed_reasons=gr.disputed_reasons_of(final.rows), ticon=carry)
    fit_identity = _run_identity(fit_run)
    if fit_identity["results_sha256"] != results_sha256:
        raise SystemExit(f"{fit_run}/results.jsonl changed during the build")
    provenance = {
        "build_commit": build_commit,
        "fit_run": fit_identity,
        "overlay_run": _run_identity(overlay_run) if overlay_run is not None else None,
        "audit": _audit_identity(audit_dir, audit),
        "verdicts": _audit_identity(verdicts_dir, final),
        "calibration": {"path": _path(calibration_dir), "sha256": cal["_sha256"]},
        "congen_run": {"path": _path(crun.run_dir), "provenance_sha256": _sha256(crun.run_dir / "PROVENANCE.json")},
        "tcd": {"path": _path(dump.path), "sha256": dump.sha256},
        "otc_tcd": {"id": TABLE_ID, "path": _path(crun.run_dir / otc_file["path"]), "sha256": otc_file["sha256"]},
        "ticon_csv": {"path": _path(ticon_csv), "sha256": _sha256(ticon_csv)},
        "ticon_refs": {"path": _path(ticon_refs), "sha256": _sha256(ticon_refs)},
    }
    return release, report, crun, provenance


def write_report(report: gr.BuildReport, provenance: dict, path: Path) -> Path:
    """BUILD_REPORT.json: the build report (BuildReport.as_dict) with `provenance`, through a temp file and an
    atomic rename."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with open(tmp, "xb") as fh:
            fh.write((json.dumps({**report.as_dict(), "provenance": provenance}, sort_keys=True, indent=1)
                      + "\n").encode())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.gesla_build", description=(__doc__ or "").split("\n")[0], allow_abbrev=False)
    for name in ("fit-run", "audit-dir", "verdicts-dir", "calibration", "ticon-csv", "ticon-refs", "congen-run", "out"):
        ap.add_argument(f"--{name}", type=Path, required=True)
    ap.add_argument("--overlay-run", type=Path)
    ap.add_argument("--tcd", type=Path, help="the TCD dump (default $OTC_WORK/tcd_table.json)")
    ap.add_argument("--datestamp", required=True)
    ap.add_argument("--created", required=True)
    argv = sys.argv[1:] if argv is None else list(argv)
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out}: not empty")
    commit = head_commit()
    _log(t0, f"build_commit {commit}")
    release, report, crun, provenance = build(fit_run=a.fit_run, audit_dir=a.audit_dir, verdicts_dir=a.verdicts_dir,
                                  calibration_dir=a.calibration, overlay_run=a.overlay_run, ticon_csv=a.ticon_csv,
                                  ticon_refs=a.ticon_refs, congen_run=a.congen_run, datestamp=a.datestamp,
                                  created=a.created, build_commit=commit, tcd_path=a.tcd,
                                  log=lambda m: _log(t0, m))
    _log(t0, f"build_release: {report.summary()}")
    errs = validate_release(release)
    _log(t0, f"validate_release: {len(errs)} errors")
    for e in errs[:20]:
        print("  ", e)
    if errs:
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    files = write_release(release, a.out, datestamp=a.datestamp, app_export=None, congen_run=crun)
    provenance = {"command_line": [sys.executable, "-m", "otc_pipeline.gesla_build", *argv], "cwd": os.getcwd(),
                  **provenance}
    write_report(report, provenance, a.out / "BUILD_REPORT.json")
    _log(t0, f"wrote {[p.name for p in files]} + BUILD_REPORT.json to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
