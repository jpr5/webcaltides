"""LINZ / Toitū Te Whenua harmonic constants for New Zealand (manifest slot A-LINZ, PP P3).

LINZ publishes one zip a year, `NZ_Tide_Constituents_<year>.zip`, at a dated path
(`/sites/default/files/<yyyy-mm>/…`), so `fetch` scrapes the link from the tide-constituents
page and never pins it. The zip holds 56 fixed-width `*_Harm_*.txt` files (two use `-Harm_`,
two names contain a space): per constituent the frequency (cycles/h), amplitude A (cm) and
phase G (deg).

Phase reference. Every header says `TIME ZONE = 180`: the phases are referred to the 180 deg
meridian, NZST = UTC+12. `sets` converts them to Greenwich with the standard time-meridian
correction, g = G - speed * 12 h (`canary.to_greenwich`, convention `phase_reference = local`,
`utc_offset_hours = 12`). The reading was fixed by red-green (proof in
cr/otc-impl/A-LINZ/): read as Greenwich, K1 and O1 are about 180 deg off our GESLA fits and
LINZ's own predictions; converted, they agree.

Header metadata is untrusted. 077NELSON says `LATITUDE = 1416` (Nelson is at 41 16 S) and its
header statistics are Auckland's; the 354GREEN ISLAND header says station 254. So every header
statistic and the header station number are dropped; the site id is the file-name prefix
(schema `linz` alias, e.g. 077NELSON). The header position (DDMM, south and east) is kept only
when it lies inside the NZ box and within 5 km of the same port (by LINZ port number) on LINZ's
tide-predictions station list (position from each port's prediction CSV header line);
otherwise it is replaced by the list position, and the set's provenance records the
replacement. A rejected position with no list entry is an error.

Datums (datums spec §4.1 item 4, slot DT5c). LINZ's "Standard port tidal levels" page gives, for
about 16 standard ports, MHWS, MHWN, MLWN, MLWS, MSL, HAT and LAT in metres "referred to Chart
Datum, which is the same as the zero of the tidal predictions in all cases". A standard port's
set (matched by name to its prediction CSV header, then by port number to its zip member) gets
`zero` `chart_datum`, `zero_name` CD, `chart_datum` `cd` (= 0) and those levels, each kind
`published`, method `source`, LINZ's column name; the epochs are the periods the page states:
MHWS..MLWS are averages over predictions for one stated year (1 July 2026 - 30 June 2027 on the
page of 8 Jun 2026), HAT and LAT the extremes over 1 January 2000 - 31 December 2018. MSL is
from each port's observation period, given on another page: no epoch. The spring and neap
ranges are differences of the levels: not taken. The set's Z0 (`msl_offset_m`) is above the
same zero; a port whose Z0 differs from the page's MSL by more than MSL_Z0_TOLERANCE_M gets no
levels (logged), since its constants might not be referred to chart datum.

SA and S1. The files are IOS (Foreman) analyses: SA is h - p1 and S1 is T + p1 - 90, not the TCD's
h and T. Their phases are converted (`ios_definitions`) after the NZST correction. LINZ prints SA's
frequency as .00011407 cph, IOS's h - p1 (the TCD's h rounds to .00011408); a file with any other SA
frequency is refused, since its definition would then be unknown.

Licence: CC BY 4.0 (LINZ site default, https://www.linz.govt.nz/copyright), attribution in text.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import urllib.parse
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from otc_pipeline.adapter import FetchResult, InputFile, SourceSet, sha256_file
from otc_pipeline.canary import to_greenwich
from otc_pipeline.datums import period, published
from otc_pipeline.ios_definitions import P1_RATE_DEG_PER_HOUR, to_tcd_phase
from otc_pipeline.http import PoliteFetcher

log = logging.getLogger(__name__)

CONSTITUENTS_PAGE = ("https://www.linz.govt.nz/products-services/data/types-linz-data/"
                     "sea-level-data/tide-constituents")
STANDARD_PORT_LEVELS_PAGE = ("https://www.linz.govt.nz/guidance/marine-information/tide-prediction-guidance/"
                             "standard-port-tidal-levels")
STATION_LIST_PAGE = ("https://www.linz.govt.nz/products-services/tides-and-tidal-streams/"
                     "tide-predictions/tide-predictions-list-view")
ZIP_LINK_RE = re.compile(r'href="([^"]*/(NZ_Tide_Constituents_[^"/]*\.zip))"')
ZIP_YEAR_RE = re.compile(r"NZ_Tide_Constituents_(\d{4})\.zip$")
PORT_CSV_RE = re.compile(r'href="(https://static\.charts\.linz\.govt\.nz/tide-tables/maj-ports/csv/'
                         r'[^"]*?(\d{4})\.csv)"')
MEMBER_RE = re.compile(r"^(\d{1,4})([A-Z][A-Z ]*?)[-_]Harm_(\d{4})-(\d{4})\.txt$")
SITE_ID_RE = re.compile(r"^[0-9]{1,4}[A-Z][A-Z0-9]*$")
HEADER_RE = re.compile(r"STATION\s+(\d+)\s+TIME ZONE\s*=\s*(-?\d+)\s*,\s*LATITUDE\s*=\s*(\d+)\s*,"
                       r"\s*LONGITUDE\s*=\s*(\d+)")
# One constituent row: NO NAME FREQUENCY STN M-Y/M-Y A G. Fortran fixed columns: a value below 1 has no leading
# zero (".7750"), and an amplitude of 100 cm or more runs into the M-Y/M-Y column ("0106/1219134.2423").
ROW_RE = re.compile(r"^\s*\d+\s+([A-Z0-9]+)\s+(\d*\.\d+)\s+\d+\s+(\d{4})/(\d{4})\s*(\d*\.\d+)\s+(\d*\.\d+)\s*$")
# A numbered row (a number, then two more fields): every such row is a constituent row, and one that does not
# match ROW_RE stops the read (a lowercase or punctuated name, a bad frequency, a cut row are never skipped).
ROW_START_RE = re.compile(r"^\s*\d+\s+\S+\s+\S")
PORT_HEADER_RE = re.compile(r"^\s*(\d+)\s*,\s*([^,]+?)\s*,\s*(\d+)°(\d+(?:\.\d+)?)'?([NS])\s*,"
                            r"\s*(\d+)°(\d+(?:\.\d+)?)'?([EW])")

LEVELS_HEADER = ("Standard Port", "MHWS", "MHWN", "MLWN", "MLWS", "SpringRange", "NeapRange", "MSL", "HAT", "LAT")
LEVEL_KEYS = {"MHWS": "mhws", "MHWN": "mhwn", "MLWN": "mlwn", "MLWS": "mlws", "MSL": "msl", "HAT": "hat", "LAT": "lat"}
SPRING_NEAP = ("MHWS", "MHWN", "MLWN", "MLWS")
LEVELS_ZERO_NOTE = "referred to Chart Datum, which is the same as the zero of the tidal predictions in all cases"
_LEVELS_PERIOD = r"(\d{1,2} [A-Z][a-z]+ \d{4})\s*[–-]\s*(\d{1,2} [A-Z][a-z]+ \d{4})"
SPRING_NEAP_PERIOD_RE = re.compile(r"MHWS, MHWN, MLWN and MLWS tidal levels given in the table are the averages of "
                                   r"the levels of all spring and neap tides predicted for the period " + _LEVELS_PERIOD)
HAT_LAT_PERIOD_RE = re.compile(r"values for HAT and LAT given in the table above are the highest and lowest tidal "
                               r"levels that are predicted to occur under average meteorological conditions during "
                               r"the period " + _LEVELS_PERIOD)
MSL_Z0_TOLERANCE_M = 0.05           # page MSL (2 decimals) vs the constants' Z0 at the same port
TIME_ZONE_MERIDIAN = 180            # the only TIME ZONE value the convention covers
UTC_OFFSET_HOURS = TIME_ZONE_MERIDIAN / 15.0
NZ_LAT = (-53.0, -30.0)
NZ_LON = ((165.0, 180.0), (-180.0, -175.0))
MAX_LIST_KM = 5.0
SPEED_TOL_DEG_PER_HOUR = 1e-4       # LINZ frequency x 360 vs the TCD speed of the mapped name
REPO = Path(__file__).resolve().parents[4]
TCD_PATH = REPO / "data" / "latest-xtide.tcd"
OTC_WORK_CLI = REPO / "bin" / "otc-work"


# --- small parsers (pure) -------------------------------------------------------------------------

def find_zip_url(page_html: str, base: str = CONSTITUENTS_PAGE) -> str:
    """The one `NZ_Tide_Constituents_*.zip` link on the tide-constituents page (absolute)."""
    links = sorted({urllib.parse.urljoin(base, html.unescape(m.group(1))) for m in ZIP_LINK_RE.finditer(page_html)})
    if len(links) != 1:
        raise ValueError(f"tide-constituents page: expected one NZ_Tide_Constituents_*.zip link, found {links}")
    return links[0]


def port_csv_urls(list_html: str, year: int) -> list[str]:
    """One prediction CSV per 'Daily Predictions' port of the station list view: the CSV for
    `year` when listed, else the port's earliest listed year."""
    out = []
    for row in re.split(r"<tr\b", list_html)[1:]:
        if not re.search(r"field-tide-type[^>]*>\s*Daily Predictions\s*<", row):
            continue
        by_year: dict[int, str] = {}
        for m in PORT_CSV_RE.finditer(row):
            by_year.setdefault(int(m.group(2)), html.unescape(m.group(1)))
        if by_year:
            out.append(by_year.get(year) or by_year[min(by_year)])
    if not out:
        raise ValueError("tide-predictions list view: no Daily Predictions port with a CSV link")
    return sorted(set(out))


