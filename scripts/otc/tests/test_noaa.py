"""NOAA CO-OPS adapter (manifest A-NOAA) on saved real responses.

Fixtures (tests/fixtures/noaa/, fetched 2026-10-08): the full tide-prediction station list
(gzipped), harcon.json for Boston 8443970, Seattle 9447130 and San Francisco 9414290, and
tidepredoffsets.json (native units) for Hull 8444351 (ratio offsets from Boston), Mill Bay
8218617 (additive offsets in feet from Ketchikan 9450460, whose harcon is saved too) and Malakal
Harbor 1841275 (listed as subordinate to itself). The live run is `test_registered_adapter_live` (OTC_LIVE=1).
"""
from __future__ import annotations

import gzip
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from otc_pipeline.adapter import FetchResult, assert_contract, check_contract, sha256_file
from otc_pipeline.adapters import noaa
from otc_pipeline.adapters.noaa import NoaaAdapter, place
from otc_pipeline.constituent_speeds import NO_ASTRO_ROW, SpeedDeviation, TableSpeeds
from otc_pipeline.http import Fetched
from otc_pipeline.sources import SOURCES, registry_errors

FIX = Path(__file__).parent / "fixtures" / "noaa"
FETCHED_AT = "2026-10-08T17:16:00Z"


class FixtureFetcher:
    """Serves the saved responses by cache name, as PoliteFetcher would after a live fetch."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.urls: list[str] = []

    def get(self, url, *, params=None, cache_name=None, **_):
        full = url + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
        self.urls.append(full)
        dest = self.cache_dir / cache_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if cache_name == noaa.LIST_NAME:
            dest.write_bytes(gzip.decompress((FIX / "stations.json.gz").read_bytes()))
        else:
            src = FIX / cache_name
            if not src.exists():
                raise FileNotFoundError(f"no fixture for {cache_name}")
            shutil.copyfile(src, dest)
        return Fetched(url=full, status=200, path=dest, name=cache_name, sha256=sha256_file(dest),
                       size=dest.stat().st_size, fetched_at=FETCHED_AT, from_cache=False)


SPEEDS = TableSpeeds.from_json(FIX / "tcd_speeds.json")   # the astro rows' speeds (TCD extract)


def adapter(**kw) -> NoaaAdapter:
    return NoaaAdapter(fetcher_factory=FixtureFetcher, **{"speeds": SPEEDS, **kw})


SAMPLE = ["8443970", "9447130", "9414290", "8444351"]


@pytest.fixture
def fetched(tmp_path) -> FetchResult:
    return adapter().fetch(tmp_path, only=SAMPLE)


def by_id(sets):
    return {(s.constant_set or {}).get("source_record_id") or s.station_hint["aliases"]["noaa"]: s for s in sets}


def test_station_list_counts():
    stations = json.loads(gzip.decompress((FIX / "stations.json.gz").read_bytes()))["stations"]
    kinds = [s["type"] for s in stations]
    assert (len(stations), kinds.count("R"), kinds.count("S")) == (3502, 1260, 2242)


def test_registered():
    assert SOURCES["noaa"] is NoaaAdapter
    assert registry_errors() == []


def test_contract_passes_on_saved_responses(fetched):
    assert_contract(adapter(), fetched)


def test_greenwich_phases_are_published_unmodified(fetched):
    boston = by_id(adapter().sets(fetched))["8443970"]
    m2 = next(c for c in boston.constant_set["constituents"] if c["name"] == "M2")
    # NOAA's amplitude and phase as served; the speed is the astro row's (NOAA gives 28.984104)
    assert m2 == {"name": "M2", "source_name": "M2", "speed_deg_per_hour": 28.9841042,
                  "amplitude_m": 1.371, "phase_deg": 109.2}
    harcon = json.loads((FIX / "harcon/8443970.json").read_text())["HarmonicConstituents"]
    assert len(boston.constant_set["constituents"]) == len(harcon) == 37
    assert boston.constant_set["provenance"]["input_sha256"] == [
        f.sha256 for f in fetched.files if f.path in (noaa.LIST_NAME, "harcon/8443970.json")]


def test_phase_local_flag_publishes_local_phases(tmp_path):
    a = adapter(phase_field="phase_local")
    sets = by_id(a.sets(a.fetch(tmp_path, only=["8443970"])))
    m2 = next(c for c in sets["8443970"].constant_set["constituents"] if c["name"] == "M2")
    assert m2["phase_deg"] == 324.2


def test_bad_phase_field_is_refused():
    with pytest.raises(ValueError):
        NoaaAdapter(phase_field="phase")


def test_station_hints(fetched):
    sets = by_id(adapter().sets(fetched))
    assert sets["8443970"].station_hint == {
        "name": "BOSTON", "country": "USA", "lat": 42.35388888888889, "lon": -71.05027777777778, "type": "reference",
        "timezone": "America/New_York", "aliases": {"noaa": "8443970"}}
    assert sets["9447130"].station_hint["timezone"] == "America/Los_Angeles"


def test_subordinate_offsets(fetched):
    hull = by_id(adapter().sets(fetched))["8444351"]
    assert hull.constant_set is None
    assert hull.station_hint["type"] == "subordinate"
    assert hull.subordinate_offsets == {
        "reference_source_record_id": "8443970", "time_offset_high_min": 5, "time_offset_low_min": 7,
        "height_offset_high": 0.97, "height_offset_low": 0.97, "height_adjusted_type": "R",
        "licence_id": "noaa-public-domain"}


def test_a_subordinate_brings_its_reference(tmp_path):
    a = adapter()
    f = a.fetch(tmp_path, only=["8444351"])
    assert sorted(f.items) == ["8443970", "8444351"]
    assert check_contract(a, f) == []


def test_subordinate_to_itself_is_skipped(tmp_path):
    a = adapter()
    f = a.fetch(tmp_path, only=SAMPLE + ["1841275"])
    assert a.skipped(f) == {"1841275": "subordinate to itself"}
    assert "1841275" not in by_id(a.sets(f))
    assert "1841275" in a.watch_keys(f)
    assert check_contract(a, f) == []


def test_subordinates_of_a_skipped_reference_are_skipped(fetched):
    items = dict(fetched.items)
    items["8443970"] = {**items["8443970"], "harcon": {"units": "meters", "HarmonicConstituents": []}}
    f = replace(fetched, items=items)
    skipped = adapter().skipped(f)
    assert skipped["8443970"] == "no harmonic constituents"
    assert skipped["8444351"].startswith("reference 8443970 is skipped")


def test_unknown_offset_type_is_skipped(fetched):
    items = dict(fetched.items)
    items["8444351"] = {**items["8444351"], "offsets": {**items["8444351"]["offsets"], "heightAdjustedType": "X"}}
    assert adapter().skipped(replace(fetched, items=items)) == {
        "8444351": "heightAdjustedType 'X' is not R or F"}


def test_additive_offsets_in_feet_become_metres(tmp_path):
    a = adapter()
    f = a.fetch(tmp_path, only=["8218617"])
    assert sorted(f.items) == ["8218617", "9450460"]
    assert by_id(a.sets(f))["8218617"].subordinate_offsets == {
        "reference_source_record_id": "9450460", "time_offset_high_min": 51, "time_offset_low_min": 77,
        "height_offset_high": 0.03048, "height_offset_low": -0.03048, "height_adjusted_type": "A",
        "licence_id": "noaa-public-domain"}
    assert check_contract(a, f) == []


def test_unknown_station_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not NOAA tide-prediction stations: 0000000"):
        adapter().fetch(tmp_path, only=["0000000"])


def test_requests(tmp_path):
    a = NoaaAdapter(fetcher_factory=lambda d: seen.setdefault("f", FixtureFetcher(d)), speeds=SPEEDS)
    seen: dict = {}
    a.fetch(tmp_path, only=["8444351"])
    assert seen["f"].urls == [
        f"{noaa.API}/stations.json?type=tidepredictions",
        f"{noaa.API}/stations/8443970/harcon.json?units=metric",
        f"{noaa.API}/stations/8444351/tidepredoffsets.json",
    ]


def test_watch_keys_follow_content(fetched):
    a = adapter()
    keys = a.watch_keys(fetched)
    assert sorted(keys) == sorted(SAMPLE)
    items = dict(fetched.items)
    # a change in a URL field only does not change the key
    items["8443970"] = {**items["8443970"], "harcon": {**items["8443970"]["harcon"], "self": "x"}}
    assert a.watch_keys(replace(fetched, items=items))["8443970"] == keys["8443970"]
    cons = [dict(c) for c in items["8443970"]["harcon"]["HarmonicConstituents"]]
    cons[0]["amplitude"] += 0.001
    items["8443970"] = {**items["8443970"], "harcon": {"units": "meters", "HarmonicConstituents": cons}}
    changed = a.watch_keys(replace(fetched, items=items))
    assert changed["8443970"] != keys["8443970"]
    assert changed["9447130"] == keys["9447130"]


def test_licence():
    lic = NoaaAdapter().licence()
    assert lic["spdx"] == "LicenseRef-PublicDomain-USGov"
    assert "not official NOAA material" in lic["attribution"]   # units are converted (test_licences)


@pytest.mark.parametrize("sid, lat, lon, want", [
    ("8443970", 42.35388888888889, -71.05027777777778, ("USA", "America/New_York")),
    ("8728819", 29.821399688720703, -85.17330169677734, ("USA", "America/Chicago")),            # Florida panhandle
    ("8447436", 41.6883, -67.76, ("USA", "America/New_York")),          # Georges Shoal: ocean zone -> land
    ("TPT2891", -19.03333333333299, 169.91666666667007, ("NIU", "Pacific/Niue")),           # NOAA longitude sign error
    ("9710441", 26.71, -78.9967, ("BHS", "America/Nassau")),
    ("1630000", 13.44338888888889, 144.6563611111111, ("GUM", "Pacific/Guam")),
])
def test_place(sid, lat, lon, want):
    assert place(sid, lat, lon) == want


# --- speeds: the astro table's, by name (KP0b) --------------------------------------------------------

def test_speeds_are_the_astro_rows(fetched):
    a = adapter()
    sets = by_id(a.sets(fetched))
    served = {c["name"]: float(c["speed"]) for c in fetched.items["8443970"]["harcon"]["HarmonicConstituents"]}
    cons = {c["name"]: c for c in sets["8443970"].constant_set["constituents"]}
    for n, c in cons.items():
        assert c["speed_deg_per_hour"] == SPEEDS.speed(n) and abs(served[n] - SPEEDS.speed(n)) <= 1e-4
    # NOAA's LAM2 and RHO keep their names; their rows are the TCD's LDA2 and RHO1 (KP0b)
    assert (cons["LAM2"]["speed_deg_per_hour"], served["LAM2"]) == (29.4556253, 29.455626)
    assert (cons["RHO"]["speed_deg_per_hour"], served["RHO"]) == (13.4715145, 13.471515)
    assert "dropped_constituents" not in sets["8443970"].constant_set


def test_a_speed_off_its_row_is_refused(fetched):
    fetched.items["8443970"]["harcon"]["HarmonicConstituents"][0]["speed"] += 2e-4
    with pytest.raises(SpeedDeviation, match="8443970"):
        list(adapter().sets(fetched))


def test_a_constituent_with_no_astro_row_is_dropped_and_recorded(fetched):
    hc = fetched.items["8443970"]["harcon"]["HarmonicConstituents"]
    c = next(c for c in hc if float(c["amplitude"]) > 0 and c["name"] == "M8")
    c["name"] = "2MS8"   # a NOAA name with no KP0b row (9455920 Anchorage serves it)
    cs = by_id(adapter().sets(fetched))["8443970"].constant_set
    assert "2MS8" not in {x["name"] for x in cs["constituents"]}
    assert cs["dropped_constituents"] == [{"name": "2MS8", "dropped_reason": "other", "detail": NO_ASTRO_ROW}]


def test_the_whole_release_validates_with_astro_tables(fetched):
    """B4: the NOAA shell checked whole on the pinned 1.0 schema, with a shape-only astro table."""
    from otc_pipeline.adapter import assemble_release
    from otc_pipeline.release_writer import validate_release

    from contract_stubs import shape_only_astro_tables
    a = adapter()
    sets = list(a.sets(fetched))
    release = assemble_release(a, sets, fetched, astro_tables=shape_only_astro_tables(sets))
    assert validate_release(release) == []
    assert release["conventions"][0]["astro_table_id"] == "test-shape-only"
    assert {cs["qc_status"] for st in release["stations"] for cs in st["constant_sets"]} == {"ok"}
