"""Marine Institute (Irish National Tide Gauge Network, ERDDAP) gauge adapter, slot A-mi-a.

Fixtures in fixtures/mi/ are real ERDDAP responses saved by the polite fetcher (one day per
station; the Aranmore day carries QC_Flag 1 rows). The raw files are read here with a separate,
plain parser, so each check compares the adapter's GaugeRecord with the bytes it came from.
The live test (OTC_LIVE=1) does the same on a fresh fetch of the three named stations.
"""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult, assert_contract, check_contract
from otc_pipeline.adapters.mi import (
    MarineInstituteAdapter,
    obs_cache_name,
    obs_url,
    parse_obs_csv,
)
from otc_pipeline.sources import SOURCES, registry_errors

FIXTURES = Path(__file__).parent / "fixtures" / "mi"
NAMED = ("Dublin Port", "Galway Port", "Malin Head - Portmore Pier")
LIVE = os.environ.get("OTC_LIVE") == "1"


def raw_rows(path: Path) -> list[list[str]]:
    """Data rows of an ERDDAP .csv response: line 1 = names, line 2 = units."""
    lines = path.read_text().splitlines()
    assert lines[0].startswith("time,station_id,"), lines[0]
    assert lines[1].startswith("UTC,"), lines[1]
    return [r for r in csv.reader(lines[2:]) if r]


