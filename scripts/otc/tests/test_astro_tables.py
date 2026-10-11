"""KP0b: the astro tables from a congen run (otc_pipeline.astro_tables), the build's check that a table
is exactly the run's, and build check 7.

Synthetic dumps for the rules. The last tests read a real congen_tables.sh run
($OTC_CONGEN_RUN, default $OTC_WORK/astro/otc-tcd-20251228-ma2mb2) and skip without it: no TCD values are in git.
"""
import copy
import json
import os
import re
from pathlib import Path

import pytest

from gesla.read_gesla import otc_work
from gesla.tcd_table import TcdTable

from otc_pipeline import astro_tables as at
from otc_pipeline.astro_tables import (AstroTableMismatch, astro_table, astro_table_errors, load_congen_run,
                                       release_table_errors, tcd_equality_errors)
from otc_pipeline.gesla_sets import FitIntegrityError
from otc_pipeline.release_writer import BuiltRelease, validate_release, write_release

from astro_from_tcd import astro_table as standin
from synthetic_congen import congen_run, kp0b_tables, write_run
from test_format_1_0 import D, SPEEDS, build, doc

TCD = "c" * 64
RUN = "d" * 64
Y0, Y1 = 2020, 2022
REAL_SPEEDS = {"M2": 28.9841042, "K1": 15.0410686, "SA": 0.0410686, "M4": 57.9682084}


def dump(sha, edit=None, speeds=SPEEDS):
    """A 2020-2022 dump; edit maps (name, key, year index) -> value."""
    cons = {n: {"speed": s, "v0u": [359.996, 10.0, 20.25], "f": [1.0, 0.9779, 1.1]} for n, s in speeds.items()}
    for (n, k, i), v in (edit or {}).items():
        if k == "speed":
            cons[n][k] = v
        else:
            cons[n][k][i] = v
    return TcdTable({"years": [Y0, Y1], "tcd_sha256": sha, "aliases": {"EP2": "S2"}, "constituents": cons})


def table(congen=None, tcd=None, names=SPEEDS, **kw):
    return astro_table(congen or dump(RUN), tcd or dump(TCD), names, table_id="tcd-test", first_year=Y0,
                       last_year=Y1, **kw)


def ok(congen, tcd, names):
    return tcd_equality_errors(congen, tcd, names, first_year=Y0, last_year=Y1) == []


# --- the table and the TCD equality check (§3.4 rule 2) ---------------------------------------------

def test_the_table_has_the_congen_values_rounded_and_reduced():
    t = table(congen=dump(RUN, {("M2", "v0u", 2): 20.2549, ("M2", "f", 1): 0.97793}))
    assert {k: v for k, v in t.items() if k != "constituents"} == {
        "astro_table_id": "tcd-test", "v0_model": "schureman_tcd", "tables_sha256": TCD, "generator": at.GENERATOR,
        "first_year": Y0, "last_year": Y1}
    assert sorted(t["constituents"]) == sorted(SPEEDS)
    m2 = t["constituents"]["M2"]
    assert m2 == {"speed_deg_per_hour": SPEEDS["M2"], "v0u_deg": [0.0, 10.0, 20.25], "f": [1.0, 0.9779, 1.1]}
    assert "Schureman" in at.GENERATOR and "Special Publication 98" in at.GENERATOR and "congen" in at.GENERATOR


def test_the_values_come_from_congen_not_from_the_tcd():
    # equal within half a step, but they round differently: the table has congen's rounding (§3.4 rule 1)
    congen = dump(RUN, {("M2", "v0u", 2): 20.254, ("M2", "f", 1): 0.97794})
    tcd = dump(TCD, {("M2", "v0u", 2): 20.256, ("M2", "f", 1): 0.97796})
    m2 = table(congen=congen, tcd=tcd)["constituents"]["M2"]
    assert (m2["v0u_deg"][2], m2["f"][1]) == (20.25, 0.9779)
    m2 = table(congen=tcd_as_run(tcd), tcd=dump(TCD, {("M2", "v0u", 2): 20.254, ("M2", "f", 1): 0.97794}))["constituents"]["M2"]
    assert (m2["v0u_deg"][2], m2["f"][1]) == (20.26, 0.978)


def tcd_as_run(t):
    data = {"years": [t.first_year, t.last_year], "tcd_sha256": RUN, "aliases": dict(t.aliases),
            "constituents": {n: {"speed": c.speed, "v0u": list(c.v0u), "f": list(c.f)} for n, c in t.constituents.items()}}
    return TcdTable(data)


def test_v0u_is_compared_modulo_360():
    # 359.996 (congen) against 0.0 (TCD): d = 359.996, min(d, 360 - d) = 0.004 < 0.005
    assert ok(dump(RUN), dump(TCD, {("M2", "v0u", 0): 0.0}), ["M2"])


@pytest.mark.parametrize("key,base,inside,outside", [
    ("v0u", 10.0, 10.0049, 10.0051),
    ("v0u", 10.0, 9.9951, 9.9949),
    ("f", 0.9779, 0.977949, 0.977951),
    ("f", 0.9779, 0.977851, 0.977849),
])
def test_the_half_step_edges(key, base, inside, outside):
    tcd = dump(TCD, {("M2", key, 1): base})
    assert ok(dump(RUN, {("M2", key, 1): inside}), tcd, ["M2"])
    assert not ok(dump(RUN, {("M2", key, 1): outside}), tcd, ["M2"])


@pytest.mark.parametrize("edit,what", [
    ({("M2", "v0u", 1): 10.01}, r"M2 2021: V0\+u 10.01 \(congen\) != 10.0 \(TCD\)"),
    ({("K1", "v0u", 0): 359.986}, r"K1 2020: V0\+u 359.986"),
    ({("O1", "f", 2): 1.1001}, r"O1 2022: f 1.1001 \(congen\) != 1.1 \(TCD\)"),
])
def test_a_congen_value_changed_by_one_step_stops_the_build(edit, what):
    with pytest.raises(AstroTableMismatch, match=what):
        table(congen=dump(RUN, edit))


