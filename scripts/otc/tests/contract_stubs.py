"""Offline stub adapters for the adapter contract test (manifest §2.4).

They read nothing from the network: `fetch` writes a small payload into the cache directory and
returns it, so the contract can be checked on known-good and known-bad output.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult, InputFile, SourceSet

FETCHED_AT = "2099-12-31T00:00:00Z"
VERSION = "20991231"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(cache_dir: Path, name: str, payload) -> InputFile:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / name).write_bytes(data)
    return InputFile(path=name, sha256=_sha(data), size=len(data),
                     source_url=f"https://example.invalid/{name}", fetched_at=FETCHED_AT)


OFFICIAL_PAYLOAD = {
    "AAA": {"name": "Alpha Harbour", "lat": 60.0, "lon": 5.0,
            "constituents": [["M2", 28.9841042, 0.5, 10.0], ["S2", 30.0, 0.2, 40.0]]},
    "BBB": {"name": "Bravo Harbour", "lat": 61.0, "lon": 6.0,
            "constituents": [["M2", 28.9841042, 0.6, 12.0], ["K1", 15.0410686, 0.1, 200.0]]},
}


class OfficialStub:
    """A well-formed official source: two reference stations, constants in Greenwich phase."""

    source = "kartverket"
    source_type = "official"
    quantity = "water_level"
    adapter_version = "stub-1"
    contract_sample = ("AAA",)

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        payload = {k: v for k, v in OFFICIAL_PAYLOAD.items() if only is None or k in only}
        f = _write(Path(cache_dir), "stations.json", payload)
        return FetchResult(source=self.source, source_version=VERSION, cache_dir=Path(cache_dir),
                           files=(f,), items=payload)

    def convention(self) -> dict:
        return {"convention_id": "stub-greenwich", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "stub"}

    def licence(self) -> dict:
        return {"licence_id": "stub-cc-by", "spdx": "CC-BY-4.0", "provider": "Stub provider",
                "attribution": "Stub attribution.", "commercial_use": True}

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        return {k: _sha(json.dumps(v, sort_keys=True).encode()) for k, v in fetched.items.items()}

    def records(self, fetched: FetchResult):
        return ()

    def _constant_set(self, fetched: FetchResult, code: str, st: dict) -> dict:
        return {
            "source": self.source, "source_type": self.source_type, "quantity": self.quantity,
            "source_record_id": code, "source_version": fetched.source_version,
            "convention_id": self.convention()["convention_id"],
            "licence_id": self.licence()["licence_id"], "qc_status": "ok",
            "constituents": [{"name": n, "speed_deg_per_hour": s, "amplitude_m": a, "phase_deg": g}
                             for n, s, a, g in st["constituents"]],
            "provenance": {"adapter_version": self.adapter_version,
                           "input_sha256": [f.sha256 for f in fetched.files]},
        }

    def sets(self, fetched: FetchResult):
        for code, st in sorted(fetched.items.items()):
            yield SourceSet(
                station_hint={"name": st["name"], "country": "NOR", "lat": st["lat"], "lon": st["lon"],
                              "type": "reference", "timezone": "Europe/Oslo",
                              "aliases": {"kartverket": code}},
                constant_set=self._constant_set(fetched, code, st),
                subordinate_offsets=None)


class MissingConventionStub(OfficialStub):
    """The F2 RED case: every set leaves out `convention_id`."""

    def sets(self, fetched: FetchResult):
        for ss in super().sets(fetched):
            cs = {k: v for k, v in ss.constant_set.items() if k != "convention_id"}
            yield replace(ss, constant_set=cs)


class SubordinateStub(OfficialStub):
    """BBB becomes a subordinate of AAA (NOAA-style offsets, no constants of its own)."""

    source = "noaa"
    adapter_version = "stub-sub-1"

    def licence(self) -> dict:
        return {"licence_id": "stub-pd", "spdx": "LicenseRef-PublicDomain-USGov",
                "provider": "Stub agency", "attribution": "Stub agency.",
                "commercial_use": True}

    def sets(self, fetched: FetchResult):
        for ss in super().sets(fetched):
            code = ss.constant_set["source_record_id"]
            if code == "BBB":
                hint = {**ss.station_hint, "type": "subordinate", "timezone": "America/New_York",
                        "aliases": {"noaa": code}}
                yield SourceSet(station_hint=hint, constant_set=None, subordinate_offsets={
                    "reference_source_record_id": "AAA", "time_offset_high_min": 5.0,
                    "time_offset_low_min": 7.0, "height_offset_high": 1.1, "height_offset_low": 0.9,
                    "height_adjusted_type": "R", "licence_id": "stub-pd"})
            else:
                yield replace(ss, station_hint={**ss.station_hint, "timezone": "America/New_York",
                                                "aliases": {"noaa": code}})


GAUGE_PAYLOAD = {
    "111": {"lat": 54.0, "lon": 8.0, "t0": 1_700_000_000, "heights": [1.0, 1.5, 2.0, 1.5]},
    "222": {"lat": 53.5, "lon": 8.5, "t0": 1_700_000_000, "heights": [0.5, 0.7, 0.9]},
}


class GaugeStub:
    """A well-formed gauge source: observations as GaugeRecord, no sets."""

    source = "pegelonline"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "stub-g-1"
    contract_sample = ("111",)
    record_source = "pegelonline"

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        payload = {k: v for k, v in GAUGE_PAYLOAD.items() if only is None or k in only}
        f = _write(Path(cache_dir), "obs.json", payload)
        return FetchResult(source=self.source, source_version=VERSION, cache_dir=Path(cache_dir),
                           files=(f,), items=payload)

    def convention(self) -> dict:
        return {"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "stub"}

    def licence(self) -> dict:
        return {"licence_id": "stub-dl-de", "spdx": "DL-DE-ZERO-2.0", "provider": "Stub WSV",
                "attribution": "Stub WSV.", "commercial_use": True}

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        return {k: _sha(json.dumps(v, sort_keys=True).encode()) for k, v in fetched.items.items()}

    def records(self, fetched: FetchResult):
        for code, st in sorted(fetched.items.items()):
            n = len(st["heights"])
            yield GaugeRecord(
                record_id=f"{self.record_source}:{code}", source=self.record_source,
                source_version=fetched.source_version, contributor="WSV", originator=True,
                licence_class="DL-DE-ZERO-2.0", lat=st["lat"], lon=st["lon"], gauge_type="Coastal",
                sampling="instantaneous", interval_min=15, declared_time_base="utc_instant",
                member_sha256=fetched.files[0].sha256,
                times_s=np.arange(n, dtype=np.int64) * 900 + st["t0"],
                heights_m=np.asarray(st["heights"], dtype=np.float64), good=np.ones(n, dtype=bool))

    def sets(self, fetched: FetchResult):
        return ()


class WrongRecordSourceStub(GaugeStub):
    """Records claim another source than the adapter's."""

    record_source = "rws"


