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
  constituent the release can publish, and build_release checks it against the run.

The station map, epoch hours, licences and withheld switches are the committed files under data/. build_commit is
the repository HEAD; a tree with uncommitted changes to tracked files is refused, so the output is the committed
code's. Writes the release files (release_writer.write_release) and BUILD_REPORT.json to OUT, which must be empty.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
from otc_pipeline.astro_tables import TABLE_ID, astro_table, load_congen_run
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


def build(*, fit_run: Path, audit_dir: Path, verdicts_dir: Path, calibration_dir: Path, overlay_run: Path | None,
          ticon_csv: Path, ticon_refs: Path, congen_run: Path, datestamp: str, created: str, build_commit: str,
          tcd_path: Path | None = None, log=print):
    """(release document, build report, congen run) (see the module docstring)."""
    rel = GeslaRelease()
    dump = gs.load_tcd_dump(tcd_path or otc_work() / "tcd_table.json")
    table = dump.table
    rows = [json.loads(x) for x in (fit_run / "results.jsonl").read_text().splitlines() if x.strip()]
    fitted = sorted(r["record"] for r in rows if r["outcome"] == "fitted")
    cal = read_calibration(calibration_dir)
    docs, refused = gs.docs_from_fit_run(fit_run, dump, rel, fitted, audit_dir=audit_dir, calibration=cal,
                                         overlay_dir=overlay_run, eot20=gs.load_eot20(audit_dir))
    log(f"fit documents: {len(docs)} of {len(fitted)} fitted records ({len(rows)} rows), fit-stage refusals {len(refused)}")
    licences, licence_ids = gesla_licences(docs)
    smap = sm.load()
    final = gs.load_audit(verdicts_dir)
    carry = ts.TiconCarry.from_audit(ts.load_ticon(ticon_csv), final, rel.metadata(), references_path=ticon_refs)
    names = {n for d in docs for n in d["fit"]["constituents"]} | ts.carried_names(carry.records, table)
    crun = load_congen_run(congen_run, tcd_sha256=table.tcd_sha256)
    congen, tcd = crun
    astro = [astro_table(congen, tcd, sorted(names), table_id=TABLE_ID)]
    release, report = gr.build_release(
        docs, table=table, table_id=TABLE_ID, datestamp=datestamp, created=created, build_commit=build_commit,
        station_ids=smap.record_ids(), licences=licences, licence_ids=licence_ids, withheld=withheld_licences(),
        astro_tables=astro, congen_run=crun, refused=refused, epoch_hours=sm.load_epoch_hours(),
        sibling_flags=smap.sibling_flags(), tombstones=smap.tombstones(), station_map_inputs=smap.inputs,
        station_aliases=smap.release_aliases(),
        fit_run_results_sha256=hashlib.sha256((fit_run / "results.jsonl").read_bytes()).hexdigest(),
        disputed_reasons=gr.disputed_reasons_of(final.rows), ticon=carry)
    return release, report, crun


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.gesla_build", description=(__doc__ or "").split("\n")[0], allow_abbrev=False)
    for name in ("fit-run", "audit-dir", "verdicts-dir", "calibration", "ticon-csv", "ticon-refs", "congen-run", "out"):
        ap.add_argument(f"--{name}", type=Path, required=True)
    ap.add_argument("--overlay-run", type=Path)
    ap.add_argument("--tcd", type=Path, help="the TCD dump (default $OTC_WORK/tcd_table.json)")
    ap.add_argument("--datestamp", required=True)
    ap.add_argument("--created", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out}: not empty")
    commit = head_commit()
    _log(t0, f"build_commit {commit}")
    release, report, crun = build(fit_run=a.fit_run, audit_dir=a.audit_dir, verdicts_dir=a.verdicts_dir,
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
    report.write(a.out / "BUILD_REPORT.json")
    _log(t0, f"wrote {[p.name for p in files]} + BUILD_REPORT.json to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