def _day(text: str) -> str:
    return datetime.strptime(text, "%d %B %Y").date().isoformat()


def parse_standard_port_levels(page_html: str) -> dict:
    """The standard-port tidal levels page: {"ports": {name: {column: metres}}, "spring_neap": (start,
    end), "hat_lat": (start, end)} (ISO dates). Raises ValueError when the table layout, the
    chart-datum note or either stated period is not as read here."""
    tables = re.findall(r"<table\b.*?</table>", page_html, re.S)
    if len(tables) != 1:
        raise ValueError(f"standard port levels page: expected one table, found {len(tables)}")
    rows = []
    for tr in re.findall(r"<tr\b.*?</tr>", tables[0], re.S):
        cells = [re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", c))) if i else
                 re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                 for i, c in enumerate(re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S))]
        rows.append(cells)
    if not rows or tuple(rows[0]) != LEVELS_HEADER:
        raise ValueError(f"standard port levels table header {rows[:1]!r} is not {LEVELS_HEADER!r}")
    ports = {}
    for r in rows[1:]:
        if len(r) != len(LEVELS_HEADER) or r[0] in ports:
            raise ValueError(f"standard port levels row {r!r}")
        ports[r[0]] = {col: float(v) for col, v in zip(LEVELS_HEADER[1:], r[1:])}
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page_html)))
    if LEVELS_ZERO_NOTE not in text:
        raise ValueError(f"standard port levels page no longer says {LEVELS_ZERO_NOTE!r}")
    sn, hl = SPRING_NEAP_PERIOD_RE.search(text), HAT_LAT_PERIOD_RE.search(text)
    if not sn or not hl:
        raise ValueError("standard port levels page: the MHWS..MLWS or HAT/LAT period is not stated as expected")
    return {"ports": ports, "spring_neap": (_day(sn[1]), _day(sn[2])), "hat_lat": (_day(hl[1]), _day(hl[2]))}


