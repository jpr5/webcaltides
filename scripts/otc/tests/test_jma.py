"""JMA adapter (A-JMA-a): parse, cm -> m, local-meridian phases -> Greenwich, ownership, contract.

The fixtures in fixtures/jma/ are a real fetch cache (bodies plus the fetcher's .meta.json) from
`JmaAdapter(year=2026).fetch(...)` on 2026-10-08: the 2026 tide-table station list and the
60-constituent pages of Tokyo (TK) and Osaka (OS). Yokohama (QS) is a third-party station of the
same list (no 60-constituent page). tcd_speeds.json holds the TCD speeds of the mapped names. The
tests run offline; expected values are read from the raw HTML here, independently of the adapter.

The real-surface proofs (OTC_LIVE=1) run the live contract; with OTC_JMA_FITS=<dir of gesla-fit
documents of the Japanese GESLA records> the canary passes with the local-meridian reference and
fails with the phases read as Greenwich or as JST; with OTC_JMA_PRED=1 our constants reproduce
JMA's own hourly predictions.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
from pathlib import Path

import pytest

from otc_pipeline.adapter import Adapter, assemble_release, assert_contract, check_contract
from otc_pipeline.adapters.jma import (
    JmaAdapter,
    JmaParseError,
    Speeds,
    parse_harms60,
    parse_predictions,
    parse_station_table,
    species,
    to_greenwich_local_meridian,
)
from otc_pipeline.release_writer import validate_release
from otc_pipeline.sources import SOURCES, registry_errors

from contract_stubs import shape_only_astro_tables

FIXTURES = Path(__file__).parent / "fixtures" / "jma"
STATIONS = ("TK", "OS", "QS")
LIVE = os.environ.get("OTC_LIVE") == "1"
FITS = os.environ.get("OTC_JMA_FITS")


@pytest.fixture
def no_network(monkeypatch):
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("the JMA fixture test made a network request")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)


@pytest.fixture
def speeds():
    return Speeds(json.loads((FIXTURES / "tcd_speeds.json").read_text())["speeds"])


@pytest.fixture
def cache(tmp_path, no_network):
    d = tmp_path / "jma"
    shutil.copytree(FIXTURES, d, ignore=shutil.ignore_patterns("tcd_speeds.json"))
    return d


def adapter(speeds, phase_reference="local_meridian"):
    return JmaAdapter(phase_reference, year=2026, speeds=speeds)


def sets_by_code(a, cache_dir) -> dict:
    fetched = a.fetch(cache_dir, only=list(STATIONS))
    return {s.constant_set["source_record_id"]: s for s in a.sets(fetched)}


def raw60(code: str) -> tuple[float, dict[str, tuple[float, float, float]]]:
    """(longitude of the phase reference, symbol -> (speed, amplitude cm, phase)) from the raw page."""
    t = (FIXTURES / "harms60" / "2026" / f"{code}.html").read_text()
    d, m, s = re.search(r"(\d+)度\s*(\d+)分\s*(\d+)秒", t).groups()
    rows = {}
    for th, sp, a, g in re.findall(r'<tr><th align="left">(.*?)</th><th>\s*([\d.]+)</th>'
                                   r'<td[^>]*>\s*([\d.]+)</td><td[^>]*>\s*([\d.]+)</td>', t):
        sym = re.sub(r"<sub>|</sub>", "", html.unescape(th.split(" (")[0])).strip()
        rows[sym] = (float(sp), float(a), float(g))
    return int(d) + int(m) / 60 + int(s) / 3600, rows


def test_registered_and_implements_the_protocol():
    assert SOURCES["jma"] is JmaAdapter
    assert isinstance(JmaAdapter(), Adapter)
    assert not [e for e in registry_errors() if "jma" in e]


def test_fixture_contract_passes(cache, speeds):
    a = adapter(speeds)
    fetched = a.fetch(cache, only=list(STATIONS))
    assert_contract(a, fetched)
    assert set(fetched.items) == set(STATIONS)


def test_release_around_the_sets_validates(cache, speeds):
    a = adapter(speeds)
    fetched = a.fetch(cache, only=list(STATIONS))
    sets = list(a.sets(fetched))
    # the astro tables are the build's (KP0b); a shape-only test table makes the shell whole
    release = assemble_release(a, sets, fetched, astro_tables=shape_only_astro_tables(sets))
    assert validate_release(release) == []
    assert release["licences"][0]["commercial_use"] is True
    assert {cs["qc_status"] for st in release["stations"] for cs in st["constant_sets"]} == {"ok"}


def test_only_jma_owned_sets_are_release_output(cache, speeds):
    a = adapter(speeds)
    fetched = a.fetch(cache, only=list(STATIONS))
    assert sorted(s.constant_set["source_record_id"] for s in a.sets(fetched)) == ["OS", "TK"]
    every = {s.constant_set["source_record_id"]: s.constant_set["provenance"] for s in a.comparator_sets(fetched)}
    assert sorted(every) == ["OS", "QS", "TK"]
    assert (every["QS"]["owner"], every["QS"]["redistribution"]) == ("third_party", "comparator_only")
    # a comparator_only set in a release shell breaks the 1.0 schema (owner "jma", redistribution "redistribute")
    class Everything(JmaAdapter):
        def sets(self, fetched):
            return self.comparator_sets(fetched)
    b = Everything(year=2026, speeds=speeds)
    errors = check_contract(b, b.fetch(cache, only=list(STATIONS)))
    assert any("provenance/owner: 'jma' was expected" in e for e in errors), errors


def test_a_set_without_the_required_fields_fails_the_contract(cache, speeds):
    """RED of the contract: a stub that drops convention_id/provenance breaks it."""
    class Stub(JmaAdapter):
        def sets(self, fetched):
            for s in super().sets(fetched):
                s.constant_set.pop("convention_id")
                s.constant_set.pop("provenance")
                yield s
    a = Stub(year=2026, speeds=speeds)
    errors = check_contract(a, a.fetch(cache, only=list(STATIONS)))
    assert any("provenance.adapter_version" in e for e in errors), errors
    assert any("convention_id" in e for e in errors), errors


def test_amplitudes_are_metres_and_phases_greenwich_from_the_local_meridian(cache, speeds):
    sets = sets_by_code(adapter(speeds), cache)
    for code in ("TK", "OS"):
        lon, served = raw60(code)
        cons = {c.get("source_name", c["name"]): c for c in sets[code].constant_set["constituents"]}
        assert len(served) == 60 and len(cons) == 59 and "OQ2" not in cons
        for sym, (sp, amp, kappa) in served.items():
            if sym == "OQ2":
                continue
            c = cons[sym]       # the JMA symbol is the name, or kept as source_name when it differs
            assert c["amplitude_m"] == pytest.approx(amp / 100.0, abs=1e-9)
            assert c["speed_deg_per_hour"] == pytest.approx(sp, abs=2e-7)
            p = round(sp / 15.0)
            assert c["phase_deg"] == pytest.approx((kappa - p * lon) % 360.0, abs=1e-3)
            assert 0.0 <= c["phase_deg"] < 360.0


def test_tokyo_m2_k1_values(cache, speeds):
    cons = {c["name"]: c for c in sets_by_code(adapter(speeds), cache)["TK"].constant_set["constituents"]}
    lon = 139 + 46 / 60 + 12 / 3600
    assert cons["M2"]["amplitude_m"] == 0.4773
    assert cons["M2"]["phase_deg"] == pytest.approx((153.73 - 2 * lon) % 360, abs=1e-3)
    assert cons["K1"]["phase_deg"] == pytest.approx((178.91 - lon) % 360, abs=1e-3)
    assert cons["SA"]["phase_deg"] == 156.85                       # long period: p = 0
    assert cons["M1C"]["source_name"] == "M1" and cons["SIG1"]["source_name"] == "σ1"


def test_test_flags_read_phases_as_greenwich_or_jst(cache, speeds):
    g = {c["name"]: c for c in sets_by_code(adapter(speeds, "greenwich_utc"), cache)["TK"].constant_set["constituents"]}
    assert g["M2"]["phase_deg"] == 153.73
    j = {c["name"]: c for c in sets_by_code(adapter(speeds, "jst"), cache)["TK"].constant_set["constituents"]}
    assert j["M2"]["phase_deg"] == pytest.approx((153.73 - 28.9841042 * 9) % 360, abs=1e-3)


def test_ownership_and_redistribution(cache, speeds):
    a = adapter(speeds)
    sets = {s.constant_set["source_record_id"]: s for s in a.comparator_sets(a.fetch(cache, only=list(STATIONS)))}
    tk, qs = sets["TK"].constant_set, sets["QS"].constant_set
    assert (tk["provenance"]["owner"], tk["provenance"]["redistribution"]) == ("jma", "redistribute")
    assert (qs["provenance"]["owner"], qs["provenance"]["redistribution"]) == ("third_party", "comparator_only")
    assert [c["name"] for c in qs["constituents"]] == ["M2", "S2", "K1", "O1"]
    assert tk["provenance"]["analysis_years"] == {"long_period": "2011-20", "short_period": "2011-20"}
    assert tk["provenance"]["source_constituents_not_published"][0]["source_name"] == "OQ2"


def test_owner_counts_of_the_2026_table():
    st = parse_station_table((FIXTURES / "station2026.html").read_bytes())
    owners = [JmaAdapter.owner({"station": s}) for s in st.values()]
    assert len(st) == 239
    assert (owners.count("jma"), owners.count("jcg_or_gsi"), owners.count("third_party")) == (70, 20, 149)


def test_station_hint_and_datum(cache, speeds):
    s = sets_by_code(adapter(speeds), cache)["TK"]
    assert s.station_hint == {"name": "東京", "country": "JPN", "lat": round(35 + 39 / 60, 6),
                              "lon": round(139 + 46 / 60, 6), "type": "reference", "timezone": "Asia/Tokyo",
                              "aliases": {}}
    # MSL 120.0 cm above the tide-table datum (DT5d): the tide-table datum is the zero and the chart
    # datum; no epoch on MSL (JMA states 2020-2024 only "as a rule"); no `tp` (module docstring).
    assert s.constant_set["datum"] == {
        "msl_offset_m": 1.2, "zero": "chart_datum", "zero_name": "潮位表基準面", "chart_datum": "tide_table_datum",
        "named": {"tide_table_datum": 0.0, "msl": 1.2},
        "basis": {"tide_table_datum": {"kind": "published", "method": "source", "source_name": "潮位表基準面"},
                  "msl": {"kind": "published", "method": "source", "source_name": "MSL"}}}


def test_provisional_msl_is_flagged(cache, speeds):
    from dataclasses import replace
    a = adapter(speeds)
    fetched = a.fetch(cache, only=list(STATIONS))
    raw = parse_station_table((cache / "station2026.html").read_bytes())
    assert raw["OF"]["note"] == "＊１" and raw["TK"]["note"] == ""       # Ofunato is marked ＊１ on the page
    items = dict(fetched.items)
    items["TK"] = {**items["TK"], "station": {**items["TK"]["station"], "note": raw["OF"]["note"]}}
    tk = {s.constant_set["source_record_id"]: s for s in a.sets(replace(fetched, items=items))}["TK"]
    assert tk.constant_set["datum"]["basis"]["msl"]["flags"] == ["provisional"]


def test_a_footnote_that_no_longer_says_provisional_is_refused():
    data = (FIXTURES / "station2026.html").read_text(encoding="utf-8")
    assert "暫定値" in data
    with pytest.raises(JmaParseError, match="provisional"):
        parse_station_table(data.replace("暫定値", "値").encode("utf-8"))


def test_local_meridian_rule():
    assert species(28.9841042) == 2 and species(15.0410686) == 1 and species(0.0410686) == 0
    assert species(86.9523127) == 6
    assert to_greenwich_local_meridian(10.0, 28.9841042, 140.0) == pytest.approx(90.0)


def test_manifest_is_written(cache, speeds):
    adapter(speeds).fetch(cache, only=list(STATIONS))
    m = json.loads((cache / "MANIFEST.json").read_text())
    assert m["licence"] == "LicenseRef-JMA-PDL-1.0" and m["archive"] == "cache/jma/"
    assert {f["path"] for f in m["files"]} == {"station2026.html", "harms60/2026/TK.html", "harms60/2026/OS.html"}


def test_watch_key_changes_with_the_constants(cache, speeds):
    a = adapter(speeds)
    fetched = a.fetch(cache, only=list(STATIONS))
    keys = a.watch_keys(fetched)
    assert len(set(keys.values())) == 3
    fetched.items["TK"]["harms60"]["constituents"][0]["amplitude_cm"] += 0.01
    after = a.watch_keys(fetched)
    assert after["TK"] != keys["TK"] and after["OS"] == keys["OS"]


@pytest.mark.parametrize("old,new,needle", [
    ("遅角は現地子午線を基準にしています", "遅角はグリニッジを基準にしています", "現地子午線"),
    ("振幅（cm）", "振幅（m）", "header"),
    ("139度 46分 12秒", "", "経度"),
])
def test_harms60_refuses_an_unexpected_page(old, new, needle):
    data = (FIXTURES / "harms60" / "2026" / "TK.html").read_text().replace(old, new).encode()
    with pytest.raises(JmaParseError, match=needle):
        parse_harms60(data, "TK")


@pytest.mark.parametrize("old,new,needle", [
    ('abbr="M2の振幅"', 'abbr="M2の振幅(m)"', "header"),
    ("振幅<br />（cm）", "振幅<br />（m）", "units"),
])
def test_station_table_refuses_a_changed_layout(old, new, needle):
    data = (FIXTURES / "station2026.html").read_text().replace(old, new).encode()
    with pytest.raises(JmaParseError, match=needle):
        parse_station_table(data)


def test_table_and_harms60_must_agree(cache, speeds):
    p = cache / "harms60" / "2026" / "TK.html"
    p.write_text(p.read_text().replace(" 47.73</td>", " 47.74</td>"))
    meta = json.loads((cache / "harms60" / "2026" / "TK.html.meta.json").read_text())
    import hashlib
    meta["sha256"], meta["size"] = hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size
    (cache / "harms60" / "2026" / "TK.html.meta.json").write_text(json.dumps(meta))
    with pytest.raises(JmaParseError, match="M2 differs"):
        adapter(speeds).fetch(cache, only=["TK"])


def test_a_speed_that_is_not_the_tcd_speed_is_refused(cache, speeds):
    speeds.speeds["M2"] += 1e-3
    with pytest.raises(JmaParseError, match="M2"):
        list(adapter(speeds).sets(adapter(speeds).fetch(cache, only=["TK"])))


def test_unknown_station_and_bad_phase_reference_are_refused(cache, speeds):
    with pytest.raises(KeyError):
        adapter(speeds).fetch(cache, only=["XX"])
    with pytest.raises(ValueError):
        JmaAdapter("utc+9")


def test_parse_predictions_line():
    # first line of JMA's 2026 Tokyo file (txt/2026/TK.txt)
    line = (" 87119145162169166154140128123128140155168175172157130 94 55 22  2  0 1726 1 1TK"
            " 4 8169141517699999999999999 9 11232135 -299999999999999")
    rows = parse_predictions(line.encode(), "TK")
    assert len(rows) == 24
    assert rows[0][0].isoformat() == "2025-12-31T15:00:00+00:00" and rows[0][1] == 0.87
    assert rows[23][1] == 0.17
    with pytest.raises(JmaParseError):
        parse_predictions(line.encode(), "OS")


# --- real surface -----------------------------------------------------------------------------------

@pytest.mark.skipif(not LIVE, reason="set OTC_LIVE=1")
def test_contract_live(tmp_path):
    a = JmaAdapter()
    assert check_contract(a, a.fetch(tmp_path, only=list(STATIONS))) == []


@pytest.fixture(scope="module")
def live_cache(tmp_path_factory):
    """One live fetch of every station for the three canary runs (the later runs read the cache)."""
    return tmp_path_factory.mktemp("jma-live")


@pytest.mark.skipif(not (LIVE and FITS), reason="set OTC_LIVE=1 and OTC_JMA_FITS=<gesla-fit documents dir>")
@pytest.mark.parametrize("phase_reference,passes", [("local_meridian", True), ("jst", False),
                                                    ("greenwich_utc", False)])
def test_jma_canary_against_gesla_fits_live(live_cache, phase_reference, passes):
    from jma_canary import run
    _, res = run(Path(FITS), phase_reference, (), live_cache)
    print(f"\nA-JMA-a canary phase_reference={phase_reference}: median |dA| {res.median_da_m * 100:.3f} cm, "
          f"median |dg| {res.median_dg_deg:.3f} deg over {res.n_pairs} pairs / {res.n_gauges} gauges")
    assert res.n_gauges >= 3 and res.passed is passes, res.reasons
