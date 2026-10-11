"""Every sealed T1 station is tidal and has every T1 level in all 12 months of its 2015 monthly_mean
(datums spec §5.2 T1 compares MSL, MHW, MLW, MTL, MN, MHHW, MLLW, DHQ and DLQ with NOAA's monthly_mean).

The check itself is tested on hand-made stations (fixtures/datums/t1_synthetic.json). The real check
reads the DT0b fetch outside the repository: set OTC_DT0B_EVIDENCE to the DT0b evidence directory
(it holds t1/stations-waterlevels.json and t1/t1-checks.jsonl); without it that test is skipped.
"""
import json
import os
from pathlib import Path

import pytest

from otc_pipeline.datum_partition import PARTITIONS, load

LEVELS = ("MSL", "MHW", "MLW", "MTL", "MN", "MHHW", "MLLW", "DHQ", "DLQ")
SYNTHETIC = json.loads((Path(__file__).parent / "fixtures" / "datums" / "t1_synthetic.json").read_text())


def t1_failures(ids, evidence):
    """[(id, reason)] for every id that is not tidal or lacks a T1 level, not inferred, in any month of 2015.
    evidence: {id: {"tidal": bool, "monthly_mean_2015": [rows with month, inferred and the levels]}}."""
    bad = []
    for sid in ids:
        e = evidence.get(sid)
        if e is None:
            bad.append((sid, "no evidence"))
            continue
        if e["tidal"] is not True:
            bad.append((sid, f"tidal={e['tidal']}"))
        rows = e["monthly_mean_2015"]
        if sorted(int(r["month"]) for r in rows if r["inferred"] == "0") != list(range(1, 13)):
            bad.append((sid, "not 12 months marked not inferred"))
        blank = sorted({lv for r in rows for lv in LEVELS if (r.get(lv) or "").strip() == ""})
        if blank:
            bad.append((sid, f"blank levels {blank}"))
    return bad


def test_check_passes_a_tidal_station_with_every_level():
    assert t1_failures(["0000001"], SYNTHETIC["stations"]) == []


@pytest.mark.parametrize("sid,reason", [
    ("0000002", "tidal=False"),
    ("0000003", "blank levels"),
    ("0000004", "not 12 months"),
    ("0000005", "not 12 months"),
    ("9999999", "no evidence"),
])
def test_check_fails_each_bad_station(sid, reason):
    bad = t1_failures([sid], SYNTHETIC["stations"])
    assert bad and all(b[0] == sid for b in bad)
    assert any(r.startswith(reason) for _, r in bad), bad


def _dt0b_evidence(ev: Path) -> dict:
    meta = {s["id"]: s for s in json.loads((ev / "t1" / "stations-waterlevels.json").read_text())["stations"]}
    out = {}
    for line in (ev / "t1" / "t1-checks.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["check"] == "monthly_mean_2015" and r["status"] == "ok":
            out[r["id"]] = {"tidal": meta[r["id"]]["tidal"],
                            "monthly_mean_2015": [m for m in (r.get("data") or []) if m.get("year") == "2015"]}
    return out


@pytest.mark.skipif(not os.environ.get("OTC_DT0B_EVIDENCE"), reason="set OTC_DT0B_EVIDENCE to the DT0b evidence dir")
@pytest.mark.parametrize("name", PARTITIONS)
def test_every_sealed_t1_station_is_tidal_with_all_t1_levels_in_2015(name):
    evidence = _dt0b_evidence(Path(os.environ["OTC_DT0B_EVIDENCE"]))
    assert t1_failures([s["id"] for s in load("t1", name, sealed_run=True)], evidence) == []


def test_t1g_generator_reproduces_the_sealed_file_byte_for_byte():
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("t1g_gap_patterns", root / "tools" / "t1g_gap_patterns.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.build() == (root.parents[1] / "data" / "datums" / "t1g_gap_patterns.json").read_text()
