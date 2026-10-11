"""The build refuses what it cannot trust (default suite, synthetic numbers, no GESLA data):

- no default licence: every record needs its licence id (build_release, source_set, load_gesla_sets);
- a fit whose time base attenuates the tide is taken only with its own sampling_corrected stamp;
- a fit run is taken only when its manifest is complete and clean and names this build's TCD dump,
  GESLA manifest and audit csv;
- the schema pin cannot be bypassed;
- a record that cannot be published yet is reported by reason, and the rest is published;
- the advisories: TIME ZONE HOURS, cut reasons, atomic write_fits, interrupted release writes.
"""
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from gesla.select_constituents import CANDIDATES
from gesla.timebase_verdict import Correction

from otc_pipeline import gesla_sets, release_writer
from otc_pipeline.gesla_release import build_release
from otc_pipeline.gesla_sets import (CUT, REASON_MAX, FitIntegrityError, NotPublishable, clip, declared_hours,
                                     docs_from_fit_run, load_gesla_sets, load_tcd_dump, source_set, time_base_annotation,
                                     write_fits)
from otc_pipeline.release_writer import SchemaPinError, check_release_dir, validate_release, write_release

from synthetic_congen import congen_run, kp0b_tables
from audit_stub import stub_audit
from test_format_1_0 import (D, LICENCE, META, SPEEDS, TABLE_ID, build, doc, fit_dict, stamped, tb_row)

NC = {"licence_id": "gesla-cmems-nc", "spdx": "CC-BY-NC-4.0", "provider": "TEST ONLY", "attribution": "TEST ONLY",
      "commercial_use": False, "restriction": "TEST ONLY", "terms_url": "https://example.org/terms"}
MEAN60 = dict(time_base_code="corrected:utc_instant->utc_mean_start60:sibling", published_time_base="utc_mean_start60",
              comparator_kind="a", comparator="sibling-1-deu-test~corrected", lag_median="-29.5", official_lag="",
              time_base_reason="declared utc_instant corrected to utc_mean_start60 by sibling")


@pytest.fixture
def dump(tmp_path):
    data = {"years": [2020, 2022], "tcd_sha256": "c" * 64, "aliases": {},
            "constituents": {n: {"speed": s, "v0u": [10.0 * i for i in range(3)], "f": [1.0] * 3}
                             for n, s in SPEEDS.items()}}
    path = tmp_path / "tcd_table.json"
    path.write_text(json.dumps(data))
    return load_tcd_dump(path)


@pytest.fixture
def table(dump):
    return dump.table


# --- 1. no default licence ---------------------------------------------------------------------------------

def test_a_cmems_record_with_no_licence_mapping_stops_the_build(table):
    d = doc(table, rid="a121tg-a12-nld-cmems", meta={"CONTRIBUTOR (ABBREVIATED)": "CMEMS"})
    for ids in (None, {}, {"other": "gesla-derived"}):
        with pytest.raises((ValueError, TypeError), match="no licence id|licence_ids"):
            build(table, [d], licence_ids=ids)
    release = build(table, [d], licences=[LICENCE, NC], licence_ids={d["record_id"]: "gesla-cmems-nc"})
    assert release["licences"] == [NC] and release["stations"][0]["constant_sets"][0]["licence_id"] == "gesla-cmems-nc"


def test_source_set_and_load_gesla_sets_need_the_licence(table, tmp_path):
    d = doc(table)
    assert inspect.signature(source_set).parameters["licence_id"].default is inspect.Parameter.empty
    with pytest.raises(ValueError, match="no licence id"):
        source_set(d, licence_id="")
    write_fits([d], tmp_path / "fits")
    with pytest.raises(ValueError, match="no licence id"):
        load_gesla_sets(in_dir=tmp_path / "fits", licence_ids={})
    (s,) = load_gesla_sets(in_dir=tmp_path / "fits", licence_ids={d["record_id"]: "gesla-cmems-nc"})
    assert s.constant_set["licence_id"] == "gesla-cmems-nc"
    assert "licence" not in json.loads((tmp_path / "fits" / "MANIFEST.json").read_text())
    assert not hasattr(gesla_sets, "LICENCE_ID")


# --- 2 and 3. fit runs: the stamp, the manifest, the inputs ------------------------------------------------

class FakeRelease:
    version = "4.1"

    def __init__(self, root: Path, metas: dict):
        self.root = root
        (root / "MANIFEST.json").write_text('{"version": "4.1"}')
        self._metas = metas
        self.members = {rid: {"sha256": hashlib.sha256(rid.encode()).hexdigest()} for rid in metas}

    def metadata(self):
        return self._metas


