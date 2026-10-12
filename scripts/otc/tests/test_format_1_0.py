"""Format 1.0: the pinned schema copy, the old (0.5) writer output against it, and the GESLA
release build (gesla_sets.source_set + gesla_release.build_release) on synthetic fit documents.

Synthetic numbers only: no GESLA data in git. The real-record tests are in test_gesla_fit_path.py
and test_gesla_release_e2e.py and skip without the local caches.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from gesla.select_constituents import CANDIDATES
from gesla.tcd_table import TcdTable

from otc_pipeline import release_writer
from otc_pipeline import gesla_release as gr
from otc_pipeline import gesla_sets as gs
from otc_pipeline.gesla_release import build_release
from otc_pipeline.gesla_sets import (FitIntegrityError, NotPublishable, attach_long_period_references, fit_document,
                                     source_set, time_base_annotation)
from otc_pipeline.release_writer import SCHEMA_SHA256, SchemaPinError, validate_release, write_release

from synthetic_congen import congen_run, kp0b_tables

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema" / "otc-1.0.schema.json"
D = "20261010"
TABLE_ID = "tcd-test"
SPEEDS = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686, "O1": 13.9430356, "SSA": 0.0821373}


@pytest.fixture(scope="module")
def table():
    years = 3
    return TcdTable({"years": [2020, 2022], "tcd_sha256": "c" * 64, "aliases": {},
                     "constituents": {n: {"speed": s, "v0u": [10.0 * i for i in range(years)], "f": [1.0] * years}
                                      for n, s in SPEEDS.items()}})


def jsonschema_errors(doc, ref=None):
    """The schema alone (or one of its $defs), with a format checker: independent of the writer's validator."""
    schema = json.loads(SCHEMA.read_text())
    if ref:
        schema = {"$defs": schema["$defs"], "$ref": f"#/$defs/{ref}"}
    v = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return sorted(f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in v.iter_errors(doc))


META = {"FILE NAME": "synthetic-1-nor-test", "SITE NAME": "Synthetic_Harbour", "SITE CODE": "1", "COUNTRY": "NOR",
        "LATITUDE": "60.39", "LONGITUDE": "5.32", "CONTRIBUTOR (ABBREVIATED)": "TEST", "TIME ZONE HOURS": "0",
        "DATUM INFORMATION": "Unspecified"}


LONG_HOURS = 157000.0
SHORT_HOURS = 100 * 24.0                                 # SSA (182.6 days) is a period drop on the test table


def fit_dict(amp_m2=1.0, rayleigh_hours=LONG_HOURS):
    """A fit consistent with the test table: its Rayleigh selection replays (gesla.short_accuracy)."""
    from gesla.select_constituents import rayleigh_select
    kept = [n for n in CANDIDATES if n in SPEEDS and n != "SSA"] + (["SSA"] if rayleigh_hours >= 360 / SPEEDS["SSA"] else [])
    cons = {n: {"amp_m": a, "phase_deg": p, "u_m": 0.001} for n, a, p in
            (("M2", amp_m2, 100.0), ("S2", 0.3, 130.0), ("K1", 0.1, 200.0), ("O1", 0.05, 360.0))}
    return {"constituents": cons, "first_s": 1262304000, "last_s": 1830297600, "n": 157000, "z0_m": 1.234,
            "rayleigh_hours": rayleigh_hours, "rayleigh_kept": kept,
            "candidates_missing_from_tcd": [n for n in CANDIDATES if n not in SPEEDS],
            "snr_dropped": ["SSA"] if "SSA" in kept else []}


def short_calibration(fit, rid="synthetic-1-nor-test", good_hours=SHORT_HOURS, microtidal=False):
    """A calibration (gesla.short_accuracy, version 2) covering one short record."""
    from gesla import short_accuracy as sa
    return {"cal_version": sa.CAL_VERSION, "refused": {}, "targets": {rid: {
        "good_hours": round(good_hours, 1), "cut_to_days": round(good_hours / 24, 1), "calibration_records": 15,
        "amp_uncertainty_m": 0.012, "phase_uncertainty_deg": 6.5, "microtidal": microtidal,
        "fit_sha256": sa.fit_sha256(fit),
        "by_constituent": {"M2": {"amp_m": 0.012, "phase_deg": 1.2}, "S2": {"amp_m": 0.008, "phase_deg": 6.5},
                           "K1": {"amp_m": 0.004, "phase_deg": 4.0}, "O1": {"amp_m": 0.002, "phase_deg": None}}}}}


