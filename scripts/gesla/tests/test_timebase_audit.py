"""G04c: comparators, audit order, provider inheritance and outputs (BP §2 steps 1, 5-7).

Synthetic records (analytic tides) for the rules; the real-record proof (Helgoland, Bremerhaven,
Cuxhaven, Trondheim, the +17 min record, the broken control) is the G04c proof script.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from gesla.read_gesla import otc_work
from gesla.record import GaugeRecord
from gesla.residue import ResidueCase, append, read, residue_path
from gesla.tcd_table import TcdTable
from gesla.timebase_audit import (OFFICIAL_BAND_MIN, AuditRow, Comparator, Outcome, RecordInfo, class_counts,
                                  decide, extrema, inherit, judge, match_offset, month_vote, noaa_id, official_outcome,
                                  predict, residue_cases, siblings, station_status, trailing_span, write_csv,
                                  write_providers, zone_for)
from gesla.timebase_measure import MonthLag
from gesla.timebase_verdict import NOT_USABLE, Correction, Verdict

HOUR = 3600


def unix(y: int, m: int = 1, d: int = 1, hh: int = 0) -> int:
    return int(np.datetime64(f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:00", "s").astype(np.int64))


@pytest.fixture(scope="module")
def table():
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    if not dump.exists():
        pytest.skip(f"TCD dump not in the cache: {dump}")
    return TcdTable.load(dump)


CONSTS = {"M2": (1.0, 30.0), "S2": (0.3, 60.0), "K1": (0.2, 100.0), "O1": (0.15, 200.0), "M4": (0.05, 10.0),
          "M6": (0.02, 20.0)}


def record(name, t, h, interval=60) -> GaugeRecord:
    t = np.asarray(t, dtype=np.int64)
    return GaugeRecord(record_id=name, source="gesla", source_version="test", contributor="X", originator=True,
                       licence_class="x", lat=0.0, lon=0.0, gauge_type="Coastal", sampling="instantaneous",
                       interval_min=interval, declared_time_base=None, member_sha256="0" * 64, times_s=t,
                       heights_m=np.asarray(h, dtype=np.float64), good=np.ones(len(t), dtype=bool))


START, END = unix(2015), unix(2017)
HOURS = np.arange(START, END, HOUR, dtype=np.int64)
SPAN = (START, END)


# --- residue (manifest §2.2) ---------------------------------------------------------------------

def test_residue_case_sha_and_roundtrip(tmp_path):
    c = ResidueCase("rec-1", "timebase", "D5", "unexplained", {"b": 1.5, "a": [1, 2], "z": None})
    assert c.evidence_sha256() == ResidueCase("rec-1", "timebase", "D5", "x",
                                              {"a": [1, 2], "z": None, "b": 1.5}).evidence_sha256()
    import hashlib
    assert c.evidence_sha256() == hashlib.sha256(b'{"a":[1,2],"b":1.5,"z":null}').hexdigest()
    p = residue_path(tmp_path, "20261008")
    assert p == tmp_path / "builds/20261008/gesla/residue.jsonl"
    assert append(p, [c, c]) == 2
    assert read(p) == [c, c]


def test_residue_case_rejects_bad_evidence():
    with pytest.raises(ValueError):
        ResidueCase("r", "timebase", "D5", "x", {"a": float("nan")})
    with pytest.raises(ValueError):
        ResidueCase("r", "nonsense", "D5", "x", {})
    with pytest.raises(ValueError):
        ResidueCase("r", "timebase", "D5", "x", {"a": object()})


# --- inputs --------------------------------------------------------------------------------------

def test_siblings_rule():
    inv = {"a": RecordInfo("a", "WSV", 3600, 54.0, 8.0, unix(2000), unix(2020)),
           "b": RecordInfo("b", "CMEMS", 600, 54.005, 8.009, unix(2010), unix(2026)),
           "c": RecordInfo("c", "WSV", 600, 54.001, 8.001, unix(2000), unix(2020)),       # same contributor
           "d": RecordInfo("d", "BfG", 3600, 54.02, 8.0, unix(2000), unix(2020)),         # 0.02 deg away
           "e": RecordInfo("e", "UHSLC", 3600, 54.0, 8.0, unix(1980), unix(1999))}       # no overlap
    s = siblings(inv)
    assert s["a"] == ["b"] and s["b"] == ["a", "c"] and s["d"] == [] and s["e"] == []


def test_noaa_id_and_zone():
    assert noaa_id("the_battery-8518750-usa-noaa") == "8518750"
    assert noaa_id("boston_ma-741a-usa-uhslc_rq") is None
    assert zone_for("helgolandbinnenhafen-9510070-deu-wsv") == "Europe/Berlin"
    assert zone_for("the_battery-8518750-usa-noaa") is None


# --- (b): extrema matched to official extrema ----------------------------------------------------

def test_match_offset_finds_a_17_minute_shift(table):
    t = np.arange(unix(2026, 11), unix(2027, 1), 60, dtype=np.int64)
    truth_t, truth_k = extrema(t, predict(table, CONSTS, 0.0, t))
    events = [(int(round(x)), int(k)) for x, k in zip(truth_t, truth_k)]
    late_t, late_k = extrema(t, predict(table, CONSTS, 0.0, t - 17 * 60))   # model stamps 17 min late
    m = match_offset(late_t, late_k, events)
    assert m["n_matched"] >= 0.95 * m["n_events"]
    assert m["lag_min"] == pytest.approx(17.0, abs=0.2)


# --- judging: a comparator that cannot test the hypothesis never condemns -----------------------

def _mean60(table, hours):
    grid = np.arange(hours[0], hours[-1] + 2 * HOUR, 60, dtype=np.int64)
    g = predict(table, CONSTS, 0.0, grid)
    i0 = np.searchsorted(grid, hours)
    return np.array([g[i:i + 60].mean() for i in i0])


@pytest.fixture(scope="module")
def mean60_record(table):
    return record("wsv-like", HOURS, _mean60(table, HOURS))


def _comp(table, step_s, kind="a", can_mean60=True):
    t = np.arange(START - 4 * HOUR, END + 4 * HOUR, step_s, dtype=np.int64)
    return Comparator(kind, f"{kind}-{step_s}", (t, predict(table, CONSTS, 0.0, t)), can_mean60=can_mean60)


def test_hourly_comparator_is_inconclusive_not_unexplained(table, mean60_record):
    o = judge(mean60_record, _comp(table, 3600, can_mean60=False), table, SPAN)
    assert o.verdict.verdict == "unexplained" and not o.conclusive, o.verdict.reasons
    assert "cannot test" in o.note


def test_sub_hourly_sibling_establishes_mean60(table, mean60_record):
    o = judge(mean60_record, _comp(table, 600), table, SPAN)
    assert (o.verdict.verdict, o.conclusive) == ("utc_mean_start60", True), o.verdict.reasons


def test_official_lag_is_record_level():
    ev = lambda lag: {"kind": "b", "id": "official:bsh:x", "lag_min": lag}
    o = official_outcome("r", ev(-2.7), SPAN)
    assert (o.verdict.verdict, o.conclusive, o.verdict.passed) == ("utc_instant", True, True)
    o = official_outcome("r", ev(-9.6), SPAN)                    # Cuxhaven: BSH's method offset
    assert o.verdict.verdict == "utc_instant" and o.conclusive
    for lag in (61.0, -52.0):                                    # whole hours: not from a record-level lag
        assert not official_outcome("r", ev(lag), SPAN).conclusive
    for lag in (-33.6, -40.4):                                   # mean60-like: cannot be tested here
        o = official_outcome("r", ev(lag), SPAN)
        assert not o.conclusive and "cannot test" in o.note
    o = official_outcome("r", ev(17.0 + OFFICIAL_BAND_MIN), SPAN)
    assert not o.conclusive and o.verdict.verdict == "unexplained"


def test_shifted_17_minutes_is_conclusively_unexplained_against_a_sibling(table):
    rec = record("plus17", HOURS, predict(table, CONSTS, 0.0, HOURS - 17 * 60))
    o = judge(rec, _comp(table, 600), table, SPAN)
    assert (o.verdict.verdict, o.conclusive) == ("unexplained", True)


def _ml(lags, y0=2010):
    return [MonthLag(y0 + i // 12, i % 12 + 1, lag, 0.99, 600, 600) for i, lag in enumerate(lags)]


def test_trailing_span_after_the_last_run_of_adjacent_off_months():
    span = (unix(2010), unix(2014))
    lags = [-30.0] * 48
    lags[5] = lags[6] = -44.0                    # 2010-06/07: a run
    lags[20] = -40.0                             # 2011-09: isolated
    assert trailing_span(_ml(lags), span) == (unix(2010, 8), unix(2014))
    assert trailing_span(_ml([-30.0] * 48), span) is None
    lags = [-30.0] * 48
    lags[40] = lags[41] = -44.0                  # the run leaves only 6 months after it
    assert trailing_span(_ml(lags), span) is None


# --- decide: audit order and disagreement --------------------------------------------------------

def _v(label, passed=True, base=None, median=0.0):
    return Verdict("r", "c", label, base or label, Correction(base or label) if passed else NOT_USABLE, passed,
                   evidence={"lags": {"median": median}})


def _o(kind, cid, label, conclusive=True, passed=True, base=None):
    return Outcome(kind, cid, _v(label, passed, base), conclusive)


def test_decide_hourly_sibling_does_not_condemn():
    got, _ = decide([_o("a", "bfg", "unexplained", conclusive=False, passed=False),
                     _o("a", "cmems", "utc_mean_start60")], final=True)
    assert got.verdict.verdict == "utc_mean_start60"


def test_decide_passing_sibling_beats_a_failing_one():
    got, _ = decide([_o("a", "x", "unexplained", passed=False), _o("a", "y", "utc_instant")], final=True)
    assert got.verdict.verdict == "utc_instant"


def test_decide_official_beats_sibling():
    got, why = decide([_o("a", "sib", "utc_plus_1", base="utc_plus_n"), _o("b", "bsh", "utc_instant")], final=False)
    assert got.verdict.verdict == "utc_instant" and "official beats" in why


def test_decide_same_class_sibling_refines_the_official_verdict():
    sib = _o("a", "sib", "months_bad:2024-07", base="utc_instant")
    sib.verdict.months_bad = ["2024-07"]
    clean = _o("a", "sib2", "utc_instant")
    got, why = decide([_o("b", "bsh", "utc_instant"), sib], final=True)
    assert got.verdict.verdict == "months_bad:2024-07" and "months audited" in why
    got, _ = decide([_o("b", "bsh", "utc_instant"), sib, clean], final=True)
    assert got.verdict.verdict == "utc_instant" and got.comparator_id == "sib2"


def _vo(cid, monthly):
    o = _o("a", cid, "utc_instant")
    o.monthly, o.audit_span = monthly, SPAN
    return o


def test_month_vote_blames_the_comparator_not_the_record():
    months = {f"20{y}-{m:02d}": 0.0 for y in (23, 24, 25) for m in range(1, 13)}
    fd_vs_cmems = _vo("cmems", {**months, "2024-07": 50.0, "2024-08": 49.0})
    # The record (CMEMS) is audited against FD (off in 2024-07/08) and two clean siblings: not its fault.
    got, why = month_vote([fd_vs_cmems], [fd_vs_cmems, _vo("bfg", months), _vo("rq", months)])
    assert got.verdict.verdict == "utc_instant" and "3 comparators" in why
    # The record (FD) is off against two of three comparators in 2024-07/08: a run -> the trailing span.
    got, why = month_vote([fd_vs_cmems], [fd_vs_cmems, _vo("wsv", {**months, "2024-07": 50.0, "2024-08": 49.0}),
                                          _vo("rq", months)])
    assert got.verdict.passed and got.verdict.span[0] == unix(2024, 9) and "span limited" in why
    # An isolated month off against both comparators is dropped.
    one = {**months, "2023-05": 9.0}
    got, _ = month_vote([_vo("a1", one)], [_vo("a1", one), _vo("a2", one)])
    assert got.verdict.verdict == "months_bad:2023-05" and got.verdict.correction.months_drop == ((2023, 5),)


def test_decide_official_class_fails_the_monthly_pass_rule():
    got, why = decide([_o("b", "bsh", "utc_instant"), _o("a", "sib", "unexplained", passed=False)], final=True)
    assert got.verdict.verdict == "unexplained" and "pass rule fails" in why


def test_decide_siblings_disagree_goes_to_residue():
    got, why = decide([_o("a", "x", "utc_instant"), _o("a", "y", "utc_plus_1", base="utc_plus_n")], final=False)
    assert got.verdict.verdict == "unexplained" and "disagree" in why


def test_decide_waits_for_later_rounds_and_ends_unexplained():
    outs = [_o("b", "bsh", "unexplained", conclusive=False, passed=False)]
    assert decide(outs, final=False) == (None, "")
    got, why = decide(outs, final=True)
    assert got.verdict.verdict == "unexplained" and "no comparator could test" in why


# --- provider inheritance (rule 5) ----------------------------------------------------------------

def _row(fid, verdict, contributor="NHS", interval=3600, kind="b", status="used"):
    corr = Correction("utc_instant") if status == "used" else NOT_USABLE
    return AuditRow(fid, True, contributor, interval, status, verdict, verdict, corr, 0, kind, "x")


def _inv(n, contributor="NHS", interval=3600, start=2000, end=2020, country="nor"):
    return {f"r{i}": RecordInfo(f"r{i}", contributor, interval, 60.0 + i, 5.0, unix(start), unix(end), country)
            for i in range(n)}


def test_inherit_with_three_agreeing_records():
    inv = _inv(5)
    rows = {f"r{i}": _row(f"r{i}", "utc_instant") for i in range(3)}
    prov = inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["r3"].comparator_kind == "inherit" and rows["r3"].usable and rows["r3"].verdict == "utc_instant"
    assert prov[0]["n_inherited"] == 2 and prov[0]["provider_verdict"] == "utc_instant"


def test_inherit_refused_with_two_records_or_disagreement_or_no_overlap():
    inv = _inv(4)
    rows = {f"r{i}": _row(f"r{i}", "utc_instant") for i in range(2)}
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["r2"].status == "unverified" and rows["r2"].verdict == "no_comparator"

    inv = _inv(5)
    rows = {f"r{i}": _row(f"r{i}", "utc_instant") for i in range(3)}
    rows["r3"] = _row("r3", "unexplained", status="residue")
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["r4"].status == "unverified"

    inv = _inv(4)
    inv["late"] = RecordInfo("late", "NHS", 3600, 70.0, 5.0, unix(2021), unix(2025), "nor")
    rows = {f"r{i}": _row(f"r{i}", "utc_instant") for i in range(3)}
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["r3"].usable and rows["late"].status == "unverified"


def test_inherit_needs_same_interval():
    inv = _inv(3)
    inv["hourly"] = RecordInfo("hourly", "NHS", 600, 70.0, 5.0, unix(2000), unix(2020), "nor")
    rows = {f"r{i}": _row(f"r{i}", "utc_instant") for i in range(3)}
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["hourly"].status == "unverified"


# --- residue rows and outputs ---------------------------------------------------------------------

def test_unexplained_and_changed_provider_go_to_residue(tmp_path):
    inv = _inv(4, contributor="WSV")
    rows = {f"r{i}": _row(f"r{i}", "utc_mean_start60", contributor="WSV") for i in range(3)}
    rows["r3"] = _row("r3", "unexplained", contributor="WSV", status="residue")
    prov = inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    res = residue_cases(rows, prov, {("WSV", 3600): "local_dst:Europe/Berlin"}, version="4.1")
    subj = {(c.subject_id, c.case, c.rule) for c in res}
    assert ("r3", "timebase", "D5") in subj
    assert ("provider:WSV:4.1:3600", "timebase", "D5") in subj
    prov_case = [c for c in res if c.subject_id.startswith("provider:")][0]
    assert prov_case.evidence["previous"] == "local_dst:Europe/Berlin" and prov_case.evidence["n_support"] == 3
    same = residue_cases(rows, prov, {("WSV", 3600): "utc_mean_start60"}, version="4.1")
    assert [c.subject_id for c in same] == ["r3"]
    write_csv(rows, tmp_path / "timebase.csv")
    write_providers(prov, tmp_path / "p.csv")
    lines = (tmp_path / "timebase.csv").read_text().splitlines()
    assert lines[0].startswith("file_id,candidate,") and len(lines) == 5


def test_station_without_a_usable_record_is_unverified_not_dropped():
    rows = {"good": _row("good", "utc_instant"), "bad": _row("bad", "unexplained", status="residue")}
    st = station_status([("T1", "good"), ("T1", "bad"), ("T2", "bad"), ("T3", "missing")], rows)
    assert st == {"T1": "audited", "T2": "unverified", "T3": "unverified"}
    assert class_counts(rows) == {"unexplained": 1, "utc_instant": 1}


# --- G04c review fixes ------------------------------------------------------------------------------

import gesla.timebase_audit as ta  # noqa: E402


def test_month_vote_scattered_off_months_over_ten_percent_is_unexplained_not_a_crash():
    months = {f"20{y}-{m:02d}": 0.0 for y in (23, 24) for m in range(1, 13)}
    off = {**months, "2023-01": 9.0, "2023-06": 9.0, "2023-11": 9.0, "2024-04": 9.0}   # 4 of 24, none adjacent
    got, why = month_vote([_vo("a1", off)], [_vo("a1", off), _vo("a2", off)])
    assert got.verdict.verdict == "unexplained" and not got.verdict.passed and "not isolated" in why
    got, _ = decide([_o("b", "bsh", "utc_instant"), _vo("a1", off), _vo("a2", off)], final=True)
    assert got.verdict.verdict == "unexplained"


def test_month_vote_ignores_siblings_that_passed_with_another_class():
    months = {f"20{y}-{m:02d}": 0.0 for y in (23, 24, 25) for m in range(1, 13)}
    hour = {k: 60.0 for k in months}
    plus = []
    for cid in ("p1", "p2"):
        o = _o("a", cid, "utc_plus_1", base="utc_plus_n")
        o.verdict.correction = Correction("utc_plus_n", n=1)
        o.monthly, o.audit_span = hour, SPAN
        plus.append(o)
    best = _vo("a0", months)
    got, _ = month_vote([best], [best] + plus)
    assert got.verdict.verdict == "utc_instant" and got.verdict.passed


# fake audit: synthetic inventory, official and sibling tasks without data
SCATTERED_OFF = {**{f"20{y}-{m:02d}": 0.0 for y in (23, 24) for m in range(1, 13)},
                 "2023-01": 9.0, "2023-06": 9.0, "2023-11": 9.0, "2024-04": 9.0}     # 4 of 24, not isolated


def _fake_audit(monkeypatch, inv, official, sibling, **kw):
    """official: rid -> lag (float), a Correction (a conclusive verdict of that class), "raise" or "nolag"
    (tried, no lag); sibling: (rid, sid) -> "pass" | "fail" | "raise", where
    "pass+1" / "fail+1" have the base utc_plus_n (utc_plus_1), a "?" makes it inconclusive, and a
    trailing "!" gives it SCATTERED_OFF monthly lags."""
    by_pos = {(i.lat, i.lon): f for f, i in inv.items()}

    class Refs:
        def __init__(self, *_):
            pass

        def counts(self):
            return {}

        def harcon(self, _f):
            return None

        def official(self, lat, lon, file_id=None):
            f = by_pos.get((lat, lon))
            return object() if f in official else None

    def off_task(rid, lat, lon):
        spec = official[rid]
        if spec == "raise":
            raise RuntimeError("boom official")
        if isinstance(spec, Correction):                # a conclusive record-level verdict of that class
            v = Verdict(rid, f"official:{rid}", spec.label, spec.label, spec, True, span=SPAN,
                        residual={"n_months": 0, "max_abs": 0.0, "off": {}}, evidence={"lags": {"median": 0.0}})
            return rid, [Outcome("c", f"official:{rid}", v, True, "", {})], [], {"span": SPAN}
        if spec == "nolag":                             # a comparator was tried but gave no lag
            return rid, [], ["official:x:nolag: too few extrema"], {"span": SPAN, "tried": ["official:x:nolag"]}
        return rid, [official_outcome(rid, {"kind": "b", "id": f"official:{rid}", "lag_min": spec}, SPAN)], [], \
            {"span": SPAN}

    def sib_task(rid, sibs):
        out = []
        for sid, _row in sibs:
            spec = sibling[(rid, sid)]
            if spec == "raise":
                raise RuntimeError(f"boom {rid} vs {sid}")
            passed = spec.startswith("pass")
            corr = Correction("utc_plus_n", n=1) if "+1" in spec else Correction("utc_instant")
            v = Verdict(rid, f"{sid}~corrected", corr.label if passed else "unexplained",
                        corr.kind, corr if passed else NOT_USABLE, passed, span=SPAN,
                        evidence={"lags": {"median": 0.0 if passed else 9.0}},
                        reasons=[] if passed else ["pass rule: residual outside"])
            monthly = dict(SCATTERED_OFF) if spec.endswith("!") else {}
            out.append(Outcome("a", f"{sid}~corrected", v, "?" not in spec, "", {}, monthly, SPAN))
        return rid, out, [], {"span": SPAN}

    off_task.__name__, sib_task.__name__ = "_official_task", "_sibling_task"
    monkeypatch.setattr(ta, "_init", lambda *a: None)
    monkeypatch.setattr(ta, "References", Refs)
    monkeypatch.setattr(ta, "_official_task", ta._safe(off_task))
    monkeypatch.setattr(ta, "_sibling_task", ta._safe(sib_task))
    return ta.audit(inv, sorted(inv), release_root="r", table_path="t", gates_dir="g", workers=1, **kw)


def _info(fid, contributor, lat, lon=5.0, country="nld"):
    return RecordInfo(fid, contributor, 3600, lat, lon, unix(2000), unix(2020), country)


def test_audit_crashed_task_never_inherits_and_its_note_survives(monkeypatch):
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(4)}
    inv["f"], inv["s"] = _info("f", "F", 50.0), _info("s", "S", 50.001)
    official = {"p0": 0.5, "p1": 0.5, "p2": 0.5, "p3": "raise", "f": 0.5, "s": 0.5}
    sibling = {("f", "s"): "raise", ("s", "f"): "pass"}
    res = _fake_audit(monkeypatch, inv, official, sibling)
    p3 = res.rows["p3"]
    assert not p3.usable and p3.comparator_kind != "inherit" and "boom official" in p3.reason
    f = res.rows["f"]                                       # crashed in the round-0 cross-check
    assert not f.usable and "boom f vs s" in f.reason
    s = res.rows["s"]                                       # its verdict came from f: re-judged on the official
    assert s.usable and s.comparator_id == "official:s"


def test_audit_rejudges_a_record_verdicted_from_a_sibling_that_became_unexplained(monkeypatch):
    inv = {"a": _info("a", "A", 50.0), "b": _info("b", "B", 50.001)}
    res = _fake_audit(monkeypatch, inv, {"a": 0.5, "b": 0.5}, {("a", "b"): "pass", ("b", "a"): "fail"})
    assert res.rows["b"].status == "residue"
    a = res.rows["a"]
    assert a.usable and a.comparator_id == "official:a" and "b~corrected" not in a.comparator_id
    assert res.rounds == 0                                  # no sibling round ran


def test_audit_counts_sibling_refined_official_support(monkeypatch):
    inv = {"x": _info("x", "C", 50.0), "y": _info("y", "D", 50.001)}
    res = _fake_audit(monkeypatch, inv, {"x": 0.5, "y": 0.5}, {("x", "y"): "pass", ("y", "x"): "pass"},
                      previous_providers={("C", 3600): "utc_plus_1"})
    assert res.rows["x"].comparator_kind == "a"
    case = [c for c in res.residue if c.subject_id.startswith("provider:C")][0]
    assert case.evidence["n_support_official"] == 1


def test_audit_names_ids_missing_from_the_inventory(monkeypatch):
    monkeypatch.setattr(ta, "_init", lambda *a: None)
    inv = {"a": _info("a", "A", 50.0)}
    with pytest.raises(ValueError, match="inventory.*ghost"):
        ta.audit(inv, ["a", "ghost"], release_root="r", table_path="t", gates_dir="g")
    with pytest.raises(ValueError, match="inventory.*nosrc"):
        ta.audit(inv, ["a"], release_root="r", table_path="t", gates_dir="g", transforms={"a~x": ("nosrc", 60)})


def test_inherit_overlap_uses_the_audited_spans_not_their_bounds():
    inv = {"r0": RecordInfo("r0", "NHS", 3600, 60.0, 5.0, unix(2000), unix(2005), "nor"),
           "r1": RecordInfo("r1", "NHS", 3600, 61.0, 5.0, unix(2000), unix(2005), "nor"),
           "r2": RecordInfo("r2", "NHS", 3600, 62.0, 5.0, unix(2015), unix(2020), "nor"),
           "gap": RecordInfo("gap", "NHS", 3600, 63.0, 5.0, unix(2008), unix(2012), "nor")}
    rows = {f: _row(f, "utc_instant") for f in ("r0", "r1", "r2")}
    for f in rows:
        rows[f].span = (inv[f].start_s, inv[f].end_s)
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["gap"].status == "unverified" and not rows["gap"].usable


def test_harcon_failure_falls_back_to_the_official_extrema(monkeypatch):
    rec = record("x-123-usa-noaa", HOURS, np.zeros(len(HOURS)))

    class Refs:
        def harcon(self, _f):
            return Path("harcon_123.json")

        def official(self, lat, lon, file_id=None):
            return ta.OfficialRef("noaa", "123", 0.1, "p")

    monkeypatch.setitem(ta._W, "table", None)
    monkeypatch.setitem(ta._W, "refs", Refs())
    monkeypatch.setitem(ta._W, "transforms", {})
    monkeypatch.setitem(ta._W, "cache", {rec.record_id: rec})
    monkeypatch.setattr(ta, "harcon_lag", lambda *a: "harcon harcon_123.json: no M2, S2, K1 or O1")
    monkeypatch.setattr(ta, "official_lag", lambda *a: {"kind": "b", "id": "official:noaa:123", "lag_min": 0.4,
                                                       "n_events": 100, "n_matched": 99})
    rid, outs, notes, _ = ta._official_task(rec.record_id, 0.0, 0.0)
    assert [o.comparator_id for o in outs] == ["official:noaa:123"]
    assert any("no M2" in n for n in notes)


def test_step_amplitude_factor_weights_a_mean60_part():
    from gesla.timebase_verdict import boxcar_response
    mid = (START + END) // 2
    step = Correction("step", first=Correction("utc_mean_start60"), parts=((mid, Correction("utc_instant")),))
    with pytest.raises(ValueError):
        step.amplitude_factor(28.9841042)
    r = boxcar_response(28.9841042)
    assert step.amplitude_factor(28.9841042, span=SPAN) == pytest.approx(1 / (0.5 * r + 0.5), rel=1e-3)
    same = Correction("step", first=Correction("utc_instant"), parts=((mid, Correction("utc_plus_n", n=1)),))
    assert same.amplitude_factor(28.9841042) == 1.0


def test_months_drop_applies_to_utc_months_after_the_shift():
    t = np.array([unix(2015, 2, 1, 0) + 1800, unix(2015, 3, 1, 0) + 1800], dtype=np.int64)
    # recorded UTC+1: the first stamp is 2015-01-31 23:30 UTC, the second 2015-02-28 23:30 UTC
    tt, _ = Correction("utc_plus_n", n=1, months_drop=((2015, 2),))(t, np.zeros(2))
    assert list(tt) == [unix(2015, 2, 1, 0) + 1800 - HOUR]


def test_local_dst_repeated_autumn_hour_second_copy_is_standard_time():
    wall = np.arange(unix(2015, 10, 25, 1), unix(2015, 10, 25, 4), 600, dtype=np.int64)
    rep = wall[(wall >= unix(2015, 10, 25, 2)) & (wall < unix(2015, 10, 25, 3))]
    t = np.concatenate([wall[wall < unix(2015, 10, 25, 3)], rep, wall[wall >= unix(2015, 10, 25, 3)]])
    tt, _ = Correction("local_dst", zone="Europe/Berlin")(t, np.zeros(len(t)))
    assert len(tt) == len(t) and np.all(np.diff(tt) == 600)
    assert tt[0] == unix(2015, 10, 24, 23) and tt[-1] == unix(2015, 10, 25, 2) + 3000


def test_iso_s_keeps_an_explicit_offset():
    assert ta._iso_s("2020-01-01T02:00:00+02:00") == unix(2020)
    assert ta._iso_s("2020-01-01") == unix(2020)


def test_load_events_naive_stamp_is_utc_not_machine_local(tmp_path, monkeypatch):
    import time
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"events": [["2026-11-15T06:00:00", "H", 1.0], ["2026-11-15T12:00:00Z", "L", 0.1]]}))
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        ev = ta.load_events(p)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
    assert ev == [(unix(2026, 11, 15, 6), 1), (unix(2026, 11, 15, 12), -1)]


def test_load_inventory_missing_interval_is_an_error(tmp_path):
    p = tmp_path / "inventory.csv"
    p.write_text("file_id,contributor,interval_s,lat,lon,start,end\nrec-x,X,,1.0,2.0,2000-01-01,2001-01-01\n")
    with pytest.raises(ValueError, match="rec-x"):
        ta.load_inventory(p)


def _gates(tmp_path, stations, near, events=()):
    (tmp_path / "evid/testplan").mkdir(parents=True)
    (tmp_path / "evid/testplan/harmonics_stations.json").write_text(json.dumps(stations))
    (tmp_path / "evid/testplan/nearest_ref.json").write_text(json.dumps(near))
    for src, ref in events:
        (tmp_path / "refs" / src / "events").mkdir(parents=True, exist_ok=True)
        (tmp_path / "refs" / src / "events" / f"{ref}.json").write_text("{}")
    return tmp_path


def test_references_bad_cache_entries_name_the_file_and_station(tmp_path):
    g = _gates(tmp_path / "a", [{"id": "S1", "lat": 1.0, "lon": 2.0}], {"S1": {"ref": "x", "dist_km": 0.1}})
    with pytest.raises(ValueError, match="nearest_ref.json.*S1"):
        ta.References(g)
    g = _gates(tmp_path / "b", [{"id": "S2", "lat": 1.0, "lon": None}],
               {"S2": {"src": "bsh", "ref": "x", "dist_km": 0.1}}, [("bsh", "x")])
    with pytest.raises(ValueError, match="S2"):
        ta.References(g)


def test_references_prefilter_is_a_distance_at_high_latitude_and_across_180(tmp_path):
    lat = 78.9
    dlon = 1.8 / (111.195 * np.cos(np.radians(lat)))          # 1.8 km east-west, > 0.08 deg here
    g = _gates(tmp_path / "a", [{"id": "NYA", "lat": lat, "lon": 11.95}, {"id": "DL", "lat": 0.0, "lon": 179.995}],
               {"NYA": {"src": "kartverket", "ref": "nya", "dist_km": 0.0},
                "DL": {"src": "linz", "ref": "dl", "dist_km": 0.0}}, [("kartverket", "nya"), ("linz", "dl")])
    refs = ta.References(g, noaa_harcon_dir=_empty_ref_caches(tmp_path)[0] / "harcon", dmi_dir=tmp_path / "dmi-events")
    assert dlon > 0.08 and refs.official(lat, 11.95 - dlon).ref == "nya"
    assert refs.official(0.0, -179.995).ref == "dl"
    assert refs.official(lat, 11.95 - 3 * dlon) is None


def test_residue_rerun_replaces_the_cases_rows(tmp_path):
    from gesla.residue import replace_case
    p = tmp_path / "residue.jsonl"
    other = ResidueCase("st-1", "k5", "K5", "other producer", {})
    append(p, [other])
    tb = [ResidueCase("rec-1", "timebase", "D5", "unexplained", {"a": 1})]
    assert replace_case(p, "timebase", tb) == 1
    assert replace_case(p, "timebase", tb) == 1
    assert read(p) == [other] + tb


def test_residue_from_json_requires_the_evidence_sha():
    line = json.dumps({"subject_id": "r", "case": "timebase", "rule": "D5", "reason": "x", "evidence": {}})
    with pytest.raises(ValueError, match="evidence_sha256"):
        ResidueCase.from_json(line)


def test_main_only_without_values_is_an_error(monkeypatch):
    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop
    monkeypatch.setattr(ta, "GeslaRelease", stop)
    monkeypatch.setattr(ta.TcdTable, "load", stop)
    with pytest.raises(SystemExit):
        ta.main(["--only"])


def test_audit_mutual_failure_stays_residue(monkeypatch):
    # Each fails the pass rule against the other: a disagreement, not a withdrawn supporter.
    inv = {"a": _info("a", "A", 50.0), "b": _info("b", "B", 50.001)}
    res = _fake_audit(monkeypatch, inv, {"a": 0.5, "b": 0.5}, {("a", "b"): "fail", ("b", "a"): "fail"})
    assert res.rows["a"].status == "residue" and res.rows["b"].status == "residue"


def test_a_crashed_task_does_not_cost_its_provider_group_the_inheritance(monkeypatch):
    # A crash is no evidence about the provider: Wemeldinge RWS / RWS_HIST crashed in the round-0
    # cross-check (436bd10) and 13 RWS records lost their inherited verdict. The crashed record
    # stays residue (its verdict is unknown); its group still inherits from the records it judged.
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(6)}
    official = {"p0": 0.5, "p1": 0.5, "p2": 0.5, "p3": "raise"}
    res = _fake_audit(monkeypatch, inv, official, {})
    p3 = res.rows["p3"]
    assert p3.status == "residue" and not p3.usable and "boom official" in p3.reason
    for f in ("p4", "p5"):
        assert res.rows[f].comparator_kind == "inherit" and res.rows[f].usable, res.rows[f].reason
    prov = res.providers[0]
    assert prov["inheritable"] and prov["n_inherited"] == 2 and prov["n_residue"] == 1
    # a judged unexplained record still breaks the group (an `unexplained` verdict is evidence)
    official["p3"] = 40.0
    res = _fake_audit(monkeypatch, inv, official, {})
    assert res.rows["p3"].status == "residue" and res.rows["p4"].status == "unverified"


# --- G04c round 2: inheritance takes its evidence from judged records only ----------------------------

def test_inherited_correction_comes_from_a_judged_record_not_a_crashed_one(monkeypatch):
    # p0 (the first audited member) crashed: its row has no correction. The inherited rows take
    # the correction of a judged, usable record, so they are usable.
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(6)}
    res = _fake_audit(monkeypatch, inv, {"p0": "raise", "p1": 0.5, "p2": 0.5, "p3": 0.5}, {})
    assert res.rows["p0"].task_failed and not res.rows["p0"].usable
    for f in ("p4", "p5"):
        r = res.rows[f]
        assert r.comparator_kind == "inherit" and r.verdict == "utc_instant", r.reason
        assert r.correction.kind == "utc_instant" and r.usable
    assert res.providers[0]["n_inherited"] == 2


def test_inherited_local_dst_keeps_the_zone_of_a_judged_record():
    inv = _inv(5)
    berlin = Correction("local_dst", zone="Europe/Berlin")
    rows = {"r0": AuditRow("r0", True, "NHS", 3600, "residue", "unexplained", "unexplained", NOT_USABLE, 0,
                           "none", "", task_failed=True)}
    for i in (1, 2, 3):
        rows[f"r{i}"] = AuditRow(f"r{i}", True, "NHS", 3600, "used", berlin.label, berlin.label, berlin, 0, "b", "x")
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["r4"].usable and rows["r4"].correction == berlin


def test_an_inconclusive_official_lag_and_a_crash_leave_the_verdict_unknown(monkeypatch):
    # p3's official lag (40 min) matches no class an official comparator can test: inconclusive, so
    # a passing sibling would resolve it. Its round-1 comparison with q (which would pass) crashes:
    # p3's verdict is unknown (task_failed), so it neither breaks P nor gets a later sibling task.
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(6)}
    inv["q"], inv["r"] = _info("q", "Q", 40.001), _info("r", "R", 40.002)
    official = {"p0": 0.5, "p1": 0.5, "p2": 0.5, "p3": 40.0, "q": 0.5}
    sibling = {("p3", "q"): "raise", ("q", "p3"): "pass", ("r", "q"): "pass", ("q", "r"): "pass",
               ("p3", "r"): "pass"}
    res = _fake_audit(monkeypatch, inv, official, sibling)
    p3 = res.rows["p3"]
    assert p3.task_failed and not p3.usable and "boom p3 vs q" in p3.reason and "r~corrected" not in p3.tried
    assert p3.round == 1, p3                                # settled when its round-1 task crashed
    assert res.rows["r"].usable
    for f in ("p4", "p5"):
        assert res.rows[f].comparator_kind == "inherit" and res.rows[f].usable, res.rows[f].reason
    assert res.providers[0]["inheritable"]


def test_a_crash_after_passing_outcomes_stays_a_failed_row(monkeypatch):
    # The crashed comparison could have failed the pass rule: a crash never yields a used row.
    inv = {"f": _info("f", "F", 50.0), "s": _info("s", "S", 50.001)}
    res = _fake_audit(monkeypatch, inv, {"f": 0.5, "s": 0.5}, {("f", "s"): "raise", ("s", "f"): "pass"})
    assert res.rows["f"].task_failed and not res.rows["f"].usable


def _crashed(fid, contributor="NHS", interval=3600):
    return AuditRow(fid, True, contributor, interval, "residue", "unexplained", "unexplained", NOT_USABLE, 0,
                    "none", "", task_failed=True)


def test_two_judged_and_one_crashed_record_are_not_inheritable():
    inv = _inv(5)
    rows = {"r0": _row("r0", "utc_instant"), "r1": _row("r1", "utc_instant"), "r2": _crashed("r2")}
    prov = inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert not prov[0]["inheritable"] and prov[0]["n_audited"] == 3
    assert rows["r3"].status == "unverified" and rows["r4"].status == "unverified"


def test_inherit_overlap_uses_judged_spans_only():
    inv = {f"r{i}": RecordInfo(f"r{i}", "NHS", 3600, 60.0 + i, 5.0, unix(2000), unix(2005), "nor") for i in range(3)}
    inv["dead"] = RecordInfo("dead", "NHS", 3600, 70.0, 5.0, unix(2015), unix(2020), "nor")     # crashed
    inv["late"] = RecordInfo("late", "NHS", 3600, 71.0, 5.0, unix(2016), unix(2019), "nor")     # overlaps only it
    rows = {f"r{i}": _row(f"r{i}", "utc_instant") for i in range(3)}
    rows["dead"] = _crashed("dead")
    prov = inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert prov[0]["inheritable"] and rows["late"].status == "unverified" and not rows["late"].usable


def test_progress_passes_every_item_through_in_order_and_logs_the_final_count():
    lines = []
    got = list(ta._progress(iter(range(7)), 7, "round 9", lines.append, every=3, secs=1e9))
    assert got == list(range(7))
    assert [x.split(" records")[0] for x in lines] == ["round 9: 3/7", "round 9: 6/7", "round 9: 7/7"]


def test_progress_log_flushes(monkeypatch):
    calls = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: calls.append((a, k)))
    ta._log("round 0: 1/1 records done")
    assert calls == [(("round 0: 1/1 records done",), {"flush": True})]


# --- G04c round 3: a crash that could have rescued a record leaves its verdict unknown ----------------

def test_a_provisional_sibling_failure_is_not_final_when_the_rescuing_comparison_crashes(monkeypatch):
    # p3 has no official result. It fails against q in round 1 (provisional: a later sibling could
    # still pass it); its round-2 comparison with r, which would pass, crashes. p3's verdict is
    # unknown, so P (three judged utc_instant records) still inherits.
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(6)}
    inv["q"], inv["r"] = _info("q", "Q", 40.001), _info("r", "R", 40.002)
    official = {"p0": 0.5, "p1": 0.5, "p2": 0.5, "q": 0.5}
    sibling = {("p3", "q"): "fail", ("q", "p3"): "pass", ("r", "q"): "pass", ("q", "r"): "pass",
               ("p3", "r"): "raise", ("r", "p3"): "pass"}
    res = _fake_audit(monkeypatch, inv, official, sibling)
    assert res.rounds == 2 and res.rows["r"].usable and res.rows["r"].round == 1
    p3 = res.rows["p3"]
    assert p3.task_failed and not p3.usable and "boom p3 vs r" in p3.reason
    for f in ("p4", "p5"):
        assert res.rows[f].comparator_kind == "inherit" and res.rows[f].usable, res.rows[f].reason
    assert res.providers[0]["inheritable"]


def test_a_crash_in_the_final_cross_check_that_could_rescue_leaves_the_verdict_unknown(monkeypatch):
    # f's official verdict passes; it fails against s in round 0. In the final cross-check its
    # comparison with t (which would pass and rescue it) crashes: f is unknown, not unexplained.
    def run(ft):
        inv = {"f": _info("f", "P", 50.0), "s": _info("s", "S", 50.001), "t": _info("t", "T", 50.002)}
        for i in range(1, 5):
            inv[f"p{i}"] = _info(f"p{i}", "P", 10.0 * i)
        official = {"f": 0.5, "s": 0.5, "p1": 0.5, "p2": 0.5, "p3": 0.5}
        sibling = {("f", "s"): "fail", ("s", "f"): "pass", ("t", "s"): "pass", ("f", "t"): ft,
                   ("s", "t"): "pass", ("t", "f"): "pass"}
        return _fake_audit(monkeypatch, inv, official, sibling)

    res = run("pass")                                       # control: t rescues f
    assert res.rows["f"].usable and res.rows["p4"].usable
    res = run("raise")
    f = res.rows["f"]
    assert f.task_failed and not f.usable and "boom f vs t" in f.reason
    assert res.rows["p4"].comparator_kind == "inherit" and res.rows["p4"].usable, res.rows["p4"].reason


@pytest.mark.parametrize("p3_official", [None, 40.0])
def test_a_crashed_record_whose_passing_siblings_disagree_stays_unexplained(monkeypatch, p3_official):
    # p3 has no conclusive official result (none, or an inconclusive 40 min lag) and passes against
    # q as utc_instant and against r as utc_plus_1: the siblings disagree, and no further passing
    # sibling can remove a class from that set. So its final cross-check crash (against t) leaves
    # it unexplained, and P does not inherit.
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(6)}
    inv["q"], inv["r"], inv["t"] = _info("q", "Q", 40.001), _info("r", "R", 40.002), _info("t", "T", 40.003)
    official = {"p0": 0.5, "p1": 0.5, "p2": 0.5, "q": 0.5, "r": 0.5}
    if p3_official is not None:
        official["p3"] = p3_official
    sibling = {("p3", "q"): "pass", ("p3", "r"): "pass+1", ("p3", "t"): "pass",
               ("q", "r"): "pass", ("r", "q"): "pass", ("t", "q"): "pass", ("t", "r"): "fail",
               ("q", "t"): "pass", ("r", "t"): "pass"}
    res = _fake_audit(monkeypatch, inv, official, sibling)       # control: t passes it too, still disagree
    assert res.rows["p3"].verdict == "unexplained" and not res.providers[0]["inheritable"]
    sibling[("p3", "t")] = "raise"
    res = _fake_audit(monkeypatch, inv, official, sibling)
    assert not any("KeyError" in r.reason for r in res.rows.values())
    assert res.rows["t"].usable and res.rows["t"].round == 1
    p3 = res.rows["p3"]
    assert p3.status == "residue" and not p3.usable and not p3.task_failed, p3
    assert "siblings disagree" in p3.reason and "boom p3 vs t" in p3.reason
    for f in ("p4", "p5"):
        assert res.rows[f].status == "unverified" and not res.rows[f].usable, res.rows[f].reason
    assert not res.providers[0]["inheritable"]


@pytest.mark.parametrize("p3_official, p3_vs_r", [(None, "pass"), (0.5, "pass+1"), (None, "fail+1"),
                                                  (None, "pass+1?")])
def test_a_crashed_record_a_pass_could_still_rescue_is_a_failed_row(monkeypatch, p3_official, p3_vs_r):
    # Same layout, but p3's passing siblings agree (one base), or p3 has a conclusive official class
    # (a same-class sibling pass refines it), or the other base comes from a failed (fail+1) or an
    # inconclusive (pass+1?) comparison, which is no disagreement: the crashed comparison against t
    # could have changed its verdict, so it is unknown, never used, and P still inherits from its
    # three judged records.
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(6)}
    inv["q"], inv["r"], inv["t"] = _info("q", "Q", 40.001), _info("r", "R", 40.002), _info("t", "T", 40.003)
    official = {"p0": 0.5, "p1": 0.5, "p2": 0.5, "q": 0.5, "r": 0.5}
    if p3_official is not None:
        official["p3"] = p3_official
    sibling = {("p3", "q"): "pass", ("p3", "r"): p3_vs_r, ("p3", "t"): "raise",
               ("q", "r"): "pass", ("r", "q"): "pass", ("t", "q"): "pass", ("t", "r"): "fail",
               ("q", "t"): "pass", ("r", "t"): "pass", ("q", "p3"): "pass", ("r", "p3"): "pass",
               ("t", "p3"): "pass"}
    res = _fake_audit(monkeypatch, inv, official, sibling)
    assert not any("KeyError" in r.reason for r in res.rows.values())
    assert res.rows["t"].usable and res.rows["t"].round == 1
    p3 = res.rows["p3"]
    assert p3.task_failed and not p3.usable and "boom p3 vs t" in p3.reason, p3
    for f in ("p4", "p5"):
        assert res.rows[f].comparator_kind == "inherit" and res.rows[f].usable, res.rows[f].reason
    assert res.providers[0]["inheritable"]


@pytest.mark.parametrize("p3_official, p3_vs_r", [(None, "pass"), (0.5, "pass+1"), (None, "fail+1"),
                                                  (None, "pass+1?")])
def test_a_crashed_record_a_month_vote_condemns_is_still_a_failed_row(monkeypatch, p3_official, p3_vs_r):
    # p3 passes q and u as utc_instant, but both are off in the same scattered months: the month vote
    # makes it unexplained. That vote could change with another comparator, so when its final
    # cross-check against t crashes it is unknown: one passing base, a conclusive official class
    # (whatever r gives), or another base only from a failed or inconclusive r, is not a
    # disagreement no pass can rescue.
    inv = {f"p{i}": _info(f"p{i}", "P", 10.0 * (i + 1)) for i in range(6)}
    for k, sid in enumerate("qrtu", 1):
        inv[sid] = _info(sid, sid.upper(), 40.0 + 0.001 * k)
    official = {"p0": 0.5, "p1": 0.5, "p2": 0.5, "q": 0.5, "r": 0.5, "u": 0.5}
    if p3_official is not None:
        official["p3"] = p3_official
    sibling = {("p3", "q"): "pass!", ("p3", "u"): "pass!", ("p3", "r"): p3_vs_r, ("p3", "t"): "pass",
               ("t", "r"): "fail"}
    for a in "qrtu":
        for b in "qrtu":
            sibling.setdefault((a, b), "pass")
        sibling[(a, "p3")] = "pass"
    res = _fake_audit(monkeypatch, inv, official, sibling)       # control: no crash, the vote condemns it
    assert res.rows["p3"].verdict == "unexplained" and "not isolated" in res.rows["p3"].reason
    assert not res.providers[0]["inheritable"]
    sibling[("p3", "t")] = "raise"
    res = _fake_audit(monkeypatch, inv, official, sibling)
    assert not any("KeyError" in r.reason for r in res.rows.values())
    assert res.rows["t"].usable and res.rows["t"].round == 1
    p3 = res.rows["p3"]
    assert p3.task_failed and not p3.usable and "boom p3 vs t" in p3.reason, p3
    for f in ("p4", "p5"):
        assert res.rows[f].comparator_kind == "inherit" and res.rows[f].usable, res.rows[f].reason
    assert res.providers[0]["inheritable"]


# --- G04e: comparator lookup (alias first, then same gauge, then same port in the source's country) ----------

def _km_north(lat: float, km: float) -> float:
    return lat + km / 111.195


def _port_gates(tmp_path):
    """One RWS ref (Hoek van Holland, app station at 0 km), one NOAA R ref (Eastport) and one NOAA S ref."""
    return _gates(tmp_path, [{"id": "THVH", "lat": 51.9775, "lon": 4.12}, {"id": "XEP", "lat": 44.9046, "lon": -66.9829},
                             {"id": "XS", "lat": 30.0, "lon": -80.0}],
                  {"THVH": {"src": "rws", "ref": "hoekvanholland", "dist_km": 0.0},
                   "XEP": {"src": "noaa", "ref": "8410140", "kind": "R", "dist_km": 0.0},
                   "XS": {"src": "noaa", "ref": "8720999", "kind": "S", "dist_km": 0.0}},
                  [("rws", "hoekvanholland"), ("noaa", "8410140"), ("noaa", "8720999")])


def test_official_same_port_within_5_km_in_the_sources_country(tmp_path):
    refs = ta.References(_port_gates(tmp_path), noaa_harcon_dir=_empty_ref_caches(tmp_path)[0] / "harcon", dmi_dir=tmp_path / "dmi-events")
    lat = _km_north(51.9775, 3.5)                               # 3.5 km: same port, not the same gauge
    got = refs.official(lat, 4.12, "hoekvanholland-hvh-nld-rws_hist")
    assert got is not None and got.ref == "hoekvanholland" and got.method == "distance" and 3.4 < got.dist_km < 3.6
    assert refs.official(lat, 4.12, "hoekvanholland-hvh-bel-cmems") is None     # another country: no port pairing
    assert refs.official(lat, 4.12) is None                                       # no record id: same gauge only
    assert refs.official(_km_north(51.9775, 5.5), 4.12, "x-hvh-nld-rws") is None  # beyond the port radius
    # within REF_MAX_KM the gauge is the same gauge, whatever the country (Bermuda UHSLC vs NOAA 2695540)
    assert refs.official(_km_north(51.9775, 1.5), 4.12, "x-hvh-bel-cmems").ref == "hoekvanholland"


def test_official_harness_noaa_subordinate_events_are_never_a_comparator(tmp_path):
    # the harness's kind-S events are not used; without A-NOAA's offsets there is no subordinate reference
    refs = ta.References(_port_gates(tmp_path), noaa_harcon_dir=_empty_ref_caches(tmp_path)[0] / "harcon", dmi_dir=tmp_path / "dmi-events")
    assert refs.official(30.0, -80.0, "y-8720999-usa-noaa") is None
    assert refs.official(_km_north(30.0, 3.0), -80.0, "y-1-usa-usgs") is None


def test_official_alias_by_noaa_station_number_beats_a_nearer_station(tmp_path):
    g = _gates(tmp_path, [{"id": "XEP", "lat": 44.9046, "lon": -66.9829}, {"id": "XN", "lat": 44.95, "lon": -66.99}],
               {"XEP": {"src": "noaa", "ref": "8410140", "kind": "R", "dist_km": 0.0},
                "XN": {"src": "noaa", "ref": "8410999", "kind": "R", "dist_km": 0.0}},
               [("noaa", "8410140"), ("noaa", "8410999")])
    refs = ta.References(g, noaa_harcon_dir=_empty_ref_caches(tmp_path)[0] / "harcon", dmi_dir=tmp_path / "dmi-events")
    got = refs.official(44.95, -66.99, "eastport_maine-8410140-usa-da_sat")     # 5 km from Eastport, 0 from 8410999
    assert got.ref == "8410140" and got.method == "alias" and 4.0 < got.dist_km < 6.0
    assert refs.official(44.95, -66.99, "x-8410140-usa-usgs").ref == "8410999"  # a USGS number is not a NOAA alias


def test_harcon_alias_and_the_adapter_cache(tmp_path):
    g = _port_gates(tmp_path / "g")
    cache = tmp_path / "noaa_harcon"
    cache.mkdir()
    (cache / "9410170.json").write_text("{}")
    (tmp_path / "stations.json").write_text('{"stations": []}')      # A-NOAA's station list (no S stations)
    (g / "refs/noaa/harcon").mkdir(parents=True)
    (g / "refs/noaa/harcon/harcon_8410140.json").write_text("{}")
    refs = ta.References(g, noaa_harcon_dir=cache, dmi_dir=_empty_dmi(tmp_path))
    assert refs.harcon("san_diego-9410170-usa-noaa") == cache / "9410170.json"    # A-NOAA's harcon cache
    assert refs.harcon("eastport_maine-8410140-usa-da_sat").name == "harcon_8410140.json"   # DA_SAT uses NOAA numbers
    assert refs.harcon("x-9410170-usa-usgs") is None
    assert refs.harcon("x-9999999-usa-noaa") is None


def test_official_task_lists_a_failed_official_attempt_as_tried(monkeypatch):
    rec = record("x-123-usa-noaa", HOURS, np.zeros(len(HOURS)))

    class Refs:
        def harcon(self, _f):
            return None

        def official(self, lat, lon, file_id=None):
            return ta.OfficialRef("noaa", "123", 3.1, "p", "distance")

    monkeypatch.setitem(ta._W, "table", None)
    monkeypatch.setitem(ta._W, "refs", Refs())
    monkeypatch.setitem(ta._W, "transforms", {})
    monkeypatch.setitem(ta._W, "cache", {rec.record_id: rec})
    monkeypatch.setattr(ta, "official_lag", lambda *a: "official:noaa:123: 12 official extrema in the windows (< 50)")
    rid, outs, notes, extra = ta._official_task(rec.record_id, 0.0, 0.0)
    assert outs == [] and extra["tried"] == ["official:noaa:123"] and "12 official extrema" in notes[0]


def test_audit_excluded_record_keeps_its_failed_comparator_and_why(monkeypatch):
    inv = {"q": _info("q", "Q", 50.0)}
    monkeypatch.setattr(ta, "_init", lambda *a: None)

    class Refs:
        def __init__(self, *_a, **_k):
            pass

        def counts(self):
            return {}

        def harcon(self, _f):
            return None

        def official(self, lat, lon, file_id=None):
            return object()

    def off_task(rid, lat, lon):
        return rid, [], ["official:rws:x: 3 official extrema in the windows (< 50)"], {"span": SPAN, "tried": ["official:rws:x"]}

    monkeypatch.setattr(ta, "References", Refs)
    monkeypatch.setattr(ta, "_official_task", ta._safe(off_task))
    res = ta.audit(inv, ["q"], release_root="r", table_path="t", gates_dir="g", workers=1)
    q = res.rows["q"]
    assert q.status == "unverified" and q.tried == ["official:rws:x"] and "3 official extrema" in q.reason


@pytest.mark.parametrize("dist, method, kept", [(3.5, "distance", False), (1.5, "distance", True), (3.5, "alias", True)])
def test_same_port_station_confirms_but_never_disputes(monkeypatch, dist, method, kept):
    rec = record("x-1-usa-usgs", HOURS, np.zeros(len(HOURS)))

    class Refs:
        def harcon(self, _f):
            return None

        def official(self, lat, lon, file_id=None):
            return ta.OfficialRef("noaa", "123", dist, "p", method)

    monkeypatch.setitem(ta._W, "table", None)
    monkeypatch.setitem(ta._W, "refs", Refs())
    monkeypatch.setitem(ta._W, "transforms", {})
    monkeypatch.setitem(ta._W, "cache", {rec.record_id: rec})
    ev = {"kind": "b", "id": "official:noaa:123", "lag_min": 25.0, "n_events": 100, "n_matched": 99,
          "dist_km": dist, "method": method}
    monkeypatch.setattr(ta, "official_lag", lambda *a: dict(ev))
    _rid, outs, notes, extra = ta._official_task(rec.record_id, 0.0, 0.0)
    assert extra["tried"] == ["official:noaa:123"]
    if kept:                                    # the same gauge (or the named station) may dispute
        assert len(outs) == 1 and not outs[0].conclusive
    else:
        assert outs == [] and "same port" in notes[-1]
    monkeypatch.setattr(ta, "official_lag", lambda *a: dict(ev, lag_min=0.4))
    _rid, outs, _n, _e = ta._official_task(rec.record_id, 0.0, 0.0)
    assert len(outs) == 1 and outs[0].conclusive and outs[0].verdict.verdict == "utc_instant"


# --- G04f: confirm-only references (DMI tidewater, NOAA subordinate offsets) ------------------------------

from gesla import dmi_reference, noaa_subordinate  # noqa: E402


def _iso(s: int) -> str:
    return str(np.datetime64(int(s), "s")) + "Z"


def _noaa_cache(tmp_path, gates, subs, ref_events):
    """A-NOAA's station list + offsets (subs: id -> (ref, lat, lon, high_min, low_min)) and the reference's
    harness events (unix s, kind)."""
    cache = tmp_path / "noaa"
    (cache / "offsets").mkdir(parents=True)
    (cache / "harcon").mkdir()
    st = [{"id": "REF1", "type": "R", "lat": 0.0, "lng": 0.0, "reference_id": ""}]
    for sid, (ref, lat, lon, hi, lo) in subs.items():
        st.append({"id": sid, "type": "S", "lat": lat, "lng": lon, "reference_id": ref})
        (cache / "offsets" / f"{sid}.json").write_text(json.dumps(
            {"refStationId": ref, "type": "S", "timeOffsetHighTide": hi, "timeOffsetLowTide": lo}))
    (cache / "stations.json").write_text(json.dumps({"stations": st}))
    ev = gates / "refs/noaa/events"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "REF1.json").write_text(json.dumps({"events": [[_iso(t), "High" if k > 0 else "Low", 1.0]
                                                         for t, k in ref_events]}))
    return cache / "harcon"


REF_EVENTS = [(unix(2026, 11, 1, 3), 1), (unix(2026, 11, 1, 9), -1), (unix(2026, 12, 31, 23), 1)]


def test_subordinate_events_move_highs_and_lows_by_their_own_offsets(tmp_path):
    g = _gates(tmp_path / "g", [], {})
    _noaa_cache(tmp_path, g, {"8534540": ("REF1", 39.4, -74.4, 88, -97)}, REF_EVENTS)
    (sub,) = noaa_subordinate.load_subordinates(tmp_path / "noaa", g / "refs/noaa/events")
    got = noaa_subordinate.subordinate_events(sub, REF_EVENTS)
    assert got == ((REF_EVENTS[0][0] + 88 * 60, 1), (REF_EVENTS[1][0] - 97 * 60, -1), (REF_EVENTS[2][0] + 88 * 60, 1))


def test_subordinate_offsets_naming_another_reference_are_an_error(tmp_path):
    g = _gates(tmp_path / "g", [], {})
    _noaa_cache(tmp_path, g, {"S1": ("REF1", 1.0, 1.0, 5, 5)}, REF_EVENTS)
    st = json.loads((tmp_path / "noaa/stations.json").read_text())
    st["stations"][1]["reference_id"] = "OTHER"
    (tmp_path / "noaa/stations.json").write_text(json.dumps(st))
    with pytest.raises(ValueError, match="S1.json"):
        noaa_subordinate.load_subordinates(tmp_path / "noaa", g / "refs/noaa/events")


def test_references_subordinate_pairing_and_built_events(tmp_path):
    g = _port_gates(tmp_path / "g")
    harcon = _noaa_cache(tmp_path, g, {"8720999": ("REF1", 30.0, -80.0, 30, -20),
                                       "8721000": ("REF1", 31.0, -80.0, 0, 0)}, REF_EVENTS)
    refs = ta.References(g, noaa_harcon_dir=harcon, dmi_dir=_empty_dmi(tmp_path))
    got = refs.official(_km_north(30.0, 1.5), -80.0, "y-1-usa-usgs")             # the same gauge
    assert (got.src, got.ref, got.method, got.confirm_only, got.band_min) == \
        ("noaa_sub", "8720999", "distance", True, ta.SUB_BAND_MIN)
    assert got.events[0] == (REF_EVENTS[0][0] + 30 * 60, 1) and "noaa:REF1" in got.via
    assert refs.official(_km_north(30.0, 3.0), -80.0, "y-1-usa-usgs") is None         # same port: never
    assert refs.official(_km_north(30.0, 1.5), -80.0, "y-1-mex-x").ref == "8720999"   # any country
    assert refs.official(30.9, -80.0, "y-8720999-usa-noaa").method == "alias"         # 11 km, named
    # a harness reference (R) beats a subordinate station
    assert refs.official(44.9046, -66.9829, "x-1-usa-usgs").src == "noaa"


def _dmi_events(tmp_path, sid, lat, lon):
    d = tmp_path / "dmi/events"
    d.mkdir(parents=True, exist_ok=True)
    st = dmi_reference.DmiStation(sid, "Havn", lat, lon)
    (d / f"{sid}.json").write_text(json.dumps(dmi_reference.events_doc(st, [("2026-11-01T05:21:00Z", "High", 1.0)],
                                                                       ta.REF_WINDOWS)))
    return d


def test_references_dmi_same_gauge_any_country_same_port_in_denmark(tmp_path):
    d = _dmi_events(tmp_path, "25149", 55.4602, 8.4397)
    empty = dmi_reference.DmiStation("25152", "Esbjerg Havn II", _km_north(55.4602, 0.8), 8.4397)  # nearer; no predictions
    (d / "25152.json").write_text(json.dumps(dmi_reference.events_doc(empty, [], ta.REF_WINDOWS)))
    refs = ta.References(_port_gates(tmp_path / "g"), noaa_harcon_dir=_empty_ref_caches(tmp_path)[0] / "harcon", dmi_dir=d)
    got = refs.official(_km_north(55.4602, 0.8), 8.4397, "esbjerg-esb-dnk-dmi")
    assert (got.src, got.ref, got.confirm_only, got.band_min) == ("dmi", "25149", True, OFFICIAL_BAND_MIN)
    assert got.events is None and got.path.endswith("25149.json")
    assert refs.official(_km_north(55.4602, 3.0), 8.4397, "x-esb-dnk-cmems").ref == "25149"
    assert refs.official(_km_north(55.4602, 3.0), 8.4397, "x-esb-deu-cmems") is None
    assert refs.official(_km_north(55.4602, 5.5), 8.4397, "x-esb-dnk-cmems") is None


def _sub_ev(lag, high, low, **kw):
    return {"kind": "b", "id": "official:noaa_sub:1", "lag_min": lag, "lag_high": high, "lag_low": low,
            "confirm_only": True, "band_min": ta.SUB_BAND_MIN, **kw}


@pytest.mark.parametrize("lag, high, low, ok", [(0.5, 2.0, -1.0, True), (-9.0, -9.9, -8.0, True),
                                                (-1.0, 3.0, -11.0, False),    # the low-water offset is off
                                                (11.0, 11.0, 11.0, False),    # beyond SUB_BAND_MIN (< official 12)
                                                (60.2, 60.0, 60.4, False), (-30.0, -30.0, -30.0, False),
                                                (0.0, None, 0.0, False)])
def test_confirm_only_outcome_needs_high_low_and_median_in_band(lag, high, low, ok):
    o = official_outcome("r", _sub_ev(lag, high, low), SPAN)
    assert o.conclusive is ok and (o.verdict.verdict == "utc_instant") is ok
    if not ok:
        assert "not all within" in o.note or "no class" in o.note or "cannot test" in o.note


def test_confirm_only_reference_never_makes_a_record_unexplained(monkeypatch):
    rec = record("x-1-usa-usgs", HOURS, np.zeros(len(HOURS)))

    class Refs:
        def harcon(self, _f):
            return None

        def official(self, lat, lon, file_id=None):
            return ta.OfficialRef("noaa_sub", "1", 0.1, "p", "distance", events=(), via="noaa:2 high +5 min, low +7 min")

    monkeypatch.setitem(ta._W, "table", None)
    monkeypatch.setitem(ta._W, "refs", Refs())
    monkeypatch.setitem(ta._W, "transforms", {})
    monkeypatch.setitem(ta._W, "cache", {rec.record_id: rec})
    monkeypatch.setattr(ta, "official_lag", lambda *a: dict(_sub_ev(60.1, 60.0, 60.2), dist_km=0.1,
                                                            method="distance", via="noaa:2 high +5 min, low +7 min"))
    _rid, outs, notes, extra = ta._official_task(rec.record_id, 0.0, 0.0)
    assert outs == [] and extra["tried"] == ["official:noaa_sub:1"]
    assert "confirm-only" in notes[-1] and "+60.1" in notes[-1] and "noaa:2" in notes[-1]
    monkeypatch.setattr(ta, "official_lag", lambda *a: dict(_sub_ev(1.0, 2.0, 0.0), dist_km=0.1, method="distance"))
    _rid, outs, _n, _e = ta._official_task(rec.record_id, 0.0, 0.0)
    assert len(outs) == 1 and outs[0].conclusive and outs[0].verdict.verdict == "utc_instant"


def test_official_lag_uses_built_events_and_reports_high_and_low(table, monkeypatch):
    # the record's trial fit = CONSTS; the reference's extrema = the same tide 7 min earlier (highs) / 7 min (lows)
    t = np.arange(ta._iso_s("2026-10-31"), ta._iso_s("2027-08-02"), 60, dtype=np.int64)
    xt, xk = extrema(t, predict(table, CONSTS, 0.0, t))
    events = tuple((int(round(x)) - 7 * 60, int(k)) for x, k in zip(xt, xk))

    class An:
        class fit:
            z0 = 0.0

        def consts(self):
            return CONSTS

    monkeypatch.setattr(ta, "_trial_fit", lambda *a: An())
    ref = ta.OfficialRef("noaa_sub", "1", 0.0, "unused-path", "alias", events=events, via="v")
    ev = ta.official_lag(record("r", HOURS, np.zeros(len(HOURS))), table, ref, SPAN)
    assert ev["confirm_only"] and ev["via"] == "v" and ev["band_min"] == ta.SUB_BAND_MIN
    for k in ("lag_min", "lag_high", "lag_low"):
        assert ev[k] == pytest.approx(7.0, abs=0.2)


def test_dmi_tide_stations_events_and_polite_fetch(tmp_path):
    feats = [{"type": "Feature", "geometry": {"coordinates": [8.0, 55.0]},
              "properties": {"stationId": "1", "name": "Old", "parameterId": ["sea_reg", "tw"], "validTo": "2020-01-01"}},
             {"type": "Feature", "geometry": {"coordinates": [8.1, 55.1]},
              "properties": {"stationId": "1", "name": "New", "parameterId": ["tw"], "validTo": None}},
             {"type": "Feature", "geometry": {"coordinates": [9.0, 56.0]},
              "properties": {"stationId": "2", "name": "No tw", "parameterId": ["sea_reg"], "validTo": None}}]
    sp = tmp_path / "stations.json"
    sp.write_text(json.dumps({"features": feats}))
    (st,) = dmi_reference.tide_stations(sp)
    assert (st.station_id, st.name, st.lat) == ("1", "New", 55.1)

    def f(t, kind, sid="1"):
        return {"properties": {"stationId": sid, "predictionType": kind, "predictionTime": t, "value": 10.0}}
    pages = {}
    calls = []

    def opener(url):
        calls.append(url)
        if url.startswith("NEXT"):
            return json.dumps({"features": [f("2026-11-01T11:55:00Z", "minimum")], "links": []}).encode()
        return json.dumps({"features": [f("2026-11-01T05:21:00Z", "maximum")],
                           "links": [{"rel": "next", "href": "NEXT" + url[-8:]}]}).encode()
    win = (("2026-11-01", "2027-01-01"),)
    dmi_reference.fetch([st], win, tmp_path / "out", dmi_reference.PoliteGet(1.0, opener), log=lambda *_: None)
    assert len(calls) == 2 and "datetime=2026-11-01T00%3A00%3A00Z%2F2026-12-31T23%3A59%3A59Z" in calls[0]
    doc = json.loads((tmp_path / "out/events/1.json").read_text())
    assert doc["events"] == [["2026-11-01T05:21:00Z", "High", 10.0], ["2026-11-01T11:55:00Z", "Low", 10.0]]
    man = json.loads((tmp_path / "out/MANIFEST.json").read_text())
    assert len(man["files"]) == 2 and all(len(x["sha256"]) == 64 for x in man["files"])
    assert dmi_reference.load_points(tmp_path / "out/events") == [(55.1, 8.1, "1", str(tmp_path / "out/events/1.json"))]
    with pytest.raises(ValueError, match="without a zone"):
        dmi_reference.events_from([f("2026-11-01T05:21:00", "maximum")], "1")
    with pytest.raises(ValueError, match="station 2"):
        dmi_reference.events_from([f("2026-11-01T05:21:00Z", "maximum", "2")], "1")


# --- G04 integration: provider inheritance needs >= INHERIT_MIN judged records in the record's own country ---

def _country_inventory(tmp_path, rows):
    """An inventory.csv of GESLA file ids (<site>-<code>-<country>-<contributor>), loaded by load_inventory."""
    p = tmp_path / "inventory.csv"
    lines = ["file_id,contributor,role,licence_class,gauge_type,interval_s,start,end,years,lat,lon"]
    for i, (fid, contributor) in enumerate(rows):
        lines.append(f"{fid},{contributor},owner,open,Coastal,3600,2000-01-01T00:00:00,2020-01-01T00:00:00,20,"
                     f"{-40.0 + 2.0 * i},150.0")
    p.write_text("\n".join(lines) + "\n")
    return ta.load_inventory(p)


def test_pacific_records_of_a_provider_do_not_vouch_for_its_records_in_another_country(monkeypatch, tmp_path):
    # BOM: 3 judged Pacific (FJI) records must not vouch for its AU records. NHS (control): 3 judged
    # NOR records still vouch for its other NOR records.
    bom_fji = [f"suva{i}-s{i}-fji-bom" for i in range(3)]
    bom_aus = [f"sydney{i}-y{i}-aus-bom" for i in range(2)]
    nhs = [f"bergen{i}-b{i}-nor-nhs" for i in range(5)]
    inv = _country_inventory(tmp_path, [(f, "BOM") for f in bom_fji + bom_aus] + [(f, "NHS") for f in nhs])
    official = {f: 0.5 for f in bom_fji + nhs[:3]}
    res = _fake_audit(monkeypatch, inv, official, {})
    bom = next(p for p in res.providers if p["contributor"] == "BOM")
    assert bom["provider_verdict"] == "utc_instant" and bom["n_audited"] == 3
    for f in bom_aus:
        r = res.rows[f]
        assert r.comparator_kind != "inherit" and not r.usable and r.verdict == "no_comparator", r
        assert "0 judged records in aus" in r.reason, r.reason
        assert r.annotation == "unverified" and r.published.label == "utc_instant", (r.annotation, r.published)
    for f in nhs[3:]:                                       # control: same country, 3 judged
        r = res.rows[f]
        assert r.comparator_kind == "inherit" and r.usable and r.verdict == "utc_instant", r.reason
        assert "in nor" in r.reason


def test_two_judged_records_in_the_country_do_not_vouch_even_when_the_provider_has_three(monkeypatch, tmp_path):
    # UNAM_HIST: 2 judged MEX records + 1 judged USA record is >= 3 for the provider, < 3 for MEX.
    mex = [f"mazatlan{i}-m{i}-mex-unam_hist" for i in range(4)]
    usa = ["sandiego-9410170-usa-unam_hist"]
    inv = _country_inventory(tmp_path, [(f, "UNAM_HIST") for f in mex + usa])
    res = _fake_audit(monkeypatch, inv, {mex[0]: 0.5, mex[1]: 0.5, usa[0]: 0.5}, {})
    assert res.providers[0]["inheritable"] and res.providers[0]["n_audited"] == 3
    for f in mex[2:]:
        r = res.rows[f]
        assert r.comparator_kind != "inherit" and not r.usable, r
        assert "2 judged records in mex" in r.reason, r.reason
    # a third judged MEX record makes MEX inheritable
    res = _fake_audit(monkeypatch, inv, {mex[0]: 0.5, mex[1]: 0.5, mex[2]: 0.5}, {})
    assert res.rows[mex[3]].comparator_kind == "inherit" and res.rows[mex[3]].usable, res.rows[mex[3]].reason


def test_a_record_without_a_country_does_not_inherit(monkeypatch, tmp_path):
    nhs = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in nhs + ["oddrecord-nhs"]])
    assert inv["oddrecord-nhs"].country is None and inv[nhs[0]].country == "nor"
    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in nhs}, {})
    r = res.rows["oddrecord-nhs"]
    assert r.comparator_kind != "inherit" and not r.usable and "country is unknown" in r.reason, r.reason


# --- G04d: no record is dropped; every record is annotated ----------------------------------------

def test_no_record_is_dropped_and_every_record_is_annotated(monkeypatch, tmp_path):
    """Owner rule: verified / corrected / unverified / disputed, all published; unverified and
    disputed keep GESLA's declared time base (wewak-like TIME ZONE HOURS 1 -> utc_plus_1)."""
    inv = {"ok": _info("ok", "A", 10.0), "bad": _info("bad", "B", 20.0), "lone": _info("lone", "C", 30.0),
           "tz1": _info("tz1", "D", 40.0), "boom": _info("boom", "E", 50.0)}
    res = _fake_audit(monkeypatch, inv, {"ok": 0.5, "bad": 31.0, "boom": "raise"}, {},
                      declared={"tz1": 1})
    assert sorted(res.rows) == sorted(inv)
    ann = {f: r.annotation for f, r in res.rows.items()}
    assert ann == {"ok": "verified", "bad": "disputed", "lone": "unverified", "tz1": "unverified", "boom": "unverified"}
    assert all(r.published is not None and r.published.usable for r in res.rows.values())
    assert res.rows["ok"].annotation_code == "verified:official"
    assert res.rows["bad"].published.label == "utc_instant" and res.rows["tz1"].published.label == "utc_plus_1"
    assert res.rows["lone"].annotation_code == "unverified:no_comparator"
    assert res.rows["boom"].annotation_code == "unverified:audit_task_failed"   # a crash is no evidence
    assert res.rows["boom"].published.label == "utc_instant"
    assert all(r.annotation_reason for r in res.rows.values())
    write_csv(res.rows, tmp_path / "timebase.csv")
    import csv as _csv
    got = list(_csv.DictReader(open(tmp_path / "timebase.csv")))
    assert len(got) == len(inv) and {g["time_base_status"] for g in got} <= {"verified", "corrected", "unverified", "disputed"}
    assert all(g["published_time_base"] for g in got) and "status" not in got[0]