def levels_datum(levels: dict, z0: float | None) -> tuple[dict | None, str | None]:
    """A standard port's datum above chart datum, or (None, why) when its Z0 does not match MSL."""
    row = levels["row"]
    if z0 is None or abs(z0 - row["MSL"]) > MSL_Z0_TOLERANCE_M:
        return None, (f"standard-port levels not written: Z0 {z0} m vs the page's MSL {row['MSL']} m "
                      f"(tolerance {MSL_Z0_TOLERANCE_M} m)")
    sn = period(*levels["spring_neap"], "LINZ spring/neap predictions")
    hl = period(*levels["hat_lat"], "LINZ HAT/LAT predictions")
    named, basis = {"cd": 0.0}, {"cd": published("Chart Datum")}
    for col, key in LEVEL_KEYS.items():
        named[key] = row[col]
        basis[key] = published(col, epoch=sn if col in SPRING_NEAP else hl if col in ("HAT", "LAT") else None)
    return {"msl_offset_m": round(z0, 6), "zero": "chart_datum", "zero_name": "CD", "chart_datum": "cd",
            "named": named, "basis": basis}, None


@dataclass(frozen=True)
class Port:
    number: int
    name: str
    lat: float
    lon: float


def parse_port_csv_header(data: bytes) -> Port:
    """`077,Nelson,41°16'S,173°16'E` (the first line of a LINZ prediction CSV)."""
    first = data.decode("utf-8-sig").splitlines()[0]
    m = PORT_HEADER_RE.match(first)
    if not m:
        raise ValueError(f"prediction CSV header {first.strip()!r} is not 'number,name,lat,lon'")
    lat = (int(m.group(3)) + float(m.group(4)) / 60.0) * (-1 if m.group(5) == "S" else 1)
    lon = (int(m.group(6)) + float(m.group(7)) / 60.0) * (-1 if m.group(8) == "W" else 1)
    return Port(int(m.group(1)), m.group(2).strip(), round(lat, 6), round(lon, 6))


