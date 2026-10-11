"""F2 proof adapter: Kartverket's live station list only (manifest F2 proof; A-KV builds the real one).

It fetches `tideapi.php?tide_request=stationlist` through PoliteFetcher and emits one set per
permanent station with no constants yet (qc_status "excluded"), so the contract runs on a real
upstream response. It is not registered in SOURCES and never ships.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from otc_pipeline.adapter import FetchResult, SourceSet
from otc_pipeline.http import PoliteFetcher

URL = "https://vannstand.kartverket.no/tideapi.php"
PARAMS = {"tide_request": "stationlist", "type": "perm", "lang": "en"}


class KartverketStationList:
    source = "kartverket"
    source_type = "official"
    quantity = "water_level"
    adapter_version = "f2-station-list-proof"
    contract_sample = ("BGO", "TRD")

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        got = PoliteFetcher(cache_dir).get(URL, params=PARAMS, cache_name="stationlist.xml")
        root = ET.fromstring(got.bytes())
        items = {}
        for loc in root.iter("location"):
            code = loc.get("code")
            if only is None or code in only:
                items[code] = {"name": loc.get("name"), "lat": float(loc.get("latitude")),
                               "lon": float(loc.get("longitude")), "type": loc.get("type")}
        meta = root.find("meta")
        self._licence_url = meta.get("licenseurl") if meta is not None else None
        version = got.fetched_at[:10].replace("-", "")
        return FetchResult(source=self.source, source_version=version, cache_dir=Path(cache_dir),
                           files=(got.input_file(),), items=items)

    def convention(self) -> dict:
        # Kartverket serves phases for UTC+1 (manifest A-KV); this proof emits no constants.
        return {"convention_id": "kartverket-utc+1", "phase_reference": "local", "utc_offset_hours": 1,
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "tcd"}

    def licence(self) -> dict:
        return {"licence_id": "kartverket-cc-by-4.0", "spdx": "CC-BY-4.0", "provider": "Kartverket",
                "attribution": "Contains data from Kartverket (the Norwegian Mapping Authority).",
                "url": "https://kartverket.no/data/lisens/"}

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        return {code: hashlib.sha256(json.dumps(st, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                for code, st in fetched.items.items()}

    def records(self, fetched: FetchResult):
        return ()

    def sets(self, fetched: FetchResult):
        for code, st in sorted(fetched.items.items()):
            svalbard = st["lat"] > 74.0
            yield SourceSet(
                station_hint={"name": st["name"], "country": "SJM" if svalbard else "NOR",
                              "lat": st["lat"], "lon": st["lon"], "type": "reference",
                              "timezone": "Arctic/Longyearbyen" if svalbard else "Europe/Oslo",
                              "aliases": {"kartverket": code}},
                constant_set={
                    "source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                    "source_record_id": code, "source_version": fetched.source_version,
                    "convention_id": self.convention()["convention_id"],
                    "licence_id": self.licence()["licence_id"], "qc_status": "excluded",
                    "constituents": [],
                    "provenance": {"adapter_version": self.adapter_version,
                                   "input_sha256": [f.sha256 for f in fetched.files]},
                })
