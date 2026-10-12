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
  Its reviewed withholds must be exactly the committed list (check_reviewed_withhold), else the build stops.
  The fit documents keep the annotation of AUDIT, the audit the fit used.
- AUDIT's weak inputs (EOT20 at each gauge, sha256-checked against its manifest): every fit is screened for a sign
  inversion (gesla_sets.screen_inverted).
- CAL: the short-record calibration (gesla.short_accuracy.read_calibration); R7: its partial overlay refit. CAL
  must have been made from RUN (then R7) on the build's TCD dump (short_accuracy.check_bound), else the build
  stops: its targets are keyed to their fits, and on other fits every short record it covers would be refused.
- TICON.csv: the pinned TICON-4 download; refs.json: ticon_refs' official references (its SHA-256 goes into every
  carried set's provenance).
- CONGEN: a congen_tables.sh run (astro_tables.load_congen_run); the astro table is built from it for every
  constituent the release can publish, and build_release checks it against the run. The release is built on the
  OTC TCD (astro_tables.TABLE_ID, TCD_SHA256: tcd-20251228 + MA2 and MB2): the fit's TCD dump must be of it (the
  run's tcd-1900-2100.json) and the run must hold the OTC TCD file; anything else is refused. The dump each audit
  (AUDIT, FINAL) read must be this dump or one with only rows of it (audit_dump), else the build stops.

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
from dataclasses import dataclass
from pathlib import Path

from gesla.read_gesla import GeslaRelease
from gesla.short_accuracy import check_bound, read_calibration
from gesla.tcd_table import dump_path

from otc_pipeline import gesla_release as gr
from otc_pipeline import gesla_sets as gs
from otc_pipeline import station_map as sm
from otc_pipeline import ticon_sets as ts
from otc_pipeline.astro_tables import (OTC_ADDED, SOURCE_TCD_SHA256, TABLE_ID, TCD_SHA256, AstroTableMismatch,
                                       astro_table, load_congen_run)
from otc_pipeline.gesla_licences import gesla_licences, withheld_licences
from otc_pipeline.release_writer import validate_release, write_release
from otc_pipeline.source_inventory import (SourceInventory, inventory_arguments, inventory_from_arguments,
                                           load_source_inventory, report_inventory)

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


def audit_dump(audit_dir: Path, dump) -> dict:
    """The TCD dump the time-base audit in `audit_dir` read (its manifest's inputs.tcd_table), checked against the
    build's dump: the same file ("same"), or a dump whose every row (speed, V0+u and f for every year), alias and
    year range is the build dump's, which only has the OTC additions more ("rows of the build's": the XTide dump
    before MA2 and MB2 were added). Anything else, a manifest without it, or a file that is no longer the one the
    audit hashed, stops the build: the audit, the fits and the agency sets read one table. The second case is exact: the audit's dump is of
    the XTide TCD the OTC TCD is made from (astro_tables.SOURCE_TCD_SHA256), the build's of the OTC TCD, and the rows
    the audit's lacks are exactly the OTC additions (astro_tables.OTC_ADDED). The dump is read from its pinned copy
    in the audit dir (pinned_audit_dump) when there is one, else from the path the manifest records."""
    m = json.loads((Path(audit_dir) / "timebase_manifest.json").read_text())
    rec = (m.get("inputs") or {}).get("tcd_table")
    if not rec or not rec.get("path") or not rec.get("sha256"):
        raise SystemExit(f"{audit_dir}: the audit manifest names no TCD dump (inputs.tcd_table)")
    if rec["sha256"] == dump.sha256:
        return {"path": rec["path"], "sha256": rec["sha256"], "relation": "same"}
    path, pinned = Path(rec["path"]), pinned_audit_dump(audit_dir, rec["sha256"])
    if pinned.exists():
        if _sha256(pinned) != rec["sha256"]:
            raise SystemExit(f"{pinned}: the pinned copy of the audit's TCD dump is not the dump it read ({rec['sha256']})")
        path = pinned
    elif not path.is_file() or _sha256(path) != rec["sha256"]:
        raise SystemExit(f"{audit_dir}: the audit read the TCD dump {path} ({rec['sha256']}), another than the build's "
                         f"{dump.path} ({dump.sha256}), and that file is not there as the audit hashed it (pin the "
                         f"audit's exact dump as {pinned.name} in the audit dir)")
    data, mine = json.loads(path.read_bytes()), json.loads(Path(dump.path).read_bytes())
    other = sorted(n for n, c in data["constituents"].items() if mine["constituents"].get(n) != c)
    not_read = sorted(set(mine["constituents"]) - set(data["constituents"]))
    why = [w for w, bad in ((f"a dump of TCD {data.get('tcd_sha256')}, not the XTide TCD {SOURCE_TCD_SHA256} the OTC TCD "
                             f"is made from", data.get("tcd_sha256") != SOURCE_TCD_SHA256),
                            (f"the build dump is of TCD {mine.get('tcd_sha256')}, not the OTC TCD {TCD_SHA256}",
                             mine.get("tcd_sha256") != TCD_SHA256),
                            (f"rows that differ or are not in the build's: {other[:20]}", bool(other)),
                            (f"rows it lacks are {not_read[:20]}, not the OTC additions {sorted(OTC_ADDED)}",
                             not_read != sorted(OTC_ADDED)),
                            ("other years", data["years"] != mine["years"]),
                            ("other aliases", data["aliases"] != mine["aliases"])) if bad]
    if why:
        raise SystemExit(f"{audit_dir}: the audit read the TCD dump {path} ({rec['sha256']}), another table than the "
                         f"build's {dump.path} ({dump.sha256}): " + "; ".join(why))
    return {"path": _path(path), "recorded_path": rec["path"], "sha256": rec["sha256"],
            "relation": "rows of the build's", "rows_not_read": not_read}