def test_a_class_other_than_the_declared_one_is_a_correction():
    from gesla.timebase_audit import annotate
    r = _row("r", "utc_mean_start60", kind="a")
    r.correction = Correction("utc_mean_start60")
    annotate(r, 0)
    assert (r.annotation, r.annotation_code) == ("corrected", "corrected:utc_instant->utc_mean_start60:sibling")
    r = _row("r", "utc_instant", kind="b")
    annotate(r, 1)          # declared UTC+1 but the official comparator says UTC
    assert r.annotation_code == "corrected:utc_plus_1->utc_instant:official"


def test_weak_results_are_applied_to_records_without_a_comparator(monkeypatch):
    from gesla import timebase_weak as tw
    inv = {k: _info(k, k.upper(), 10.0 * (i + 1)) for i, k in enumerate(("v", "c", "d", "u"))}
    results = {"v": tw.WeakResult("verified", "utc_instant", "verified:eot20+served", "both agree"),
               "c": tw.WeakResult("corrected", "local_dst:Europe/Berlin",
                                  "corrected:utc_instant->local_dst:Europe/Berlin:dst_selftest+eot20", "step"),
               "d": tw.WeakResult("disputed", "utc_instant", "disputed:weak_checks_disagree", "E 0, S off"),
               "u": tw.WeakResult("unverified", "utc_instant", "unverified:weak_insufficient", "nothing")}

    def weak_task(rid, n_decl, origin_status, *_):
        return rid, [], [], {"weak": results[rid], "span": SPAN}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {}, {}, weak=_Weak())
    got = {f: (r.annotation, r.annotation_code, r.published.label) for f, r in res.rows.items()}
    assert got["v"] == ("verified", "verified:eot20+served", "utc_instant")
    assert got["c"] == ("corrected", "corrected:utc_instant->local_dst:Europe/Berlin:dst_selftest+eot20",
                        "local_dst:Europe/Berlin")
    assert got["d"] == ("disputed", "disputed:weak_checks_disagree", "utc_instant")
    assert got["u"] == ("unverified", "unverified:weak_insufficient", "utc_instant")
    assert [c.subject_id for c in res.residue] == ["d"]       # disputed goes to D5 too