def make_run(tmp_path, dump, fits: dict, audit_rows: list, *, status="complete", dirty=False, tcd_sha=None,
             outcomes=None, fits_map=True, eot=None):
    """A fit run dir in the run_fits_tb.py layout around these fits, its manifest naming this build's inputs."""
    run = tmp_path / "run"
    (run / "fits").mkdir(parents=True)
    audit = stub_audit(tmp_path / "audit", audit_rows, eot=eot)
    rel = FakeRelease(tmp_path, {rid: {**META, "FILE NAME": rid} for rid in fits})
    lines = []
    for rid, fit in fits.items():
        (run / "fits" / f"{rid}.json").write_text(json.dumps(fit))
        row = next((r for r in audit_rows if r["file_id"] == rid), None)
        label = row["published_time_base"] if row and row["time_base_status"] in ("verified", "corrected") else "utc_instant"
        lines.append(json.dumps({"record": rid, "outcome": (outcomes or {}).get(rid, "fitted"), "time_base": label,
                                 "months_dropped": 0, "first": fit["first_s"], "last": fit["last_s"],
                                 "data_hours": 157000.0, "reason": "x"}))
    (run / "results.jsonl").write_text("\n".join(lines) + "\n")
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
    extra = {"fits_sha256": {rid: sha(run / "fits" / f"{rid}.json") for rid in fits}} if fits_map else {}
    (run / "timebase_manifest.json").write_text(json.dumps({
        "status": status, "dirty": dirty, "commit": "c6f2fac", **extra,
        "inputs": {"tcd_table": {"sha256": tcd_sha or dump.sha256}, "gesla_manifest": {"sha256": sha(rel.root / "MANIFEST.json")},
                   "timebase_csv": {"sha256": sha(audit / "timebase.csv")}},
        "outputs": {"results.jsonl": sha(run / "results.jsonl")}}))
    return run, audit, rel


def mean60_factors(table, fit):
    corr = Correction("utc_mean_start60")
    span = (fit["first_s"], fit["last_s"] + 3600)
    return {n: round(corr.amplitude_factor(table.speed(n), span), 9) for n in sorted(fit["constituents"])}


W = "sibling-2-deu-test"


def test_an_unstamped_mean60_fit_in_a_run_is_refused(tmp_path, dump):
    run, audit, rel = make_run(tmp_path, dump, {W: fit_dict()}, [tb_row("corrected", file_id=W, **MEAN60)])
    with pytest.raises(FitIntegrityError, match="no sampling_corrected stamp"):
        docs_from_fit_run(run, dump, rel, [W], audit_dir=audit)


def test_a_stamped_mean60_fit_is_taken_and_a_wrong_stamp_is_refused(tmp_path, dump):
    fit = fit_dict()
    good = stamped(fit, "utc_mean_start60", factors=mean60_factors(dump.table, fit))
    run, audit, rel = make_run(tmp_path / "a", dump, {W: good}, [tb_row("corrected", file_id=W, **MEAN60)])
    (d,), refused = docs_from_fit_run(run, dump, rel, [W], audit_dir=audit)
    assert refused == [] and d["fit"]["stats"]["sampling_corrected"]["label"] == "utc_mean_start60"
    assert d["tcd_sha256"] == dump.table.tcd_sha256 and d["member_sha256"] == rel.members[W]["sha256"]
    for bad, needle in ((stamped(fit, "utc_mean_start60", factors={"M2": 1.0}), "stamp factor M2"),
                        (stamped(fit, "utc_mean_start60", kind="step", factors=mean60_factors(dump.table, fit)), "kind"),
                        (stamped(fit, "utc_instant"), "stamp is for utc_instant")):
        run, audit, rel = make_run(tmp_path / needle.replace(" ", "_"), dump, {W: bad},
                                   [tb_row("corrected", file_id=W, **MEAN60)])
        with pytest.raises(FitIntegrityError, match=needle):
            docs_from_fit_run(run, dump, rel, [W], audit_dir=audit)


@pytest.mark.parametrize("kw, needle", [
    (dict(status="partial"), "status 'partial'"),
    (dict(dirty=True), "dirty True"),
    (dict(tcd_sha="f" * 64), "inputs.tcd_table"),
])
def test_a_run_that_is_not_complete_clean_and_on_this_builds_inputs_is_refused(tmp_path, dump, kw, needle):
    run, audit, rel = make_run(tmp_path, dump, {"x-1-nor-test": fit_dict()}, [], **kw)
    with pytest.raises(FitIntegrityError, match=needle):
        docs_from_fit_run(run, dump, rel, ["x-1-nor-test"], audit_dir=audit)


def test_a_run_on_another_gesla_manifest_or_audit_or_with_changed_results_is_refused(tmp_path, dump):
    rid = "x-1-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {rid: fit_dict()}, [])
    (rel.root / "MANIFEST.json").write_text('{"version": "4.1", "changed": true}')
    with pytest.raises(FitIntegrityError, match="inputs.gesla_manifest"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)
    run, audit, rel = make_run(tmp_path / "b", dump, {rid: fit_dict()}, [])
    other = stub_audit(tmp_path / "b" / "other", [tb_row(file_id="y")])
    with pytest.raises(FitIntegrityError, match="inputs.timebase_csv"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=other)
    (run / "results.jsonl").write_text((run / "results.jsonl").read_text() + "\n")
    with pytest.raises(FitIntegrityError, match="results.jsonl is not the one"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)


