"""Tests for the OTC release writer and validator (format 0.2).

The example document is the site repo's src/schema/example.json, copied at the
pinned commit by sync_schema.sh.
"""
import copy
import csv
import gzip
import hashlib
import io
import json
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline.release_writer import (
    CSV_COLUMNS,
    next_datestamp,
    validate_release,
    write_release,
)

HERE = Path(__file__).resolve().parent
SCHEMA = HERE.parent / "schema" / "otc-0.2.schema.json"
EXAMPLE = HERE / "fixtures" / "example-0.2.json"
D = "20991231"


@pytest.fixture
def release():
    return json.loads(EXAMPLE.read_text())


def first_set(doc):
    return doc["stations"][0]["constant_sets"][0]


def write(release, out_dir, datestamp=D, **kw):
    kw.setdefault("app_export", None)
    return write_release(release, out_dir, datestamp=datestamp, **kw)


# --- validate_release -------------------------------------------------------

def test_example_is_valid(release):
    assert validate_release(release, SCHEMA) == []


def test_missing_convention_id_is_rejected(release):
    first_set(release).pop("convention_id")
    errors = validate_release(release, SCHEMA)
    assert any("convention_id" in e for e in errors), errors


@pytest.mark.parametrize("label, mutate, needle", [
    ("unknown convention_id",
     lambda d: first_set(d).__setitem__("convention_id", "nope"), "convention_id 'nope'"),
    ("unknown licence_id",
     lambda d: first_set(d).__setitem__("licence_id", "nope"), "licence_id 'nope'"),
    ("recommended_set_id not a set of the station",
     lambda d: d["stations"][0].__setitem__("recommended_set_id", "OTC-X/none"), "recommended_set_id"),
    ("null recommended_set_id on a reference station",
     lambda d: d["stations"][0].__setitem__("recommended_set_id", None), "recommended_set_id"),
    ("subordinate offsets to an unknown station",
     lambda d: d["stations"][0].__setitem__(
         "subordinate_offsets", {"reference_station_id": "OTC-NOPE", "height_adjusted_type": "R"}),
     "reference_station_id"),
    ("subordinate offsets to a removed station",
     lambda d: d["stations"][0].__setitem__(
         "subordinate_offsets", {"reference_station_id": "OTC-EXAMPLE-0002", "height_adjusted_type": "R"}),
     "reference_station_id"),
    ("offset licence_id unknown",
     lambda d: d["stations"][0].__setitem__(
         "subordinate_offsets", {"reference_station_id": "OTC-EXAMPLE-0001",
                                 "height_adjusted_type": "R", "licence_id": "nope"}),
     "licence_id 'nope'"),
    ("constituent not in the release table",
     lambda d: first_set(d)["constituents"][0].__setitem__("name", "Q1"), "Q1"),
    ("dropped constituent not in the release table",
     lambda d: first_set(d)["dropped_constituents"][0].__setitem__("name", "Q1"), "Q1"),
    ("duplicate constituent in one set",
     lambda d: first_set(d)["constituents"].append(copy.deepcopy(first_set(d)["constituents"][0])),
     "duplicate constituent"),
    ("validation for a set the station does not have",
     lambda d: d["stations"][0]["validation"][0].__setitem__("set_id", "OTC-X/none"), "validation"),
    ("duplicate station_id",
     lambda d: d["stations"].append(copy.deepcopy(d["stations"][1])), "duplicate station_id"),
    ("duplicate set_id",
     lambda d: d["stations"][0]["constant_sets"][1].__setitem__("set_id", first_set(d)["set_id"]),
     "duplicate set_id"),
    ("duplicate convention definition",
     lambda d: d["conventions"].append(copy.deepcopy(d["conventions"][0])), "duplicate convention_id"),
    ("duplicate licence definition",
     lambda d: d["licences"].append(copy.deepcopy(d["licences"][0])), "duplicate licence_id"),
    ("duplicate constituent table row",
     lambda d: d["constituents"].append(copy.deepcopy(d["constituents"][0])), "duplicate constituent table"),
    ("constituent name of 16 characters",
     lambda d: first_set(d)["constituents"][0].__setitem__("name", "M" * 16), "too long"),
    ("constituent source_name of 32 characters",
     lambda d: first_set(d)["constituents"][0].__setitem__("source_name", "x" * 32), "too long"),
    ("bad created date-time",
     lambda d: d["release"].__setitem__("created", "yesterday"), "date-time"),
    ("local convention without utc_offset_hours",
     lambda d: d["conventions"][1].pop("utc_offset_hours"), "utc_offset_hours"),
])
def test_negative_controls(release, label, mutate, needle):
    mutate(release)
    errors = validate_release(release, SCHEMA)
    assert any(needle in e for e in errors), f"{label}: {errors}"