class _Weak:
    served: dict = {}


def test_weak_task_gets_the_declared_hours_and_the_origins_strong_status(monkeypatch):
    """The audit forwards each record's declared TIME ZONE HOURS and, for each served origin, its status after
    the strong audit (an origin verified by an official comparator is "verified"; one with no comparator is
    "unverified"; one outside the pool is "not audited")."""
    from gesla import timebase_weak as tw
    inv = {"w": _info("w", "W", 10.0), "o": _info("o", "O", 20.0), "u": _info("u", "U", 30.0)}
    seen = {}

    def weak_task(rid, n_decl, origin_status, *_):
        seen[rid] = (n_decl, dict(origin_status))
        return rid, [], [], {"weak": tw.WeakResult("unverified", "x", "unverified:weak_insufficient", "-"), "span": SPAN}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))

    class W:
        served = {"w": (tw.Served("T1", "o", {}), tw.Served("T2", "u", {}), tw.Served("T3", "gone", {}))}

    _fake_audit(monkeypatch, inv, {"o": 0.5}, {}, weak=W(), declared={"w": 1})
    assert seen["w"] == (1, {"o": "verified", "u": "unverified", "gone": "not audited"})


def test_origin_status():
    from gesla.timebase_audit import origin_status
    assert origin_status(None, 0) == "not audited"
    assert origin_status(_row("o", "utc_instant", kind="b"), 0) == "verified"
    assert origin_status(_row("o", "utc_instant", kind="b"), 1) == "corrected"     # declared UTC+1, verdict UTC
    assert origin_status(_row("o", "unexplained", status="residue"), 0) == "disputed"
    assert origin_status(_row("o", "utc_instant", kind="w"), 0) == "weak only"     # weak-only: not an anchor


