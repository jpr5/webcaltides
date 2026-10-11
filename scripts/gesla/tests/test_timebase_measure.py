"""G04a: time-base measurements (BP §2 step 2).

Two kinds of test:
- synthetic tests (always run, no data): a known shift is recovered by the monthly lag and
  by the per-constituent lag; a 60-min boxcar copy is found by the sampling test; a local
  record with a missing spring-forward hour is found by the gap test.
- real-record tests (run when the verified GESLA cache and the TCD dump are present; skipped
  otherwise, with the reason): the T4 check's numbers on the real WSV, CMEMS, BfG and UHSLC
  records (verify/ctrl41.out, xcorr_raw41.out, xcorr_raw40.out, dst_gap.out, avg41.out) and the
  PoC's per-constituent lags (fits/timebase.out). When the T4 outputs themselves are present
  (OTC_T4_DIR, default ~/.local/share/copilotkit/cr/webcaltides-ticon4-check/verify), the monthly
  lag series is also compared with them month by month.
"""

from __future__ import annotations

import math
import os
import re
import statistics
from pathlib import Path

import numpy as np
import pytest

from gesla.read_gesla import GeslaRelease, ShaMismatch, otc_work
from gesla.record import GaugeRecord
from gesla.tcd_table import TcdTable
from gesla.timebase_measure import (CONSTITUENTS, constituent_lags, lag_minutes, monthly_lags, read_fixture,
                                    samples, sampling_test, spring_gap, summary)

T4_DIR = Path(os.environ.get("OTC_T4_DIR",
                             Path.home() / ".local/share/copilotkit/cr/webcaltides-ticon4-check/verify"))
HOUR = 3600
DAY = 86400