def test_a_mismatched_tcd_stops_the_build(table):
    d = {**doc(table), "tcd_sha256": "f" * 64}
    with pytest.raises(FitIntegrityError, match="fitted with TCD"):
        build(table, [d])


# --- 4. the schema pin -------------------------------------------------------------------------------------

def test_a_permissive_schema_cannot_validate_an_invalid_document(tmp_path, monkeypatch):
    bad = json.loads((Path(__file__).parent / "fixtures" / "example-1.0.json").read_text())
    bad["format_version"] = "0.5"
    assert list(inspect.signature(validate_release).parameters) == ["release"]
    assert validate_release(bad) != []
    permissive = tmp_path / "permissive.json"
    permissive.write_text("{}")
    monkeypatch.setattr(release_writer, "_schema_file", lambda: permissive)
    with pytest.raises(SchemaPinError):
        validate_release(bad)
    from otc_pipeline import adapter
    assert "schema_path" not in inspect.signature(adapter.run_contract).parameters
    assert "schema_path" not in inspect.signature(adapter.check_contract).parameters


def test_the_pin_check_and_the_parse_read_the_schema_once(monkeypatch):
    path = release_writer._schema_file()
    reads = []
    real_bytes, real_text = Path.read_bytes, Path.read_text
    monkeypatch.setattr(Path, "read_bytes", lambda p: reads.append(p) or real_bytes(p))
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **k: reads.append(p) or real_text(p, *a, **k))
    validate_release({})
    assert reads.count(path) == 1


# --- 5. the build report -----------------------------------------------------------------------------------

def test_one_short_record_among_good_ones_is_reported_and_the_rest_published(table, tmp_path):
    good = [doc(table, rid=f"good-{i}-nor-test", row=tb_row()) for i in range(2)]
    short = doc(table, rid="short-1-nor-test", fit=fit_dict(rayleigh_hours=100 * 24.0))
    kw = dict(table=table, table_id=TABLE_ID, datestamp=D, created="2026-10-10T00:00:00Z", build_commit="abcdef0",
              station_ids={d["record_id"]: f"OTC-T{i:04d}" for i, d in enumerate(good + [short])}, licences=[LICENCE],
              licence_ids={d["record_id"]: "gesla-derived" for d in good + [short]}, withheld={},
              epoch_hours={d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 0} for d in good + [short]},
              contributor_roles={"TEST": "originator"},
              sibling_flags={}, tombstones=[], station_map_inputs={"fit_run_results_sha256": "f" * 64},
              fit_run_results_sha256="f" * 64, disputed_reasons={}, sampling_s={d["record_id"]: 3600 for d in good + [short]},
              astro_tables=kp0b_tables(congen_run(table), SPEEDS, TABLE_ID), congen_run=congen_run(table))
    fit_stage = [{"record": "tiny-1-nor-test", "reason": "too_short", "message": "100 good hours", "good_hours": 100.0}]
    release, report = build_release(good + [short], refused=fit_stage, **kw)
    assert [s["constant_sets"][0]["source_record_id"] for s in release["stations"]] == ["good-0-nor-test", "good-1-nor-test"]
    assert report.published == ["good-0-nor-test", "good-1-nor-test"]
    assert report.counts() == {"short_record_uncalibrated": 1, "too_short": 1}
    assert {r["record"] for r in report.refused} == {"short-1-nor-test", "tiny-1-nor-test"}
    assert validate_release(release) == []
    out = json.loads(report.write(tmp_path / "BUILD_REPORT.json").read_text())
    assert out["published"] == 2 and out["refused"] == 2 and out["refused_by_reason"] == {"short_record_uncalibrated": 1, "too_short": 1}
    with pytest.raises(ValueError, match="no record is publishable"):
        build_release([short], refused=[], **kw)