def test_providers_carry_the_final_annotation_counts(monkeypatch, tmp_path):
    inv = {"ok": _info("ok", "A", 10.0), "lone": _info("lone", "A", 30.0)}
    res = _fake_audit(monkeypatch, inv, {"ok": 0.5}, {})
    p = [x for x in res.providers if x["contributor"] == "A"][0]
    assert (p["n_no_comparator"], p["n_verified"], p["n_unverified"], p["n_disputed"]) == (1, 1, 1, 0)
    write_providers(res.providers, tmp_path / "p.csv")
    head = (tmp_path / "p.csv").read_text().splitlines()[0]
    assert "n_no_comparator" in head and head.endswith("n_verified,n_corrected,n_unverified,n_disputed")


def test_real_weak_task_forwards_the_declared_hours_lineage_and_origin_status(monkeypatch):
    """_weak_task itself (not a fake): what it hands to assess()."""
    from gesla import timebase_weak as tw
    t = np.arange(unix(2010), unix(2012), HOUR, dtype=np.int64)
    rec = GaugeRecord(record_id="r-1-usa-noaa", source="gesla", source_version="t", contributor="NOAA", originator=True,
                      licence_class="x", lat=40.0, lon=-70.0, gauge_type="Coastal", sampling="instantaneous",
                      interval_min=60, declared_time_base=None, member_sha256="0" * 64, times_s=t,
                      heights_m=np.sin(t / 3600.0), good=np.ones(len(t), dtype=bool))
    got = {}

    def fake_assess(record, table, span, **kw):
        got.update(kw)
        return tw.WeakResult("unverified", "x", "unverified:weak_insufficient", "-")

    inputs = tw.WeakInputs({"r-1-usa-noaa": {"eot": {"dist_km": 1.0}, "zone": "America/New_York"}})
    served = {"r-1-usa-noaa": (tw.Served("T1", "o-2-usa-usgs", {}), tw.Served("T2", "r-1-usa-noaa", {}))}
    lin = {"r-1-usa-noaa": tw.Lineage("NOAA", "originator"), "o-2-usa-usgs": tw.Lineage("USGS", "originator")}
    monkeypatch.setattr(ta, "assess", fake_assess)
    monkeypatch.setattr(ta, "_W", {"table": None, "transforms": {}, "cache": {"r-1-usa-noaa": rec},
                                   "weak": ta.WeakConfig(inputs, served, lin)})
    rid, _o, notes, extra = ta._weak_task("r-1-usa-noaa", 1, {"o-2-usa-usgs": "verified"})
    assert notes == [] and "failed" not in extra
    assert got["n_decl"] == 1 and got["zone"] == "America/New_York" and got["missing_inputs"] is False
    # T2 is built from the record itself: no agreement, unqualified (TB-SIB), whatever the other origin's status
    assert [(r.served.station, r.kind) for r in got["served"]] == [("T1", "independent"), ("T2", "unqualified")]
    ta._weak_task("r-1-usa-noaa", 0, {"o-2-usa-usgs": "unverified"})
    assert [r.kind for r in got["served"]] == ["unqualified", "unqualified"]


