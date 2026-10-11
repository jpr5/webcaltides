"""gesla.fit_run: the gated full-fit runner (fitfix confirm N2 and its advisory)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import gesla.fit_run as fr
import gesla.timebase_audit as ta
from gesla.timebase_verdict import Correction

FIELDS = ["file_id", "time_base_status", "time_base_code", "published_time_base", "correction", "months_bad"]


def _audit(tmp_path, rows=(), **over):
    """A stub audit output dir: timebase.csv (rows) and a manifest (complete, no crashes, the csv's sha)."""
    d = tmp_path / "audit"
    d.mkdir(exist_ok=True)
    with open(d / "timebase.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    m = {"status": "complete", "weak_crashes": [], "commit": "a" * 40,
         "outputs": {"timebase.csv": ta.sha256_file(d / "timebase.csv")}}
    m.update(over)
    (d / "timebase_manifest.json").write_text(json.dumps(m))
    return d


class _Release:
    metadata_path = "metadata/GESLA4-1_ALL.csv"
    names = ("a-1-nor-nhs", "b-2-deu-wsv")

    def __init__(self, root=None):
        self.root = Path(_Release.root)
        (self.root / "metadata").mkdir(parents=True, exist_ok=True)
        (self.root / self.metadata_path).write_text("x\n")
        (self.root / "MANIFEST.json").write_text("{}")
        self.members = set(self.names)

    def metadata(self):
        return {n: {"TIME ZONE HOURS": "0", "LATITUDE": "1", "LONGITUDE": "2"} for n in self.names}


@pytest.fixture
def env(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "tcd_table.json").write_text("{}")
    _Release.root = str(tmp_path / "release")
    monkeypatch.setenv("OTC_WORK", str(work))
    monkeypatch.setenv("FIT_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("WORKERS", "1")
    monkeypatch.setattr(fr, "GeslaRelease", _Release)
    monkeypatch.setattr(fr, "_init", lambda: None)
    return tmp_path


def _results(tmp_path):
    p = tmp_path / "out/results.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _fitted(n):
    (fr.FITS / f"{n}.json").write_text(json.dumps({"record": n}))
    return {"record": n, "outcome": "fitted", "reason": "x"}


def test_fit_run_refuses_a_crashed_audit_and_fits_nothing(env, monkeypatch):
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env, weak_crashes=["x"]) / "timebase.csv"))
    assert fr.main() == 4 and _results(env) == []


def test_fit_run_exits_5_and_is_not_complete_when_a_record_ends_as_error(env, monkeypatch):
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    monkeypatch.setattr(fr, "one", lambda n: {"record": n, "outcome": "error", "reason": "x"} if n.startswith("b")
                        else _fitted(n))
    assert fr.main() == 5
    m = json.loads((env / "out/timebase_manifest.json").read_text())
    assert m["status"] == "incomplete" and m["errors"] == ["b-2-deu-wsv"] and m["outcomes"] == {"fitted": 1, "error": 1}


def test_fit_run_completes_with_exit_0_and_names_the_audit(env, monkeypatch):
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    monkeypatch.setattr(fr, "one", _fitted)
    assert fr.main() == 0
    m = json.loads((env / "out/timebase_manifest.json").read_text())
    assert m["status"] == "complete" and m["audit"]["status"] == "complete" and m["audit"]["weak_crashes"] == []
    assert m["inputs"]["runner"]["path"].endswith("gesla/fit_run.py")


