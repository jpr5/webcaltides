"""Build Tier B calls from pinned, unchanged agency caches; never fetch constants."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from otc_pipeline.adapters import jma, kartverket
from otc_pipeline.adapters.noaa import NoaaAdapter
from otc_pipeline.adapters.noaa_currents import NoaaCurrentsAdapter
from otc_pipeline.constituent_speeds import TableSpeeds
from otc_pipeline.http import PoliteFetcher

from astro_entry import build_entry

START = "2026-10-01T00:00:00Z"
STOP = "2026-11-01T00:00:00Z"
TIDES = ("8443970", "9447130", "9414290")
SUBORDINATES = ("8444351", "8415809", "TPT2705")
FOLDED = ("8415809", "TPT2705")
CURRENTS = (("ACT1616", 1), ("BOS1101", 1), ("BOS1101", 15),
            ("PUG1501", 1), ("PUG1501", 6))
JMA = ("TK", "OS", "KR", "NG")
KARTVERKET = ("BGO", "TRD", "SVG")


class CacheOnlyFetcher(PoliteFetcher):
    """Return verified original response bytes or fail; a cache miss never uses HTTP."""

    def get(self, url, *, params=None, cache_name=None, **kwargs):
        if not cache_name or kwargs:
            raise ValueError("Tier B constants require a named, unchanged GET cache entry")
        full = self.build_url(url, params)
        hit = self._cached(self._fingerprint(full, "GET", None, None), cache_name,
                           self._cache_path(cache_name))
        if hit is None:
            raise ValueError(f"missing or invalid pinned constants response: {full}")
        return hit


@contextmanager
def cached_module(module, cache):
    original = module.PoliteFetcher
    module.PoliteFetcher = lambda *_args, **_kwargs: CacheOnlyFetcher(cache)
    try:
        yield
    finally:
        module.PoliteFetcher = original


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def required_dict(value: dict | None, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"missing required {label}")
    return value


def iso(stamp):
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # NOAA explicitly requested GMT.
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_window(point):
    return START <= point["time"] < STOP


def verified(path, pins):
    meta_path = path.with_name(path.name + ".meta.json")
    meta = json.loads(meta_path.read_text())
    expected = meta.get("response_sha256", meta.get("sha256"))
    if expected != digest(path):
        raise ValueError(f"official response hash mismatch: {path}")
    request = meta.get("request")
    if "request_sha256" in meta:
        actual = hashlib.sha256(json.dumps(request, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
        if actual != meta["request_sha256"]:
            raise ValueError(f"official request hash mismatch: {meta_path}")
    pins.append({"path": str(path), "sha256": expected,
                 "meta_sha256": digest(meta_path), "request": request})
    return path.read_bytes()


def noaa_points(path, pins, kind):
    raw = json.loads(verified(path, pins))
    if "error" in raw:
        raise ValueError(f"NOAA refused {path}: {raw['error']}")
    if kind in ("heights", "extremes"):
        result = [{"time": iso(p["t"]), "height_m": float(p["v"]),
                   **({"type": {"H": "high", "L": "low"}[p["type"]]}
                      if kind == "extremes" else {})} for p in raw["predictions"]]
    else:
        payload = raw["current_predictions"]
        if payload["units"] != "meters, cm/s":
            raise ValueError(f"unexpected NOAA current units: {payload['units']}")
        if kind == "direction":
            result = [{"time": iso(p["Time"]), "speed_ms": float(p["Speed"]) / 100,
                       "direction_deg": float(p["Direction"])} for p in payload["cp"]]
        else:
            result = [{"time": iso(p["Time"]), "type": p["Type"],
                       "velocity_ms": float(p["Velocity_Major"]) / 100}
                      for p in payload["cp"]]
    return [p for p in result if in_window(p)]


def kartverket_points(path, pins, kind, code):
    doc = ET.fromstring(verified(path, pins))
    loc = doc.find("./stationdata/location")
    if loc is None or loc.get("code") != code:
        raise ValueError(f"Kartverket response is not station {code}: {path}")
    data = loc.find("data")
    if data is None or any(data.get(k) != v for k, v in
                           {"type": "prediction", "unit": "cm", "reflevelcode": "MSL"}.items()):
        raise ValueError(f"Kartverket response is not official predictions above MSL: {path}")
    result = [{"time": iso(p.attrib["time"]), "height_m": float(p.attrib["value"]) / 100,
               **({"type": p.get("flag")} if kind == "extremes" else {})}
              for p in data.findall("waterlevel")]
    return [p for p in result if in_window(p)]


def build(args):
    pins, inputs, rows = [], [], []
    speeds = TableSpeeds.from_tcd_dump(args.run_dir / "tcd-1700-2100.json")
    if speeds.tcd_sha256 != args.tcd_sha256:
        raise ValueError("adapter speed dump does not belong to the selected own TCD")
    tide_adapter = NoaaAdapter(speeds=speeds, fetcher_factory=CacheOnlyFetcher)
    tides_fetch = tide_adapter.fetch(args.cache / "noaa", only=list(TIDES + SUBORDINATES))
    tide_sets = {s.station_hint["aliases"]["noaa"]: s for s in tide_adapter.sets(tides_fetch)}
    current_adapter = NoaaCurrentsAdapter(speeds=speeds, fetcher_factory=CacheOnlyFetcher)
    currents_fetch = current_adapter.fetch(args.cache / "noaa-currents",
                                          only=sorted({s for s, _ in CURRENTS} | {"ACT0091"}))
    current_sets = {s.station_hint["aliases"]["noaa"]: s for s in current_adapter.sets(currents_fetch)}
    with tempfile.TemporaryDirectory(prefix="otc-tier-b-manifests-") as scratch:
        kv_adapter = kartverket.KartverketAdapter(speeds=speeds)
        with cached_module(kartverket, args.cache / "kartverket"):
            kv_fetch = kv_adapter.fetch(Path(scratch), only=list(KARTVERKET))
        jma_adapter = jma.JmaAdapter(year=2026, speeds=jma.Speeds.from_tcd_dump(
            args.run_dir / "tcd-1700-2100.json"))
        with cached_module(jma, args.cache / "jma"):
            jma_fetch = jma_adapter.fetch(Path(scratch), only=list(JMA))
    kv_fetch = replace(kv_fetch, cache_dir=args.cache / "kartverket")
    jma_fetch = replace(jma_fetch, cache_dir=args.cache / "jma")
    kv_sets = {required_dict(s.constant_set, "Kartverket set")["source_record_id"]: s
               for s in kv_adapter.sets(kv_fetch)}
    jma_sets = {required_dict(s.constant_set, "JMA set")["source_record_id"]: s
                for s in jma_adapter.sets(jma_fetch)}
    names = set()
    all_sets = [*tide_sets.values(), *current_sets.values(), *kv_sets.values(), *jma_sets.values()]
    for ss in all_sets:
        cs = ss.constant_set or {}
        names.update(c["name"] for c in cs.get("constituents", []))
        for b in cs.get("current_bins", []):
            names.update(c["name"] for c in b["constituents"])
    table = build_entry(args.run_dir, sorted(names), args.table_id, args.tcd_sha256)
    for fetched in (tides_fetch, currents_fetch, kv_fetch, jma_fetch):
        for file in fetched.files:
            path = fetched.cache_dir / file.path
            if digest(path) != file.sha256:
                raise ValueError(f"original agency input changed: {path}")
            inputs.append({**file.as_dict(), "path": str(path)})

    def row(source, sid, kind, entry, call, reference, limits, **extra):
        if not reference:
            raise ValueError(f"empty official comparison: {source}/{sid}/{kind}")
        rows.append({"id": f"{source}/{sid}/{kind}", "source": source, "station": sid,
                     "kind": kind, "call": {"entry": entry, "args": call},
                     "reference": reference, "limits": limits,
                     "exact_events": source in ("noaa", "noaa-currents"), **extra})

    for sid in TIDES:
        cons = required_dict(tide_sets[sid].constant_set, f"NOAA {sid} set")["constituents"]
        ref = noaa_points(args.official / f"noaa/{sid}-6.json", pins, "heights")
        row("noaa", sid, "heights", "predict_strict",
            {"constituents": cons, "times": [p["time"] for p in ref], "datum_term": 0}, ref,
            {"height_max_m": 0.02, "height_mae_m": 0.005})
        ref = noaa_points(args.official / f"noaa/{sid}-hilo.json", pins, "extremes")
        row("noaa", sid, "extremes", "extremes_strict",
            {"constituents": cons, "start": START, "stop": STOP, "datum_term": 0}, ref,
            {"time_max_s": 120, "time_mae_s": 30, "height_max_m": 0.02})
    folded_calls = []
    for sid in SUBORDINATES:
        offsets = dict(required_dict(tide_sets[sid].subordinate_offsets, f"NOAA {sid} offsets"))
        ref_sid = offsets.pop("reference_source_record_id")
        offsets["reference_station_id"] = ref_sid
        cs = required_dict(tide_sets[ref_sid].constant_set, f"NOAA {ref_sid} reference set")
        datum = cs["datum"]
        chart_term = datum["msl_offset_m"] - datum["named"][datum["chart_datum"]]
        call = {"ref_constituents": cs["constituents"], "chart_datum_term": chart_term,
                "offsets": offsets, "start": START, "stop": STOP, "datum_shift": 0}
        ref = noaa_points(args.official / f"noaa/{sid}-hilo.json", pins, "extremes")
        row("noaa", sid, "subordinate-extremes", "subordinate_extremes_strict", call, ref,
            {"time_max_s": 120, "height_max_m": 0.03}, reference_station=ref_sid)
        if sid in FOLDED:
            folded_calls.append({"station": sid, "reference_station": ref_sid, "call": call,
                                 "constant_set": cs, "station_hint": tide_sets[sid].station_hint})
    for sid, bin_number in (*CURRENTS, ("ACT0091", 1)):
        cs = current_sets[sid].constant_set
        current_bin = None
        ref = noaa_points(args.official / f"noaa-currents/{sid}-{bin_number}-events.json", pins, "events")
        if cs:
            current_bin = next(b for b in cs["current_bins"] if b["bin"] == bin_number)
            call = {"bin": current_bin, "start": START, "stop": STOP}
            entry = "current_events_strict"
            limits = {"velocity_max_ms": 0.01, "slack_time_max_s": 120, "peak_time_max_s": 180}
        else:
            offs = required_dict(current_sets[sid].subordinate_offsets, f"NOAA {sid} current offsets")
            offset = dict(next(o for o in offs["current_offsets"]
                               if o["bin"] == bin_number))
            ref_sid = offset.pop("reference_source_record_id")
            offset["reference_station_id"] = ref_sid
            ref_cs = required_dict(current_sets[ref_sid].constant_set, f"NOAA {ref_sid} current reference set")
            ref_bin = next(b for b in ref_cs["current_bins"]
                           if b["bin"] == offset["reference_bin"])
            call = {"ref_bin": ref_bin, "offset": offset, "start": START, "stop": STOP}
            entry = "subordinate_current_events_strict"
            limits = {"velocity_max_ms": 0.01, "slack_time_max_s": 180, "peak_time_max_s": 240}
        row("noaa-currents", f"{sid}:{bin_number}", "events", entry, call, ref, limits)
        if cs:
            if current_bin is None:
                raise ValueError(f"no current bin {sid}:{bin_number}")
            ref = noaa_points(args.official / f"noaa-currents/{sid}-{bin_number}-direction.json", pins, "direction")
            row("noaa-currents", f"{sid}:{bin_number}", "direction", "currents_strict",
                {"bin": current_bin, "times": [p["time"] for p in ref]}, ref,
                {"direction_max_deg": 5, "minimum_speed_ms": 0.1})
    for code in JMA:
        cs = required_dict(jma_sets[code].constant_set, f"JMA {code} set")
        if cs["provenance"]["owner"] != "jma":
            raise ValueError(f"{code} is not a JMA-owned station")
        path = args.cache / f"jma/predictions/2026/{code}.txt"
        ref = [{"time": iso(t.isoformat()), "height_m": h}
               for t, h in jma.parse_predictions(verified(path, pins), code)]
        ref = [p for p in ref if in_window(p)]
        row("jma", code, "heights", "predict_strict",
            {"constituents": cs["constituents"], "times": [p["time"] for p in ref],
             "datum_term": cs["datum"]["msl_offset_m"]}, ref, {"height_max_m": 0.05})
    for code in KARTVERKET:
        cons = required_dict(kv_sets[code].constant_set, f"Kartverket {code} set")["constituents"]
        for kind, suffix in (("heights", "pre"), ("extremes", "tab")):
            ref = kartverket_points(args.official / f"kartverket/{code}-{suffix}.xml", pins, kind, code)
            call = {"constituents": cons, "datum_term": 0}
            call.update({"times": [p["time"] for p in ref]} if kind == "heights"
                        else {"start": START, "stop": STOP})
            row("kartverket", code, kind, "predict_strict" if kind == "heights" else "extremes_strict",
                call, ref, {"height_max_m": 0.03} if kind == "heights" else {"time_max_s": 180})
    return {"format": "otc-tier-b-1", "window": {"start": START, "stop": STOP},
            "astro_table": table, "inputs": inputs, "official_pins": pins,
            "rows": rows, "folded_calls": folded_calls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cache", "official", "run-dir", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--tcd-sha256", required=True)
    args = parser.parse_args()
    document = build(args)
    args.out.write_text(json.dumps(document, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False) + "\n")
    print(f"Bound {len(document['rows'])} official rows; {digest(args.out)}")


if __name__ == "__main__":
    main()