def test_weak_task_without_good_data_is_counted_as_a_failure(monkeypatch):
    """_weak_task's "no good data" exit gives no weak result: it is unverified:weak_failed AND in the failure count."""
    inv = {"empty": _info("empty", "E", 10.0)}

    def weak_task(rid, n_decl, origin_status, *_):
        return rid, [], ["no good data"], {}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {}, {}, weak=_Weak())
    assert res.rows["empty"].annotation_code == "unverified:weak_failed"
    assert res.weak_failures == [("empty", "no good data")]


def test_residue_evidence_carries_the_weak_checks():
    """A record disputed by the weak checks goes to D5 with those checks in its evidence."""
    from gesla import timebase_weak as tw
    from gesla.timebase_audit import apply_weak, row_evidence
    row = AuditRow("v", True, "UHSLC_RQ", 3600, "unverified", "no_comparator", "no_comparator", NOT_USABLE, None,
                   "none", "")
    ev = {"E": {"value": -1, "tau": -62.5}, "S": {"value": "n/a"}, "S_independent": False, "D": {"value": "no_step"},
          "S_all": [{"station": "T6e11ade", "kind": "dependent", "value": 0},
                    {"station": "Tdce40e9", "kind": "dependent", "value": -1}]}
    apply_weak(row, tw.WeakResult("disputed", "utc_instant", "disputed:served_stations_disagree", "x", ev), SPAN)
    got = row_evidence(row)
    assert got["weak"]["code"] == "disputed:served_stations_disagree"
    assert [s["station"] for s in got["weak"]["evidence"]["S_all"]] == ["T6e11ade", "Tdce40e9"]