def test_alias_reused_by_another_station_is_rejected(release):
    twin = copy.deepcopy(release["stations"][0])
    twin["station_id"] = "OTC-EXAMPLE-0003"
    for s in twin["constant_sets"]:
        s["set_id"] = s["set_id"].replace("0001", "0003")
    twin["recommended_set_id"] = twin["recommended_set_id"].replace("0001", "0003")
    for v in twin["validation"]:
        v["set_id"] = v["set_id"].replace("0001", "0003")
    release["stations"].append(twin)
    errors = validate_release(release, SCHEMA)
    assert "duplicate alias kartverket:EXA (OTC-EXAMPLE-0001, OTC-EXAMPLE-0003)" in errors
    assert "duplicate alias gesla:example-file-id (OTC-EXAMPLE-0001, OTC-EXAMPLE-0003)" in errors


def test_alias_in_two_systems_is_allowed(release):
    release["stations"][0]["aliases"]["xtide"] = "EXA"
    assert validate_release(release, SCHEMA) == []


def test_subordinate_station_with_null_recommended_set(release):
    sub = {
        "station_id": "OTC-EXAMPLE-0004", "status": "active", "name": "Sub", "country": "NOR",
        "lat": 60.1, "lon": 5.1, "timezone": "Europe/Oslo", "type": "subordinate",
        "recommended_set_id": None, "constant_sets": [],
        "subordinate_offsets": {"reference_station_id": "OTC-EXAMPLE-0001", "height_adjusted_type": "R",
                                "time_offset_high_min": 5, "height_offset_high": 1.1, "licence_id": "kartverket"},
    }
    release["stations"].append(sub)
    assert validate_release(release, SCHEMA) == []


# --- next_datestamp ---------------------------------------------------------

def test_next_datestamp_empty_dir(tmp_path):
    assert next_datestamp(tmp_path, date(2026, 10, 8)) == "20261008"


def test_next_datestamp_missing_dir(tmp_path):
    assert next_datestamp(tmp_path / "nope", date(2026, 10, 8)) == "20261008"


def test_next_datestamp_counts_up(tmp_path):
    (tmp_path / "OTC_20261008.json").write_text("{}")
    assert next_datestamp(tmp_path, date(2026, 10, 8)) == "20261008.2"
    (tmp_path / "OTC_20261008.2.sha256").write_text("")
    assert next_datestamp(tmp_path, date(2026, 10, 8)) == "20261008.3"
    (tmp_path / "OTC_20261008.10.csv").write_text("")
    assert next_datestamp(tmp_path, date(2026, 10, 8)) == "20261008.11"


def test_next_datestamp_ignores_other_days_and_files(tmp_path):
    (tmp_path / "OTC_20261007.json").write_text("{}")
    (tmp_path / "OTC_latest.json").write_text("{}")
    (tmp_path / "OTC_202610081.json").write_text("{}")
    assert next_datestamp(tmp_path, date(2026, 10, 8)) == "20261008"


def test_next_datestamp_any_file_of_the_stem_counts(tmp_path):
    (tmp_path / "OTC_20261008.app.json").write_text("[]")
    assert next_datestamp(tmp_path, date(2026, 10, 8)) == "20261008.2"


# --- write_release ------------------------------------------------------------

