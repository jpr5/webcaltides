"""C2a: the one committed entry point of the OTC release build (GESLA + TICON-4 + the official agency sources).

    python -m otc_pipeline.build_otc <gesla_build inputs> --agency-cache ROOT --currents-canary-cache DIR \\
        --datestamp YYYYMMDD --created ISO8601 --out OUT          (bin/build-otc runs this)

1. The agency sets (combine.load_agency): kartverket, linz, jma, noaa, noaa-currents from ROOT/<source>, with the
   network blocked, positions corrected (data/otc/position_corrections.json).
2. The GESLA build (gesla_build.build_parts: every GESLA and TICON-4 gate as gesla_build runs them), with the
   agency constituent names in the astro table.
3. The canaries (D8): kartverket, linz, jma and noaa against this build's GESLA fits (canary.canary; comparators:
   fits with a clean time base that the station map does not flag); noaa-currents against NOAA's published current
   predictions cached in DIR (currents_canary; fill DIR first with `currents_canary fetch`).
4. The merge (combine.merge): station ids from the committed map (combine mint assigns them), official sets
   recommended, subordinates mapped; D11: every included source must pass its canary.
5. validate_release (0 errors), no set with a disputed time base, then write_release and BUILD_REPORT.json, whose
   `agency` section lists per source what was published, joined and refused, the canaries and the position
   corrections, and whose provenance adds each agency cache's MANIFEST.json hash.

The tree must be committed (gesla_build.head_commit); OUT must be empty.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from otc_pipeline import combine
from otc_pipeline import currents_canary as cc
from otc_pipeline import gesla_build as gb
from otc_pipeline import gesla_sets as gs
from otc_pipeline import station_map as sm
from otc_pipeline.astro_tables import TABLE_ID
from otc_pipeline.canary import CANARY_CONSTITUENTS, canary
from otc_pipeline.release_writer import validate_release, write_release

GESLA_CANARY_SOURCES = ("kartverket", "linz", "jma", "noaa")
EXCEPTIONS_PATH = sm.DATA / "canary_exceptions.json"


def _has_canary_constituents(ss) -> bool:
    return set(CANARY_CONSTITUENTS) <= {c["name"] for c in (ss.constant_set or {}).get("constituents") or []}


def gesla_comparators(parts: gb.BuildParts) -> list:
    """COMPARATOR ONLY (gesla_sets.comparator_set): this build's GESLA fits with a time base the final audit does
    not dispute, no station-map flag, and all four canary constituents (M2, S2, K1, O1: a fit without one, a short
    record's Rayleigh drop or a microtidal gauge, cannot be compared); a fit that cannot be a comparator (refused,
    short and uncalibrated) is skipped."""
    out = []
    for d in parts.docs:
        rid = d["record_id"]
        if d["time_base"]["code"] == "disputed" or rid in parts.disputed_reasons or parts.sibling_flags.get(rid):
            continue
        try:
            ss = gs.comparator_set(d)
        except (gs.NotPublishable, gs.FitIntegrityError):
            continue
        if _has_canary_constituents(ss):
            out.append(ss)
    return out


def load_canary_exceptions(path: Path = EXCEPTIONS_PATH) -> list[dict]:
    d = json.loads(Path(path).read_text())
    if d.get("format") != 1:
        raise combine.CombineError(f"{path}: format {d.get('format')!r}, expected 1")
    for e in d["exceptions"]:
        if not str(e.get("reason", "")).strip():
            raise combine.CombineError(f"{path}: exception {e} has no reason")
    return list(d["exceptions"])


def run_canaries(agency: dict, comparators: list, currents_cache: Path, *, start: str = cc.START,
                 exceptions: list[dict] | None = None, log=print) -> tuple[dict, dict]:
    """(source -> convention.canary object, source -> full result) for every included source. A source set that
    does not publish all four canary constituents cannot be compared: it fails the source's canary unless it is
    on the reviewed exceptions list (data/otc/canary_exceptions.json, with the same missing constituents); an
    exception that no longer matches a set stops the build. canary.canary checks every pair it makes."""
    exceptions = load_canary_exceptions() if exceptions is None else exceptions
    conv, full = {}, {}
    used = set()
    for src, a in agency.items():
        if src in GESLA_CANARY_SOURCES:
            lacking = [ss for ss in a.sets if ss.constant_set is not None and not _has_canary_constituents(ss)]
            r = canary(src, [ss for ss in a.sets if ss not in lacking], comparators)
            conv[src], full[src] = r.convention_canary(), r.to_dict()
            rows = [{"source_record_id": ss.constant_set["source_record_id"],
                     "missing": sorted(set(CANARY_CONSTITUENTS) - {c["name"] for c in ss.constant_set["constituents"]})}
                    for ss in lacking]
            full[src]["not_compared_missing_constituents"] = rows
            for row in rows:
                ok = next((i for i, e in enumerate(exceptions) if e["source"] == src
                           and e["source_record_id"] == row["source_record_id"]
                           and sorted(e["missing"]) == row["missing"]), None)
                if ok is None:
                    conv[src] = {**conv[src], "status": "fail"}
                    full[src]["passed"] = False
                    full[src]["reasons"] = [*full[src]["reasons"],
                                            f"{row['source_record_id']}: missing {', '.join(row['missing'])}, "
                                            "not compared and not on the canary exceptions list"]
                else:
                    used.add(ok)
        elif src == "noaa-currents":
            r = cc.run_canary(a.sets, currents_cache, start=start)
            conv[src], full[src] = cc.convention_canary(r), r
        else:
            raise combine.CombineError(f"{src}: no canary is defined")
        log(f"canary {src}: {conv[src]}")
    stale = [e for i, e in enumerate(exceptions) if i not in used and e["source"] in agency]
    if stale:
        raise combine.CombineError(f"canary exceptions that match no uncompared set (review them): {stale}")
    return conv, full


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.build_otc", description=(__doc__ or "").split("\n")[0],
                                 allow_abbrev=False)
    for name in ("fit-run", "audit-dir", "verdicts-dir", "calibration", "ticon-csv", "ticon-refs", "congen-run",
                 "agency-cache", "currents-canary-cache", "out"):
        ap.add_argument(f"--{name}", type=Path, required=True)
    ap.add_argument("--overlay-run", type=Path)
    ap.add_argument("--tcd", type=Path, help="the TCD dump (default $OTC_WORK/tcd_table.json)")
    ap.add_argument("--currents-canary-start", default=cc.START)
    ap.add_argument("--datestamp", required=True)
    ap.add_argument("--created", required=True)
    argv = sys.argv[1:] if argv is None else list(argv)
    a = ap.parse_args(argv)
    t0 = time.time()

    def log(msg: str) -> None:
        gb._log(t0, msg)
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out}: not empty")
    commit = gb.head_commit()
    log(f"build_commit {commit}")
    agency = combine.load_agency(a.agency_cache, jma_year=int(a.datestamp[:4]))
    log("agency sets: " + ", ".join(f"{s} {len(x.sets)}" for s, x in agency.items()))
    parts = gb.build_parts(fit_run=a.fit_run, audit_dir=a.audit_dir, verdicts_dir=a.verdicts_dir,
                           calibration_dir=a.calibration, overlay_run=a.overlay_run, ticon_csv=a.ticon_csv,
                           ticon_refs=a.ticon_refs, congen_run=a.congen_run, datestamp=a.datestamp,
                           created=a.created, build_commit=commit, tcd_path=a.tcd,
                           extra_names=combine.agency_names(agency), log=log)
    log(f"gesla build_release: {parts.report.summary()}")
    comparators = gesla_comparators(parts)
    log(f"canary comparators (GESLA fits): {len(comparators)}")
    conv, full = run_canaries(agency, comparators, a.currents_canary_cache, start=a.currents_canary_start, log=log)
    release, arep = combine.merge(parts.release, agency, station_ids=sm.load().record_ids(), build_commit=commit,
                                  table_id=TABLE_ID, canaries=conv)
    arep.canaries = full
    log("agency: " + json.dumps(arep.sources, sort_keys=True))
    errs = validate_release(release)
    log(f"validate_release: {len(errs)} errors")
    for e in errs[:20]:
        print("  ", e)
    disputed = [cs["set_id"] for st in release["stations"] if st["status"] == "active" for cs in st["constant_sets"]
                if (cs.get("time_base") or {}).get("code") == "disputed"]
    log(f"sets with a disputed time base: {len(disputed)}")
    if errs or disputed:
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    files = write_release(release, a.out, datestamp=a.datestamp, app_export=None, congen_run=parts.congen_run)
    provenance = {"command_line": [sys.executable, "-m", "otc_pipeline.build_otc", *argv], "cwd": os.getcwd(),
                  **parts.provenance,
                  "agency_cache": {s: {"path": str((a.agency_cache / s).resolve()),
                                       "manifest_sha256": _sha256(a.agency_cache / s / "MANIFEST.json")}
                                   for s in agency},
                  "position_corrections_sha256": _sha256(combine.CORRECTIONS_PATH),
                  "currents_canary_cache": str(a.currents_canary_cache.resolve())}
    gb.write_report(parts.report, provenance, a.out / "BUILD_REPORT.json", extra={"agency": arep.as_dict()})
    log(f"wrote {[p.name for p in files]} + BUILD_REPORT.json to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