# --- G04 integration: an inherited verdict yields to the record's own disagreeing weak evidence --------------

def test_an_inherited_record_the_weak_checks_dispute_is_disputed_not_inherited(monkeypatch, tmp_path):
    # UNAM_HIST at c89c712: 3 judged MEX records vouch for the MEX records; Alvarado and Yavaros were
    # disputed by the weak checks in G04d-r2 but inherited utc_instant, because inheritance ran first.
    from gesla import timebase_weak as tw
    judged = [f"ensenada{i}-31{i}-mex-unam_hist" for i in range(3)]
    alv, yav, ok, quiet = ("alvarado-400-mex-unam_hist", "yavaros-677-mex-unam_hist", "guaymas-500-mex-unam_hist",
                           "lapaz-600-mex-unam_hist")
    inv = _country_inventory(tmp_path, [(f, "UNAM_HIST") for f in judged + [alv, yav, ok, quiet]])
    results = {alv: tw.WeakResult("disputed", "utc_instant", "disputed:weak_checks_disagree", "E n/a, S off"),
               yav: tw.WeakResult("disputed", "utc_instant", "disputed:weak_checks_disagree", "E 0, S off"),
               ok: tw.WeakResult("verified", "utc_instant", "verified:eot20+served", "both agree"),
               quiet: tw.WeakResult("unverified", "utc_instant", "unverified:weak_insufficient", "nothing")}
    seen = []

    def weak_task(rid, n_decl, origin_status, *_):
        seen.append(rid)
        return rid, [], [], {"weak": results[rid], "span": SPAN}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in judged}, {}, weak=_Weak())
    for f in (alv, yav):
        r = res.rows[f]
        assert r.annotation == "disputed" and not r.usable and r.status == "residue", (r.annotation, r.reason)
        assert r.annotation_code == "disputed:weak_checks_disagree" and r.published.label == "utc_instant"
        assert "inherits utc_instant" in r.reason and "S off" in r.reason, r.reason
    assert sorted(c.subject_id for c in res.residue) == sorted([alv, yav])
    assert sorted(seen) == sorted([alv, yav, ok, quiet])          # the weak checks run on inherited records
    for f in (ok, quiet):                                          # verified or silent: inheritance stands
        r = res.rows[f]
        assert r.comparator_kind == "inherit" and r.usable and r.annotation == "verified", (f, r.reason)
        assert r.weak["code"] == results[f].code


def test_an_inherited_record_the_weak_checks_put_on_another_time_base_is_disputed(monkeypatch, tmp_path):
    # A weak `corrected` to another class is the record's own evidence disagreeing with its provider.
    from gesla import timebase_weak as tw
    judged = [f"ensenada{i}-31{i}-mex-unam_hist" for i in range(3)]
    other = "salinacruz-700-mex-unam_hist"
    inv = _country_inventory(tmp_path, [(f, "UNAM_HIST") for f in judged + [other]])
    got = tw.WeakResult("corrected", "utc_minus_6", "corrected:utc_instant->utc_minus_6:eot20+served", "6 h off")

    def weak_task(rid, n_decl, origin_status, *_):
        return rid, [], [], {"weak": got, "span": SPAN}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in judged}, {}, weak=_Weak())
    r = res.rows[other]
    assert r.annotation == "disputed" and not r.usable and r.published.label == "utc_instant", (r.annotation, r.reason)
    assert r.annotation_code == "disputed:inherited_utc_instant_vs_weak_utc_minus_6", r.annotation_code
    assert [c.subject_id for c in res.residue] == [other]
    assert "inherits utc_instant" in r.reason and "utc_minus_6" in r.reason, r.reason


def test_the_overlap_that_lets_a_record_inherit_counts_only_its_own_countrys_judged_records():
    # 3 judged NOR records span 2000-2005; 3 judged SWE records span 2015-2020. A NOR record of 2015-2020
    # overlaps only the Swedish ones: it does not inherit (the Norwegian records do not cover its span).
    inv = {f"n{i}": RecordInfo(f"n{i}", "NHS", 3600, 60.0 + i, 5.0, unix(2000), unix(2005), "nor") for i in range(3)}
    inv.update({f"s{i}": RecordInfo(f"s{i}", "NHS", 3600, 65.0 + i, 5.0, unix(2015), unix(2020), "swe")
                for i in range(3)})
    inv["late"] = RecordInfo("late", "NHS", 3600, 70.0, 5.0, unix(2015), unix(2020), "nor")
    inv["swe_late"] = RecordInfo("swe_late", "NHS", 3600, 71.0, 5.0, unix(2016), unix(2019), "swe")
    rows = {f: _row(f, "utc_instant") for f in ("n0", "n1", "n2", "s0", "s1", "s2")}
    for f, r in rows.items():
        r.span = (inv[f].start_s, inv[f].end_s)
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["late"].status == "unverified" and not rows["late"].usable, rows["late"].reason
    assert "span does not overlap the audited records of NHS in nor" in rows["late"].reason
    assert rows["swe_late"].comparator_kind == "inherit" and rows["swe_late"].usable    # control: Sweden covers it


def test_an_inherited_local_dst_needs_one_zone_across_the_judged_records():
    # Three judged records pass as local_dst, but their corrections name two zones: no zone to inherit.
    inv = _inv(5, country="mex")
    zones = ("America/Mexico_City", "America/Mexico_City", "America/Tijuana")
    rows = {}
    for i, z in enumerate(zones):
        c = Correction("local_dst", zone=z)
        rows[f"r{i}"] = AuditRow(f"r{i}", True, "NHS", 3600, "used", "local_dst:America/Mexico_City",
                                 "local_dst:America/Mexico_City", c, 0, "b", "x")
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    for f in ("r3", "r4"):
        assert rows[f].comparator_kind != "inherit" and not rows[f].usable, rows[f].correction
        assert "zones" in rows[f].reason and "America/Tijuana" in rows[f].reason, rows[f].reason
    # one zone: inherits it
    rows = {f"r{i}": AuditRow(f"r{i}", True, "NHS", 3600, "used", "local_dst:America/Tijuana",
                              "local_dst:America/Tijuana", Correction("local_dst", zone="America/Tijuana"), 0, "b", "x")
            for i in range(3)}
    inherit(rows, inv, sorted(inv), set(inv), version="4.1")
    assert rows["r3"].usable and rows["r3"].correction.zone == "America/Tijuana"


def test_comparators_tried_keeps_the_weak_checks_on_inherited_and_no_comparator_rows(monkeypatch, tmp_path):
    # A strong comparator tried without a verdict must not overwrite the weak checks in comparators_tried.
    from gesla import timebase_weak as tw
    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inh, lone = "molde-m1-nor-nhs", "lone-l1-nor-xyz"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [inh]] + [(lone, "XYZ")])
    unv = tw.WeakResult("unverified", "utc_instant", "unverified:weak_insufficient", "nothing")

    def weak_task(rid, n_decl, origin_status, *_):
        return rid, [], [], {"weak": unv, "span": SPAN}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    # the official task of inh and lone runs a comparator that gives no lag (tried, no verdict)
    official = {f: 0.5 for f in judged}
    official.update({inh: "nolag", lone: "nolag"})
    res = _fake_audit(monkeypatch, inv, official, {}, weak=_Weak())
    for f in (inh, lone):
        r = res.rows[f]
        assert "official:x:nolag" in r.tried, r.tried
        assert {"weak:eot20", "weak:served", "weak:dst_selftest"} <= set(r.tried), (f, r.tried)
    assert res.rows[inh].comparator_kind == "inherit"


def test_a_missing_confirm_only_cache_is_a_hard_error(tmp_path):
    # Without the DMI events dir or A-NOAA's station list the confirm-only comparators would vanish silently.
    g = _port_gates(tmp_path / "g")
    noaa, dmi = _empty_ref_caches(tmp_path)
    ta.References(g, noaa_harcon_dir=noaa / "harcon", dmi_dir=dmi)            # present (empty): fine
    with pytest.raises(FileNotFoundError, match="DMI tidewater events"):
        ta.References(g, noaa_harcon_dir=noaa / "harcon", dmi_dir=tmp_path / "no-dmi")
    (noaa / "stations.json").unlink()
    with pytest.raises(FileNotFoundError, match="NOAA station list"):
        ta.References(g, noaa_harcon_dir=noaa / "harcon", dmi_dir=dmi)
    with pytest.raises(FileNotFoundError, match="NOAA station list"):
        noaa_subordinate.load_subordinates(tmp_path / "no-noaa", g / "refs/noaa/events")
    with pytest.raises(FileNotFoundError, match="DMI tidewater events"):
        dmi_reference.load_points(tmp_path / "no-dmi")


def _empty_ref_caches(tmp_path):
    """An empty A-NOAA cache (station list with no stations) and an empty DMI events dir: no confirm-only refs."""
    noaa, dmi = tmp_path / "noaa-cache", tmp_path / "dmi-events"
    (noaa / "harcon").mkdir(parents=True)
    (noaa / "stations.json").write_text('{"stations": []}')
    dmi.mkdir()
    return noaa, dmi


def _empty_dmi(tmp_path):
    d = tmp_path / "dmi-empty"
    d.mkdir(exist_ok=True)
    return d


# --- GATE-TB A-M1: an inherited record's weak checks test the INHERITED class ---------------------------------

def _inherit_with_weak(monkeypatch, tmp_path, cls, weak_of):
    """3 judged NOR records of class cls (NHS) and one NOR record that inherits it; weak_of(n_decl) -> WeakResult.
    Returns (audit result, the inheriting record id, the n_decl each weak task got)."""
    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inh = "molde-m1-nor-nhs"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [inh]])
    seen = {}

    def weak_task(rid, n_decl, origin_status, *_):
        seen[rid] = n_decl
        return rid, [], [], {"weak": weak_of(n_decl), "span": SPAN}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {f: cls for f in judged}, {}, weak=_Weak())
    return res, inh, seen


def test_an_inherited_utc_plus_1_is_kept_when_its_own_weak_checks_find_plus_1(monkeypatch, tmp_path):
    # Declared UTC (0 h); the provider's verdict is utc_plus_1. Judged against the declared 0 h, evidence of +1 h
    # reads as a dispute (disputed:served_offset_unconfirmed); judged against the inherited +1 h it agrees.
    from gesla import timebase_weak as tw

    def weak_of(n):
        return (tw.WeakResult("verified", "utc_plus_1", "verified:eot20+served_independent", "E +1, S +1")
                if n == 1 else tw.WeakResult("disputed", "utc_instant", "disputed:served_offset_unconfirmed", "E +1, S +1"))

    res, inh, seen = _inherit_with_weak(monkeypatch, tmp_path, Correction("utc_plus_n", n=1), weak_of)
    r = res.rows[inh]
    assert seen[inh] == 1, seen                                    # tested against the inherited class
    assert r.comparator_kind == "inherit" and r.usable and r.correction.label == "utc_plus_1", (r.annotation, r.reason)
    assert r.annotation_code == "corrected:utc_instant->utc_plus_1:provider", r.annotation_code


def test_an_inherited_utc_plus_1_is_disputed_when_its_own_weak_checks_disagree(monkeypatch, tmp_path):
    from gesla import timebase_weak as tw

    def weak_of(n):
        return tw.WeakResult("disputed", "utc_plus_1", "disputed:weak_checks_disagree", "E +1, S off")

    res, inh, seen = _inherit_with_weak(monkeypatch, tmp_path, Correction("utc_plus_n", n=1), weak_of)
    r = res.rows[inh]
    assert seen[inh] == 1 and r.annotation == "disputed" and not r.usable, (r.annotation, r.reason)
    assert r.annotation_code == "disputed:weak_checks_disagree" and r.published.label == "utc_instant"


@pytest.mark.parametrize("cls", [Correction("utc_mean_start60"), Correction("local_dst", zone="Europe/Oslo")])
def test_an_inherited_class_the_weak_checks_cannot_test_is_kept(monkeypatch, tmp_path, cls):
    # The weak checks speak only of whole hours (and DST steps against the declared hours): a mean60 or
    # local_dst inheritance is not tested by them and never yields to them.
    from gesla import timebase_weak as tw

    def weak_of(n):
        return tw.WeakResult("disputed", "utc_instant", "disputed:weak_checks_disagree", "E off, S off")

    res, inh, seen = _inherit_with_weak(monkeypatch, tmp_path, cls, weak_of)
    r = res.rows[inh]
    assert inh not in seen, seen
    assert r.comparator_kind == "inherit" and r.usable and r.correction.label == cls.label, (r.annotation, r.reason)
    assert "weak checks cannot test" in r.reason, r.reason


