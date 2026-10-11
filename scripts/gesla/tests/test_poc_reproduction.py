"""G03c: constituent selection (Rayleigh, noise), uncertainty, and the PoC reproduction.

Two kinds of test:
- synthetic tests (always run, no data): the Rayleigh rule, the band-noise estimate, the
  noise rule and the uncertainty rule on series with known constants;
- real-record tests (run when the verified GESLA cache, the TCD dump and the PoC output
  are present; skipped otherwise, with the reason): the 12 PoC records.

PoC-compatible mode is keep_unassessed=False (the PoC dropped samples that the spike test
cannot assess), the PoC's Rayleigh length (rayleigh_on_span: the span, not the data held),
the PoC's sub-hourly rule (poc_hourly; only Hirtshals, a 10-min record, is affected) and the PoC's time base per record (WSV: hourly means stamped at the start,
so +30 min and the 60-min boxcar response divided out). In that mode every constant of the
primary fit and of both half fits, and every uncertainty u, must equal the PoC's
fits/fits.json within output rounding (amplitude 0.01 mm, phase 0.001 deg).
OTC_POC_DIR overrides the PoC directory (default ~/.local/share/copilotkit/cr/webcaltides-ownfit).
"""

from __future__ import annotations

import cmath
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from gesla.fit import year_and_hours
from gesla.noise import BANDS, band_noise, band_of, uncertainty
from gesla.qc import Series, clean
from gesla.read_gesla import GeslaRelease, otc_work
from gesla.record import GaugeRecord
from gesla.select_constituents import (CANDIDATES, FIT_SPAN_YEARS, analyse, fit_record, last_years,
                                       rayleigh_select)
from gesla.tcd_table import TcdTable

REPO = Path(__file__).resolve().parents[3]
TCD_PATH = REPO / "data" / "latest-xtide.tcd"
POC_DIR = Path(os.environ.get("OTC_POC_DIR",
                              Path.home() / ".local/share/copilotkit/cr/webcaltides-ownfit"))

# --- synthetic fixtures ------------------------------------------------------------------------

SPEEDS = {"M2": 28.9841042, "S2": 30.0, "N2": 28.4397295, "K1": 15.0410686, "O1": 13.9430356,
          "P1": 14.9589314, "M4": 57.9682084, "SA": 0.0410686}


def fake_table() -> TcdTable:
    n = 3
    return TcdTable({
        "years": [2019, 2021], "tcd_sha256": "0" * 64, "aliases": {},
        "constituents": {k: {"speed": v, "v0u": [17.0 * (i + 1) for i in range(n)],
                             "f": [1.0 + 0.01 * i for i in range(n)]} for k, v in SPEEDS.items()},
    })


def synth(table: TcdTable, truth: dict, t: np.ndarray, noise_sd: float = 0.0, seed: int = 1) -> np.ndarray:
    years, hours = year_and_hours(t)
    h = 2.0 + 0.003 * (t - t.mean()) / 3600 / 8766
    for n, (a, g) in truth.items():
        c = table[n]
        i = years - table.first_year
        h = h + np.array(c.f)[i] * a * np.cos(np.radians(c.speed * hours + np.array(c.v0u)[i] - g))
    if noise_sd:
        h = h + np.random.default_rng(seed).normal(0.0, noise_sd, len(t))
    return h


T2019 = 1546300800      # 2019-01-01 00:00 UTC
T2022 = 1640995200


# --- candidates and Rayleigh -----------------------------------------------------------------

def test_candidates_are_noaa_37_plus_the_ticon4_extras_in_a_fixed_order():
    assert len(CANDIDATES) == 45 and len(set(CANDIDATES)) == 45
    assert CANDIDATES[:4] == ("M2", "S2", "K1", "O1")
    assert CANDIDATES[-8:] == ("EPS2", "MKS2", "SIG1", "N4", "S3", "2MK5", "2MO5", "2MS6")


def test_rayleigh_separates_k1_p1_only_after_half_a_year():
    table = fake_table()
    cands = ("M2", "S2", "K1", "P1", "SA")
    # |K1 - P1| = 0.0821372 deg/h: resolvable when T >= 360 / 0.0821372 = 4383 h.
    assert rayleigh_select(table, 4300.0, cands) == ["M2", "S2", "K1"]
    assert rayleigh_select(table, 4400.0, cands) == ["M2", "S2", "K1", "P1"]
    # SA needs speed * T >= 360 deg: T >= 8766 h.
    assert "SA" not in rayleigh_select(table, 8700.0, cands)
    assert "SA" in rayleigh_select(table, 8800.0, cands)