def test_a_withheld_licence_is_refused_and_reported_never_published(table, tmp_path):
    # L-NC round 4: records whose licence is withheld (no data or products to third parties without consent)
    # are refused with the reason, and a station with only withheld sets is reported, not published
    good = doc(table, rid="good-0-nor-test", row=tb_row())
    held = [doc(table, rid=f"held-{i}-gbr-bodc", row=tb_row()) for i in range(2)]
    nc = {**LICENCE, "licence_id": "gesla-bodc-nc", "spdx": "CC-BY-NC-4.0", "commercial_use": False,
          "restriction": "Non-commercial use only: test.", "terms_url": "https://example.org/terms"}
    ids = {"good-0-nor-test": "OTC-T0000", "held-0-gbr-bodc": "OTC-T0001", "held-1-gbr-bodc": "OTC-T0001"}
    kw = dict(table=table, table_id=TABLE_ID, datestamp=D, created="2026-10-10T00:00:00Z", build_commit="abcdef0",
              station_ids=ids, licences=[LICENCE, nc],
              licence_ids={"good-0-nor-test": "gesla-derived", "held-0-gbr-bodc": "gesla-bodc-nc",
                           "held-1-gbr-bodc": "gesla-bodc-nc"},
              epoch_hours={d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 0} for d in [good] + held},
              contributor_roles={"TEST": "originator"},
              sibling_flags={}, tombstones=[], station_map_inputs={"fit_run_results_sha256": "f" * 64},
              fit_run_results_sha256="f" * 64, disputed_reasons={}, sampling_s={d["record_id"]: 3600 for d in [good] + held},
              astro_tables=kp0b_tables(congen_run(table), SPEEDS, TABLE_ID), congen_run=congen_run(table), refused=[])
    reason = "BODC: no data or products to third parties without prior consent"
    release, report = build_release([good] + held, withheld={"gesla-bodc-nc": reason}, **kw)
    assert report.published == ["good-0-nor-test"]
    assert [s["station_id"] for s in release["stations"]] == ["OTC-T0000"]
    assert [x["licence_id"] for x in release["licences"]] == ["gesla-derived"]
    assert sorted((r["record"], r["reason"]) for r in report.refused) == \
        [("held-0-gbr-bodc", "withheld"), ("held-1-gbr-bodc", "withheld")]
    assert all(reason in r["message"] for r in report.refused)
    assert report.withheld_stations == {"OTC-T0001": reason}
    out = json.loads(report.write(tmp_path / "BUILD_REPORT.json").read_text())
    assert out["refused_by_reason"] == {"withheld": 2} and out["withheld_stations"] == {"OTC-T0001": reason}
    assert validate_release(release) == []
    # a station with a published and a withheld record is published, never reported withheld; a station with
    # records under two withheld licences reports both reasons (confirm-A #6)
    mixed = doc(table, rid="good-1-gbr-test", row=tb_row())
    jodc = doc(table, rid="held-2-jpn-jodc", row=tb_row())
    nc2 = {**nc, "licence_id": "gesla-jodc-nc"}
    kw2 = {**kw, "licences": [LICENCE, nc, nc2],
           "station_ids": {**ids, "good-1-gbr-test": "OTC-T0001", "held-2-jpn-jodc": "OTC-T0002", "held-1-gbr-bodc": "OTC-T0002"},
           "licence_ids": {**kw["licence_ids"], "good-1-gbr-test": "gesla-derived", "held-2-jpn-jodc": "gesla-jodc-nc"},
           "epoch_hours": {d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 0} for d in [good, mixed, jodc] + held},
           "sampling_s": {d["record_id"]: 3600 for d in [good, mixed, jodc] + held}}
    r2 = "JODC: not to provide the data to a third party without permission"
    release2, report2 = build_release([good, mixed, jodc] + held, withheld={"gesla-bodc-nc": reason, "gesla-jodc-nc": r2}, **kw2)
    assert sorted(s["station_id"] for s in release2["stations"]) == ["OTC-T0000", "OTC-T0001"]
    assert report2.withheld_stations == {"OTC-T0002": f"{reason}; {r2}"}
    with pytest.raises(TypeError):        # withheld is required: no build forgets it
        build_release([good], **kw)
    with pytest.raises(ValueError, match="withheld licence 'gesla-nope' is not in licences"):
        build_release([good], withheld={"gesla-nope": reason}, **kw)


def test_a_disputed_record_is_flagged(table):
    row = tb_row("disputed", comparator_kind="b", time_base_code="disputed:comparators_disagree",
                 time_base_reason="declared utc_instant kept; evidence disagrees: lag +32.6 min fits no class")
    d = doc(table, row=row)
    cs = gesla_sets.source_set(d, licence_id="gesla-derived").constant_set
    assert cs["time_base"]["code"] == "disputed" and cs["qc_status"] == "flagged"
    assert [f["flag"] for f in cs["qc_flags"]] == ["time_base"]
    # owner rule: a disputed time base is not carried, so the build refuses the set
    with pytest.raises(ValueError, match=r"no record is publishable .*'time_base_disputed': 1"):
        build(table, [d])


# --- advisories --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("meta", [{}, {"TIME ZONE HOURS": ""}, {"TIME ZONE HOURS": " "}, {"TIME ZONE HOURS": "x"}])
def test_a_missing_time_zone_is_never_utc(meta):
    with pytest.raises(NotPublishable, match="TIME ZONE HOURS") as e:
        declared_hours({"FILE NAME": "x", **meta})
    assert e.value.reason == "time_zone_missing"
    assert declared_hours({"TIME ZONE HOURS": "-3"}) == -3


def test_a_cut_reason_is_marked():
    long = "x" * (REASON_MAX + 50)
    assert len(clip(long)) == REASON_MAX and clip(long).endswith(CUT) and clip("short") == "short"
    tb = time_base_annotation(tb_row(time_base_reason=long), 0, "x", decided_in="20261010")
    assert tb["reason"].endswith(CUT)