def pinned_audit_dump(audit_dir: Path, sha256: str) -> Path:
    """Where the audit's exact TCD dump is pinned: `tcd_table.<sha256>.json` in the audit dir. The audits record the
    shared $OTC_WORK/tcd_table.json, which a later dump would overwrite; audit_dump reads the pinned copy first (it
    must hash to what the audit recorded)."""
    return Path(audit_dir) / f"tcd_table.{sha256}.json"


def check_reviewed_withhold(verdicts_dir: Path, final: gs.Audit, withhold_path: Path | None = None) -> dict:
    """The final verdicts carry exactly the committed reviewed-withhold list (gesla.timebase_audit.WITHHOLD_PATH):
    their manifest records the list it applied (inputs.reviewed_withhold) with the committed file's SHA-256, and the
    records the audit disputed on reviewer evidence are exactly the listed records it has rows for, each disputed so.
    Anything else stops the build: verdicts older or newer than the committed list would publish a withheld record
    unlabelled, or withhold one the list no longer holds. Returns the binding for provenance."""
    from gesla.timebase_audit import REVIEWER_DISPUTED, WITHHOLD_PATH, load_withhold
    path = Path(withhold_path or WITHHOLD_PATH)
    want = _sha256(path)
    m = json.loads((Path(verdicts_dir) / "timebase_manifest.json").read_text())
    got = ((m.get("inputs") or {}).get("reviewed_withhold") or {}).get("sha256")
    listed = {e["file_id"] for e in load_withhold(path)}
    disputed = {f for f, r in final.rows.items() if r.get("time_base_code") == REVIEWER_DISPUTED}
    missing = sorted((listed & set(final.rows)) - disputed)
    extra = sorted(disputed - listed)
    if got != want or missing or extra:
        raise SystemExit(f"{verdicts_dir}: the verdicts' reviewed withholds are not the committed list {path} ({want}): "
                         f"the audit applied {got}; listed but not disputed on reviewer evidence: {missing}; disputed "
                         f"on reviewer evidence but not listed: {extra}. Build with verdicts made on the committed list")
    return {"path": str(path), "sha256": want, "records": len(listed), "disputed": len(disputed)}


@dataclass
class BuildParts:
    """What build_parts makes: the release and its report, the congen run and provenance (build's result), and
    the inputs a combined build reads again: the fit documents (the agency canaries' comparators), the final
    audit's disputed reasons, the station map's sibling flags and the TCD table."""
    release: dict
    report: gr.BuildReport
    congen_run: object
    provenance: dict
    docs: list
    disputed_reasons: dict
    sibling_flags: dict
    table: object