@pytest.mark.parametrize("name", ["M2", "K1", "SA", "M4"])
def test_congen_and_tcd_speeds_are_equal_exactly(name):
    # one 7-decimal step apart is a mismatch for every constituent (the float distance is 9.99e-8 for
    # M2 and 1.0000001e-7 for K1, so a 1e-7 tolerance would pass one and not the other)
    tcd = dump(TCD, speeds=REAL_SPEEDS)
    assert ok(dump(RUN, speeds=REAL_SPEEDS), tcd, [name])
    for step in (1e-7, -1e-7):
        moved = dump(RUN, {(name, "speed", 0): round(REAL_SPEEDS[name] + step, 7)}, speeds=REAL_SPEEDS)
        assert tcd_equality_errors(moved, tcd, [name], first_year=Y0, last_year=Y1) == [
            f"{name}: speed {round(REAL_SPEEDS[name] + step, 7)} (congen) != {REAL_SPEEDS[name]} (TCD)"]


def test_f_within_half_a_step_is_equal():
    assert ok(dump(RUN, {("M2", "f", 1): 0.97794}), dump(TCD), ["M2"])


def test_a_constituent_missing_from_the_run_or_the_tcd_stops_the_build():
    with pytest.raises(AstroTableMismatch, match="XX9: not a constituent of the TCD"):
        table(names=[*SPEEDS, "XX9"])
    short = TcdTable({"years": [Y0, Y1], "tcd_sha256": RUN, "aliases": {},
                      "constituents": {"M2": {"speed": SPEEDS["M2"], "v0u": [0, 0, 0], "f": [1, 1, 1]}}})
    with pytest.raises(AstroTableMismatch, match="the congen run has no constituent 'S2'"):
        table(congen=short)


def test_a_year_outside_the_run_stops_the_build():
    with pytest.raises(AstroTableMismatch, match="year 2023 outside"):
        astro_table(dump(RUN), dump(TCD), SPEEDS, first_year=Y0, last_year=2023)


def test_names_are_mapped_by_the_tcd_aliases_and_keyed_as_given():
    t = table(names=["EP2"])
    assert list(t["constituents"]) == ["EP2"] and t["constituents"]["EP2"]["speed_deg_per_hour"] == SPEEDS["S2"]


def test_lam2_and_rho_are_the_tcds_lda2_and_rho1():
    extra = {"LDA2": 29.4556253, "RHO1": 13.4715145}
    def with_extra(sha, v):
        data = {"years": [Y0, Y1], "tcd_sha256": sha, "aliases": {},
                "constituents": {n: {"speed": s, "v0u": [v, 1.0, 2.0], "f": [1.0, 1.01, 1.02]} for n, s in extra.items()}}
        return TcdTable(data)
    t = astro_table(with_extra(RUN, 5.0), with_extra(TCD, 5.0), ["LAM2", "RHO"], table_id="t", first_year=Y0,
                    last_year=Y1)["constituents"]
    assert t["LAM2"]["speed_deg_per_hour"] == 29.4556253 and t["RHO"]["v0u_deg"] == [5.0, 1.0, 2.0]
    with pytest.raises(AstroTableMismatch, match=r"LAM2 2020: V0\+u 5.01"):
        astro_table(with_extra(RUN, 5.01), with_extra(TCD, 5.0), ["LAM2"], table_id="t", first_year=Y0, last_year=Y1)


def test_the_tcd_itself_is_not_a_congen_run():
    with pytest.raises(AstroTableMismatch, match="must come from a congen run"):
        table(congen=dump(TCD))


def test_no_names_is_no_table():
    with pytest.raises(AstroTableMismatch, match="no constituents"):
        table(names=[])


# --- load_congen_run --------------------------------------------------------------------------------

FIT_PIN = TcdTable({"years": [Y0, Y1], "tcd_sha256": at.TCD_SHA256, "aliases": {},
                    "constituents": {n: {"speed": s, "v0u": [0, 0, 0], "f": [1, 1, 1]} for n, s in SPEEDS.items()}})


def _write_run(d: Path, edit=None) -> Path:
    return write_run(d, FIT_PIN, edit=edit)


def test_load_congen_run_checks_the_provenance(tmp_path):
    run = load_congen_run(_write_run(tmp_path))
    congen, tcd = run
    assert isinstance(run, at.CongenRun) and tcd.tcd_sha256 == at.TCD_SHA256
    assert congen.tcd_sha256 == run.provenance["files"]["congen_tcd"]["sha256"]
    p = tmp_path / at.CONGEN_RUN_FILES["congen_dump"]
    p.write_text(p.read_text().replace("37.25", "37.26"))
    with pytest.raises(AstroTableMismatch, match="PROVENANCE says"):
        load_congen_run(tmp_path)


def test_load_congen_run_hashes_the_congen_tcd_on_disk(tmp_path):
    _write_run(tmp_path)
    with open(tmp_path / at.CONGEN_TCD, "ab") as fh:
        fh.write(b"\0")
    with pytest.raises(AstroTableMismatch, match=r"congen-1700-2100.tcd: SHA-256 .*PROVENANCE says"):
        load_congen_run(tmp_path)
    (tmp_path / at.CONGEN_TCD).unlink()
    with pytest.raises(FileNotFoundError):
        load_congen_run(tmp_path)


def test_the_congen_dump_must_be_of_the_hashed_tcd(tmp_path):
    # every file matches PROVENANCE, but the congen dump says it was made from another .tcd
    _write_run(tmp_path, edit=lambda prov, congen, tcd: congen.update(tcd_sha256="f" * 64))
    with pytest.raises(AstroTableMismatch, match="the congen dump is of TCD f+, not the run's"):
        load_congen_run(tmp_path)


def test_the_tcd_itself_as_the_run_tcd_is_refused(tmp_path):
    def same(prov, congen, tcd):
        prov["files"]["congen_tcd"]["sha256"] = at.TCD_SHA256
    _write_run(tmp_path, edit=same)
    with pytest.raises(AstroTableMismatch):
        load_congen_run(tmp_path)


