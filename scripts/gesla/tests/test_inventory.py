"""BP S2: GESLA record inventory and the licence exclusion switch."""

import hashlib
import json
import os
import pathlib
import sys
import zipfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from gesla import inventory  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
ROLES = REPO / "data" / "gesla" / "contributor_roles.csv"
OTC_WORK = pathlib.Path(os.environ.get("OTC_WORK", pathlib.Path.home() / ".local/share/opentideconstants/work"))
CACHE = OTC_WORK / "inputs" / "gesla" / "4.1"

# The 4.1 header as published: "OVERALL RECORD QUALITY" and "DOWNLOAD LINK" run together.
HEADER = (
    "FILE NAME,SITE NAME,SITE CODE,COUNTRY,CONTRIBUTOR (ABBREVIATED),CONTRIBUTOR (FULL),"
    "CONTRIBUTOR WEBSITE,CONTRIBUTOR CONTACT,ORGINATOR,ORIGINATOR WEBSITE,ORIGINATOR CONTACT,"
    "LATITUDE,LONGITUDE,COORDINATE SYSTEM,START DATE/TIME,END DATE/TIME,NUMBER OF YEARS,"
    "TIME ZONE HOURS,DATUM INFORMATION,INSTRUMENT,PRECISION,NULL VALUE,GAUGE TYPE,"
    "OVERALL RECORD QUALITYDOWNLOAD LINK"
)


def row(file_id, contributor, gauge="Coastal", lat="  42.35480100", lon=" -71.05340000"):
    return (
        f'{file_id},Site,CODE,USA,{contributor},Full name,"https://x",a@b,Orig,"https://y",c@d,'
        f"{lat},{lon},Unspecified,1921/01/01 01:00:00,2026/05/31 23:00:00,106,0,Station Datum,"
        f"Unspecified,Unspecified,-99.9999,{gauge},No obvious issues,https://example/v4-1/{file_id}"
    )


def write_metadata(tmp_path, rows):
    path = tmp_path / "GESLA4-1_ALL.csv"
    path.write_bytes(("\r".join([HEADER, *rows]) + "\r").encode("utf-8"))
    return path


def test_parses_cr_lines_and_the_missing_header_comma(tmp_path):
    path = write_metadata(tmp_path, [row("boston-8443970-usa-noaa", "NOAA"), row("a121tg-a12-nld-cmems", "CMEMS", "River")])
    records = inventory.parse_metadata(path)
    assert [r["file_id"] for r in records] == ["boston-8443970-usa-noaa", "a121tg-a12-nld-cmems"]
    boston = records[0]
    assert boston["contributor"] == "NOAA"
    assert boston["gauge_type"] == "Coastal"
    assert boston["lat"] == pytest.approx(42.354801)
    assert boston["lon"] == pytest.approx(-71.0534)
    assert boston["start"] == "1921-01-01T01:00:00"
    assert boston["end"] == "2026-05-31T23:00:00"
    assert boston["years"] == 106
    assert boston["download_link"] == "https://example/v4-1/boston-8443970-usa-noaa"
    assert records[1]["gauge_type"] == "River"


