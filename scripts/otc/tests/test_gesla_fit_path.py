"""The in-repo fit path (gesla_sets.fit_records): a record is fitted with its published time base and,
for an hourly-mean time base (utc_mean_start60), its published amplitudes have the boxcar response
divided out (sampling_corrected after fit_record).

Both tests need the TCD dump ($OTC_TCD_TABLE or $OTC_WORK/tcd_table.json) and skip without it. The
real-record test also needs the verified GESLA cache, the audit's timebase.csv ($OTC_TIMEBASE_CSV) and
the corrected fit run ($OTC_FIT_CORRECTED_RUN, results.jsonl + fits/), and skips without them. Every fit
goes through the audit gate (check_audit_outputs); the tests hand it a stub audit dir (audit_stub).
"""
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from gesla.fit import year_and_hours
from gesla.qc import clean
from gesla.read_gesla import otc_work
from gesla.record import GaugeRecord
from gesla.select_constituents import fit_record
from gesla.tcd_table import TcdTable
from gesla.timebase_verdict import Correction, boxcar_response

from otc_pipeline.gesla_sets import fit_records, load_audit, published_correction, source_set

from audit_stub import stub_audit

REPO = Path(__file__).resolve().parents[3]
WSV = "bhvalterleuchtturm-4990010-deu-wsv"   # one of the 11 records corrected to utc_mean_start60
TIDE = (("M2", 1.2, 40.0), ("S2", 0.35, 80.0), ("N2", 0.25, 20.0), ("K1", 0.12, 200.0), ("O1", 0.09, 150.0))


@pytest.fixture(scope="module")
def table():
    dump = Path(os.environ.get("OTC_TCD_TABLE", otc_work() / "tcd_table.json"))
    if not dump.exists():
        pytest.skip(f"TCD dump not in the cache: {dump}")
    return TcdTable.load(dump)


def hourly_means(table, t):
    """Synthetic gauge heights: each stamp t holds the mean of the tide over [t, t + 1 h), so every
    constituent is attenuated by its boxcar response and centred at t + 30 min."""
    mid = t + 1800
    years, hours = year_and_hours(mid)
    h = np.zeros(len(t))
    for name, amp, g in TIDE:
        i = years - table.first_year
        v0u = np.asarray(table[name].v0u)[i]
        f = np.asarray(table[name].f)[i]
        sp = table.speed(name)
        h += boxcar_response(sp) * f * amp * np.cos(np.radians(sp * hours + v0u - g))
    return h


class FakeRelease:
    """The GeslaRelease surface fit_records reads, around one synthetic record."""
    version = "4.1"

    def __init__(self, record, meta):
        self._record, self._meta = record, meta

    def metadata(self):
        return {self._record.record_id: self._meta}

    def record(self, name, roles):
        assert name == self._record.record_id
        return self._record