def test_one_cleans_and_corrects_the_sampling_with_the_published_correction(monkeypatch):
    # The time shift (clean) and the amplitude factor (sampling_corrected) get the same correction: the record's
    # published one (here utc_mean_start60, with a bad month dropped).
    row = {"time_base_status": "corrected", "published_time_base": "utc_mean_start60", "months_bad": "2020-02",
           "time_base_code": "corrected:utc_instant->utc_mean_start60:sibling"}
    seen = {}

    class Rec:
        gauge_type, licence_class, originator, interval_min, good = "Coastal", "A", True, 60, None

        def __len__(self):
            return 10

    class Rel:
        def metadata(self):
            return {"b-2-deu-wsv": {"TIME ZONE HOURS": "0", "LATITUDE": "1", "LONGITUDE": "2"}}

        def record(self, name, roles):
            r = Rec()
            r.good = type("G", (), {"sum": lambda self: 10})()
            return r

    import numpy as np

    class Win:
        times_s = np.array([0, 3600], dtype=np.int64)

        def __len__(self):
            return 2

    monkeypatch.setattr(fr, "_rel", Rel())
    monkeypatch.setattr(fr, "_tb", {"b-2-deu-wsv": row})
    monkeypatch.setattr(fr, "_table", object())
    monkeypatch.setattr(fr, "clean", lambda rec, corr: seen.setdefault("clean", corr) and [1] * 5)
    monkeypatch.setattr(fr, "last_years", lambda s, y: s)
    monkeypatch.setattr(fr, "in_tcd_years", lambda t, s: Win())
    monkeypatch.setattr(fr, "record_hours", lambda t: 10_000.0)
    rf = type("RF", (), {"primary": type("P", (), {"first_s": 0, "last_s": 3600})()})()
    monkeypatch.setattr(fr, "fit_record", lambda t, s: rf)

    def sc(rf, table, corr, span):
        seen["sampling"] = (rf, corr, span)
        raise RuntimeError("stop here")

    monkeypatch.setattr(fr, "sampling_corrected", sc)
    out = fr.one("b-2-deu-wsv")
    want = Correction("utc_mean_start60", months_drop=((2020, 2),))
    assert seen["clean"] == want and seen["sampling"][1] == want and seen["sampling"][0] is rf
    assert seen["sampling"][2] == (0, 7200)
    assert out["outcome"] == "error" and "stop here" in out["reason"]   # a failure here is an error, not a fit


def test_the_fit_run_manifest_hashes_every_fit_file(env, monkeypatch):
    # WR-1.0 (check_fit_run) refuses a fits/<rid>.json that does not match the run: the manifest maps every
    # fitted record to its fit file's sha256.
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    monkeypatch.setattr(fr, "one", _fitted)
    assert fr.main() == 0
    m = json.loads((env / "out/timebase_manifest.json").read_text())
    assert m["fits_sha256"] == {n: ta.sha256_file(env / "out/fits" / f"{n}.json") for n in _Release.names}
    assert m["dirty"] in (True, False) and m["commit"] and m["outputs"]["results.jsonl"]
    assert {"tcd_table", "gesla_manifest", "timebase_csv"} <= set(m["inputs"])


def test_a_fitted_record_without_its_fit_file_makes_the_run_incomplete(env, monkeypatch):
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    monkeypatch.setattr(fr, "one", lambda n: _fitted(n) if n.startswith("a") else
                        {"record": n, "outcome": "fitted", "reason": "x"})
    assert fr.main() == 5
    m = json.loads((env / "out/timebase_manifest.json").read_text())
    assert m["status"] == "incomplete" and m["errors"] == ["b-2-deu-wsv"] and "b-2-deu-wsv" not in m["fits_sha256"]


def test_a_fitted_row_records_the_fits_own_span_and_scales_over_it(monkeypatch, tmp_path):
    # WR-1.0 checks each fit's first_s/last_s against its results.jsonl row: the row's first/last must be the span
    # the fit used (after despiking: here 2 h later than the window), and the sampling factor uses the same span.
    import numpy as np
    seen = {}

    class Fit:
        names = ("M2",)
        amplitude_m = {"M2": 1.0}
        phase_deg = {"M2": 10.0}
        rms_m = 0.1

    class P:
        fit, first_s, last_s, snr_dropped = Fit(), 7200, 36000, ()

    class RF:
        primary, half_errors, u = P(), (), {"M2": 0.01}

        def to_json(self):
            return "{}"

    class Win:
        times_s = np.array([0, 36000], dtype=np.int64)

        def __len__(self):
            return 2

    class Rel:
        def metadata(self):
            return {"a-1-nor-nhs": {"TIME ZONE HOURS": "0", "LATITUDE": "1", "LONGITUDE": "2"}}

        def record(self, name, roles):
            return type("R", (), {"gauge_type": "C", "licence_class": "A", "originator": True, "interval_min": 60,
                                  "good": np.ones(3, dtype=bool), "__len__": lambda self: 3})()

    monkeypatch.setattr(fr, "FITS", tmp_path)
    monkeypatch.setattr(fr, "_rel", Rel())
    monkeypatch.setattr(fr, "_tb", {})
    monkeypatch.setattr(fr, "_table", object())
    monkeypatch.setattr(fr, "clean", lambda rec, corr: [1] * 5)
    monkeypatch.setattr(fr, "last_years", lambda s, y: s)
    monkeypatch.setattr(fr, "in_tcd_years", lambda t, s: Win())
    monkeypatch.setattr(fr, "record_hours", lambda t: 10_000.0)
    monkeypatch.setattr(fr, "fit_record", lambda t, s: RF())
    monkeypatch.setattr(fr, "sampling_corrected", lambda rf, table, corr, span: seen.setdefault("span", span) and rf)
    out = fr.one("a-1-nor-nhs")
    assert out["outcome"] == "fitted", out
    assert (out["first"], out["last"]) == (7200, 36000) and (out["window_first"], out["window_last"]) == (0, 36000)
    assert seen["span"] == (7200, 36000 + 3600)