@pytest.mark.parametrize("edit,what", [
    (lambda prov, c, t: prov["tool"]["congen"].update(sha256="0" * 64), "not the pinned congen 1.7-r2"),
    (lambda prov, c, t: prov["tool"]["libtcd"].update(sha256="0" * 64), "not the pinned congen 1.7-r2"),
    (lambda prov, c, t: prov.update(command="cp harmonics.tcd congen-1700-2100.tcd"), "the run's command is"),
    (lambda prov, c, t: prov.update(years=[1900, 2100]), r"the run covers \[1900, 2100\]"),
    (lambda prov, c, t: prov.pop("tool"), "missing or malformed"),
    (lambda prov, c, t: prov["files"].update(congen_tcd="x"), "files.congen_tcd is 'x', not an object"),
])
def test_a_run_that_is_not_the_pinned_congen_run_is_refused(tmp_path, edit, what):
    _write_run(tmp_path, edit=edit)
    with pytest.raises(AstroTableMismatch, match=what):
        load_congen_run(tmp_path)


def test_a_malformed_provenance_file_is_refused(tmp_path):
    _write_run(tmp_path)
    (tmp_path / "PROVENANCE.json").write_text("{not json")
    with pytest.raises(AstroTableMismatch, match="missing or malformed"):
        load_congen_run(tmp_path)


@pytest.mark.parametrize("which", ["congen", "TCD"])
def test_a_dump_that_does_not_cover_1700_2100_is_refused(tmp_path, which):
    def short(prov, congen, tcd):
        d = congen if which == "congen" else tcd
        d["years"] = [at.FIRST_YEAR + 1, at.LAST_YEAR]
        for c in d["constituents"].values():
            c["v0u"], c["f"] = c["v0u"][1:], c["f"][1:]
    _write_run(tmp_path, edit=short)
    with pytest.raises(AstroTableMismatch, match=f"the {which} dump covers 1701-2100, expected 1700-2100"):
        load_congen_run(tmp_path)


def test_load_congen_run_refuses_another_tcd(tmp_path):
    _write_run(tmp_path)
    with pytest.raises(AstroTableMismatch, match="expected " + "e" * 64):
        load_congen_run(tmp_path, tcd_sha256="e" * 64)


def test_a_congen_run_is_made_only_by_load_congen_run():
    congen, tcd = RUN_PAIR
    with pytest.raises(TypeError, match="load_congen_run"):
        at.CongenRun(congen, tcd, {}, Path("."))


def test_the_pinned_tools_are_the_scripts():
    script = (Path(__file__).resolve().parents[1] / "tools" / "congen_tables.sh").read_text()
    assert f"CONGEN_SHA256={at.CONGEN_SHA256}" in script and f"LIBTCD_SHA256={at.LIBTCD_SHA256}" in script
    assert f"\nOTC_TCD_SHA256={at.TCD_SHA256}\n" in script and f"\nOTC_TCD_ID={at.TABLE_ID}\n" in script
    assert f"\nSOURCE_TCD_SHA256={at.SOURCE_TCD_SHA256}\n" in script
    assert f"\nOTC_ADDED=({' '.join(at.OTC_ADDED)})\n" in script


