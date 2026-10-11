"""The audit's outputs do not depend on the worker count.

From the code (gesla.timebase_audit.audit): every phase hands its tasks to executor.map, which
yields the results in the order of the inputs whatever the number of workers or the order they
finish in; the tasks are pure functions of their arguments and of the worker state _init builds
from the same arguments; the worker's record cache only returns the same parsed record again; the
CSVs are written sorted by file id. So 1 worker and 3 workers must give the same bytes. This test
runs a tiny real audit (three small sibling pairs with official comparators, so round 0, its
cross-check and the final cross-check all run) both ways and compares timebase.csv,
timebase_providers.csv and the residue cases byte for byte.

Needs the verified GESLA cache, the TCD dump and the gates cache; skipped otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import real_data

from gesla.read_gesla import otc_work
from gesla.residue import append
from gesla.timebase_audit import audit, load_inventory, write_csv, write_providers

REPO = Path(__file__).resolve().parents[3]
TINY = ("puerto_cortes-9650593-usa-noaa", "sekiu-9443361-usa-noaa", "perkpolder_walsoorden-per-nld-rws")


@pytest.fixture(scope="module")
def inputs():
    work = otc_work()
    release = work / "inputs" / "gesla" / "4.1"
    table = Path(os.environ.get("OTC_TCD_TABLE", work / "tcd_table.json"))
    gates = Path(os.environ.get("OTC_GATES_DIR", work / "gates"))
    inv = REPO / "data" / "gesla" / "4.1" / "inventory.csv"
    missing = [str(p) for p in (release / "GESLA4.1_ALL.zip", release / "MANIFEST.json", table, gates, inv)
               if not p.exists()]
    real_data.require(missing, "real audit inputs")
    return load_inventory(inv), release, table, gates


def _run(inputs, workers: int, out: Path) -> dict[str, bytes]:
    inv, release, table, gates = inputs
    res = audit(inv, TINY, release_root=release, table_path=table, gates_dir=gates, workers=workers)
    out.mkdir(parents=True)
    write_csv(res.rows, out / "timebase.csv")
    write_providers(res.providers, out / "timebase_providers.csv")
    append(out / "residue.jsonl", res.residue)
    return {p.name: p.read_bytes() for p in sorted(out.iterdir())}


def test_one_and_three_workers_give_the_same_bytes(inputs, tmp_path):
    one = _run(inputs, 1, tmp_path / "w1")
    three = _run(inputs, 3, tmp_path / "w3")
    assert set(one) == {"timebase.csv", "timebase_providers.csv", "residue.jsonl"}
    assert one["timebase.csv"].count(b"\n") == 7, "the tiny audit is 6 records and a header"
    for name in one:
        assert one[name] == three[name], f"{name} differs between 1 and 3 workers"