# --- gate5 review C M1-M3 (and A 3): a fit run never reports complete over a bad write, a resume across a change,
# or an input-integrity failure ------------------------------------------------------------------------------------

def _one_env(monkeypatch, tmp_path, record=None, clean=None):
    """fr.one with a stub release / table / fit: record() and clean() are given or trivial."""
    import numpy as np

    class Fit:
        names = ("M2",)
        amplitude_m = {"M2": 1.0}
        phase_deg = {"M2": 10.0}
        rms_m = 0.1

    class RF:
        primary = type("P", (), {"fit": Fit(), "first_s": 0, "last_s": 3600, "snr_dropped": ()})()
        half_errors, u = (), {"M2": 0.01}

        def to_json(self):
            return '{"fit": "new"}'

    class Rel:
        def metadata(self):
            return {"a-1-nor-nhs": {"TIME ZONE HOURS": "0", "LATITUDE": "1", "LONGITUDE": "2"}}

        def record(self, name, roles):
            if record:
                return record()
            return type("R", (), {"gauge_type": "C", "licence_class": "A", "originator": True, "interval_min": 60,
                                  "good": np.ones(3, dtype=bool), "__len__": lambda self: 3})()

    class Win:
        times_s = np.array([0, 3600], dtype=np.int64)

        def __len__(self):
            return 2

    monkeypatch.setattr(fr, "FITS", tmp_path)
    monkeypatch.setattr(fr, "_rel", Rel())
    monkeypatch.setattr(fr, "_tb", {})
    monkeypatch.setattr(fr, "_table", object())
    monkeypatch.setattr(fr, "clean", clean or (lambda rec, corr: [1] * 5))
    monkeypatch.setattr(fr, "last_years", lambda s, y: s)
    monkeypatch.setattr(fr, "in_tcd_years", lambda t, s: Win())
    monkeypatch.setattr(fr, "record_hours", lambda t: 10_000.0)
    monkeypatch.setattr(fr, "fit_record", lambda t, s: RF())
    monkeypatch.setattr(fr, "sampling_corrected", lambda rf, table, corr, span: rf)


def test_a_failed_fit_file_write_is_an_error_and_leaves_no_stale_file(monkeypatch, tmp_path):
    # Gate5 C M1: a stale fits/<rid>.json from an earlier run must not survive a failed (re)write as "the fit".
    _one_env(monkeypatch, tmp_path)
    (tmp_path / "a-1-nor-nhs.json").write_text('{"fit": "stale"}')

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fr.os, "replace", boom)
    out = fr.one("a-1-nor-nhs")
    assert out["outcome"] == "error" and "No space left" in out["reason"], out
    assert not (tmp_path / "a-1-nor-nhs.json").exists() and not list(tmp_path.glob("*.tmp*"))


def test_a_fit_file_is_written_atomically(monkeypatch, tmp_path):
    _one_env(monkeypatch, tmp_path)
    (tmp_path / "a-1-nor-nhs.json").write_text('{"fit": "stale"}')
    assert fr.one("a-1-nor-nhs")["outcome"] == "fitted"
    assert (tmp_path / "a-1-nor-nhs.json").read_text() == '{"fit": "new"}' and not list(tmp_path.glob("*.tmp*"))


def test_input_integrity_failures_and_bugs_are_errors_not_read_errors(monkeypatch, tmp_path):
    # Gate5 C M3: only a malformed record (the parser's own ValueError) is a read_error; a member whose bytes do not
    # match the release manifest, a record with no usable verdict, and any bug fail the run.
    from gesla.read_gesla import ShaMismatch
    from gesla.timebase_verdict import UnexplainedRecord

    def raises(e):
        def f(*a, **k):
            raise e
        return f

    cases = {"sha": (raises(ShaMismatch("member a: sha256 x")), None, "error"),
             "parse": (raises(ValueError("a-1-nor-nhs: header lacks LATITUDE")), None, "read_error"),
             "unexplained": (None, raises(UnexplainedRecord("no usable time-base verdict")), "error"),
             "bug": (None, raises(KeyError("months_drop")), "error")}
    got = {}
    for k, (rec, cl, _want) in cases.items():
        _one_env(monkeypatch, tmp_path, record=rec, clean=cl)
        got[k] = fr.one("a-1-nor-nhs")["outcome"]
    assert got == {k: v[2] for k, v in cases.items()}, got


