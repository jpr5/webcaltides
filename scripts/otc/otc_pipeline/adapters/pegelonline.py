"""PEGELONLINE (WSV, Germany) gauge adapter (manifest A-PEG-a): harvested archive -> GaugeRecord.

The PEGELONLINE REST API (v2, `/stations/{uuid}/W/measurements.json`) serves only the last 31
days, so this adapter never reads it. It reads the daily harvest (S0a) in the private work bucket:

    archive/pegelonline/stations/YYYY-MM-DD.json   station snapshot (stations.json?includeTimeseries=true)
    archive/pegelonline/stations/MANIFEST.json     SHA-256 of each snapshot
    archive/pegelonline/YYYY/MM/DD/MANIFEST.json   SHA-256 of each station's file for that UTC day
    archive/pegelonline/YYYY/MM/DD/<uuid>.csv.gz   `timestamp_utc,value_cm`, one row per measurement

Every object is checked against its MANIFEST before it is read; a mismatch stops the fetch.

What the API states, and what this adapter declares (the G04 time-base audit verifies it):

- Time: the API gives every timestamp "in lokaler gesetzlicher Zeit (MEZ/MESZ mit
  Sommerzeitumstellung)" with an explicit UTC offset (ISO 8601, e.g. 2025-09-25T10:00:00+02:00).
  The offset makes the instant unambiguous; the harvest stores it converted to UTC. So the
  stamps are UTC.
- Sampling: the API calls the series "gemessene Rohdaten" (measured raw data, not checked) with an
  `equidistance` of 1 min at these gauges. It does not say whether a value is an instantaneous
  reading or a mean over an interval. This adapter declares `instantaneous` and the time base
  `utc_instant` as a hypothesis, not a fact. Note: the WSV records in GESLA-4.1 (hourly) are
  60-min means stamped at the start (BP §2, `utc_mean_start60`); those are a different product,
  but the audit must test the same possibility here.
- QC: the raw series carries no quality flag. `good` is every finite value.
- Heights: centimetres above the gauge zero (PNP); converted to metres, datum unchanged (the fit
  absorbs the constant offset; PNP is `W.gaugeZero` in the snapshot, metres above NHN).

Published datums (datums spec §4.1 item 6, slot DT5e). The harvest snapshot has no characteristic
values, so `fetch` also reads `stations.json?includeTimeseries=true&includeCharacteristicValues=true`
from the live API (one request). `published_datums` gives, per record, the levels PEGELONLINE
publishes above the gauge zero, for the fit (A-PEG-b) to put on the record's set beside its own
`msl_offset_m`: `zero` `gauge_zero`, `zero_name` PNP; MThw -> `mhw` and MTnw -> `mlw` (cm -> m,
`source_name` the German names, `epoch` the published timespan); `nhn` = -(gaugeZero in m NHN),
the height of the NHN zero above PNP, only where the unit is "m. ü. NHN" (not NN, DHHN12). That
gaugeZero is the current one, valid from its `validFrom` date, which the notes carry:
PEGELONLINE does not say whether MThw/MTnw over an older timespan refer to the same PNP. HThw and
NTnw are observed extremes, not taken. PEGELONLINE states no chart datum: no `chart_datum`.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from gesla.record import GaugeRecord
from otc_pipeline.adapter import FetchResult, InputFile, sha256_file
from otc_pipeline.datums import period, published
from otc_pipeline.http import PoliteFetcher

ARCHIVE = "archive/pegelonline"
BUCKET = "opentideconstants-work"
DECLARED_TIME_BASE = "utc_instant"
SAMPLING = "instantaneous"
CSV_HEADER = ["timestamp_utc", "value_cm"]
_DAY_KEY = re.compile(rf"\A{ARCHIVE}/(\d{{4}})/(\d{{2}})/(\d{{2}})/MANIFEST\.json\Z")
CHARACTERISTIC_URL = "https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations.json"
CHARACTERISTIC_PARAMS = {"includeTimeseries": "true", "includeCharacteristicValues": "true"}
CHARACTERISTIC_NAME = "characteristic/stations.json"
# characteristic value shortname -> (OTC key); the observed extremes HThw/NTnw are not taken
CHARACTERISTIC_KEYS = {"MThw": "mhw", "MTnw": "mlw"}
NHN_UNIT = "m. ü. NHN"
_SNAPSHOT_KEY = re.compile(rf"\A{ARCHIVE}/stations/(\d{{4}}-\d{{2}}-\d{{2}})\.json\Z")

# GESLA-4.1 "GAUGE TYPE" of every WSV record, by PEGELONLINE station number (the number is in the
# GESLA file name, e.g. cuxhavensteubenhft-5990020-deu-wsv). From GESLA4-1_ALL.csv, SHA-256
# 92fd88dcdf8a57d7551870aabdd90d13a1751311e2895837dc271a159b42ab48. Using GESLA's own type keeps
# the River/Lake rules (BP §5) the same for a gauge whichever source it comes from.
_GESLA_COASTAL = frozenset("""
3950020 3970010 3990010 3990020 4970030 4970040 4990010 5650068 5970096 5979020 5980060 5990011
5990020 9340010 9340020 9340030 9360010 9390010 9410010 9420010 9420030 9420040 9430020 9430030
9430050 9440020 9440030 9460010 9460020 9460040 9510010 9510060 9510063 9510066 9510070 9510075
9510095 9510132 9520081 9530010 9530020 9550021 9570010 9570040 9570050 9570070 9610010 9610015
9610025 9610035 9610045 9610050 9610066 9610070 9610075 9610080 9620085 9620090 9630007 9640015
9650024 9650030 9650040 9650043 9650070 9650073 9650080 9660001 9670046 9670050 9670055 9670063
9670065 9670067 9690077 9690078 9690084 9690085 9690088 9690093
""".split())
_GESLA_RIVER = frozenset("""
3770030 3790010 3790020 3880010 3880050 3910010 3910020 4910040 4910050 4910060 4930010 4940010
4940020 4940030 4950010 4950020 4960020 4960030 4960040 4960050 4960060 4960070 4960080 4970010
4970020 5930062 5930070 5930090 5940060 5940080 5950010 5950070 5950080 5950081 5950090 5960010
5960020 5970010 5970013 5970016 5970018 5970019 5970022 5970023 5970024 5970025 5970026 5970031
5970035 5970037 5970038 5970042 5970050 5970091 5970094 5980010 5980030 9520040 9520050 9520060
9520061 9520070
""".split())
# For a station GESLA does not have: open-sea and bay waters are Coastal, every other water River.
_COASTAL_WATERS = frozenset({"NORDSEE", "OSTSEE", "JADE", "KLEINES HAFF"})


def gauge_type(station: dict) -> str:
    number = str(station.get("number", ""))
    if number in _GESLA_COASTAL:
        return "Coastal"
    if number in _GESLA_RIVER:
        return "River"
    return "Coastal" if station.get("water") in _COASTAL_WATERS else "River"


# -- the archive store ------------------------------------------------------------------------

class LocalStore:
    """A directory with the bucket's key layout (the `$OTC_WORK` cache, or test fixtures)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def url(self, key: str) -> str:
        return (self.root / key).resolve().as_uri()

    def ls(self, prefix: str) -> dict[str, int]:
        base = self.root / prefix
        return {p.relative_to(self.root).as_posix(): p.stat().st_size
                for p in sorted(base.rglob("*")) if p.is_file()} if base.is_dir() else {}

    def get(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((self.root / key).read_bytes())


class WorkBucketStore:
    """The private work bucket, through the repo helper `bin/otc-work` (credentials: §0.3)."""

    def __init__(self, helper: Path | None = None, bucket: str | None = None):
        self.helper = Path(helper or os.environ.get("OTC_WORK_HELPER")
                           or Path(__file__).resolve().parents[4] / "bin" / "otc-work")
        self.bucket = bucket or os.environ.get("OTC_WORK_BUCKET") or BUCKET

    def url(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def _run(self, *args: str) -> str:
        p = subprocess.run([str(self.helper), *args], capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            raise RuntimeError(f"otc-work {args[0]} {args[1]}: exit {p.returncode}: {p.stderr.strip()[-500:]}")
        return p.stdout

    def ls(self, prefix: str) -> dict[str, int]:
        out = {}
        for line in self._run("ls", prefix).splitlines():
            parts = line.split(None, 3)       # date time size key
            if len(parts) == 4 and parts[2].isdigit():
                out[parts[3]] = int(parts[2])
        return out

    def get(self, key: str, dest: Path) -> None:
        self._run("get", key, str(dest))


# -- the adapter ------------------------------------------------------------------------------

@dataclass(frozen=True)
class _DayFile:
    day: str          # YYYY-MM-DD (the UTC day of the directory)
    path: str         # relative to the cache dir (= the bucket key)
    sha256: str


def _utc_seconds(stamp: str) -> int:
    return int(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _canonical_sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PegelonlineAdapter:
    source = "pegelonline"
    source_type = "gauge"
    quantity = "water_level"
    adapter_version = "pegelonline-a-2"   # a-2: published datums (DT5e)
    # Cuxhaven Steubenhöft, Helgoland Binnenhafen, Bremerhaven Alter Leuchtturm (manifest A-PEG-a)
    contract_sample = ("aad49293-242a-43ad-a8b1-e91d7792c4b2", "c0ec139b-13b4-4f86-bee3-06665ad81a40",
                       "d3f822a0-e201-4a61-8913-589c74818ae0")

    def __init__(self, store=None, workers: int = 8,
                 characteristic_fetcher: Callable[[Path], PoliteFetcher] | None = None):
        """`characteristic_fetcher`: cache dir -> fetcher for the live characteristic-values request
        (default PoliteFetcher, one request a second)."""
        self.store = store if store is not None else WorkBucketStore()
        self.workers = workers
        self._characteristic_fetcher = characteristic_fetcher or (lambda d: PoliteFetcher(d, min_interval_s=1.0))

    # fetch ---------------------------------------------------------------------------------

    def _download(self, cache_dir: Path, keys: list[str]) -> None:
        def one(key: str) -> None:
            self.store.get(key, cache_dir / key)
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            list(pool.map(one, keys))

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        listing = self.store.ls(f"{ARCHIVE}/")
        snapshots = sorted(k for k in listing if _SNAPSHOT_KEY.match(k))
        day_manifests = sorted(k for k in listing if _DAY_KEY.match(k))
        if not snapshots or not day_manifests:
            raise ValueError(f"{ARCHIVE}: no station snapshot or no harvested day in the archive")
        snap_key, snap_manifest_key = snapshots[-1], f"{ARCHIVE}/stations/MANIFEST.json"
        # Manifests and the snapshot are small and can change (the newest day is rewritten by
        # each harvest run), so they are always re-read; day files are reused when their SHA matches.
        self._download(cache_dir, [snap_manifest_key, snap_key, *day_manifests])

        files: list[InputFile] = []
        snap_entry = self._manifest_entry(cache_dir, snap_manifest_key, snap_key)
        files += [self._input(cache_dir, snap_manifest_key, None), self._input(cache_dir, snap_key, snap_entry)]
        snapshot = json.loads((cache_dir / snap_key).read_text(encoding="utf-8"))
        stations = {s["uuid"]: s for s in snapshot["stations"]}
        wanted = sorted(stations) if only is None else list(dict.fromkeys(only))
        unknown = [u for u in wanted if u not in stations]
        if unknown:
            raise ValueError(f"{unknown} not in the station snapshot {snap_key}")

        todo: list[tuple[str, dict]] = []   # (key, manifest entry) of day files to check
        days: dict[str, list[_DayFile]] = {u: [] for u in wanted}
        for mkey in day_manifests:
            y, m, d = _DAY_KEY.match(mkey).groups()
            manifest = json.loads((cache_dir / mkey).read_text(encoding="utf-8"))
            files.append(self._input(cache_dir, mkey, None))
            entries = {e["path"]: e for e in manifest["files"]}
            for uuid in wanted:
                e = entries.get(f"{uuid}.csv.gz")
                if e is None:
                    continue              # no harvest of this station on this day
                key = f"{ARCHIVE}/{y}/{m}/{d}/{e['path']}"
                todo.append((key, e))
                days[uuid].append(_DayFile(f"{y}-{m}-{d}", key, e["sha256"]))
        stale = [k for k, e in todo
                 if not (cache_dir / k).is_file() or sha256_file(cache_dir / k) != e["sha256"]]
        self._download(cache_dir, stale)
        for key, e in todo:
            files.append(self._input(cache_dir, key, e))

        cv = self._characteristic_fetcher(cache_dir).get(CHARACTERISTIC_URL, params=CHARACTERISTIC_PARAMS,
                                                         cache_name=CHARACTERISTIC_NAME, refresh=True)
        files.append(cv.input_file())
        w_series = {}
        for st in json.loads(cv.bytes()):
            w = [t for t in st.get("timeseries") or [] if t.get("shortname") == "W"]
            if len(w) > 1:
                raise ValueError(f"{CHARACTERISTIC_NAME}: station {st.get('uuid')} has {len(w)} W series")
            if w:
                w_series[st["uuid"]] = {"gaugeZero": w[0].get("gaugeZero"),
                                        "characteristicValues": w[0].get("characteristicValues") or []}

        last = _DAY_KEY.match(day_manifests[-1]).groups()
        items = {u: {"station": stations[u], "days": tuple(days[u]), "characteristic": w_series.get(u),
                     "characteristic_sha256": cv.sha256} for u in wanted}
        return FetchResult(source=self.source, source_version="".join(last), cache_dir=cache_dir,
                           files=tuple(files), items=items)

    def _manifest_entry(self, cache_dir: Path, manifest_key: str, key: str) -> dict:
        doc = json.loads((cache_dir / manifest_key).read_text(encoding="utf-8"))
        name = key.rsplit("/", 1)[1]
        for e in doc["files"]:
            if e["path"] == name:
                return e
        raise ValueError(f"{key} is not listed in {manifest_key}")

    def _input(self, cache_dir: Path, key: str, entry: dict | None) -> InputFile:
        """An InputFile for a cached object; checked against its MANIFEST entry when it has one."""
        p = cache_dir / key
        sha, size = sha256_file(p), p.stat().st_size
        if entry is not None and (sha != entry["sha256"] or size != entry["size"]):
            raise ValueError(f"{key}: sha256/size {sha}/{size} differ from its MANIFEST "
                             f"({entry['sha256']}/{entry['size']})")
        fetched_at = (entry or {}).get("fetched_at") or datetime.fromtimestamp(
            p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return InputFile(path=key, sha256=sha, size=size, source_url=self.store.url(key), fetched_at=fetched_at)

    # convert -------------------------------------------------------------------------------

    @staticmethod
    def _series(fetched: FetchResult, uuid: str) -> tuple[np.ndarray, np.ndarray]:
        times: list[int] = []
        values: list[float] = []
        for df in fetched.items[uuid]["days"]:
            raw = (Path(fetched.cache_dir) / df.path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != df.sha256:
                raise ValueError(f"{df.path} changed since fetch (sha256 differs from its MANIFEST)")
            rows = csv.reader(io.StringIO(gzip.decompress(raw).decode("utf-8")))
            header = next(rows, None)
            if header != CSV_HEADER:
                raise ValueError(f"{df.path}: header {header!r}, expected {CSV_HEADER!r}")
            lo = _utc_seconds(f"{df.day}T00:00:00Z")
            for row in rows:
                if not row:
                    continue
                t = _utc_seconds(row[0])
                if not lo <= t < lo + 86400:
                    raise ValueError(f"{df.path}: stamp {row[0]} is outside its day directory {df.day}")
                times.append(t)
                values.append(float(row[1]) if row[1].strip() else float("nan"))
        t = np.asarray(times, dtype=np.int64)
        if t.size and np.any(np.diff(t) <= 0):
            i = int(np.argmax(np.diff(t) <= 0))
            raise ValueError(f"pegelonline:{uuid}: stamps not strictly increasing at "
                             f"{datetime.fromtimestamp(int(t[i + 1]), timezone.utc).isoformat()}")
        return t, np.asarray(values, dtype=np.float64)

    def records(self, fetched: FetchResult) -> Iterable[GaugeRecord]:
        for uuid in sorted(fetched.items):
            st = fetched.items[uuid]["station"]
            t, cm = self._series(fetched, uuid)
            heights = cm / 100.0
            yield GaugeRecord(
                record_id=f"{self.source}:{uuid}", source=self.source,
                source_version=fetched.source_version, contributor="WSV", originator=True,
                licence_class="DL-DE-ZERO-2.0", lat=float(st["latitude"]), lon=float(st["longitude"]),
                gauge_type=gauge_type(st), sampling=SAMPLING,
                interval_min=int(st["W"]["equidistance_min"]), declared_time_base=DECLARED_TIME_BASE,
                # one series spans many day files: hash the ordered (path, sha256) list
                member_sha256=_canonical_sha([[d.path, d.sha256] for d in fetched.items[uuid]["days"]]),
                times_s=t, heights_m=heights, good=np.isfinite(heights))

    def sets(self, fetched: FetchResult):
        return ()

    def published_datums(self, fetched: FetchResult) -> dict[str, dict]:
        """record_id -> {"datum": the published levels above PNP (no msl_offset_m: the fit's Z0 goes
        with it), "notes": the gauge zero's value and validFrom, for the slot report (not release
        provenance, which is a closed list)}. Records whose station
        publishes neither MThw/MTnw nor an NHN gauge zero are left out."""
        out = {}
        for uuid in sorted(fetched.items):
            w = fetched.items[uuid].get("characteristic")
            if not w:
                continue
            named, basis, prov = {}, {}, {"characteristic_sha256": fetched.items[uuid]["characteristic_sha256"]}
            for cv in w["characteristicValues"]:
                key = CHARACTERISTIC_KEYS.get(cv.get("shortname"))
                if key is None:
                    continue
                if cv.get("unit") != "cm" or key in named:
                    raise ValueError(f"pegelonline:{uuid}: characteristic value {cv.get('shortname')} "
                                     f"unit {cv.get('unit')!r} or listed twice")
                named[key] = round(float(cv["value"]) / 100.0, 6)
                basis[key] = published(cv["shortname"], epoch=period(
                    cv["timespanStart"], cv["timespanEnd"], f"PEGELONLINE {cv['shortname']} timespan"))
            gz = w.get("gaugeZero") or {}
            if gz.get("unit") == NHN_UNIT and isinstance(gz.get("value"), (int, float)):
                named["nhn"], basis["nhn"] = round(-float(gz["value"]), 6), published("NHN")
                prov["gauge_zero_m_nhn"] = gz["value"]
                prov["gauge_zero_valid_from"] = gz.get("validFrom")
            if not named:
                continue
            out[f"{self.source}:{uuid}"] = {
                "datum": {"zero": "gauge_zero", "zero_name": "PNP", "named": named, "basis": basis},
                "notes": prov}
        return out

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """uuid -> SHA-256 of the station's normalised series (UTC stamps, cm) and metadata."""
        keys = {}
        for uuid in sorted(fetched.items):
            st = fetched.items[uuid]["station"]
            t, cm = self._series(fetched, uuid)
            h = hashlib.sha256()
            h.update(json.dumps({**{k: st.get(k) for k in ("latitude", "longitude", "number", "W")},
                                 "characteristic": fetched.items[uuid].get("characteristic")},
                                sort_keys=True, separators=(",", ":")).encode())
            h.update(t.astype("<i8").tobytes())
            h.update(cm.astype("<f8").tobytes())
            keys[uuid] = h.hexdigest()
        return keys

    # convention and licence ----------------------------------------------------------------

    def convention(self) -> dict:
        # The constants for this source come from our fit (A-PEG-b): Greenwich (UTC) phases.
        return {"convention_id": "otc-greenwich-schureman-v1", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "tcd"}

    def licence(self) -> dict:
        return {"licence_id": "pegelonline-dl-de-zero-2.0", "spdx": "DL-DE-ZERO-2.0",
                "provider": "Wasserstraßen- und Schifffahrtsverwaltung des Bundes (WSV), PEGELONLINE",
                "attribution": "Water levels: WSV, PEGELONLINE (pegelonline.wsv.de), "
                               "Datenlizenz Deutschland – Zero – Version 2.0.",
                # PEGELONLINE's terms of use, which apply DL-DE Zero 2.0 to its data
                "url": "https://pegelonline.wsv.de/gast/nutzungsbedingungen",
                # DL-DE Zero 2.0: any use, commercial included, with no conditions
                "commercial_use": True}