def tb_row(status="verified", **over):
    row = {"time_base_status": status, "time_base_code": f"{status}:official", "published_time_base": "utc_instant",
           "comparator_kind": "b", "comparator": "official:kartverket:BGO", "official_lag": "-1.3",
           "lag_median": "-1.3", "months_bad": "", "reason": "record-level lag -1.3 min",
           "time_base_reason": "declared utc_instant confirmed by official (official:kartverket:BGO)",
           "weak_eot20": "", "weak_served": "", "weak_dst": ""}
    row.update(over)
    return row


def stamped(fit, label, kind=None, factors=None):
    """fit with the sampling_corrected stamp gesla.select_constituents.sampling_corrected writes."""
    return {**fit, "stats": {"sampling_corrected": {"kind": kind or label, "label": label, "factors": factors or {}}}}


def doc(table, *, row=None, fit=None, rid="synthetic-1-nor-test", meta=None, calibration=None):
    tb = time_base_annotation(row, 0, rid, decided_in="20261010", decided_build_commit="1efca1c")
    f = fit or fit_dict()
    d = fit_document(rid, "4.1", "a" * 64, {**META, **(meta or {})}, table, f, good_hours=f["rayleigh_hours"],
                     time_base=tb, calibration=calibration)
    attach_long_period_references([d])          # a run of one record: no long record to judge it against
    return d


LICENCE = {"licence_id": "gesla-derived", "spdx": "LicenseRef-GESLA-Research", "provider": "TEST ONLY",
           "attribution": "TEST ONLY", "commercial_use": True}


def build(table, docs, **over):
    kw = dict(table=table, table_id=TABLE_ID, datestamp=D, created="2026-10-10T00:00:00Z", build_commit="abcdef0",
              station_ids={d["record_id"]: f"OTC-T{i:04d}" for i, d in enumerate(docs)}, licences=[LICENCE],
              licence_ids={d["record_id"]: "gesla-derived" for d in docs}, withheld={}, refused=[],
              epoch_hours={d["record_id"]: {"member_sha256": d["member_sha256"], "hours": 0} for d in docs},
              contributor_roles={d["contributor"]: "originator" for d in docs},
              sibling_flags={}, tombstones=[], station_map_inputs={"fit_run_results_sha256": "f" * 64},
              fit_run_results_sha256="f" * 64, disputed_reasons={}, sampling_s={d["record_id"]: 3600 for d in docs},
              astro_tables=kp0b_tables(congen_run(table), SPEEDS, TABLE_ID), congen_run=congen_run(table))
    kw.update(over)
    release, _report = build_release(docs, **kw)
    return release


# --- the pin -----------------------------------------------------------------------------------------------

def test_schema_copy_is_the_pinned_one():
    assert hashlib.sha256(SCHEMA.read_bytes()).hexdigest() == SCHEMA_SHA256
    assert release_writer._schema_file() == SCHEMA
    assert release_writer.pinned_schema() == json.loads(SCHEMA.read_text())
    script = (ROOT / "sync_schema.sh").read_text()
    assert re.search(rf"^SCHEMA_SHA256={SCHEMA_SHA256}$", script, re.M), "sync_schema.sh pins another SHA-256"
    assert json.loads(SCHEMA.read_text())["properties"]["format_version"]["const"] == release_writer.FORMAT_VERSION


def test_a_schema_copy_off_the_pin_stops_the_build(table, tmp_path, monkeypatch, capsys):
    release = build(table, [doc(table)])
    monkeypatch.setattr(release_writer, "SCHEMA_SHA256", "0" * 64)
    with pytest.raises(SchemaPinError):
        write_release(release, tmp_path, datestamp=D, app_export=None, congen_run=congen_run(table))
    assert list(tmp_path.iterdir()) == []
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(release))
    assert release_writer.main(["validate", str(path)]) == 2
    assert "the pin is" in capsys.readouterr().err


