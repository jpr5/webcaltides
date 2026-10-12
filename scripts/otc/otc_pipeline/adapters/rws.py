"""Rijkswaterstaat (Netherlands) gauge adapter: WATHTE 10-min observations from DDAPI 2.0.

Endpoints (keyless JSON POST; measured 2026-10-08):

- catalogue:    POST {BASE}/METADATASERVICES/OphalenCatalogus
- observations: POST {BASE}/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen
  body {"Locatie": {"Code": <code>}, "AquoPlusWaarnemingMetadata": {"AquoMetadata": {...}},
        "Periode": {"Begindatumtijd": ISO, "Einddatumtijd": ISO}}; both ends are inclusive.
  One response holds at most about 160,000 values, so the adapter asks for one calendar month
  at a time. An empty period answers 204 with no body.

Stations: every catalogue location with both a measured and an astronomical WATHTE series
relative to NAP (RWS publishes an astronomical tide only for tidal gauges).

Time base, from the responses themselves (declared, then checked by the time-base audit):

- `Tijdstip` carries an explicit offset; every stamp seen is `+01:00` in January and July, so
  RWS stamps in MET (UTC+1, no summer time). The stamp is parsed with its own offset into Unix
  seconds; that is reading the stamp, not a correction.
- `AquoMetadata.WaardeBepalingsMethode` is `other:F007`, "Rekenkundig gemiddelde waarde over
  vorige 5 en volgende 5 minuten": each value is the mean over [t-5 min, t+5 min). A centred
  10-min mean has no lag and an M2 amplitude response of 0.9997, so the BP §2 class it should
  match is `utc_instant`; `sampling` is still recorded as "mean". Any other method fails the
  conversion: the adapter does not guess a time base it has not read from the source.

Quality: `good` is `Kwaliteitswaardecode == "00"` (normal value). Every other code (seen: "25";
"99" is a gap with value 999999999) is kept as not good. Gap values become NaN.
Heights: centimetres relative to NAP, converted to metres.
Licence: CC0 1.0 (rijkswaterstaatdata.nl/waterdata).

Layout in the cache directory (the work bucket's `cache/rws/`):

- `catalogue.json`            the raw catalogue response
- `obs/<code>/<YYYY-MM>.json` raw monthly responses (HTTP cache)
- `members/<code>.jsonl`      one raw monthly response body per line, in month order; this is
                              the record's member file and its SHA-256 is `member_sha256`
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from gesla.record import GaugeRecord

from otc_pipeline.adapter import FetchResult, InputFile, sha256_file
from otc_pipeline.http import PoliteFetcher

BASE = "https://ddapi20-waterwebservices.rijkswaterstaat.nl"
CATALOGUE_URL = f"{BASE}/METADATASERVICES/OphalenCatalogus"
OBSERVATIONS_URL = f"{BASE}/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"
CATALOGUE_REQUEST = {"CatalogusFilter": {"Grootheden": True, "Parameters": True, "Compartimenten": True,
                                         "Hoedanigheden": True, "Eenheden": True, "ProcesTypes": True}}
JSON_HEADERS = {"Content-Type": "application/json"}

# WaardeBepalingsMethode code -> (GaugeRecord.sampling, declared BP §2 time-base class)
METHOD_SAMPLING: dict[str, tuple[str, str]] = {
    "other:F007": ("mean", "utc_instant"),   # mean over previous 5 and next 5 minutes (centred)
}
GOOD_CODE = "00"
GAP_VALUE = 999999999.0
INTERVAL_S = 600


def observation_request(code: str, begin: str, end: str) -> dict:
    return {"Locatie": {"Code": code},
            "AquoPlusWaarnemingMetadata": {"AquoMetadata": {
                "Compartiment": {"Code": "OW"}, "Grootheid": {"Code": "WATHTE"},
                "Hoedanigheid": {"Code": "NAP"}, "ProcesType": "meting"}},
            "Periode": {"Begindatumtijd": begin, "Einddatumtijd": end}}


def _iso(d: date) -> str:
    return f"{d.isoformat()}T00:00:00.000+00:00"


def month_periods(start: date, end: date) -> list[tuple[str, str]]:
    """[start, end) as calendar-month (begin, end) pairs in UTC. Ends are inclusive upstream, so
    the boundary sample appears twice; `convert` merges it."""
    out = []
    d = date(start.year, start.month, 1)
    while d < end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        out.append((_iso(max(d, start)), _iso(min(nxt, end))))
        d = nxt
    return out


def _is_wathte_nap(meta: dict) -> bool:
    return (meta.get("Grootheid", {}).get("Code") == "WATHTE"
            and meta.get("Hoedanigheid", {}).get("Code") == "NAP"
            and meta.get("Compartiment", {}).get("Code") == "OW")


def tidal_locations(catalogue: dict) -> dict[str, dict]:
    """Catalogue locations with both a measured and an astronomical WATHTE/NAP series, by code."""
    ids = {"meting": set(), "astronomisch": set()}
    for m in catalogue.get("AquoMetadataLijst", []):
        if _is_wathte_nap(m) and m.get("ProcesType") in ids:
            ids[m["ProcesType"]].add(m["AquoMetadata_MessageID"])
    has: dict[int, set[str]] = {}
    for link in catalogue.get("AquoMetadataLocatieLijst", []):
        for kind, s in ids.items():
            if link.get("AquoMetaData_MessageID") in s:
                has.setdefault(link["Locatie_MessageID"], set()).add(kind)
    return {loc["Code"]: loc for loc in catalogue.get("LocatieLijst", [])
            if has.get(loc["Locatie_MessageID"]) == {"meting", "astronomisch"}}


def _responses(member: bytes) -> list[dict]:
    return [json.loads(line) for line in member.splitlines() if line.strip()]


def _parse(code: str, member: bytes):
    """(location, method, {unix_s: (value_cm, quality_code)}) from a member file."""
    location: dict | None = None
    methods: set[str] = set()
    samples: dict[int, tuple[float, str]] = {}
    for resp in _responses(member):
        for w in resp.get("WaarnemingenLijst", []):
            meta = w.get("AquoMetadata", {})
            if not _is_wathte_nap(meta) or meta.get("ProcesType") != "meting":
                continue
            unit = meta.get("Eenheid", {}).get("Code")
            if unit != "cm":
                raise ValueError(f"rws:{code}: unit {unit!r} is not cm")
            methods.add(meta.get("WaardeBepalingsMethode", {}).get("Code"))
            location = location or w.get("Locatie")
            for m in w.get("MetingenLijst", []):
                t = int(datetime.fromisoformat(m["Tijdstip"]).timestamp())
                v = m["Meetwaarde"]["Waarde_Numeriek"]
                q = m["WaarnemingMetadata"]["Kwaliteitswaardecode"]
                v = float("nan") if v is None else float(v)
                if t in samples and samples[t] != (v, q) and not (
                        math.isnan(v) and math.isnan(samples[t][0]) and samples[t][1] == q):
                    raise ValueError(f"rws:{code}: two values for {m['Tijdstip']}: {samples[t]} and {(v, q)}")
                samples[t] = (v, q)
    unknown = methods - set(METHOD_SAMPLING)
    if unknown or len(methods) > 1:
        raise ValueError(f"rws:{code}: WaardeBepalingsMethode {sorted(map(str, methods))} is not one "
                         f"known method ({sorted(METHOD_SAMPLING)}); the time base is not declared for it")
    if location is None or not samples:
        raise ValueError(f"rws:{code}: no WATHTE/NAP measurements in the member file")
    return location, methods.pop(), samples


def convert(code: str, member: bytes, *, source_version: str) -> GaugeRecord:
    """A GaugeRecord from a member file (raw response bodies, one per line)."""
    location, method, samples = _parse(code, member)
    sampling, time_base = METHOD_SAMPLING[method]
    times = np.array(sorted(samples), dtype=np.int64)
    values = np.array([samples[t][0] for t in times], dtype=np.float64)
    codes = [samples[t][1] for t in times]
    gap = ~np.isfinite(values) | (np.abs(values) >= GAP_VALUE)
    heights = np.where(gap, np.nan, values / 100.0)
    good = np.array([c == GOOD_CODE for c in codes], dtype=bool) & ~gap
    steps = np.diff(times)
    if len(steps) and int(np.median(steps)) != INTERVAL_S:
        raise ValueError(f"rws:{code}: median step {int(np.median(steps))} s is not {INTERVAL_S} s")
    return GaugeRecord(
        record_id=f"rws:{code}", source="rws", source_version=source_version,
        contributor="Rijkswaterstaat", originator=True, licence_class="CC0-1.0",
        lat=float(location["Lat"]), lon=float(location["Lon"]), gauge_type="Coastal",
        sampling=sampling, interval_min=INTERVAL_S // 60, declared_time_base=time_base,
        member_sha256=hashlib.sha256(member).hexdigest(),
        times_s=times, heights_m=heights.astype(np.float64), good=good)


def _last_full_year() -> tuple[date, date]:
    y = datetime.now(timezone.utc).year - 1
    return date(y, 1, 1), date(y + 1, 1, 1)


class RwsAdapter:
    source = "rws"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "rws-1"
    contract_sample = ("hoekvanholland", "vlissingen", "denhelder.marsdiep")

    def __init__(self, start: date | None = None, end: date | None = None,
                 fetcher_factory=PoliteFetcher) -> None:
        default = _last_full_year()
        self.start, self.end = start or default[0], end or default[1]
        self._fetcher_factory = fetcher_factory

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        fetcher = self._fetcher_factory(cache_dir)
        cat = fetcher.get(CATALOGUE_URL, data=json.dumps(CATALOGUE_REQUEST).encode(),
                          headers=JSON_HEADERS, cache_name="catalogue.json")
        stations = tidal_locations(json.loads(cat.bytes()))
        codes = sorted(stations) if only is None else list(only)
        missing = [c for c in codes if c not in stations]
        if missing:
            raise ValueError(f"rws: {missing} are not tidal WATHTE locations in the catalogue")
        for code in codes:
            lines = []
            for begin, end in month_periods(self.start, self.end):
                got = fetcher.get(OBSERVATIONS_URL, headers=JSON_HEADERS,
                                  data=json.dumps(observation_request(code, begin, end)).encode(),
                                  cache_name=f"obs/{code}/{begin[:7]}.json")
                body = got.bytes().strip()
                if not body:          # 204: no data in that month
                    continue
                if b"\n" in body:
                    body = json.dumps(json.loads(body), separators=(",", ":")).encode()
                lines.append(body + b"\n")
            member = cache_dir / "members" / f"{code}.jsonl"
            member.parent.mkdir(parents=True, exist_ok=True)
            tmp = member.with_name(member.name + ".tmp")
            tmp.write_bytes(b"".join(lines))
            tmp.replace(member)
        version = datetime.now(timezone.utc).strftime("%Y%m%d")   # the fetch datestamp
        return self.load(cache_dir, codes, source_version=version, extra=(cat.input_file(),),
                         source_url=OBSERVATIONS_URL)

    def load(self, cache_dir: Path, codes: list[str], *, source_version: str,
             extra: tuple[InputFile, ...] = (), source_url: str = OBSERVATIONS_URL) -> FetchResult:
        """A FetchResult over member files already in `cache_dir/members/`."""
        cache_dir = Path(cache_dir)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        files, items = list(extra), {}
        for code in codes:
            name = f"members/{code}.jsonl"
            p = cache_dir / name
            files.append(InputFile(path=name, sha256=sha256_file(p), size=p.stat().st_size,
                                   source_url=source_url, fetched_at=now))
            items[code] = {"member": name}
        return FetchResult(source=self.source, source_version=source_version, cache_dir=cache_dir,
                           files=tuple(files), items=items)

    def convention(self) -> dict:
        return {"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "tcd"}

    def licence(self) -> dict:
        return {"licence_id": "rws-cc0-1.0", "spdx": "CC0-1.0", "provider": "Rijkswaterstaat",
                "attribution": "Contains data from Rijkswaterstaat (waterinfo / Waterwebservices).",
                "url": "https://rijkswaterstaatdata.nl/waterdata/"}

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """Station code -> SHA-256 of its normalised series (stamp, value, quality code)."""
        keys = {}
        for code, item in fetched.items.items():
            _, method, samples = _parse(code, (Path(fetched.cache_dir) / item["member"]).read_bytes())
            norm = [method] + [[t, None if math.isnan(v) else v, q] for t, (v, q) in sorted(samples.items())]
            keys[code] = hashlib.sha256(json.dumps(norm, separators=(",", ":")).encode()).hexdigest()
        return keys

    def records(self, fetched: FetchResult) -> Iterable[GaugeRecord]:
        for code, item in sorted(fetched.items.items()):
            yield convert(code, (Path(fetched.cache_dir) / item["member"]).read_bytes(),
                          source_version=fetched.source_version)

    def sets(self, fetched: FetchResult):
        return ()
