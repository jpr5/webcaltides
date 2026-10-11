"""The fast monthly lag path gives the same numbers as the reference, BIT FOR BIT.

gesla.timebase_measure slices each month by binary search and scores all lags of a month in one
array operation; gesla.timebase_verdict computes each corrected series' monthly lags once per
evaluation. tests/reference_monthly_lags.py keeps the code as it was before (one lag at a time,
whole-record masks, rows recomputed per call). These tests compare the two:

- synthetic pairs (always run): hourly against 10-min, gaps longer than the 70-min interpolation
  limit, short months, a comparator that covers only part of a month (some lags matched, some
  not), unsorted stamps (the fallback path), shifts of 0, +30 and -60 min;
- the per-lag correlations themselves (all 361 lags, NaN where a lag is not scored);
- evaluate() with the fast path against evaluate() with the reference (the whole Verdict, all
  evidence, by repr), on a record with an isolated off month (the dropped-months path);
- real GESLA record pairs (when the verified GESLA cache is present; skipped otherwise): hourly
  NOAA/UHSLC, 10-min CMEMS against hourly WSV, RWS against RWS_HIST, both directions, the same
  three shifts, and evaluate() on each.

"Bit for bit": every float is compared by float.hex(), so -0.0 vs 0.0 or a last-bit difference fails.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

import gesla.timebase_verdict as tv
from gesla.read_gesla import GeslaRelease, otc_work
from gesla.record import GaugeRecord
from gesla.tcd_table import TcdTable
from gesla.timebase_measure import LAGS_MIN, MIN_SAMPLES, MonthLag, monthly_lags, samples

import real_data
import reference_monthly_lags as ref

HOUR = 3600
SHIFTS = (0, 30 * 60, -60 * 60)
W_M2 = 2 * math.pi * 28.9841042 / 360 / HOUR
W_K1 = 2 * math.pi * 15.0410686 / 360 / HOUR


def unix(y: int, m: int = 1, d: int = 1, hh: int = 0) -> int:
    return int(np.datetime64(f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:00", "s").astype(np.int64))


def tide(t: np.ndarray) -> np.ndarray:
    t = np.asarray(t, dtype=np.float64)
    return 1.0 * np.cos(W_M2 * t) + 0.3 * np.cos(W_K1 * t + 0.7)


def noisy(t: np.ndarray, seed: int) -> np.ndarray:
    """tide() plus noise: real correlations are not exactly 1, so the rounding of every sum shows."""
    return tide(t) + np.random.default_rng(seed).normal(0.0, 0.05, len(t))


def fingerprint(rows: list[MonthLag]) -> list[tuple]:
    return [(r.year, r.month, float(r.lag_min).hex(), float(r.r).hex(), r.n, r.n_matched) for r in rows]


def assert_same_rows(got: list[MonthLag], want: list[MonthLag], what: str) -> None:
    fg, fw = fingerprint(got), fingerprint(want)
    assert len(fg) == len(fw), f"{what}: {len(fg)} months, reference {len(fw)}"
    diff = [(a, b) for a, b in zip(fg, fw) if a != b]
    assert not diff, f"{what}: {len(diff)} of {len(fw)} months differ, first {diff[0]}"


# --- synthetic fixtures ------------------------------------------------------------------------

def _drop(t: np.ndarray, lo: int, hi: int) -> np.ndarray:
    return t[(t < lo) | (t >= hi)]


def synthetic_pairs() -> dict[str, tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]]:
    rng = np.random.default_rng(7)
    start, end = unix(2015, 11), unix(2016, 6)
    rec_t = np.arange(start, end, HOUR, dtype=np.int64)                         # hourly record
    ref_t = np.arange(start - 6 * HOUR, end + 6 * HOUR, 600, dtype=np.int64)    # 10-min comparator
    # gaps: comparator gaps longer than 70 min (not interpolated) and shorter ones (interpolated)
    ref_g = _drop(_drop(ref_t, unix(2016, 1, 10), unix(2016, 1, 10, 5)), unix(2016, 2, 3), unix(2016, 2, 3, 1))
    # a short month: the record has only ~8 days of 2016-03 (below and around MIN_SAMPLES)
    rec_g = _drop(_drop(rec_t, unix(2016, 3, 9), unix(2016, 4)), unix(2015, 12, 20), unix(2015, 12, 22))
    # the comparator covers only 2016-04-01..04-09 08:00: the hourly April record matches 199..203
    # samples across the +-180 min lags, so some lags reach MIN_SAMPLES (200) and others fall below it
    # and must stay NaN (the per-lag path inside a block; asserted in the per-lag test)
    ref_part = ref_g[(ref_g < unix(2016, 4, 9, 8)) | (ref_g >= unix(2016, 5))]
    # irregular record stamps (jittered by up to 5 min; sorted and unique)
    jit = np.unique(rec_t + rng.integers(-300, 300, len(rec_t)))
    out = {
        "hourly-vs-10min": ((rec_t, noisy(rec_t, 1)), (ref_t, noisy(ref_t, 2))),
        "gaps-short-months": ((rec_g, noisy(rec_g, 3)), (ref_g, noisy(ref_g, 4))),
        "partial-comparator": ((rec_g, noisy(rec_g, 5)), (ref_part, noisy(ref_part, 6))),
        "irregular": ((jit, noisy(jit, 7)), (ref_g, noisy(ref_g, 8))),
        "10min-vs-hourly": ((ref_g, noisy(ref_g, 9)), (rec_t, noisy(rec_t, 10))),
        **edge_pairs(),
    }
    return out


def edge_pairs() -> dict:
    """1-min records across the 2016-01/02 boundary: a record sample sits on each month's inclusive
    end bound (23:59 on the last day). The comparator (10-min) is thinned so that the stamps exactly
    on the padded window edges (Feb's start - 4 h, Jan's end + 4 h) are the only ones bracketing the
    record's stamps at its true lag (+175 min at the start edge, -175 min at the end edge): a slice
    that drops an edge stamp changes n, n_matched and the lag."""
    from gesla.timebase_measure import COMPARATOR_PAD_S, _month_bounds
    rec_t = np.arange(unix(2016, 1, 25), unix(2016, 2, 5), 60, dtype=np.int64)
    feb0 = _month_bounds(2016, 2)[0] - COMPARATOR_PAD_S         # 2016-01-31 20:00
    jan1 = _month_bounds(2016, 1)[1] + COMPARATOR_PAD_S         # 2016-02-01 03:59
    grid = np.arange(unix(2016, 1, 24), unix(2016, 2, 6), 600, dtype=np.int64)
    # Feb's start edge: 20:00 then 21:10 (a 70-min gap, still interpolated); Jan's end edge: 02:50 then 03:59 (69 min)
    thin = grid[~(((grid > feb0) & (grid < feb0 + 70 * 60)) | ((grid > jan1 - 69 * 60) & (grid < jan1 + 60 * 60)))]
    comp_t = np.union1d(thin, [jan1])
    out = {}
    for name, lag in (("1min-start-edge", 175), ("1min-end-edge", -175)):
        # rec(t) ~ ref(t - lag): the record is the comparator's tide shifted by the lag
        out[name] = ((rec_t, noisy(rec_t - lag * 60, 30 + lag % 7)), (comp_t, tide(comp_t)))
    return out


@pytest.mark.parametrize("name", list(synthetic_pairs()))
@pytest.mark.parametrize("shift", SHIFTS, ids=lambda s: f"{s // 60:+d}min")
def test_monthly_lags_match_the_reference_bit_for_bit(name, shift):
    (rt, rv), comp = synthetic_pairs()[name]
    rec = (rt + shift, rv)
    want = ref.monthly_lags(rec, comp)
    assert want, f"{name}: the fixture has no audited month"
    assert_same_rows(monthly_lags(rec, comp), want, f"{name} shift {shift}")


def test_unsorted_stamps_take_the_reference_path_and_warn():
    """Samples are sorted by contract; unsorted stamps still give the reference numbers (the masks
    over the whole series), but never silently: a RuntimeWarning names the slow path."""
    (rt, rv), comp = synthetic_pairs()["gaps-short-months"]
    p = np.random.default_rng(3).permutation(len(rt))
    rec = (rt[p], rv[p])
    with pytest.warns(RuntimeWarning, match="not sorted"):
        got = monthly_lags(rec, comp)
    assert_same_rows(got, ref.monthly_lags(rec, comp), "unsorted record")
    q = np.random.default_rng(4).permutation(len(comp[0]))
    with pytest.warns(RuntimeWarning, match="not sorted"):
        got = monthly_lags((rt, rv), (comp[0][q], comp[1][q]))
    assert_same_rows(got, ref.monthly_lags((rt, rv), (comp[0][q], comp[1][q])), "unsorted comparator")


def test_sorted_stamps_do_not_warn():
    rec, comp = synthetic_pairs()["gaps-short-months"]
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        monthly_lags(rec, comp)


def test_float_stamps_score_as_int64_seconds():
    """Stamps are int64 Unix seconds (Samples). lag_minutes takes any other dtype as the int64 seconds
    it holds (truncated), so a float32 copy or a float64 copy with sub-second parts scores exactly as
    those int64 stamps do in the reference. (The reference itself, given float32 stamps, does float32
    arithmetic on ~1.7e9 s, 128 s resolution: that is not a number to match.)"""
    (rt, rv), (bt, bv) = synthetic_pairs()["irregular"]
    rv = noisy(rt - 33 * 60, 21)                       # a real lag, so sub-minute stamp errors show
    frac = np.random.default_rng(5).uniform(0.0, 0.99, len(rt))
    cases = {"float32": (rt.astype(np.float32), bt.astype(np.float32)),
             "float64+fraction": (rt + frac, bt.astype(np.float64))}
    for name, (fa, fb) in cases.items():
        want = ref.monthly_lags((fa.astype(np.int64), rv), (fb.astype(np.int64), bv))
        assert want, name
        assert_same_rows(monthly_lags((fa, rv), (fb, bv)), want, f"{name} stamps")


def test_month_edge_fixtures_reach_the_edges():
    """The edge fixtures do what they claim: a record sample on Jan's end bound, and at the true lag the
    comparator stamps on the padded edges are used (matched at the best lag needs them)."""
    from gesla.timebase_measure import COMPARATOR_PAD_S, _month_bounds
    pairs = edge_pairs()
    (rt, _), (bt, _) = pairs["1min-end-edge"]
    assert _month_bounds(2016, 1)[1] in rt
    assert _month_bounds(2016, 1)[1] + COMPARATOR_PAD_S in bt and _month_bounds(2016, 2)[0] - COMPARATOR_PAD_S in bt
    for name, (y, m), lag in (("1min-start-edge", (2016, 2), 175), ("1min-end-edge", (2016, 1), -175)):
        rows = {(r.year, r.month): r for r in ref.monthly_lags(*pairs[name])}
        assert abs(rows[(y, m)].lag_min - lag) < 2, (name, rows[(y, m)])
        assert rows[(y, m)].n_matched == rows[(y, m)].n, (name, rows[(y, m)])   # the edge stamps bracket every sample


def test_corrected_rows_keeps_each_shift_apart():
    """The row memo is keyed by the whole correction (kind and n, zone), never the kind alone: utc_plus_1
    and utc_plus_2 on one evaluation each get their own reference rows."""
    from gesla.timebase_verdict import Correction, _corrected_rows, _Evidence
    t = np.arange(unix(2015, 1), unix(2015, 7), HOUR, dtype=np.int64)
    rec = _record("late2", t, noisy(t - 120 * 60, 17))
    ct = np.arange(unix(2014, 12, 31), unix(2015, 7, 1, 6), 600, dtype=np.int64)
    comp = (ct, tide(ct))
    ev = _Evidence(rec, comp, None, (int(t[0]), int(t[-1]) + 1), None)
    for corr in (Correction("utc_plus_n", n=1), Correction("utc_plus_n", n=2), Correction("utc_plus_n", n=-1),
                 Correction("utc_mean_start60"), Correction("utc_plus_n", n=1, months_drop=((2015, 3),))):
        assert_same_rows(_corrected_rows(corr, ev), ref.corrected_rows(corr, ev), corr.label)
    # the record is 120 min late: only utc_plus_2 corrects it to within the +-3 min band
    assert all(abs(r.lag_min) <= 3 for r in _corrected_rows(Correction("utc_plus_n", n=2), ev))
    assert all(abs(r.lag_min) > 3 for r in _corrected_rows(Correction("utc_plus_n", n=1), ev))


def test_corrected_rows_refuses_a_step():
    """The row memo is for shift classes only (a step's parts are recomputed); a step must not reach it."""
    from gesla.timebase_verdict import Correction, _corrected_rows
    step = Correction("step", parts=((unix(2015, 3), Correction("utc_plus_n", n=1)),))
    with pytest.raises(ValueError, match="step"):
        _corrected_rows(step, None)


@pytest.mark.parametrize("name", ["hourly-vs-10min", "partial-comparator", "irregular"])
def test_every_lag_correlation_matches_the_reference(name):
    """All 361 per-lag correlations and matched counts of each month, not only the chosen lag."""
    from gesla.timebase_measure import COMPARATOR_PAD_S, _correlations, _month_bounds
    (at, av), (bt, bv) = synthetic_pairs()[name]
    checked = below = 0
    for y, m in ((2015, 12), (2016, 1), (2016, 2), (2016, 4)):
        s, e = _month_bounds(y, m)
        ma = (at >= s) & (at <= e)
        mb = (bt >= s - COMPARATOR_PAD_S) & (bt <= e + COMPARATOR_PAD_S)
        want_r, want_n = ref.correlations(at[ma], av[ma], bt[mb], bv[mb])
        got_r, got_n = _correlations(at[ma], av[ma], bt[mb], bv[mb], LAGS_MIN)
        assert np.array_equal(got_n, want_n), f"{name} {y}-{m:02d}: matched counts differ"
        assert [float(x).hex() for x in got_r] == [float(x).hex() for x in want_r], \
            f"{name} {y}-{m:02d}: correlations differ"
        checked += int(np.isfinite(want_r).sum())
        below += int(((want_n > 0) & (want_n < MIN_SAMPLES)).sum())
    assert checked > 361, f"{name}: too few scored lags ({checked}) to test anything"
    if name == "partial-comparator":
        assert below > 0, "the fixture no longer has a lag with 0 < matched < MIN_SAMPLES (the NaN guard)"


# --- evaluate(): the memoised rows against the reference rows ---------------------------------

def _record(name: str, t: np.ndarray, h: np.ndarray, interval: int = 60) -> GaugeRecord:
    t = np.asarray(t, dtype=np.int64)
    return GaugeRecord(record_id=name, source="gesla", source_version="test", contributor="X", originator=True,
                       licence_class="x", lat=0.0, lon=0.0, gauge_type="Coastal", sampling="instantaneous",
                       interval_min=interval, declared_time_base=None, member_sha256="0" * 64, times_s=t,
                       heights_m=np.asarray(h, dtype=np.float64), good=np.ones(len(t), dtype=bool))


# _CLEAN_COMPARATOR: noise on a 10-min comparator biases an hourly record's lag by about 3 min (at
# lag 0 the hourly stamps fall on comparator samples, full noise; between samples the interpolation
# averages the noise down, so the correlation peaks off zero). The verdict fixtures use a noise-free
# comparator so that they reach the classes they test; the noisy pairs above test the numbers.


def _reference_evaluate(monkeypatch, *args, **kw):
    """evaluate() with the reference monthly lags and without the row memo (9dda78a's computation)."""
    with monkeypatch.context() as mp:
        mp.setattr(tv, "monthly_lags", ref.monthly_lags)
        mp.setattr(tv, "_corrected_rows", ref.corrected_rows, raising=False)
        return tv.evaluate(*args, **kw)


def test_evaluate_matches_the_reference_with_dropped_months(monkeypatch):
    """A record 60 min late (utc_plus_1) with one isolated month 20 min further off: months_bad, so
    the pass rule reuses the corrected rows minus the dropped month."""
    t = np.arange(unix(2014), unix(2016), HOUR, dtype=np.int64)
    late = np.where((t >= unix(2015, 3)) & (t < unix(2015, 4)), 80 * 60, 60 * 60)
    rec = _record("late", t, noisy(t - late, 11))
    ct = np.arange(unix(2013, 12, 31), unix(2016, 1, 1, 6), 600, dtype=np.int64)
    comp = (ct, tide(ct))         # a noise-free comparator: see _CLEAN_COMPARATOR
    got = tv.evaluate(rec, comp, comparator_id="c", table=None)
    want = _reference_evaluate(monkeypatch, rec, comp, comparator_id="c", table=None)
    assert want.verdict == "months_bad:2015-03" and want.passed, want.line()
    assert repr(got) == repr(want)


def test_evaluate_matches_the_reference_when_unexplained(monkeypatch):
    """Two adjacent off months: not isolated; every candidate is tried and rejected."""
    t = np.arange(unix(2014), unix(2016), HOUR, dtype=np.int64)
    off = (t >= unix(2015, 3)) & (t < unix(2015, 5))
    rec = _record("off", t, noisy(t - np.where(off, 25 * 60, 0), 13))
    ct = np.arange(unix(2013, 12, 31), unix(2016, 1, 1, 6), 600, dtype=np.int64)
    comp = (ct, tide(ct))
    got = tv.evaluate(rec, comp, comparator_id="c", table=None)
    want = _reference_evaluate(monkeypatch, rec, comp, comparator_id="c", table=None)
    assert want.verdict == "unexplained", want.line()
    assert repr(got) == repr(want)


# --- real GESLA records ------------------------------------------------------------------------

REAL_PAIRS = (("newport-8452660-usa-noaa", "newport_ri-253-usa-uhslc_fd"),          # hourly NOAA / UHSLC
              ("borkumtg-bor-deu-cmems", "borkumfischerbalje-9340020-deu-wsv"),     # 10-min CMEMS / hourly WSV
              ("texel_noordzee-tex-nld-rws", "texel_noordzee-texnze-nld-rws_hist"))  # RWS / RWS_HIST
REAL_YEARS = 4          # the last 4 years of each record's audit span that the comparator overlaps: real gaps and short months, bounded run time


@pytest.fixture(scope="module")
def real():
    rel_root = otc_work() / "inputs" / "gesla" / "4.1"
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    missing = [str(p) for p in (rel_root / "GESLA4.1_ALL.zip", rel_root / "MANIFEST.json", dump) if not p.exists()]
    real_data.require(missing, "real-record inputs")
    release = GeslaRelease(rel_root)
    return release, TcdTable.load(dump), {}


def _rec(real, name: str) -> GaugeRecord:
    cache = real[2]
    if name not in cache:
        cache[name] = real[0].record(name, roles={})
    return cache[name]


def _span(s, comp) -> tuple[int, int]:
    """The last REAL_YEARS of the record's audit span that the comparator overlaps."""
    from gesla.timebase_audit import audit_span
    got = audit_span(s)
    assert got is not None, "a real fixture record without good data"
    lo, hi = got
    hi = min(hi, int(comp[0][-1]) + 1)
    return max(lo, int(comp[0][0]), hi - REAL_YEARS * 365 * 86400), hi


@pytest.mark.parametrize("pair", [p for a, b in REAL_PAIRS for p in ((a, b), (b, a))], ids=lambda p: p[0].split("-")[0])
def test_real_pairs_match_the_reference_bit_for_bit(real, pair, monkeypatch):
    from gesla.timebase_audit import zone_for
    x, y = _rec(real, pair[0]), _rec(real, pair[1])
    sx, sy = samples(x), samples(y)
    span = _span(sx, sy)
    m = (sx[0] >= span[0]) & (sx[0] < span[1])
    for shift in SHIFTS:
        rec = (sx[0][m] + shift, sx[1][m])
        want = ref.monthly_lags(rec, sy)
        assert want, f"{pair} shift {shift}: no audited month"
        assert_same_rows(monthly_lags(rec, sy), want, f"{pair} shift {shift}")
    zone = zone_for(x.record_id)
    got = tv.evaluate(x, sy, comparator_id=y.record_id, table=real[1], zone=zone, span=span)
    want = _reference_evaluate(monkeypatch, x, sy, comparator_id=y.record_id, table=real[1], zone=zone, span=span)
    assert repr(got) == repr(want), want.line()
