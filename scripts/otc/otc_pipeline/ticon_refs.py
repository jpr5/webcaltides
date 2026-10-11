"""Independent references for the TICON-4 quality gate, built from cached downloads (never committed).

    python -m otc_pipeline.ticon_refs --cache DIR --ticon CSV --weak-inputs JSON --out refs.json
    load_references(path) -> (records, sha256)        what ticon_sets.TiconCarry.from_audit reads

The cache DIR holds what was downloaded once (at most 1 request a second), so a build is reproducible:
- chs_stations.json: the CHS IWLS station list (api-iwls.dfo-mpo.gc.ca/api/v1/stations);
- chs/{code}_{01,07}.json: IWLS official predictions (wlp), hourly, January and July 2024;
- chs2026/{code}_wlp_{MM}.json: the same for months of 2026;
- station2026.html: JMA's 2026 tide-table station list (M2 S2 K1 O1 harmonic constants).
EOT20 constants at the gauge cell come from the time-base audit's weak inputs.

Per TICON-4 record (refs.json "records"):
- official_series: the CHS official predictions themselves, as month segments {"start_s", "values" (m, hourly)},
  for a record in Canada: the station with the GESLA file's CHS number within 5 km, else the nearest within 2 km;
- official_constants: JMA's M2 S2 K1 O1 (kappa on the local meridian -> Greenwich g = kappa - p lon), nearest
  station within 3 km;
- eot20: EOT20's M2 S2 N2 K1 O1 at the gauge cell (the gate decides where it is valid).
The output names each input file with its SHA-256; the build puts refs.json's own SHA-256 into the sets'
provenance.input_sha256.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

__all__ = ["build_references", "load_references"]

FORMAT = 1
CHS_CODE_KM, CHS_NEAR_KM, JMA_KM = 5.0, 2.0, 3.0
MIN_HOURS = 600                  # a month segment with fewer hourly values is not used


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _km(a, b, c, d) -> float:
    p = math.radians
    h = math.sin((p(c) - p(a)) / 2) ** 2 + math.cos(p(a)) * math.cos(p(c)) * math.sin((p(d) - p(b)) / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def _segment(path: Path) -> dict | None:
    try:
        rows = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or len(rows) < MIN_HOURS:
        return None
    ts = [datetime.fromisoformat(r["eventDate"].replace("Z", "+00:00")).timestamp() for r in rows]
    if any(round(b - a) != 3600 for a, b in zip(ts, ts[1:])):
        return None                                   # hourly and gap-free, or not used
    return {"start_s": int(ts[0]), "values": [round(float(r["value"]), 3) for r in rows]}


def _jma(html: str) -> list[dict]:
    out = []
    for row in re.findall(r'<tr\s+class="mtx"><td>\d+</td>(.*?)</tr>', html, re.S):
        tds = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<td>(.*?)</td>", row, re.S)]

        def dm(s):
            m = re.match(r"\s*(\d+)\D+(\d+)", s)
            return int(m.group(1)) + int(m.group(2)) / 60
        lat, lon = dm(tds[2]), dm(tds[3])
        v = [float(x) for x in tds[7:15]]
        cons = {}
        for i, (n, p) in enumerate((("M2", 2), ("S2", 2), ("K1", 1), ("O1", 1))):
            cons[n] = [round(v[2 * i] / 100, 4), round((v[2 * i + 1] - p * lon) % 360, 2)]
        out.append({"code": tds[0], "lat": lat, "lon": lon, "constituents": cons})
    return out


def build_references(cache: Path, ticon_csv: Path, weak_inputs: Path) -> dict:
    """refs.json content for every TICON-4 record (positions from the TICON-4 CSV)."""
    cache = Path(cache)
    pos = {}
    with open(ticon_csv, newline="") as f:
        for r in csv.DictReader(f):
            pos.setdefault(r["tide_gauge_name"], (float(r["lat"]), float(r["lon"])))
    chs = [s for s in json.loads((cache / "chs_stations.json").read_text())
           if any(t["code"] == "wlp" for t in s.get("timeSeries", []))]
    jma = _jma((cache / "station2026.html").read_text(encoding="utf-8", errors="replace"))
    weak = json.loads(Path(weak_inputs).read_text())["records"]
    used: dict[str, str] = {}
    records: dict[str, list] = {}
    for g, (lat, lon) in sorted(pos.items()):
        refs = []
        if "-can-" in g:
            code = re.sub(r"[a-z]$", "", g.split("-")[-3]).zfill(5)
            by = [q for q in chs if q["code"] == code and _km(lat, lon, q["latitude"], q["longitude"]) < CHS_CODE_KM]
            best = min(chs, key=lambda q: _km(lat, lon, q["latitude"], q["longitude"]))
            pick = by[0] if by else (best if _km(lat, lon, best["latitude"], best["longitude"]) < CHS_NEAR_KM else None)
            if pick:
                files = sorted([cache / "chs" / f"{pick['code']}_{m}.json" for m in ("01", "07")] +
                               list((cache / "chs2026").glob(f"{pick['code']}_wlp_[0-9][0-9].json")))
                segs = []
                for p in files:
                    if p.is_file() and (s := _segment(p)):
                        segs.append(s)
                        used[str(p.relative_to(cache))] = _sha(p)
                if segs:
                    refs.append({"kind": "official_series", "id": f"official:chs:{pick['code']}",
                                 "km": round(_km(lat, lon, pick["latitude"], pick["longitude"]), 2), "segments": segs})
        if "-jpn-" in g and jma:
            best = min(jma, key=lambda q: _km(lat, lon, q["lat"], q["lon"]))
            d = _km(lat, lon, best["lat"], best["lon"])
            if d < JMA_KM:
                refs.append({"kind": "official_constants", "id": f"official:jma:{best['code']}", "km": round(d, 2),
                             "constituents": best["constituents"]})
        e = (weak.get(g) or {}).get("eot")
        if e:
            refs.append({"kind": "eot20", "id": "eot20", "km": e["dist_km"],
                         "constituents": {k: [round(e[k][0], 5), round(e[k][1], 3)]
                                          for k in ("M2", "S2", "N2", "K1", "O1") if k in e}})
        if refs:
            records[g] = refs
    inputs = {"chs_stations.json": _sha(cache / "chs_stations.json"), "station2026.html": _sha(cache / "station2026.html"),
              "ticon_csv": _sha(ticon_csv), "weak_inputs": _sha(weak_inputs), **dict(sorted(used.items()))}
    return {"format": FORMAT, "inputs": inputs, "records": records}


def load_references(path: Path) -> tuple[dict[str, list], str]:
    """(record -> references, SHA-256 of the file) of a refs.json from build_references."""
    data = Path(path).read_bytes()
    doc = json.loads(data)
    if doc.get("format") != FORMAT:
        raise ValueError(f"{path}: refs format {doc.get('format')!r}, expected {FORMAT}")
    return doc["records"], hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", required=True, type=Path)
    ap.add_argument("--ticon", required=True, type=Path)
    ap.add_argument("--weak-inputs", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    doc = build_references(a.cache, a.ticon, a.weak_inputs)
    a.out.write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n")
    kinds: dict[str, int] = {}
    for refs in doc["records"].values():
        for r in refs:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"{a.out}: {len(doc['records'])} records, {kinds}, sha256 {_sha(a.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
