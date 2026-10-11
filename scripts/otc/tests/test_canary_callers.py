"""The canary's GESLA comparators: every canary caller loads them as comparators, offline.

A canary only compares a source's constants with our GESLA fits; it never publishes the GESLA side.
So its callers load `gesla_sets.load_comparator_sets`, which needs no licence and gives sets that
cannot be published (no licence_id, which the schema requires on every set). The publishing loader,
`load_gesla_sets`, needs a licence id per record and has no default.

- every caller binds its loader call to the loader's real signature (no TypeError);
- the NOAA convention canary (noaa.run_canary, the production CLI path) runs offline on the saved
  NOAA responses against comparator documents written from them: it passes under NOAA's GMT phases
  and fails with the local phases (the RED flag).
"""
from __future__ import annotations

import inspect
import shutil
from pathlib import Path

import pytest

from gesla.select_constituents import CANDIDATES
from gesla.tcd_table import TcdTable
from otc_pipeline import gesla_sets
from otc_pipeline.adapter import FetchResult, assemble_release
from otc_pipeline.adapters import noaa
from otc_pipeline.gesla_sets import fit_document, time_base_annotation, write_fits
from otc_pipeline.release_writer import validate_release

import jma_canary
import kartverket_canary
import noaa_harcon_canary

LOADERS = ("load_gesla_sets", "load_comparator_sets")


class _StubAdapter:
    """Stands in for a caller's adapter: no fetch, no sets (only the loader call is under test)."""

    def __init__(self, *a, **k):
        pass

    def fetch(self, *a, **k):
        return FetchResult("stub", "20261010", Path("."), (), {})

    def sets(self, fetched):
        return []

    comparator_sets = sets


def _callers():
    # (name, module whose names the caller looks up, adapter class name, call)
    return [
        ("noaa.run_canary", noaa, "NoaaAdapter",
         lambda tmp: noaa.run_canary("20261010", "phase_GMT", ["8443970"], tmp)),
        ("kartverket_canary.run", kartverket_canary, "KartverketAdapter",
         lambda tmp: kartverket_canary.run("20261010", "local", ["BGO"], tmp)),
        ("jma_canary.run", jma_canary, "JmaAdapter",
         lambda tmp: jma_canary.run(tmp / "fits", "local_meridian", ["TK"], tmp)),
        ("noaa_harcon_canary.run", noaa_harcon_canary, "NoaaHarconProof",
         lambda tmp: noaa_harcon_canary.run("20261010", "greenwich_utc", ["8443970"], tmp)),
    ]


@pytest.mark.parametrize("name,module,adapter_name,call", _callers(), ids=[c[0] for c in _callers()])
def test_every_canary_caller_binds_the_comparator_loader(monkeypatch, tmp_path, name, module, adapter_name, call):
    called = []
    for loader in LOADERS:
        real = getattr(gesla_sets, loader, None)
        if real is None:
            continue

        def spy(*a, _real=real, _loader=loader, **k):
            inspect.signature(_real).bind(*a, **k)   # the TypeError a real call would raise
            called.append(_loader)
            return []
        monkeypatch.setattr(gesla_sets, loader, spy)
        if hasattr(module, loader):
            monkeypatch.setattr(module, loader, spy)
    monkeypatch.setattr(module, adapter_name, _StubAdapter)
    monkeypatch.setattr(module, "canary", lambda *a, **k: "ran", raising=False)
    monkeypatch.setattr("otc_pipeline.canary.canary", lambda *a, **k: "ran")
    got = call(tmp_path)
    assert (got[-1] if isinstance(got, tuple) else got) == "ran"   # jma_canary.run returns (sets, result)
    assert called == ["load_comparator_sets"], f"{name} called {called}"


# --- comparators are never publishable ------------------------------------------------------------

SPEEDS = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356}
TABLE = TcdTable({"years": [2020, 2020], "tcd_sha256": "b" * 64, "aliases": {},
                  "constituents": {n: {"speed": sp, "v0u": [0.0], "f": [1.0]} for n, sp in SPEEDS.items()}})


def _doc(rid, lat, lon, cons, name="Boston", country="USA"):
    meta = {"SITE NAME": name, "SITE CODE": rid.split("-")[1], "COUNTRY": country, "LATITUDE": str(lat),
            "LONGITUDE": str(lon), "CONTRIBUTOR (ABBREVIATED)": "NOAA"}
    fit = {"first_s": 1104537600, "last_s": 1704067200, "n": 160000, "z0_m": 1.6,
           "rayleigh_hours": 166000.0, "rayleigh_kept": [n for n in CANDIDATES if n in SPEEDS],
           "candidates_missing_from_tcd": [n for n in CANDIDATES if n not in SPEEDS],
           "constituents": {n: {"amp_m": a, "phase_deg": g, "u_m": 0.001} for n, (a, g) in cons.items()}}
    tb = time_base_annotation(None, 0, rid, decided_in="20261010")
    return fit_document(rid, "4.1", "a" * 64, meta, TABLE, fit, good_hours=166000.0, time_base=tb)