def test_evidence_the_annotation_cannot_read_is_refused_not_dropped():
    with pytest.raises(NotPublishable, match="unknown comparator_kind"):
        time_base_annotation(tb_row(comparator_kind="z"), 0, "x", decided_in="20261010")
    row = tb_row("disputed", comparator_kind="w", comparator="", weak_eot20="0 (+1.0)", weak_served="off (+6.5) dependent")
    with pytest.raises(NotPublishable, match="names no served station"):
        time_base_annotation(row, 0, "x", decided_in="20261010")
    row = tb_row("disputed", comparator_kind="w", comparator="", weak_eot20="n/a (+3.0)", weak_dst="no_step (+0.6)")
    tb = time_base_annotation(row, 0, "x", decided_in="20261010")
    assert [e["comparator_kind"] for e in tb["evidence"]] == ["dst_selftest"]


def test_write_fits_is_atomic_and_never_mixes_doc_sets(table, tmp_path, monkeypatch):
    docs = [doc(table, rid=f"r-{i}-nor-test") for i in range(3)]
    write_fits(docs, tmp_path / "a")
    with pytest.raises(FileExistsError, match="already holds a doc set"):
        write_fits(docs, tmp_path / "a")
    real = gesla_sets.os.replace
    calls = []

    def fail_third(src, dst):
        calls.append(dst)
        if len(calls) == 3:
            raise OSError("disk full")
        return real(src, dst)
    monkeypatch.setattr(gesla_sets.os, "replace", fail_third)
    with pytest.raises(OSError, match="disk full"):
        write_fits(docs, tmp_path / "b")
    assert list((tmp_path / "b").iterdir()) == []


def test_an_interrupted_release_write_is_detected(table, tmp_path):
    release = build(table, [doc(table)])
    write_release(release, tmp_path, datestamp=D, app_export=None, congen_run=congen_run(table))
    assert check_release_dir(tmp_path) == []
    (tmp_path / f".OTC_{D}.2.csv.0123456789abcdef.tmp").write_bytes(b"partial")
    (tmp_path / f"OTC_{D}.2.json").write_bytes(b"{}")
    (tmp_path / f"OTC_{D}.csv").write_bytes(b"changed")
    problems = check_release_dir(tmp_path)
    assert any("temp file" in p for p in problems)
    assert any(f"OTC_{D}.2: no OTC_{D}.2.sha256" in p for p in problems)
    assert any(f"OTC_{D}.csv: SHA-256 differs" in p for p in problems)


# --- confirmation round: the fit files are bound to the run manifest (C13) and the smaller items -----------

def test_a_fit_changed_after_the_manifest_is_refused(tmp_path, dump):
    rid = "x-1-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {rid: fit_dict()}, [])
    docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)
    changed = {**fit_dict(), "z0_m": 9.0}
    (run / "fits" / f"{rid}.json").write_text(json.dumps(changed))
    with pytest.raises(FitIntegrityError, match="not the file the run recorded"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)


def test_a_run_without_the_fit_map_is_refused(tmp_path, dump):
    run, audit, rel = make_run(tmp_path, dump, {"x-1-nor-test": fit_dict()}, [], fits_map=False)
    with pytest.raises(FitIntegrityError, match="no fits_sha256"):
        docs_from_fit_run(run, dump, rel, ["x-1-nor-test"], audit_dir=audit)


def test_a_mixed_run_dir_is_refused(tmp_path, dump):
    rid = "x-1-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {rid: fit_dict()}, [])
    (run / "fits" / "y-2-nor-test.json").write_text(json.dumps(fit_dict()))     # a fit of another run
    with pytest.raises(FitIntegrityError, match="not in the run's fits_sha256"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)
    run, audit, rel = make_run(tmp_path / "b", dump, {rid: fit_dict(), "y-2-nor-test": fit_dict()}, [])
    (run / "fits" / "y-2-nor-test.json").unlink()
    with pytest.raises(FitIntegrityError, match="missing"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)


def test_the_fit_map_must_cover_exactly_the_fitted_rows(tmp_path, dump):
    rid = "x-1-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {rid: fit_dict(), "y-2-nor-test": fit_dict()}, [],
                               outcomes={"y-2-nor-test": "short"})
    with pytest.raises(FitIntegrityError, match="fitted rows"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)


@pytest.mark.parametrize("dirty", [True, None, "false", 0])
def test_any_dirty_value_but_false_is_refused(tmp_path, dump, dirty):
    run, audit, rel = make_run(tmp_path, dump, {"x-1-nor-test": fit_dict()}, [], dirty=dirty)
    with pytest.raises(FitIntegrityError, match="dirty"):
        docs_from_fit_run(run, dump, rel, ["x-1-nor-test"], audit_dir=audit)


