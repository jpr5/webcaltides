"""PEGELONLINE gauge adapter (manifest A-PEG-a).

The fixtures are real objects from the work-bucket archive (`archive/pegelonline/`): the station
snapshot and its MANIFEST, and two harvested UTC days (MANIFEST plus the CSVs of the three named
stations). `LocalStore` reads them with the same key layout as the bucket.
`characteristic.synthetic.json` is a SYNTHETIC body in the shape of the live characteristic-values
response (invented values, no fetched data in git), served by `CharacteristicStub` for DT5e.

Every expected number here is read from the raw CSVs by this test, independently of the adapter.
Set OTC_LIVE=1 to run the same comparison on the full live archive.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from otc_pipeline.adapter import check_contract
from otc_pipeline.adapters.pegelonline import (
    ARCHIVE, DECLARED_TIME_BASE, LocalStore, PegelonlineAdapter, WorkBucketStore,
)
from otc_pipeline.adapter import sha256_file
from otc_pipeline.adapters import pegelonline
from otc_pipeline.http import Fetched
from otc_pipeline.sources import SOURCES, registry_errors

FIXTURES = Path(__file__).parent / "fixtures" / "pegelonline"
LIVE = os.environ.get("OTC_LIVE") == "1"

CHARACTERISTIC = FIXTURES / "characteristic.synthetic.json"


class CharacteristicStub:
    """Serves the synthetic characteristic-values body, as PoliteFetcher would after a live fetch."""

    def __init__(self, cache_dir: Path, body: bytes | None = None):
        self.cache_dir, self.body, self.calls = Path(cache_dir), body, []

    def get(self, url, *, params=None, cache_name=None, refresh=False):
        self.calls.append((url, dict(params or {}), cache_name, refresh))
        dest = self.cache_dir / cache_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.body if self.body is not None else CHARACTERISTIC.read_bytes())
        return Fetched(url=url, status=200, path=dest, name=cache_name, sha256=sha256_file(dest),
                       size=dest.stat().st_size, fetched_at="2026-10-10T00:00:00Z", from_cache=False)


def peg(store, body: bytes | None = None) -> PegelonlineAdapter:
    return PegelonlineAdapter(store=store, characteristic_fetcher=lambda d: CharacteristicStub(d, body))


NAMED = {
    "aad49293-242a-43ad-a8b1-e91d7792c4b2": "CUXHAVEN STEUBENHÖFT",
    "c0ec139b-13b4-4f86-bee3-06665ad81a40": "HELGOLAND BINNENHAFEN",
    "d3f822a0-e201-4a61-8913-589c74818ae0": "BHV ALTER LEUCHTTURM",   # Bremerhaven Alter Leuchtturm
}


def raw_series(root: Path, uuid: str) -> tuple[list[int], list[float]]:
    """(UTC seconds, cm) from every harvested day file of one station, read directly."""
    times, values = [], []
    for f in sorted((root / ARCHIVE).glob(f"[0-9][0-9][0-9][0-9]/*/*/{uuid}.csv.gz")):
        rows = list(csv.reader(io.StringIO(gzip.decompress(f.read_bytes()).decode("utf-8"))))
        assert rows[0] == ["timestamp_utc", "value_cm"]
        for ts, v in rows[1:]:
            times.append(int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
                             .replace(tzinfo=timezone.utc).timestamp()))
            values.append(float(v))
    return times, values


@pytest.fixture
def fetched(tmp_path):
    adapter = peg(LocalStore(FIXTURES))
    return adapter, adapter.fetch(tmp_path / "cache", only=sorted(NAMED))


def test_registered():
    assert SOURCES["pegelonline"] is PegelonlineAdapter
    assert not [e for e in registry_errors() if "pegelonline" in e]
    assert set(PegelonlineAdapter.contract_sample) == set(NAMED)


def test_named_stations_match_the_raw_files(fetched):
    adapter, got = fetched
    records = {r.record_id: r for r in adapter.records(got)}
    assert set(records) == {f"pegelonline:{u}" for u in NAMED}
    for uuid in NAMED:
        r = records[f"pegelonline:{uuid}"]
        times, values = raw_series(FIXTURES, uuid)
        assert len(times) > 2000                          # two whole days of 1-min values
        assert len(r) == len(times)
        assert int(r.times_s[0]) == times[0] and int(r.times_s[-1]) == times[-1]
        assert np.array_equal(r.times_s, np.asarray(times, dtype=np.int64))
        assert np.allclose(r.heights_m, np.asarray(values) / 100.0)
        assert r.good.all()


def test_record_metadata(fetched):
    adapter, got = fetched
    snap = json.loads((FIXTURES / ARCHIVE / "stations" / "2026-10-08.json").read_text())
    by_uuid = {s["uuid"]: s for s in snap["stations"]}
    for r in adapter.records(got):
        st = by_uuid[r.record_id.split(":", 1)[1]]
        assert (r.lat, r.lon) == (st["latitude"], st["longitude"])
        assert r.source == "pegelonline" and r.source_version == "20260921" == got.source_version
        assert r.contributor == "WSV" and r.originator is True
        assert r.licence_class == "DL-DE-ZERO-2.0"
        assert r.interval_min == st["W"]["equidistance_min"] == 1
        assert r.sampling == "instantaneous"
        assert r.declared_time_base == DECLARED_TIME_BASE == "utc_instant"
        assert r.gauge_type == "Coastal"                  # GESLA-4.1 WSV type for all three
        assert len(r.member_sha256) == 64


def test_contract_passes_on_saved_responses(fetched):
    adapter, got = fetched
    assert check_contract(adapter, got) == []
    assert adapter.licence()["commercial_use"] is True
    # what the fit's time-base annotation publishes as declared: a format 1.0 class
    assert {r.declared_time_base for r in adapter.records(got)} == {"utc_instant"}


def test_watch_keys_follow_the_series(tmp_path, fetched):
    adapter, got = fetched
    keys = adapter.watch_keys(got)
    assert set(keys) == set(NAMED)
    # one changed sample in one day file changes only that station's key
    root = tmp_path / "edited"
    shutil.copytree(FIXTURES, root)
    uuid = "aad49293-242a-43ad-a8b1-e91d7792c4b2"
    f = root / ARCHIVE / "2026/09/21" / f"{uuid}.csv.gz"
    lines = gzip.decompress(f.read_bytes()).decode().splitlines()
    ts, v = lines[5].split(",")
    lines[5] = f"{ts},{float(v) + 1:g}"
    f.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
    _rehash(root, "2026/09/21", f)
    other = peg(LocalStore(root))
    keys2 = other.watch_keys(other.fetch(tmp_path / "cache2", only=sorted(NAMED)))
    assert keys2[uuid] != keys[uuid]
    assert all(keys2[u] == keys[u] for u in NAMED if u != uuid)


def _rehash(root: Path, day: str, f: Path) -> None:
    import hashlib
    m = root / ARCHIVE / day / "MANIFEST.json"
    doc = json.loads(m.read_text())
    for e in doc["files"]:
        if e["path"] == f.name:
            e["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
            e["size"] = f.stat().st_size
    m.write_text(json.dumps(doc))


def test_a_file_that_differs_from_its_manifest_is_refused(tmp_path):
    root = tmp_path / "bad"
    shutil.copytree(FIXTURES, root)
    f = root / ARCHIVE / "2026/09/20" / "c0ec139b-13b4-4f86-bee3-06665ad81a40.csv.gz"
    f.write_bytes(f.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="MANIFEST"):
        peg(LocalStore(root)).fetch(tmp_path / "cache", only=sorted(NAMED))


def test_a_stamp_outside_its_day_directory_is_refused(tmp_path):
    root = tmp_path / "moved"
    shutil.copytree(FIXTURES, root)
    uuid = "d3f822a0-e201-4a61-8913-589c74818ae0"
    f = root / ARCHIVE / "2026/09/20" / f"{uuid}.csv.gz"
    lines = gzip.decompress(f.read_bytes()).decode().splitlines()
    lines.append("2026-09-22T00:00:00Z,500")
    f.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
    _rehash(root, "2026/09/20", f)
    adapter = peg(LocalStore(root))
    got = adapter.fetch(tmp_path / "cache", only=[uuid])
    with pytest.raises(ValueError, match="outside its day"):
        list(adapter.records(got))


def test_an_unknown_station_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not in the station snapshot"):
        peg(LocalStore(FIXTURES)).fetch(
            tmp_path / "cache", only=["00000000-0000-0000-0000-000000000000"])


@pytest.mark.skipif(not LIVE, reason="live archive: set OTC_LIVE=1")
def test_named_stations_match_the_live_archive(tmp_path):
    adapter = PegelonlineAdapter(store=WorkBucketStore())
    got = adapter.fetch(tmp_path / "cache", only=sorted(NAMED))
    assert check_contract(adapter, got) == []
    for r in adapter.records(got):
        times, _ = raw_series(tmp_path / "cache", r.record_id.split(":", 1)[1])
        assert (len(r), int(r.times_s[0]), int(r.times_s[-1])) == (len(times), times[0], times[-1])


# -- published datums (datums spec §4.1 item 6, DT5e) on the synthetic characteristic values ------

def test_characteristic_values_are_one_live_request(tmp_path):
    stubs = []
    a = PegelonlineAdapter(store=LocalStore(FIXTURES),
                           characteristic_fetcher=lambda d: stubs.append(CharacteristicStub(d)) or stubs[-1])
    got = a.fetch(tmp_path / "cache", only=sorted(NAMED))
    assert stubs[0].calls == [(pegelonline.CHARACTERISTIC_URL,
                               {"includeTimeseries": "true", "includeCharacteristicValues": "true"},
                               "characteristic/stations.json", True)]
    assert "characteristic/stations.json" in {f.path for f in got.files}


def test_published_datums_are_mthw_mtnw_and_nhn_above_pnp(fetched):
    adapter, got = fetched
    pub = adapter.published_datums(got)
    cux = pub["pegelonline:aad49293-242a-43ad-a8b1-e91d7792c4b2"]["datum"]
    span = {"start": "2010-11-01", "end": "2020-10-31"}
    assert cux == {"zero": "gauge_zero", "zero_name": "PNP",
                   "named": {"mhw": 6.5, "mlw": 3.6, "nhn": 5.0},
                   "basis": {"mhw": {"kind": "published", "method": "source", "source_name": "MThw",
                                     "epoch": {**span, "name": "PEGELONLINE MThw timespan"}},
                             "mlw": {"kind": "published", "method": "source", "source_name": "MTnw",
                                     "epoch": {**span, "name": "PEGELONLINE MTnw timespan"}},
                             "nhn": {"kind": "published", "method": "source", "source_name": "NHN"}}}
    assert pub["pegelonline:aad49293-242a-43ad-a8b1-e91d7792c4b2"]["notes"]["gauge_zero_valid_from"] == "2019-11-01"
    # Helgoland's gauge zero is in m NN (DHHN12), not NHN: no nhn level; its own timespan
    hel = pub["pegelonline:c0ec139b-13b4-4f86-bee3-06665ad81a40"]["datum"]
    assert set(hel["named"]) == {"mhw", "mlw"} and hel["basis"]["mhw"]["epoch"]["start"] == "2005-11-01"
    # Bremerhaven publishes no MThw/MTnw here: only its NHN gauge zero
    assert pub["pegelonline:d3f822a0-e201-4a61-8913-589c74818ae0"]["datum"]["named"] == {"nhn": 5.5}
    for item in pub.values():
        assert "chart_datum" not in item["datum"]          # PEGELONLINE states no chart datum


def test_a_characteristic_value_not_in_cm_is_refused(tmp_path):
    doc = json.loads(CHARACTERISTIC.read_text())
    doc[0]["timeseries"][0]["characteristicValues"][0]["unit"] = "m"
    body = json.dumps(doc).encode()
    a = peg(LocalStore(FIXTURES), body)
    got = a.fetch(tmp_path / "cache", only=sorted(NAMED))
    with pytest.raises(ValueError, match="MThw"):
        a.published_datums(got)


def test_watch_key_follows_the_characteristic_values(tmp_path):
    keys = peg(LocalStore(FIXTURES)).watch_keys(peg(LocalStore(FIXTURES)).fetch(tmp_path / "a", only=sorted(NAMED)))
    doc = json.loads(CHARACTERISTIC.read_text())
    doc[0]["timeseries"][0]["characteristicValues"][0]["value"] = 651.0
    body = json.dumps(doc).encode()
    a = peg(LocalStore(FIXTURES), body)
    keys2 = a.watch_keys(a.fetch(tmp_path / "b", only=sorted(NAMED)))
    cux = "aad49293-242a-43ad-a8b1-e91d7792c4b2"
    assert keys2[cux] != keys[cux] and all(keys2[u] == keys[u] for u in NAMED if u != cux)