def test_a_comparator_set_has_no_licence_and_cannot_be_published(tmp_path):
    doc = _doc("boston-8443970-usa-noaa", 42.3548, -71.0534, {"M2": (1.371, 109.2), "S2": (0.208, 146.2),
                                                              "K1": (0.143, 205.6), "O1": (0.116, 187.1)})
    comp = gesla_sets.comparator_set(doc)
    pub = gesla_sets.source_set(doc, licence_id="test-only-licence")
    assert "licence_id" not in comp.constant_set
    # the same set otherwise: the comparator is the publishable set without its licence
    assert {k: v for k, v in pub.constant_set.items() if k != "licence_id"} == comp.constant_set
    write_fits([doc], tmp_path / "fits")
    (loaded,) = gesla_sets.load_comparator_sets(in_dir=tmp_path / "fits")
    assert loaded == comp

    class GeslaFit:
        source = "gesla-fit"
        convention = staticmethod(lambda: {"convention_id": gesla_sets.CONVENTION_ID, "phase_reference": "greenwich_utc",
                                           "v0_model": "schureman_tcd", "nodal_handling": "none",
                                           "constituent_table_version": "tcd"})
        licence = staticmethod(lambda: {"licence_id": "test-only-licence", "spdx": "CC-BY-4.0", "provider": "TEST ONLY",
                                        "attribution": "TEST ONLY", "commercial_use": True})
    fetched = FetchResult("gesla-fit", "4.1", tmp_path, (), {})
    for ss, ok in ((pub, True), (comp, False)):
        cs = {**ss.constant_set, "provenance": {**ss.constant_set["provenance"], "build": {"build_commit": "abcdef0"}}}
        release = assemble_release(GeslaFit, [type(ss)(station_hint={**ss.station_hint, "timezone": "America/New_York"},
                                                       constant_set=cs)], fetched)
        errors = validate_release(release)
        if ok:
            assert errors == [], errors
        else:
            assert "stations/0/constant_sets/0: 'licence_id' is a required property" in errors, errors


def test_the_publishing_loader_still_needs_the_licence(tmp_path):
    doc = _doc("boston-8443970-usa-noaa", 42.3548, -71.0534, {"M2": (1.371, 109.2)})
    write_fits([doc], tmp_path / "fits")
    with pytest.raises(TypeError):
        gesla_sets.load_gesla_sets(in_dir=tmp_path / "fits")   # no licence_ids: no default
    with pytest.raises(ValueError, match="no licence id"):
        gesla_sets.load_gesla_sets(in_dir=tmp_path / "fits", licence_ids={})


# --- the NOAA convention canary, offline -------------------------------------------------------------

def _noaa_comparators(tmp_path) -> Path:
    """Comparator documents written from the saved NOAA harcon of the three canary stations (their GESLA
    record ids): the constants a correct convention must agree with."""
    import test_noaa as t
    fetched = t.adapter().fetch(tmp_path / "noaa-src", only=list(noaa.CANARY_STATIONS))
    docs = []
    for ss in t.adapter().sets(fetched):
        cs = ss.constant_set
        if cs is None or cs["source_record_id"] not in noaa.CANARY_STATIONS:
            continue
        cons = {c["name"]: (c["amplitude_m"], c["phase_deg"]) for c in cs["constituents"] if c["name"] in SPEEDS}
        h = ss.station_hint
        docs.append(_doc(noaa.G9_GESLA[cs["source_record_id"]], h["lat"], h["lon"], cons, name=h["name"]))
    out = tmp_path / "fits"
    write_fits(docs, out)
    return out


@pytest.mark.parametrize("phase_field,passes", [("phase_GMT", True), ("phase_local", False)])
def test_noaa_convention_canary_runs_offline(monkeypatch, tmp_path, phase_field, passes):
    import test_noaa as t
    fits = _noaa_comparators(tmp_path)
    monkeypatch.setattr(gesla_sets, "fits_dir", lambda datestamp: fits)
    monkeypatch.setattr(noaa, "NoaaAdapter", lambda **kw: t.adapter(**kw))
    cache = tmp_path / "cache"
    shutil.rmtree(cache, ignore_errors=True)
    res = noaa.run_canary("20261010", phase_field, list(noaa.CANARY_STATIONS), cache)
    assert res.n_gauges == 3 and res.passed is passes, res.to_dict()