def epoch(stamp: str) -> int:
    return int(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def cache_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "mi"
    shutil.copytree(FIXTURES, dst)
    return dst


@pytest.fixture
def june(tmp_path):
    adapter = MarineInstituteAdapter(start=date(2025, 6, 1), end=date(2025, 6, 2))
    return adapter, adapter.load(cache_copy(tmp_path), only=list(NAMED))


def check_against_raw(rec: GaugeRecord, raw: Path) -> None:
    rows = raw_rows(raw)
    assert len(rec) == len(rows) > 0
    assert rec.times_s[0] == epoch(rows[0][0])
    assert rec.times_s[-1] == epoch(rows[-1][0])
    assert list(rec.times_s) == [epoch(r[0]) for r in rows]
    np.testing.assert_array_equal(rec.heights_m, np.array([float(r[5]) for r in rows]))
    assert rec.member_sha256 == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert (rec.lat, rec.lon) == (float(rows[-1][2]), float(rows[-1][3]))


# --- URL and cache layout --------------------------------------------------------------------

def test_obs_url_is_percent_encoded_and_matches_the_saved_request():
    url = obs_url("Dublin Port", date(2025, 6, 1), date(2025, 6, 2))
    meta = (FIXTURES / "obs" / "Dublin_Port_20250601_20250602.csv.meta.json").read_text()
    assert f'"url": "{url}"' in meta
    assert '"' not in url and " " not in url and ">" not in url and "<" not in url
    assert "station_id=%22Dublin%20Port%22" in url
    assert "time%3E=2025-06-01T00%3A00%3A00Z" in url and "time%3C2025-06-02T00%3A00%3A00Z" in url
    assert "QC_Flag" in url


def test_obs_cache_name():
    assert obs_cache_name("Malin Head - Portmore Pier", date(2025, 6, 1), date(2025, 6, 2)) == \
        "obs/Malin_Head_Portmore_Pier_20250601_20250602.csv"


# --- conversion ------------------------------------------------------------------------------

def test_named_stations_match_the_raw_responses(june):
    adapter, fetched = june
    recs = {r.record_id: r for r in adapter.records(fetched)}
    assert sorted(recs) == sorted(f"marine-institute:{s}" for s in NAMED)
    for sid in NAMED:
        raw = FIXTURES / obs_cache_name(sid, date(2025, 6, 1), date(2025, 6, 2))
        check_against_raw(recs[f"marine-institute:{sid}"], raw)


def test_record_fields(june):
    adapter, fetched = june
    rec = next(r for r in adapter.records(fetched) if r.record_id == "marine-institute:Dublin Port")
    assert rec.source == "marine-institute"
    assert rec.source_version == fetched.source_version
    assert len(rec.source_version) == 8 and rec.source_version.isdigit()
    assert rec.contributor == "Marine Institute" and rec.originator is True
    assert rec.licence_class == "CC-BY-4.0"
    assert rec.gauge_type == "Coastal"
    assert rec.interval_min == 5
    assert rec.declared_time_base == "utc_instant"
    assert rec.sampling == "instantaneous"
    assert (rec.lat, rec.lon) == (53.3457, -6.2217)
    assert rec.good.all()   # every fixture row is QC_Flag 0 with a finite height


def test_qc_flag_1_rows_are_good(tmp_path):
    adapter = MarineInstituteAdapter(start=date(2024, 1, 1), end=date(2024, 1, 2))
    fetched = adapter.load(cache_copy(tmp_path), only=["Aranmore Island - Leabgarrow"])
    (rec,) = adapter.records(fetched)
    raw = FIXTURES / "obs" / "Aranmore_Island_Leabgarrow_20240101_20240102.csv"
    assert {r[6] for r in raw_rows(raw)} == {"1"}
    check_against_raw(rec, raw)
    assert rec.good.all()


SYNTH = """time,station_id,latitude,longitude,Water_Level_LAT,Water_Level_OD_Malin,QC_Flag
UTC,,degrees_north,degrees_east,metres,metres,
2025-01-01T00:00:00Z,X Harbour,53.0,-6.0,2.0,0.1,0
2025-01-01T00:05:00Z,X Harbour,53.0,-6.0,2.1,0.2,1
2025-01-01T00:10:00Z,X Harbour,53.0,-6.0,2.2,0.3,9
2025-01-01T00:15:00Z,X Harbour,53.0,-6.0,2.3,NaN,0
2025-01-01T00:20:00Z,X Harbour,53.0,-6.0,2.4,-9999.0,0
2025-01-01T00:25:00Z,X Harbour,53.0,-6.0,2.5,0.6,
"""


def test_good_mask_follows_qc_flag_and_missing_values():
    p = parse_obs_csv(SYNTH)
    assert p.station_id == "X Harbour" and p.datum == "OD Malin"
    assert list(p.good) == [True, True, False, False, False, False]
    assert len(p.times_s) == 6


def test_lat_datum_is_used_only_when_od_malin_is_absent():
    text = SYNTH.replace(",0.1,", ",NaN,").replace(",0.2,", ",NaN,").replace(",0.3,", ",NaN,") \
        .replace(",0.6,", ",NaN,").replace(",-9999.0,", ",NaN,")
    p = parse_obs_csv(text)
    assert p.datum == "LAT"
    np.testing.assert_array_equal(p.heights_m, [2.0, 2.1, 2.2, 2.3, 2.4, 2.5])
    assert list(p.good) == [True, True, False, True, True, False]


@pytest.mark.parametrize("bad", [
    SYNTH.replace("QC_Flag", "Flag", 1),                                       # columns changed
    SYNTH.replace("UTC,,degrees_north", "seconds,,degrees_north", 1),          # time units changed
    SYNTH.replace("metres,metres", "cm,metres", 1),                            # height units changed
    SYNTH.replace("X Harbour,53.0,-6.0,2.1", "Y Harbour,53.0,-6.0,2.1", 1),    # two stations
])
def test_unexpected_responses_are_refused(bad):
    with pytest.raises(ValueError):
        parse_obs_csv(bad)


# --- contract, watch keys, registry ----------------------------------------------------------

def test_contract_passes_on_saved_responses(june):
    adapter, fetched = june
    assert_contract(adapter, fetched)
    assert adapter.licence()["commercial_use"] is True
    # what the fit's time-base annotation publishes as declared: a format 1.0 class
    assert {r.declared_time_base for r in adapter.records(fetched)} == {"utc_instant"}


def test_watch_keys_name_every_station_and_follow_the_data(june, tmp_path):
    adapter, fetched = june
    keys = adapter.watch_keys(fetched)
    assert sorted(keys) == sorted(NAMED)
    assert keys == adapter.watch_keys(fetched)
    p = fetched.cache_dir / obs_cache_name("Galway Port", date(2025, 6, 1), date(2025, 6, 2))
    p.write_text(p.read_text().replace(",0\n", ",1\n", 1))
    changed = adapter.watch_keys(adapter.load(fetched.cache_dir, only=list(NAMED)))
    assert changed["Galway Port"] != keys["Galway Port"]
    assert changed["Dublin Port"] == keys["Dublin Port"]


def test_load_refuses_an_unknown_or_missing_station(tmp_path):
    adapter = MarineInstituteAdapter(start=date(2025, 6, 1), end=date(2025, 6, 2))
    cache = cache_copy(tmp_path)
    with pytest.raises(ValueError, match="not a station"):
        adapter.load(cache, only=["Atlantis Harbour"])
    with pytest.raises(FileNotFoundError):
        adapter.load(cache, only=["Sligo"])


def test_load_lists_the_fetched_files(june):
    _, fetched = june
    assert isinstance(fetched, FetchResult) and fetched.source == "marine-institute"
    paths = sorted(f.path for f in fetched.files)
    assert "info.csv" in paths and "stations.csv" in paths
    assert sum(p.startswith("obs/") for p in paths) == 3
    for f in fetched.files:
        assert f.source_url.startswith("https://erddap.marine.ie/erddap/")


def test_default_window_is_the_last_complete_utc_year():
    a = MarineInstituteAdapter(today=date(2026, 10, 8))
    assert (a.start, a.end) == (date(2025, 1, 1), date(2026, 1, 1))


def test_registered():
    assert SOURCES["marine-institute"] is MarineInstituteAdapter
    assert registry_errors() == []


def test_licence_and_convention():
    a = MarineInstituteAdapter()
    assert a.licence()["spdx"] == "CC-BY-4.0"
    assert a.convention()["phase_reference"] == "greenwich_utc"


# --- live (the slot's proof surface) ---------------------------------------------------------

@pytest.mark.skipif(not LIVE, reason="live fetch: set OTC_LIVE=1")
def test_live_named_stations(tmp_path):
    adapter = MarineInstituteAdapter()
    fetched = adapter.fetch(tmp_path / "mi", only=list(NAMED))
    recs = {r.record_id: r for r in adapter.records(fetched)}
    assert sorted(recs) == sorted(f"marine-institute:{s}" for s in NAMED)
    for sid in NAMED:
        check_against_raw(recs[f"marine-institute:{sid}"],
                          fetched.cache_dir / obs_cache_name(sid, adapter.start, adapter.end))
    assert check_contract(adapter, fetched) == []


# --- the served licence (A3) -----------------------------------------------------------------------

def test_the_served_licence_is_the_one_claimed(june):
    adapter, fetched = june
    from otc_pipeline.adapters.mi import SERVED_LICENCE, served_licence
    assert served_licence((fetched.cache_dir / "info.csv").read_text()) == SERVED_LICENCE
    assert adapter.licence()["spdx"] == "CC-BY-4.0"


@pytest.mark.parametrize("served", ["Creative Commons Attribution-NonCommercial 4.0 (https://creativecommons.org/licenses/by-nc/4.0/)",
                                    None])
def test_another_served_licence_is_refused(tmp_path, served):
    cache = cache_copy(tmp_path)
    info = cache / "info.csv"
    lines = [ln for ln in info.read_text().splitlines(keepends=True)]
    i = next(k for k, ln in enumerate(lines) if ln.startswith("attribute,NC_GLOBAL,license,"))
    if served is None:
        del lines[i]
    else:
        lines[i] = f'attribute,NC_GLOBAL,license,String,"{served}"\n'
    info.write_text("".join(lines))
    with pytest.raises(ValueError, match="licen"):
        MarineInstituteAdapter(start=date(2025, 6, 1), end=date(2025, 6, 2)).load(cache, only=list(NAMED))