def test_the_otc_definitions_are_m2_with_h_1_and_h_3():
    """MA2 and MB2 in congen's Basic form: M2's arguments (T s h = 2 -2 2, u = 2 xi - 2 nu, node factor 78)
    with only h changed."""
    rows = {ln.split()[0]: ln.split()[1:] for ln in at.OTC_DEFINITIONS.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")}
    m2 = ["Basic", "2", "-2", "2", "0", "0", "0", "2", "-2", "0", "0", "0", "0", "78"]
    assert sorted(rows) == sorted(at.OTC_ADDED)
    assert rows["MA2"] == m2[:3] + ["1"] + m2[4:] and rows["MB2"] == m2[:3] + ["3"] + m2[4:]


@pytest.mark.parametrize("edit,what", [
    (lambda prov, c, t: prov["otc_tcd"]["source_tcd"].update(sha256="0" * 64), "the run's OTC TCD is made from"),
    (lambda prov, c, t: prov["otc_tcd"].update(added=["MA2"]), "the run's OTC TCD is made from"),
    (lambda prov, c, t: prov["otc_tcd"].update(definitions_sha256="0" * 64), "the run's OTC TCD is made from"),
    (lambda prov, c, t: prov.pop("otc_tcd"), "PROVENANCE.json has no otc_tcd: a congen run made before the OTC TCD"),
])
def test_a_run_whose_otc_tcd_is_not_the_decided_one_is_refused(tmp_path, edit, what):
    _write_run(tmp_path, edit=edit)
    with pytest.raises(AstroTableMismatch, match=what):
        load_congen_run(tmp_path)


def test_the_otc_tcd_file_in_a_run_is_hashed_and_must_be_the_dumped_tcd(tmp_path):
    otc = b"stand-in OTC TCD"
    sha = __import__("hashlib").sha256(otc).hexdigest()

    import hashlib
    defs = at.OTC_DEFINITIONS.read_bytes()

    def with_file(prov, congen, tcd):
        prov["files"]["otc_tcd"] = {"path": "otc.tcd", "sha256": sha}
        tcd["tcd_sha256"] = sha
        # a run holding the OTC TCD lists its other files too: the fit dump (here the TCD dump's own years)
        (tmp_path / "fit.json").write_text(json.dumps(tcd))
        for key, name in (("fit_dump", "fit.json"), ("congen_input", "in.txt"), ("otc_definitions", "defs.txt")):
            prov["files"][key] = {"path": name, "sha256": hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}
    (tmp_path / "otc.tcd").write_bytes(otc)
    (tmp_path / "in.txt").write_bytes(b"congen input\n" + defs)
    (tmp_path / "defs.txt").write_bytes(defs)
    _write_run(tmp_path, edit=with_file)
    assert load_congen_run(tmp_path, tcd_sha256=sha).tcd.tcd_sha256 == sha
    (tmp_path / "otc.tcd").write_bytes(otc + b"\0")
    with pytest.raises(AstroTableMismatch, match="the OTC TCD otc.tcd hashes to"):
        load_congen_run(tmp_path, tcd_sha256=sha)


def test_the_gate_scripts_map_noaa_names_as_the_astro_tables_do():
    gates = Path(__file__).resolve().parents[2] / "gates"
    for f, var in (("runner.rb", "NMAP"), ("gate3/extras.rb", "NM")):
        m = re.search(rf"^{var} = \{{([^}}]*)\}}", (gates / f).read_text(), re.M)
        pairs = dict(re.findall(r"'([A-Z0-9]+)' => '([A-Z0-9]+)'", m.group(1)))
        assert {k: pairs.get(k) for k in at.NAME_TO_TCD} == at.NAME_TO_TCD, f


# --- the build takes only the congen run's table (release_table_errors) -----------------------------

FIT = dump(TCD)          # the fit's table (2020-2022)
RUN_PAIR = congen_run(FIT)


def kp0b(names=SPEEDS):
    return kp0b_tables(RUN_PAIR, names, "tcd-test")


def build_with(tables, run=RUN_PAIR):
    return build(FIT, [doc(FIT)], astro_tables=tables, congen_run=run)


def test_the_run_table_builds():
    assert validate_release(build_with(kp0b())) == []
    assert release_table_errors(kp0b()[0], *RUN_PAIR) == []


def test_a_published_table_with_altered_values_is_refused():
    t = kp0b()
    i = 2026 - at.FIRST_YEAR
    t[0]["constituents"]["M2"]["v0u_deg"][i] = 123.45
    t[0]["constituents"]["M2"]["f"][i] = 0.5
    errs = release_table_errors(t[0], *RUN_PAIR)
    assert [e.split(": ", 1)[1] for e in errs] == [
        f"v0u_deg 123.45 != the congen run's {kp0b()[0]['constituents']['M2']['v0u_deg'][i]}",
        f"f 0.5 != the congen run's {kp0b()[0]['constituents']['M2']['f'][i]}"]
    with pytest.raises(ValueError, match="astro table 'tcd-test' is not the congen run's table .*M2 2026: v0u_deg 123.45"):
        build_with(t)


def test_a_value_altered_in_any_year_is_refused():
    t = kp0b()
    for y in (1700, 1750, 2100):
        t[0]["constituents"]["K1"]["f"][y - at.FIRST_YEAR] += 0.0001
    errs = release_table_errors(t[0], *RUN_PAIR)
    assert [e.split(" K1 ")[1].split(":")[0] for e in errs] == ["1700", "1750", "2100"]


def test_the_release_owns_its_tables():
    t = kp0b()
    release = build_with(t)
    assert release["astro_tables"][0] is not t[0]
    i = 2026 - at.FIRST_YEAR
    before = release["astro_tables"][0]["constituents"]["M2"]["v0u_deg"][i]
    t[0]["constituents"]["M2"]["v0u_deg"][i] = 123.45
    assert release["astro_tables"][0]["constituents"]["M2"]["v0u_deg"][i] == before


def test_write_release_checks_the_tables_against_the_run_again(tmp_path):
    release = build_with(kp0b())
    release["astro_tables"][0]["constituents"]["M2"]["v0u_deg"][326] = 123.45
    assert validate_release(release) == []   # check 7 does not look at values
    with pytest.raises(ValueError, match="the astro tables are not the congen run's .*M2 2026: v0u_deg 123.45"):
        write_release(release, tmp_path, datestamp=D, app_export=None, congen_run=RUN_PAIR)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match="CongenRun"):
        write_release(release, tmp_path, datestamp=D, app_export=None, congen_run=tuple(RUN_PAIR))


def test_a_built_release_is_not_written_without_its_congen_run(tmp_path):
    release = build_with(kp0b())
    assert isinstance(release, BuiltRelease) and type(copy.deepcopy(release)) is BuiltRelease
    assert json.dumps(release, sort_keys=True) == json.dumps(dict(release), sort_keys=True)   # the marker is not serialised
    with pytest.raises(ValueError, match="made by a release build: write it with congen_run"):
        write_release(release, tmp_path / "a", datestamp=D, app_export=None, congen_run=None)
    assert not (tmp_path / "a").exists() or list((tmp_path / "a").iterdir()) == []
    assert len(write_release(release, tmp_path / "b", datestamp=D, app_export=None, congen_run=RUN_PAIR)) == 6


def test_a_document_not_from_a_build_still_writes_without_a_run(tmp_path):
    example = json.loads((Path(__file__).parent / "fixtures" / "example-1.0.json").read_text())
    assert not isinstance(example, BuiltRelease)
    assert write_release(example, tmp_path, datestamp=example["release"]["datestamp"], app_export=None, congen_run=None)


def test_build_release_takes_only_a_loaded_congen_run():
    with pytest.raises(ValueError, match="must be astro_tables.load_congen_run's CongenRun"):
        build_with(kp0b(), run=tuple(RUN_PAIR))


@pytest.mark.parametrize("edit,what", [
    (lambda t: t.update(v0_model="tamura_iho2006"), "v0_model 'tamura_iho2006', expected 'schureman_tcd'"),
    (lambda t: t.update(generator="TEST ONLY: the TCD dump's V0+u and f"), "marks a TEST ONLY table"),
    (lambda t: t.update(generator="something else"), "generator 'something else', expected"),
    (lambda t: t.update(first_year=1900), r"years 1900-2100, the release's tables cover 1700-2100"),
    (lambda t: t.update(tables_sha256="e" * 64), "the run's TCD is"),
    (lambda t: t["constituents"]["K1"].update(speed_deg_per_hour=15.0410687), "K1: speed 15.0410687 != the run's"),
    (lambda t: t["constituents"]["O1"]["f"].pop(), "O1: f is not 401 values"),
])
def test_a_table_that_is_not_the_run_table_is_refused(edit, what):
    t = kp0b()
    edit(t[0])
    with pytest.raises(ValueError, match=what):
        build_with(t)