def test_a_weak_failure_on_an_inherited_record_is_visible_and_counted(monkeypatch, tmp_path):
    # Hedel/Heesbeen/Keizersveer at 22434f5: the weak fit raised FitError on 3 inherited daily records; they read
    # as cleanly verified in timebase.csv, and the summary's 12 failures matched only 9 CSV rows.
    import csv

    from gesla.fit import FitError

    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inh, lone = "molde-m1-nor-nhs", "lone-l1-nor-xyz"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [inh]] + [(lone, "XYZ")])

    def weak_task(rid, n_decl, origin_status, *_):
        raise FitError("design rank 70 of 92")

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in judged}, {}, weak=_Weak())
    r = res.rows[inh]
    assert r.comparator_kind == "inherit" and r.usable and r.annotation == "verified", (r.annotation, r.reason)
    assert r.weak["code"] == "unverified:weak_failed" and inh not in {c.subject_id for c in res.residue}
    assert inh in {rid for rid, _note in res.weak_failures}
    assert "weak_failed" in r.reason and "FitError" in r.reason, r.reason
    write_csv(res.rows, tmp_path / "t.csv")
    with open(tmp_path / "t.csv", newline="") as fh:
        got = {x["file_id"]: x for x in csv.DictReader(fh)}
    for f in (inh, lone):
        assert got[f]["weak_eot20"].startswith("weak_failed") and "FitError" in got[f]["weak_eot20"], got[f]
        assert got[f]["weak_served"].startswith("weak_failed") and got[f]["weak_dst"].startswith("weak_failed")
    n_csv = sum(1 for x in got.values() if x["weak_eot20"].startswith("weak_failed"))
    assert n_csv == len(res.weak_failures) == 2, (n_csv, res.weak_failures)


def test_an_inherited_origin_disputed_by_its_own_weak_checks_does_not_vouch_as_verified(monkeypatch, tmp_path):
    # x (no comparator) has served stations whose origins are inherited records: bad (its own weak checks dispute
    # it) and good (they say nothing). The origins' status x is judged with is taken after the inherited
    # records' weak results: bad is disputed, good still verified. Inherited records are judged with any
    # inherited origin pending (their verdicts are not final yet).
    from gesla import timebase_weak as tw
    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    bad, good, x = "molde-m1-nor-nhs", "alesund-a1-nor-nhs", "kristiansund-k1-nor-xyz"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [bad, good]] + [(x, "XYZ")])
    seen = {}

    def weak_task(rid, n_decl, origin_status, *_):
        seen[rid] = dict(origin_status)
        if rid == bad:
            return rid, [], [], {"weak": tw.WeakResult("disputed", "utc_instant", "disputed:weak_checks_disagree",
                                                        "S off"), "span": SPAN}
        return rid, [], [], {"weak": tw.WeakResult("unverified", "utc_instant", "unverified:weak_insufficient", "-"),
                             "span": SPAN}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))

    class W:
        served = {x: (tw.Served("T1", bad, {}), tw.Served("T2", good, {})),
                  good: (tw.Served("T3", bad, {}),)}

    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in judged}, {}, weak=W())
    assert res.rows[bad].annotation == "disputed" and res.rows[good].comparator_kind == "inherit"
    assert seen[x] == {bad: "disputed", good: "verified"}, seen[x]
    assert seen[good][bad] != "verified", seen[good]


def test_the_run_manifest_names_the_code_commit_inputs_and_outputs(tmp_path):
    # B-L3: the outputs carried no commit or run identity; a killed later run left earlier outputs looking current.
    import subprocess
    repo = Path(__file__).resolve().parents[3]
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    inp = tmp_path / "inventory.csv"
    inp.write_text("x\n")
    out = tmp_path / "out"
    m = ta.write_manifest(out, repo, ["--workers", "4"], {"inventory": inp, "previous_providers": None}, status="running")
    got = json.loads((out / "timebase_manifest.json").read_text())
    assert got == m and got["status"] == "running" and got["commit"] == head and len(head) == 40
    assert got["inputs"]["inventory"] == {"path": str(inp), "sha256": ta.sha256_file(inp)}
    assert got["inputs"]["previous_providers"] is None and got["argv"] == ["--workers", "4"]
    assert isinstance(got["dirty"], bool) and got["started"]
    (out / "timebase.csv").write_text("file_id\n")
    m2 = ta.write_manifest(out, repo, ["--workers", "4"], {"inventory": inp}, status="complete", started=got["started"],
                           outputs=[out / "timebase.csv"], extra={"weak_failures": [["r", "FitError: x"]]})
    got = json.loads((out / "timebase_manifest.json").read_text())
    assert got == m2 and got["status"] == "complete" and got["started"] == m["started"] and got["finished"]
    assert got["outputs"]["timebase.csv"] == ta.sha256_file(out / "timebase.csv")
    assert got["weak_failures"] == [["r", "FitError: x"]]


def test_the_complete_manifest_keeps_the_commit_the_run_started_at(tmp_path, monkeypatch):
    # Every start value differs between the two writes (review C M1): the clock steps, the status turns dirty,
    # HEAD moves and an input file is rewritten. The complete manifest keeps what the run started with.
    out, repo = tmp_path / "out", Path(__file__).resolve().parents[3]
    inp = tmp_path / "station_map.csv"
    inp.write_text("id,file_id\n1,a\n")
    clock = iter(["2026-01-01T00:00:00+00:00", "2026-01-01T02:00:00+00:00"])
    monkeypatch.setattr(ta, "_utcnow", lambda: next(clock))
    git = {"rev-parse": "a" * 40 + "\n", "status": ""}
    monkeypatch.setattr(ta, "_git", lambda _repo, *a: git[a[0]])
    m = ta.write_manifest(out, repo, [], {"station_map": inp}, status="running")
    assert (m["started"], m["commit"], m["dirty"]) == ("2026-01-01T00:00:00+00:00", "a" * 40, False)
    git.update({"rev-parse": "f" * 40 + "\n", "status": " M gesla/timebase_audit.py\n"})
    inp.write_text("id,file_id\n1,b\n")
    m2 = ta.write_manifest(out, repo, [], {"station_map": inp}, status="complete", started=m["started"], run=m)
    assert m2["started"] == "2026-01-01T00:00:00+00:00" and m2["finished"] == "2026-01-01T02:00:00+00:00"
    assert m2["commit"] == "a" * 40 and m2["dirty"] is False, (m2["commit"], m2["dirty"])
    assert m2["inputs"] == m["inputs"] and m2["inputs"]["station_map"]["sha256"] != ta.sha256_file(inp)



def test_an_inherited_record_with_no_weak_result_keeps_the_inheritance_and_is_counted(monkeypatch, tmp_path):
    # Review C F2: an empty weak task (no good data) on an inherited record is no evidence: no demotion,
    # but a visible weak_failed and a weak_failures entry.
    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inh = "molde-m1-nor-nhs"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [inh]])

    def weak_task(rid, n_decl, origin_status, *_):
        return rid, [], ["no good data"], {}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in judged}, {}, weak=_Weak())
    r = res.rows[inh]
    assert r.comparator_kind == "inherit" and r.usable and r.annotation == "verified", (r.annotation, r.reason)
    assert r.weak["code"] == "unverified:weak_failed" and "no good data" in r.reason
    assert res.weak_failures == [(inh, "no good data")] and not res.residue


def test_an_inherited_record_whose_own_weak_checks_verify_another_class_is_disputed(monkeypatch, tmp_path):
    # Review C F1: the provider's verdict is utc_plus_1; the record declares UTC and its own weak checks verify
    # utc_instant. Its own evidence rejects the inherited class: disputed, published with the declared UTC.
    from gesla import timebase_weak as tw

    def weak_of(n):
        return tw.WeakResult("verified", "utc_instant", "verified:eot20+served_independent", "E 0, S 0")

    res, inh, seen = _inherit_with_weak(monkeypatch, tmp_path, Correction("utc_plus_n", n=1), weak_of)
    r = res.rows[inh]
    assert r.annotation == "disputed" and not r.usable and r.published.label == "utc_instant", (r.annotation, r.reason)
    assert r.annotation_code == "disputed:inherited_utc_plus_1_vs_weak_utc_instant", r.annotation_code
    assert inh in {c.subject_id for c in res.residue}


# --- GATE-TB confirm A-N1: same-lineage served evidence cannot test an inherited class other than the declared one ---

def _real_weak_path(monkeypatch, tmp_path, cls, e_value, s_value, s_kind="dependent"):
    """3 judged NOR records of class cls and one NOR record that inherits it (declared UTC). The weak checks run
    for real (_weak_task -> served_kind -> assess -> checks -> select_served -> combine); only the fit and the
    phase-check numbers are faked: EOT20 = e_value, the one served station = s_value. The served station's
    origin is a judged NHS record (same lineage: dependent) or, with s_kind "independent", an XYZ record."""
    from gesla import timebase_weak as tw
    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inh, other = "molde-m1-nor-nhs", "oslo-o1-nor-xyz"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [inh]] + [(other, "XYZ")])
    origin = judged[0] if s_kind == "dependent" else other
    monkeypatch.setattr(tw, "fit_features", lambda *a, **k: {"consts": {}, "season": {}, "year": 2010})
    monkeypatch.setattr(tw, "speeds", lambda table: {"M2": 28.984104})
    monkeypatch.setattr(tw, "dst_check", lambda *a, **k: {"value": "no_step", "code": "no_step"})
    monkeypatch.setattr(tw, "phase_check", lambda fit, ref, sp, **k: {"value": e_value if "dist_km" in k else s_value,
                                                                       "tau": 0.0})
    monkeypatch.setattr(ta, "_record", lambda rid: object())
    monkeypatch.setattr(ta, "samples", lambda rec: None)
    monkeypatch.setattr(ta, "audit_span", lambda s: SPAN)

    class Inputs:
        def eot(self, f):
            return {"dist_km": 1.0}

        def zone(self, f):
            return "Europe/Oslo"

        def has(self, f):
            return True

    lineages = {f: tw.Lineage(inv[f].contributor, "originator") for f in inv}
    weak = ta.WeakConfig(Inputs(), {inh: (tw.Served("T1", origin, {}),)}, lineages)
    monkeypatch.setitem(ta._W, "table", None)
    monkeypatch.setitem(ta._W, "weak", weak)
    monkeypatch.setitem(ta._W, "transforms", {})
    official = {f: cls for f in judged}
    official[other] = 0.5                               # other: a verified origin when s_kind is independent
    return _fake_audit_real_weak(monkeypatch, inv, official, weak), inh


def _fake_audit_real_weak(monkeypatch, inv, official, weak):
    """_fake_audit, but _weak_task is the real one (not replaced)."""
    real = ta._weak_task
    res = _fake_audit(monkeypatch, inv, official, {}, weak=weak)
    assert ta._weak_task is real
    return res


@pytest.mark.parametrize("e_value", ["n/a", 1])
def test_an_inherited_utc_plus_1_is_not_disputed_by_its_own_lineages_served_station(monkeypatch, tmp_path, e_value):
    # A same-lineage served station agrees with the DECLARED base by construction (S = 0 here): judged against the
    # inherited +1 h it would read as a dispute. It cannot discriminate, so it carries no weight; the inheritance
    # stands (with EOT20 n/a, or with EOT20 confirming +1).
    res, inh = _real_weak_path(monkeypatch, tmp_path, Correction("utc_plus_n", n=1), e_value, 0)
    r = res.rows[inh]
    assert r.comparator_kind == "inherit" and r.usable and r.correction.label == "utc_plus_1", \
        (r.annotation_code, r.reason)
    assert r.weak["code"].startswith("unverified:"), r.weak
    assert "T1" in str(r.weak["evidence"].get("S_excluded")), r.weak


def test_an_inherited_utc_plus_1_still_yields_to_an_independent_served_station(monkeypatch, tmp_path):
    # An independent served station of another lineage, at 0 h, disagrees with EOT20 at +1: disputed.
    res, inh = _real_weak_path(monkeypatch, tmp_path, Correction("utc_plus_n", n=1), 1, 0, s_kind="independent")
    r = res.rows[inh]
    assert r.annotation == "disputed" and r.published.label == "utc_instant", (r.annotation_code, r.reason)
    assert r.weak["evidence"]["S_independent"] is True and not r.weak["evidence"].get("S_excluded"), r.weak


def test_an_inherited_utc_instant_still_yields_to_its_own_lineages_served_station(monkeypatch, tmp_path):
    # Alvarado at 22434f5: inherited = declared (UTC); a same-lineage served station that is off still disputes it
    # (judged against the declared base, as G04d judges a record without a comparator).
    res, inh = _real_weak_path(monkeypatch, tmp_path, Correction("utc_instant"), "n/a", "off")
    r = res.rows[inh]
    assert r.annotation == "disputed" and r.annotation_code == "disputed:weak_checks_disagree", (r.annotation_code,
                                                                                                r.reason)


def test_the_manifest_commit_is_unknown_when_the_repo_is_not_the_checkouts_toplevel(tmp_path):
    # Review B A3: git -C walks up; a tree that is not the checkout's toplevel (here a subdirectory) must not
    # report the enclosing repository's commit as the code commit.
    sub = Path(__file__).resolve().parents[3] / "scripts"
    m = ta.write_manifest(tmp_path / "out", sub, [], {}, status="running")
    assert m["commit"] == "unknown" and m["dirty"] is True, (m["commit"], m["dirty"])


def _main_with_fakes(monkeypatch, tmp_path, gates, out, result=None, rc=0, metadata="file_name,TIME ZONE HOURS\n"):
    """main() end to end with the heavy parts faked (release, table, controls, inventory, audit); OTC_WORK and
    OTC_GATES_DIR from the environment, as run.sh sets them. Returns the complete manifest."""
    import gesla.timebase_verdict as tv
    work = tmp_path / "work"
    (work / "cache/noaa").mkdir(parents=True, exist_ok=True)
    (work / "cache/noaa/stations.json").write_text('{"stations": []}')
    (work / "cache/dmi-tidewater/events").mkdir(parents=True, exist_ok=True)
    table = tmp_path / "tcd_table.json"
    table.write_text("{}")

    class Release:
        metadata_path = "metadata/GESLA4-1_ALL.csv"

        def __init__(self, root):
            self.root = Path(root)
            (self.root / "metadata").mkdir(parents=True, exist_ok=True)
            (self.root / self.metadata_path).write_text(metadata)
            (self.root / "MANIFEST.json").write_text("{}")

        def metadata(self):
            return {}

    monkeypatch.setenv("OTC_WORK", str(work))
    monkeypatch.setenv("OTC_GATES_DIR", str(gates))
    monkeypatch.setenv("OTC_TCD_TABLE", str(table))
    monkeypatch.setattr(ta, "GeslaRelease", Release)
    monkeypatch.setattr(ta.TcdTable, "load", staticmethod(lambda p: None))
    monkeypatch.setattr(tv, "run_positive_controls", lambda *a, **k: None)
    monkeypatch.setattr(ta, "load_inventory", lambda p: {})
    monkeypatch.setattr(ta, "audit", lambda *a, **k: result or ta.AuditResult({}, [], [], 0, []))
    assert ta.main(["--out-dir", str(out), "--build-date", "20261010"]) == rc
    return json.loads((out / "timebase_manifest.json").read_text())


def test_two_runs_differing_only_in_the_gates_dir_have_distinguishable_manifests(monkeypatch, tmp_path):
    # Review B F1: the gates dir (strong comparators), OTC_WORK, the release root and the confirm-only caches
    # decide the verdicts; the manifest records them (with the args' defaults) and hashes the index files.
    def gates(name, ref):
        g = tmp_path / name / "evid/testplan"
        g.mkdir(parents=True)
        (g / "harmonics_stations.json").write_text("[]")
        (g / "nearest_ref.json").write_text(json.dumps({"S1": {"src": "rws", "ref": ref, "dist_km": 0.1}}))
        return tmp_path / name

    m1 = _main_with_fakes(monkeypatch, tmp_path, gates("g1", "a"), tmp_path / "out")     # same argv both times
    m2 = _main_with_fakes(monkeypatch, tmp_path, gates("g2", "b"), tmp_path / "out")
    def keep(m):
        return {k: v for k, v in m.items() if k not in ("started", "finished", "outputs")}

    assert keep(m1) != keep(m2)
    c1, c2 = m1["config"], m2["config"]
    assert c1["gates_dir"] != c2["gates_dir"] and c1["gates_dir"].endswith("g1")
    assert c1["otc_work"] == str(tmp_path / "work") and c1["release_root"].endswith("inputs/gesla/4.1")
    assert c1["dmi_dir"].endswith("cache/dmi-tidewater/events") and c1["noaa_cache"].endswith("cache/noaa")
    assert c1["args"]["version"] == "4.1" and c1["args"]["build_date"] == "20261010" and "workers" in c1["args"]
    i1, i2 = m1["inputs"], m2["inputs"]
    assert i1["gates_nearest_ref"]["sha256"] != i2["gates_nearest_ref"]["sha256"]
    assert i1["gates_harmonics_stations"]["sha256"] == i2["gates_harmonics_stations"]["sha256"]
    assert i1["noaa_stations"]["path"].endswith("cache/noaa/stations.json") and i1["noaa_stations"]["sha256"]