def test_write_refuses_invalid_release(release, tmp_path):
    first_set(release).pop("convention_id")
    with pytest.raises(ValueError, match="convention_id"):
        write(release, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_write_refuses_datestamp_mismatch(release, tmp_path):
    with pytest.raises(ValueError, match="datestamp"):
        write(release, tmp_path, datestamp="20991230")
    assert list(tmp_path.iterdir()) == []


def test_write_refuses_existing_stem(release, tmp_path):
    (tmp_path / f"OTC_{D}.csv").write_text("x")
    with pytest.raises(FileExistsError):
        write(release, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"OTC_{D}.csv"]
    assert (tmp_path / f"OTC_{D}.csv").read_text() == "x"


def test_write_files_and_order(release, tmp_path):
    paths = write(release, tmp_path)
    names = [p.name for p in paths]
    assert names == [f"OTC_{D}.json", f"OTC_{D}.json.gz", f"OTC_{D}.jsonl", f"OTC_{D}.meta.json",
                     f"OTC_{D}.csv", f"OTC_{D}.sha256"]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(names)


def test_write_optional_files(release, tmp_path):
    app = [{"id": "noaa:9414290", "source": "gesla", "constituents": []}]
    paths = write(release, tmp_path, app_export=app, parquet=True, jsonl_gz=True)
    names = [p.name for p in paths]
    assert names == [f"OTC_{D}.json", f"OTC_{D}.json.gz", f"OTC_{D}.jsonl", f"OTC_{D}.jsonl.gz",
                     f"OTC_{D}.meta.json", f"OTC_{D}.csv", f"OTC_{D}.parquet", f"OTC_{D}.app.json",
                     f"OTC_{D}.sha256"]
    assert json.loads((tmp_path / f"OTC_{D}.app.json").read_text()) == app


def test_round_trip_json(release, tmp_path):
    original = copy.deepcopy(release)
    write(release, tmp_path)
    assert release == original, "write_release must not mutate its input"
    back = json.loads((tmp_path / f"OTC_{D}.json").read_text(encoding="utf-8"))
    assert back == original
    assert validate_release(back, SCHEMA) == []
    gz = json.loads(gzip.decompress((tmp_path / f"OTC_{D}.json.gz").read_bytes()))
    assert gz == original


def test_jsonl_and_meta(release, tmp_path):
    write(release, tmp_path, jsonl_gz=True)
    schema = json.loads(SCHEMA.read_text())

    def sub(name):
        return Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"],
                                     "$ref": f"#/$defs/{name}"})

    raw = (tmp_path / f"OTC_{D}.jsonl").read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw
    lines = raw.decode("utf-8").splitlines()
    stations = [json.loads(line) for line in lines]
    assert stations == release["stations"]
    for s in stations:
        assert sub("station").is_valid(s)
    assert gzip.decompress((tmp_path / f"OTC_{D}.jsonl.gz").read_bytes()) == raw
    meta = json.loads((tmp_path / f"OTC_{D}.meta.json").read_text())
    assert meta == {k: v for k, v in release.items() if k != "stations"}
    assert sub("meta").is_valid(meta)


def read_csv(path):
    raw = path.read_bytes()
    assert b"\r" not in raw.replace(b'"\r', b""), "CSV line ends must be LF"
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"), newline="")))


def test_csv_layout(release, tmp_path):
    write(release, tmp_path)
    path = tmp_path / f"OTC_{D}.csv"
    header = path.read_text(encoding="utf-8").split("\n", 1)[0]
    assert header.split(",") == CSV_COLUMNS
    assert CSV_COLUMNS[0] == "record_type"
    rows = read_csv(path)
    types = [r["record_type"] for r in rows]
    assert types == ["convention", "convention",
                     "station", "constant_set", "constituent", "constituent",
                     "constant_set", "constituent", "validation",
                     "tombstone"]
    assert set(types) <= {"station", "constant_set", "constituent", "offset", "convention",
                          "validation", "tombstone"}
    station = rows[2]
    assert station["station_id"] == "OTC-EXAMPLE-0001"
    assert station["name"] == "Example Harbour (illustrative values, not data)"
    assert json.loads(station["aliases"]) == {"gesla": ["example-file-id"], "kartverket": "EXA"}
    assert station["amplitude_m"] == ""
    cset = rows[3]
    assert cset["set_id"] == "OTC-EXAMPLE-0001/gesla-fit" and cset["station_id"] == "OTC-EXAMPLE-0001"
    assert json.loads(cset["provenance"]) == first_set(release)["provenance"]
    assert cset["record_span_start"] == "2007-01-01T00:00:00Z" and cset["good_samples"] == "160000"
    m2 = rows[4]
    assert (m2["set_id"], m2["name"], m2["amplitude_m"], m2["phase_deg"]) == \
        ("OTC-EXAMPLE-0001/gesla-fit", "M2", "0.5", "100.0")
    assert m2["station_id"] == "OTC-EXAMPLE-0001"
    tomb = rows[-1]
    assert (tomb["station_id"], tomb["status"], tomb["removed_in"]) == ("OTC-EXAMPLE-0002", "removed", D)
    assert tomb["removed_reason"].startswith("Example tombstone")
    conv = rows[1]
    assert (conv["convention_id"], conv["phase_reference"], conv["utc_offset_hours"]) == \
        ("kartverket-utc1-v1", "local", "1")