def test_the_test_only_stand_in_is_refused():
    t = standin(FIT, SPEEDS, table_id="tcd-test", first_year=Y0, last_year=Y1)
    assert release_table_errors(t, *RUN_PAIR) == [
        "astro table 'tcd-test': generator \"TEST ONLY: the TCD dump's V0+u and f (the release's tables are KP0b's)\" "
        "marks a TEST ONLY table, which is never released"]
    with pytest.raises(ValueError, match="TEST ONLY"):
        build_with([t])


def test_a_one_year_table_is_refused():
    one = astro_table(*RUN_PAIR, SPEEDS, table_id="tcd-test", first_year=2021, last_year=2021)
    with pytest.raises(ValueError, match="years 2021-2021"):
        build_with([one])


def test_a_run_checked_against_another_tcd_is_refused():
    other = dump("e" * 64)
    with pytest.raises(FitIntegrityError, match="the congen run was checked against TCD e+"):
        build_with(kp0b(), run=congen_run(other))


# --- build check 7 ----------------------------------------------------------------------------------

@pytest.fixture()
def release():
    r = build_with(kp0b())
    assert astro_table_errors(r) == [] and validate_release(r) == []
    return r


def test_check_7_a_missing_row_fails(release):
    r = copy.deepcopy(release)
    del r["astro_tables"][0]["constituents"]["K1"]
    errs = astro_table_errors(r)
    assert len(errs) == 1 and "constituent 'K1' has no row in astro table 'tcd-test'" in errs[0]
    assert any("has no row" in e for e in validate_release(r))


def test_check_7_speeds(release):
    r = copy.deepcopy(release)
    r["astro_tables"][0]["constituents"]["M2"]["speed_deg_per_hour"] += 2e-7
    errs = astro_table_errors(r)
    assert any("M2 speed 28.9841042 != astro table row speed" in e for e in errs)
    assert any("M2 astro table row speed" in e and "constituents table speed 28.9841042" in e for e in errs)
    r["astro_tables"][0]["constituents"]["M2"]["speed_deg_per_hour"] = SPEEDS["M2"] + 0.5e-7
    assert astro_table_errors(r) == []


def mini_release(rows, set_cons=(), bins=None, nodal="f_u_at_prediction", first=2020, last=2022, table_id="t"):
    """The smallest document check 7 reads: one convention, one table, one set."""
    n = last - first + 1
    cs = {"set_id": "S/x", "convention_id": "c", "constituents": [{"name": k, "speed_deg_per_hour": v} for k, v in set_cons]}
    if bins is not None:
        cs["current_bins"] = [{"bin": 1, "constituents": [{"name": k, "speed_deg_per_hour": v} for k, v in bins]}]
    return {"conventions": [{"convention_id": "c", "nodal_handling": nodal, "constituent_table_version": "v",
                             "astro_table_id": table_id}],
            "constituents": [{"constituent_table_version": "v", "name": k, "speed_deg_per_hour": v} for k, v in rows.items()],
            "astro_tables": [{"astro_table_id": "t", "first_year": first, "last_year": last,
                              "constituents": {k: {"speed_deg_per_hour": v, "v0u_deg": [0.0] * n, "f": [1.0] * n}
                                               for k, v in rows.items()}}],
            "stations": [{"station_id": "S", "constant_sets": [cs]}]}


@pytest.mark.parametrize("name", ["M2", "K1", "SA", "M4"])
def test_check_7_one_speed_step_passes_and_two_fail_for_every_constituent(name):
    s = REAL_SPEEDS[name]
    for steps, fails in ((1, False), (-1, False), (2, True), (-2, True)):
        r = mini_release({name: round(s + steps * 1e-7, 7)}, [(name, s)])
        r["constituents"][0]["speed_deg_per_hour"] = s
        errs = astro_table_errors(r)
        assert bool(errs) is fails, (name, steps, errs)
        if fails:
            assert len(errs) == 2 and f"{name} speed {s} != astro table row speed" in errs[0]


def test_check_7_is_within_1e_7_not_more():
    for row, sset, fails in ((28.9841042, 28.98410434, True), (28.9841042, 28.98410430, False),
                             (15.0410686, 15.0410687, False), (15.0410686, 15.04106871, True)):
        r = mini_release({"M2": row}, [("M2", sset)])
        assert bool(astro_table_errors(r)) is fails, (row, sset)


def test_check_7_covers_current_bins():
    assert astro_table_errors(mini_release({"M2": 28.9841042}, bins=[("M2", 28.9841042)])) == []
    errs = astro_table_errors(mini_release({"M2": 28.9841042}, bins=[("K1", 15.0410686)]))
    assert errs == ["stations/0 (S) constant_sets/0 (S/x): constituent 'K1' has no row in astro table 't'"]
    errs = astro_table_errors(mini_release({"M2": 28.9841042}, bins=[("M2", 28.9841045)]))
    assert errs == ["stations/0 (S) constant_sets/0 (S/x): M2 speed 28.9841045 != astro table row speed 28.9841042"]


def test_check_7_last_year_before_first_year():
    r = mini_release({"M2": 28.9841042}, first=2022, last=2020)
    assert astro_table_errors(r) == ["astro_tables/0 (t): last_year 2020 is before first_year 2022"]


def test_check_7_array_lengths(release):
    r = copy.deepcopy(release)
    r["astro_tables"][0]["constituents"]["S2"]["f"].append(1.0)
    r["astro_tables"][0]["constituents"]["O1"]["v0u_deg"].pop()
    errs = astro_table_errors(r)
    assert errs == ["astro_tables/0 (tcd-test) O1: v0u_deg has 400 entries, expected 401 (1700-2100)",
                    "astro_tables/0 (tcd-test) S2: f has 402 entries, expected 401 (1700-2100)"]


