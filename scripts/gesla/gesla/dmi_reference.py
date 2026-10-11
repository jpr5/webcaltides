"""DMI comparator (slot G04f): the Danish Meteorological Institute's official tidewater predictions
(high and low waters) as a (b) official reference for the time-base audit (timebase_audit.official_lag).

Source: the keyless OGC API `https://opendataapi.dmi.dk/v2/oceanObs`, collection `tidewater`,
`predictionType=minimum_maximum` (A-dmi-a, e09b2d2: "DMI tidewater predictions = official reference").
DMI serves predictions for the current and the next year only, so the comparison is the audit's (b)
comparison over the harness windows (timebase_audit.REF_WINDOWS, 2026-11..2027-08). Only stations
whose station list entry (A-dmi-a's cache/dmi/stations.json) offers the parameter `tw` have
predictions. `predictionTime` is UTC (an explicit Z): the events file keeps it unchanged.

A DMI reference CONFIRMS utc_instant only (timebase_audit.CONFIRM_ONLY_SOURCES): A-dmi-a measured the
M2 phase of DMI's predictions against DMI's own observations 16 min apart at Esbjerg (-16), Hirtshals
(+16) and Frederikshavn (+16) (A-dmi-a tidewater_check.txt, Mar-May 2026), more than the official
band, so a record off DMI's prediction is not, on that alone, a record with a wrong clock.

Cache (written by `python -m gesla.dmi_reference fetch`, never by the audit): $OTC_WORK/cache/dmi-tidewater/
  raw/<stationId>/<start>_<end>.json   the API responses (one per station and harness window, all pages)
  events/<stationId>.json              the harness events format (what timebase_audit.load_events reads)
  MANIFEST.json                        url, sha256, size and fetch time of every raw file
Fetching is polite: one request at a time, MIN_INTERVAL_S apart, and it stops on any HTTP error.
Licence: CC BY 4.0 (DMI terms of use for free data).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import urlencode

SRC = "dmi"
BASE = "https://opendataapi.dmi.dk/v2/oceanObs/collections/tidewater/items"
PARAMETER = "tw"
MIN_INTERVAL_S = 2.0
PAGE_LIMIT = 10_000
USER_AGENT = "OpenTideConstants-pipeline/0.1 (+https://opentideconstants.org)"
LICENCE = "CC-BY-4.0"


@dataclass(frozen=True)
class DmiStation:
    station_id: str
    name: str
    lat: float
    lon: float


def tide_stations(stations_json: str | Path) -> list[DmiStation]:
    """Stations with tidewater predictions (parameter `tw`), one per station id: the version whose
    validity ends last (open-ended first)."""
    d = json.loads(Path(stations_json).read_text())
    best: dict[str, tuple[str, DmiStation]] = {}
    for f in d.get("features", []):
        p = f.get("properties") or {}
        if PARAMETER not in (p.get("parameterId") or []) or not p.get("stationId"):
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        key = p.get("validTo") or "9999"
        s = DmiStation(str(p["stationId"]), str(p.get("name", "")), float(lat), float(lon))
        if s.station_id not in best or key > best[s.station_id][0]:
            best[s.station_id] = (key, s)
    return [v[1] for _, v in sorted(best.items())]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, a)))


def stations_near(stations: Sequence[DmiStation], points: Iterable[tuple[float, float]], km: float) -> list[DmiStation]:
    pts = list(points)
    return [s for s in stations if any(haversine_km(s.lat, s.lon, a, b) <= km for a, b in pts)]


# --- fetch -------------------------------------------------------------------------------------

def _iso_z(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_url(station_id: str, start: str, end: str) -> str:
    """[start, end) in UTC days; DMI's datetime interval is closed, so it ends 1 s before `end`."""
    a = dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc)
    b = dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc) - dt.timedelta(seconds=1)
    return BASE + "?" + urlencode({"stationId": station_id, "predictionType": "minimum_maximum",
                                   "datetime": f"{_iso_z(a)}/{_iso_z(b)}", "limit": PAGE_LIMIT})


