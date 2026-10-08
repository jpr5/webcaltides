"""G03b: GaugeRecord, the GESLA reader, QC and the fit basis.

Two kinds of test:
- synthetic tests (always run, no data): the record invariants, the reader on a small
  GESLA-format file, the QC steps, and exact recovery of known constants from the basis;
- real-record tests (run when the verified GESLA cache, the TCD dump and the NOAA harcon
  reference are present; skipped otherwise, with the reason): Boston 8443970 from GESLA 4.1.

OTC_FIT_OMIT_V0U=1 zeroes V0+u in the TCD table the real-record tests use. That is the
negative control: the strict Boston check must then fail (M2 phase off NOAA by > 10 deg).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from gesla.fit import fit_despiked, lsq, year_and_hours
from gesla.qc import Series, clean, despike_mask, to_hourly
from gesla.read_gesla import (ContributorRole, GeslaRelease, ShaMismatch, otc_work, parse_metadata,
                              parse_record)
from gesla.record import GaugeRecord
from gesla.tcd_table import TcdTable

REPO = Path(__file__).resolve().parents[3]
TCD_PATH = REPO / "data" / "latest-xtide.tcd"
OMIT_V0U = os.environ.get("OTC_FIT_OMIT_V0U") == "1"

# --- synthetic fixtures ------------------------------------------------------------------------

FAKE_TABLE = {
    "years": [2019, 2021],
    "tcd_sha256": "0" * 64,
    "aliases": {},
    "constituents": {
        "M2": {"speed": 28.9841042, "v0u": [10.0, 200.0, 333.0], "f": [0.97, 1.0, 1.03]},
        "K1": {"speed": 15.0410686, "v0u": [50.0, 120.0, 300.0], "f": [1.10, 1.0, 0.92]},
    },
}


def fake_table() -> TcdTable:
    return TcdTable(FAKE_TABLE)


def make_record(**over) -> GaugeRecord:
    t = np.arange(0, 10 * 3600, 3600, dtype=np.int64)
    kw = dict(record_id="x-1-usa-noaa", source="gesla", source_version="4.1", contributor="NOAA",
              originator=True, licence_class="A", lat=1.0, lon=2.0, gauge_type="Coastal",
              sampling="instantaneous", interval_min=60, declared_time_base=None, member_sha256="a" * 64,
              times_s=t, heights_m=np.zeros(len(t)), good=np.ones(len(t), dtype=bool))
    kw.update(over)
    return GaugeRecord(**kw)


GESLA_TEXT = """# FORMAT VERSION 5.0
# SITE NAME Test
# LATITUDE      54.18052298
# LONGITUDE      7.89080439
# TIME ZONE HOURS 0
# NULL VALUE 301.0001
# GAUGE TYPE Coastal
# COLUMN 1 Date yyyy/mm/dd
2020/01/01 00:00:00     1.0000 1 1
2020/01/01 01:00:00   301.0001 1 1
2020/01/01 02:00:00     1.2000 2 1
2020/01/01 03:00:00     1.3000 0 0
2020/01/01 04:00:00     1.4000 0 1
2020/01/01 04:00:00     9.9000 0 1
2020/01/01 05:00:00     1.5000 1 1
"""
GESLA_META = {"CONTRIBUTOR (ABBREVIATED)": "WSV", "GAUGE TYPE": "Coastal", "NULL VALUE": "301.0001"}


# --- GaugeRecord ---------------------------------------------------------------------------------

def test_record_accepts_a_valid_record_and_freezes_arrays():
    r = make_record()
    assert len(r) == 10
    with pytest.raises(ValueError):
        r.heights_m[0] = 1.0
    with pytest.raises(Exception):
        r.lat = 3.0  # type: ignore[misc]


@pytest.mark.parametrize("over, msg", [
    ({"source": "noaa"}, "source"),
    ({"source": "rws"}, "must start with 'rws:'"),
    ({"sampling": "hourly"}, "sampling"),
    ({"interval_min": 0}, "interval_min"),
    ({"interval_min": 6.0}, "interval_min"),
    ({"member_sha256": "ABC"}, "member_sha256"),
    ({"declared_time_base": "utc_instant"}, "declared_time_base"),
    ({"times_s": np.arange(10, dtype=np.float64)}, "times_s"),
    ({"heights_m": np.zeros(9)}, "differ in length"),
    ({"heights_m": np.full(10, np.nan)}, "non-finite"),
    ({"lat": 91.0}, "lat"),
])
def test_record_rejects_broken_invariants(over, msg):
    with pytest.raises(ValueError, match=msg):
        make_record(**over)


def test_record_allows_adapter_records():
    r = make_record(record_id="rws:HOEKVHLD", source="rws", source_version="20261008",
                    declared_time_base="utc_instant", sampling="mean")
    assert r.declared_time_base == "utc_instant"


# --- reader ----------------------------------------------------------------------------------------

def test_reader_nulls_flags_and_sha():
    data = GESLA_TEXT.encode()
    sha = hashlib.sha256(data).hexdigest()
    r = parse_record("test-1-deu-wsv", data, GESLA_META, {"WSV": ContributorRole(True, "A")},
                     source_version="4.1", expected_sha256=sha)
    assert r.member_sha256 == sha and r.contributor == "WSV" and r.originator and r.licence_class == "A"
    assert r.lat == pytest.approx(54.18052298) and r.gauge_type == "Coastal" and r.interval_min == 60
    assert r.declared_time_base is None and r.sampling == "instantaneous"
    assert np.isnan(r.heights_m[1])                       # header NULL VALUE 301.0001
    assert r.good.tolist() == [True, False, False, False, True, True, True]
    assert r.times_s[0] == 1577836800                     # as stamped
    with pytest.raises(ShaMismatch):
        parse_record("test-1-deu-wsv", data, GESLA_META, {}, source_version="4.1", expected_sha256="f" * 64)


def test_reader_unlisted_contributor():
    data = GESLA_TEXT.encode()
    r = parse_record("t", data, GESLA_META, {}, source_version="4.1",
                     expected_sha256=hashlib.sha256(data).hexdigest())
    assert (r.originator, r.licence_class) == (False, "unlisted")


def test_metadata_header_missing_comma_and_cr_lines():
    raw = ("FILE NAME,GAUGE TYPE,OVERALL RECORD QUALITYDOWNLOAD LINK\r"
           "a-1,Coastal,No obvious issues,https://x/a-1\r").encode()
    m = parse_metadata(raw)
    assert m["a-1"]["OVERALL RECORD QUALITY"] == "No obvious issues"
    assert m["a-1"]["DOWNLOAD LINK"] == "https://x/a-1"


# --- QC --------------------------------------------------------------------------------------------

def test_clean_drops_bad_and_duplicates_keeping_first():
    data = GESLA_TEXT.encode()
    r = parse_record("t", data, GESLA_META, {}, source_version="4.1",
                     expected_sha256=hashlib.sha256(data).hexdigest())
    s = clean(r)
    assert s.heights_m.tolist() == [1.0, 1.4, 1.5]
    assert s.stats == {"rows": 7, "non_null": 6, "flag_dropped": 2, "duplicates_dropped": 1,
                       "hours_off_modal_dropped": 0, "hourly": 3}


def test_hourly_keeps_the_modal_minute():
    t = np.arange(0, 6 * 3600, 600, dtype=np.int64) + 300     # 10-min steps at :05, :15, ...
    t = np.concatenate([t, [6 * 3600 + 5 * 60 + 600 * k for k in range(0, 1)]])
    h = np.arange(len(t), dtype=np.float64)
    tt, _ = to_hourly(t, h)
    assert set(((tt // 60) % 60).tolist()) == {5}
    assert len(tt) == 7


def test_correction_hook_is_applied_after_hourly():
    r = make_record()
    s = clean(r, lambda t, h: (t - 1800, h))
    assert s.times_s[0] == -1800


def test_despike_drops_an_injected_spike():
    t = np.arange(0, 2000 * 3600, 3600, dtype=np.int64)
    rng = np.random.default_rng(0)
    res = rng.normal(0, 0.01, len(t))
    res[1000] = 1.0
    keep, mad = despike_mask(t, res)
    assert not keep[1000] and keep[500] and 0.005 < mad < 0.02


def test_despike_keeps_samples_with_too_few_neighbours():
    """An isolated fragment cannot be assessed: it is kept and counted, not dropped as a spike."""
    t = np.arange(0, 2000 * 3600, 3600, dtype=np.int64)
    t = np.concatenate([t, [t[-1] + 100 * 3600, t[-1] + 101 * 3600]])
    rng = np.random.default_rng(1)
    res = rng.normal(0, 0.01, len(t))
    res[1000] = 1.0
    keep, mad = despike_mask(t, res)
    assert keep[-1] and keep[-2] and not keep[1000]
    assert int((~keep).sum()) == 1


def test_despike_six_hourly_series_is_not_wiped_out():
    t = np.arange(0, 400 * 6 * 3600, 6 * 3600, dtype=np.int64)
    keep, mad = despike_mask(t, np.random.default_rng(2).normal(0, 0.01, len(t)))
    assert keep.all() and not np.isfinite(mad)
    t = t + 1577836800                                           # 2020, inside the fake dump
    h = 0.5 * np.cos(np.radians(28.9841042 * t / 3600.0))
    fit = fit_despiked(fake_table(), ["M2"], Series("six-hourly", t, h))
    assert fit.n == len(t) and fit.stats["spikes_dropped"] == 0
    assert fit.stats["spikes_unassessed"] == len(t) and fit.stats["spike_mad_m"] is None
    json.loads(fit.to_json())


def test_despike_zero_mad_drops_nothing():
    t = np.arange(0, 500 * 3600, 3600, dtype=np.int64)
    res = np.zeros(len(t))
    res[100] = 1e-9
    keep, mad = despike_mask(t, res)
    assert keep.all() and mad == 0.0


def test_hourly_keeps_the_hourly_part_of_a_mixed_record():
    """Hourly at :30 for 300 h, then 15-min data for 500 h: one value for every hour."""
    hourly = np.arange(0, 300 * 3600, 3600, dtype=np.int64) + 1800
    sub = np.arange(300 * 3600, 800 * 3600, 900, dtype=np.int64)
    t = np.concatenate([hourly, sub])
    tt, _ = to_hourly(t, np.zeros(len(t)))
    assert len(tt) == 800
    assert np.array_equal(tt[:300], hourly)
    assert set((tt[300:] % 3600).tolist()) == {0}


def test_hourly_keeps_one_value_per_hour_for_sub_minute_data():
    t = np.arange(0, 5 * 3600, 15, dtype=np.int64)
    tt, _ = to_hourly(t, np.zeros(len(t)))
    assert tt.tolist() == [0, 3600, 7200, 10800, 14400]


def test_hourly_counts_hours_without_the_modal_offset():
    r = make_record(times_s=np.concatenate([np.arange(0, 50 * 3600, 600), np.arange(50 * 3600, 60 * 3600, 600) + 300]).astype(np.int64),
                    heights_m=np.zeros(360), good=np.ones(360, dtype=bool), interval_min=10)
    s = clean(r)
    assert s.stats["hourly"] == 50 and s.stats["hours_off_modal_dropped"] == 10


@pytest.mark.parametrize("step, expect", [(15, 1), (90, 2), (60, 1), (3600, 60)])
def test_reader_interval_for_sub_minute_and_odd_steps(step, expect):
    lines = "".join(f"2020/01/01 {(k * step) // 3600:02d}:{(k * step) // 60 % 60:02d}:{(k * step) % 60:02d}"
                    f"     1.0000 1 1\n" for k in range(20))
    data = (GESLA_TEXT.split("2020/01/01 00:00:00")[0] + lines).encode()
    r = parse_record("t", data, GESLA_META, {}, source_version="4.1",
                     expected_sha256=hashlib.sha256(data).hexdigest())
    assert r.interval_min == expect


# --- fit basis (synthetic) -------------------------------------------------------------------------

def test_year_and_hours():
    y, h = year_and_hours(np.array([1577836800, 1577836800 + 3600 * 24 * 366 + 7200], dtype=np.int64))
    assert y.tolist() == [2020, 2021] and h.tolist() == [0.0, 2.0]


def test_basis_recovers_known_constants_across_year_boundaries():
    table = fake_table()
    t = np.arange(1546300800, 1640995200, 3600, dtype=np.int64)    # 2019-01-01 .. 2022-01-01
    years, hours = year_and_hours(t)
    truth = {"M2": (1.3744, 109.355), "K1": (0.141, 205.2)}
    h = 5.0 + 0.004 * (t - t.mean()) / 3600 / 8766
    for n, (a, g) in truth.items():
        c = table[n]
        i = years - table.first_year
        v = np.array(c.v0u)[i]
        f = np.array(c.f)[i]
        h = h + f * a * np.cos(np.radians(c.speed * hours + v - g))
    r = lsq(table, ["M2", "K1"], t, h)
    for n, (a, g) in truth.items():
        assert r.amplitude_m[n] == pytest.approx(a, abs=1e-9)
        assert r.phase_deg[n] == pytest.approx(g, abs=1e-7)
    assert r.z0 == pytest.approx(5.0, abs=1e-9) and r.trend_m_per_yr == pytest.approx(0.004, abs=1e-9)
    out = json.loads(r.to_json())
    assert out["constituents"]["M2"] == {"amp_m": 1.3744, "phase_deg": 109.355}
    assert r.to_json() == lsq(table, ["K1", "M2"], t, h).to_json()   # stable output


def test_basis_rejects_years_outside_the_dump():
    t = np.array([1609459200 + 366 * 86400 * k for k in range(4)], dtype=np.int64)
    with pytest.raises(ValueError, match="outside the TCD dump"):
        lsq(fake_table(), ["M2"], t, np.zeros(len(t)))


# --- real record: Boston 8443970, GESLA 4.1 ---------------------------------------------------------

BOSTON = "boston-8443970-usa-noaa"
# The PoC's constituent list for Boston (cr/webcaltides-ownfit/fits/fits.json, boston.primary).
POC_BOSTON = ["M2", "S2", "K1", "O1", "N2", "P1", "K2", "Q1", "M4", "SA", "SSA", "NU2", "MU2", "L2", "2N2",
              "MS4", "MN4", "M6", "J1", "OO1", "T2", "LDA2", "S1", "M3", "MK3", "2MK3", "S4", "M8", "2Q1",
              "RHO1", "MM", "MF", "MSF", "R2", "2SM2", "S6", "M1", "EPS2", "MKS2", "SIG1", "N4", "S3",
              "2MK5", "2MO5", "2MS6"]
NOAA_MAIN = ("M2", "S2", "N2", "K1", "O1")
YEARS_19 = 19 * 365.25 * 86400


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def real():
    rel_root = otc_work() / "inputs" / "gesla" / "4.1"
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    harcon = otc_work() / "gates" / "refs" / "noaa" / "harcon" / "harcon_8443970.json"
    missing = [str(p) for p in (rel_root / "GESLA4.1_ALL.zip", rel_root / "MANIFEST.json", dump, harcon)
               if not p.exists()]
    if missing:
        pytest.skip(f"real-record inputs not in the cache: {missing}")
    table = TcdTable.load(dump, tcd_sha256=_sha256(TCD_PATH))
    if OMIT_V0U:
        data = json.loads(dump.read_text())
        for c in data["constituents"].values():
            c["v0u"] = [0.0] * len(c["v0u"])
        table = TcdTable(data)
    release = GeslaRelease(rel_root)
    record = release.record(BOSTON, roles={})
    noaa = {c["name"]: (c["amplitude"], c["phase_GMT"])
            for c in json.loads(harcon.read_text())["HarmonicConstituents"]}
    s = clean(record)
    window = s.times_s >= s.times_s.max() - YEARS_19
    s19 = Series(s.record_id, s.times_s[window], s.heights_m[window], dict(s.stats, in_window=int(window.sum())))
    fit = fit_despiked(table, POC_BOSTON, s19)
    # The PoC dropped samples it could not assess for spikes (fewer than 6 in their 25-h window);
    # the default keeps them. Boston has 3 (an isolated 3-hour fragment, 2026-02-24).
    poc_fit = fit_despiked(table, POC_BOSTON, s19, keep_unassessed=False)
    print(f"\nBOSTON n={fit.n} stats={fit.stats} omit_v0u={OMIT_V0U}")
    for n in NOAA_MAIN:
        a, g = fit.amplitude_m[n] * 100, fit.phase_deg[n]
        na, ng = noaa[n][0] * 100, noaa[n][1]
        print(f"  {n:3s} fit {a:7.2f} cm {g:7.2f} deg | NOAA {na:7.2f} cm {ng:7.2f} deg | "
              f"dA {a - na:+.2f} cm dg {(g - ng + 180) % 360 - 180:+.2f} deg")
    return record, release, fit, noaa, poc_fit


def test_boston_record_is_verified(real):
    record, release, *_ = real
    assert record.member_sha256 == release.members[BOSTON]["sha256"]
    assert record.contributor == "NOAA" and record.gauge_type == "Coastal" and record.interval_min == 60
    assert record.lat == pytest.approx(42.354801) and record.lon == pytest.approx(-71.0534)


def test_boston_m2_reproduces_the_poc(real):
    _, _, _, _, fit = real
    assert fit.stats["spikes_dropped"] == 277
    assert round(fit.amplitude_m["M2"] * 100, 2) == 137.44
    assert round(fit.phase_deg["M2"], 2) == 109.35


@pytest.mark.parametrize("con", NOAA_MAIN)
def test_boston_matches_noaa_harcon(real, con):
    _, _, fit, noaa, _ = real
    da = abs(fit.amplitude_m[con] - noaa[con][0]) * 100
    dg = abs((fit.phase_deg[con] - noaa[con][1] + 180) % 360 - 180)
    assert da <= 0.7, f"{con}: |dA| {da:.2f} cm > 0.7 cm"
    assert dg <= 1.2, f"{con}: |dg| {dg:.2f} deg > 1.2 deg"


def test_boston_without_v0u_is_off_noaa(real):
    """The V0+u term carries the phase convention: zeroing it moves Boston M2 > 10 deg off NOAA."""
    _, _, fit, noaa, _ = real
    if not OMIT_V0U:
        pytest.skip("negative control: run with OTC_FIT_OMIT_V0U=1")
    dg = abs((fit.phase_deg["M2"] - noaa["M2"][1] + 180) % 360 - 180)
    assert dg > 10.0
