"""otc_pipeline.gesla_build: the release is built from committed code only, into an empty directory. The build
itself runs on real inputs (the release-integration proof); these tests cover the guards around it."""
import subprocess

import pytest

from otc_pipeline import gesla_build as gb

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