# --- the old writer's output fails 1.0 ---------------------------------------------------------------------

# What the 0.5 writer path (gesla_sets.source_set at A-NOAAC 0f25475 + adapter.assemble_release) emitted for
# one GESLA fit, keys and shapes as it wrote them; the numbers are synthetic.
OLD_0_5 = {
    "format_version": "0.5",
    "release": {"datestamp": "20000101", "created": "2000-01-01T00:00:00Z", "doi": None, "concept_doi": None,
                "source_versions": {"gesla-fit": "4.1"}},
    "conventions": [{"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                     "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                     "constituent_table_version": "tcd-test"}],
    "licences": [{"licence_id": "gesla-derived", "spdx": "CC-BY-4.0", "provider": "GESLA", "attribution": "x"}],
    "constituents": [{"constituent_table_version": "tcd-test", "name": "M2", "speed_deg_per_hour": 28.9841042}],
    "stations": [{"station_id": "OTC-CONTRACT-00001", "status": "active", "name": "Synthetic Harbour",
                  "country": "NOR", "lat": 60.39, "lon": 5.32, "timezone": "Europe/Oslo", "type": "reference",
                  "aliases": {"gesla": ["synthetic-1-nor-test"]}, "recommended_set_id": "OTC-CONTRACT-00001/gesla-fit",
                  "constant_sets": [{
                      "set_id": "OTC-CONTRACT-00001/gesla-fit", "source": "gesla-fit", "source_type": "gauge",
                      "quantity": "water_level", "source_record_id": "synthetic-1-nor-test", "source_version": "4.1",
                      "record_span": {"start": "2010-01-01T00:00:00Z", "end": "2028-01-01T00:00:00Z",
                                      "good_samples": 157000},
                      "datum": {"msl_offset_m": 1.234}, "convention_id": "otc-greenwich-schureman-v1",
                      "licence_id": "gesla-derived", "qc_status": "accepted",
                      "constituents": [{"name": "M2", "speed_deg_per_hour": 28.9841042, "amplitude_m": 1.0,
                                        "phase_deg": 100.0, "amp_uncertainty_m": 0.001}],
                      "provenance": {"input_sha256": ["a" * 64], "tcd_sha256": "c" * 64}}]}],
}


def test_the_0_5_writer_output_fails_the_1_0_schema():
    # the station's errors are inside the station oneOf: check the active station on its own as well
    errors = jsonschema_errors(OLD_0_5) + jsonschema_errors(OLD_0_5["stations"][0], "active_station") \
        + jsonschema_errors(OLD_0_5["licences"][0], "licence")
    text = "\n".join(errors)
    for needle in ("format_version: '1.0' was expected",
                   "release: 'min_reader_version' is a required property",
                   "release: 'fit_min_good_hours' is a required property",
                   "Additional properties are not allowed ('doi' was unexpected)",
                   "'build' is a required property",
                   "'accepted' is not one of ['ok', 'flagged', 'no_constants']",
                   "'time_base' is a required property",
                   "'good_hours' is a required property",
                   "'commercial_use' is a required property"):
        assert needle in text, (needle, errors)
    assert validate_release(OLD_0_5) != []


# --- the 1.0 build ---------------------------------------------------------------------------------------