def subordinate(offsets):
    return {
        "station_id": "OTC-EXAMPLE-0004", "status": "active", "name": "Sub", "country": "NOR",
        "lat": 60.1, "lon": 5.1, "timezone": "Europe/Oslo", "type": "subordinate",
        "recommended_set_id": None, "constant_sets": [], "subordinate_offsets": offsets,
    }


def test_offset_to_a_subordinate_station_is_rejected(release):
    release["stations"].append(subordinate({"reference_station_id": "OTC-EXAMPLE-0001",
                                            "height_adjusted_type": "R"}))
    release["stations"].append(subordinate({"reference_station_id": "OTC-EXAMPLE-0004",
                                            "height_adjusted_type": "R"}))
    release["stations"][-1]["station_id"] = "OTC-EXAMPLE-0005"
    errors = validate_release(release, SCHEMA)
    assert any("'OTC-EXAMPLE-0004' is not a reference station" in e for e in errors), errors


def test_offset_to_itself_is_rejected(release):
    release["stations"].append(subordinate({"reference_station_id": "OTC-EXAMPLE-0004",
                                            "height_adjusted_type": "R"}))
    errors = validate_release(release, SCHEMA)
    assert any("is the station itself" in e for e in errors), errors


def test_csv_offset_row(release, tmp_path):
    release["stations"].insert(1, subordinate({
        "reference_station_id": "OTC-EXAMPLE-0001", "height_adjusted_type": "A",
        "time_offset_high_min": -12, "height_offset_low": 0.25}))
    write(release, tmp_path)
    rows = read_csv(tmp_path / f"OTC_{D}.csv")
    assert [r["record_type"] for r in rows][-3:] == ["station", "offset", "tombstone"]
    r = rows[-2]
    assert (r["station_id"], r["reference_station_id"], r["height_adjusted_type"],
            r["time_offset_high_min"], r["height_offset_low"], r["time_offset_low_min"]) == \
        ("OTC-EXAMPLE-0004", "OTC-EXAMPLE-0001", "A", "-12", "0.25", "")
    assert rows[-3]["recommended_set_id"] == ""


def test_csv_rfc4180_quoting(release, tmp_path):
    tricky = 'Port "Quoted", Fjord\nsecond line, æøå'
    release["stations"][0]["name"] = tricky
    write(release, tmp_path)
    rows = read_csv(tmp_path / f"OTC_{D}.csv")
    assert [r["name"] for r in rows if r["record_type"] == "station"] == [tricky]
    text = (tmp_path / f"OTC_{D}.csv").read_text(encoding="utf-8")
    assert '"Port ""Quoted"", Fjord\nsecond line, æøå"' in text