def test_rayleigh_conflicts_go_to_the_earlier_candidate():
    table = fake_table()
    assert rayleigh_select(table, 4300.0, ("P1", "K1")) == ["P1"]


def test_rayleigh_skips_candidates_the_tcd_lacks():
    assert rayleigh_select(fake_table(), 10000.0, ("M2", "MKS2")) == ["M2"]


# --- noise ---------------------------------------------------------------------------------------

def test_band_of():
    assert band_of(SPEEDS["SA"]) == "LP"
    assert band_of(SPEEDS["K1"]) == "D1" and band_of(SPEEDS["M2"]) == "D2" and band_of(SPEEDS["M4"]) == "D4"
    assert set(BANDS) == {"LP", "D1", "D2", "D3", "D4", "D5", "D6", "D8"}


def test_band_noise_is_deterministic_and_tracks_white_noise():
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    r = np.random.default_rng(7).normal(0.0, 0.1, len(t))
    a = band_noise(fake_table(), r, t, (t[-1] - t[0]) / 3600)
    b = band_noise(fake_table(), r, t, (t[-1] - t[0]) / 3600)
    assert a == b
    # White noise sd s over N samples: a single-sinusoid amplitude has RMS s * sqrt(4 / N).
    expect = 0.1 * math.sqrt(4 / len(t))
    for band in ("D1", "D2", "D4"):
        assert 0.7 * expect < a[band] < 1.3 * expect, (band, a[band], expect)


def test_band_noise_does_not_see_a_tidal_line():
    """Frequencies are >= 3 Rayleigh from every TCD line, so a pure M2 signal is not noise."""
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    r = synth(table, {"M2": (1.0, 30.0)}, t) - 2.0 - 0.003 * (t - t.mean()) / 3600 / 8766
    assert band_noise(table, r, t, (t[-1] - t[0]) / 3600)["D2"] < 0.01


def test_band_noise_seed_matters():
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    r = np.random.default_rng(7).normal(0.0, 0.1, len(t))
    span = (t[-1] - t[0]) / 3600
    assert band_noise(fake_table(), r, t, span, seed=0) != band_noise(fake_table(), r, t, span, seed=1)


# --- noise rule and uncertainty ----------------------------------------------------------------

def test_analyse_recovers_constants_and_drops_a_constituent_below_the_noise():
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    truth = {"M2": (1.2, 110.0), "K1": (0.15, 205.0), "O1": (0.11, 187.0), "N2": (0.0001, 40.0)}
    s = Series("synthetic", t, synth(table, truth, t, noise_sd=0.05))
    a = analyse(table, s, candidates=("M2", "K1", "O1", "N2"))
    assert a.rayleigh_kept == ("M2", "K1", "O1", "N2")
    assert a.snr_dropped == ("N2",)
    assert set(a.fit.names) == {"M2", "K1", "O1"}
    for n in ("M2", "K1", "O1"):
        assert a.fit.amplitude_m[n] == pytest.approx(truth[n][0], abs=0.004)
        assert a.fit.phase_deg[n] == pytest.approx(truth[n][1], abs=2.0)


def test_uncertainty_is_the_larger_of_noise_and_half_disagreement():
    noise = {"M2": 0.002, "K1": 0.002}
    h1 = {"M2": (1.00, 10.0), "K1": (0.10, 200.0)}
    h2 = {"M2": (1.02, 10.0), "K1": (0.10, 200.0)}
    u = uncertainty(noise, h1, h2)
    assert u["M2"] == pytest.approx(0.01)
    assert u["K1"] == pytest.approx(0.002)
    # A constituent missing from a half is not understated: at least the noise of a half-length
    # fit (sqrt(2) x the band noise when no half measured it); see uncertainty_detail.
    assert uncertainty({"M2": 0.002}, {}, {}) == {"M2": pytest.approx(0.002 * math.sqrt(2))}


def test_uncertainty_uses_the_vector_difference():
    h1 = {"M2": (1.0, 0.0)}
    h2 = {"M2": (1.0, 2.0)}
    expect = abs(cmath.rect(1.0, 0.0) - cmath.rect(1.0, math.radians(-2.0))) / 2
    assert uncertainty({"M2": 0.0}, h1, h2)["M2"] == pytest.approx(expect)


