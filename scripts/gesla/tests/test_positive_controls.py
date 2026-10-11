"""G04b: the positive controls (BP §2, "Positive controls, run every build"), on real records.

- the pinned GESLA-4.0 WSV fixtures (SHA-256 checked) -> local_dst:Europe/Berlin;
- a copy of a real NOAA record shifted by +60 min -> utc_plus_1;
- a 60-min boxcar copy of a real 10-min record -> utc_mean_start60.
A wrong control raises PositiveControlError, and `python -m gesla.timebase_verdict controls`
exits non-zero. Runs when the verified GESLA cache and the TCD dump are present.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gesla.read_gesla import GeslaRelease, otc_work
from gesla.tcd_table import TcdTable
from gesla.timebase_verdict import CONTROLS, PositiveControlError, run_positive_controls

GESLA_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def real():
    rel_root = otc_work() / "inputs" / "gesla" / "4.1"
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    missing = [str(p) for p in (rel_root / "GESLA4.1_ALL.zip", rel_root / "MANIFEST.json", dump) if not p.exists()]
    if missing:
        pytest.skip(f"real-record inputs not in the cache: {missing}")
    return GeslaRelease(rel_root), TcdTable.load(dump)


def test_controls_come_out_right(real, capsys):
    release, table = real
    results = run_positive_controls(release, table)
    with capsys.disabled():
        print()
        for c in results:
            print(c.line())
    got = {c.name: c.verdict.verdict for c in results}
    assert got == {c.name: c.expect for c in CONTROLS}
    assert all(c.verdict.passed for c in results)
    # The 4.0 fixtures were read against the manifest (read_fixture checks SHA-256 and size).
    shas = {c.name: c.member_sha256 for c in results}
    assert shas["wsv40:helgolandbinnenhafen-9510070-deu-wsv"].startswith("ab973059")


def test_a_wrong_control_raises(real, monkeypatch):
    release, table = real
    monkeypatch.setenv("OTC_TB_TEST_SWAP_CLASSES", "utc_mean_start60,utc_instant")
    with pytest.raises(PositiveControlError, match="boxcar"):
        run_positive_controls(release, table)


def test_a_wrong_control_stops_the_build(real):
    env = dict(os.environ, OTC_TB_TEST_SWAP_CLASSES="utc_mean_start60,utc_instant")
    p = subprocess.run([sys.executable, "-m", "gesla.timebase_verdict", "controls"], cwd=GESLA_DIR, env=env,
                       capture_output=True, text=True, timeout=900)
    assert p.returncode != 0
    assert "PositiveControlError" in p.stderr