def build(*, fit_run: Path, audit_dir: Path, verdicts_dir: Path, calibration_dir: Path, overlay_run: Path | None,
          ticon_csv: Path, ticon_refs: Path, congen_run: Path, datestamp: str, created: str, build_commit: str,
          tcd_path: Path | None = None, log=print):
    """(release document, build report, congen run, provenance) (see the module docstring); provenance is
    BUILD_REPORT.json's, without the command line (main adds it)."""
    p = build_parts(fit_run=fit_run, audit_dir=audit_dir, verdicts_dir=verdicts_dir, calibration_dir=calibration_dir,
                    overlay_run=overlay_run, ticon_csv=ticon_csv, ticon_refs=ticon_refs, congen_run=congen_run,
                    datestamp=datestamp, created=created, build_commit=build_commit, tcd_path=tcd_path, log=log)
    return p.release, p.report, p.congen_run, p.provenance


def build_parts(*, fit_run: Path, audit_dir: Path, verdicts_dir: Path, calibration_dir: Path,
                overlay_run: Path | None, ticon_csv: Path, ticon_refs: Path, congen_run: Path, datestamp: str,
                created: str, build_commit: str, tcd_path: Path | None = None, extra_names=(), log=print) -> BuildParts:
    """build(), with its inputs kept (BuildParts). `extra_names`: constituent names the astro table must also
    have (the agency sets' of a combined build, otc_pipeline.build_otc); the GESLA sets do not change."""
    rel = GeslaRelease()
    tcd_path = tcd_path or dump_path()
    dump = gs.load_tcd_dump(tcd_path)
    table = dump.table
    if table.tcd_sha256 != TCD_SHA256:
        raise SystemExit(f"{tcd_path}: a dump of TCD {table.tcd_sha256}; the release is built on the OTC TCD "
                         f"{TABLE_ID} ({TCD_SHA256}): fit and build with its dump (the congen run's tcd-1900-2100.json)")
    audit_dumps = {"audit": audit_dump(audit_dir, dump), "verdicts": audit_dump(verdicts_dir, dump)}
    final = gs.load_audit(verdicts_dir)
    withhold = check_reviewed_withhold(verdicts_dir, final)
    results = (fit_run / "results.jsonl").read_bytes()      # one read: hashed (the station map pin), then parsed
    rows = [json.loads(x) for x in results.decode("utf-8").splitlines() if x.strip()]
    fitted = sorted(r["record"] for r in rows if r["outcome"] == "fitted")
    cal = read_calibration(calibration_dir)
    try:
        check_bound(cal, [fit_run] + ([overlay_run] if overlay_run is not None else []),
                    tcd_table_sha256=dump.sha256)
    except ValueError as e:
        raise SystemExit(f"{calibration_dir}: {e}") from None
    docs, refused = gs.docs_from_fit_run(fit_run, dump, rel, fitted, audit_dir=audit_dir, calibration=cal,
                                         overlay_dir=overlay_run, eot20=gs.load_eot20(audit_dir))
    log(f"fit documents: {len(docs)} of {len(fitted)} fitted records ({len(rows)} rows), fit-stage refusals {len(refused)}")
    licences, licence_ids = gesla_licences(docs)
    smap = sm.load()
    audit = gs.load_audit(audit_dir)
    carry = ts.TiconCarry.from_audit(ts.load_ticon(ticon_csv), final, rel.metadata(), references_path=ticon_refs)
    names = {n for d in docs for n in d["fit"]["constituents"]} | ts.carried_names(carry.records, table) | set(extra_names)
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
    disputed = gr.disputed_reasons_of(final.rows)
    release, report = gr.build_release(
        docs, table=table, table_id=TABLE_ID, datestamp=datestamp, created=created, build_commit=build_commit,
        station_ids=smap.record_ids(), licences=licences, licence_ids=licence_ids, withheld=withheld_licences(),
        astro_tables=astro, congen_run=crun, refused=refused, epoch_hours=sm.load_epoch_hours(),
        sibling_flags=smap.sibling_flags(), tombstones=smap.tombstones(), station_map_inputs=smap.inputs,
        station_aliases=smap.release_aliases(),
        fit_run_results_sha256=results_sha256,
        disputed_reasons=disputed, ticon=carry)
    # Scientific selection (including TICON neighbour choices) uses the same
    # fitted documents. Account for existing non-fit outcomes only afterwards.
    account_nonfit(rows, report)
    fit_identity = _run_identity(fit_run)
    if fit_identity["results_sha256"] != results_sha256:
        raise SystemExit(f"{fit_run}/results.jsonl changed during the build")
    provenance = {
        "build_commit": build_commit,
        "fit_run": fit_identity,
        "overlay_run": _run_identity(overlay_run) if overlay_run is not None else None,
        "audit": {**_audit_identity(audit_dir, audit), "tcd_dump": audit_dumps["audit"]},
        "verdicts": {**_audit_identity(verdicts_dir, final), "tcd_dump": audit_dumps["verdicts"],
                     "reviewed_withhold": withhold},
        "calibration": {"path": _path(calibration_dir), "sha256": cal["_sha256"]},
        "congen_run": {"path": _path(crun.run_dir), "provenance_sha256": _sha256(crun.run_dir / "PROVENANCE.json")},
        "tcd": {"path": _path(dump.path), "sha256": dump.sha256},
        "otc_tcd": {"id": TABLE_ID, "path": _path(crun.run_dir / otc_file["path"]), "sha256": otc_file["sha256"]},
        "ticon_csv": {"path": _path(ticon_csv), "sha256": _sha256(ticon_csv)},
        "ticon_refs": {"path": _path(ticon_refs), "sha256": _sha256(ticon_refs)},
    }
    return BuildParts(release, report, crun, provenance, docs, disputed, smap.sibling_flags(), table)


