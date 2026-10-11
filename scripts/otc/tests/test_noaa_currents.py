"""NOAA CO-OPS tidal-current adapter (manifest A-NOAAC) on saved real responses.

Fixtures (tests/fixtures/noaa_currents/, fetched 2026-10-08): the full current-prediction station
list (metric, gzipped), harcon.json for ACT1616 (one bin), BOS1101 (three bins), EPT0003 (four
bins) and PUG1501 (two listed bins, eight in harcon), currentpredictionoffsets.json for their
bins and for ACT0091 (subordinate to EPT0003 bin 11), and NOAA's MAX_SLACK current predictions
for 2026-10-08 + 7 days at the proof bins.
"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from otc_pipeline.adapter import assemble_release, assert_contract, check_contract, sha256_file
from otc_pipeline import current_proof
from otc_pipeline.adapters import noaa_currents
from otc_pipeline.adapters.noaa_currents import NoaaCurrentsAdapter
from otc_pipeline.constituent_speeds import SpeedDeviation, TableSpeeds
from otc_pipeline.http import Fetched
from otc_pipeline.release_writer import validate_release
from otc_pipeline.sources import SOURCES, registry_errors

from contract_stubs import shape_only_astro_tables

FIX = Path(__file__).parent / "fixtures" / "noaa_currents"
FETCHED_AT = "2026-10-08T17:30:00Z"


class FixtureFetcher:
    """Serves the saved responses by cache name, as PoliteFetcher would after a live fetch."""

    def __init__(self, cache_dir: Path, overrides: dict | None = None):
        self.cache_dir = Path(cache_dir)
        self.overrides = overrides or {}

    def get(self, url, *, params=None, cache_name=None, **_):
        full = url + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
        dest = self.cache_dir / cache_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if cache_name in self.overrides:
            dest.write_text(json.dumps(self.overrides[cache_name]))
        elif cache_name == noaa_currents.LIST_NAME:
            dest.write_bytes(gzip.decompress((FIX / "stations.json.gz").read_bytes()))
        else:
            src = FIX / cache_name
            if not src.exists():
                raise FileNotFoundError(f"no fixture for {cache_name}")
            shutil.copyfile(src, dest)
        return Fetched(url=full, status=200, path=dest, name=cache_name, sha256=sha256_file(dest),
                       size=dest.stat().st_size, fetched_at=FETCHED_AT, from_cache=False)


SPEEDS = TableSpeeds.from_json(FIX / "tcd_speeds.json")   # the astro rows' speeds (TCD extract)


def adapter(overrides: dict | None = None, **kw) -> NoaaCurrentsAdapter:
    return NoaaCurrentsAdapter(fetcher_factory=lambda d: FixtureFetcher(d, overrides), **{"speeds": SPEEDS, **kw})


SAMPLE = ["ACT1616", "BOS1101", "ACT0091"]


@pytest.fixture
def fetched(tmp_path):
    return adapter().fetch(tmp_path, only=SAMPLE)


def sets_by_id(a, fetched):
    return {(s.constant_set or {}).get("source_record_id") or s.station_hint["aliases"]["noaa"]: s
            for s in a.sets(fetched)}


def bin_of(ss, n):
    return next(b for b in ss.constant_set["current_bins"] if b["bin"] == n)


def con(b, name):
    return next(c for c in b["constituents"] if c["name"] == name)


def test_station_list_counts():
    rows = json.loads(gzip.decompress((FIX / "stations.json.gz").read_bytes()))["stations"]
    kinds = [r["type"] for r in rows]
    assert (len(rows), kinds.count("H"), kinds.count("S"), kinds.count("W")) == (4439, 2221, 1939, 279)
    assert len({r["id"] for r in rows}) == 2788


def test_registered():
    assert SOURCES["noaa-currents"] is NoaaCurrentsAdapter
    assert registry_errors() == []


def test_contract_passes_on_saved_responses(fetched):
    assert_contract(adapter(), fetched)


def test_subordinate_fetch_pulls_in_its_reference(fetched):
    assert set(fetched.items) == {"ACT1616", "BOS1101", "ACT0091", "EPT0003"}


def test_act1616_bin_converted_unmodified(fetched):
    ss = sets_by_id(adapter(), fetched)["ACT1616"]
    cs = ss.constant_set
    assert (cs["quantity"], cs["constituents"], cs["source"]) == ("current", [], "noaa-currents")
    b = bin_of(ss, 1)
    # station-list depth (metric; NOAA's predictions report 4.6 m), not harcon binDepth 2.13
    assert (b["depth_m"], b["depth_type"], b["azimuth_deg"]) == (4.57, "below_surface", 37.0)
    assert (b["mean_flood_dir_deg"], b["mean_ebb_dir_deg"]) == (37.0, 226.0)
    m2 = con(b, "M2")
    assert (m2["major_amplitude_ms"], m2["major_phase_deg"], m2["speed_deg_per_hour"]) == (0.9353, 322.3, 28.9841042)   # the astro row's speed
    assert (m2["minor_amplitude_ms"], m2["minor_phase_deg"]) == (0.0, 0.0)
    assert (b["mean_major_ms"], b["mean_minor_ms"]) == (0.0772, 0.0)   # majorMeanSpeed 7.72 cm/s
    assert len(b["constituents"]) == 37 and all(c["name"] != "Z0" for c in b["constituents"])
    assert ss.station_hint["default_current_bin"] == 1
    assert ss.station_hint["aliases"] == {"noaa": "ACT1616"}
    assert (ss.station_hint["country"], ss.station_hint["timezone"]) == ("USA", "America/New_York")


def test_bos1101_three_bins_default_shallowest(fetched):
    ss = sets_by_id(adapter(), fetched)["BOS1101"]
    assert [b["bin"] for b in ss.constant_set["current_bins"]] == [15, 11, 1]
    assert ss.station_hint["default_current_bin"] == 15
    b1 = bin_of(ss, 1)
    # NOAA depthType B is "below chart datum" (MDAPI docs)
    assert (b1["depth_m"], b1["depth_type"], b1["azimuth_deg"]) == (15.24, "below_chart_datum", 270.7)
    assert (b1["mean_major_ms"], b1["mean_minor_ms"]) == (-0.0206, 0.0021)   # signed, cm/s / 100
    assert con(b1, "M2")["minor_amplitude_ms"] == 0.0051


def test_subordinate_current_offsets(fetched, tmp_path):
    a = adapter()
    s = sets_by_id(a, fetched)
    sub = s["ACT0091"]
    assert sub.constant_set is None and sub.station_hint["type"] == "subordinate"
    (co,) = sub.subordinate_offsets["current_offsets"]
    assert (co["reference_source_record_id"], co["reference_bin"], co["flood_amp_ratio"],
            co["time_adj_max_flood_min"], co["mean_flood_dir_deg"]) == ("EPT0003", 11, 1.2, 0, 210.0)
    sets = list(a.sets(fetched))
    # the astro tables are the build's (KP0b); a shape-only test table makes the shell whole
    release = assemble_release(a, sets, fetched, a.constituent_table(), shape_only_astro_tables(sets))
    st = next(x for x in release["stations"] if x.get("aliases") == {"noaa": "ACT0091"})
    ref = next(x for x in release["stations"] if x.get("aliases") == {"noaa": "EPT0003"})
    assert st["current_offsets"][0]["reference_station_id"] == ref["station_id"]
    assert validate_release(release) == []


def test_reference_bin_must_exist(tmp_path):
    off = json.loads((FIX / "offsets" / "ACT0091_1.json").read_text())
    off["refStationBin"] = 99
    a = adapter({"offsets/ACT0091_1.json": off})
    f = a.fetch(tmp_path, only=["ACT0091"])
    assert a.skipped(f)["ACT0091_1"] == "reference bin EPT0003_99 is not a usable H bin"
    assert "ACT0091" not in sets_by_id(a, f)


def test_weak_and_variable_station_makes_nothing(tmp_path):
    a = adapter()
    f = a.fetch(tmp_path, only=["ACT0261"])     # W: no harcon or offsets request
    assert [x.path for x in f.files] == ["stations.json"]
    sk = a.skipped(f)
    assert sk["ACT0261"].startswith("W only: weak and variable")
    assert list(a.sets(f)) == []


def test_watch_keys_per_bin(tmp_path, fetched):
    a = adapter()
    keys = a.watch_keys(fetched)
    assert {"BOS1101", "BOS1101_1", "BOS1101_11", "BOS1101_15", "ACT0091_1"} <= set(keys)
    h = json.loads((FIX / "harcon" / "BOS1101.json").read_text())
    for row in h["HarmonicConstituents"]:
        if row["binNbr"] == 11 and row["constituentName"] == "M2":
            row["majorAmplitude"] += 0.01
    f2 = adapter({"harcon/BOS1101.json": h}).fetch(tmp_path / "2", only=SAMPLE)
    k2 = a.watch_keys(f2)
    changed = sorted(k for k in keys if keys[k] != k2[k])
    assert changed == ["BOS1101", "BOS1101_11"]


def test_bad_constituent_number_skips_the_bin(tmp_path):
    h = json.loads((FIX / "harcon" / "ACT1616.json").read_text())
    h["HarmonicConstituents"][0]["constNum"] = 2
    a = adapter({"harcon/ACT1616.json": h})
    f = a.fetch(tmp_path, only=["ACT1616"])
    assert "constNum 2" in a.skipped(f)["ACT1616_1"]
    assert a.skipped(f)["ACT1616"] == "no H bin with usable constants"


def test_red_flags(fetched):
    nb = sets_by_id(adapter(drop="bins"), fetched)["BOS1101"]
    assert con(bin_of(nb, 1), "M2") == con(bin_of(nb, 15), "M2")
    na = sets_by_id(adapter(drop="azimuth"), fetched)["BOS1101"]
    assert {b["azimuth_deg"] for b in na.constant_set["current_bins"]} == {0.0}
    nm = sets_by_id(adapter(drop="mean"), fetched)["ACT1616"]
    assert "mean_major_ms" not in bin_of(nm, 1) and "mean_minor_ms" not in bin_of(nm, 1)
    with pytest.raises(ValueError):
        NoaaCurrentsAdapter(drop="depth")


def test_contract_rejects_reference_station_id_in_current_offsets(fetched):
    a = adapter()
    sets = list(a.sets(fetched))
    sub = next(s for s in sets if s.constant_set is None)
    sub.subordinate_offsets["current_offsets"][0]["reference_station_id"] = "OTC-X"
    errors = check_contract(_Fixed(a, sets), fetched)
    assert any("current_offsets reference_station_id is assigned by the build" in e for e in errors), errors


class _Fixed:
    """The adapter, with sets() returning given sets."""
    def __init__(self, a, sets):
        self._a, self._sets = a, sets

    def __getattr__(self, name):
        return getattr(self._a, name)

    def sets(self, fetched):
        return self._sets


# -- proof helpers ------------------------------------------------------------------------

def test_engine_input_flood_sign(fetched):
    s = sets_by_id(adapter(), fetched)
    b = bin_of(s["BOS1101"], 15)                           # azimuth 260, flood 249: same sign
    inp = current_proof.engine_input(b, datetime(2026, 10, 8, tzinfo=timezone.utc),
                                     datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert next(c for c in inp["constituents"] if c["name"] == "M2")["amp"] > 0
    flipped = {**b, "azimuth_deg": (b["azimuth_deg"] + 180) % 360}
    inp2 = current_proof.engine_input(flipped, datetime(2026, 10, 8, tzinfo=timezone.utc),
                                      datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert next(c for c in inp2["constituents"] if c["name"] == "M2")["amp"] < 0
    assert inp2["datum_offset"] == -inp["datum_offset"]
    act = current_proof.engine_input(bin_of(s["ACT1616"], 1), datetime(2026, 10, 8, tzinfo=timezone.utc),
                                     datetime(2026, 10, 9, tzinfo=timezone.utc))
    names = {c["name"] for c in act["constituents"]}
    assert {"LDA2", "RHO1"} <= names and not {"LAM2", "RHO"} & names   # engine spellings
    assert act["datum_offset"] == 0.0772                              # mean_major_ms


def _has_engine() -> bool:
    try:
        return subprocess.run(["bundle", "exec", "ruby", "-e", "require 'tcd'"], cwd=current_proof.WEBCALTIDES,
                              capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _has_engine(), reason="needs the webcaltides Ruby bundle (tcd gem)")
@pytest.mark.parametrize("station,bin_,drop,ok", [
    ("ACT1616", 1, None, True), ("ACT1616", 1, "mean", False),
    ("BOS1101", 1, None, True), ("BOS1101", 1, "bins", False),
    ("BOS1101", 15, "azimuth", False),
])
def test_engine_reproduces_noaa_predictions(tmp_path, station, bin_, drop, ok):
    a = adapter(drop=drop)
    f = a.fetch(tmp_path, only=[station])
    b = bin_of(sets_by_id(a, f)[station], bin_)
    start = datetime(2026, 10, 8, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    model = current_proof.run_engine(current_proof.engine_input(b, start - timedelta(hours=6),
                                                                end + timedelta(hours=6)), tmp_path)
    noaa = json.loads((FIX / "predictions" / f"{station}_{bin_}_20261008_7d.json").read_text())
    assert current_proof.compare(station, bin_, noaa, model, start, end).passed is ok


# --- speeds: the astro table's, by name (KP0b) --------------------------------------------------------

def test_bin_speeds_and_the_constituent_table_are_the_astro_rows(fetched):
    a = adapter()
    for ss in a.sets(fetched):
        for b in (ss.constant_set or {}).get("current_bins") or []:
            for c in b["constituents"]:
                assert c["speed_deg_per_hour"] == SPEEDS.speed(c["name"])
                assert abs(noaa_currents.NOAA_SPEEDS[c["name"]] - c["speed_deg_per_hour"]) <= 1e-4
    rows = {r["name"]: r["speed_deg_per_hour"] for r in a.constituent_table()}
    assert rows == {n: SPEEDS.speed(n) for n in noaa_currents.NOAA_SPEEDS}
    assert (rows["LAM2"], rows["RHO"]) == (29.4556253, 13.4715145)


def test_a_noaa_speed_off_its_row_is_refused(fetched, monkeypatch):
    monkeypatch.setitem(noaa_currents.NOAA_SPEEDS, "M2", noaa_currents.NOAA_SPEEDS["M2"] + 2e-4)
    with pytest.raises(SpeedDeviation, match="M2"):
        list(adapter().sets(fetched))


def test_a_constituent_with_no_astro_row_is_left_out_and_recorded(fetched):
    """Today all 37 NOAA names have a row; a table without one (M6) shows the path: the bins leave it
    out, the set records it once, and the constituent table has no row for it."""
    from otc_pipeline.constituent_speeds import NO_ASTRO_ROW
    lacking = TableSpeeds({n: v for n, v in SPEEDS.speeds.items() if n != "M6"}, SPEEDS.aliases, SPEEDS.tcd_sha256)
    a = adapter(speeds=lacking)
    ss = sets_by_id(a, fetched)["ACT1616"]
    cs = ss.constant_set
    assert all("M6" not in {c["name"] for c in b["constituents"]} for b in cs["current_bins"])
    assert len(bin_of(ss, 1)["constituents"]) == 36
    assert cs["dropped_constituents"] == [{"name": "M6", "dropped_reason": "other", "detail": NO_ASTRO_ROW}]
    assert "M6" not in {r["name"] for r in a.constituent_table()} and len(a.constituent_table()) == 36
    # with the full table nothing is dropped
    assert "dropped_constituents" not in sets_by_id(adapter(), fetched)["ACT1616"].constant_set