def test_resume_refuses_a_dir_from_another_commit_or_audit_and_keeps_its_manifest(env, monkeypatch, tmp_path):
    # Gate5 C M2 / A 3: rerunning into a FIT_OUT that holds rows of another commit or inputs must refuse, before it
    # rewrites that run's manifest; the same identity resumes.
    git = {"rev-parse": "c" * 40 + "\n", "status": ""}           # a clean checkout at one commit
    monkeypatch.setattr(ta, "_git", lambda _repo, *a: git[a[0]])
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    monkeypatch.setattr(fr, "one", _fitted)
    assert fr.main() == 0
    mp = env / "out/timebase_manifest.json"
    first = mp.read_text()
    assert fr.main() == 0                                          # same identity: resumes (nothing left to do)
    other = env / "other"
    other.mkdir()
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(other, rows=[{"file_id": "z", "time_base_status": "unverified"}])
                                           / "timebase.csv"))
    before = mp.read_text()
    assert fr.main() == 6
    assert mp.read_text() == before and json.loads(first)["inputs"]["timebase_csv"] == json.loads(before)["inputs"]["timebase_csv"]
    monkeypatch.setenv("TIMEBASE_CSV", str(env / "audit/timebase.csv"))   # the same audit, but another commit
    git["rev-parse"] = "d" * 40 + "\n"
    assert fr.main() == 6 and mp.read_text() == before
    git.update({"rev-parse": "c" * 40 + "\n", "status": " M gesla/fit_run.py\n"})  # same commit, dirty tree
    assert fr.main() == 6 and mp.read_text() == before


def test_results_without_a_manifest_are_not_resumed(env, monkeypatch):
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    (env / "out").mkdir()
    (env / "out/results.jsonl").write_text(json.dumps({"record": "a-1-nor-nhs", "outcome": "fitted"}) + "\n")
    assert fr.main() == 6 and not (env / "out/timebase_manifest.json").exists()


def test_a_write_error_row_makes_the_run_incomplete(env, monkeypatch):
    # Gate5 C M1 (main side): a row carrying write_error, even if marked fitted, is an error of the run.
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    monkeypatch.setattr(fr, "one", lambda n: {**_fitted(n), **({"write_error": "EACCES"} if n.startswith("b") else {})})
    assert fr.main() == 5
    assert json.loads((env / "out/timebase_manifest.json").read_text())["errors"] == ["b-2-deu-wsv"]


# --- gate5 review B F1, F3 and advisories -----------------------------------------------------------------------

@pytest.mark.parametrize("status", ["unverified", "disputed"])
def test_an_unverified_or_disputed_row_is_fitted_with_the_declared_time_base(status):
    # The owner rule: only verified/corrected rows publish the audit's correction; every other record keeps GESLA's
    # declared time base, whatever the audit's own guess (published_time_base, months_bad) says.
    row = {"time_base_status": status, "published_time_base": "utc_mean_start60", "months_bad": "2020-02",
           "time_base_code": f"{status}:x", "correction": ""}
    for n_decl, label in ((0, "utc_instant"), (1, "utc_plus_1")):
        corr, why = fr.published(row, n_decl)
        assert corr == ta.declared_correction(n_decl) and corr.label == label and not corr.months_drop, (status, corr)
        assert why == f"{status}: declared"


def test_a_record_not_in_the_audit_pool_is_fitted_with_the_declared_time_base():
    corr, why = fr.published(None, -2)
    assert corr == ta.declared_correction(-2) and why == "not in the audit pool: declared"