def account_nonfit(rows: list[dict], report: gr.BuildReport) -> None:
    """Retain actual fit-run non-fit outcomes in reporting without adding constants."""
    seen = set()
    accounted = set(report.published) | {r["record"] for r in report.refused}
    pending = []
    for row in rows:
        rid, outcome = row["record"], row["outcome"]
        if rid in seen:
            raise ValueError(f"duplicate fit-run record: {rid}")
        seen.add(rid)
        if outcome not in {"fitted", "short", "unusable"}:
            raise ValueError(f"unknown fit-run outcome for {rid}: {outcome}")
        if outcome != "fitted" and rid not in accounted:
            pending.append(gs.refusal(rid, "too_short" if outcome == "short" else "fit_unusable",
                                       row.get("reason", outcome), row.get("data_hours")))
    report.refused.extend(pending)


def write_report(report: gr.BuildReport, provenance: dict, path: Path, extra: dict | None = None, *,
                 release: dict | None = None, inventory: SourceInventory | None = None) -> Path:
    """BUILD_REPORT.json: the build report (BuildReport.as_dict) with `provenance` (and the `extra` sections of a
    combined build), through a temp file and an atomic rename."""
    body = {**report.as_dict(), "provenance": provenance, **(extra or {})}
    if inventory is not None:
        if release is None:
            raise ValueError("source inventory reporting requires the actual release")
        body["source_inventory"] = report_inventory(inventory, release, report)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with open(tmp, "xb") as fh:
            fh.write((json.dumps(body, sort_keys=True, indent=1)
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
    ap.add_argument("--tcd", type=Path, help="the TCD dump (default gesla.tcd_table.dump_path(): $TCD_TABLE or "
                    "$OTC_TCD_TABLE, else $OTC_WORK/tcd_table.json)")
    ap.add_argument("--datestamp", required=True)
    ap.add_argument("--created", required=True)
    inventory_arguments(ap)
    argv = sys.argv[1:] if argv is None else list(argv)
    a = ap.parse_args(argv)
    inventory = inventory_from_arguments(a)
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
    write_report(report, provenance, a.out / "BUILD_REPORT.json", release=release, inventory=inventory)
    _log(t0, f"wrote {[p.name for p in files]} + BUILD_REPORT.json to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
