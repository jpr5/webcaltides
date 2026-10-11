"""End to end on real records: fit documents from the gated fit run, a release document, the release files, and
the files checked against the pinned 1.0 schema, with the build report.

- OTC_FIT_RUN is a complete in-repo fit run with fits_sha256 (TB-SIB fit-5c), fitted against the audit in
  OTC_AUDIT_DIR (full-5c): docs_from_fit_run reads it, as otc_pipeline.gesla_build does.
- OTC_VERDICTS_DIR (default OTC_AUDIT_DIR) holds the final verdicts (full-5d): their disputed reasons
  (disputed_reasons_of) decide what is never published, as in the build.
- The records are chosen per audit outcome. The test states what the build must do with each one: the clean ones
  publish; a set the audit disputes is refused (time_base_disputed, FIX-DISP), a whole-hour EOT20 lag that only the
  final verdicts dispute is refused (eot20_whole_hour_unresolved), a record under a withheld licence is refused
  (withheld, L-NC), and a short record is refused without a calibration (short_record_uncalibrated).
- The published records are refitted by the in-repo path (fit_records), which must reproduce the run's M2.

Skips unless these are set (local caches; no GESLA data is in git):
  OTC_FIT_RUN       a complete fit run
  OTC_AUDIT_DIR     an audit output dir that passes check_audit_outputs; or
  OTC_TIMEBASE_CSV  the rows of an audit made before the crash check (G04-int full-3), which the gate
                    refuses: the test puts them in a stub audit dir (audit_stub)
and the verified GESLA cache and the TCD dump are in $OTC_WORK, and a congen_tables.sh run is in
$OTC_CONGEN_RUN (default $OTC_WORK/astro/otc-tcd-20251228-ma2mb2).

Station ids are test inputs: their slot (the station map) is not done for this test.
The licences are the real per-contributor ones (L-NC, otc_pipeline.gesla_licences). The astro table is KP0b's,
from the congen run (astro_tables.astro_table).
"""
import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from gesla.read_gesla import otc_work
from gesla.select_constituents import CANDIDATES
from gesla.short_accuracy import UNCALIBRATED

from otc_pipeline.gesla_licences import gesla_licences, withheld_licences
from otc_pipeline.astro_tables import astro_table, load_congen_run
from otc_pipeline.gesla_release import EOT20_WHOLE_HOUR, build_release, disputed_reasons_of
from otc_pipeline.gesla_sets import (FitIntegrityError, docs_from_fit_run, fit_records, load_audit, load_tcd_dump,
                                     needs_stamp, published_correction)
from otc_pipeline.release_writer import SCHEMA_SHA256, check_release_dir, validate_release, write_release

from audit_stub import stub_audit

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parents[1]
SCHEMA_BYTES = (ROOT / "schema" / "otc-1.0.schema.json").read_bytes()
SCHEMA = json.loads(SCHEMA_BYTES)
TABLE_ID = "tcd-20251228"   # data/latest-xtide.tcd -> harmonics-dwf-20251228-free.tcd
# one record per audit outcome (and per evidence kind) that the build publishes ...
PUBLISH = ["verified:official", "verified:sibling", "verified:harcon", "verified:provider",
           "unverified:weak_insufficient", "unverified:eot20_only_served_dependent", "corrected"]
# ... and one per outcome it refuses, with the reason it must give
REFUSE = {"disputed:comparators_disagree/a": "time_base_disputed", "disputed:comparators_disagree/b": "time_base_disputed",
          "disputed:weak_checks_disagree": "time_base_disputed", "disputed:served_stations_disagree": "time_base_disputed",
          "withheld": "withheld", "short": UNCALIBRATED}
FINAL_ONLY = {"final:" + EOT20_WHOLE_HOUR: "eot20_whole_hour_unresolved"}   # disputed by the final verdicts only
N_MEAN60 = 3