def test_check_7_tables_sha256_and_the_table_id(release):
    r = copy.deepcopy(release)
    r["conventions"][0]["tables_sha256"] = "e" * 64
    assert astro_table_errors(r) == [f"conventions/0 ({r['conventions'][0]['convention_id']}): tables_sha256 "
                                     f"{'e' * 64} != astro table 'tcd-test''s {TCD}"]
    r = copy.deepcopy(release)
    r["conventions"][0]["astro_table_id"] = "tcd-other"
    assert astro_table_errors(r) == [f"conventions/0 ({r['conventions'][0]['convention_id']}): astro_table_id "
                                     "'tcd-other' is not defined in astro_tables"]
    r = copy.deepcopy(release)
    r["astro_tables"].append(copy.deepcopy(r["astro_tables"][0]))
    assert astro_table_errors(r) == ["duplicate astro_table_id 'tcd-test'"]


def test_check_7_applies_to_every_convention_that_names_a_table(release):
    # schema check (7) has no nodal_handling condition: "every constituent of every set with constants
    # has a row in its convention's astro table"
    r = copy.deepcopy(release)
    r["conventions"][0]["nodal_handling"] = "none"
    del r["astro_tables"][0]["constituents"]["K1"]
    assert len(astro_table_errors(r)) == 1 and "constituent 'K1' has no row" in astro_table_errors(r)[0]
    # a convention that names no table has nothing to check against
    r = mini_release({"M2": 28.9841042}, [("K1", 15.0410686)], nodal="none")
    del r["conventions"][0]["astro_table_id"]
    assert astro_table_errors(r) == []


def test_build_release_stops_on_a_missing_row():
    with pytest.raises(ValueError, match="build check 7 .*constituent 'K1' has no row"):
        build_with(kp0b(["M2", "S2", "O1"]))


# --- the real congen run ------------------------------------------------------------------------------

def _run_dir() -> Path:
    d = Path(os.environ.get("OTC_CONGEN_RUN", otc_work() / "astro" / "otc-tcd-20251228-ma2mb2"))
    if not (d / "PROVENANCE.json").is_file():
        pytest.skip(f"needs a congen_tables.sh run in {d}")
    return d


GESLA_45 = ["2MK3", "2MK5", "2MO5", "2MS6", "2N2", "2Q1", "2SM2", "EPS2", "J1", "K1", "K2", "L2", "LDA2", "M1",
            "M2", "M3", "M4", "M6", "M8", "MF", "MK3", "MKS2", "MM", "MN4", "MS4", "MSF", "MU2", "N2", "N4", "NU2",
            "O1", "OO1", "P1", "Q1", "R2", "RHO1", "S1", "S2", "S3", "S4", "S6", "SA", "SIG1", "SSA", "T2"]


def test_the_congen_run_equals_the_tcd_for_every_constituent_and_year():
    congen, tcd = load_congen_run(_run_dir())
    assert (congen.first_year, congen.last_year, tcd.first_year, tcd.last_year) == (1700, 2100, 1700, 2100)
    assert set(congen.constituents) == set(tcd.constituents) and len(tcd.constituents) == 178
    assert tcd_equality_errors(congen, tcd, tcd.constituents) == []
    assert tcd.tcd_sha256 == at.TCD_SHA256
    t = astro_table(congen, tcd, GESLA_45)
    assert t["tables_sha256"] == at.TCD_SHA256 and (t["first_year"], t["last_year"]) == (1700, 2100)
    for name, row in t["constituents"].items():
        assert len(row["v0u_deg"]) == len(row["f"]) == 401
        assert all(0.0 <= v < 360.0 for v in row["v0u_deg"])
        # the published value is the TCD's representation
        assert row["v0u_deg"] == [round(v % 360.0, 2) % 360.0 for v in tcd[name].v0u], name
        assert row["f"] == [round(f, 4) for f in tcd[name].f], name


def test_the_real_tables_with_one_value_moved_by_001_degree_stop_the_build(tmp_path):
    congen, tcd = load_congen_run(_run_dir())
    data = {"years": [1700, 2100], "tcd_sha256": congen.tcd_sha256, "aliases": dict(congen.aliases),
            "constituents": {n: {"speed": c.speed, "v0u": list(c.v0u), "f": list(c.f)}
                             for n, c in congen.constituents.items()}}
    data["constituents"]["M2"]["v0u"][2026 - 1700] += 0.01
    with pytest.raises(AstroTableMismatch, match="M2 2026: V0\\+u"):
        astro_table(TcdTable(data), tcd, GESLA_45)


def test_ma2_and_mb2_are_m2_minus_and_plus_sa():
    """Speeds M2 -/+ SA within one stored step (1e-7 deg/h); V0+u M2's -/+ SA's within one step (0.01 degree);
    f M2's, every year 1700-2100."""
    congen, tcd = load_congen_run(_run_dir())
    m2, sa = tcd["M2"], tcd["SA"]
    for name, sign in (("MA2", -1), ("MB2", 1)):
        c = tcd[name]
        assert abs(c.speed - (m2.speed + sign * sa.speed)) <= 1e-7 + 1e-12, name
        d = [abs(v - (m + sign * s)) % 360.0 for v, m, s in zip(c.v0u, m2.v0u, sa.v0u, strict=True)]
        assert max(min(x, 360.0 - x) for x in d) <= 0.01 + 1e-9, name
        assert c.f == m2.f, name
    t = astro_table(congen, tcd, ["MA2", "MB2", "M2"])
    assert t["astro_table_id"] == at.TABLE_ID == "otc-tcd-20251228-ma2mb2"


