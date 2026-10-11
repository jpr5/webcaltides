"""LINZ adapter (manifest A-LINZ) on saved real responses: members of the 2025 zip
(NZ_Tide_Constituents_2025.zip, sha256 0843afa0…9406), the tide-constituents page, the
tide-predictions list view (its table) and six ports' prediction CSVs, all in fixtures/linz/.
`tcd_speeds.json` holds the TCD speeds of the names the zip uses (from the TCD dump).
`pages/standard-port-tidal-levels.synthetic.html` is a SYNTHETIC page in the layout of LINZ's standard
port tidal levels page (invented numbers, no fetched data in git) for the DT5c datum tests.

The live test (OTC_LIVE=1) fetches the zip, the station list and every port CSV and runs the
contract on all 56 sets.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from otc_pipeline.adapter import InputFile, assemble_release, assert_contract, check_contract
from otc_pipeline.adapters import linz
from otc_pipeline.adapters.linz import (LinzAdapter, Port, Speeds, ddmm, find_zip_url, in_nz_box, load_fetched,
                                        normalised_sha256, parse_harm, parse_port_csv_header, port_csv_urls, site_id)
from otc_pipeline.ios_definitions import P1_EPOCH, p1_deg
from otc_pipeline.release_writer import validate_release

from contract_stubs import shape_only_astro_tables

FIX = Path(__file__).parent / "fixtures" / "linz"
LIVE = os.environ.get("OTC_LIVE") == "1"
ZIP_NAME = "NZ_Tide_Constituents_2025.zip"
MEMBERS = sorted(p.name for p in (FIX / "members").iterdir())


def speeds() -> Speeds:
    d = json.loads((FIX / "tcd_speeds.json").read_text())
    return Speeds(d["speeds"], d["aliases"], d["tcd_sha256"])


def _input(cache: Path, rel: str, url: str) -> InputFile:
    data = (cache / rel).read_bytes()
    return InputFile(rel, hashlib.sha256(data).hexdigest(), len(data), url, "2026-10-08T00:00:00Z")


LEVELS_PAGE = "pages/standard-port-tidal-levels.synthetic.html"


def fixture_fetch(tmp_path: Path, members=MEMBERS, mutate=None, only=None, levels: str | None = None):
    """A FetchResult over a zip of the fixture members (optionally rewritten by `mutate`)."""
    cache = tmp_path / "cache"
    (cache / "zip").mkdir(parents=True)
    with zipfile.ZipFile(cache / "zip" / ZIP_NAME, "w") as z:
        for m in members:
            data = (FIX / "members" / m).read_bytes()
            z.writestr(m, mutate(m, data) if mutate else data)
    (cache / "ports").mkdir()
    ports = []
    for p in sorted((FIX / "ports").iterdir()):
        (cache / "ports" / p.name).write_bytes(p.read_bytes())
        ports.append(_input(cache, f"ports/{p.name}", f"https://static.charts.linz.govt.nz/x/{p.name}"))
    zf = _input(cache, f"zip/{ZIP_NAME}", f"https://www.linz.govt.nz/sites/default/files/2026-08/{ZIP_NAME}")
    lv = None
    if levels is not None:
        (cache / "pages").mkdir()
        (cache / "pages" / "levels.html").write_text(levels, encoding="utf-8")
        lv = _input(cache, "pages/levels.html", linz.STANDARD_PORT_LEVELS_PAGE)
    return load_fetched(cache, zf, ports, levels_file=lv, only=only)


def sets_by_id(adapter, fetched):
    return {s.constant_set["source_record_id"]: s for s in adapter.sets(fetched)}


def cons(s):
    return {c["name"]: (c["amplitude_m"], c["phase_deg"]) for c in s.constant_set["constituents"]}


# --- scraping ---------------------------------------------------------------------------------------

def test_zip_link_is_scraped_not_pinned():
    page = (FIX / "pages" / "tide-constituents.html").read_text()
    assert find_zip_url(page) == ("https://www.linz.govt.nz/sites/default/files/2026-08/"
                                  "NZ_Tide_Constituents_2025.zip")
    moved = page.replace("/2026-08/NZ_Tide_Constituents_2025.zip", "/2027-09/NZ_Tide_Constituents_2026.zip")
    assert find_zip_url(moved).endswith("/2027-09/NZ_Tide_Constituents_2026.zip")


@pytest.mark.parametrize("page", ["<p>no link</p>",
                                  '<a href="/a/NZ_Tide_Constituents_2025.zip"></a><a href="/b/NZ_Tide_Constituents_2026.zip"></a>'])
def test_zip_link_must_be_unique(page):
    with pytest.raises(ValueError, match="expected one"):
        find_zip_url(page)


def test_station_list_gives_one_csv_per_daily_port():
    urls = port_csv_urls((FIX / "pages" / "tide-predictions-list-view.table.html").read_text(), 2026)
    assert "https://static.charts.linz.govt.nz/tide-tables/maj-ports/csv/Nelson 2026.csv" in urls
    assert not any("Nelson 2027" in u for u in urls)
    assert len(urls) > 80 and all(u.endswith(".csv") for u in urls)
    assert all("2025" in u for u in port_csv_urls(
        (FIX / "pages" / "tide-predictions-list-view.table.html").read_text(), 1999))  # earliest listed year


def test_port_csv_header():
    p = parse_port_csv_header((FIX / "ports" / "Nelson 2026.csv").read_bytes())
    assert p == Port(77, "Nelson", -41.266667, 173.266667)
    # a real header with the minute mark missing (468 New Brighton Pier, 2026)
    q = parse_port_csv_header("468,New Brighton Pier,43°30.4'S,172°44.1E\n".encode())
    assert (q.number, q.lat, q.lon) == (468, -43.506667, 172.735)
    with pytest.raises(ValueError):
        parse_port_csv_header(b"Nelson,41S,173E\n")


# --- members ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("member, sid", [
    ("077NELSON_Harm_2005-2025.txt", "077NELSON"),
    ("148MANAWATURIVER-Harm_2006-2019.txt", "148MANAWATURIVER"),
    ("354GREEN ISLAND_Harm_2005-2024.txt", "354GREENISLAND"),
    ("58RIVERTON_Harm_2023-2025.txt", "58RIVERTON"),
])
def test_site_id_is_the_file_name_prefix(member, sid):
    assert site_id(member) == sid


def test_site_id_rejects_other_names():
    with pytest.raises(ValueError):
        site_id("README.txt")


def test_parse_auckland():
    hf = parse_harm((FIX / "members" / "070AUCKLAND_Harm_2004-2025.txt").read_text("latin-1"), "070AUCKLAND_Harm_2004-2025.txt")
    assert (hf.site_id, hf.time_zone, hf.header_lat, round(hf.header_lon, 4)) == ("070AUCKLAND", 180, -36.85, 174.7667)
    assert len(hf.rows) == 28 and hf.rows[0].name == "Z0"
    m2 = next(r for r in hf.rows if r.name == "M2")
    assert (m2.freq_cph, m2.amp_cm, m2.phase_deg) == (0.0805114, 114.1986, 204.50)


def test_ddmm_and_box():
    assert ddmm(4116, south=True) == pytest.approx(-41.2667, abs=1e-4)
    assert ddmm(1416, south=True) == pytest.approx(-14.2667, abs=1e-4)   # Nelson's header
    assert ddmm(4175, south=True) is None
    assert in_nz_box(-41.27, 173.27) and in_nz_box(-43.95, -176.57)
    assert not in_nz_box(-14.27, 173.27) and not in_nz_box(None, 173.0)


def test_watch_key_ignores_line_ends():
    t = (FIX / "members" / "077NELSON_Harm_2005-2025.txt").read_text("latin-1")
    assert normalised_sha256(t) == normalised_sha256(t.replace("\n", "\r\n") + "\n\n")
    assert normalised_sha256(t) != normalised_sha256(t.replace("133.5880", "133.5881"))


# --- sets --------------------------------------------------------------------------------------------

def test_phases_are_converted_from_nzst_to_greenwich(tmp_path):
    fetched = fixture_fetch(tmp_path)
    auck = sets_by_id(LinzAdapter(speeds=speeds()), fetched)["070AUCKLAND"]
    c = cons(auck)
    # G = 204.50 (M2), 175.90 (K1), 130.02 (O1) at UTC+12; g = G - speed * 12 h
    assert c["M2"][1] == pytest.approx((204.50 - 28.9841042 * 12) % 360, abs=1e-3)
    assert c["K1"][1] == pytest.approx((175.90 - 15.0410686 * 12) % 360, abs=1e-3)
    assert c["O1"][1] == pytest.approx((130.02 - 13.9430356 * 12) % 360, abs=1e-3)
    # agrees with TICON-3's Greenwich phases at Auckland (M2 216.939, K1 355.427, O1 324.574) within 2 deg
    for name, g in (("M2", 216.939), ("K1", 355.427), ("O1", 324.574)):
        assert abs((c[name][1] - g + 180) % 360 - 180) < 2.0
    assert c["M2"][0] == pytest.approx(1.141986)
    assert auck.constant_set["datum"] == {"msl_offset_m": 1.94345}
    assert auck.constant_set["record_span"] == {"start": "2004-01-01T00:00:00Z", "end": "2025-12-31T23:00:00Z"}


def test_mislabel_flag_reads_phases_as_greenwich(tmp_path):
    fetched = fixture_fetch(tmp_path)
    c = cons(sets_by_id(LinzAdapter(speeds=speeds(), phase_reference="greenwich_utc"), fetched)["070AUCKLAND"])
    assert (c["M2"][1], c["K1"][1]) == (204.5, 175.9)


def test_nelson_position_comes_from_the_station_list(tmp_path):
    fetched = fixture_fetch(tmp_path)
    s = sets_by_id(LinzAdapter(speeds=speeds()), fetched)["077NELSON"]
    assert (s.station_hint["lat"], s.station_hint["lon"]) == (-41.266667, 173.266667)
    pos = s.constant_set["provenance"]["position"]
    assert pos["from"] == "station_list" and "outside the NZ box" in pos["replaced_because"]
    assert pos["header_lat"] == pytest.approx(-14.2667, abs=1e-4)
    port_sha = fetched.items["077NELSON"]["port_sha256"]
    assert port_sha in s.constant_set["provenance"]["input_sha256"]
    # without the check (test flag) the header position is emitted: about 14 deg S
    raw = sets_by_id(LinzAdapter(speeds=speeds(), position_check=False), fetched)["077NELSON"]
    assert raw.station_hint["lat"] == pytest.approx(-14.2667, abs=1e-4)


def test_far_header_position_is_replaced(tmp_path):
    # 210DARGAVILLE's header says 36 56 S; LINZ's list has Dargaville at 35 56 S (111 km away)
    s = sets_by_id(LinzAdapter(speeds=speeds()), fixture_fetch(tmp_path))["210DARGAVILLE"]
    pos = s.constant_set["provenance"]["position"]
    assert pos["from"] == "station_list" and "km from LINZ port 210" in pos["replaced_because"]
    assert s.station_hint["lat"] == pytest.approx(-35.9333, abs=1e-4)


def test_close_header_position_is_kept_and_unlisted_port_uses_header(tmp_path):
    got = sets_by_id(LinzAdapter(speeds=speeds()), fixture_fetch(tmp_path))
    auck = got["070AUCKLAND"].constant_set["provenance"]["position"]
    assert auck["from"] == "header" and auck["list_distance_km"] <= 5.0
    manawatu = got["148MANAWATURIVER"]
    pos = manawatu.constant_set["provenance"]["position"]
    assert pos["from"] == "header" and "list_port" not in pos
    assert (pos["header_lat"], pos["header_lon"]) == pytest.approx((manawatu.station_hint["lat"],
                                                                    manawatu.station_hint["lon"]), abs=1e-6)
    # the Green Island header says station 254; the file name (354) joins it to the list
    gi = got["354GREENISLAND"]
    assert gi.station_hint["name"] == "Green Island" and gi.station_hint["aliases"] == {"linz": "354GREENISLAND"}


def test_rejected_position_without_a_list_entry_fails(tmp_path):
    def bad(m, data):
        return data.replace(b"=  4028,", b"=  1028,") if m.startswith("148") else data
    with pytest.raises(ValueError, match="not on the LINZ station list"):
        list(LinzAdapter(speeds=speeds()).sets(fixture_fetch(tmp_path, mutate=bad)))


def test_unknown_time_zone_fails(tmp_path):
    def bad(m, data):
        return data.replace(b"TIME ZONE = 180", b"TIME ZONE = 0") if m.startswith("070") else data
    with pytest.raises(ValueError, match="TIME ZONE = 0"):
        list(LinzAdapter(speeds=speeds()).sets(fixture_fetch(tmp_path, mutate=bad, only=["070AUCKLAND"])))


def test_unknown_constituent_fails(tmp_path):
    def bad(m, data):
        return data.replace(b" M8   ", b" XX8  ") if m.startswith("070") else data
    with pytest.raises(KeyError, match="XX8"):
        list(LinzAdapter(speeds=speeds()).sets(fixture_fetch(tmp_path, mutate=bad, only=["070AUCKLAND"])))


def test_frequency_must_match_the_tcd_speed(tmp_path):
    def bad(m, data):
        return data.replace(b".08051140", b".08061140") if m.startswith("070") else data
    with pytest.raises(ValueError, match="frequency"):
        list(LinzAdapter(speeds=speeds()).sets(fixture_fetch(tmp_path, mutate=bad, only=["070AUCKLAND"])))


def test_only_keeps_the_named_sites(tmp_path):
    fetched = fixture_fetch(tmp_path, only=["073TAURANGA"])
    assert list(fetched.items) == ["073TAURANGA"]
    with pytest.raises(KeyError):
        fixture_fetch(tmp_path / "b", only=["999NOWHERE"])


def test_contract_and_release_validation(tmp_path):
    fetched = fixture_fetch(tmp_path)
    ad = LinzAdapter(speeds=speeds())
    assert_contract(ad, fetched)
    assert fetched.source_version == "2025"
    assert set(ad.watch_keys(fetched)) == {site_id(m) for m in MEMBERS}
    lic = ad.licence()
    assert lic["spdx"] == "CC-BY-4.0" and "Toitū Te Whenua Land Information New Zealand" in lic["attribution"]
    assert lic["commercial_use"] is True
    # the whole shell on the pinned 1.0 schema, with a shape-only astro table (the build's are KP0b's)
    sets = list(ad.sets(fetched))
    release = assemble_release(ad, sets, fetched, astro_tables=shape_only_astro_tables(sets))
    assert validate_release(release) == []
    assert {cs["qc_status"] for st in release["stations"] for cs in st["constant_sets"]} == {"ok"}


def test_contract_catches_the_mislabelled_convention_id(tmp_path):
    fetched = fixture_fetch(tmp_path, only=["070AUCKLAND"])
    ad = LinzAdapter(speeds=speeds())
    sets = list(ad.sets(fetched))
    ad.convention = lambda: {**LinzAdapter.convention(ad), "convention_id": "other"}   # noqa: E731
    ad.sets = lambda f: sets   # noqa: E731
    assert any("convention_id" in e for e in check_contract(ad, fetched))


def test_registered():
    from otc_pipeline.sources import SOURCES, registry_errors
    assert SOURCES["linz"] is LinzAdapter
    assert not [e for e in registry_errors() if "linz" in e]


@pytest.mark.skipif(not LIVE, reason="live fetch: set OTC_LIVE=1")
def test_live_all_sites(tmp_path):
    ad = LinzAdapter()
    fetched = ad.fetch(tmp_path)
    assert len(fetched.items) == 56
    assert_contract(ad, fetched)
    nelson = sets_by_id(ad, fetched)["077NELSON"]
    assert nelson.station_hint["lat"] == pytest.approx(-41.27, abs=0.05)
    assert linz.MAX_LIST_KM == 5.0


# --- published datums (datums spec §4.1 item 4, DT5c) on the synthetic levels page ---------------

def levels_page() -> str:
    return (FIX / LEVELS_PAGE).read_text(encoding="utf-8")


def test_standard_port_levels_page_is_parsed():
    got = linz.parse_standard_port_levels(levels_page())
    assert got["spring_neap"] == ("2026-07-01", "2027-06-30")
    assert got["hat_lat"] == ("2000-01-01", "2018-12-31")
    assert got["ports"]["Auckland"] == {"MHWS": 3.3, "MHWN": 2.8, "MLWN": 1.1, "MLWS": 0.5, "SpringRange": 2.8,
                                        "NeapRange": 1.7, "MSL": 1.95, "HAT": 3.7, "LAT": 0.1}


@pytest.mark.parametrize("old, new, msg", [
    ("<th>MSL</th>", "<th>Z0</th>", "header"),
    ("which is the same as the zero of the tidal predictions", "which is near the zero", "no longer says"),
    ("predicted for the period 1 July 2026", "predicted for the year 1 July 2026", "period"),
])
def test_a_changed_levels_page_is_refused(old, new, msg):
    with pytest.raises(ValueError, match=msg):
        linz.parse_standard_port_levels(levels_page().replace(old, new))


def test_standard_port_set_gets_the_levels_above_chart_datum(tmp_path):
    f = fixture_fetch(tmp_path, levels=levels_page())
    sets = sets_by_id(LinzAdapter(speeds=speeds()), f)
    d = sets["070AUCKLAND"].constant_set["datum"]
    assert (d["zero"], d["zero_name"], d["chart_datum"]) == ("chart_datum", "CD", "cd")
    assert d["msl_offset_m"] == 1.94345                       # the constants' Z0, as before
    assert d["named"] == {"cd": 0.0, "mhws": 3.3, "mhwn": 2.8, "mlwn": 1.1, "mlws": 0.5, "msl": 1.95,
                          "hat": 3.7, "lat": 0.1}
    sn = {"start": "2026-07-01", "end": "2027-06-30", "name": "LINZ spring/neap predictions"}
    hl = {"start": "2000-01-01", "end": "2018-12-31", "name": "LINZ HAT/LAT predictions"}
    for k in ("mhws", "mhwn", "mlwn", "mlws"):
        assert d["basis"][k] == {"kind": "published", "method": "source", "source_name": k.upper(), "epoch": sn}
    for k in ("hat", "lat"):
        assert d["basis"][k]["epoch"] == hl
    assert "epoch" not in d["basis"]["msl"] and d["basis"]["cd"]["source_name"] == "Chart Datum"
    assert f.items["070AUCKLAND"]["levels"]["sha256"] in sets["070AUCKLAND"].constant_set["provenance"]["input_sha256"]
    # a port that is not a standard port keeps only its Z0
    assert sets["210DARGAVILLE"].constant_set["datum"] == {"msl_offset_m": 2.444751}


def test_a_port_whose_z0_is_far_from_msl_gets_no_levels(tmp_path, caplog):
    s = sets_by_id(LinzAdapter(speeds=speeds()), fixture_fetch(tmp_path, levels=levels_page()))["097NAPIER"]
    assert s.constant_set["datum"] == {"msl_offset_m": 1.010259}
    assert "097NAPIER: standard-port levels not written: Z0 1.010259 m vs the page's MSL 1.5 m" in caplog.text


def test_a_standard_port_with_no_port_csv_is_an_error(tmp_path):
    page = levels_page().replace("<td>Tauranga</td>", "<td>Taurangaa</td>")
    with pytest.raises(ValueError, match="Taurangaa"):
        fixture_fetch(tmp_path, levels=page)


def test_linz_contract_and_build_checks_with_levels(tmp_path):
    from otc_pipeline.datums import datum_build_errors
    f = fixture_fetch(tmp_path, levels=levels_page())
    a = LinzAdapter(speeds=speeds())
    assert check_contract(a, f) == []
    for s in a.sets(f):
        assert datum_build_errors(s.constant_set.get("datum", {})) == []


def test_watch_key_follows_the_levels(tmp_path):
    a = LinzAdapter(speeds=speeds())
    k1 = a.watch_keys(fixture_fetch(tmp_path / "a", levels=levels_page()))
    k2 = a.watch_keys(fixture_fetch(tmp_path / "b", levels=levels_page().replace("<td>3.30</td>", "<td>3.31</td>")))
    assert k1["070AUCKLAND"] != k2["070AUCKLAND"] and k1["073TAURANGA"] == k2["073TAURANGA"]


# --- SA and S1: IOS's arguments converted to the TCD's -------------------------------------------------

def test_sa_and_s1_are_converted_from_ios_to_the_tcd(tmp_path):
    c = cons(sets_by_id(LinzAdapter(speeds=speeds()), fixture_fetch(tmp_path))["070AUCKLAND"])
    p1 = p1_deg(P1_EPOCH)
    # served (UTC+12): SA .00011407 cph 4.9046 cm 99.40; S1 .04166667 cph 0.4938 cm 238.77
    assert c["SA"][1] == pytest.approx((99.40 - 0.0410686 * 12 + p1) % 360, abs=1e-3)
    assert c["S1"][1] == pytest.approx((238.77 - 15.0 * 12 - p1 + 90) % 360, abs=1e-3)


def test_sa_served_at_the_tcd_frequency_is_refused(tmp_path):
    def mutate(member, data):
        return data.replace(b"   2 SA      .00011407", b"   2 SA      .00011408")
    with pytest.raises(ValueError, match="SA"):
        list(LinzAdapter(speeds=speeds()).sets(fixture_fetch(tmp_path, mutate=mutate)))


# --- F2: every constituent row is read (RELEASE-AGENCY fix round 1) -------------------------------

_HEAD = ("Rangatira Point        Units in CM\n"
         " STATION  351   TIME ZONE =  180, LATITUDE =  4050, LONGITUDE = 17457\n"
         "  NO NAME    FREQUENCY   STN  M-Y/ M-Y      A       G\n")


def test_values_below_one_and_an_amplitude_run_into_the_year_column_are_read():
    hf = parse_harm(_HEAD + "   1 Z0      .00000000   148 0106/1219134.2423    .00\n"
                    "   2 Q1      .03721850   351 0198/1206   .7750   5.34\n"
                    "  11 M2      .08051140   351 0198/1206 55.2125 280.56\n", "351RANGATIRAPOINT-Harm_1998-2006.txt")
    assert [(r.name, r.amp_cm, r.phase_deg) for r in hf.rows] == [
        ("Z0", 134.2423, 0.0), ("Q1", 0.775, 5.34), ("M2", 55.2125, 280.56)]


def test_a_constituent_row_that_cannot_be_read_stops_the_read():
    with pytest.raises(ValueError, match="cannot be read"):
        parse_harm(_HEAD + "  11 M2      .08051140   351 0198/1206 55.2125 280.56\n"
                   "  12 S2      .08333334   351 0198/1206 26,1980 336.55\n", "351RANGATIRAPOINT-Harm_1998-2006.txt")


@pytest.mark.parametrize("row", ["  12 s2      .08333334   351 0198/1206 26.1980 336.55",
                                 "  12 S(2)    .08333334   351 0198/1206 26.1980 336.55",
                                 "  12 S2      .0833333x   351 0198/1206 26.1980 336.55",
                                 "  12 S2      .08333334",
                                 "  12  M2", "4  S1", "  4", "  12  MX2  "])
def test_any_numbered_row_that_cannot_be_read_stops_the_read(row):
    with pytest.raises(ValueError, match="cannot be read"):
        parse_harm(_HEAD + "  11 M2      .08051140   351 0198/1206 55.2125 280.56\n" + row + "\n",
                   "351RANGATIRAPOINT-Harm_1998-2006.txt")