def test_a_gesla_release_validates_against_1_0(table, tmp_path):
    docs = [doc(table, row=tb_row()),
            doc(table, rid="synthetic-2-nor-test", row=tb_row("unverified", comparator_kind="none", comparator="",
                                                              time_base_reason="declared utc_instant kept; no comparator")),
            doc(table, rid="synthetic-3-nor-test", row=None)]
    release = build(table, docs)
    assert release["format_version"] == "1.0" and release["release"]["min_reader_version"] == "1.0"
    assert jsonschema_errors(release) == []
    assert validate_release(release) == []
    written = write_release(release, tmp_path, datestamp=D, app_export=None, congen_run=congen_run(table))
    on_disk = json.loads((tmp_path / f"OTC_{D}.json").read_text())
    assert on_disk == release and jsonschema_errors(on_disk) == []
    meta = json.loads((tmp_path / f"OTC_{D}.meta.json").read_text())
    assert "stations" not in meta and jsonschema_errors(meta, "meta") == []
    for line in (tmp_path / f"OTC_{D}.jsonl").read_text().splitlines():
        assert jsonschema_errors(json.loads(line), "station") == []
    assert any(p.name.endswith(".csv") for p in written)
    sets = [st["constant_sets"][0] for st in release["stations"]]
    assert [s["qc_status"] for s in sets] == ["ok", "flagged", "flagged"]
    assert [f["flag"] for f in sets[1]["qc_flags"]] == ["time_base"]
    assert sets[0]["time_base"]["evidence"] == [{"comparator_kind": "official_predictions", "comparator_id": "kartverket:BGO",
                                                 "lag_min": -1.3, "detail": "record-level lag -1.3 min"}]
    assert sets[2]["time_base"]["code"] == "unverified" and "evidence" not in sets[2]["time_base"]
    assert sets[0]["datum"] == {"msl_offset_m": 1.234}
    assert sets[0]["dropped_constituents"] == [{"name": "SSA", "dropped_reason": "noise"}]
    assert sets[0]["provenance"]["build"] == {"build_commit": "abcdef0"}
    assert sets[0]["record_span"]["good_hours"] == 157000.0
    assert next(c for c in sets[0]["constituents"] if c["name"] == "O1")["phase_deg"] == 0.0   # 360 -> 0
    st = release["stations"][0]
    assert st["timezone"] == "Europe/Oslo" and st["aliases"] == {"gesla": ["synthetic-1-nor-test"]}


def test_a_corrected_mean60_record_needs_its_sampling_response_divided(table):
    row = tb_row("corrected", time_base_code="corrected:utc_instant->utc_mean_start60:sibling",
                 published_time_base="utc_mean_start60", comparator_kind="a",
                 comparator="bremerhaventg-bre-deu-cmems~corrected", lag_median="-29.45", official_lag="-33.21",
                 time_base_reason="declared utc_instant corrected to utc_mean_start60 by sibling")
    with pytest.raises(FitIntegrityError, match="no sampling_corrected stamp"):
        source_set(doc(table, row=row), licence_id="gesla-derived")
    with pytest.raises(FitIntegrityError, match="stamp is for utc_instant"):
        source_set(doc(table, row=row, fit=stamped(fit_dict(), "utc_instant")), licence_id="gesla-derived")
    from gesla.timebase_verdict import Correction
    f = fit_dict()
    factors = {n: Correction("utc_mean_start60").amplitude_factor(table.speed(n), (f["first_s"], f["last_s"] + 3600))
               for n in f["constituents"]}
    cs = source_set(doc(table, row=row, fit=stamped(f, "utc_mean_start60", factors=factors)),
                    licence_id="gesla-derived").constant_set
    tb = cs["time_base"]
    assert tb["correction"] == {"from": "utc_instant", "to": "utc_mean_start60"} and tb["published"] == "utc_mean_start60"
    assert tb["evidence"][0]["comparator_kind"] == "sibling_record"
    assert tb["evidence"][0]["comparator_id"] == "bremerhaventg-bre-deu-cmems" and tb["evidence"][0]["lag_min"] == -29.45
    assert cs["qc_status"] == "ok"


def test_a_short_record_with_missing_calibration_retains_the_exact_finding(table):
    cs = source_set(doc(table, fit=fit_dict(rayleigh_hours=SHORT_HOURS)), licence_id="gesla-derived").constant_set
    assert "no calibration" in cs["fallback"]["otc_finding"]
    f = fit_dict(rayleigh_hours=SHORT_HOURS)
    other = short_calibration(f, rid="someone-else")
    cs = source_set(doc(table, fit=f, calibration=other), licence_id="gesla-derived").constant_set
    assert "not a target" in cs["fallback"]["otc_finding"]
    refit = fit_dict(amp_m2=1.01, rayleigh_hours=SHORT_HOURS)
    cs = source_set(doc(table, fit=refit, calibration=short_calibration(f)), licence_id="gesla-derived").constant_set
    assert "differs" in cs["fallback"]["otc_finding"]
    assert cs["fallback"]["reason"] == "short_record_uncalibrated" and "expected_accuracy" not in cs