def _env(name):
    v = os.environ.get(name)
    if not v:
        pytest.skip(f"needs {name}")
    return Path(v)


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    run = _env("OTC_FIT_RUN")
    if os.environ.get("OTC_AUDIT_DIR"):
        audit_dir = Path(os.environ["OTC_AUDIT_DIR"])
    else:
        audit_dir = stub_audit(tmp_path_factory.mktemp("audit"), csv_from=_env("OTC_TIMEBASE_CSV"))
    verdicts_dir = Path(os.environ.get("OTC_VERDICTS_DIR") or audit_dir)
    from gesla.read_gesla import GeslaRelease, load_contributor_roles
    try:
        rel = GeslaRelease()
    except (OSError, ValueError) as e:
        pytest.skip(f"no verified GESLA cache: {e}")
    dump = load_tcd_dump(otc_work() / "tcd_table.json")
    roles = load_contributor_roles(REPO / "data/gesla/contributor_roles.csv")
    congen_run = Path(os.environ.get("OTC_CONGEN_RUN", otc_work() / "astro" / "otc-tcd-20251228-ma2mb2"))
    if not (congen_run / "PROVENANCE.json").is_file():
        pytest.skip(f"needs a congen_tables.sh run in {congen_run}")
    return run, audit_dir, verdicts_dir, rel, dump, roles, load_congen_run(congen_run)


def choose(run: Path, docs: list[dict], tb: dict, final: dict, licence_ids: dict, final_differs: bool
           ) -> tuple[list[str], list[str], dict[str, str]]:
    """(instant records to publish, hourly-mean records to publish, record -> the reason it must be refused):
    for each kind in PUBLISH, REFUSE (and FINAL_ONLY when the final verdicts are not the audit's) the first record
    by name, and the first N_MEAN60 clean hourly-mean records."""
    rows = {r["record"]: r for r in (json.loads(x) for x in (run / "results.jsonl").read_text().splitlines()
                                     if x.strip())}
    withheld = withheld_licences()
    final_reasons = disputed_reasons_of(final)

    def full(rid):
        f = json.loads((run / "fits" / f"{rid}.json").read_text())
        return all(n in f["rayleigh_kept"] or n in f["candidates_missing_from_tcd"] for n in CANDIDATES)

    def kind(d):
        rid = d["record_id"]
        row, held = tb[rid], licence_ids[rid] in withheld
        code = row["time_base_code"]
        if row["time_base_status"] == "disputed":
            return None if held else f"{code}/{row['comparator_kind']}" if code == "disputed:comparators_disagree" else code
        if rid in final_reasons:
            return None if held else "final:" + final_reasons[rid].split()[0]
        if held:
            return "withheld"
        if not full(rid):
            return "short" if (d.get("refusal") or {}).get("reason") == UNCALIBRATED else None
        if needs_stamp(rows[rid]["time_base"]):
            return "mean60"
        return "corrected" if code.startswith("corrected:") else code

    want = {**{k: None for k in PUBLISH}, **REFUSE, **(FINAL_ONLY if final_differs else {})}
    instant, mean60, refuse = [], [], {}
    for d in sorted(docs, key=lambda d: d["record_id"]):
        rid, k = d["record_id"], kind(d)
        if k == "mean60":
            if len(mean60) < N_MEAN60 and not d.get("refusal"):
                mean60.append(rid)
        elif k in want and (k == "short" or not d.get("refusal")) and (k == "short" or full(rid)):
            reason = want.pop(k)
            if reason is None:
                instant.append(rid)
            else:
                refuse[rid] = reason
    assert not want and len(mean60) == N_MEAN60, (sorted(want), mean60)
    return instant, mean60, refuse


