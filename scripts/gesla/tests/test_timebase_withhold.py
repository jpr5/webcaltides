"""FIX-LST-4: the reviewed-withhold list (timebase_audit.load_withhold / apply_reviewed_withhold)."""

from __future__ import annotations

import sys
from pathlib import Path

import json

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from gesla import timebase_audit as ta
from gesla.timebase_audit import AuditRow, RecordInfo
from gesla.timebase_verdict import NOT_USABLE, Correction

UNV = ("unverified", "none", NOT_USABLE)


def entry(fid: str = "wcoco_t-wcoco-usa-sfwmd", **kw) -> dict:
    e = {"file_id": fid, "short": "leads 3 NOAA stations by 234-409 min", "reason": "a damped canal cannot lead",
         "evidence": {"reviewer": "gate C", "report": "gateC/sfwmd-harcon.txt", "numbers": "-234..-409"},
         "date": "2026-10-11"}
    e.update(kw)
    return e


def setup(spec: dict):
    inv, rows = {}, {}
    for f, (status, kind, corr) in spec.items():
        inv[f] = RecordInfo(f, f.rsplit("-", 1)[-1].upper(), 3600, 10.0, 20.0, 0, 10 ** 9)
        rows[f] = AuditRow(f, True, inv[f].contributor, 3600, status, corr.label if corr.usable else "no_comparator",
                           corr.label, corr, None, kind, "", reason="r0")
    return inv, rows


def test_a_listed_record_is_disputed_with_the_reviewer_reason_never_corrected():
    inv, rows = setup({"wcoco_t-wcoco-usa-sfwmd": UNV, "other-1-usa-sfwmd": UNV})
    counts = ta.apply_reviewed_withhold(rows, [entry()], inv, lambda *_: None)
    r = rows["wcoco_t-wcoco-usa-sfwmd"]
    assert r.status == "residue" and not r.correction.usable and r.comparator_id == ta.REVIEWER_DISPUTED
    assert r.reason == "reviewer_evidence: leads 3 NOAA stations by 234-409 min"
    ta.annotate(r, 0)
    assert r.annotation == "disputed" and r.annotation_code == "disputed:reviewer_evidence"
    assert r.published.label == "utc_instant"
    row = ta.csv_row(r)
    assert row["time_base_code"] == "disputed:reviewer_evidence" and row["disputed_reason"] == "reviewer_evidence"
    assert "reviewed withhold (2026-10-11, gate C)" in row["agency_constants"]
    assert rows["other-1-usa-sfwmd"].status == "unverified" and counts["applied"] == 1


def test_a_disputed_record_is_withheld_with_its_earlier_reason_kept():
    inv, rows = setup({"b-2-x-c2": ("residue", "w", NOT_USABLE)})
    rows["b-2-x-c2"].comparator_id, rows["b-2-x-c2"].reason = "disputed:eot20_whole_hour", "EOT20 -5 h"
    counts = ta.apply_reviewed_withhold(rows, [entry("b-2-x-c2")], inv, lambda *_: None)
    assert rows["b-2-x-c2"].comparator_id == ta.REVIEWER_DISPUTED and rows["b-2-x-c2"].status == "residue"
    assert rows["b-2-x-c2"].reason.endswith("was disputed: EOT20 -5 h") and counts["was_disputed"] == 1


@pytest.mark.parametrize("kind", ["b", "c", "a", "inherit", "w"])
def test_a_listed_record_the_audit_verified_by_any_evidence_stops_the_audit(kind):
    inv, rows = setup({"x-1-usa-noaa": ("used", kind, Correction("utc_instant")), "y-2-usa-usgs": UNV})
    with pytest.raises(ta.WithholdError, match="verified or corrected: x-1-usa-noaa"):
        ta.apply_reviewed_withhold(rows, [entry("y-2-usa-usgs"), entry("x-1-usa-noaa")], inv, lambda *_: None)
    assert rows["y-2-usa-usgs"].status == "unverified"            # nothing applied before the stop