def test_a_calibrated_short_record_is_published_with_its_accuracy(table):
    f = fit_dict(rayleigh_hours=SHORT_HOURS)
    cs = source_set(doc(table, fit=f, calibration=short_calibration(f)), licence_id="gesla-derived").constant_set
    assert cs["expected_accuracy"] == {"cut_to_days": 100.0, "calibration_records": 15, "amp_uncertainty_m": 0.012,
                                       "phase_uncertainty_deg": 6.5}
    assert cs["qc_status"] == "flagged"
    flag = next(x for x in cs["qc_flags"] if x["flag"] == "short_record")
    assert flag["values"]["S2_phase_uncertainty_deg"] == 6.5 and flag["values"]["O1_phase_uncertainty_deg"] is None
    assert cs["dropped_constituents"] == [{"name": "SSA", "dropped_reason": "other",
                                           "detail": "period longer than the record (needs 182.6 days)"}]
    assert not [x for x in cs["qc_flags"] if x["flag"] == "microtidal"]
    assert jsonschema_errors(cs, "constant_set") == [] or all("set_id" in e or "build" in e for e in jsonschema_errors(cs, "constant_set"))


def test_a_microtidal_short_record_is_flagged(table):
    f = fit_dict(rayleigh_hours=SHORT_HOURS)
    cs = source_set(doc(table, fit=f, calibration=short_calibration(f, microtidal=True)),
                    licence_id="gesla-derived").constant_set
    assert [x["flag"] for x in cs["qc_flags"]][:2] == ["short_record", "microtidal"]


LP_SPEEDS = {**SPEEDS, "SA": 0.0410686, "MM": 0.5443747, "MF": 1.0980331, "MSF": 1.0158958}


@pytest.fixture(scope="module")
def table_lp():
    return TcdTable({"years": [2020, 2022], "tcd_sha256": "c" * 64, "aliases": {},
                     "constituents": {n: {"speed": s, "v0u": [0.0] * 3, "f": [1.0] * 3} for n, s in LP_SPEEDS.items()}})


def lp_fit(table, hours, **lp):
    """A fit on the long-period test table: the mains of fit_dict and long-period lines name=(amp, u)."""
    from gesla.select_constituents import rayleigh_select
    f = fit_dict(rayleigh_hours=hours)
    f["constituents"].update({n: {"amp_m": a, "phase_deg": 0.0, "u_m": u} for n, (a, u) in lp.items()})
    return dict(f, rayleigh_kept=rayleigh_select(table, hours), snr_dropped=[], band_noise_m={"LP": 0.004},
                candidates_missing_from_tcd=[n for n in CANDIDATES if n not in LP_SPEEDS])


def lp_doc(table, f, rid="synthetic-1-nor-test", meta=None, attach=True):
    tb = time_base_annotation(None, 0, rid, decided_in="20261010")
    d = fit_document(rid, "4.1", "a" * 64, {**META, **(meta or {})}, table, f, good_hours=f["rayleigh_hours"],
                     time_base=tb, calibration=short_calibration(f, rid=rid, good_hours=f["rayleigh_hours"]))
    if attach:
        attach_long_period_references([d])
    return d


