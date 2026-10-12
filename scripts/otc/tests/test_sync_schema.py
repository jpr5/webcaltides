"""sync_schema.sh: never replace the committed copy with unverified content."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(os.environ["OTC_SITE_REPO"]) if os.environ.get("OTC_SITE_REPO") else None
PIN = "533eafa0ad1e5a79f9c3da6cd024f200b131fc03"


def _site_has_pin():
    if SITE is None:
        return False
    return subprocess.run(["git", "-C", str(SITE), "cat-file", "-e", f"{PIN}^{{commit}}"],
                          capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _site_has_pin(), reason=f"needs OTC_SITE_REPO, a site checkout with commit {PIN[:7]}")


@pytest.fixture
def tree(tmp_path):
    """A copy of scripts/otc with only what sync_schema.sh touches."""
    t = tmp_path / "otc"
    (t / "schema").mkdir(parents=True)
    (t / "tests" / "fixtures").mkdir(parents=True)
    shutil.copy2(ROOT / "sync_schema.sh", t / "sync_schema.sh")
    shutil.copy2(ROOT / "schema" / "otc-1.0.schema.json", t / "schema" / "otc-1.0.schema.json")
    shutil.copy2(ROOT / "tests" / "fixtures" / "example-1.0.json", t / "tests" / "fixtures" / "example-1.0.json")
    for p in (t / "schema" / "otc-1.0.schema.json", t / "tests" / "fixtures" / "example-1.0.json"):
        p.chmod(0o644)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    return t, tmpdir


def run(t, tmpdir, *args):
    env = dict(os.environ, TMPDIR=str(tmpdir))
    args = args or ("--site-repo", str(SITE))
    return subprocess.run(["bash", str(t / "sync_schema.sh"), *args], env=env,
                          capture_output=True, text=True)


def test_sync_ok_keeps_mode_and_leaves_no_temp_files(tree):
    t, tmpdir = tree
    r = run(t, tmpdir)
    assert r.returncode == 0, r.stderr
    for p in (t / "schema" / "otc-1.0.schema.json", t / "tests" / "fixtures" / "example-1.0.json"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o644, p
    assert list(tmpdir.iterdir()) == []


def test_wrong_pin_does_not_replace_the_committed_copy(tree):
    t, tmpdir = tree
    script = t / "sync_schema.sh"
    text = script.read_text()
    good = "c304322a7efc4546dcf720bb81710fc0dfec6cd9eaa63dfeb184e377508046b9"
    assert good in text
    script.write_text(text.replace(good, "0" * 64))
    schema = t / "schema" / "otc-1.0.schema.json"
    schema.write_bytes(b'{"committed": true}\n')
    r = run(t, tmpdir)
    assert r.returncode != 0
    assert schema.read_bytes() == b'{"committed": true}\n'
    # the example is still checked and reported although the schema failed first
    assert "example-1.0.json" in r.stdout + r.stderr
    assert list(tmpdir.iterdir()) == []


def test_check_reports_both_files(tree):
    t, tmpdir = tree
    (t / "schema" / "otc-1.0.schema.json").write_text("{}")
    r = run(t, tmpdir, "--check")
    assert r.returncode != 0
    assert "FAIL" in r.stderr and "ok:" in r.stdout and "example-1.0.json" in r.stdout


def test_no_source_named_is_refused(tree):
    t, tmpdir = tree
    for args in ((), ("--site-repo",)):
        r = subprocess.run(["bash", str(t / "sync_schema.sh"), *args], env=dict(os.environ, TMPDIR=str(tmpdir)),
                           capture_output=True, text=True)
        assert r.returncode == 2 and "usage" in r.stderr