class PoliteGet:
    """One request at a time, at least interval_s apart; any non-200 answer raises (no retry)."""

    def __init__(self, interval_s: float = MIN_INTERVAL_S, opener: Callable[[str], bytes] | None = None):
        self.interval_s = max(float(interval_s), 1.0)
        self.opener = opener or self._open
        self._last = 0.0

    @staticmethod
    def _open(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            if r.status != 200:
                raise RuntimeError(f"{url}: HTTP {r.status}")
            return r.read()

    def __call__(self, url: str) -> bytes:
        wait = self._last + self.interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return self.opener(url)
        finally:
            self._last = time.monotonic()


def fetch_window(station_id: str, start: str, end: str, get: PoliteGet) -> tuple[list[dict], list[tuple[str, bytes]]]:
    """All features of one window (following `next` links) and the raw pages (url, body)."""
    url, feats, pages = window_url(station_id, start, end), [], []
    while url:
        body = get(url)
        pages.append((url, body))
        d = json.loads(body)
        feats += d.get("features", [])
        nxt = [l["href"] for l in d.get("links", []) if l.get("rel") == "next" and l.get("href")]
        url = nxt[0] if nxt and d.get("features") else None
    return feats, pages


def events_from(features: Sequence[dict], station_id: str) -> list[tuple[str, str, float]]:
    """[(UTC ISO Z, "High"|"Low", cm)], sorted, duplicates removed. A stamp without a zone, another
    station or another prediction type is an error (never a silent choice)."""
    out = set()
    for f in features:
        p = f.get("properties") or {}
        if str(p.get("stationId")) != station_id:
            raise ValueError(f"DMI tidewater: a feature of station {p.get('stationId')} in {station_id}'s answer")
        kind = {"maximum": "High", "minimum": "Low"}.get(p.get("predictionType"))
        if kind is None:
            raise ValueError(f"DMI tidewater {station_id}: predictionType {p.get('predictionType')!r}")
        t = dt.datetime.fromisoformat(str(p["predictionTime"]).replace("Z", "+00:00"))
        if t.tzinfo is None:
            raise ValueError(f"DMI tidewater {station_id}: predictionTime without a zone: {p['predictionTime']}")
        out.add((_iso_z(t), kind, float(p["value"])))
    return sorted(out)


def events_doc(station: DmiStation, events: Sequence[tuple[str, str, float]], windows: Sequence[tuple[str, str]]) -> dict:
    return {"source": SRC, "ref": station.station_id, "name": station.name, "lat": station.lat, "lon": station.lon,
            "datum": "DVR90", "units": "cm", "tz": "UTC", "licence": LICENCE, "product": "tidewater minimum_maximum",
            "windows": [list(w) for w in windows], "events": [list(e) for e in events]}


def fetch(stations: Sequence[DmiStation], windows: Sequence[tuple[str, str]], out_dir: str | Path,
          get: PoliteGet | None = None, log=print) -> dict:
    """Fetch every station's windows (skipping stations already done), write raw pages, events and MANIFEST."""
    out = Path(out_dir)
    get = get or PoliteGet()
    mpath = out / "MANIFEST.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {
        "archive": "cache/dmi-tidewater/", "licence": LICENCE,
        "citation": "Danish Meteorological Institute (DMI), oceanObs API tidewater, https://opendataapi.dmi.dk/v2/oceanObs",
        "files": []}
    for s in stations:
        ep = out / "events" / f"{s.station_id}.json"
        if ep.exists():
            continue
        evs: list = []
        for a, b in windows:
            feats, pages = fetch_window(s.station_id, a, b, get)
            for k, (url, body) in enumerate(pages):
                rel = f"raw/{s.station_id}/{a}_{b}" + (f"_p{k}" if k else "") + ".json"
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                (out / rel).write_bytes(body)
                manifest["files"].append({"path": rel, "source_url": url, "size": len(body),
                                          "sha256": hashlib.sha256(body).hexdigest(),
                                          "fetched_at": _iso_z(dt.datetime.now(dt.timezone.utc))})
            evs += events_from(feats, s.station_id)
        ep.parent.mkdir(parents=True, exist_ok=True)
        ep.write_text(json.dumps(events_doc(s, sorted(set(map(tuple, evs))), windows), indent=1) + "\n")
        mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        log(f"{s.station_id} {s.name}: {len(evs)} events")
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


# --- the audit's view ----------------------------------------------------------------------------

def load_points(events_dir: str | Path) -> list[tuple[float, float, str, str]]:
    """(lat, lon, station id, events path) of every events file with events (the station's position is in
    the file). DMI lists `tw` for many secondary gauges ("... Havn II") but predicts nothing for them:
    such a file is skipped, so it never shadows the station of the same harbour that has predictions."""
    d = Path(events_dir)
    out = []
    if not d.is_dir():
        # a missing dir would silently drop every DMI comparator: refuse instead
        raise FileNotFoundError(f"DMI tidewater events dir {d} not found (dmi_reference fetch): the DMI "
                                f"comparators need it; point OTC_WORK at the cache, or give an empty dir")
    for p in sorted(d.glob("*.json")):
        doc = json.loads(p.read_text())
        if not doc.get("events"):
            continue
        try:
            out.append((float(doc["lat"]), float(doc["lon"]), str(doc["ref"]), str(p)))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"{p}: DMI events file without a position or ref ({type(e).__name__}: {e})") from e
    return out


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import csv

    from .read_gesla import otc_work
    from .timebase_audit import PORT_KM, REF_WINDOWS
    repo = Path(__file__).resolve().parents[3]
    ap = argparse.ArgumentParser(prog="python -m gesla.dmi_reference")
    ap.add_argument("command", choices=["fetch"])
    ap.add_argument("--stations", default=str(otc_work() / "cache/dmi/stations.json"), help="A-dmi-a's station list")
    ap.add_argument("--inventory", default=str(repo / "data/gesla/4.1/inventory.csv"))
    ap.add_argument("--out", default=str(otc_work() / "cache/dmi-tidewater"))
    ap.add_argument("--interval", type=float, default=MIN_INTERVAL_S)
    a = ap.parse_args(argv)
    with open(a.inventory, newline="") as fh:
        pts = [(float(r["lat"]), float(r["lon"])) for r in csv.DictReader(fh) if r["file_id"].split("-")[-2] == "dnk"]
    near = stations_near(tide_stations(a.stations), pts, PORT_KM)
    print(f"{len(near)} DMI tidewater stations within {PORT_KM} km of a Danish GESLA record")
    fetch(near, REF_WINDOWS, a.out, PoliteGet(a.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
