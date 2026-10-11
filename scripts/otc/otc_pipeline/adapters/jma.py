"""JMA (Japan Meteorological Agency) harmonic constants (manifest A-JMA-a, R-REF survey item 1).

JMA's tide-table station list `station<YEAR>.php` gives, for each of its ~239 tide-table
stations, the position (degrees and whole minutes), the height of MSL above the tide-table datum
(潮位表基準面, cm) and the amplitude (cm) and phase lag (遅角, degrees) of M2, S2, K1 and O1. For
the stations JMA itself runs (気象庁所管, the ones with a 分潮一覧表 link) `harms60.php` gives the
full 60-constituent list, the analysis years and the longitude the phases are referred to.

Phase reference. harms60 states "遅角は現地子午線を基準にしています" (the phase lags are referred
to the local meridian): they are local epochs kappa, with the equilibrium argument taken at the
station's own meridian. The Greenwich phase lag is g = kappa - p * lambda (p the species number
of the constituent, lambda the east longitude of the station; Schureman: kappa = G + pL with L
west longitude). The per-source canary against our GESLA fits of the Japanese gauges is what
confirms this; reading the phases as Greenwich, or as Japan standard time (UTC+9), fails it.
The parser refuses a harms60 page that no longer states the local-meridian reference.

The format's convention object (0.3 and 1.0 alike) can express "local" only as one zone offset
for the whole source, which a per-station meridian is not. The adapter declares phase_reference "local" with
utc_offset_hours 9 (JST, the time base of JMA's predictions) and a convention_id that names the
local-meridian rule; the published phases are already Greenwich (`to_greenwich_local_meridian`).

Ownership. Only JMA-owned stations are cleared for redistribution under JMA's Public Data
Licence; the other stations of the table belong to the Japan Coast Guard, GSI or port authorities
(third-party content). Every set carries `provenance.owner` ("jma", "jcg_or_gsi" or
"third_party") and `provenance.redistribution` ("redistribute" or "comparator_only").
`sets()` is the release output and yields only the JMA-owned, redistributable sets (format 1.0
allows nothing else in a release file: owner "jma", redistribution "redistribute");
`comparator_sets()` yields every station, the others as comparator_only, for comparisons inside
the pipeline (the canary). A JMA-owned station is one
with a 分潮一覧表 (harms60) link: the page says the list is given only for JMA-owned stations. A
third-party station whose MSL elevation is listed is a JCG or GSI station (the page lists MSL
elevations only for JMA, JCG and GSI stations).

Datums (datums spec §4.1 item 5, slot DT5d). The constants' zero is the station's tide-table
datum (潮位表基準面), which JMA describes as the average low water of spring tides, "海図の水深0mに
ほぼ相当" (approximately the zero of the chart's depths): `zero` `chart_datum`, `zero_name` and
`source_name` 潮位表基準面, `chart_datum` `tide_table_datum` (= 0). `msl` is JMA's MSL above that
datum (the table's MSL-潮位表基準面, also msl_offset_m), flagged `provisional` where the table marks
it ＊１ (provisional values after the 2011 earthquake's ground movement). No epoch is written: the
page says MSL is "原則として" (as a rule) the 2020-2024 mean, not per station. JMA's elevation
(標高) of the datum is not written as `tp`: JMA uses Tokyo Bay mean sea level (TP) as the
reference "but at some stations, such as remote islands, GSI's local height reference or the
station's own MSL" without saying which, and the ＊２ stations (横浜, 神津島, 能登) are computed from
MSL; a `tp` key would mislabel those.

Predictions. `fetch_predictions` fetches JMA's hourly tide predictions (text, cm above the
tide-table datum, JST) for audits; `parse_predictions` reads them.

    uv run python -m otc_pipeline.adapters.jma --cache-dir DIR [CODE ...]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping

from otc_pipeline.adapter import FetchResult, SourceSet
from otc_pipeline.canary import to_greenwich
from otc_pipeline.datums import published
from otc_pipeline.http import PoliteFetcher

BASE = "https://www.data.jma.go.jp/kaiyou/db/tide/suisan/"
PRED_BASE = "https://www.data.jma.go.jp/kaiyou/data/db/tide/suisan/txt/"
MIN_INTERVAL_S = 1.5                       # polite: sequential, >= 1 s between requests
SPDX = "LicenseRef-JMA-PDL-1.0"
LICENCE_URL = "https://www.jma.go.jp/jma/en/copyright.html"
JST = 9                                    # hours east of UTC
LOCAL_MERIDIAN_NOTE = "遅角は現地子午線を基準にしています"
SPEED_TOL_DEG_PER_HOUR = 1e-6              # JMA's 7-decimal speed vs the TCD speed of the mapped name
PHASE_REFERENCES = ("local_meridian", "jst", "greenwich_utc")

# The station table's header cells (abbr attributes) in order; a change means a new layout.
TABLE_ABBRS = ("番号", "地点記号", "掲載地点名", "緯度", "経度", "MSL-潮位表基準面", "MSLの標高",
               "潮位表基準面の標高", "主要4分潮", "分潮一覧表", "備考", "M2", "S2", "K1", "O1",
               "M2の振幅", "M2の遅角", "S2の振幅", "S2の遅角", "K1の振幅", "K1の遅角", "O1の振幅", "O1の遅角")
TABLE_UNIT_NOTES = ("MSL-<br />潮位表<br />基準面<br />（cm）", "振幅<br />（cm）", "遅角<br />（°）")
HARMS_HEADER = ("記号（日本語名称）", "速度（度/時）", "振幅（cm）", "遅角（°）")
MAIN4 = ("M2", "S2", "K1", "O1")
PROVISIONAL_MSL_MARK = "＊１"      # 備考: the MSL is a provisional value (the table's footnote)
TIDE_TABLE_DATUM = "潮位表基準面"

# JMA symbol (subscripts flattened, Greek letters spelled) -> TCD name. JMA's M1 is
# 155.555 (14.4920521 deg/h) = TCD M1C, not TCD M1 (14.4966939). JMA's OQ2 (27.3416964) is not
# TCD OQ2 (27.3509801); its TCD twin is OQ2-HORN, which is not a valid OTC name: it is left out
# (recorded in provenance.source_constituents_not_published; at most a few mm).
NAME_MAP = {"SIGMA1": "SIG1", "THETA1": "THE1", "LAMBDA2": "LDA2", "M1": "M1C"}
DROPPED = {"OQ2": "JMA OQ2 is 27.3416964 deg/h; the TCD's OQ2 is 27.3509801 and its twin OQ2-HORN "
                  "is not a valid OTC constituent name"}
GREEK = {"σ": "SIGMA", "ρ": "RHO", "χ": "CHI", "π": "PI", "ψ": "PSI", "φ": "PHI", "θ": "THETA",
         "μ": "MU", "ν": "NU", "λ": "LAMBDA"}


class JmaParseError(ValueError):
    pass


# --- phase conversion -----------------------------------------------------------------------------

def species(speed_deg_per_hour: float) -> int:
    """Species number p of a constituent: the nearest whole number of cycles per lunar/solar day
    (0 for Sa..Mf, 1 for diurnal, 2 for semidiurnal, ...)."""
    return int(round(speed_deg_per_hour / 15.0))


def to_greenwich_local_meridian(kappa_deg: float, speed_deg_per_hour: float, lon_east_deg: float) -> float:
    """A phase lag referred to the station's own meridian (local epoch kappa) as a Greenwich phase
    lag: g = kappa - p * lambda_east."""
    g = (float(kappa_deg) - species(speed_deg_per_hour) * float(lon_east_deg)) % 360.0
    return 0.0 if g >= 360.0 else g


# --- parsing --------------------------------------------------------------------------------------

def _text(cell: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()


def _deg_min(s: str, what: str) -> float:
    m = re.fullmatch(r"(\d{1,3})゜(\d{1,2})'", s)
    if not m or int(m[2]) >= 60:
        raise JmaParseError(f"{what}: {s!r} is not degrees゜minutes'")
    return int(m[1]) + int(m[2]) / 60.0


def _num(s: str, what: str) -> float | None:
    if s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        raise JmaParseError(f"{what}: {s!r} is not a number") from None


def _decode(data: bytes) -> str:
    return data.decode("utf-8").replace("\r", "")


def parse_station_table(data: bytes) -> dict[str, dict]:
    """code -> station row of a station<YEAR>.php page. Raises JmaParseError on a changed layout."""
    t = _decode(data)
    abbrs = tuple(re.findall(r'<th[^>]*abbr="([^"]*)"', t))
    if abbrs != TABLE_ABBRS:
        raise JmaParseError(f"station table header changed: {abbrs!r}")
    for note in TABLE_UNIT_NOTES:
        if note not in t:
            raise JmaParseError(f"station table no longer says {note!r} (units)")
    out: dict[str, dict] = {}
    for row in re.findall(r'<tr\s+class="mtx">(.*?)</tr>', t, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if not cells or (len(cells) == 1 and _text(cells[0]).startswith("＊")):
            continue    # the footnote row (＊１ provisional MSL, ＊２ datum from MSL)
        if len(cells) != 18:
            raise JmaParseError(f"station row with {len(cells)} cells: {_text(row)[:80]!r}")
        c = [_text(x) for x in cells]
        code = c[1]
        if not re.fullmatch(r"[A-Z0-9]{2}", code) or code in out:
            raise JmaParseError(f"bad or duplicate station code {code!r}")
        harms = re.search(r'href="(harms60\.php\?[^"]*)"', cells[16])
        main4 = {}
        for i, name in enumerate(MAIN4):
            amp, ph = _num(c[8 + 2 * i], f"{code} {name} amplitude"), _num(c[9 + 2 * i], f"{code} {name} phase")
            if amp is None or ph is None or amp < 0 or not 0 <= ph < 360:
                raise JmaParseError(f"{code}: {name} amplitude/phase {c[8 + 2 * i]!r}/{c[9 + 2 * i]!r}")
            main4[name] = {"amplitude_cm": amp, "phase_deg": ph}
        out[code] = {
            "number": int(c[0]), "code": code, "name": c[2],
            "lat": _deg_min(c[3], f"{code} latitude"), "lon": _deg_min(c[4], f"{code} longitude"),
            "msl_above_datum_cm": _num(c[5], f"{code} MSL"),
            "msl_elevation_cm": _num(c[6], f"{code} MSL elevation"),
            "datum_elevation_cm": _num(c[7], f"{code} datum elevation"),
            "main4": main4,
            "harms60_href": html.unescape(harms[1]) if harms else None,
            "note": c[17],
        }
        if out[code]["msl_above_datum_cm"] is None:
            raise JmaParseError(f"{code}: no MSL above the tide-table datum")
    if not out:
        raise JmaParseError("station table has no station rows")
    # ＊１ is read as "provisional MSL" (DT5d's `provisional` flag): refuse a page whose footnote no
    # longer says so
    if any("＊１" in v["note"] for v in out.values()) and not re.search(r"＊１[^＊]*MSL[^＊]*暫定値", _text(t)):
        raise JmaParseError("station table marks ＊１ but its footnote no longer says the MSL is provisional (暫定値)")
    return out


def _symbol(th: str) -> tuple[str, str]:
    """(JMA symbol as printed, the symbol in OTC spelling) of a harms60 row header."""
    sym = re.sub(r"\s*\(.*\)\s*$", "", html.unescape(th))
    sym = re.sub(r"<sub>(.*?)</sub>", r"\1", sym).strip()
    return sym, "".join(GREEK.get(ch, ch) for ch in sym).upper()


def parse_harms60(data: bytes, code: str) -> dict:
    """A harms60.php page: {name, longitude_deg, analysis_years, constituents[]}. Raises
    JmaParseError unless it is the station's 60 constituents in cm, local-meridian phases."""
    t = _decode(data)
    if LOCAL_MERIDIAN_NOTE not in t:
        raise JmaParseError(f"{code}: harms60 no longer says {LOCAL_MERIDIAN_NOTE!r}")
    title = re.search(r"<h2[^>]*>\s*分潮一覧表\s*(.*?)\s*</h2>", t, re.S) or re.search(
        r"<title>[^<]*分潮一覧表\s*(.*?)\s*</title>", t)
    if not title:
        raise JmaParseError(f"{code}: harms60 has no station name")
    header = tuple(_text(h) for h in re.findall(r'<tr class="mtx">(.*?)</tr>', t, re.S)[0:1]
                   for h in re.findall(r"<th>(.*?)</th>", h))
    if header != HARMS_HEADER:
        raise JmaParseError(f"{code}: harms60 header {header!r}")
    lon = re.search(r"<h3>経度</h3>\s*<div[^>]*>\s*<ul>\s*(\d+)度\s*(\d+)分\s*(\d+)秒", t)
    if not lon:
        raise JmaParseError(f"{code}: harms60 has no 経度 (longitude)")
    years = dict(re.findall(r"<li>\s*(長周期|短周期)（年）\s*([0-9-]+)\s*</li>", t))
    cons = []
    for th, sp, amp, ph in re.findall(
            r'<tr><th align="left">(.*?)</th><th>\s*([0-9.]+)</th><td[^>]*>\s*([0-9.]+)</td>'
            r'<td[^>]*>\s*([0-9.]+)</td></tr>', t):
        sym, name = _symbol(th)
        cons.append({"source_name": sym, "name": name, "speed": float(sp),
                     "amplitude_cm": float(amp), "phase_deg": float(ph)})
    if len(cons) != 60 or len({c["name"] for c in cons}) != 60:
        raise JmaParseError(f"{code}: harms60 has {len(cons)} constituent rows, expected 60")
    return {"name": _text(title[1]),
            "longitude_deg": int(lon[1]) + int(lon[2]) / 60.0 + int(lon[3]) / 3600.0,
            "analysis_years": {"long_period": years.get("長周期"), "short_period": years.get("短周期")},
            "constituents": cons}