def test_a_short_record_with_no_long_record_nearby_publishes_no_long_period_line(table_lp):
    """200 days: SSA kept by the Rayleigh rule (1.1 cycles) but never published short; MM, MF and MSF resolved and
    well above their own noise, but no long record of the same water within 3 km confirms them (waal_km_922's MF)."""
    f = lp_fit(table_lp, 200 * 24.0, SSA=(0.965, 0.01), MM=(0.05, 0.001), MF=(0.413, 0.1), MSF=(0.03, 0.001))
    assert {"SSA", "MM", "MF", "MSF"} <= set(f["rayleigh_kept"]) and "SA" not in f["rayleigh_kept"]
    cs = source_set(lp_doc(table_lp, f), licence_id="gesla-derived").constant_set
    assert sorted(c["name"] for c in cs["constituents"]) == ["K1", "M2", "O1", "S2"]
    held = {d["name"]: d for d in cs["dropped_constituents"] if d["dropped_reason"] == "long_period_rule"}
    assert set(held) == {"SSA", "MM", "MF", "MSF"}
    assert "never publishes SA or SSA" in held["SSA"]["detail"] and "within 3 km confirms" in held["MF"]["detail"]
    assert [d["name"] for d in cs["dropped_constituents"]] == sorted(d["name"] for d in cs["dropped_constituents"])
    assert next(d for d in cs["dropped_constituents"] if d["name"] == "SA")["dropped_reason"] == "other"
    assert [e for e in jsonschema_errors(cs, "constant_set") if "set_id" not in e and "build" not in e] == []


def test_a_short_record_publishes_only_the_lines_its_long_neighbour_confirms(table_lp):
    short = lp_doc(table_lp, lp_fit(table_lp, 200 * 24.0, MM=(0.05, 0.01), MF=(0.04, 0.005), MSF=(0.06, 0.005)),
                   attach=False)
    long_ = lp_doc(table_lp, lp_fit(table_lp, LONG_HOURS, MM=(0.052, 0.001), MSF=(0.01, 0.001)),
                   rid="long-1-nor-test", meta={"LATITUDE": "60.40"}, attach=False)       # 1.1 km north, no MF
    attach_long_period_references([short, long_])
    assert short["long_period_reference"]["record"] == "long-1-nor-test" and "long_period_reference" not in long_
    cs = source_set(short, licence_id="gesla-derived").constant_set
    names = {c["name"] for c in cs["constituents"]}
    assert "MM" in names and not names & {"MF", "MSF"}
    held = {d["name"]: d["detail"] for d in cs["dropped_constituents"] if d["dropped_reason"] == "long_period_rule"}
    assert "differs by 0.050 m (over 0.030 m) from the long record long-1-nor-test (1.1 km), which has 0.010 m" in held["MSF"]
    assert "which has it under its noise" in held["MF"]


def test_a_long_record_keeps_its_long_period_lines(table_lp):
    f = lp_fit(table_lp, LONG_HOURS, SA=(0.1, 0.01), SSA=(0.05, 0.01), MM=(0.02, 0.01), MF=(0.01, 0.01))
    d = fit_document("long-1-nor-test", "4.1", "a" * 64, META, table_lp, f, good_hours=LONG_HOURS,
                     time_base=time_base_annotation(None, 0, "long-1-nor-test", decided_in="20261010"))
    names = {c["name"] for c in source_set(d, licence_id="gesla-derived").constant_set["constituents"]}
    assert {"SA", "SSA", "MM", "MF"} <= names


def test_a_short_record_never_checked_against_the_long_records_is_not_published(table_lp):
    f = lp_fit(table_lp, 200 * 24.0, MM=(0.05, 0.01))
    with pytest.raises(FitIntegrityError, match="long_period_reference"):
        source_set(lp_doc(table_lp, f, attach=False), licence_id="gesla-derived")


def test_an_implausible_fit_is_retained_with_its_actual_finding(table):
    f = fit_dict()
    cal = {"cal_version": 2, "refused": {"synthetic-1-nor-test": {"reason": "implausible_fit", "detail": "M2 17.67 m"}},
           "targets": {}}
    cs = source_set(doc(table, fit=f, calibration=cal), licence_id="gesla-derived").constant_set
    assert cs["fallback"]["reason"] == "refused_fit"
    assert "17.67" in cs["fallback"]["otc_finding"]
    assert cs["qc_status"] == "flagged"
    assert next(c for c in cs["constituents"] if c["name"] == "M2")["amplitude_m"] == f["constituents"]["M2"]["amp_m"]