def test_rejects_a_header_it_does_not_know(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_bytes(b"FILE NAME,SOMETHING ELSE\rx,y\r")
    with pytest.raises(inventory.InventoryError, match="header"):
        inventory.parse_metadata(path)


def test_rejects_a_row_with_the_wrong_field_count(tmp_path):
    path = write_metadata(tmp_path, [row("x", "NOAA") + ",extra"])
    with pytest.raises(inventory.InventoryError, match="x"):
        inventory.parse_metadata(path)


def test_unknown_contributor_is_an_unlisted_redistributor(tmp_path):
    roles = inventory.load_roles(ROLES)
    path = write_metadata(tmp_path, [row("a", "NOAA"), row("b", "CMEMS"), row("c", "NEWCOMER")])
    inv = inventory.build_inventory(inventory.parse_metadata(path), roles)
    by_id = {r["file_id"]: r for r in inv}
    assert by_id["a"]["licence_class"] == "research_consultancy"
    assert by_id["a"]["role"] == "originator"
    assert by_id["b"]["licence_class"] == "research_only"
    assert by_id["b"]["role"] == "redistributor"
    assert by_id["c"]["licence_class"] == "unlisted"
    assert by_id["c"]["role"] == "redistributor"


def test_roles_table_uses_only_known_values():
    roles = inventory.load_roles(ROLES)
    assert roles
    for contributor, entry in roles.items():
        assert entry["role"] in inventory.ROLES, contributor
        assert entry["licence_class"] in inventory.LICENCE_CLASSES, contributor


def test_exclusion_switch_defaults_to_empty():
    assert inventory.excluded_classes({}) == frozenset()
    assert inventory.excluded_classes({"GESLA_EXCLUDE_LICENCE_CLASSES": ""}) == frozenset()
    assert inventory.excluded_classes({"GESLA_EXCLUDE_LICENCE_CLASSES": " research_only , unlisted "}) == frozenset(
        {"research_only", "unlisted"}
    )


def test_exclusion_switch_rejects_an_unknown_class():
    with pytest.raises(inventory.InventoryError, match="restricted"):
        inventory.excluded_classes({"GESLA_EXCLUDE_LICENCE_CLASSES": "restricted"})


def test_switch_filters_by_class(tmp_path):
    roles = inventory.load_roles(ROLES)
    path = write_metadata(tmp_path, [row("a", "NOAA"), row("b", "CMEMS"), row("c", "CRMS")])
    inv = inventory.build_inventory(inventory.parse_metadata(path), roles)
    assert [r["file_id"] for r in inventory.apply_switch(inv, frozenset())] == ["a", "b", "c"]
    kept = inventory.apply_switch(inv, frozenset({"research_only", "unlisted"}))
    assert [r["file_id"] for r in kept] == ["a"]


def test_metadata_sha_is_checked_against_the_manifest(tmp_path):
    path = write_metadata(tmp_path, [row("a", "NOAA")])
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(json.dumps({"files": [{"path": "metadata/GESLA4-1_ALL.csv", "sha256": "0" * 64, "size": 1}]}))
    with pytest.raises(inventory.InventoryError, match="SHA-256"):
        inventory.verify_metadata(path, manifest)
    good = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.write_text(
        json.dumps({"files": [{"path": "metadata/GESLA4-1_ALL.csv", "sha256": good, "size": path.stat().st_size}]})
    )
    inventory.verify_metadata(path, manifest)


def test_interval_is_the_modal_step_of_the_first_samples(tmp_path):
    body = "# HEADER\n# more\n" + "".join(
        f"2000/01/01 {h:02d}:{m:02d}:00    1.0000 1 1\n" for h in range(0, 3) for m in (0, 15, 30, 45)
    )
    zpath = tmp_path / "x.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("rec-a", body)
        z.writestr("rec-b", "# only a header\n")
    got = inventory.intervals_from_zip(zpath, ["rec-a", "rec-b"])
    assert got == {"rec-a": 900, "rec-b": None}


# --- the real 4.1 metadata (skipped when the local cache has not been fetched) -------------------

real = pytest.mark.skipif(
    not (CACHE / "metadata" / "GESLA4-1_ALL.csv").exists(), reason="GESLA 4.1 cache not fetched (scripts/gesla/fetch_mirror.rb)"
)


@real
def test_real_counts_reproduce_the_poc():
    inventory.verify_metadata(CACHE / "metadata" / "GESLA4-1_ALL.csv", CACHE / "MANIFEST.json")
    inv = inventory.build_inventory(inventory.parse_metadata(CACHE / "metadata" / "GESLA4-1_ALL.csv"), inventory.load_roles(ROLES))
    counts = inventory.class_counts(inv)
    assert len(inv) == 6682
    assert counts == {"research_consultancy": 5413, "research_only": 719, "unlisted": 550}
    assert len(inventory.apply_switch(inv, frozenset())) == 6682
    assert len(inventory.apply_switch(inv, frozenset({"research_only", "unlisted"}))) == 5413
    # every 4.1 contributor is in the committed table (none falls through to the default)
    roles = inventory.load_roles(ROLES)
    assert {r["contributor"] for r in inv} == set(roles)