def test_fit_record_uses_the_last_19_years_and_halves_of_the_span():
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    s = Series("synthetic", t, synth(table, {"M2": (1.0, 50.0), "K1": (0.2, 100.0)}, t, noise_sd=0.02))
    r = fit_record(table, s, candidates=("M2", "K1"))
    assert r.primary.first_s == T2019 and r.primary.last_s == t[-1]
    mid = T2019 + (t[-1] - T2019) / 2
    assert r.half1.last_s < mid <= r.half2.first_s
    assert set(r.u) == {"M2", "K1"} and all(v > 0 for v in r.u.values())
    out = json.loads(r.to_json())
    assert out["constituents"]["M2"]["u_m"] == round(r.u["M2"], 5)
    assert r.to_json() == fit_record(table, s, candidates=("M2", "K1")).to_json()   # deterministic
    assert FIT_SPAN_YEARS == 19.0


def test_last_years_window():
    t = np.arange(0, 30 * 365 * 86400, 86400 * 10, dtype=np.int64) + T2019 - 30 * 365 * 86400
    s = last_years(Series("x", t, np.zeros(len(t))), 19.0)
    assert s.times_s[0] >= t[-1] - 19 * 365.25 * 86400 > s.times_s[0] - 86400 * 10


# --- real records: the PoC ---------------------------------------------------------------------

# PoC key -> (GESLA 4.1 file, PoC time base). Source: PoC tools/run_fits.py ST.
POC_RECORDS = {
    "helgoland": ("helgolandbinnenhafen-9510070-deu-wsv", "mean30"),
    "bremerhaven": ("bhvalterleuchtturm-4990010-deu-wsv", "mean30"),
    "hirtshals": ("hirtshalstg-hir-dnk-cmems", "none"),
    "leeville": ("leeville-8762084-usa-noaa", "none"),
    "woodlawn": ("woodlawn_beach-8729736-usa-noaa", "none"),
    "blueangels": ("blue_angels_park-8729941-usa-noaa", "none"),
    "boston": ("boston-8443970-usa-noaa", "none"),
    "seattle": ("seattle-9447130-usa-noaa", "none"),
    "sanfrancisco": ("san_francisco-9414290-usa-noaa", "none"),
    "keywest": ("key_west-8724580-usa-noaa", "none"),
    "trondheim": ("trondheim-trd-nor-nhs", "none"),
    "bergen": ("bergen-bgo-nor-nhs", "none"),
}
SHORT = {"leeville", "woodlawn", "blueangels"}       # 1.5-2.5-year records
BROKEN = {"hirtshals"}                                # contains no tide (PoC §4; K-checks reject it)
AMP_TOL_M = 1e-5        # one unit of the output rounding (0.01 mm)
PHASE_TOL_DEG = 1e-3    # one unit of the output rounding (0.001 deg)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mean30(t: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """PoC time base for WSV GESLA-4.1: hourly mean over [t, t+60 min) stamped at t."""
    return t + 1800, h


def boxcar(table: TcdTable, name: str) -> float:
    x = math.radians(table.speed(name) / 2)     # 60-min boxcar: sin(w * 30 min) / (w * 30 min)
    return math.sin(x) / x


@pytest.fixture(scope="module")
def poc():
    rel_root = otc_work() / "inputs" / "gesla" / "4.1"
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    fits = POC_DIR / "fits" / "fits.json"
    missing = [str(p) for p in (rel_root / "GESLA4.1_ALL.zip", rel_root / "MANIFEST.json", dump, fits, TCD_PATH)
               if not p.exists()]
    if missing:
        pytest.skip(f"real-record inputs not in the cache: {missing}")
    table = TcdTable.load(dump, tcd_sha256=_sha256(TCD_PATH))
    return table, GeslaRelease(rel_root), json.loads(fits.read_text())