def test_uncalibrated_short_record_has_no_invented_accuracy(table):
    f = fit_dict(rayleigh_hours=SHORT_HOURS)
    cs = source_set(doc(table, fit=f), licence_id="gesla-derived").constant_set
    assert cs["fallback"]["reason"] == "short_record_uncalibrated"
    assert "expected_accuracy" not in cs
    assert any(flag["flag"] == "short_record" for flag in cs["qc_flags"])


def test_nofit_identity_keeps_actual_span_and_reason():
    row = {"record": META["FILE NAME"], "outcome": "unusable", "reason": "rank deficiency",
           "data_hours": 480.0, "first": 1577836800, "last": 1580428800, "window_samples": 480}
    cs = gs.no_constants_set(row, META, source_version="4.1", member_sha256="a" * 64,
                              licence_id="gesla-derived", tcd_sha256="c" * 64,
                              time_base=time_base_annotation(None, 0, row["record"], decided_in=D)).constant_set
    assert cs["qc_status"] == "no_constants" and cs["constituents"] == []
    assert cs["no_constants_reason"] == "fit_unusable"
    assert cs["record_span"]["start"] == "2020-01-01T00:00:00Z"
    assert cs["record_issues"][0]["reason"] == "rank deficiency"
    assert "expected_accuracy" not in cs


def test_fallback_cannot_override_a_nonfallback_even_by_source_tier():
    bad = gr.Candidate({"set_id": "official", "source_type": "official", "quantity": "water_level",
                        "source_record_id": "official", "fallback": {"reason": "refused_fit"}}, 0, 0)
    good = gr.Candidate({"set_id": "gauge", "source_type": "gauge", "quantity": "water_level",
                         "source_record_id": "gauge"}, 0, 0)
    assert gr.rank([bad, good])[0][0] is good
    assert gr.recommend([bad, good])[0] is good
    assert gr.set_order(good, [bad, good]) == [good, bad]


def test_build_needs_the_inputs_other_slots_own(table):
    d = doc(table)
    with pytest.raises(ValueError, match="astro_tables is required"):
        build(table, [d], astro_tables=None)
    with pytest.raises(ValueError, match="no station id"):
        build(table, [d], station_ids={})
    with pytest.raises(ValueError, match="licence 'gesla-cmems-nc' is not in licences"):
        build(table, [d], licence_ids={d["record_id"]: "gesla-cmems-nc"})


# --- the time-base annotation from the audit row ---------------------------------------------------------

def test_weak_disputed_row_lists_its_weak_checks():
    row = tb_row("disputed", comparator_kind="w", comparator="disputed:served_stations_disagree",
                 weak_eot20="0 (+0.0)", weak_served="n/a", weak_dst="no_step (+0.6)",
                 time_base_reason="declared utc_instant kept; evidence disagrees: the served stations disagree")
    tb = time_base_annotation(row, 0, "x-1-mex-test", decided_in="20261010")
    assert tb["code"] == "disputed" and tb["published"] == tb["declared"] == "utc_instant"
    assert tb["evidence"] == [{"comparator_kind": "model", "comparator_id": "eot20", "detail": "0 (+0.0)", "lag_min": 0.0},
                              {"comparator_kind": "dst_selftest", "comparator_id": "x-1-mex-test",
                               "detail": "no_step (+0.6)"}]


@pytest.mark.parametrize("over, needle", [
    (dict(time_base_status="disputed", comparator_kind="w", comparator="", weak_eot20="n/a"), "no evidence"),
    (dict(time_base_status="corrected", published_time_base="utc_instant"), "published time base is the declared"),
    (dict(published_time_base="utc_plus_1"), "GESLA declares utc_instant"),
    (dict(time_base_status="inherited"), "time_base_status 'inherited'"),
])
def test_an_inconsistent_audit_row_stops_the_build(over, needle):
    with pytest.raises(NotPublishable, match=needle):
        time_base_annotation(tb_row(**over), 0, "x", decided_in="20261010")