def site_id(member: str) -> str:
    """The site id (schema `linz` alias) of a zip member: its file-name prefix without spaces."""
    m = MEMBER_RE.match(Path(member).name)
    if not m:
        raise ValueError(f"zip member {member!r} is not <number><NAME>_Harm_<yyyy>-<yyyy>.txt")
    sid = m.group(1) + m.group(2).replace(" ", "")
    if not SITE_ID_RE.fullmatch(sid):
        raise ValueError(f"zip member {member!r}: site id {sid!r} does not match {SITE_ID_RE.pattern}")
    return sid


def ddmm(value: int, *, south: bool) -> float | None:
    """DDMM / DDDMM to signed degrees; None when the minutes are not 0-59."""
    deg, minutes = divmod(int(value), 100)
    if minutes >= 60:
        return None
    x = deg + minutes / 60.0
    if south:
        return -x
    return x - 360.0 if x > 180.0 else x


def in_nz_box(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return NZ_LAT[0] <= lat <= NZ_LAT[1] and any(lo <= lon <= hi for lo, hi in NZ_LON)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, a)))


@dataclass(frozen=True)
class Row:
    name: str          # as LINZ spells it
    freq_cph: float
    start_my: str      # MMYY
    end_my: str
    amp_cm: float
    phase_deg: float   # referred to TIME ZONE meridian


@dataclass(frozen=True)
class HarmFile:
    site_id: str
    header_name: str
    header_station: int
    time_zone: int
    header_lat: float | None
    header_lon: float | None
    rows: tuple[Row, ...]


def parse_harm(text: str, member: str) -> HarmFile:
    """One `*_Harm_*.txt` file. Header statistics are not read (they are untrusted)."""
    lines = text.splitlines()
    if len(lines) < 3 or "Units in CM" not in lines[0]:
        raise ValueError(f"{member}: first line must name the site and say 'Units in CM'")
    m = HEADER_RE.search(lines[1])
    if not m:
        raise ValueError(f"{member}: second line {lines[1].strip()!r} has no STATION/TIME ZONE/LATITUDE/LONGITUDE")
    rows = []
    for line in lines[2:]:
        r = ROW_RE.match(line)
        if r:
            rows.append(Row(r.group(1), float(r.group(2)), r.group(3), r.group(4),
                            float(r.group(5)), float(r.group(6))))
        elif ROW_START_RE.match(line):
            raise ValueError(f"{member}: a constituent row that cannot be read: {line.strip()!r}")
    names = [r.name for r in rows]
    if len(set(names)) != len(names):
        raise ValueError(f"{member}: a constituent is listed twice")
    if not any(n != "Z0" for n in names):
        raise ValueError(f"{member}: no constituent rows")
    return HarmFile(site_id(member), lines[0].split("Units in CM")[0].strip(), int(m.group(1)), int(m.group(2)),
                    ddmm(int(m.group(3)), south=True), ddmm(int(m.group(4)), south=False), tuple(rows))