def poc_hourly(record: GaugeRecord) -> GaugeRecord:
    """The PoC's sub-hourly rule (PoC fit.to_hourly): when the median step of the good, unique
    samples is under an hour, keep only samples on the modal minute, in every hour. G03b's rule
    differs in one case: an hour holding a single sample keeps it whatever its minute."""
    idx = np.flatnonzero(record.good)
    t = record.times_s[idx]
    order = np.argsort(t, kind="stable")
    idx, t = idx[order], t[order]
    first = np.ones(len(t), dtype=bool)
    first[1:] = t[1:] != t[:-1]
    idx, t = idx[first], t[first]
    if len(t) < 2 or np.median(np.diff(t)) >= 3600:
        return record
    minute = (t // 60) % 60
    mode = int(np.argmax(np.bincount(minute, minlength=60)))
    good = np.zeros(len(record), dtype=bool)
    good[idx[minute == mode]] = True
    return dataclasses.replace(record, good=good)


_RUNS: dict = {}


def run(poc, key: str, keep_unassessed: bool, *, poc_hours: bool | None = None):
    """keep_unassessed=False is PoC-compatible mode; it also uses the PoC's hourly rule unless
    poc_hours says otherwise. keep_unassessed=True is the default pipeline."""
    table, release, _ = poc
    poc_hours = (not keep_unassessed) if poc_hours is None else poc_hours
    if (key, keep_unassessed, poc_hours) not in _RUNS:
        name, tb = POC_RECORDS[key]
        rec = release.record(name, roles={})
        if poc_hours:
            rec = poc_hourly(rec)
        s = clean(rec, mean30 if tb == "mean30" else (lambda t, h: (t, h)))
        r = fit_record(table, s, keep_unassessed=keep_unassessed, rayleigh_on_span=not keep_unassessed)
        if tb == "mean30":
            r = r.scaled({n: 1.0 / boxcar(table, n) for n in r.considered()})
        _RUNS[(key, keep_unassessed, poc_hours)] = r
    return _RUNS[(key, keep_unassessed, poc_hours)]


def _diffs(ours, theirs: dict) -> list[str]:
    out = []
    if set(ours.fit.names) != set(theirs["constituents"]):
        out.append(f"constituents differ: ours-only {sorted(set(ours.fit.names) - set(theirs['constituents']))} "
                   f"PoC-only {sorted(set(theirs['constituents']) - set(ours.fit.names))}")
        return out
    if ours.fit.stats["spikes_dropped"] != theirs["stats"]["spikes_dropped"]:
        out.append(f"spikes {ours.fit.stats['spikes_dropped']} vs PoC {theirs['stats']['spikes_dropped']}")
    for n, c in theirs["constituents"].items():
        da = abs(ours.fit.amplitude_m[n] - c["amp"])
        dg = abs((ours.fit.phase_deg[n] - c["phase"] + 180) % 360 - 180)
        dn = abs(ours.constituent_noise[n] - c["noise"])
        if da > AMP_TOL_M or dg > PHASE_TOL_DEG or dn > AMP_TOL_M:
            out.append(f"{n}: dA {da:.3g} m dg {dg:.3g} deg dnoise {dn:.3g} m")
    return out


def poc_u(theirs: dict) -> dict[str, float]:
    """The PoC's u (tools/compare.py), for every constituent of the primary fit: the halves rule,
    and the band noise alone for a constituent missing from a half."""
    p, h1, h2 = (theirs[k]["constituents"] for k in ("primary", "half1", "half2"))
    u = {n: c["noise"] for n, c in p.items()}
    for n in p:
        if n in h1 and n in h2:
            u[n] = uncertainty({n: p[n]["noise"]}, {n: (h1[n]["amp"], h1[n]["phase"])},
                               {n: (h2[n]["amp"], h2[n]["phase"])})[n]
    return u


@pytest.mark.parametrize("key", list(POC_RECORDS))
def test_poc_mode_reproduces_the_poc(poc, key):
    """Constants of the primary and both halves, and u where both halves have the constituent
    (basis "halves"), equal the PoC's. Where a half lacks it, the PoC used the band noise alone;
    G03c's rule (noise.uncertainty_detail) is deliberately not below that, so u >= the PoC's."""
    ours = run(poc, key, keep_unassessed=False)
    theirs = poc[2][key]
    problems = [f"half error: {e}" for e in ours.half_errors]
    for part in ("primary", "half1", "half2"):
        a = getattr(ours, part)
        if a is not None:
            problems += [f"{part}: {p}" for p in _diffs(a, theirs[part])]
    for n, u in poc_u(theirs).items():
        if n not in ours.u:
            problems.append(f"u {n}: missing from our fit")
        elif ours.u_basis[n] == "halves":
            if abs(ours.u[n] - u) > AMP_TOL_M:
                problems.append(f"u {n}: {ours.u[n]:.6f} vs PoC {u:.6f}")
        elif ours.u[n] < u - AMP_TOL_M:
            problems.append(f"u {n} ({ours.u_basis[n]}): {ours.u[n]:.6f} below PoC {u:.6f}")
    assert not problems, f"{key}: " + "; ".join(problems[:12])


@pytest.mark.parametrize("key", [k for k in POC_RECORDS if k not in BROKEN])
def test_constituent_counts(poc, key):
    for keep in (False, True):
        n = len(run(poc, key, keep_unassessed=keep).primary.fit.names)
        lo, hi = (23, 28) if key in SHORT else (41, 45)
        assert lo <= n <= hi, f"{key} keep_unassessed={keep}: {n} constituents"


def test_broken_record_keeps_almost_nothing(poc):
    """Hirtshals CMEMS holds no tide (PoC §4): the noise rule keeps only a few constituents."""
    assert len(run(poc, "hirtshals", keep_unassessed=True).primary.fit.names) <= 6


def test_poc_fit_span_is_the_last_19_years(poc):
    r = run(poc, "boston", keep_unassessed=True)
    assert r.primary.span_hours <= 19 * 365.25 * 24
    assert r.primary.span_hours > 18.9 * 365.25 * 24


def test_hirtshals_differs_from_the_poc_only_by_the_hourly_rule(poc):
    """With G03b's hourly rule (and otherwise PoC mode) Hirtshals keeps hours that hold one
    sample off the modal minute; the PoC dropped them. That is the whole difference: with the
    PoC's rule the fit reproduces the PoC (test_poc_mode_reproduces_the_poc[hirtshals])."""
    g03b = run(poc, "hirtshals", keep_unassessed=False, poc_hours=False)
    pocr = run(poc, "hirtshals", keep_unassessed=False, poc_hours=True)
    extra = g03b.primary.fit.stats["hourly"] - pocr.primary.fit.stats["hourly"]
    print(f"\nHIRTSHALS hourly G03b {g03b.primary.fit.stats['hourly']} PoC rule {pocr.primary.fit.stats['hourly']}")
    assert extra > 0
    assert _diffs(pocr.primary, poc[2]["hirtshals"]["primary"]) == []
    assert _diffs(g03b.primary, poc[2]["hirtshals"]["primary"]) != []


# --- short records (CR G03c group 1) --------------------------------------------------------------

def dense_table() -> TcdTable:
    """fake_table plus a TCD line every 0.02 cpd across D1 and D2 (none fitted, none in the
    signal). Like the real dump (176 lines), it leaves no test frequency 3 Rayleigh from every
    line in D1 or D2 once the span is under about 45 days."""
    n = 3
    speeds = dict(SPEEDS)
    for lo, hi, tag in ((0.80, 1.20, "XD1"), (1.75, 2.15, "XD2")):
        for i, cpd in enumerate(np.arange(lo, hi + 1e-9, 0.02)):
            speeds[f"{tag}_{i}"] = cpd * 15.0
    return TcdTable({
        "years": [2019, 2021], "tcd_sha256": "0" * 64, "aliases": {},
        "constituents": {k: {"speed": v, "v0u": [17.0 * (i + 1) for i in range(n)],
                             "f": [1.0 + 0.01 * i for i in range(n)]} for k, v in speeds.items()},
    })


SHORT_TRUTH = {"M2": (1.0, 110.0), "S2": (0.3, 140.0), "K1": (0.2, 200.0), "O1": (0.15, 190.0)}


@pytest.mark.parametrize("days", [30, 60])
def test_short_record_keeps_the_main_constituents(days):
    """A 30- or 60-day record resolves M2, S2, K1, O1 (Rayleigh); a dense TCD must not leave
    their bands without a noise estimate and so drop them."""
    table = dense_table()
    t = np.arange(T2019, T2019 + days * 86400, 3600, dtype=np.int64)
    s = Series("short", t, synth(table, SHORT_TRUTH, t, noise_sd=0.01))
    r = fit_record(table, s, candidates=tuple(SHORT_TRUTH))
    p = r.primary
    assert set(p.fit.names) == set(SHORT_TRUTH), (p.fit.names, p.snr_dropped, p.noise)
    for b in ("D1", "D2"):
        assert math.isfinite(p.noise[b]) and p.noise_n[b] > 0, (b, p.noise, p.noise_n)
    for n, (a, g) in SHORT_TRUTH.items():
        assert p.fit.amplitude_m[n] == pytest.approx(a, abs=0.01), n
        assert (p.fit.phase_deg[n] - g + 180) % 360 - 180 == pytest.approx(0.0, abs=2.0), n
    json.loads(r.to_json())


def test_a_long_record_keeps_the_build_plan_noise_rule():
    """When 40 test frequencies 3 Rayleigh from every TCD line exist (every PoC record), the
    estimate is the build plan's: basis tcd_lines in every band."""
    table = dense_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    s = Series("long", t, synth(table, SHORT_TRUTH, t, noise_sd=0.01))
    a = analyse(table, s, candidates=tuple(SHORT_TRUTH))
    assert set(a.noise_basis.values()) == {"tcd_lines"}
    assert set(a.noise_n.values()) == {40}


def test_an_unmeasurable_band_is_an_error_not_a_silent_drop(monkeypatch):
    import gesla.noise as noise_mod
    from gesla.fit import FitError
    monkeypatch.setattr(noise_mod, "NOISE_MAX_TRIES", 0)
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    s = Series("nonoise", t, synth(table, SHORT_TRUTH, t, noise_sd=0.01))
    with pytest.raises(FitError, match=r"nonoise: .*band D2.*no noise"):
        analyse(table, s, candidates=("M2",))


@pytest.fixture(scope="module")
def real_dump():
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    if not dump.exists():
        pytest.skip(f"TCD dump not in the cache: {dump}")
    return TcdTable.load(dump)


@pytest.mark.parametrize("days", [30, 60])
def test_short_record_with_the_real_dump(real_dump, days):
    t = np.arange(T2019, T2019 + days * 86400, 3600, dtype=np.int64)
    s = Series("short", t, synth(real_dump, SHORT_TRUTH, t, noise_sd=0.01))
    r = fit_record(real_dump, s)
    p = r.primary
    for n, (a, g) in SHORT_TRUTH.items():
        assert n in p.fit.names, (n, p.fit.names, p.noise)
        assert p.fit.amplitude_m[n] == pytest.approx(a, abs=0.01), n
        assert (p.fit.phase_deg[n] - g + 180) % 360 - 180 == pytest.approx(0.0, abs=2.0), n


# --- uncertainty basis, error narrowing, scaled(), half bounds (CR G03c group 2) ------------------

def test_uncertainty_detail_rule_and_basis():
    from gesla.noise import uncertainty_detail
    noise = {"M2": 0.002, "K1": 0.002, "P1": 0.002}
    h1 = {"M2": (1.00, 10.0), "K1": (0.10, 200.0)}
    h2 = {"M2": (1.02, 10.0)}
    prim = {"M2": (1.01, 10.0), "K1": (0.13, 200.0), "P1": (0.05, 190.0)}
    u, basis = uncertainty_detail(noise, h1, h2, primary=prim)
    assert basis == {"M2": "halves", "K1": "one_half", "P1": "band_noise"}
    assert u["M2"] == pytest.approx(0.01)
    # One half: the half-vs-primary difference (z_p ~ (z1 + z2) / 2), floored at the half noise.
    assert u["K1"] == pytest.approx(0.03)
    # No half: the noise of a half-length fit, sqrt(2) x the band noise when no half measured it.
    assert u["P1"] == pytest.approx(0.002 * math.sqrt(2))
    u2, _ = uncertainty_detail(noise, h1, h2, primary=prim, half_noise={"P1": 0.004})
    assert u2["P1"] == pytest.approx(0.004)


def _half_failure_record():
    table = fake_table()
    t = np.concatenate([[T2019], np.arange(T2019 + 600 * 86400, T2022, 3600)]).astype(np.int64)
    return table, Series("gappy", t, synth(table, {"M2": (1.0, 50.0), "K1": (0.2, 100.0)}, t, noise_sd=0.02))


def test_a_failed_half_is_reported_and_u_is_flagged_not_understated():
    table, s = _half_failure_record()
    r = fit_record(table, s, candidates=("M2", "K1"))
    assert r.half1 is None and r.half_errors and r.half_errors[0].startswith("half1: gappy: ")
    assert r.u_basis == {"M2": "one_half", "K1": "one_half"}
    out = json.loads(r.to_json())
    assert out["halves"]["half1"] is None and out["half_errors"] == list(r.half_errors)
    for n in ("M2", "K1"):
        assert out["constituents"][n]["u_basis"] == "one_half"
        assert r.u[n] >= r.half2.noise[band_of(SPEEDS[n])] - 1e-15
        assert r.u[n] >= r.primary.constituent_noise[n]


def test_a_constituent_the_halves_cannot_resolve_gets_a_flagged_larger_u():
    """K1/P1 need 4383 h: a 6000-h span resolves them, its 3000-h halves do not."""
    table = fake_table()
    t = np.arange(T2019, T2019 + 6000 * 3600, 3600, dtype=np.int64)
    truth = {"M2": (1.0, 50.0), "K1": (0.3, 100.0), "P1": (0.1, 95.0)}
    r = fit_record(table, Series("kp", t, synth(table, truth, t, noise_sd=0.02)), candidates=("M2", "K1", "P1"))
    assert "P1" in r.primary.fit.names and "P1" not in r.half1.fit.names
    assert r.u_basis["P1"] == "band_noise" and r.u_basis["M2"] == "halves"
    hn = max(h.noise["D1"] for h in (r.half1, r.half2))
    assert r.u["P1"] == pytest.approx(max(r.primary.constituent_noise["P1"], hn))
    assert r.u["P1"] > r.primary.constituent_noise["P1"]


def test_a_defect_in_a_half_is_not_swallowed(monkeypatch):
    import gesla.select_constituents as sc
    real = sc.analyse
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("operands could not be broadcast together")
        return real(*a, **k)
    monkeypatch.setattr(sc, "analyse", flaky)
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    s = Series("bug", t, synth(table, {"M2": (1.0, 50.0)}, t, noise_sd=0.02))
    with pytest.raises(ValueError, match="broadcast"):
        sc.fit_record(table, s, candidates=("M2",))


def test_scaled_multiplies_every_analysis_and_recomputes_u():
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    s = Series("sc", t, synth(table, {"M2": (1.0, 50.0), "K1": (0.2, 100.0)}, t, noise_sd=0.02))
    r = fit_record(table, s, candidates=("M2", "K1"))
    x = r.scaled({"M2": 2.0})
    for a, b in ((r.primary, x.primary), (r.half1, x.half1), (r.half2, x.half2)):
        assert b.fit.amplitude_m["M2"] == 2.0 * a.fit.amplitude_m["M2"]
        assert b.fit.amplitude_m["K1"] == a.fit.amplitude_m["K1"]
        assert b.fit.phase_deg == a.fit.phase_deg and b.constituent_noise == a.constituent_noise
    z1 = cmath.rect(x.half1.fit.amplitude_m["M2"], -math.radians(x.half1.fit.phase_deg["M2"]))
    z2 = cmath.rect(x.half2.fit.amplitude_m["M2"], -math.radians(x.half2.fit.phase_deg["M2"]))
    assert x.u["M2"] == pytest.approx(max(x.primary.constituent_noise["M2"], abs(z1 - z2) / 2))
    assert x.u_basis == r.u_basis


@pytest.mark.parametrize("key", ["Sa", "XX", "P1"])
def test_scaled_rejects_a_factor_for_a_constituent_the_fit_never_considered(key):
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    s = Series("sc", t, synth(table, {"M2": (1.0, 50.0)}, t, noise_sd=0.02))
    r = fit_record(table, s, candidates=("M2", "K1"))
    with pytest.raises(ValueError, match=f"sc: .*{key}"):
        r.scaled({key: 2.0})


def test_half2_ends_at_the_last_sample_the_primary_used():
    """A final sub-hourly sample that the primary despiked must not enter half2."""
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    h = synth(table, {"M2": (1.0, 50.0)}, t, noise_sd=0.01)
    t = np.append(t, t[-1] + 1800)
    h = np.append(h, h[-1] + 5.0)
    r = fit_record(table, Series("tail", t, h), candidates=("M2",))
    assert r.primary.last_s == t[-2]
    mid = r.primary.first_s + (r.primary.last_s - r.primary.first_s) / 2
    assert r.half2.fit.stats["in_window"] == int(((t >= mid) & (t <= r.primary.last_s)).sum())


# --- group 3 and review slot 3 ------------------------------------------------------------------

def long_table(first: int, last: int, speeds: dict = SPEEDS) -> TcdTable:
    n = last - first + 1
    return TcdTable({
        "years": [first, last], "tcd_sha256": "0" * 64, "aliases": {},
        "constituents": {k: {"speed": v, "v0u": [(17.0 * (i + 1)) % 360 for i in range(n)],
                             "f": [1.0] * n} for k, v in speeds.items()},
    })


def test_fit_record_truncates_a_long_record_to_the_last_19_years():
    table = long_table(1990, 2021)
    t = np.arange(T2022 - 25 * 365 * 86400, T2022, 6 * 3600, dtype=np.int64)
    s = Series("long", t, synth(table, {"M2": (1.0, 50.0)}, t, noise_sd=0.02))
    r = fit_record(table, s, candidates=("M2",))
    assert r.primary.first_s >= t[-1] - 19 * 365.25 * 86400 > r.primary.first_s - 6 * 3600
    assert 18.99 * 8766 < r.primary.span_hours <= 19 * 8766


def test_fit_record_drops_years_outside_the_tcd_like_the_poc():
    table = long_table(2019, 2021)
    t = np.arange(T2019 - 200 * 86400, T2022, 3600, dtype=np.int64)     # starts in 2018
    s = Series("early", t, synth(long_table(2018, 2021), {"M2": (1.0, 50.0)}, t, noise_sd=0.02))
    r = fit_record(table, s, candidates=("M2",))
    assert r.primary.first_s == T2019
    assert r.primary.fit.stats["outside_tcd_years"] == int((t < T2019).sum())


def test_noise_rule_uses_each_constituents_own_band():
    """Noise only in D1: a small K1 is dropped, an equally small M2 (quiet D2) is kept."""
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    h = synth(table, {"M2": (1.0, 50.0), "S2": (0.004, 10.0), "K1": (0.004, 100.0)}, t)
    rng = np.random.default_rng(3)
    th = (t - t[0]) / 3600.0
    for _ in range(60):     # many diurnal sinusoids away from the lines: D1-only noise
        cpd = rng.uniform(0.82, 0.88)
        h = h + 0.01 * np.cos(np.radians(cpd * 15 * th + rng.uniform(0, 360)))
    a = analyse(table, Series("d1", t, h), candidates=("M2", "S2", "K1"))
    assert a.noise["D1"] > 5 * a.noise["D2"]
    assert "K1" in a.snr_dropped and "S2" in a.fit.names


def test_rayleigh_boundary_is_inclusive():
    table = fake_table()
    t_kp = 360.0 / abs(SPEEDS["K1"] - SPEEDS["P1"])
    assert "P1" in rayleigh_select(table, t_kp * (1 + 1e-12), ("K1", "P1"))
    assert "P1" not in rayleigh_select(table, t_kp * (1 - 1e-9), ("K1", "P1"))
    assert "SA" in rayleigh_select(table, 360.0 / SPEEDS["SA"] * (1 + 1e-12), ("SA",))


def test_band_noise_without_tidal_lines_is_a_clear_error():
    table = TcdTable({"years": [2019, 2021], "tcd_sha256": "0" * 64, "aliases": {},
                      "constituents": {"Z0": {"speed": 0.0, "v0u": [0, 0, 0], "f": [1, 1, 1]}}})
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    with pytest.raises(ValueError, match="no positive-speed"):
        band_noise(table, np.zeros(len(t)), t, 1000.0)


def test_unknown_constituent_error_names_the_record():
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    from gesla.fit import lsq
    with pytest.raises(KeyError, match="rec42"):
        lsq(table, ["M2", "QQ9"], t, np.zeros(len(t)), record_id="rec42")


def test_candidates_missing_from_the_tcd_are_recorded():
    table = fake_table()
    t = np.arange(T2019, T2022, 3600, dtype=np.int64)
    s = Series("miss", t, synth(table, {"M2": (1.0, 50.0)}, t, noise_sd=0.02))
    r = fit_record(table, s, candidates=("M2", "MKS2"))
    assert r.primary.tcd_missing == ("MKS2",)
    assert json.loads(r.to_json())["candidates_missing_from_tcd"] == ["MKS2"]