def test_a_mean60_record_publishes_m2_with_the_boxcar_factor_divided_out(table, tmp_path):
    t = np.arange(1_577_836_800, 1_577_836_800 + 2 * 366 * 86400, 3600, dtype=np.int64)   # 2020-2021, hourly
    rid = "synthetic-1-deu-test"
    rec = GaugeRecord(record_id=rid, source="gesla", source_version="4.1", contributor="TEST", originator=True,
                      licence_class="A", lat=53.5, lon=8.6, gauge_type="Coastal", sampling="mean", interval_min=60,
                      declared_time_base=None, member_sha256="a" * 64, times_s=t, heights_m=hourly_means(table, t),
                      good=np.ones(len(t), dtype=bool))
    meta = {"FILE NAME": rid, "SITE NAME": "Synthetic", "COUNTRY": "DEU", "LATITUDE": "53.5", "LONGITUDE": "8.6",
            "TIME ZONE HOURS": "0", "CONTRIBUTOR (ABBREVIATED)": "TEST"}
    row = {"file_id": rid, "time_base_status": "corrected", "published_time_base": "utc_mean_start60", "months_bad": "",
           "comparator_kind": "a", "comparator": "synthetic-2-deu-test~corrected", "lag_median": "-30.0",
           "official_lag": "", "reason": "", "time_base_reason": "declared utc_instant corrected to utc_mean_start60",
           "weak_eot20": "", "weak_served": "", "weak_dst": ""}
    docs, skipped = fit_records(table, FakeRelease(rec, meta), [rid], audit_dir=stub_audit(tmp_path, [row]))
    assert skipped == []
    (d,) = docs
    assert d["time_base"]["published"] == "utc_mean_start60"
    assert d["fit"]["stats"]["sampling_corrected"]["label"] == "utc_mean_start60"
    assert d["time_base"]["decided_in"] == "20261010"
    published = {c["name"]: c["amplitude_m"] for c in source_set(d, licence_id="gesla-derived").constant_set["constituents"]}
    # the same series and correction without the division: what the in-repo path published before
    raw = fit_record(table, clean(rec, published_correction(row, 0))).primary.fit.amplitude_m
    b = boxcar_response(table.speed("M2"))
    assert published["M2"] == pytest.approx(1.2, abs=2e-4)
    assert raw["M2"] == pytest.approx(1.2 * b, abs=2e-4)
    assert published["M2"] / raw["M2"] == pytest.approx(1 / b, rel=1e-4)
    assert 1.010 < 1 / b < 1.012
    for name, amp, _ in TIDE:
        assert published[name] == pytest.approx(amp, abs=5e-4), name


def test_an_instant_record_is_unchanged_by_the_division(table, tmp_path):
    t = np.arange(1_577_836_800, 1_577_836_800 + 2 * 366 * 86400, 3600, dtype=np.int64)
    rid = "synthetic-3-nor-test"
    rec = GaugeRecord(record_id=rid, source="gesla", source_version="4.1", contributor="TEST", originator=True,
                      licence_class="A", lat=60.4, lon=5.3, gauge_type="Coastal", sampling="instantaneous",
                      interval_min=60, declared_time_base=None, member_sha256="a" * 64, times_s=t,
                      heights_m=hourly_means(table, t), good=np.ones(len(t), dtype=bool))
    meta = {"FILE NAME": rid, "SITE NAME": "Synthetic", "COUNTRY": "NOR", "LATITUDE": "60.4", "LONGITUDE": "5.3",
            "TIME ZONE HOURS": "0"}
    (d,), _ = fit_records(table, FakeRelease(rec, meta), [rid], audit_dir=stub_audit(tmp_path, []))
    raw = fit_record(table, clean(rec, Correction("utc_instant"))).to_dict()["constituents"]
    assert {n: c["amp_m"] for n, c in d["fit"]["constituents"].items()} == {n: c["amp_m"] for n, c in raw.items()}


def test_the_in_repo_path_fits_the_list_the_window_a_rule_chooses(table, tmp_path, monkeypatch):
    # FIX-CONST: fit_records chooses the list as gesla.fit_run does, from the record's COUNTRY, so a refit
    # reproduces the run; the fit records the choice only for the extended list
    import gesla.select_constituents as sc
    t = np.arange(1_577_836_800, 1_577_836_800 + 2 * 366 * 86400, 3600, dtype=np.int64)
    rid = "synthetic-4-nor-test"
    rec = GaugeRecord(record_id=rid, source="gesla", source_version="4.1", contributor="TEST", originator=True,
                      licence_class="A", lat=60.4, lon=5.3, gauge_type="Coastal", sampling="instantaneous",
                      interval_min=60, declared_time_base=None, member_sha256="a" * 64, times_s=t,
                      heights_m=hourly_means(table, t), good=np.ones(len(t), dtype=bool))
    meta = {"FILE NAME": rid, "SITE NAME": "Synthetic", "COUNTRY": "NOR", "LATITUDE": "60.4", "LONGITUDE": "5.3",
            "TIME ZONE HOURS": "0"}
    seen, real_fit = {}, sc.fit_record

    def choose(tab, series, *, country):
        seen["country"] = country
        return seen["choice"]

    def fit(tab, series, **kw):
        seen["kw"] = kw
        return real_fit(tab, series)
    monkeypatch.setattr(sc, "select_candidate_list", choose)
    monkeypatch.setattr(sc, "fit_record", fit)
    for choice, want in ((("extended", {"chosen": "extended"}), {"candidate_list": "extended", "selection": {"chosen": "extended"}}),
                         (("base", {"chosen": "base"}), {"candidate_list": "base", "selection": None})):
        seen["choice"] = choice
        fit_records(table, FakeRelease(rec, meta), [rid], audit_dir=stub_audit(tmp_path / choice[0], []))
        assert seen["country"] == "NOR" and seen["kw"] == want


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    tb_csv = os.environ.get("OTC_TIMEBASE_CSV")
    corrected = os.environ.get("OTC_FIT_CORRECTED_RUN")
    if not tb_csv or not corrected:
        pytest.skip("needs OTC_TIMEBASE_CSV and OTC_FIT_CORRECTED_RUN")
    from gesla.read_gesla import GeslaRelease, load_contributor_roles
    try:
        rel = GeslaRelease()
    except (OSError, ValueError) as e:
        pytest.skip(f"no verified GESLA cache: {e}")
    audit = stub_audit(tmp_path_factory.mktemp("audit"), csv_from=Path(tb_csv))
    return rel, audit, Path(corrected), load_contributor_roles(REPO / "data/gesla/contributor_roles.csv")