def normalised_sha256(text: str) -> str:
    """SHA-256 of the member text with line ends and trailing blanks normalised (watch key)."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _my_to_iso(my: str, end: bool) -> str:
    month, yy = int(my[:2]), int(my[2:])
    year = 1900 + yy if yy >= 50 else 2000 + yy
    if not 1 <= month <= 12:
        raise ValueError(f"month-year {my!r}")
    if not end:
        return f"{year:04d}-{month:02d}-01T00:00:00Z"
    nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
    last = datetime.fromtimestamp(nxt.timestamp() - 3600, timezone.utc)
    return last.strftime("%Y-%m-%dT%H:%M:%SZ")


def _span_key(my: str) -> tuple[int, int]:
    yy = int(my[2:])
    return (1900 + yy if yy >= 50 else 2000 + yy, int(my[:2]))


# --- speeds ---------------------------------------------------------------------------------------

class Speeds:
    """Constituent name -> TCD name and speed (deg/h). `from_tcd_dump` reads the TCD dump."""

    def __init__(self, speeds: Mapping[str, float], aliases: Mapping[str, str] | None = None,
                 tcd_sha256: str | None = None):
        self.speeds = dict(speeds)
        self.aliases = dict(aliases or {})
        self.tcd_sha256 = tcd_sha256

    @classmethod
    def from_tcd_dump(cls, path: Path | None = None, tcd_path: Path = TCD_PATH) -> "Speeds":
        from gesla.read_gesla import otc_work
        from gesla.tcd_table import TcdTable
        dump = Path(path or os.environ.get("OTC_TCD_TABLE") or otc_work() / "tcd_table.json")
        table = TcdTable.load(dump, tcd_sha256=sha256_file(tcd_path))
        return cls({n: c.speed for n, c in table.constituents.items()}, table.aliases, table.tcd_sha256)

    def name(self, raw: str) -> str:
        up = raw.strip().upper()
        name = self.aliases.get(up, up)
        if name not in self.speeds:
            raise KeyError(f"constituent {raw!r} has no TCD definition")
        return name

    def speed(self, name: str) -> float:
        return self.speeds[self.name(name)]


# --- the adapter ----------------------------------------------------------------------------------

class LinzAdapter:
    source = "linz"
    source_type = "official"
    quantity = "water_level"
    adapter_version = "1.1.0"   # 1.1: standard-port datums (DT5c)
    contract_sample = ("070AUCKLAND", "073TAURANGA", "097NAPIER", "077NELSON")

    def __init__(self, *, speeds: Speeds | None = None, phase_reference: str = "local",
                 position_check: bool = True, min_interval_s: float = 1.0, static_interval_s: float = 0.25):
        """`phase_reference="greenwich_utc"` and `position_check=False` are test flags (the RED
        of the proof): phases read as Greenwich, header positions trusted."""
        if phase_reference not in ("local", "greenwich_utc"):
            raise ValueError(f"phase_reference {phase_reference!r}")
        self._speeds = speeds
        self.phase_reference = phase_reference
        self.position_check = position_check
        self.min_interval_s = min_interval_s
        self.static_interval_s = static_interval_s

    # -- fetch ----------------------------------------------------------------------------------

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        cache_dir = Path(cache_dir)
        f = PoliteFetcher(cache_dir, min_interval_s=self.min_interval_s,
                          host_intervals={"static.charts.linz.govt.nz": self.static_interval_s})
        page = f.get(CONSTITUENTS_PAGE, cache_name="pages/tide-constituents.html", refresh=True)
        zip_url = find_zip_url(page.bytes().decode("utf-8", "replace"))
        zname = urllib.parse.unquote(zip_url.rsplit("/", 1)[1])
        zgot = f.get(zip_url, cache_name=f"zip/{zname}")
        lst = f.get(STATION_LIST_PAGE, cache_name="pages/tide-predictions-list-view.html", refresh=True)
        lv = f.get(STANDARD_PORT_LEVELS_PAGE, cache_name="pages/standard-port-tidal-levels.html", refresh=True)
        year = datetime.now(timezone.utc).year
        port_files = []
        for url in port_csv_urls(lst.bytes().decode("utf-8", "replace"), year):
            name = urllib.parse.unquote(url.rsplit("/", 1)[1])
            quoted = urllib.parse.quote(url, safe=":/")
            port_files.append(f.get(quoted, cache_name=f"ports/{name}").input_file())
        return load_fetched(cache_dir, zgot.input_file(), port_files,
                            page_files=(page.input_file(), lst.input_file()), levels_file=lv.input_file(), only=only)

    # -- contract -------------------------------------------------------------------------------

    def convention(self) -> dict:
        if self.phase_reference == "greenwich_utc":   # the deliberate mislabel (test flag)
            return {"convention_id": "linz-mislabelled-greenwich", "phase_reference": "greenwich_utc",
                    "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                    "constituent_table_version": "tcd"}
        return {"convention_id": "linz-nzst-utc+12", "phase_reference": "local",
                "utc_offset_hours": UTC_OFFSET_HOURS, "v0_model": "schureman_tcd",
                "nodal_handling": "f_u_at_prediction", "constituent_table_version": "tcd"}

    def licence(self) -> dict:
        return {"licence_id": "linz-cc-by-4.0", "spdx": "CC-BY-4.0",
                "provider": "Toitū Te Whenua Land Information New Zealand",
                # LINZ's wording for adapted work (linz.govt.nz/copyright), then what was changed
                "attribution": "This work is based on Toitū Te Whenua Land Information New Zealand data which are "
                               "licensed by Toitū Te Whenua Land Information New Zealand for re-use under the "
                               "Creative Commons Attribution 4.0 International licence. Adapted by "
                               "OpenTideConstants: amplitudes converted from cm to metres, phase lags converted "
                               "from NZST (UTC+12) to Greenwich, constituent names given as the TCD's.",
                "url": "https://www.linz.govt.nz/copyright",
                # CC BY 4.0 allows commercial use with attribution
                "commercial_use": True}

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        return {sid: it["watch_sha256"] for sid, it in fetched.items.items()}

    def records(self, fetched: FetchResult):
        return ()

    @property
    def speeds(self) -> Speeds:
        if self._speeds is None:
            self._speeds = Speeds.from_tcd_dump()
        return self._speeds

    def position(self, hf: HarmFile, port: Port | None) -> tuple[float, float, dict]:
        """(lat, lon, provenance.position) for a site under the header/station-list rule."""
        prov: dict = {"header_lat": hf.header_lat, "header_lon": hf.header_lon}
        if port is not None:
            prov.update(list_port=port.number, list_lat=port.lat, list_lon=port.lon)
        if not self.position_check:
            return hf.header_lat, hf.header_lon, {**prov, "from": "header", "checked": False}
        reasons = []
        if not in_nz_box(hf.header_lat, hf.header_lon):
            reasons.append(f"header position ({hf.header_lat}, {hf.header_lon}) is outside the NZ box")
        elif port is not None:
            d = distance_km(hf.header_lat, hf.header_lon, port.lat, port.lon)
            prov["list_distance_km"] = round(d, 3)
            if d > MAX_LIST_KM:
                reasons.append(f"header position is {d:.1f} km from LINZ port {port.number:03d} "
                               f"on the station list (> {MAX_LIST_KM:g} km)")
        if not reasons:
            return hf.header_lat, hf.header_lon, {**prov, "from": "header"}
        if port is None:
            raise ValueError(f"{hf.site_id}: {reasons[0]}, and the port is not on the LINZ station "
                             "list to replace it")
        return port.lat, port.lon, {**prov, "from": "station_list", "replaced_because": "; ".join(reasons)}

    def sets(self, fetched: FetchResult) -> Iterable[SourceSet]:
        conv = self.convention()
        for sid, it in sorted(fetched.items.items()):
            hf = parse_harm(it["text"], it["member"])
            if hf.time_zone != TIME_ZONE_MERIDIAN:
                raise ValueError(f"{sid}: TIME ZONE = {hf.time_zone}; the convention covers only "
                                 f"{TIME_ZONE_MERIDIAN}")
            port = Port(**it["port"]) if it.get("port") else None
            lat, lon, pos = self.position(hf, port)
            cons, z0 = [], None
            for r in hf.rows:
                if r.name == "Z0":
                    z0 = r.amp_cm / 100.0
                    continue
                name = self.speeds.name(r.name)
                speed = self.speeds.speed(name)
                if abs(r.freq_cph * 360.0 - speed) > SPEED_TOL_DEG_PER_HOUR:
                    raise ValueError(f"{sid}: {r.name} frequency {r.freq_cph} cph is {r.freq_cph * 360:.7f} deg/h, "
                                     f"TCD {name} is {speed:.7f}")
                if name == "SA":   # IOS's h - p1, which LINZ prints as .00011407 cph (the TCD's h: .00011408)
                    ios = round((speed - P1_RATE_DEG_PER_HOUR) / 360.0, 8)
                    if round(r.freq_cph, 8) != ios:
                        raise ValueError(f"{sid}: SA frequency {r.freq_cph} cph is not IOS's h - p1 ({ios}): "
                                         "no phase conversion is known for this definition")
                g = round(to_tcd_phase(name, to_greenwich(r.phase_deg, speed, conv)), 3) % 360.0
                c = {"name": name, "speed_deg_per_hour": speed, "amplitude_m": round(r.amp_cm / 100.0, 6),
                     "phase_deg": g}
                if name != r.name:
                    c["source_name"] = r.name
                cons.append(c)
            start = min((r.start_my for r in hf.rows), key=_span_key)
            end = max((r.end_my for r in hf.rows), key=_span_key)
            inputs = [it["zip_sha256"], it["member_sha256"]] + ([it["port_sha256"]] if pos["from"] == "station_list" else [])
            cs = {"source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                  "source_record_id": sid, "source_version": fetched.source_version,
                  "record_span": {"start": _my_to_iso(start, False), "end": _my_to_iso(end, True)},
                  "convention_id": conv["convention_id"], "licence_id": self.licence()["licence_id"],
                  "qc_status": "ok", "constituents": cons,
                  "provenance": {"adapter_version": self.adapter_version, "input_sha256": inputs,
                                 "member": it["member"], "position": pos,
                                 "header_dropped": "station number, statistics (untrusted)"}}
            if it.get("levels"):
                datum, why = levels_datum(it["levels"], z0)
                if why:   # provenance is a closed list: logged, not published
                    log.warning("linz %s: %s", sid, why)
                    datum = {"msl_offset_m": round(z0, 6)} if z0 is not None else None
                cs["datum"] = datum
                if datum is None:
                    del cs["datum"]
                else:
                    inputs.append(it["levels"]["sha256"])
            elif z0 is not None:
                cs["datum"] = {"msl_offset_m": round(z0, 6)}
            name = port.name if port is not None else hf.header_name.title()
            tz = "Pacific/Chatham" if lon < 0 else "Pacific/Auckland"
            yield SourceSet(
                station_hint={"name": name, "country": "NZL", "lat": round(lat, 6), "lon": round(lon, 6),
                              "type": "reference", "timezone": tz, "aliases": {"linz": sid}},
                constant_set=cs)


# --- fetch assembly (no network) ------------------------------------------------------------------

def load_fetched(cache_dir: Path, zip_file: InputFile, port_files: Iterable[InputFile], *,
                 page_files: Iterable[InputFile] = (), levels_file: InputFile | None = None,
                 only: list[str] | None = None) -> FetchResult:
    """Unpack the cached zip into `members/`, read the port CSV headers and (when given) the
    standard-port levels page, and build the FetchResult. Every cached file is checked against
    its SHA-256 first. A standard port on the levels page with no port CSV or no zip member is an
    error: its levels would be lost silently."""
    cache_dir = Path(cache_dir)
    files = [zip_file, *port_files, *page_files, *([levels_file] if levels_file else [])]
    for fl in files:
        if sha256_file(cache_dir / fl.path) != fl.sha256:
            raise ValueError(f"{fl.path}: bytes differ from the fetched SHA-256")
    ports: dict[int, tuple[Port, str]] = {}
    for pf in port_files:
        port = parse_port_csv_header((cache_dir / pf.path).read_bytes())
        if port.number in ports and ports[port.number][0] != port:
            raise ValueError(f"port {port.number:03d} has two different headers on the station list")
        ports[port.number] = (port, pf.sha256)
    std = None
    if levels_file is not None:
        std = parse_standard_port_levels((cache_dir / levels_file.path).read_bytes().decode("utf-8"))
        by_name = {p.name: n for n, (p, _sha) in ports.items()}
        missing = sorted(set(std["ports"]) - set(by_name))
        if missing:
            raise ValueError(f"standard ports {missing} have no prediction CSV on the station list")
        std_numbers = {by_name[name]: name for name in std["ports"]}
    zbytes = (cache_dir / zip_file.path).read_bytes()
    keep = set(only) if only is not None else None
    items, members = {}, []
    seen_numbers: set[int] = set()
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        names = sorted(n for n in z.namelist() if not n.endswith("/"))
        bad = [n for n in names if not MEMBER_RE.match(Path(n).name)]
        if bad:
            raise ValueError(f"{zip_file.path}: unexpected members {bad}")
        for n in names:
            sid = site_id(n)
            if sid in items:
                raise ValueError(f"{zip_file.path}: two members for site {sid}")
            if keep is not None and sid not in keep:
                continue
            data = z.read(n)
            rel = f"members/{zip_file.sha256[:12]}/{Path(n).name}"
            out = cache_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            msha = hashlib.sha256(data).hexdigest()
            members.append(InputFile(rel, msha, len(data), f"{zip_file.source_url}#{Path(n).name}",
                                     zip_file.fetched_at))
            text = data.decode("latin-1")
            number = int(MEMBER_RE.match(Path(n).name).group(1))
            port = ports.get(number)
            items[sid] = {"member": Path(n).name, "text": text, "member_sha256": msha,
                          "zip_sha256": zip_file.sha256, "watch_sha256": normalised_sha256(text),
                          "port": asdict(port[0]) if port else None, "port_sha256": port[1] if port else None}
            if std is not None and number in std_numbers:
                row = std["ports"][std_numbers[number]]
                items[sid]["levels"] = {"row": row, "spring_neap": std["spring_neap"], "hat_lat": std["hat_lat"],
                                        "sha256": levels_file.sha256}
                # the watch key covers the published levels and their periods too (PP §4.2)
                items[sid]["watch_sha256"] = hashlib.sha256(json.dumps(
                    [items[sid]["watch_sha256"], row, std["spring_neap"], std["hat_lat"]],
                    sort_keys=True).encode()).hexdigest()
            seen_numbers.add(number)
    if keep is not None and keep - set(items):
        raise KeyError(f"no zip member for {sorted(keep - set(items))}")
    if std is not None and keep is None and set(std_numbers) - seen_numbers:
        raise ValueError(f"standard ports {sorted(std_numbers[n] for n in set(std_numbers) - seen_numbers)} "
                         "have no zip member")
    m = ZIP_YEAR_RE.search(zip_file.path)
    version = m.group(1) if m else zip_file.fetched_at[:10].replace("-", "")
    return FetchResult(source="linz", source_version=version, cache_dir=cache_dir,
                       files=tuple(files + members), items=items)


def manifest(fetched: FetchResult) -> dict:
    """MANIFEST.json for the work-bucket copy (manifest §0.3)."""
    return fetched.manifest(dataset="LINZ NZ tide constituents", source_version=fetched.source_version,
                            licence="CC-BY-4.0",
                            citation="Toitū Te Whenua Land Information New Zealand, NZ tide constituents",
                            archive=next(f.path for f in fetched.files if f.path.startswith("zip/")))


def stash(fetched: FetchResult, cli: Path = OTC_WORK_CLI) -> str:
    """Upload every fetched file and MANIFEST.json to cache/linz/<zip-sha256>/ in the work bucket."""
    zsha = next(f.sha256 for f in fetched.files if f.path.startswith("zip/"))
    prefix = f"cache/linz/{zsha}/"
    for f in fetched.files:
        subprocess.run([str(cli), "put", str(Path(fetched.cache_dir) / f.path), prefix + f.path], check=True,
                       stdout=subprocess.DEVNULL)
    mpath = Path(fetched.cache_dir) / "MANIFEST.json"
    mpath.write_text(json.dumps(manifest(fetched), indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    subprocess.run([str(cli), "put", str(mpath), prefix + "MANIFEST.json"], check=True, stdout=subprocess.DEVNULL)
    return prefix


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m otc_pipeline.adapters.linz")
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--only", nargs="+", metavar="SITE")
    ap.add_argument("--stash", action="store_true", help="upload to cache/linz/<zip-sha256>/ in the work bucket")
    ap.add_argument("--sets", type=Path, help="write the sets as JSON lines here")
    a = ap.parse_args(argv)
    ad = LinzAdapter()
    fetched = ad.fetch(a.cache, only=a.only)
    sets = list(ad.sets(fetched))
    print(f"linz {fetched.source_version}: {len(fetched.files)} file(s), {len(sets)} set(s)")
    if a.sets:
        with a.sets.open("w") as fh:
            for s in sets:
                fh.write(json.dumps({"station_hint": s.station_hint, "constant_set": s.constant_set},
                                    sort_keys=True, ensure_ascii=False) + "\n")
    if a.stash:
        print("stashed to", stash(fetched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