def test_the_tcd_hash_is_checked_against_the_dump_bytes(tmp_path, dump):
    run, audit, rel = make_run(tmp_path, dump, {"x-1-nor-test": fit_dict()}, [])
    (d,), _ = docs_from_fit_run(run, dump, rel, ["x-1-nor-test"], audit_dir=audit)
    # same table, other bytes (a key order change): the dump is not the one the run recorded
    other = tmp_path / "tcd_other.json"
    other.write_text(json.dumps(json.loads(dump.path.read_text()), indent=2))
    with pytest.raises(FitIntegrityError, match="inputs.tcd_table"):
        docs_from_fit_run(run, dump.__class__(dump.table, hashlib.sha256(other.read_bytes()).hexdigest(), other),
                          rel, ["x-1-nor-test"], audit_dir=audit)


def test_refused_is_required(table):
    assert inspect.signature(build_release).parameters["refused"].default is inspect.Parameter.empty


def test_write_fits_and_read_fits_refuse_stray_files(table, tmp_path):
    d = doc(table)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "stray-record.json").write_text("{}")
    with pytest.raises(FileExistsError, match="not empty"):
        write_fits([d], tmp_path / "a")
    write_fits([d], tmp_path / "b")
    (tmp_path / "b" / ".x.json.0123456789abcdef.tmp").write_text("{}")
    with pytest.raises(ValueError, match="not in MANIFEST.json"):
        gesla_sets.read_fits(tmp_path / "b")


@pytest.mark.parametrize("mark, needle", [
    ({"kind": "utc_instant", "label": "utc_mean_start60", "factors": {}}, "stamp kind"),
    ({"kind": "utc_mean_start60", "label": "utc_mean_start60", "factors": {}}, "stamp factor M2"),
])
def test_source_set_checks_the_stamp_kind_and_factors(table, mark, needle):
    row = tb_row("corrected", file_id=W, **MEAN60)
    d = doc(table, row=row, fit={**fit_dict(), "stats": {"sampling_corrected": mark}})
    with pytest.raises(FitIntegrityError, match=needle):
        source_set(d, licence_id="gesla-derived")
    good = doc(table, row=row, fit=stamped(fit_dict(), "utc_mean_start60", factors=mean60_factors(table, fit_dict())))
    assert source_set(good, licence_id="gesla-derived").constant_set["qc_status"] == "ok"


def test_check_release_dir_on_a_missing_or_empty_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        check_release_dir(tmp_path / "nope")
    assert check_release_dir(tmp_path) == ["no release stem in the directory"]


def test_build_report_write_is_atomic(tmp_path, monkeypatch):
    from otc_pipeline import gesla_release
    report = gesla_release.BuildReport(published=["a"], refused=[])
    synced = []
    real = gesla_release.os.fsync
    monkeypatch.setattr(gesla_release.os, "fsync", lambda fd: synced.append(fd) or real(fd))
    (tmp_path / ".BUILD_REPORT.json.tmp").write_text("stale")       # a fixed temp name must not matter
    report.write(tmp_path / "BUILD_REPORT.json")
    assert synced and json.loads((tmp_path / "BUILD_REPORT.json").read_text())["published"] == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == [".BUILD_REPORT.json.tmp", "BUILD_REPORT.json"]


def test_validate_release_uses_the_pinned_schema_in_behaviour():
    ex = json.loads((Path(__file__).parent / "fixtures" / "example-1.0.json").read_text())
    assert validate_release(ex) == []
    for mutate, needle in ((lambda d: d.__setitem__("format_version", "0.5"), "'1.0' was expected"),
                           (lambda d: d["release"].pop("fit_min_good_hours"), "fit_min_good_hours"),
                           (lambda d: d["licences"][0].pop("commercial_use"), "commercial_use")):
        bad = json.loads(json.dumps(ex))
        mutate(bad)
        assert any(needle in e for e in validate_release(bad)), needle


def _rewrite_rows(run, edit):
    """Edit results.jsonl rows and re-record its hash in the manifest (the fits map is left as it was)."""
    rows = [json.loads(x) for x in (run / "results.jsonl").read_text().splitlines() if x.strip()]
    rows = edit(rows)
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    m = json.loads((run / "timebase_manifest.json").read_text())
    m["outputs"]["results.jsonl"] = hashlib.sha256((run / "results.jsonl").read_bytes()).hexdigest()
    (run / "timebase_manifest.json").write_text(json.dumps(m))


def test_a_row_whose_span_is_not_the_fits_is_refused(tmp_path, dump):
    rid = "x-1-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {rid: fit_dict()}, [])
    _rewrite_rows(run, lambda rows: [{**r, "first": r["first"] + 3600} for r in rows])
    with pytest.raises(FitIntegrityError, match="span"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)


def test_duplicate_rows_in_results_are_refused(tmp_path, dump):
    rid = "x-1-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {rid: fit_dict()}, [])
    _rewrite_rows(run, lambda rows: rows + rows)
    with pytest.raises(FitIntegrityError, match="more than one row"):
        docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)