def test_a_release_of_real_records_validates_against_1_0(inputs, tmp_path):
    run, audit_dir, verdicts_dir, rel, dump, roles, crun = inputs
    congen, tcd = crun
    tb = load_audit(audit_dir).rows
    final = load_audit(verdicts_dir).rows
    fitted = sorted(json.loads(x)["record"] for x in (run / "results.jsonl").read_text().splitlines()
                    if x.strip() and json.loads(x)["outcome"] == "fitted")
    every, fit_refused = docs_from_fit_run(run, dump, rel, fitted, audit_dir=audit_dir)
    assert fit_refused == []
    _, every_lic = gesla_licences(every)
    instant, mean60, refuse = choose(run, every, tb, final, every_lic,
                                     Path(verdicts_dir).resolve() != Path(audit_dir).resolve())
    published = instant + mean60
    docs = [d for d in every if d["record_id"] in {*published, *refuse}]
    names = {n for d in docs for n in d["fit"]["constituents"]}
    records = [d["record_id"] for d in docs]
    licences, licence_ids = gesla_licences(docs)
    disputed = disputed_reasons_of(final)
    release, report = build_release(
        docs, table=dump.table, table_id=TABLE_ID, datestamp="20261010", created="2026-10-10T00:00:00Z",
        build_commit="c6f2fac", station_ids={r: f"OTC-WR10-{i:04d}" for i, r in enumerate(records)},
        licences=licences, licence_ids=licence_ids, withheld=withheld_licences(), refused=fit_refused,
        epoch_hours={d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 0} for d in docs},
        sibling_flags={}, tombstones=[], station_map_inputs={"fit_run_results_sha256": "f" * 64},
        fit_run_results_sha256="f" * 64, disputed_reasons=disputed,
        astro_tables=[astro_table(congen, tcd, names, table_id=TABLE_ID)], congen_run=crun)
    assert {r["record"]: r["reason"] for r in report.refused} == refuse
    assert sorted(report.published) == sorted(published)
    write_release(release, tmp_path, datestamp="20261010", app_export=None, congen_run=crun)
    assert check_release_dir(tmp_path) == []
    assert hashlib.sha256(SCHEMA_BYTES).hexdigest() == SCHEMA_SHA256

    def errors(doc, ref=None):
        schema = SCHEMA if ref is None else {"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{ref}"}
        v = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
        return [e.message for e in v.iter_errors(doc)]
    on_disk = json.loads((tmp_path / "OTC_20261010.json").read_text())
    assert errors(on_disk) == [] and validate_release(on_disk) == []
    meta = json.loads((tmp_path / "OTC_20261010.meta.json").read_text())
    assert errors(meta, "meta") == []
    lines = (tmp_path / "OTC_20261010.jsonl").read_text().splitlines()
    assert len(lines) == len(published) and all(errors(json.loads(x), "station") == [] for x in lines)
    sets = {cs["source_record_id"]: cs for st in on_disk["stations"] for cs in st["constant_sets"]}
    assert sorted(sets) == sorted(published)
    # FIX-DISP: no set the audit or the final verdicts dispute is in the files
    assert {cs["time_base"]["code"] for cs in sets.values()} == {"verified", "unverified", "corrected"}
    assert not set(sets) & set(disputed)
    assert on_disk["release"]["source_versions"] == {"gesla": rel.version}
    refit, refit_refused = fit_records(dump.table, rel, published, audit_dir=audit_dir, roles=roles)
    assert refit_refused == []
    refit_m2 = {d["record_id"]: d["fit"]["constituents"]["M2"]["amp_m"] for d in refit}
    for rid, cs in sets.items():
        m2 = next(c for c in cs["constituents"] if c["name"] == "M2")["amplitude_m"]
        run_fit = json.loads((run / "fits" / f"{rid}.json").read_text())
        assert m2 == pytest.approx(run_fit["constituents"]["M2"]["amp_m"], abs=1e-5), rid
        assert m2 == pytest.approx(refit_m2[rid], abs=1e-5), rid       # the in-repo refit reproduces the run
        if rid in mean60:
            # the run's fit carries its own sampling stamp: the boxcar attenuation is divided out
            assert (run_fit.get("stats") or {}).get("sampling_corrected"), rid
            assert published_correction(tb[rid], 0).kind == "utc_mean_start60"
            assert cs["time_base"]["correction"] == {"from": "utc_instant", "to": "utc_mean_start60"}
        assert cs["qc_status"] == ("flagged" if cs["time_base"]["code"] == "unverified" else "ok")


def test_the_gate_refuses_the_real_pre_crash_check_audit():
    # G04-int full-3 was made before the crash check: its manifest has no weak_crashes, so nothing is fitted from it
    audit = _env("OTC_TIMEBASE_CSV").parent
    with pytest.raises(ValueError, match="no weak_crashes"):
        load_audit(audit)


def test_the_partial_corrected_run_is_refused(inputs):
    # G04-int fit-2/out refitted only the 11 hourly-mean records, with the out-of-tree driver: partial, unstamped
    run, audit_dir, _, rel, dump, _, _ = inputs
    partial = _env("OTC_FIT_PARTIAL_RUN")
    with pytest.raises(FitIntegrityError, match="status 'partial'"):
        docs_from_fit_run(partial, dump, rel, ["bhvalterleuchtturm-4990010-deu-wsv"], audit_dir=audit_dir)