def test_parquet_matches_csv(release, tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    write(release, tmp_path, parquet=True)
    table = pq.read_table(tmp_path / f"OTC_{D}.parquet")
    assert table.column_names == CSV_COLUMNS
    rows = read_csv(tmp_path / f"OTC_{D}.csv")
    assert table.num_rows == len(rows)
    assert table.column("record_type").to_pylist() == [r["record_type"] for r in rows]
    amps = table.column("amplitude_m").to_pylist()
    assert amps[4] == 0.5 and amps[2] is None
    assert str(table.schema.field("amplitude_m").type) == "double"
    assert str(table.schema.field("good_samples").type) == "int64"


def test_sha256_file(release, tmp_path):
    paths = write(release, tmp_path, app_export=[{"a": 1}], jsonl_gz=True)
    lines = (tmp_path / f"OTC_{D}.sha256").read_text().splitlines()
    listed = {}
    for line in lines:
        digest, name = line.split("  ", 1)
        listed[name] = digest
    assert set(listed) == {p.name for p in paths if not p.name.endswith(".sha256")}
    assert [line.split("  ", 1)[1] for line in lines] == sorted(listed)
    for name, digest in listed.items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest


def test_sha256_written_last(release, tmp_path):
    write(release, tmp_path)
    files = sorted(tmp_path.iterdir(), key=lambda p: p.stat().st_mtime_ns)
    assert files[-1].name == f"OTC_{D}.sha256"


def test_byte_identical_for_identical_input(release, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    pa = write(copy.deepcopy(release), a, app_export=[{"x": 1.5}], parquet=True, jsonl_gz=True)
    pb = write(copy.deepcopy(release), b, app_export=[{"x": 1.5}], parquet=True, jsonl_gz=True)
    for x, y in zip(pa, pb):
        assert x.read_bytes() == y.read_bytes(), x.name


def test_key_order_does_not_change_bytes(release, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    shuffled = json.loads(json.dumps(release), object_pairs_hook=lambda kv: dict(reversed(kv)))
    pa = write(release, a)
    pb = write(shuffled, b)
    for x, y in zip(pa, pb):
        assert x.read_bytes() == y.read_bytes(), x.name


def test_float_formatting_is_fixed(release, tmp_path):
    first_set(release)["constituents"][0]["amplitude_m"] = 0.1 + 0.2
    first_set(release)["constituents"][0]["phase_deg"] = 1e-7
    write(release, tmp_path)
    text = (tmp_path / f"OTC_{D}.json").read_text()
    assert '"amplitude_m":0.30000000000000004' in text
    assert '"phase_deg":1e-07' in text
    rows = read_csv(tmp_path / f"OTC_{D}.csv")
    m2 = [r for r in rows if r["record_type"] == "constituent"][0]
    assert (m2["amplitude_m"], m2["phase_deg"]) == ("0.30000000000000004", "1e-07")


def test_nan_is_refused(release, tmp_path):
    first_set(release)["constituents"][0]["amplitude_m"] = float("nan")
    with pytest.raises(ValueError):
        write(release, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_second_write_same_day(release, tmp_path):
    day = date(2099, 12, 31)
    d1 = next_datestamp(tmp_path, day)
    first = write(copy.deepcopy(release), tmp_path, datestamp=d1)
    before = {p.name: p.read_bytes() for p in first}
    d2 = next_datestamp(tmp_path, day)
    assert d2 == "20991231.2"
    second_doc = copy.deepcopy(release)
    second_doc["release"]["datestamp"] = d2
    second = write(second_doc, tmp_path, datestamp=d2)
    assert [p.name for p in second] == [f"OTC_20991231.2.{ext}" for ext in
                                        ("json", "json.gz", "jsonl", "meta.json", "csv", "sha256")]
    for name, data in before.items():
        assert (tmp_path / name).read_bytes() == data


def test_write_refuses_stem_with_only_an_optional_file(release, tmp_path):
    (tmp_path / f"OTC_{D}.parquet").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        write(release, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"OTC_{D}.parquet"]


def test_gzip_has_no_time_stamp(release, tmp_path):
    write(release, tmp_path, jsonl_gz=True)
    for ext in ("json.gz", "jsonl.gz"):
        head = (tmp_path / f"OTC_{D}.{ext}").read_bytes()[:10]
        assert head[4:8] == b"\0\0\0\0", ext  # MTIME field
        assert head[3] & 0x08 == 0, ext       # no FNAME


@pytest.mark.parametrize("where", ["amplitude", "provenance"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_validate_reports_non_finite_numbers(release, where, bad):
    if where == "amplitude":
        first_set(release)["constituents"][0]["amplitude_m"] = bad
    else:
        first_set(release)["provenance"]["score"] = bad
    errors = validate_release(release, SCHEMA)
    assert any("is not a finite number" in e for e in errors), errors


def test_failed_write_removes_its_files(release, tmp_path, monkeypatch):
    import otc_pipeline.release_writer as rw
    real_link = rw.os.link

    def link(src, dst):
        if str(dst).endswith(".csv"):
            raise OSError("disk full")
        real_link(src, dst)

    monkeypatch.setattr(rw.os, "link", link)
    with pytest.raises(OSError, match="disk full"):
        write(release, tmp_path)
    assert list(tmp_path.iterdir()) == []