def test_the_audit_logs_how_many_references_of_each_kind_it_loaded(tmp_path, monkeypatch):
    # Review B A1: an empty confirm-only cache is legal (no references) but must be visible in the run log.
    g = _port_gates(tmp_path / "g")
    d = _dmi_events(tmp_path, "25149", 55.4602, 8.4397)
    refs = ta.References(g, noaa_harcon_dir=_empty_ref_caches(tmp_path)[0] / "harcon", dmi_dir=d)
    assert refs.counts() == {"harness": len(refs.points), "dmi": 1, "noaa_sub": 0}
    lines = []
    monkeypatch.setattr(ta, "References", lambda *a, **k: refs)
    monkeypatch.setattr(ta, "_init", lambda *a: None)
    ta.audit({}, [], release_root="r", table_path="t", gates_dir="g", workers=1, log=lines.append)
    assert any(x.startswith("references: ") and "dmi 1" in x and "noaa_sub 0" in x for x in lines), lines


def test_a_code_crash_in_a_weak_task_is_not_an_expected_fit_failure(monkeypatch, tmp_path):
    # Review B A6: a FitError (daily data, a degenerate design) is a data limit: an inherited record keeps its
    # inheritance (weak_failed). Any other exception in the weak code is a crash: weak_crashed, and an inherited
    # record is no longer verified by its provider, since its own checks never ran because of our code.
    import csv
    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inh, lone = "molde-m1-nor-nhs", "lone-l1-nor-xyz"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [inh]] + [(lone, "XYZ")])

    def weak_task(rid, n_decl, origin_status, *_):
        raise KeyError("S")

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in judged}, {}, weak=_Weak())
    for f in (inh, lone):
        r = res.rows[f]
        assert r.annotation == "unverified" and r.annotation_code == "unverified:weak_crashed", (f, r.annotation_code)
        assert not r.usable and "KeyError" in r.reason, r.reason
    assert "weak_crashed" in res.rows[inh].reason and "inherits utc_instant" in res.rows[inh].reason
    write_csv(res.rows, tmp_path / "t.csv")
    with open(tmp_path / "t.csv", newline="") as fh:
        got = {x["file_id"]: x for x in csv.DictReader(fh)}
    assert got[inh]["weak_eot20"].startswith("weak_crashed: ") and got[lone]["weak_served"].startswith("weak_crashed: ")
    assert sorted(rid for rid, _ in res.weak_failures) == sorted([inh, lone])


def test_the_csv_reason_of_an_inherited_weak_failure_keeps_that_the_inheritance_stands(monkeypatch, tmp_path):
    # Review A A2: the FitError text filled the 240-character CSV reason, so "the inheritance stands" was cut off.
    import csv

    from gesla.fit import FitError
    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inh = "molde-m1-nor-nhs"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [inh]])

    def weak_task(rid, n_decl, origin_status, *_):
        raise FitError("design rank 70 of 92, condition 3.1e16: the record's span cannot separate " + "x" * 200)

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in judged}, {}, weak=_Weak())
    write_csv(res.rows, tmp_path / "t.csv")
    with open(tmp_path / "t.csv", newline="") as fh:
        row = {x["file_id"]: x for x in csv.DictReader(fh)}[inh]
    assert "the inheritance stands" in row["reason"] and "weak_failed" in row["reason"], row["reason"]
    assert row["reason"].startswith("inherits utc_instant"), row["reason"]


def test_the_weak_pass_checks_the_results_it_got_not_the_sizes_it_planned(monkeypatch, tmp_path):
    # Review B A4: done += len(part) could never differ from len(todo). The pass now checks each phase's results:
    # one per record it sent, no stranger.
    from gesla import timebase_weak as tw
    inv = {"a": _info("a", "A", 10.0), "b": _info("b", "B", 20.0)}

    def weak_task(rid, n_decl, origin_status, *_):
        return "a", [], [], {"weak": tw.WeakResult("unverified", "utc_instant", "unverified:weak_insufficient", "-"),
                             "span": SPAN}

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    with pytest.raises(RuntimeError, match="weak evidence .no comparator.: results for"):
        _fake_audit(monkeypatch, inv, {}, {}, weak=_Weak())


def test_a_bare_value_error_in_a_weak_task_is_a_crash_not_a_data_limit(monkeypatch, tmp_path):
    # Fitfix review C M4: ValueError is what Python raises for many code bugs (unpacking, int("x"), our own guards);
    # only the fit call sites turn their ValueError / LinAlgError into FitError (timebase_weak.fit_features).
    judged = [f"bergen{i}-b{i}-nor-nhs" for i in range(3)]
    inh = "molde-m1-nor-nhs"
    inv = _country_inventory(tmp_path, [(f, "NHS") for f in judged + [inh]])

    def weak_task(rid, n_decl, origin_status, *_):
        raise ValueError("not enough values to unpack (expected 3, got 2)")

    weak_task.__name__ = "_weak_task"
    monkeypatch.setattr(ta, "_weak_task", ta._safe(weak_task))
    res = _fake_audit(monkeypatch, inv, {f: 0.5 for f in judged}, {}, weak=_Weak())
    r = res.rows[inh]
    assert r.weak["code"] == "unverified:weak_crashed" and not r.usable, r.weak
    assert ta.EXPECTED_WEAK_ERRORS == frozenset({"FitError"})


def _gates_dir(tmp_path, name="g", ref="a"):
    g = tmp_path / name / "evid/testplan"
    g.mkdir(parents=True, exist_ok=True)
    (g / "harmonics_stations.json").write_text("[]")
    (g / "nearest_ref.json").write_text(json.dumps({"S1": {"src": "rws", "ref": ref, "dist_km": 0.1}}))
    return tmp_path / name


def test_main_exits_non_zero_when_a_weak_task_crashed(monkeypatch, tmp_path):
    # Confirm-2 B-N3: a crashed weak pass must stop a pipeline; the outputs are still written (and name the crash).
    row = _row("x", "no_comparator", kind="none", status="unverified")
    row.weak = {"code": "unverified:weak_crashed", "notes": ["_weak_task failed: KeyError: 'S'"]}
    res = ta.AuditResult({"x": row}, [], [], 0, [("x", "_weak_task failed: KeyError: 'S'")])
    m = _main_with_fakes(monkeypatch, tmp_path, _gates_dir(tmp_path), tmp_path / "out", result=res, rc=3)
    assert m["status"] == "complete" and m["weak_crashes"] == ["x"]
    assert (tmp_path / "out/timebase.csv").exists()


def test_the_manifest_hashes_the_release_metadata_with_the_declared_zones(monkeypatch, tmp_path):
    # Confirm-2 B-N4: the declared TIME ZONE HOURS come from the release metadata; two releases at the same path
    # with different metadata must give different manifests.
    g = _gates_dir(tmp_path)
    m1 = _main_with_fakes(monkeypatch, tmp_path, g, tmp_path / "out", metadata="file_name,TIME ZONE HOURS\na,0\n")
    m2 = _main_with_fakes(monkeypatch, tmp_path, g, tmp_path / "out", metadata="file_name,TIME ZONE HOURS\na,1\n")
    assert m1["inputs"]["gesla_metadata"]["path"].endswith("metadata/GESLA4-1_ALL.csv")
    assert m1["inputs"]["gesla_metadata"]["sha256"] != m2["inputs"]["gesla_metadata"]["sha256"]
    assert m1["inputs"]["gesla_manifest"]["sha256"] == m2["inputs"]["gesla_manifest"]["sha256"]


def test_the_manifest_fingerprints_each_comparator_cache_dir(monkeypatch, tmp_path):
    # Confirm-2 A-A3: the caches were recorded by path only; a changed DMI event file (or any cache file) must
    # change the manifest. Fingerprint: sorted (relative name, size, mtime) of every file under the dir.
    import os
    g = _gates_dir(tmp_path)
    (g / "refs/rws/events").mkdir(parents=True)
    (g / "refs/rws/events/a.json").write_text("[]")
    out = tmp_path / "out"
    m1 = _main_with_fakes(monkeypatch, tmp_path, g, out)
    ev = tmp_path / "work/cache/dmi-tidewater/events/25149.json"
    ev.write_text('{"events": [1]}')
    os.utime(ev, (1_700_000_000, 1_700_000_000))
    m2 = _main_with_fakes(monkeypatch, tmp_path, g, out)
    c1, c2 = m1["caches"], m2["caches"]
    assert set(c1) == {"gates_refs", "dmi_events", "noaa_cache"}, c1
    assert c1["dmi_events"]["files"] == 0 and c2["dmi_events"]["files"] == 1
    assert c1["dmi_events"]["sha256"] != c2["dmi_events"]["sha256"]
    assert c1["gates_refs"] == c2["gates_refs"] and c1["gates_refs"]["files"] == 1
    assert c1["noaa_cache"]["path"].endswith("cache/noaa")


@pytest.mark.parametrize("e_value", [0, -1])
def test_eot20_alone_does_not_overturn_an_inherited_class(monkeypatch, tmp_path, e_value):
    # Confirm-2 A-A1 (documented rule, backlog): inherited utc_plus_1, declared UTC, the dependent served station
    # excluded, EOT20 disagreeing (0 h or -1 h): combine never disputes on EOT20 alone, so the inheritance stands.
    res, inh = _real_weak_path(monkeypatch, tmp_path, Correction("utc_plus_n", n=1), e_value, 0)
    r = res.rows[inh]
    assert r.comparator_kind == "inherit" and r.correction.label == "utc_plus_1", (r.annotation_code, r.reason)
    assert r.weak["code"] == "unverified:weak_insufficient", r.weak


# --- fitfix review C M3: dir_fingerprint cannot be fooled by links, permissions or the root's kind ------------------

def test_dir_fingerprint_records_missing_plain_file_and_broken_link_as_distinct_states(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    kinds = {k: ta.dir_fingerprint(tmp_path / k)["kind"] for k in ("absent", "file", "dangling")}
    assert kinds == {"absent": "missing", "file": "not_dir", "dangling": "broken_link"}, kinds
    d = tmp_path / "d"
    d.mkdir()
    fp = ta.dir_fingerprint(d)
    assert fp["kind"] == "dir" and fp["files"] == 0 and fp["realpath"] == str(d.resolve())


def test_dir_fingerprint_changes_when_an_existing_file_changes(tmp_path):
    # Fitfix review B (a): the same name with a new size, or the same size with a new mtime.
    import os
    d = tmp_path / "events"
    d.mkdir()
    p = d / "a.json"
    p.write_text("[1]")
    os.utime(p, (1_700_000_000, 1_700_000_000))
    fp0 = ta.dir_fingerprint(d)["sha256"]
    p.write_text("[12]")
    os.utime(p, (1_700_000_000, 1_700_000_000))
    fp1 = ta.dir_fingerprint(d)["sha256"]
    os.utime(p, (1_700_000_001, 1_700_000_001))
    fp2 = ta.dir_fingerprint(d)["sha256"]
    assert len({fp0, fp1, fp2}) == 3


def test_dir_fingerprint_follows_a_linked_subdir_and_records_link_targets(tmp_path):
    import os
    real = tmp_path / "real"
    real.mkdir()
    (real / "x.json").write_text("1")
    d = tmp_path / "cache"
    d.mkdir()
    (d / "own.json").write_text("0")
    (d / "sub").symlink_to(real)
    fp = ta.dir_fingerprint(d)
    assert fp["files"] == 2 and fp["links"] == 1, fp            # the linked subdir's file is counted
    (real / "x.json").write_text("22")
    assert ta.dir_fingerprint(d)["sha256"] != fp["sha256"]     # a change behind the link is seen
    # a file link retargeted to another file of the same size and mtime is seen (the target is hashed)
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("A")
    b.write_text("B")
    for p in (a, b):
        os.utime(p, (1_700_000_000, 1_700_000_000))
    (d / "ln").symlink_to(a)
    f1 = ta.dir_fingerprint(d)["sha256"]
    (d / "ln").unlink()
    (d / "ln").symlink_to(b)
    assert ta.dir_fingerprint(d)["sha256"] != f1
    before = ta.dir_fingerprint(d)
    (d / "gone").symlink_to(tmp_path / "nowhere")              # a dangling link inside is recorded, not skipped
    g = ta.dir_fingerprint(d)
    assert before["broken_links"] == 0 and g["broken_links"] == 1 and g["sha256"] != before["sha256"]


def test_dir_fingerprint_fails_loudly_on_an_unreadable_subdir(tmp_path):
    import os
    d = tmp_path / "cache"
    (d / "locked").mkdir(parents=True)
    (d / "locked/x.json").write_text("1")
    os.chmod(d / "locked", 0)
    try:
        if os.access(d / "locked", os.R_OK):
            pytest.skip("running with privileges that read a mode-000 dir")
        with pytest.raises(PermissionError, match="locked"):
            ta.dir_fingerprint(d)
    finally:
        os.chmod(d / "locked", 0o755)


# --- fitfix review C M2: a consumer of the audit's outputs refuses an incomplete or crashed audit -------------------

def _stub_audit_out(tmp_path, **over):
    out = tmp_path / "audit"
    out.mkdir(exist_ok=True)
    (out / "timebase.csv").write_text("file_id\nx\n")
    m = {"status": "complete", "weak_crashes": [], "commit": "a" * 40,
         "outputs": {"timebase.csv": ta.sha256_file(out / "timebase.csv")}}
    m.update(over)
    m = {k: v for k, v in m.items() if v is not ...}
    (out / "timebase_manifest.json").write_text(json.dumps(m))
    return out


def test_check_audit_outputs_accepts_a_complete_audit_without_crashes(tmp_path):
    m = ta.check_audit_outputs(_stub_audit_out(tmp_path))
    assert m["status"] == "complete" and m["weak_crashes"] == []


@pytest.mark.parametrize("over, why", [
    ({"status": "running"}, "status running"),
    ({"weak_crashes": ["x"]}, "1 weak task.*crashed"),
    ({"weak_crashes": ...}, "no weak_crashes"),
    ({"outputs": {"timebase.csv": "0" * 64}}, "timebase.csv.*sha256"),
])
def test_check_audit_outputs_refuses_an_incomplete_crashed_or_changed_audit(tmp_path, over, why):
    with pytest.raises(ValueError, match=why):
        ta.check_audit_outputs(_stub_audit_out(tmp_path, **over))


def test_check_audit_outputs_refuses_a_dir_without_a_manifest(tmp_path):
    (tmp_path / "timebase.csv").write_text("file_id\n")
    with pytest.raises(ValueError, match="no timebase_manifest.json"):
        ta.check_audit_outputs(tmp_path)


def test_main_exits_zero_when_the_weak_failures_are_only_data_limits(monkeypatch, tmp_path):
    # Fitfix review B (b): weak_failed (FitError) is expected; only a crash makes main exit 3.
    row = _row("x", "no_comparator", kind="none", status="unverified")
    row.weak = {"code": "unverified:weak_failed", "notes": ["_weak_task failed: FitError: design rank 70 of 92"]}
    res = ta.AuditResult({"x": row}, [], [], 0, [("x", "_weak_task failed: FitError: design rank 70 of 92")])
    m = _main_with_fakes(monkeypatch, tmp_path, _gates_dir(tmp_path), tmp_path / "out", result=res, rc=0)
    assert m["status"] == "complete" and m["weak_crashes"] == [] and m["weak_failures"]
    assert ta.check_audit_outputs(tmp_path / "out")["status"] == "complete"
