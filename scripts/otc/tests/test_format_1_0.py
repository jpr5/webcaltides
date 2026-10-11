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
from otc_pipeline.gesla_release import build_release
from otc_pipeline.gesla_sets import (FitIntegrityError, NotPublishable, fit_document, source_set,
                                     time_base_annotation)
from otc_pipeline.release_writer import SCHEMA_SHA256, SchemaPinError, validate_release, write_release

from astro_from_tcd import astro_table

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


def fit_dict(amp_m2=1.0, rayleigh_kept=None):
    cons = {n: {"amp_m": a, "phase_deg": p, "u_m": 0.001} for n, a, p in
            (("M2", amp_m2, 100.0), ("S2", 0.3, 130.0), ("K1", 0.1, 200.0), ("O1", 0.05, 360.0))}
    return {"constituents": cons, "first_s": 1262304000, "last_s": 1830297600, "n": 157000, "z0_m": 1.234,
            "rayleigh_kept": list(CANDIDATES if rayleigh_kept is None else rayleigh_kept),
            "candidates_missing_from_tcd": [], "snr_dropped": ["SSA"]}


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


def doc(table, *, row=None, fit=None, rid="synthetic-1-nor-test", meta=None):
    tb = time_base_annotation(row, 0, rid, decided_in="20261010", decided_build_commit="1efca1c")
    return fit_document(rid, "4.1", "a" * 64, {**META, **(meta or {})}, table, fit or fit_dict(), good_hours=157000.0,
                        time_base=tb)


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
              astro_tables=[astro_table(table, SPEEDS, table_id=TABLE_ID, first_year=2020, last_year=2022)])
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
        write_release(release, tmp_path, datestamp=D, app_export=None)
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
    written = write_release(release, tmp_path, datestamp=D, app_export=None)
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


def test_a_short_record_is_not_published_without_expected_accuracy(table):
    with pytest.raises(NotPublishable, match="short record"):
        source_set(doc(table, fit=fit_dict(rayleigh_kept=[n for n in CANDIDATES if n not in ("SA", "T2")])),
                   licence_id="gesla-derived")


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