# --- predictions ----------------------------------------------------------------------------------

def parse_predictions(data: bytes, code: str) -> list[tuple[datetime, float]]:
    """JMA's yearly hourly-prediction text file -> [(UTC time, height m above the tide-table datum)].
    Each line: 24 x 3-char hourly heights (cm, JST hours 0..23), YYMMDD (3 x 2 chars), station code."""
    out = []
    for n, line in enumerate(data.decode("ascii").splitlines(), 1):
        if not line.strip():
            continue
        if len(line) < 80 or line[78:80] != code:
            raise JmaParseError(f"{code}: prediction line {n} is not for {code}: {line[:82]!r}")
        yy, mm, dd = (int(line[72 + 2 * i:74 + 2 * i]) for i in range(3))
        day = datetime(2000 + yy, mm, dd, tzinfo=UTC) - timedelta(hours=JST)
        for h in range(24):
            out.append((day + timedelta(hours=h), int(line[3 * h:3 * h + 3]) / 100.0))
    return out


def fetch_predictions(cache_dir: Path, code: str, year: int, fetcher: PoliteFetcher | None = None):
    """(Fetched, [(UTC time, m)]) of JMA's hourly predictions of one station-year."""
    f = fetcher or PoliteFetcher(Path(cache_dir), min_interval_s=MIN_INTERVAL_S)
    got = f.get(f"{PRED_BASE}{year}/{code}.txt", cache_name=f"predictions/{year}/{code}.txt")
    return got, parse_predictions(got.bytes(), code)