@pytest.mark.parametrize("status", ["unverified", "disputed", "not_in_pool"])
def test_one_fits_unused_records_with_the_declared_base_not_the_audits(monkeypatch, tmp_path, status):
    # Gate6 recheck F3, through one(): the record declares UTC+1 and the audit's own time base differs (mean60,
    # a bad month); clean() must get the declared utc_plus_1, and the row must say so.
    seen = {}
    _one_env(monkeypatch, tmp_path, clean=lambda rec, corr: seen.setdefault("corr", corr) and [1] * 5)
    fr._rel.metadata = lambda: {"a-1-nor-nhs": {"TIME ZONE HOURS": "1", "LATITUDE": "1", "LONGITUDE": "2"}}
    if status != "not_in_pool":
        monkeypatch.setattr(fr, "_tb", {"a-1-nor-nhs": {
            "time_base_status": status, "published_time_base": "utc_mean_start60", "months_bad": "2020-02",
            "time_base_code": f"{status}:x", "correction": "utc_mean_start60"}})
    out = fr.one("a-1-nor-nhs")
    assert seen["corr"] == Correction("utc_plus_n", n=1) and out["time_base"] == "utc_plus_1", (status, seen)
    assert out["months_dropped"] == 0 and out["time_base_from"].endswith("declared"), out["time_base_from"]


def test_a_verified_or_corrected_row_is_fitted_with_its_correction():
    row = {"time_base_status": "corrected", "published_time_base": "utc_mean_start60", "months_bad": "2020-02 2021-11",
           "time_base_code": "corrected:x", "correction": "utc_mean_start60"}
    corr, _ = fr.published(row, 0)
    assert corr == Correction("utc_mean_start60", months_drop=((2020, 2), (2021, 11)))


def test_a_resume_fits_only_the_records_not_done_and_never_duplicates_a_row(env, monkeypatch):
    # Gate5 B F1: the same identity resumes: rows already in results.jsonl are kept, not fitted again.
    git = {"rev-parse": "c" * 40 + "\n", "status": ""}
    monkeypatch.setattr(ta, "_git", lambda _repo, *a: git[a[0]])
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    called = []

    def one(n):
        called.append(n)
        return _fitted(n)

    monkeypatch.setattr(fr, "one", one)
    monkeypatch.setenv("LIMIT", "1")                     # a first, partial run: only the first record
    assert fr.main() == 0 and called == ["a-1-nor-nhs"]
    monkeypatch.delenv("LIMIT")
    assert fr.main() == 0 and called == ["a-1-nor-nhs", "b-2-deu-wsv"]
    rows = _results(env)
    assert sorted(r["record"] for r in rows) == ["a-1-nor-nhs", "b-2-deu-wsv"]      # one row each
    assert json.loads((env / "out/timebase_manifest.json").read_text())["status"] == "complete"
    # a same-identity run over a complete dir fits nothing and adds no row
    assert fr.main() == 0 and called == ["a-1-nor-nhs", "b-2-deu-wsv"]
    assert sorted(r["record"] for r in _results(env)) == ["a-1-nor-nhs", "b-2-deu-wsv"]


@pytest.mark.parametrize("var, value", [("LIMIT", "1"), ("ONLY", None)])
def test_a_limited_run_is_partial_never_complete(env, monkeypatch, var, value):
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))
    monkeypatch.setattr(fr, "one", _fitted)
    if value is None:
        (env / "only.txt").write_text("b-2-deu-wsv\n")
        value = str(env / "only.txt")
    monkeypatch.setenv(var, value)
    assert fr.main() == 0
    m = json.loads((env / "out/timebase_manifest.json").read_text())
    assert m["status"] == "partial" and m["n_results"] == 1, m["status"]


def test_a_used_row_whose_correction_does_not_rebuild_stops_the_run_before_any_fit(env, monkeypatch):
    # The precheck: every verified/corrected row's published label must rebuild to its CSV correction.
    rows = [{"file_id": "a-1-nor-nhs", "time_base_status": "verified", "time_base_code": "verified:official",
             "published_time_base": "utc_instant", "correction": "utc_mean_start60", "months_bad": ""}]
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env, rows) / "timebase.csv"))
    called = []
    monkeypatch.setattr(fr, "one", lambda n: called.append(n) or _fitted(n))
    with pytest.raises(SystemExit, match="rebuilt correction utc_instant != CSV utc_mean_start60"):
        fr.main()
    assert called == [] and _results(env) == []


def test_a_task_that_raises_in_the_worker_is_an_error_row(env, monkeypatch):
    monkeypatch.setenv("TIMEBASE_CSV", str(_audit(env) / "timebase.csv"))

    def one(n):
        if n.startswith("b"):
            raise MemoryError("worker killed")
        return _fitted(n)

    monkeypatch.setattr(fr, "one", one)
    assert fr.main() == 5
    r = {x["record"]: x for x in _results(env)}["b-2-deu-wsv"]
    assert r["outcome"] == "error" and r["reason"].startswith("worker: MemoryError")
