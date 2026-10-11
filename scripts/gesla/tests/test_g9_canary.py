"""G03c: the G9 convention canary (BP §8 G9).

Fits of the NOAA GESLA records at NOAA reference stations against NOAA harcon: per station,
the median |dA| and the median |dg| over M2, S2, K1, O1 must be within 1 cm and 2 deg.
The real-record test runs the default fit (G03c selection, default QC) at Boston, Seattle,
San Francisco and Key West; it skips, with the reason, when the cache lacks its inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from gesla.qc import clean
from gesla.read_gesla import GeslaRelease, otc_work
from gesla.select_constituents import (G9_AMP_TOL_M, G9_CONSTITUENTS, G9_PHASE_TOL_DEG, fit_record,
                                       g9_canary, load_noaa_harcon)
from gesla.tcd_table import TcdTable

REPO = Path(__file__).resolve().parents[3]
TCD_PATH = REPO / "data" / "latest-xtide.tcd"


def test_g9_constants():
    assert G9_CONSTITUENTS == ("M2", "S2", "K1", "O1")
    assert G9_AMP_TOL_M == 0.01 and G9_PHASE_TOL_DEG == 2.0


def test_g9_passes_on_small_differences_and_wraps_phase():
    fit = {"M2": (1.374, 359.5), "S2": (0.209, 146.5), "K1": (0.141, 205.2), "O1": (0.115, 187.4)}
    ref = {"M2": (1.371, 0.5), "S2": (0.208, 146.2), "K1": (0.143, 205.6), "O1": (0.116, 187.1)}
    r = g9_canary("x", fit, ref)
    assert r.passed
    assert r.median_dg_deg == pytest.approx(0.35)
    assert r.median_da_m == pytest.approx(0.0015)


def test_g9_fails_on_a_phase_convention_error():
    ref = {"M2": (1.371, 109.2), "S2": (0.208, 146.2), "K1": (0.143, 205.6), "O1": (0.116, 187.1)}
    fit = {n: (a, (g + 30.0) % 360) for n, (a, g) in ref.items()}
    r = g9_canary("x", fit, ref)
    assert not r.passed and r.median_dg_deg == pytest.approx(30.0)


def test_g9_fails_on_an_amplitude_error_and_on_a_missing_constituent():
    ref = {"M2": (1.371, 109.2), "S2": (0.208, 146.2), "K1": (0.143, 205.6), "O1": (0.116, 187.1)}
    fit = {n: (a * 1.2, g) for n, (a, g) in ref.items()}
    assert not g9_canary("x", fit, ref).passed
    fit = {n: v for n, v in ref.items() if n != "O1"}
    r = g9_canary("x", fit, ref)
    assert not r.passed and "O1" in r.missing


G9_STATIONS = {
    "8443970": "boston-8443970-usa-noaa",
    "9447130": "seattle-9447130-usa-noaa",
    "9414290": "san_francisco-9414290-usa-noaa",
    "8724580": "key_west-8724580-usa-noaa",
}


@pytest.fixture(scope="module")
def inputs():
    rel_root = otc_work() / "inputs" / "gesla" / "4.1"
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    harcon_dir = otc_work() / "gates" / "refs" / "noaa" / "harcon"
    need = [rel_root / "GESLA4.1_ALL.zip", rel_root / "MANIFEST.json", dump, TCD_PATH]
    need += [harcon_dir / f"harcon_{sid}.json" for sid in G9_STATIONS]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        pytest.skip(f"real-record inputs not in the cache: {missing}")
    table = TcdTable.load(dump, tcd_sha256=hashlib.sha256(TCD_PATH.read_bytes()).hexdigest())
    return table, GeslaRelease(rel_root), harcon_dir


@pytest.mark.parametrize("sid", list(G9_STATIONS))
def test_g9_canary_at_noaa_reference_stations(inputs, sid):
    table, release, harcon_dir = inputs
    r = fit_record(table, clean(release.record(G9_STATIONS[sid], roles={})))
    fit = {n: (r.primary.fit.amplitude_m[n], r.primary.fit.phase_deg[n]) for n in r.primary.fit.names}
    res = g9_canary(sid, fit, load_noaa_harcon(harcon_dir / f"harcon_{sid}.json"))
    print(f"\nG9 {sid} {G9_STATIONS[sid]}: " + json.dumps(res.to_dict(), sort_keys=True))
    assert res.passed, res.to_dict()


def test_g9_all_missing_is_a_clear_failure_in_strict_json():
    r = g9_canary("x", {}, {"M2": (1.0, 0.0)})
    assert not r.passed and set(r.missing) == set(G9_CONSTITUENTS)
    d = json.loads(json.dumps(r.to_dict(), allow_nan=False))
    assert d["median_da_cm"] is None and d["median_dg_deg"] is None
    assert "no constituent" in d["error"]


def test_g9_fails_on_a_reference_side_miss():
    ref = {"M2": (1.371, 109.2), "S2": (0.208, 146.2), "K1": (0.143, 205.6)}
    fit = dict(ref, O1=(0.116, 187.1))
    r = g9_canary("x", fit, ref)
    assert not r.passed and r.missing == ("O1",)


def _harcon(tmp_path, consts, units="meters"):
    p = tmp_path / "harcon.json"
    p.write_text(json.dumps({"units": units, "HarmonicConstituents": consts}))
    return p


def test_harcon_rejects_non_metre_units(tmp_path):
    with pytest.raises(ValueError, match="units"):
        load_noaa_harcon(_harcon(tmp_path, [], units="feet"))


def test_harcon_missing_key_names_the_file(tmp_path):
    p = _harcon(tmp_path, [{"name": "M2", "amplitude": 1.0}])
    with pytest.raises(ValueError, match=f"{p}.*phase_GMT"):
        load_noaa_harcon(p)


def test_harcon_duplicate_name_raises(tmp_path):
    c = {"name": "M2", "amplitude": 1.0, "phase_GMT": 10.0}
    with pytest.raises(ValueError, match="duplicate.*M2"):
        load_noaa_harcon(_harcon(tmp_path, [c, c]))