def test_the_otc_tcd_keeps_every_source_constituent_unchanged():
    """The run's TCD dump against the source TCD's fit dump ($OTC_WORK/tcd_table.json, dump_tcd.rb of
    data/harmonics-dwf-20251228-free.tcd, 1900-2100): every one of its 176 constituents is equal."""
    src_path = otc_work() / "tcd_table.json"
    if not src_path.is_file():
        pytest.skip(f"needs {src_path}")
    src = TcdTable.load(src_path)
    if src.tcd_sha256 != at.SOURCE_TCD_SHA256:
        pytest.skip(f"{src_path} is not a dump of the source TCD")
    _, tcd = load_congen_run(_run_dir())
    assert len(src.constituents) == 176 and set(tcd.constituents) == set(src.constituents) | set(at.OTC_ADDED)
    off = src.first_year - tcd.first_year
    for n, c in src.constituents.items():
        o = tcd.constituents[n]
        assert (o.speed, o.v0u[off:], o.f[off:]) == (c.speed, c.v0u, c.f), n


def test_the_libtcd_dump_is_dump_tcd_rb_byte_for_byte(tmp_path):
    """tools/tcd (tcd_dump.c + tcd_json.py, built by the run) on the source TCD writes the file dump_tcd.rb
    wrote for it ($OTC_WORK/tcd_table.json, 1900-2100)."""
    import subprocess
    import sys
    run = _run_dir()
    exe = run / "build" / "tcd_dump"
    ref = otc_work() / "tcd_table.json"
    src = Path(__file__).resolve().parents[3] / "data" / "harmonics-dwf-20251228-free.tcd"
    if not (exe.is_file() and ref.is_file() and src.is_file()):
        pytest.skip("needs the run's tcd_dump, $OTC_WORK/tcd_table.json and the source TCD")
    tsv, out = tmp_path / "d.tsv", tmp_path / "d.json"
    tsv.write_text(subprocess.run([str(exe), str(src), "1900", "2100"], check=True, capture_output=True,
                                  text=True).stdout)
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "tcd" / "tcd_json.py"),
                    str(src), "1900", "2100", str(tsv), str(out)], check=True, capture_output=True)
    assert out.read_bytes() == ref.read_bytes()


