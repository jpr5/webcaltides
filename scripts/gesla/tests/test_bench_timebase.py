"""bench_timebase.py never writes or impersonates a real build (TB-PERF-2 review C, M1).

The bench runs gesla.timebase_audit.main on a subset, by default with the positive controls
stubbed out. Its output must therefore (a) go only to an explicit --out-dir outside
$OTC_WORK/builds, (b) carry a manifest that says it is a benchmark, which check_audit_outputs
refuses, (c) leave run_positive_controls as it found it, and (d) keep residue.jsonl inside the
out-dir. ta.main is replaced by a stand-in that writes what the real one writes (the manifest via
the real write_manifest, residue where the real one puts it), so no audit runs here; the real run
is proved in the TB-PERF-2 M1 proof.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import gesla.timebase_audit as ta
import gesla.timebase_verdict as tv
from gesla import residue as residue_mod

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bench_timebase as bench  # noqa: E402


@pytest.fixture
def work(tmp_path, monkeypatch):
    w = tmp_path / "work"
    (w / "builds").mkdir(parents=True)
    monkeypatch.setenv("OTC_WORK", str(w))
    return w


@pytest.fixture
def fake_main(monkeypatch, tmp_path):
    """Stands in for ta.main: writes timebase.csv, residue (to out/residue.jsonl with --out-dir, else
    to today's build, as the real main does) and a status-complete manifest; records the controls
    function it saw."""
    calls: list[dict] = []

    def fake(argv):
        out_dir = argv[argv.index("--out-dir") + 1] if "--out-dir" in argv else None
        work = Path(__import__("os").environ["OTC_WORK"])
        out = Path(out_dir) if out_dir else work / "builds" / "20261010" / "gesla"
        out.mkdir(parents=True, exist_ok=True)
        (out / "timebase.csv").write_text("file_id\nx\n")
        rp = out / "residue.jsonl" if out_dir else residue_mod.residue_path(work, "20261010")
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text("")
        ta.write_manifest(out, tmp_path, argv, {}, status="complete", outputs=[out / "timebase.csv", rp],
                          extra={"weak_crashes": []})
        calls.append({"argv": list(argv), "controls": tv.run_positive_controls})
        return 0

    monkeypatch.setattr(ta, "main", fake)
    return calls


def test_refuses_without_an_out_dir(work, fake_main):
    with pytest.raises(SystemExit, match="--out-dir"):
        bench.main(["--only", "x"])
    assert not fake_main, "the audit ran"
    assert not any((work / "builds").iterdir()), "the bench wrote under $OTC_WORK/builds"


@pytest.mark.parametrize("form", ["sep", "eq"])
def test_refuses_an_out_dir_under_builds(work, fake_main, form):
    inside = work / "builds" / "20261010" / "gesla"
    args = ["--out-dir", str(inside)] if form == "sep" else [f"--out-dir={inside}"]
    with pytest.raises(SystemExit, match="OTC_WORK/builds"):
        bench.main(["--only", "x", *args])
    assert not fake_main and not inside.exists()


def test_refuses_builds_reached_through_a_link(work, fake_main, tmp_path):
    link = tmp_path / "elsewhere"
    link.symlink_to(work / "builds")
    with pytest.raises(SystemExit, match="OTC_WORK/builds"):
        bench.main(["--out-dir", str(link / "b")])
    assert not fake_main


def test_output_is_a_benchmark_that_consumers_refuse(work, fake_main, tmp_path):
    out = tmp_path / "bench"
    assert bench.main(["--only", "x", "--out-dir", str(out)]) == 0
    m = ta.json.loads((out / ta.MANIFEST).read_text())
    assert m["status"] == "benchmark"
    assert m["benchmark"] == {"controls": "skipped"}
    with pytest.raises(ValueError, match="benchmark"):
        ta.check_audit_outputs(out)
    assert (out / "residue.jsonl").exists()
    assert not any((work / "builds").iterdir()), "residue or outputs landed under $OTC_WORK/builds"


def test_with_controls_is_recorded(work, fake_main, tmp_path):
    out = tmp_path / "bench"
    orig = tv.run_positive_controls
    bench.main(["--with-controls", "--out-dir", str(out)])
    assert fake_main[0]["controls"] is orig, "--with-controls must run the real controls"
    assert ta.json.loads((out / ta.MANIFEST).read_text())["benchmark"] == {"controls": "run"}
    with pytest.raises(ValueError, match="benchmark"):
        ta.check_audit_outputs(out)


def test_controls_are_restored_after_a_run_and_after_a_crash(work, fake_main, monkeypatch, tmp_path):
    orig = tv.run_positive_controls
    bench.main(["--out-dir", str(tmp_path / "a")])
    assert fake_main[0]["controls"] is not orig, "by default the bench stubs the controls"
    assert tv.run_positive_controls is orig

    def boom(argv):
        raise RuntimeError("audit crashed")

    monkeypatch.setattr(ta, "main", boom)
    with pytest.raises(RuntimeError):
        bench.main(["--out-dir", str(tmp_path / "b")])
    assert tv.run_positive_controls is orig


def test_check_audit_outputs_refuses_a_benchmark_key_even_if_complete(tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "timebase.csv").write_text("file_id\n")
    ta.write_manifest(out, tmp_path, [], {}, status="complete", outputs=[out / "timebase.csv"],
                      extra={"weak_crashes": [], "benchmark": {"controls": "skipped"}})
    with pytest.raises(ValueError, match="benchmark"):
        ta.check_audit_outputs(out)


@pytest.mark.parametrize("abbrev", ["--out", "--out-d", "--ou"])
def test_refuses_an_abbreviated_out_dir(work, fake_main, tmp_path, abbrev):
    """The audit's argparse takes a unique prefix of --out-dir as --out-dir, and the last one wins: an
    abbreviation after the validated --out-dir would move the output past the check."""
    inside = work / "builds" / "20261010" / "gesla"
    for args in (["--out-dir", str(tmp_path / "ok"), abbrev, str(inside)],
                 ["--out-dir", str(tmp_path / "ok"), f"{abbrev}={inside}"]):
        with pytest.raises(SystemExit, match="abbreviat"):
            bench.main(args)
    assert not fake_main and not inside.exists()


def test_a_failed_benchmark_mark_does_not_hide_the_audit_error(work, monkeypatch, tmp_path):
    def boom(argv):
        raise RuntimeError("audit crashed")

    def bad_mark(out, with_controls):
        raise OSError("manifest not writable")

    monkeypatch.setattr(ta, "main", boom)
    monkeypatch.setattr(bench, "_mark_benchmark", bad_mark)
    orig = tv.run_positive_controls
    with pytest.raises(RuntimeError, match="audit crashed") as ei:
        bench.main(["--out-dir", str(tmp_path / "o")])
    assert isinstance(ei.value.__cause__, OSError), "the mark failure is chained to the audit's error"
    assert tv.run_positive_controls is orig


def test_a_failed_benchmark_mark_after_a_clean_audit_raises(work, fake_main, monkeypatch, tmp_path):
    """An unmarked complete manifest would pass for an audit: the bench must fail, not print a table."""
    def bad_mark(out, with_controls):
        raise OSError("manifest not writable")

    monkeypatch.setattr(bench, "_mark_benchmark", bad_mark)
    with pytest.raises(OSError):
        bench.main(["--out-dir", str(tmp_path / "o")])