def test_a_strongly_corrected_record_stops_the_audit_too():
    inv, rows = setup({"w-1-deu-wsv": ("used", "a", Correction("utc_mean_start60"))})
    with pytest.raises(ta.WithholdError):
        ta.apply_reviewed_withhold(rows, [entry("w-1-deu-wsv")], inv, lambda *_: None)


def test_an_id_not_in_the_inventory_stops_the_audit():
    inv, rows = setup({"a-1-x-c1": UNV})
    with pytest.raises(ta.WithholdError, match="not in the inventory: typo-1-x-c1"):
        ta.apply_reviewed_withhold(rows, [entry("a-1-x-c1"), entry("typo-1-x-c1")], inv, lambda *_: None)
    with pytest.raises(ta.WithholdError, match="not in the inventory"):
        ta.audit(inv, ["a-1-x-c1"], release_root="/nonexistent", table_path="/nonexistent", gates_dir="/nonexistent",
                 withhold=[entry("typo-1-x-c1")])


def test_a_listed_record_outside_the_run_is_counted_not_an_error():
    inv, rows = setup({"a-1-x-c1": UNV})
    inv["b-2-x-c2"] = RecordInfo("b-2-x-c2", "C2", 3600, 1.0, 2.0, 0, 1)
    counts = ta.apply_reviewed_withhold(rows, [entry("b-2-x-c2")], inv, lambda *_: None)
    assert counts["not_in_run"] == 1 and counts["applied"] == 0


@pytest.mark.parametrize("bad, msg", [({"short": ""}, "lacks short"), ({"evidence": {"reviewer": "r", "report": "p"}},
                                                                         "lacks evidence.numbers"),
                                      ({"date": "11/10/2026"}, "date"), ({"reason": None}, "lacks reason")])
def test_load_withhold_needs_every_field(tmp_path, bad, msg):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"entries": [entry(**bad)]}))
    with pytest.raises(ta.WithholdError, match=msg):
        ta.load_withhold(p)


def test_load_withhold_refuses_a_repeated_id(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"entries": [entry(), entry()]}))
    with pytest.raises(ta.WithholdError, match="listed twice"):
        ta.load_withhold(p)


def test_the_committed_list_is_valid_and_names_the_six_reviewed_records():
    got = ta.load_withhold()
    assert sorted(e["file_id"] for e in got) == sorted([
        "wcoco_t_navd88-wcoco-usa-sfwmd", "calahootchieriver_s79_t-s79_t-usa-sfwmd", "burgassouth-bur-bgr-cmems",
        "crms0590-crms0590-usa-crms", "crms0511-crms0511-usa-crms", "crms0531-crms0531-usa-crms",
        "s197_t-s197-usa-sfwmd", "mbts_navd88-mbts-usa-sfwmd", "mdts-mdts-usa-sfwmd", "kivalina-9491253-usa-noaa"])
    assert not any("san_felipe" in e["file_id"] for e in got)          # an owner decision, not a reviewed withhold


def test_the_audit_applies_the_list_and_stops_on_a_strong_listed_record(monkeypatch):
    from test_timebase_audit import _fake_audit, _info
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(4)}
    inv["u"] = _info("u", "U", 80.0, country=None)                     # no comparator: unverified
    official = {f"p{i}": 0.5 for i in range(4)}
    res = _fake_audit(monkeypatch, inv, official, {}, withhold=[entry("u")])
    assert res.rows["u"].comparator_id == ta.REVIEWER_DISPUTED and res.rows["u"].annotation == "disputed"
    assert res.rows["u"].annotation_code == "disputed:reviewer_evidence"
    assert res.sibling_counts["reviewed_withhold"]["applied"] == 1
    with pytest.raises(ta.WithholdError, match="verified or corrected: p0"):
        _fake_audit(monkeypatch, inv, official, {}, withhold=[entry("p0")])