# --- the adapter ----------------------------------------------------------------------------------

class Speeds:
    """TCD name and speed for a JMA constituent. `from_tcd_dump` reads the TCD dump (G03a)."""

    def __init__(self, speeds: Mapping[str, float]):
        self.speeds = dict(speeds)

    @classmethod
    def from_tcd_dump(cls, path: Path | None = None) -> "Speeds":
        import os

        from gesla.read_gesla import otc_work
        from gesla.tcd_table import TcdTable
        dump = Path(path or os.environ.get("OTC_TCD_TABLE") or otc_work() / "tcd_table.json")
        return cls({n: c.speed for n, c in TcdTable.load(dump).constituents.items()})

    def tcd(self, jma_name: str, jma_speed: float) -> tuple[str, float]:
        name = NAME_MAP.get(jma_name, jma_name)
        if name not in self.speeds:
            raise JmaParseError(f"constituent {jma_name} ({name}) has no TCD definition")
        speed = self.speeds[name]
        if abs(speed - jma_speed) > SPEED_TOL_DEG_PER_HOUR + 1e-7:
            raise JmaParseError(f"constituent {jma_name}: JMA speed {jma_speed:.7f}, TCD {name} {speed:.7f}")
        return name, speed


class JmaAdapter:
    source = "jma"
    source_type = "official"
    quantity = "water_level"
    adapter_version = "2"   # 2: tide-table datum and MSL with basis (DT5d)
    contract_sample = ("TK", "OS", "QS")   # Tokyo, Osaka (JMA); Yokohama (third party)

    def __init__(self, phase_reference: str = "local_meridian", *, year: int | None = None,
                 speeds: Speeds | None = None):
        if phase_reference not in PHASE_REFERENCES:
            raise ValueError(f"phase_reference {phase_reference!r} is not one of {PHASE_REFERENCES}")
        self.phase_reference = phase_reference
        self.year = year or datetime.now(UTC).year
        self._speeds = speeds

    @property
    def speeds(self) -> Speeds:
        if self._speeds is None:
            self._speeds = Speeds.from_tcd_dump()
        return self._speeds

    # -- fetch ------------------------------------------------------------------------------
    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        f = PoliteFetcher(cache_dir, min_interval_s=MIN_INTERVAL_S)
        tab = f.get(f"{BASE}station{self.year}.php", cache_name=f"station{self.year}.html")
        stations = parse_station_table(tab.bytes())
        codes = sorted(stations) if only is None else list(only)
        unknown = [c for c in codes if c not in stations]
        if unknown:
            raise KeyError(f"not JMA tide-table stations in {self.year}: {unknown}")
        files, items = [tab.input_file()], {}
        for code in codes:
            st = stations[code]
            it = {"station": st, "harms60": None, "input_sha256": [tab.sha256]}
            if st["harms60_href"]:
                got = f.get(BASE + st["harms60_href"], cache_name=f"harms60/{self.year}/{code}.html")
                files.append(got.input_file())
                h = parse_harms60(got.bytes(), code)
                if h["name"] != st["name"]:
                    raise JmaParseError(f"{code}: harms60 is for {h['name']!r}, table says {st['name']!r}")
                by = {c["name"]: c for c in h["constituents"]}
                for name, v in st["main4"].items():
                    if (by[name]["amplitude_cm"], by[name]["phase_deg"]) != (v["amplitude_cm"], v["phase_deg"]):
                        raise JmaParseError(f"{code}: {name} differs between the table and harms60")
                it["harms60"] = h
                it["input_sha256"].append(got.sha256)
            items[code] = it
        version = min(fl.fetched_at for fl in files)[:10].replace("-", "")
        fetched = FetchResult(source=self.source, source_version=version, cache_dir=cache_dir,
                              files=tuple(files), items=items)
        manifest = fetched.manifest(
            dataset=f"JMA tide-table station list {self.year} (M2/S2/K1/O1) and 60-constituent lists "
                    "of JMA-owned stations", licence=SPDX, citation=self.licence()["citation"],
            archive=f"cache/{self.source}/")
        (cache_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True,
                                                            ensure_ascii=False) + "\n")
        return fetched

    # -- convention and licence --------------------------------------------------------------
    def convention(self) -> dict:
        common = {"v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                  "constituent_table_version": "tcd"}
        if self.phase_reference == "greenwich_utc":      # test flag: served phases read as Greenwich
            return {"convention_id": "jma-read-as-greenwich", "phase_reference": "greenwich_utc", **common}
        if self.phase_reference == "jst":                # test flag: served phases read as JST (UTC+9)
            return {"convention_id": "jma-read-as-jst", "phase_reference": "local",
                    "utc_offset_hours": JST, **common}
        return {"convention_id": "jma-local-meridian", "phase_reference": "local",
                "utc_offset_hours": JST, **common}

    def licence(self) -> dict:
        return {"licence_id": "jma-pdl-1.0", "spdx": SPDX, "provider": "Japan Meteorological Agency",
                "citation": "Japan Meteorological Agency, tide tables (潮位表) and harmonic constants, "
                            "https://www.data.jma.go.jp/kaiyou/db/tide/suisan/",
                "attribution": f"Source: Japan Meteorological Agency (気象庁) website, tide tables ({BASE}). "
                               "Edited by OpenTideConstants (units converted to metres, phase lags "
                               "converted to Greenwich).",
                "url": LICENCE_URL,
                # PDL 1.0 is compatible with CC BY 4.0 (JMA's terms): commercial use with attribution
                # and an edit notice. It covers JMA's own content only, which is all sets() yields.
                "commercial_use": True}

    def _phase(self, kappa: float, speed: float, lon: float) -> float:
        if self.phase_reference == "local_meridian":
            return round(to_greenwich_local_meridian(kappa, speed, lon), 3) % 360.0
        return round(to_greenwich(kappa, speed, self.convention()), 3) % 360.0

    # -- outputs ------------------------------------------------------------------------------
    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        """code -> SHA-256 of the station's normalised row and constants (not the raw bytes)."""
        keys = {}
        for code, it in fetched.items.items():
            st = {k: v for k, v in it["station"].items() if k != "number"}
            norm = {"station": st, "harms60": it["harms60"]}
            keys[code] = hashlib.sha256(json.dumps(norm, sort_keys=True, separators=(",", ":"),
                                                   ensure_ascii=False).encode()).hexdigest()
        return keys

    def records(self, fetched: FetchResult):
        return ()

    @staticmethod
    def owner(it: Mapping) -> str:
        st = it["station"]
        if st["harms60_href"]:
            return "jma"
        return "jcg_or_gsi" if st["msl_elevation_cm"] is not None else "third_party"

    def sets(self, fetched: FetchResult):
        """The release output: the JMA-owned stations only (redistribution "redistribute")."""
        for ss in self.comparator_sets(fetched):
            if ss.constant_set["provenance"]["redistribution"] == "redistribute":
                yield ss

    def comparator_sets(self, fetched: FetchResult):
        """Every station of the fetch, JMA-owned or not (comparator_only): for comparisons inside the
        pipeline (the canary), never for a release file."""
        conv = self.convention()
        for code, it in sorted(fetched.items.items()):
            st, h = it["station"], it["harms60"]
            owner = self.owner(it)
            if h is not None:
                lon_ref = h["longitude_deg"]
                rows = h["constituents"]
            else:
                lon_ref = st["lon"]
                rows = [{"name": n, "source_name": n, "speed": None, **st["main4"][n]} for n in MAIN4]
            cons, dropped = [], []
            for r in rows:
                if r["name"] in DROPPED:   # not a TCD constituent: kept in provenance, not as dropped_constituents
                    dropped.append({"source_name": r["source_name"], "speed_deg_per_hour": r["speed"],
                                    "amplitude_m": round(r["amplitude_cm"] / 100.0, 6),
                                    "reason": DROPPED[r["name"]]})
                    continue
                name, speed = (self.speeds.tcd(r["name"], r["speed"]) if r["speed"] is not None
                               else (r["name"], self.speeds.speeds[r["name"]]))
                c = {"name": name, "speed_deg_per_hour": speed,
                     "amplitude_m": round(r["amplitude_cm"] / 100.0, 6),
                     "phase_deg": self._phase(r["phase_deg"], speed, lon_ref)}
                if r["source_name"] != name:
                    c["source_name"] = r["source_name"]
                cons.append(c)
            # The tide-table datum and JMA's MSL above it (module docstring: no `tp`, no MSL epoch).
            msl = round(st["msl_above_datum_cm"] / 100.0, 4)
            datum = {"msl_offset_m": msl, "zero": "chart_datum", "zero_name": TIDE_TABLE_DATUM,
                     "chart_datum": "tide_table_datum",
                     "named": {"tide_table_datum": 0.0, "msl": msl},
                     "basis": {"tide_table_datum": published(TIDE_TABLE_DATUM),
                               "msl": published("MSL", provisional=PROVISIONAL_MSL_MARK in st["note"])}}
            prov = {"adapter_version": self.adapter_version, "input_sha256": list(it["input_sha256"]),
                    "owner": owner,
                    "redistribution": "redistribute" if owner == "jma" else "comparator_only",
                    "phase_reference_longitude_deg": round(lon_ref, 6),
                    "jma_station_code": code}
            if h is not None:
                prov["analysis_years"] = h["analysis_years"]
            if dropped:
                prov["source_constituents_not_published"] = dropped
            cs = {"source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                  "source_record_id": code, "source_version": fetched.source_version,
                  "datum": datum, "convention_id": conv["convention_id"],
                  "licence_id": self.licence()["licence_id"], "qc_status": "ok",
                  "constituents": cons, "provenance": prov}
            yield SourceSet(
                station_hint={"name": st["name"], "country": "JPN", "lat": round(st["lat"], 6),
                              "lon": round(st["lon"], 6), "type": "reference", "timezone": "Asia/Tokyo",
                              "aliases": {}},
                constant_set=cs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.adapters.jma")
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("codes", nargs="*")
    a = ap.parse_args(argv)
    ad = JmaAdapter(year=a.year)
    fetched = ad.fetch(a.cache_dir, only=a.codes or None)
    for s in ad.comparator_sets(fetched):   # every station, with its owner
        cs = s.constant_set
        m2 = next(c for c in cs["constituents"] if c["name"] == "M2")
        print(cs["source_record_id"], s.station_hint["name"], cs["provenance"]["owner"], len(cs["constituents"]),
              "M2", (m2["amplitude_m"], m2["phase_deg"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