def unix(y: int, m: int = 1, d: int = 1, hh: int = 0, mm: int = 0) -> int:
    return int(np.datetime64(f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:{mm:02d}", "s").astype(np.int64))


def record(name: str, t: np.ndarray, h: np.ndarray, good: np.ndarray | None = None) -> GaugeRecord:
    t = np.asarray(t, dtype=np.int64)
    h = np.asarray(h, dtype=np.float64)
    return GaugeRecord(record_id=name, source="gesla", source_version="test", contributor="X", originator=True,
                       licence_class="x", lat=0.0, lon=0.0, gauge_type="Coastal", sampling="instantaneous",
                       interval_min=int(np.median(np.diff(t)) // 60), declared_time_base=None,
                       member_sha256="0" * 64, times_s=t, heights_m=h,
                       good=np.ones(len(t), dtype=bool) if good is None else good)


# Two-constituent tide (M2 1 m, K1 0.3 m), analytic, so shifts and boxcar means are exact.
W_M2 = 2 * math.pi * 28.9841042 / 360 / HOUR      # rad/s
W_K1 = 2 * math.pi * 15.0410686 / 360 / HOUR


def tide(t: np.ndarray) -> np.ndarray:
    t = np.asarray(t, dtype=np.float64)
    return 1.0 * np.cos(W_M2 * t) + 0.3 * np.cos(W_K1 * t + 0.7)


def tide_mean60(t: np.ndarray) -> np.ndarray:
    """Mean of tide() over [t, t + 60 min)."""
    t = np.asarray(t, dtype=np.float64)
    out = np.zeros_like(t)
    for a, w, p in ((1.0, W_M2, 0.0), (0.3, W_K1, 0.7)):
        out += a * (np.sin(w * (t + HOUR) + p) - np.sin(w * t + p)) / (w * HOUR)
    return out


# --- synthetic -------------------------------------------------------------------------------

def test_lag_recovers_a_known_shift():
    ref_t = np.arange(unix(2016), unix(2016, 3), 600, dtype=np.int64)          # 10-min comparator
    rec_t = np.arange(unix(2016), unix(2016, 3), HOUR, dtype=np.int64)          # hourly record
    rec = (rec_t, tide(rec_t - 17 * 60))                                         # 17 min late
    ref = (ref_t, tide(ref_t))
    lag, r, n, matched = lag_minutes(rec, ref, unix(2016, 1, 1), unix(2016, 2, 1) - 60)
    assert lag == pytest.approx(17.0, abs=0.2)
    assert r > 0.999 and n == 31 * 24 and matched == n - 1      # Jan 1 00:00 - 17 min is before the data


def test_lag_is_none_with_too_few_samples():
    t = np.arange(unix(2016), unix(2016) + 100 * HOUR, HOUR, dtype=np.int64)
    assert lag_minutes((t, tide(t)), (t, tide(t)), unix(2016), unix(2016, 2)) == (None, None, 100, 0)


def test_lag_is_none_when_no_sample_can_be_matched_not_a_crash():
    # Both sides have >= 200 samples in the window, but no record sample can be interpolated from the
    # comparator: every correlation is NaN. Real cases: Papeete REFMAR vs CMEMS 2008-12 (the record
    # covers Dec 19-31, the comparator Nov 30-Dec 12) and Wemeldinge RWS vs RWS_HIST 1970-1988 (both
    # 3-hourly, every comparator gap > 70 min). The month has no lag; it is not an exception.
    rec_t = np.arange(unix(2008, 12, 19, 21), unix(2009, 1, 1, 3), HOUR, dtype=np.int64)
    ref_t = np.arange(unix(2008, 11, 30, 20), unix(2008, 12, 12), HOUR, dtype=np.int64)
    assert len(rec_t) >= 200 and len(ref_t) >= 200
    lo, hi = unix(2008, 12), unix(2009, 1) - 60
    assert lag_minutes((rec_t, tide(rec_t)), (ref_t, tide(ref_t)), lo, hi) == (None, None, len(rec_t[rec_t <= hi]), 0)
    t3 = np.arange(unix(1970), unix(1971), 3 * HOUR, dtype=np.int64)
    assert lag_minutes((t3, tide(t3)), (t3, tide(t3)), unix(1970), unix(1970, 2) - 60)[:2] == (None, None)
    assert monthly_lags((t3, tide(t3)), (t3, tide(t3))) == []
    # a month with matches next to one without: the matched month keeps its lag
    t = np.concatenate([np.arange(unix(2008, 11), unix(2008, 12), HOUR), rec_t]).astype(np.int64)
    r = np.concatenate([np.arange(unix(2008, 11), unix(2008, 12), HOUR), ref_t]).astype(np.int64)
    rows = monthly_lags((t, tide(t)), (np.unique(r), tide(np.unique(r))), years=[2008], months=[11, 12])
    assert [(x.year, x.month) for x in rows] == [(2008, 11)] and abs(rows[0].lag_min) < 0.2


def test_lag_is_none_when_the_comparator_only_brushes_the_month():
    # Repro A: the record is hourly through Dec 2008; the 1-min comparator covers only Nov 30 20:00 to
    # Dec 1 00:30. Its 271 samples are in the padded window, but at most 4 record samples can be matched
    # at any lag, and a correlation of 2-4 samples is +-1. Before the fix this was (30.0, 1.0, 744).
    rec_t = np.arange(unix(2008, 11), unix(2009, 1), HOUR, dtype=np.int64)
    ref_t = np.arange(unix(2008, 11, 30, 20), unix(2008, 12, 1, 0, 31), 60, dtype=np.int64)
    got = lag_minutes((rec_t, tide(rec_t)), (ref_t, tide(ref_t)), unix(2008, 12), unix(2009, 1) - 60)
    assert got[:3] == (None, None, 744)


def test_lag_is_none_when_the_comparator_ends_before_the_record_starts():
    # Repro B (Papeete REFMAR vs CMEMS 2008-12 shape): the record is hourly from Dec 19 21:00 to the
    # month end, the comparator hourly and 40 min shifted, ending 78 min before the record starts. Only
    # the large positive lags reach 1-2 record samples. Before the fix this was (140.x, 1.0, 291).
    rec_t = np.arange(unix(2008, 12, 19, 21), unix(2009, 1), HOUR, dtype=np.int64)
    end = int(rec_t[0]) - 78 * 60
    ref_t = np.arange(end - (18 * 24 + 23) * HOUR, end + 1, HOUR, dtype=np.int64)
    got = lag_minutes((rec_t, tide(rec_t)), (ref_t, tide(ref_t - 40 * 60)), unix(2008, 12), unix(2009, 1) - 60)
    assert got[:3] == (None, None, 291)


def _late_start_comparator(rec_t: np.ndarray, cliff_lag: int) -> np.ndarray:
    """A 1-min comparator that starts so that exactly MIN_SAMPLES (200) record samples can be matched
    at cliff_lag and 199 at cliff_lag + 1: every lag above cliff_lag has too few matches."""
    first = int(rec_t[-200]) - 60 * (cliff_lag + 1)
    return np.arange(first, unix(2009, 1, 1, 4), 60, dtype=np.int64)


def test_peak_next_to_an_unmatched_lag_is_not_refined():
    # The comparator starts late in the month, so lags above +30 match too few record samples (NaN).
    # The record is 30 min late: the peak is at +30, next to a NaN neighbour. The parabola through a
    # NaN is NaN; the peak lag is kept as it is.
    rec_t = np.arange(unix(2008, 12), unix(2009, 1), HOUR, dtype=np.int64)
    ref_t = _late_start_comparator(rec_t, 30)
    lag, r, n, matched = lag_minutes((rec_t, tide(rec_t - 30 * 60)), (ref_t, tide(ref_t)),
                                     unix(2008, 12), unix(2009, 1) - 60)
    assert lag == 30.0 and r > 0.999
    assert n == 744 and matched == 200           # n is the record's samples, matched those at the peak lag


def test_lag_is_none_when_too_few_lags_can_be_scored():
    # Only lags -180 and -179 match >= 200 record samples: a peak at the edge of a two-lag window is
    # not a measurement.
    rec_t = np.arange(unix(2008, 12), unix(2009, 1), HOUR, dtype=np.int64)
    ref_t = _late_start_comparator(rec_t, -179)
    got = lag_minutes((rec_t, tide(rec_t)), (ref_t, tide(ref_t)), unix(2008, 12), unix(2009, 1) - 60)
    assert got[:3] == (None, None, 744)


def test_monthly_lags_carry_the_matched_count():
    t = np.arange(unix(2016), unix(2016, 3), HOUR, dtype=np.int64)
    rows = monthly_lags((t, tide(t)), (t, tide(t)), years=[2016], months=[1, 2])
    # January's first record sample (Jan 1 00:00) has no comparator sample before it to interpolate
    # from (the data start there); February's are all matched (January's tail is in the padding).
    assert [(r.month, r.n, r.n_matched) for r in rows] == [(1, 744, 743), (2, 696, 696)]
    assert all(abs(r.lag_min) < 0.01 for r in rows)


def test_monthly_lags_and_summary():
    t = np.arange(unix(2015), unix(2017), HOUR, dtype=np.int64)
    rows = monthly_lags((t, tide(t + 30 * 60)), (t, tide(t)))                  # 30 min early
    assert [(r.year, r.month) for r in rows][:2] == [(2015, 1), (2015, 2)] and len(rows) == 24
    assert all(abs(r.lag_min + 30.0) < 0.3 for r in rows)
    s = summary(rows)
    assert s["n_months"] == 24 and s["jan"]["n"] == 2 and s["jul"]["n"] == 2
    assert s["median"] == pytest.approx(-30.0, abs=0.3)
    assert s["jan"]["median"] == pytest.approx(-30.0, abs=0.3)
    only = monthly_lags((t, tide(t)), (t, tide(t)), years=[2016], months=[1, 7])
    assert [(r.year, r.month) for r in only] == [(2016, 1), (2016, 7)]


def test_sampling_test_finds_the_hour_mean():
    ref_t = np.arange(unix(2016), unix(2016, 4), 600, dtype=np.int64)
    rec_t = np.arange(unix(2016), unix(2016, 4), HOUR, dtype=np.int64)
    res = sampling_test((rec_t, tide_mean60(rec_t)), (ref_t, tide(ref_t)), unix(2016), unix(2016, 4) - 60)
    # The 60-sample mean over the 1-min grid is centred 30 s early (T4 avg41.py's method): ~0.3 cm here.
    assert res["mean60"]["resid_cm"] < 0.5
    # A pure two-line tide cannot separate mean60 from inst_t30 (the boxcar of a sinusoid is a
    # 30-min shift times a factor); the real-record test (avg41) does. Here: both beat inst_t.
    assert max(res["mean60"]["resid_cm"], res["inst_t30"]["resid_cm"]) < 0.1 * res["inst_t"]["resid_cm"]
    assert res["ref_interval_min"] == 10
    inst = sampling_test((rec_t, tide(rec_t)), (ref_t, tide(ref_t)), unix(2016), unix(2016, 4) - 60)
    assert inst["inst_t"]["resid_cm"] < 0.01 < inst["mean60"]["resid_cm"]


def test_spring_gap_on_a_local_record():
    # Hourly stamps in Europe/Berlin wall time 2014-2016: the 02:00 hour on the last Sunday in
    # March does not exist (flagged bad, as WSV did); a UTC record has it.
    t = np.arange(unix(2014), unix(2017), HOUR, dtype=np.int64)
    gaps = [unix(2014, 3, 30, 2), unix(2015, 3, 29, 2), unix(2016, 3, 27, 2)]
    good = ~np.isin(t, gaps)
    local = spring_gap(record("local", t, tide(t), good), "Europe/Berlin")
    assert (local.hits, local.years) == (3, 3) and (local.control_hits, local.controls) == (0, 9)
    utc = spring_gap(record("utc", t, tide(t)), "Europe/Berlin")
    assert (utc.hits, utc.years) == (0, 3)
    # A stamp that is simply absent (not flagged) is a gap too.
    absent = spring_gap(record("absent", t[good], tide(t[good])), "Europe/Berlin")
    assert absent.hits == 3


def test_constituent_lags_recover_a_shift(real_or_fake_table):
    table = real_or_fake_table
    t = np.arange(unix(2015), unix(2016, 7), HOUR, dtype=np.int64)
    from gesla.fit import design
    names = ("M2", "S2", "K1", "O1", "M4", "M6")
    amps = np.array([1.0, 0.3, 0.2, 0.15, 0.05, 0.02])

    def h(tt):
        d = design(table, names, tt)
        return d[:, 0::2] @ amps * 1.0 + d[:, 1::2] @ (amps * 0.5)

    ref = record("ref", t, h(t))
    rec = record("rec", t, h(t - 12 * 60) * 0.98)                                 # 12 min late, x0.98
    out = constituent_lags(table, rec, ref, unix(2015), unix(2016, 7))
    for n in ("M2", "S2", "K1", "O1"):
        assert out[n]["lag_min"] == pytest.approx(12.0, abs=0.1), n
        assert out[n]["amp_ratio"] == pytest.approx(0.98, abs=0.002), n


# --- real records ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real():
    rel_root = otc_work() / "inputs" / "gesla" / "4.1"
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    missing = [str(p) for p in (rel_root / "GESLA4.1_ALL.zip", rel_root / "MANIFEST.json", dump) if not p.exists()]
    if missing:
        pytest.skip(f"real-record inputs not in the cache: {missing}")
    return GeslaRelease(rel_root), TcdTable.load(dump)


@pytest.fixture(scope="module")
def real_or_fake_table():
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    if not dump.exists():
        pytest.skip(f"TCD dump not in the cache: {dump}")
    return TcdTable.load(dump)


_records: dict[str, GaugeRecord] = {}


def rec41(real, name: str) -> GaugeRecord:
    if name not in _records:
        _records[name] = real[0].record(name, roles={})
    return _records[name]


def rec40(real, name: str) -> GaugeRecord:
    key = "4.0/" + name
    if key not in _records:
        _records[key] = read_fixture(real[0], name)
    return _records[key]


HEL_WSV, HEL_CMEMS = "helgolandbinnenhafen-9510070-deu-wsv", "helgolandtg-hel-deu-cmems"
BHV_WSV, BHV_CMEMS = "bhvalterleuchtturm-4990010-deu-wsv", "bremerhaventg-bre-deu-cmems"
CUX_WSV, CUX_UHSLC = "cuxhavensteubenhft-5990020-deu-wsv", "cuxhaven-825-deu-uhslc_fd"
CUX_BFG, CUX_CMEMS = "cuxhaven_steubenhoft-cux-deu-bfg", "cuxhaventg-cux-deu-cmems"
TRD_NHS, TRD_CMEMS = "trondheim-trd-nor-nhs", "trondheimtg-tro-nor-cmems"
XCORR_PAIRS = ((HEL_WSV, HEL_CMEMS), (BHV_WSV, BHV_CMEMS), (CUX_WSV, CUX_UHSLC))
XCORR_YEARS, XCORR_MONTHS = range(2000, 2027), (1, 4, 7, 10, 11)       # as xcorr_months.py
CTRL_YEARS, CTRL_MONTHS = (2005, 2010, 2015, 2020, 2024), (1, 7)       # as ctrl41.py
# T4 months that lag_minutes no longer scores: the comparator covers too little of the month for
# MIN_SAMPLES matched record samples at any lag. Helgoland CMEMS 2000-07 covers only Jun 30 20:00 to
# Jul 3 04:40 (<= 56 of the 744 WSV samples can be matched); T4 scored it from those.
T4_THIN = {HEL_WSV: {(2000, 7)}}


def t4_xcorr(ver: str) -> dict[str, list[tuple[int, int, float, int]]] | None:
    path = T4_DIR / f"xcorr_{ver}.out"
    if not path.exists():
        return None
    out, cur = {}, None
    for line in path.read_text().splitlines():
        if line.startswith("=="):
            cur = line.split()[2]
            out[cur] = []
        elif line.strip():
            y, m, lag, _r, n = line.split()
            out[cur].append((int(y), int(m), float(lag), int(n)))
    return out


def test_fixture_sha_is_checked(real, tmp_path):
    release = real[0]
    entry = release.files[f"fixtures/gesla-4.0-wsv/{HEL_WSV}"]
    assert entry["sha256"].startswith("ab973059")
    with pytest.raises(KeyError, match="no-such"):
        read_fixture(release, "no-such-record")
    bad = GeslaRelease.__new__(GeslaRelease)
    bad.__dict__.update(release.__dict__)
    bad.files = dict(release.files)
    bad.files[f"fixtures/gesla-4.0-wsv/{HEL_WSV}"] = dict(entry, sha256="0" * 64)
    with pytest.raises(ShaMismatch, match=HEL_WSV):
        read_fixture(bad, HEL_WSV)


def test_wsv41_monthly_lag_is_minus_29_5(real, capsys):
    """xcorr_raw41.out: WSV-4.1 against same-gauge CMEMS / UHSLC, months 1,4,7,10,11 of 2000-2026."""
    t4 = t4_xcorr("raw41")
    lines = []
    for a, b in XCORR_PAIRS:
        rows = monthly_lags(samples(rec41(real, a)), samples(rec41(real, b)), years=XCORR_YEARS, months=XCORR_MONTHS)
        s = summary(rows)
        within = sum(abs(r.lag_min + 29.5) <= 0.5 for r in rows)
        lines.append(f"{a} vs {b}: months {len(rows)}, median {s['median']:+.1f}, jan {s['jan']['median']:+.1f} "
                     f"(n {s['jan']['n']}), jul {s['jul']['median']:+.1f} (n {s['jul']['n']}), "
                     f"within -29.5+-0.5: {within}/{len(rows)}, min {min(r.lag_min for r in rows):+.1f}, "
                     f"max {max(r.lag_min for r in rows):+.1f}")
        # The T4 data itself: Helgoland's July median is -30.0 and only ~half its months are within
        # -29.5 +- 0.5 (the CMEMS comparator wanders -23..-44); every median is within 1 min.
        assert abs(s["median"] + 29.5) <= 0.5, lines[-1]
        assert abs(s["jan"]["median"] + 29.5) <= 1.0 and abs(s["jul"]["median"] + 29.5) <= 1.0, lines[-1]
        if t4 is not None:
            want = {(y, m): (lag, n) for y, m, lag, n in t4[a] if (y, m) not in T4_THIN.get(a, ())}
            got = {(r.year, r.month): (r.lag_min, r.n) for r in rows}
            assert set(got) == set(want), f"{a}: months differ from T4"
            worst = max(abs(got[k][0] - want[k][0]) for k in want)
            lines.append(f"  vs T4 xcorr_raw41.out: {len(want)} months, max |dlag| {worst:.2f} min")
            assert worst <= 0.15, lines[-1]
    # Bremerhaven: every month; Cuxhaven: every month but the comparator's own bad 2024-07 (UHSLC_FD
    # is off by ~50 min that month, ctrl41.out) and 2007-11 (-28.6).
    bhv = monthly_lags(samples(rec41(real, BHV_WSV)), samples(rec41(real, BHV_CMEMS)),
                       years=XCORR_YEARS, months=XCORR_MONTHS)
    assert all(abs(r.lag_min + 29.5) <= 0.5 for r in bhv)
    with capsys.disabled():
        print("\n" + "\n".join(lines))


def test_ctrl41_cuxhaven_wsv_every_month(real, capsys):
    """ctrl41.out: Cuxhaven WSV-4.1 vs BfG and vs CMEMS, Jan and Jul of 2005..2024: -29.5 +- 0.5;
    the BfG and CMEMS records themselves are on UTC instants (0.0 against UHSLC, but UHSLC 2024-07)."""
    lines = []
    for a, b in ((CUX_WSV, CUX_BFG), (CUX_WSV, CUX_CMEMS)):
        rows = monthly_lags(samples(rec41(real, a)), samples(rec41(real, b)), years=CTRL_YEARS, months=CTRL_MONTHS)
        lines.append(f"{a} vs {b}: " + " ".join(f"{r.year}-{r.month:02d}:{r.lag_min:+.1f}" for r in rows))
        assert len(rows) >= 9 and all(abs(r.lag_min + 29.5) <= 0.5 for r in rows), lines[-1]
    for a in (CUX_BFG, CUX_CMEMS):
        rows = monthly_lags(samples(rec41(real, a)), samples(rec41(real, CUX_UHSLC)), years=CTRL_YEARS,
                            months=CTRL_MONTHS)
        lines.append(f"{a} vs {CUX_UHSLC}: " + " ".join(f"{r.year}-{r.month:02d}:{r.lag_min:+.1f}" for r in rows))
        assert all(abs(r.lag_min) <= 0.2 for r in rows if (r.year, r.month) != (2024, 7)), lines[-1]
    with capsys.disabled():
        print("\n" + "\n".join(lines))


def test_wsv40_fixtures_are_local_time(real, capsys):
    """xcorr_raw40.out: GESLA-4.0 WSV fixtures vs the 4.1 CMEMS / UHSLC records: +60 min in January,
    +120 min in July (CET / CEST stamps)."""
    t4 = t4_xcorr("raw40")
    lines = []
    for a, b in XCORR_PAIRS:
        rows = monthly_lags(samples(rec40(real, a)), samples(rec41(real, b)), years=XCORR_YEARS, months=XCORR_MONTHS)
        s = summary(rows)
        lines.append(f"4.0 {a} vs 4.1 {b}: jan {s['jan']['median']:+.1f} (n {s['jan']['n']}), "
                     f"jul {s['jul']['median']:+.1f} (n {s['jul']['n']})")
        assert abs(s["jan"]["median"] - 60) <= 1.0 and abs(s["jul"]["median"] - 120) <= 1.0, lines[-1]
        if t4 is not None:
            want = {(y, m): lag for y, m, lag, _n in t4[a] if (y, m) not in T4_THIN.get(a, ())}
            got = {(r.year, r.month): r.lag_min for r in rows}
            assert set(got) == set(want)
            worst = max(abs(got[k] - want[k]) for k in want)
            lines.append(f"  vs T4 xcorr_raw40.out: {len(want)} months, max |dlag| {worst:.2f} min")
            assert worst <= 0.15, lines[-1]
    with capsys.disabled():
        print("\n" + "\n".join(lines))


def test_spring_gap_wsv40_vs_41(real, capsys):
    """dst_gap.out: the 02:00 hour on the last Sunday in March is missing in 26 of 26 years in the
    4.0 WSV fixtures, never on control Sundays, and never in 4.1."""
    lines = []
    for name in (HEL_WSV, BHV_WSV, CUX_WSV):
        g40 = spring_gap(rec40(real, name), "Europe/Berlin", years=range(2000, 2026))
        g41 = spring_gap(rec41(real, name), "Europe/Berlin", years=range(2000, 2026))
        lines.append(f"{name}: 4.0 {g40.hits}/{g40.years} (controls {g40.control_hits}/{g40.controls}); "
                     f"4.1 {g41.hits}/{g41.years} (controls {g41.control_hits}/{g41.controls})")
        assert (g40.hits, g40.years, g40.control_hits, g40.controls) == (26, 26, 0, 78), lines[-1]
        assert (g41.hits, g41.years, g41.control_hits) == (0, 26, 0), lines[-1]
    with capsys.disabled():
        print("\n" + "\n".join(lines))


def test_sampling_test_bremerhaven_2016(real, capsys):
    """avg41.out: Bremerhaven WSV-4.1 against CMEMS (10-min) in 2016: the hour mean over [t, t+60)
    fits to 0.42 cm, the instant at t+30 to 1.32 cm, the instant at t to 32.58 cm."""
    res = sampling_test(samples(rec41(real, BHV_WSV)), samples(rec41(real, BHV_CMEMS)),
                        unix(2016), unix(2016, 12, 31, 23, 59))
    line = (f"Bremerhaven WSV-4.1 vs CMEMS 2016 (ref interval {res['ref_interval_min']} min): "
            + "; ".join(f"{k} slope {res[k]['slope']:.4f} resid {res[k]['resid_cm']:.2f} cm n {res[k]['n']}"
                        for k in ("mean60", "inst_t30", "inst_t")))
    with capsys.disabled():
        print("\n" + line)
    assert res["ref_interval_min"] == 10
    assert res["mean60"]["resid_cm"] == pytest.approx(0.42, abs=0.02) and res["mean60"]["n"] == 8782, line
    assert res["mean60"]["slope"] == pytest.approx(1.0006, abs=0.0005), line
    assert res["inst_t30"]["resid_cm"] == pytest.approx(1.32, abs=0.02), line
    assert res["inst_t"]["resid_cm"] == pytest.approx(32.58, abs=0.05) and res["inst_t"]["n"] == 8783, line


# PoC fits/timebase.out, mode "none", 2014-01-01..2026-01-01: lag (min) / amplitude ratio.
POC_TIMEBASE = {
    (HEL_WSV, HEL_CMEMS): {"M2": (-29.9, 0.994), "S2": (-30.2, 0.993), "K1": (-27.0, 1.005), "O1": (-29.8, 1.010),
                           "M4": (-26.0, 0.995), "M6": (-30.0, 0.919)},
    (BHV_WSV, BHV_CMEMS): {"M2": (-29.5, 0.989), "S2": (-29.5, 0.988), "K1": (-28.9, 0.997), "O1": (-29.8, 0.995),
                           "M4": (-29.6, 0.958), "M6": (-29.4, 0.905)},
    (TRD_NHS, TRD_CMEMS): {"M2": (-0.1, 1.000), "S2": (-0.1, 1.000), "K1": (-0.1, 0.999), "O1": (-0.1, 1.000),
                           "M4": (-0.0, 0.999), "M6": (-0.0, 1.005)},
}


@pytest.mark.parametrize("pair", list(POC_TIMEBASE), ids=lambda p: p[0].split("-")[0])
def test_constituent_lags_reproduce_the_poc(real, pair, capsys):
    release, table = real
    out = constituent_lags(table, rec41(real, pair[0]), rec41(real, pair[1]), unix(2014), unix(2026))
    assert tuple(out) == CONSTITUENTS
    line = f"{pair[0]} vs {pair[1]} 2014-2025: " + " ".join(
        f"{n} {out[n]['lag_min']:+.1f}/{out[n]['amp_ratio']:.3f}" for n in CONSTITUENTS)
    with capsys.disabled():
        print("\n" + line)
    for n, (lag, ratio) in POC_TIMEBASE[pair].items():
        assert out[n]["lag_min"] == pytest.approx(lag, abs=0.2), f"{n}: {line}"
        assert out[n]["amp_ratio"] == pytest.approx(ratio, abs=0.003), f"{n}: {line}"


def test_lag_is_none_when_only_the_comparator_is_short():
    # Review C m4: a dense 1-min record (10080 samples in a week) against an hourly comparator with fewer than
    # MIN_SAMPLES samples in the padded window: every lag still has >= 200 matched record samples, so only the
    # comparator-side floor keeps this week from a lag built on < 200 comparator points.
    start, end = unix(2016), unix(2016) + 7 * 24 * HOUR - 60
    rec_t = np.arange(start, end + 1, 60, dtype=np.int64)
    ref_t = np.arange(start - 4 * HOUR, end + 4 * HOUR, HOUR, dtype=np.int64)
    assert len(ref_t) < 200 <= len(rec_t)
    got = lag_minutes((rec_t, tide(rec_t - 17 * 60)), (ref_t, tide(ref_t)), start, end)
    assert got == (None, None, len(rec_t), 0), got
