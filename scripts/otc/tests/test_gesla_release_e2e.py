"""End to end on real records: fit documents, a release document, the release files, and the files checked
against the pinned 1.0 schema, with the build report.

- OTC_FIT_RUN (G04-int fit-1) only chooses the records. It is refused as a source: its manifest has no
  fits_sha256 (its fit files are not bound to it) and its hourly-mean fits have no sampling stamp. So
  every record is refitted here by the in-repo path (fit_records), which stamps each fit. Once the gated
  fit of the full-4 audit exists with fits_sha256, docs_from_fit_run can read it instead.
- One short record is included: the build leaves it out and reports it.

Skips unless these are set (local caches; no GESLA data is in git):
  OTC_FIT_RUN       a complete fit run
  OTC_AUDIT_DIR     an audit output dir that passes check_audit_outputs; or
  OTC_TIMEBASE_CSV  the rows of an audit made before the crash check (G04-int full-3), which the gate
                    refuses: the test puts them in a stub audit dir (audit_stub)
and the verified GESLA cache and the TCD dump are in $OTC_WORK, and a congen_tables.sh run is in
$OTC_CONGEN_RUN (default $OTC_WORK/astro/congen-1.7-r2).

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
from gesla.timebase_verdict import boxcar_response

from otc_pipeline.gesla_licences import gesla_licences, withheld_licences
from otc_pipeline.astro_tables import astro_table, load_congen_run
from otc_pipeline.gesla_release import build_release
from otc_pipeline.gesla_sets import (FitIntegrityError, docs_from_fit_run, fit_records, load_audit, load_tcd_dump,
                                     needs_stamp, published_correction)
from otc_pipeline.release_writer import SCHEMA_SHA256, check_release_dir, validate_release, write_release

from audit_stub import stub_audit

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parents[1]
SCHEMA_BYTES = (ROOT / "schema" / "otc-1.0.schema.json").read_bytes()
SCHEMA = json.loads(SCHEMA_BYTES)
TABLE_ID = "tcd-20251228"   # data/latest-xtide.tcd -> harmonics-dwf-20251228-free.tcd
# one record per audit outcome (and per evidence kind), plus records outside the pool
WANT = ["verified:official", "verified:sibling", "verified:harcon", "verified:provider",
        "unverified:weak_insufficient", "unverified:eot20_only_served_dependent",
        "disputed:comparators_disagree/a", "disputed:comparators_disagree/b", "disputed:weak_checks_disagree",
        "disputed:served_stations_disagree", "not_in_pool", "not_in_pool"]
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
    from gesla.read_gesla import GeslaRelease, load_contributor_roles
    try:
        rel = GeslaRelease()
    except (OSError, ValueError) as e:
        pytest.skip(f"no verified GESLA cache: {e}")
    dump = load_tcd_dump(otc_work() / "tcd_table.json")
    roles = load_contributor_roles(REPO / "data/gesla/contributor_roles.csv")
    congen_run = Path(os.environ.get("OTC_CONGEN_RUN", otc_work() / "astro" / "congen-1.7-r2"))
    if not (congen_run / "PROVENANCE.json").is_file():
        pytest.skip(f"needs a congen_tables.sh run in {congen_run}")
    return run, audit_dir, rel, dump, roles, load_congen_run(congen_run)


def choose(run: Path, tb: dict) -> tuple[list[str], list[str], str]:
    """(instant records for WANT, hourly-mean records, one short record), each the first by name."""
    fitted = [r for r in (json.loads(x) for x in (run / "results.jsonl").read_text().splitlines())
              if r.get("outcome") == "fitted"]

    def kind(rid):
        row = tb.get(rid)
        if row is None:
            return "not_in_pool"
        code = row["time_base_code"]
        return f"{code}/{row['comparator_kind']}" if code == "disputed:comparators_disagree" else code

    def full(rid):
        f = json.loads((run / "fits" / f"{rid}.json").read_text())
        return all(n in f["rayleigh_kept"] or n in f["candidates_missing_from_tcd"] for n in CANDIDATES)

    instant, want, mean60, short = [], list(WANT), [], None
    for r in sorted(fitted, key=lambda r: r["record"]):
        rid = r["record"]
        if needs_stamp(r["time_base"]):
            if len(mean60) < N_MEAN60:
                mean60.append(rid)
            continue
        if short is None and tb.get(rid) is None and not full(rid):
            short = rid
            continue
        k = kind(rid)
        if k in want and full(rid):
            instant.append(rid)
            want.remove(k)
    assert not want and short and len(mean60) == N_MEAN60, (want, short, mean60)
    return instant, mean60, short


def test_a_release_of_real_records_validates_against_1_0(inputs, tmp_path):
    run, audit_dir, rel, dump, roles, crun = inputs
    congen, tcd = crun
    tb = load_audit(audit_dir).rows
    instant, mean60, short = choose(run, tb)
    # the pre-stamp run is not a source: its fit files are not bound to its manifest
    with pytest.raises(FitIntegrityError, match="no fits_sha256"):
        docs_from_fit_run(run, dump, rel, instant[:1], audit_dir=audit_dir)
    docs, refused = fit_records(dump.table, rel, instant + mean60 + [short], audit_dir=audit_dir, roles=roles)
    assert refused == []
    names = {n for d in docs for n in d["fit"]["constituents"]}
    records = [d["record_id"] for d in docs]
    licences, licence_ids = gesla_licences(docs)
    release, report = build_release(
        docs, table=dump.table, table_id=TABLE_ID, datestamp="20261010", created="2026-10-10T00:00:00Z",
        build_commit="c6f2fac", station_ids={r: f"OTC-WR10-{i:04d}" for i, r in enumerate(records)},
        licences=licences, licence_ids=licence_ids, withheld=withheld_licences(), refused=refused,
        epoch_hours={d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 0} for d in docs},
        sibling_flags={}, tombstones=[], station_map_inputs={"fit_run_results_sha256": "f" * 64},
        fit_run_results_sha256="f" * 64, disputed_reasons={},
        astro_tables=[astro_table(congen, tcd, names, table_id=TABLE_ID)], congen_run=crun)
    assert report.counts() == {"short_record_uncalibrated": 1} and report.refused[0]["record"] == short
    assert sorted(report.published) == sorted(instant + mean60)
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
    assert len(lines) == len(instant) + N_MEAN60 and all(errors(json.loads(x), "station") == [] for x in lines)
    sets = {cs["source_record_id"]: cs for st in on_disk["stations"] for cs in st["constant_sets"]}
    assert {cs["time_base"]["code"] for cs in sets.values()} == {"verified", "unverified", "disputed", "corrected"}
    assert on_disk["release"]["source_versions"] == {"gesla": rel.version}
    for rid, cs in sets.items():
        m2 = next(c for c in cs["constituents"] if c["name"] == "M2")["amplitude_m"]
        run_m2 = json.loads((run / "fits" / f"{rid}.json").read_text())["constituents"]["M2"]["amp_m"]
        if rid in mean60:
            # the run's (pre-stamp) M2 carries the boxcar attenuation; the published one has it divided out
            assert m2 / run_m2 == pytest.approx(1 / boxcar_response(dump.table.speed("M2")), rel=1e-4), rid
            assert published_correction(tb[rid], 0).kind == "utc_mean_start60"
            assert cs["time_base"]["correction"] == {"from": "utc_instant", "to": "utc_mean_start60"}
        else:
            assert m2 == pytest.approx(run_m2, abs=1e-5), rid      # the in-repo refit reproduces the run
        assert cs["qc_status"] == ("flagged" if cs["time_base"]["code"] in ("unverified", "disputed") else "ok")


def test_the_gate_refuses_the_real_pre_crash_check_audit():
    # G04-int full-3 was made before the crash check: its manifest has no weak_crashes, so nothing is fitted from it
    audit = _env("OTC_TIMEBASE_CSV").parent
    with pytest.raises(ValueError, match="no weak_crashes"):
        load_audit(audit)


def test_the_partial_corrected_run_is_refused(inputs):
    # G04-int fit-2/out refitted only the 11 hourly-mean records, with the out-of-tree driver: partial, unstamped
    run, audit_dir, rel, dump, _, _ = inputs
    partial = _env("OTC_FIT_PARTIAL_RUN")
    with pytest.raises(FitIntegrityError, match="status 'partial'"):
        docs_from_fit_run(partial, dump, rel, ["bhvalterleuchtturm-4990010-deu-wsv"], audit_dir=audit_dir)