def test_results_are_hashed_and_parsed_from_one_read(tmp_path, dump, monkeypatch):
    rid = "x-1-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {rid: fit_dict()}, [])
    reads = []
    real_bytes, real_text = Path.read_bytes, Path.read_text
    monkeypatch.setattr(Path, "read_bytes", lambda p: reads.append(p.name) or real_bytes(p))
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **k: reads.append(p.name) or real_text(p, *a, **k))
    docs_from_fit_run(run, dump, rel, [rid], audit_dir=audit)
    assert reads.count("results.jsonl") == 1


def test_an_overlay_refit_replaces_its_records_and_must_be_a_partial_run_on_the_same_inputs(tmp_path, dump):
    """SR-ACC R7: the records the Rayleigh hierarchy changes are published from a partial refit (gesla.fit_run ONLY)."""
    import shutil
    a, b = "x-1-nor-test", "y-2-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {a: fit_dict(), b: fit_dict()}, [])
    over, _, _ = make_run(tmp_path / "o", dump, {b: fit_dict(amp_m2=1.5)}, [], status="partial")
    shutil.copy(audit / "timebase.csv", tmp_path / "o" / "audit" / "timebase.csv")
    over2, _, _ = make_run(tmp_path / "p", dump, {b: fit_dict(amp_m2=1.5)}, [tb_row(file_id="z-9-nor-test")],
                           status="partial")
    m = json.loads((over / "timebase_manifest.json").read_text())
    m["inputs"]["timebase_csv"]["sha256"] = hashlib.sha256((audit / "timebase.csv").read_bytes()).hexdigest()
    (over / "timebase_manifest.json").write_text(json.dumps(m))
    docs, _ = docs_from_fit_run(run, dump, rel, [a, b], audit_dir=audit, overlay_dir=over)
    amps = {d["record_id"]: d["fit"]["constituents"]["M2"]["amp_m"] for d in docs}
    assert amps == {a: 1.0, b: 1.5}
    with pytest.raises(FitIntegrityError, match="overlay is a partial run"):
        docs_from_fit_run(run, dump, rel, [a, b], audit_dir=audit, overlay_dir=run)       # a complete run is not an overlay
    with pytest.raises(FitIntegrityError, match="inputs.timebase_csv"):
        docs_from_fit_run(run, dump, rel, [a, b], audit_dir=audit, overlay_dir=over2)     # another audit


def test_an_implausible_fit_is_refused_with_no_calibration(tmp_path, dump):
    """SR-ACC R8 in the run path itself: an M2 over 10 m never publishes, calibration or not."""
    good, bad = "x-1-nor-test", "big-1-nor-test"
    run, audit, rel = make_run(tmp_path, dump, {good: fit_dict(), bad: fit_dict(amp_m2=17.67)}, [])
    docs, _ = docs_from_fit_run(run, dump, rel, [good, bad], audit_dir=audit)
    by = {d["record_id"]: d for d in docs}
    assert by[good]["refusal"] is None
    assert by[bad]["refusal"]["reason"] == "implausible_fit"
    with pytest.raises(NotPublishable) as e:
        gesla_sets.source_set(by[bad], licence_id="gesla-derived")
    assert e.value.reason == "implausible_fit"


def _eot_of(fit, shift=0.0, dist_km=3.5):
    return {"dist_km": dist_km, **{c: [fit["constituents"][c]["amp_m"], (fit["constituents"][c]["phase_deg"] - shift) % 360]
                                   for c in ("M2", "S2", "K1", "O1")}}


def test_a_sign_inverted_fit_is_refused_long_or_short(tmp_path, dump):
    """antonio_enes-191a-moz-uhslc_rq: every main constituent about +195 deg from EOT20 at its cell, the EOT20 amplitude
    gate of the audit (M2 ratio 1.069) having skipped it. The screen runs on every record of the run."""
    from test_format_1_0 import SHORT_HOURS, short_calibration
    good, inv, inv_short = "x-1-nor-test", "inv-1-nor-test", "invs-1-nor-test"
    fits = {good: fit_dict(), inv: fit_dict(), inv_short: fit_dict(rayleigh_hours=SHORT_HOURS)}
    eot = {good: _eot_of(fits[good], shift=8.0), inv: _eot_of(fits[inv], shift=195.0),
           inv_short: _eot_of(fits[inv_short], shift=165.0)}
    run, audit, rel = make_run(tmp_path, dump, fits, [], eot=eot)
    cal = short_calibration(fits[inv_short], rid=inv_short, good_hours=157000.0)    # the short one is calibrated
    docs, _ = docs_from_fit_run(run, dump, rel, sorted(fits), audit_dir=audit, calibration=cal,
                                eot20=gesla_sets.load_eot20(audit))
    by = {d["record_id"]: d for d in docs}
    assert by[good]["refusal"] is None
    for rid in (inv, inv_short):
        assert by[rid]["refusal"]["reason"] == "implausible_fit" and "sign-inverted" in by[rid]["refusal"]["detail"]
        with pytest.raises(NotPublishable) as e:
            gesla_sets.source_set(by[rid], licence_id="gesla-derived")
        assert e.value.reason == "implausible_fit"
    docs, _ = docs_from_fit_run(run, dump, rel, sorted(fits), audit_dir=audit, calibration=cal)   # no EOT20: no screen
    assert [d["refusal"] for d in docs] == [None, None, None]


