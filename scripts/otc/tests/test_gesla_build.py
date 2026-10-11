"""otc_pipeline.gesla_build: the release is built from committed code only, into an empty directory. The build
itself runs on real inputs (the release-integration proof); these tests cover the guards around it, the wiring of
build() (which input reaches which step) and the provenance it writes to BUILD_REPORT.json."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from otc_pipeline import astro_tables as at
from otc_pipeline import gesla_build as gb
from otc_pipeline import gesla_release as gr
from otc_pipeline import gesla_sets as gs
from otc_pipeline import station_map as sm
from otc_pipeline import ticon_sets as ts
from gesla import short_accuracy as sa

ARGS = ["--fit-run", "r", "--audit-dir", "a", "--verdicts-dir", "v", "--calibration", "c", "--ticon-csv", "t.csv",
        "--ticon-refs", "refs.json", "--congen-run", "g", "--datestamp", "20261011", "--created", "2026-10-11T00:00:00Z"]


def _repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "f.txt").write_text("one\n")
    git("add", "f.txt")
    git("-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x")
    return tmp_path


def test_build_commit_is_head_of_a_clean_tree(tmp_path):
    repo = _repo(tmp_path)
    (repo / "untracked.txt").write_text("not part of the build\n")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert gb.head_commit(repo) == head


def test_a_tree_with_uncommitted_changes_is_refused(tmp_path):
    repo = _repo(tmp_path)
    (repo / "f.txt").write_text("two\n")
    with pytest.raises(SystemExit, match="committed code only"):
        gb.head_commit(repo)


def test_the_output_directory_must_be_empty(tmp_path):
    (tmp_path / "OTC_20261011.json").write_text("{}")
    with pytest.raises(SystemExit, match="not empty"):
        gb.main([*ARGS, "--out", str(tmp_path)])


def test_abbreviated_flags_are_refused():
    with pytest.raises(SystemExit):
        gb.main(["--fit", "r", *ARGS[2:], "--out", "o"])


# --- build() wiring and the provenance it records (review B2/B3) ---------------------------------------------

def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _inputs(tmp_path):
    """Input dirs with the files build() hashes; their contents are only bytes to hash."""
    def put(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p
    put("run/results.jsonl", '{"record": "a", "outcome": "fitted"}\n{"record": "b", "outcome": "short"}\n')
    put("run/timebase_manifest.json", '{"run": 1}')
    put("overlay/results.jsonl", '{"record": "a", "outcome": "fitted"}\n')
    put("overlay/timebase_manifest.json", '{"overlay": 1}')
    same = {"tcd_table": {"path": str(tmp_path / "tcd.json"), "sha256": "d" * 64}}   # the dump load_tcd_dump fakes
    put("audit/timebase_manifest.json", json.dumps({"audit": 1, "inputs": same}))
    from gesla.timebase_audit import WITHHOLD_PATH
    rw = {"reviewed_withhold": {"path": str(WITHHOLD_PATH), "sha256": _sha(WITHHOLD_PATH)}}   # the committed list
    put("final/timebase_manifest.json", json.dumps({"final": 1, "inputs": {**same, **rw}}))
    put("congen/PROVENANCE.json", '{"congen": 1}')
    put("ticon.csv", "ticon\n")
    put("refs.json", "{}")
    put("tcd.json", "{}")
    (tmp_path / "cal").mkdir()
    return {k: tmp_path / v for k, v in dict(fit_run="run", overlay_run="overlay", audit_dir="audit",
                                              verdicts_dir="final", congen_run="congen", ticon_csv="ticon.csv",
                                              ticon_refs="refs.json", calibration_dir="cal", tcd_path="tcd.json").items()}


class _Run(SimpleNamespace):
    """Stands for astro_tables.CongenRun: unpacks as (congen, tcd)."""
    congen, tcd, run_dir = "congen", "tcd", None
    provenance = {"files": {"otc_tcd": {"path": "otc.tcd", "sha256": at.TCD_SHA256}}}

    def __iter__(self):
        return iter((self.congen, self.tcd))


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """Every heavy step of build() replaced by a recorder: the test sees what build() hands to each."""
    seen = {}
    audits = {"audit": SimpleNamespace(rows={"a": {"time_base_status": "verified"}}, csv_sha256="1" * 64,
                                       decided_build_commit="aud0001"),
              "final": SimpleNamespace(rows={"a": {"time_base_status": "disputed", "disputed_reason": "eot20_whole_hour"}},
                                       csv_sha256="2" * 64, decided_build_commit="fin0001")}
    table = SimpleNamespace(tcd_sha256=at.TCD_SHA256)
    crun = _Run()
    paths = _inputs(tmp_path)
    cal = {"cal_version": 6, "_sha256": "c" * 64,          # made from the run, then the overlay, on the dump (check_bound)
           "config": {"method": sa.method()},
           "inputs": {"fit_runs": [sa.run_identity(paths["fit_run"]), sa.run_identity(paths["overlay_run"])],
                      "tcd_table_sha256": "d" * 64}}
    doc = {"record_id": "a", "fit": {"constituents": {"M2": {}}}}

    def docs_from_fit_run(run, dump, rel, ids, **kw):
        seen["docs_from_fit_run"] = dict(run=run, ids=list(ids), **kw)
        return [doc], [{"record": "b", "reason": "too_short"}]

    def load_congen_run(run_dir, *, tcd_sha256):
        seen["congen"] = (run_dir, tcd_sha256)
        crun.run_dir = Path(run_dir)
        return crun

    def from_audit(records, final, meta, *, references_path):
        seen["ticon_audit"], seen["ticon_refs"] = final, references_path
        return SimpleNamespace(records=[])

    def build_release(docs, **kw):
        seen["build_release"] = dict(docs=docs, **kw)
        return {"release": 1}, gr.BuildReport()
    smap = SimpleNamespace(record_ids=lambda: {"a": "OTC-1"}, sibling_flags=lambda: {}, tombstones=lambda: [],
                           inputs={"fit_run_results_sha256": "x"}, release_aliases=lambda: {})
    monkeypatch.setattr(gb, "GeslaRelease", lambda: SimpleNamespace(metadata=lambda: {}))
    monkeypatch.setattr(gs, "load_tcd_dump", lambda p: SimpleNamespace(table=table, sha256="d" * 64, path=Path(p)))
    monkeypatch.setattr(gs, "load_audit", lambda d: audits[Path(d).name])
    monkeypatch.setattr(gs, "docs_from_fit_run", docs_from_fit_run)
    eot20 = {"a": {"M2": [1.0, 100.0]}}           # FIX-LP: the inversion screen reads the fit audit's EOT20

    def load_eot20(audit_dir):
        seen["eot20_audit_dir"] = audit_dir
        return eot20
    monkeypatch.setattr(gs, "load_eot20", load_eot20)
    seen["eot20"] = eot20
    monkeypatch.setattr(gb, "read_calibration", lambda d: seen.setdefault("calibration_dir", d) and cal)
    monkeypatch.setattr(gb, "gesla_licences", lambda docs: ([], {}))
    monkeypatch.setattr(sm, "load", lambda: smap)
    monkeypatch.setattr(sm, "load_epoch_hours", lambda: {})
    monkeypatch.setattr(ts, "load_ticon", lambda p: seen.setdefault("ticon_csv", p) and [])
    monkeypatch.setattr(ts.TiconCarry, "from_audit", from_audit)
    monkeypatch.setattr(ts, "carried_names", lambda records, table: set())
    monkeypatch.setattr(gb, "load_congen_run", load_congen_run)
    monkeypatch.setattr(gb, "astro_table", lambda congen, tcd, names, table_id: {"names": list(names)})
    monkeypatch.setattr(gr, "build_release", build_release)
    from gesla.timebase_audit import WITHHOLD_PATH, load_withhold
    withhold = {"path": str(WITHHOLD_PATH), "sha256": _sha(WITHHOLD_PATH), "records": len(load_withhold()), "disputed": 0}
    return SimpleNamespace(seen=seen, audits=audits, cal=cal, crun=crun, doc=doc, paths=paths, withhold=withhold)


def _build(fakes, **over):
    kw = dict(fakes.paths, datestamp="20261011", created="2026-10-11T00:00:00Z", build_commit="abc1234", log=lambda m: None)
    return gb.build(**{**kw, **over})


def test_build_hands_each_input_to_its_step(fakes):
    p = fakes.paths
    release, report, crun, prov = _build(fakes)
    seen = fakes.seen
    assert seen["docs_from_fit_run"] == dict(run=p["fit_run"], ids=["a"], audit_dir=p["audit_dir"],
                                             calibration=fakes.cal, overlay_dir=p["overlay_run"], eot20=seen["eot20"])
    assert seen["eot20_audit_dir"] == p["audit_dir"]
    assert seen["calibration_dir"] == p["calibration_dir"] and seen["ticon_csv"] == p["ticon_csv"]
    # the final verdicts, not the fit's audit, decide what is disputed and annotate TICON-4
    assert seen["ticon_audit"] is fakes.audits["final"] and seen["ticon_refs"] == p["ticon_refs"]
    br = seen["build_release"]
    assert br["disputed_reasons"] == {"a": "eot20_whole_hour"}
    assert br["fit_run_results_sha256"] == _sha(p["fit_run"] / "results.jsonl")
    assert br["congen_run"] is fakes.crun is crun and br["build_commit"] == "abc1234"
    assert br["refused"] == [{"record": "b", "reason": "too_short"}] and br["docs"] == [fakes.doc]
    assert seen["congen"] == (p["congen_run"], at.TCD_SHA256)


def test_a_fit_dump_of_another_tcd_is_refused(fakes, monkeypatch):
    # the release is built on the OTC TCD: a fit made with the plain XTide TCD's dump never reaches the release
    other = SimpleNamespace(table=SimpleNamespace(tcd_sha256=at.SOURCE_TCD_SHA256), sha256="d" * 64, path=Path("x"))
    monkeypatch.setattr(gs, "load_tcd_dump", lambda p: other)
    with pytest.raises(SystemExit, match="the release is built on the OTC TCD otc-tcd-20251228-ma2mb2"):
        _build(fakes)
    assert "docs_from_fit_run" not in fakes.seen


def test_a_calibration_of_other_fit_runs_stops_the_build(fakes):
    # FIX-SRCAL: a calibration keyed to other fits would refuse every short record it covers as uncalibrated;
    # the build stops, naming both runs, before any document is made
    p = fakes.paths
    for runs in ([p["fit_run"]], [p["overlay_run"], p["fit_run"]], [p["fit_run"], p["fit_run"]]):
        fakes.cal["inputs"]["fit_runs"] = [sa.run_identity(r) for r in runs]
        with pytest.raises(SystemExit, match="the calibration was made from the fit runs .* the build uses"):
            _build(fakes)
        assert "docs_from_fit_run" not in fakes.seen
    fakes.cal["inputs"]["fit_runs"] = [sa.run_identity(p["fit_run"]), sa.run_identity(p["overlay_run"])]
    (p["fit_run"] / "timebase_manifest.json").write_text('{"run": 2}')        # a refit: other fits, same rows
    with pytest.raises(SystemExit, match="re-run gesla.short_accuracy on the build's fit runs"):
        _build(fakes)
    assert "docs_from_fit_run" not in fakes.seen


def test_a_calibration_made_on_another_tcd_dump_stops_the_build(fakes):
    fakes.cal["inputs"]["tcd_table_sha256"] = "e" * 64          # e.g. the plain XTide dump's
    with pytest.raises(SystemExit, match="made on the TCD dump e+, the build uses d+"):
        _build(fakes)
    assert "docs_from_fit_run" not in fakes.seen


@pytest.mark.parametrize("config,why", [({}, "method is not recorded"),            # v6, v7
                                         ({"method": {"cal_method_version": 2, "cut_list_rule": "donor list"}},
                                          "method is {'cal_method_version': 2")])
def test_a_calibration_of_another_method_stops_the_build(fakes, config, why):
    # FIX-SRCAL r2: same fit run and dump, superseded cut method (v7) never reaches a release
    fakes.cal["config"] = config
    with pytest.raises(SystemExit, match=why):
        _build(fakes)
    assert "docs_from_fit_run" not in fakes.seen


def test_a_congen_run_without_the_otc_tcd_is_refused(fakes, monkeypatch):
    monkeypatch.setattr(fakes.crun, "provenance", {"files": {}})
    with pytest.raises(SystemExit, match="holds no OTC TCD file"):
        _build(fakes)


def _dump(path, rows, years=(1900, 2100), aliases=None, tcd=at.TCD_SHA256):
    n = years[1] - years[0] + 1
    path.write_text(json.dumps({"tcd_sha256": tcd, "years": list(years), "aliases": aliases or {},
                                "constituents": {k: {"speed": v, "v0u": [1.0] * n, "f": [1.0] * n}
                                                 for k, v in rows.items()}}))
    return path


@pytest.mark.parametrize("which", ["audit_dir", "verdicts_dir"])
def test_an_audit_made_on_another_table_stops_the_build(fakes, which):
    # INT-2 N7: the time-base audit, the fits and the agency sets read one table; an audit whose dump has a row
    # the build's dump does not have, or has with other values, never reaches a release
    p = fakes.paths
    _dump(p["tcd_path"], {"M2": 28.9841042, "MA2": 28.9430356})
    for rows in ({"M2": 28.9841042, "GAM2": 28.9112506}, {"M2": 28.98}):
        old = _dump(p["tcd_path"].parent / "old.json", rows)
        (p[which] / "timebase_manifest.json").write_text(json.dumps(
            {"inputs": {"tcd_table": {"path": str(old), "sha256": _sha(old)}}}))
        with pytest.raises(SystemExit, match="another table than the build's"):
            _build(fakes)
        assert "docs_from_fit_run" not in fakes.seen
    (p[which] / "timebase_manifest.json").write_text(json.dumps(
        {"inputs": {"tcd_table": {"path": str(old), "sha256": "f" * 64}}}))     # the file is not the one it read
    with pytest.raises(SystemExit, match="not there as the audit hashed it"):
        _build(fakes)
    (p[which] / "timebase_manifest.json").write_text("{}")
    with pytest.raises(SystemExit, match="names no TCD dump"):
        _build(fakes)


def test_an_audit_on_the_rows_of_the_build_dump_is_recorded(fakes):
    # the dump before MA2 and MB2 were added: every row it has is the build dump's
    p = fakes.paths
    _dump(p["tcd_path"], {"M2": 28.9841042, "MA2": 28.9430356, "MB2": 29.0251729})
    old = _dump(p["tcd_path"].parent / "old.json", {"M2": 28.9841042}, tcd=at.SOURCE_TCD_SHA256)
    (p["audit_dir"] / "timebase_manifest.json").write_text(json.dumps(
        {"inputs": {"tcd_table": {"path": str(old), "sha256": _sha(old)}}}))
    *_, prov = _build(fakes)
    assert prov["audit"]["tcd_dump"] == {"path": str(old.resolve()), "recorded_path": str(old), "sha256": _sha(old),
                                         "relation": "rows of the build's", "rows_not_read": ["MA2", "MB2"]}
    assert prov["verdicts"]["tcd_dump"]["relation"] == "same"
    _dump(old, {"M2": 28.9841042}, years=(1900, 2000), tcd=at.SOURCE_TCD_SHA256)
    (p["audit_dir"] / "timebase_manifest.json").write_text(json.dumps(
        {"inputs": {"tcd_table": {"path": str(old), "sha256": _sha(old)}}}))
    with pytest.raises(SystemExit, match="years"):
        _build(fakes)


def _final_manifest(fakes, sha):
    p = fakes.paths["verdicts_dir"] / "timebase_manifest.json"
    m = json.loads(p.read_text())
    m["inputs"]["reviewed_withhold"] = None if sha is None else {"path": "x", "sha256": sha}
    p.write_text(json.dumps(m))


def test_verdicts_made_on_another_reviewed_withhold_list_stop_the_build(fakes):
    # INT-2 D1: verdicts older than the committed list (FIX-LST r6 before FIX-LST-6) publish its new records unlabelled
    for sha in ("a" * 64, None):
        _final_manifest(fakes, sha)
        with pytest.raises(SystemExit, match="not the committed list"):
            _build(fakes)
        assert "docs_from_fit_run" not in fakes.seen


def test_a_listed_record_the_verdicts_do_not_dispute_stops_the_build(fakes):
    from gesla.timebase_audit import REVIEWER_DISPUTED, load_withhold
    listed = load_withhold()[0]["file_id"]
    fakes.audits["final"].rows[listed] = {"time_base_status": "unverified", "time_base_code": "unverified"}
    with pytest.raises(SystemExit, match=f"listed but not disputed on reviewer evidence: \\['{listed}'\\]"):
        _build(fakes)
    fakes.audits["final"].rows[listed] = {"time_base_status": "disputed", "time_base_code": REVIEWER_DISPUTED}
    fakes.audits["final"].rows["z-1-xxx-yy"] = {"time_base_status": "disputed", "time_base_code": REVIEWER_DISPUTED}
    with pytest.raises(SystemExit, match="disputed on reviewer evidence but not listed: \\['z-1-xxx-yy'\\]"):
        _build(fakes)
    del fakes.audits["final"].rows["z-1-xxx-yy"]
    *_, prov = _build(fakes)
    assert prov["verdicts"]["reviewed_withhold"]["disputed"] == 1


def test_an_audit_dump_is_the_xtide_tcd_minus_exactly_the_otc_additions(fakes):
    # INT-2 A1: a subset dump of any TCD was accepted (M2 only, another TCD); only the XTide dump the OTC TCD is made
    # from, lacking exactly MA2 and MB2, is
    p = fakes.paths
    _dump(p["tcd_path"], {"M2": 28.9841042, "S2": 30.0, "MA2": 28.9430356, "MB2": 29.0251729})
    for rows, tcd, why in (({"M2": 28.9841042}, at.SOURCE_TCD_SHA256, "not the OTC additions"),
                           ({"M2": 28.9841042}, "f" * 64, "not the XTide TCD"),
                           ({"M2": 28.9841042, "S2": 30.0}, "f" * 64, "not the XTide TCD"),
                           ({"M2": 28.9841042, "S2": 30.0, "MA2": 28.9430356}, at.SOURCE_TCD_SHA256,
                            r"rows it lacks are \['MB2'\]")):
        old = _dump(p["tcd_path"].parent / "old.json", rows, tcd=tcd)
        (p["audit_dir"] / "timebase_manifest.json").write_text(json.dumps(
            {"inputs": {"tcd_table": {"path": str(old), "sha256": _sha(old)}}}))
        with pytest.raises(SystemExit, match=why):
            _build(fakes)
        assert "docs_from_fit_run" not in fakes.seen
    old = _dump(p["tcd_path"].parent / "old.json", {"M2": 28.9841042, "S2": 30.0}, tcd=at.SOURCE_TCD_SHA256)
    (p["audit_dir"] / "timebase_manifest.json").write_text(json.dumps(
        {"inputs": {"tcd_table": {"path": str(old), "sha256": _sha(old)}}}))
    *_, prov = _build(fakes)
    assert prov["audit"]["tcd_dump"]["rows_not_read"] == ["MA2", "MB2"]


def test_the_audit_dump_pinned_in_the_audit_dir_is_read_when_the_shared_one_changed(fakes):
    # INT-2 A2: the audits record the shared $OTC_WORK/tcd_table.json; a regenerated file stopped every later build
    p = fakes.paths
    _dump(p["tcd_path"], {"M2": 28.9841042, "MA2": 28.9430356, "MB2": 29.0251729})
    old = _dump(p["tcd_path"].parent / "old.json", {"M2": 28.9841042}, tcd=at.SOURCE_TCD_SHA256)
    sha = _sha(old)
    (p["audit_dir"] / "timebase_manifest.json").write_text(json.dumps(
        {"inputs": {"tcd_table": {"path": str(old), "sha256": sha}}}))
    pin = gb.pinned_audit_dump(p["audit_dir"], sha)
    pin.write_bytes(old.read_bytes())
    old.write_text(json.dumps(json.loads(old.read_text()), sort_keys=True, indent=1))    # regenerated: other bytes
    *_, prov = _build(fakes)
    assert prov["audit"]["tcd_dump"]["path"] == str(pin.resolve()) and prov["audit"]["tcd_dump"]["recorded_path"] == str(old)
    pin.write_text(pin.read_text() + " ")
    with pytest.raises(SystemExit, match="pinned copy of the audit's TCD dump is not the dump it read"):
        _build(fakes)
    pin.unlink()
    with pytest.raises(SystemExit, match="not there as the audit hashed it"):
        _build(fakes)


def test_build_records_the_identity_of_every_input(fakes):
    p = fakes.paths
    *_, prov = _build(fakes)
    assert prov["build_commit"] == "abc1234"
    assert prov["fit_run"] == {"path": str(p["fit_run"].resolve()),
                               "results_sha256": _sha(p["fit_run"] / "results.jsonl"),
                               "manifest_sha256": _sha(p["fit_run"] / "timebase_manifest.json")}
    assert prov["overlay_run"] == {"path": str(p["overlay_run"].resolve()),
                                   "results_sha256": _sha(p["overlay_run"] / "results.jsonl"),
                                   "manifest_sha256": _sha(p["overlay_run"] / "timebase_manifest.json")}
    for key, name, csv_sha, commit in (("audit", "audit_dir", "1" * 64, "aud0001"),
                                       ("verdicts", "verdicts_dir", "2" * 64, "fin0001")):
        assert prov[key] == {"path": str(p[name].resolve()),
                             "manifest_sha256": _sha(p[name] / "timebase_manifest.json"),
                             "timebase_csv_sha256": csv_sha, "commit": commit,
                             "tcd_dump": {"path": str(p["tcd_path"]), "sha256": "d" * 64, "relation": "same"},
                             **({"reviewed_withhold": fakes.withhold} if key == "verdicts" else {})}, key
    assert prov["calibration"] == {"path": str(p["calibration_dir"].resolve()), "sha256": "c" * 64}
    assert prov["congen_run"] == {"path": str(p["congen_run"].resolve()),
                                  "provenance_sha256": _sha(p["congen_run"] / "PROVENANCE.json")}
    assert prov["tcd"] == {"path": str(p["tcd_path"].resolve()), "sha256": "d" * 64}
    assert prov["otc_tcd"] == {"id": at.TABLE_ID, "path": str((p["congen_run"] / "otc.tcd").resolve()),
                               "sha256": at.TCD_SHA256}
    assert prov["ticon_csv"] == {"path": str(p["ticon_csv"].resolve()), "sha256": _sha(p["ticon_csv"])}
    assert prov["ticon_refs"] == {"path": str(p["ticon_refs"].resolve()), "sha256": _sha(p["ticon_refs"])}


def test_no_overlay_is_recorded_as_none(fakes):
    fakes.cal["inputs"]["fit_runs"] = [sa.run_identity(fakes.paths["fit_run"])]
    *_, prov = _build(fakes, overlay_run=None)
    assert fakes.seen["docs_from_fit_run"]["overlay_dir"] is None and prov["overlay_run"] is None


def test_the_build_report_carries_the_provenance_and_the_command_line(fakes, tmp_path, monkeypatch):
    p, out = fakes.paths, tmp_path / "out"
    argv = ["--fit-run", str(p["fit_run"]), "--audit-dir", str(p["audit_dir"]), "--verdicts-dir", str(p["verdicts_dir"]),
            "--calibration", str(p["calibration_dir"]), "--overlay-run", str(p["overlay_run"]),
            "--ticon-csv", str(p["ticon_csv"]), "--ticon-refs", str(p["ticon_refs"]), "--congen-run", str(p["congen_run"]),
            "--tcd", str(p["tcd_path"]), "--datestamp", "20261011", "--created", "2026-10-11T00:00:00Z", "--out", str(out)]
    monkeypatch.setattr(gb, "head_commit", lambda: "abc1234")
    monkeypatch.setattr(gb, "validate_release", lambda release: [])
    monkeypatch.setattr(gb, "write_release", lambda release, out, **kw: [])
    assert gb.main(argv) == 0
    rep = json.loads((out / "BUILD_REPORT.json").read_text())
    prov = rep["provenance"]
    assert prov["command_line"] == [sys.executable, "-m", "otc_pipeline.gesla_build", *argv]
    assert prov["cwd"] == str(Path.cwd())
    assert prov["fit_run"]["results_sha256"] == _sha(p["fit_run"] / "results.jsonl")
    assert prov["calibration"]["sha256"] == "c" * 64 and prov["build_commit"] == "abc1234"
    assert rep["refused_by_reason"] == {} and rep["published"] == 0     # the report itself is still all there


def test_a_congen_run_the_astro_tables_refuse_is_a_one_line_exit(fakes, monkeypatch):
    def refuse(run_dir, *, tcd_sha256):
        raise at.AstroTableMismatch(f"{run_dir}: PROVENANCE.json has no otc_tcd: a congen run made before the OTC TCD")
    monkeypatch.setattr(gb, "load_congen_run", refuse)
    with pytest.raises(SystemExit, match="^--congen-run: .*has no otc_tcd"):
        _build(fakes)