def test_the_real_wsv_mean60_record_publishes_the_corrected_m2(table, real):
    rel, audit, corrected, roles = real
    tb = load_audit(audit).rows
    assert tb[WSV]["published_time_base"] == "utc_mean_start60"
    (d,), skipped = fit_records(table, rel, [WSV], audit_dir=audit, roles=roles)
    assert skipped == []
    m2 = next(c for c in source_set(d, licence_id="gesla-derived").constant_set["constituents"] if c["name"] == "M2")["amplitude_m"]
    want = json.loads((corrected / "fits" / f"{WSV}.json").read_text())["constituents"]["M2"]["amp_m"]
    assert m2 == pytest.approx(want, abs=1e-5)
    raw = fit_record(table, clean(rel.record(WSV, roles), published_correction(tb[WSV], 0))).primary.fit.amplitude_m["M2"]
    assert m2 / raw == pytest.approx(1 / boxcar_response(table.speed("M2")), rel=1e-4)
    assert math.isclose(m2 / raw, 1.0107, abs_tol=5e-4)


def test_the_fit_refuses_an_audit_the_gate_refuses(table, tmp_path):
    rec_meta = {"FILE NAME": "x", "SITE NAME": "x", "COUNTRY": "NOR", "LATITUDE": "0", "LONGITUDE": "0"}
    rel = FakeRelease(GaugeRecord(record_id="x-1-nor-test", source="gesla", source_version="4.1", contributor="T",
                                  originator=True, licence_class="A", lat=0.0, lon=0.0, gauge_type="Coastal",
                                  sampling="instantaneous", interval_min=60, declared_time_base=None,
                                  member_sha256="a" * 64, times_s=np.arange(3, dtype=np.int64) * 3600,
                                  heights_m=np.zeros(3), good=np.ones(3, dtype=bool)), rec_meta)
    for name, kw, needle in (("old", {"weak_crashes": None}, "no weak_crashes"),
                             ("crashed", {"weak_crashes": ["x-1-nor-test"]}, "crashed"),
                             ("running", {"status": "running"}, "did not complete")):
        d = stub_audit(tmp_path / name, [], **kw)
        if kw.get("weak_crashes", []) is None:
            m = json.loads((d / "timebase_manifest.json").read_text())
            del m["weak_crashes"]
            (d / "timebase_manifest.json").write_text(json.dumps(m))
        with pytest.raises(ValueError, match=needle):
            fit_records(table, rel, ["x-1-nor-test"], audit_dir=d)
    d = stub_audit(tmp_path / "changed", [])
    (d / "timebase.csv").write_text("file_id\n")
    with pytest.raises(ValueError, match="is not the manifest's"):
        fit_records(table, rel, ["x-1-nor-test"], audit_dir=d)