def test_the_eot20_inputs_are_the_audits_own(tmp_path):
    audit = stub_audit(tmp_path / "a", [], eot={"r-1": {"M2": [1.0, 2.0], "dist_km": 1.0}, "r-2": None})
    assert gesla_sets.load_eot20(audit) == {"r-1": {"M2": [1.0, 2.0], "dist_km": 1.0}}
    (audit / "weak_inputs.json").write_text('{"records": {}}')                      # changed since the audit read it
    with pytest.raises(FitIntegrityError, match="the audit read"):
        gesla_sets.load_eot20(audit)
    bare = stub_audit(tmp_path / "b", [], inputs={})
    with pytest.raises(FitIntegrityError, match="names no weak inputs"):
        gesla_sets.load_eot20(bare)


def test_make_docs_is_the_entry_point_with_calibration_and_overlay(tmp_path, dump, monkeypatch):
    """The fit stage's entry point: a short record publishes with the calibration, is refused without it."""
    from test_format_1_0 import SHORT_HOURS, short_calibration
    from gesla import short_accuracy as sa
    rid = "short-1-nor-test"
    f = fit_dict(rayleigh_hours=SHORT_HOURS)
    run, audit, rel = make_run(tmp_path, dump, {rid: f}, [])
    rows = (run / "results.jsonl").read_text().replace('"data_hours": 157000.0', f'"data_hours": {SHORT_HOURS}')
    (run / "results.jsonl").write_text(rows)
    m = json.loads((run / "timebase_manifest.json").read_text())
    m["outputs"]["results.jsonl"] = hashlib.sha256((run / "results.jsonl").read_bytes()).hexdigest()
    (run / "timebase_manifest.json").write_text(json.dumps(m))
    cal = tmp_path / "cal"
    sa.write_calibration({**short_calibration(f, rid=rid),         # made from this run on this dump (check_bound)
                          "inputs": {"fit_runs": [sa.run_identity(run)], "tcd_table_sha256": dump.sha256}}, cal)
    monkeypatch.setattr(gesla_sets, "GeslaRelease", lambda: rel)
    monkeypatch.setattr(gesla_sets, "load_tcd_dump", lambda p: dump)
    gesla_sets.main(["make-docs", "--fit-run", str(run), "--audit-dir", str(audit), "--calibration", str(cal),
                     "--out", str(tmp_path / "docs")])
    (d,) = gesla_sets.read_fits(tmp_path / "docs")
    cs = gesla_sets.source_set(d, licence_id="gesla-derived").constant_set
    assert cs["expected_accuracy"]["calibration_records"] == 15
    gesla_sets.main(["make-docs", "--fit-run", str(run), "--audit-dir", str(audit), "--out", str(tmp_path / "docs2")])
    (d2,) = gesla_sets.read_fits(tmp_path / "docs2")
    with pytest.raises(NotPublishable) as e:
        gesla_sets.source_set(d2, licence_id="gesla-derived")
    assert e.value.reason == "short_record_uncalibrated"


def test_make_docs_refuses_a_calibration_of_other_fits_or_another_dump(tmp_path, dump, monkeypatch):
    # FIX-SRCAL r1 N1: the fit stage stops, as the build does, rather than refuse every short record it covers
    from test_format_1_0 import SHORT_HOURS, short_calibration
    from gesla import short_accuracy as sa
    rid = "short-1-nor-test"
    f = fit_dict(rayleigh_hours=SHORT_HOURS)
    run, audit, rel = make_run(tmp_path, dump, {rid: f}, [])
    other = tmp_path / "other-run"
    other.mkdir()
    (other / "results.jsonl").write_text("{}\n")
    monkeypatch.setattr(gesla_sets, "GeslaRelease", lambda: rel)
    monkeypatch.setattr(gesla_sets, "load_tcd_dump", lambda p: dump)
    for n, inputs, why in ((1, {"fit_runs": [sa.run_identity(other)], "tcd_table_sha256": dump.sha256}, "the build uses"),
                           (2, {"fit_runs": [sa.run_identity(run)], "tcd_table_sha256": "e" * 64}, "made on the TCD dump"),
                           (3, {}, "the build uses")):
        cal = tmp_path / f"cal{n}"
        sa.write_calibration({**short_calibration(f, rid=rid), "inputs": inputs}, cal)
        with pytest.raises(ValueError, match=why):
            gesla_sets.main(["make-docs", "--fit-run", str(run), "--audit-dir", str(audit), "--calibration", str(cal),
                             "--out", str(tmp_path / f"docs{n}")])
        assert not (tmp_path / f"docs{n}").exists()