class GarbageFetchStub(OfficialStub):
    """fetch returns something that is not a FetchResult."""

    def fetch(self, cache_dir: Path, only: list[str] | None = None):
        return {"not": "a FetchResult"}


class NoneKeysStub(OfficialStub):
    """watch_keys returns None."""

    def watch_keys(self, fetched: FetchResult):
        return None


def shape_only_astro_tables(sets, table_id: str = "test-shape-only") -> list[dict]:
    """TEST ONLY: one astro table with a row for every constituent the sets use (their own speeds;
    V0+u 0 and f 1, which are not real values), so a test can check a whole contract shell on the
    pinned schema. The release's tables are the build's (KP0b), and the schema does not check the
    values (build check 7 does)."""
    rows = {}
    for ss in sets:
        cs = ss.constant_set if isinstance(ss, SourceSet) and isinstance(ss.constant_set, dict) else {}
        cons = list(cs.get("constituents") or []) + [c for b in cs.get("current_bins") or []
                                                     for c in b.get("constituents") or []]
        for c in cons:
            rows.setdefault(c["name"], {"speed_deg_per_hour": c["speed_deg_per_hour"], "v0u_deg": [0.0], "f": [1.0]})
    return [{"astro_table_id": table_id, "v0_model": "schureman_tcd", "tables_sha256": "0" * 64,
             "generator": "TEST ONLY: shape only, not V0+u or f values", "first_year": 2026, "last_year": 2026,
             "constituents": rows or {"M2": {"speed_deg_per_hour": 28.9841042, "v0u_deg": [0.0], "f": [1.0]}}}]