def test_declared_offsets_and_dropped_months():
    tb = time_base_annotation(tb_row(published_time_base="utc_plus_1", months_bad="2011-02 2011-02 2010-07",
                                     time_base_reason="declared utc_plus_1 confirmed"), 1, "x", decided_in="20261010")
    assert (tb["declared"], tb["published"], tb["months_dropped"]) == ("utc_plus_1", "utc_plus_1", ["2010-07", "2011-02"])
    assert time_base_annotation(None, -3, "x", decided_in="20261010")["declared"] == "utc_minus_3"


# --- FIX-CONST: an extended-list fit (MA2, MB2) ----------------------------------------------------------------

def test_an_extended_fit_publishes_ma2_and_is_checked_against_its_own_list():
    from gesla.select_constituents import EXTENDED_CANDIDATES
    speeds = {**SPEEDS, "MA2": 28.9430356, "MB2": 29.0251729}
    t2 = TcdTable({"years": [2020, 2022], "tcd_sha256": "e" * 64, "aliases": {},
                   "constituents": {n: {"speed": s, "v0u": [10.0 * i for i in range(3)], "f": [1.0] * 3}
                                    for n, s in speeds.items()}})
    f = fit_dict()
    f["constituents"]["MA2"] = {"amp_m": 0.04, "phase_deg": 75.0, "u_m": 0.001}
    f.update(candidate_list="extended", candidate_selection={"chosen": "extended"},
             rayleigh_kept=[n for n in EXTENDED_CANDIDATES if n in speeds],
             candidates_missing_from_tcd=[n for n in EXTENDED_CANDIDATES if n not in speeds])
    d = doc(t2, fit=f)
    assert d["refusal"] is None and d["rayleigh_drops"] == []
    release = build(t2, [d], astro_tables=kp0b_tables(congen_run(t2), speeds, TABLE_ID), congen_run=congen_run(t2))
    assert validate_release(release) == []
    (cs,) = release["stations"][0]["constant_sets"]
    ma2 = next(c for c in cs["constituents"] if c["name"] == "MA2")
    assert ma2["speed_deg_per_hour"] == 28.9430356 and ma2["amplitude_m"] == 0.04
    # read as a 45-list fit (no candidate_list), its kept list is not the Rayleigh rule's: refused, never published
    f.pop("candidate_list")
    assert doc(t2, fit=f)["refusal"]["reason"] == "short_record_uncalibrated"


def test_an_extended_fit_short_of_its_own_list_is_a_calibrated_short_record_not_an_integrity_error():
    # the drops a document carries are its own list's: MB2 left out at 300 days is a short-record drop
    from gesla.select_constituents import EXTENDED_CANDIDATES
    speeds = {**SPEEDS, "MA2": 28.9430356, "MB2": 29.0251729}
    t2 = TcdTable({"years": [2020, 2022], "tcd_sha256": "e" * 64, "aliases": {},
                   "constituents": {n: {"speed": s, "v0u": [10.0 * i for i in range(3)], "f": [1.0] * 3}
                                    for n, s in speeds.items()}})
    from gesla.select_constituents import rayleigh_select
    hours = 300 * 24.0
    f = fit_dict(rayleigh_hours=hours)
    f.update(candidate_list="extended", rayleigh_kept=rayleigh_select(t2, hours, EXTENDED_CANDIDATES),
             candidates_missing_from_tcd=[n for n in EXTENDED_CANDIDATES if n not in speeds])
    d = doc(t2, fit=f, calibration=short_calibration(f, good_hours=hours))
    assert d["refusal"] is None and "MB2" in {x["name"] for x in d["rayleigh_drops"]}
    cs = source_set(d, licence_id="gesla-derived")         # read against the 45, this would be an integrity error
    assert "MB2" in {x["name"] for x in cs.constant_set["dropped_constituents"]}


def test_an_unknown_candidate_list_is_an_integrity_error(table):
    # N8: a fit naming a list this code does not have is refused as FitIntegrityError, not a KeyError
    d = doc(table, fit=fit_dict())
    d["fit"]["candidate_list"] = "bogus"
    with pytest.raises(FitIntegrityError, match="candidate_list 'bogus' is not one of"):
        source_set(d, licence_id="gesla-derived")