def test_tcd_json_aliases_are_dump_tcd_rbs():
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("tcd_json", root / "otc" / "tools" / "tcd" / "tcd_json.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rb = (root / "gesla" / "dump_tcd.rb").read_text()
    block = re.search(r"TICON_TO_TCD_NAMES = \{(.*?)\}", rb, re.S).group(1)
    assert dict(re.findall(r"'([A-Z0-9]+)' => '([A-Z0-9]+)'", block)) == mod.ALIASES


def _run_copy(tmp_path):
    import shutil
    src = _run_dir()
    d = tmp_path / "run"
    d.mkdir()
    for k, e in json.loads((src / "PROVENANCE.json").read_text())["files"].items():
        shutil.copy2(src / e["path"], d / e["path"])
    shutil.copy2(src / "PROVENANCE.json", d / "PROVENANCE.json")
    return d


def _bump_m2_2026(p: Path) -> None:
    j = json.loads(p.read_text())
    v = j["constituents"]["M2"]["v0u"]
    i = 2026 - j["years"][0]
    v[i] = round((v[i] + 1.0) % 360, 2)
    p.write_text(json.dumps(j, separators=(",", ":")))


def _reprovenance(d: Path, key: str) -> None:
    """PROVENANCE made to agree with an edited file (an attacker or a stale run that re-hashed it)."""
    prov = json.loads((d / "PROVENANCE.json").read_text())
    e = prov["files"][key]
    e["sha256"] = __import__("hashlib").sha256((d / e["path"]).read_bytes()).hexdigest()
    (d / "PROVENANCE.json").write_text(json.dumps(prov))


def test_every_listed_run_file_is_hashed(tmp_path):
    # N4: the fit dump, the congen input and the OTC definitions are hashed against PROVENANCE like the dumps
    d = _run_copy(tmp_path)
    load_congen_run(d)
    for key in ("fit_dump", "congen_input", "otc_definitions"):
        e = json.loads((d / "PROVENANCE.json").read_text())["files"][key]
        keep = (d / e["path"]).read_bytes()
        (d / e["path"]).write_bytes(keep + b"\n")
        with pytest.raises(AstroTableMismatch, match=f"{re.escape(e['path'])}: SHA-256 .*PROVENANCE says"):
            load_congen_run(d)
        (d / e["path"]).write_bytes(keep)
    load_congen_run(d)


def test_a_fit_dump_with_other_values_is_refused_even_when_rehashed(tmp_path):
    # N4: a fit dump that still says it is of the OTC TCD (tcd_sha256) but holds other values
    d = _run_copy(tmp_path)
    _bump_m2_2026(d / "tcd-1900-2100.json")
    _reprovenance(d, "fit_dump")
    with pytest.raises(AstroTableMismatch, match="the fit dump is not the TCD dump's: M2: values differ"):
        load_congen_run(d)


@pytest.mark.parametrize("key,match", [("otc_definitions", "are not the committed otc_constituents.txt"),
                                       ("congen_input", "does not end with the committed OTC definitions")])
def test_edited_definitions_or_congen_input_are_refused_even_when_rehashed(tmp_path, key, match):
    d = _run_copy(tmp_path)
    e = json.loads((d / "PROVENANCE.json").read_text())["files"][key]
    (d / e["path"]).write_bytes((d / e["path"]).read_bytes() + b"# edited\n")
    _reprovenance(d, key)
    with pytest.raises(AstroTableMismatch, match=match):
        load_congen_run(d)


def test_a_run_with_the_otc_tcd_must_list_its_other_files(tmp_path):
    d = _run_copy(tmp_path)
    prov = json.loads((d / "PROVENANCE.json").read_text())
    del prov["files"]["fit_dump"]
    (d / "PROVENANCE.json").write_text(json.dumps(prov))
    with pytest.raises(AstroTableMismatch, match=r"must list \['fit_dump'\]"):
        load_congen_run(d)


def test_fit_dump_errors_compares_the_years_it_holds():
    _, tcd = load_congen_run(_run_dir())
    fit = TcdTable.load(_run_dir() / "tcd-1900-2100.json")
    assert at.fit_dump_errors(fit, tcd) == []
    other = TcdTable({"years": [1900, 2100], "tcd_sha256": "0" * 64, "aliases": dict(fit.aliases),
                      "constituents": {n: {"speed": c.speed, "v0u": list(c.v0u), "f": list(c.f)}
                                       for n, c in fit.constituents.items()}})
    assert at.fit_dump_errors(other, tcd) == [f"fit dump of TCD {'0' * 64}, TCD dump of {tcd.tcd_sha256}"]


def test_tcd_merge_leaves_nothing_behind_on_failure(tmp_path):
    # N6: a failure before the OTC TCD exists (OUT is a directory) and after (TCD_MERGE_FAIL_AFTER_RECORDS) leaves
    # neither OUT.tcd.records nor a partial OUT.tcd; a normal run writes the pinned OTC TCD
    import hashlib
    import shutil
    import subprocess
    prefix = otc_work() / "astro" / "congen-1.7-r2" / "build" / "prefix"
    prefix = Path(os.environ.get("OTC_LIBTCD_PREFIX", prefix))
    src = Path(__file__).resolve().parents[3] / "data" / "harmonics-dwf-20251228-free.tcd"
    cc = shutil.which("cc") or shutil.which("gcc")
    if not ((prefix / "lib" / "libtcd.a").is_file() and src.is_file() and cc):
        pytest.skip("needs a built libtcd (congen_tables.sh build/prefix), the source TCD and a C compiler")
    exe = tmp_path / "tcd_merge"
    subprocess.run([cc, "-O2", f"-I{prefix / 'include'}", str(Path(__file__).resolve().parents[1] / "tools" / "tcd" /
                    "tcd_merge.c"), str(prefix / "lib" / "libtcd.a"), "-o", str(exe)], check=True)
    congen = _run_dir() / at.CONGEN_TCD
    args = lambda out: [str(exe), str(src), str(congen), str(out), "2026-10-11 00:00", "MA2", "MB2"]
    (tmp_path / "busy.tcd").mkdir()
    (tmp_path / "busy.tcd" / "keep").write_text("x")
    r = subprocess.run(args(tmp_path / "busy.tcd"), capture_output=True, text=True)
    assert r.returncode == 1 and "could not create" in r.stderr and not (tmp_path / "busy.tcd.records").exists()
    r = subprocess.run(args(tmp_path / "part.tcd"), capture_output=True, text=True,
                       env={**os.environ, "TCD_MERGE_FAIL_AFTER_RECORDS": "100"})
    assert r.returncode == 1 and "TCD_MERGE_FAIL_AFTER_RECORDS" in r.stderr
    assert not (tmp_path / "part.tcd").exists() and not (tmp_path / "part.tcd.records").exists()
    subprocess.run(args(tmp_path / "ok.tcd"), check=True, capture_output=True)
    assert hashlib.sha256((tmp_path / "ok.tcd").read_bytes()).hexdigest() == at.TCD_SHA256
    assert not (tmp_path / "ok.tcd.records").exists()


def test_noaa_names_get_their_own_rows_with_the_tcd_values():
    congen, tcd = load_congen_run(_run_dir())
    t = astro_table(congen, tcd, ["LAM2", "LDA2", "RHO", "RHO1"])["constituents"]
    assert sorted(t) == ["LAM2", "LDA2", "RHO", "RHO1"]
    assert t["LAM2"] == t["LDA2"] and t["RHO"] == t["RHO1"]
    assert abs(t["LAM2"]["speed_deg_per_hour"] - 29.455626) < 1e-6 and abs(t["RHO"]["speed_deg_per_hour"] - 13.471515) < 1e-6


def test_sigma1_and_theta1_are_the_tcds_sig1_and_the1():
    congen, tcd = load_congen_run(_run_dir())
    t = astro_table(congen, tcd, ["SIGMA1", "SIG1", "THETA1", "THE1"])["constituents"]
    assert t["SIGMA1"] == t["SIG1"] and t["THETA1"] == t["THE1"]
    assert abs(t["SIGMA1"]["speed_deg_per_hour"] - 12.92714) < 1e-6 and abs(t["THETA1"]["speed_deg_per_hour"] - 15.512589) < 1e-6


JMA_59 = ["2MK6", "2MN6", "2MS6", "2N2", "2Q1", "2SM2", "2SM6", "CHI1", "J1", "K1", "K2", "KJ2", "L2", "LDA2", "M1C",
          "M2", "M3", "M4", "M6", "MF", "MK3", "MK4", "MKS2", "MM", "MN4", "MNS2", "MO3", "MP1", "MS4", "MSF", "MSK6",
          "MSN2", "MSN6", "MU2", "N2", "NU2", "O1", "OO1", "OP2", "P1", "PHI1", "PI1", "PSI1", "Q1", "R2", "RHO1", "S1",
          "S2", "S4", "SA", "SIG1", "SK3", "SK4", "SN4", "SO1", "SO3", "SSA", "T2", "THE1"]


def test_every_name_the_jma_adapter_publishes_has_a_row():
    # the JMA adapter (ADPORT 61fbb20) publishes TCD names: M1 -> M1C, SIGMA1 -> SIG1, THETA1 -> THE1,
    # LAMBDA2 -> LDA2; OQ2 is left out. M1C (14.4920521 deg/h) is its own TCD constituent, not M1.
    congen, tcd = load_congen_run(_run_dir())
    t = astro_table(congen, tcd, JMA_59)["constituents"]
    assert len(t) == 59 and t["M1C"]["speed_deg_per_hour"] == 14.4920521 and t["M1C"] != t.get("M1")


def test_a_constituent_the_tcd_lacks_stops_the_build():
    # Kartverket's GAM2: no TCD row, so no value can be checked equal to the TCD (prediction spec §3.4 rule 2)
    congen, tcd = load_congen_run(_run_dir())
    with pytest.raises(AstroTableMismatch, match="GAM2: not a constituent of the TCD"):
        astro_table(congen, tcd, ["M2", "GAM2"])
