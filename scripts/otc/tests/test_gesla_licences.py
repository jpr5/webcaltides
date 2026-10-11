"""L-NC: per-contributor licences of the GESLA-fit sets, and the adapter contract's several licences."""
import collections
import copy
import csv
import hashlib
import json
import os
import types
from pathlib import Path

import pytest

from otc_pipeline import adapter as ad
from otc_pipeline.gesla_licences import (INVENTORY_CSV, LICENCES_JSON, OVERRIDES_CSV as OVERRIDES, ROLES_CSV, contributor_licences, gesla_licences,
                                         load_licences, record_licence_ids)

GESLA_TERMS = "https://gesla787883612.wordpress.com/license/"
# owner decisions 2026-10-08 (ledger) and the orchestrator rule for TTW/RWS_HIST/UNAM_HIST (2026-10-10)
WANT = {"CMEMS": "gesla-cmems-nc", "UZ": "gesla-uz-nc", "CV": "gesla-cv-nc", "CRMS": "gesla-unlisted-nc",
        "HCT": "gesla-unlisted-nc", "CM": "gesla-unlisted-nc", "TTW": "gesla-ttw", "RWS_HIST": "gesla-rws-hist",
        "UNAM": "gesla-unam-nc", "UNAM_HIST": "gesla-unam-nc",
        # round 3: the originator-terms check (orchestrator decisions 2026-10-10)
        "BOM": "gesla-bom-nc", "BODC": "gesla-bodc-nc", "UHSLC_RQ": "gesla-uhslc-nc", "UHSLC_FD": "gesla-uhslc-nc",
        "JODC_JMA": "gesla-jodc-nc", "JODC_PAHB": "gesla-jodc-nc", "CDWR": "gesla-cdwr-nc", "IEO": "gesla-ieo-nc",
        # round 5: CHS terms (MEDS), and JODC_JCG/JODC_GIAJ under the same J-DOSS clause
        "MEDS": "gesla-chs-withheld", "JODC_JCG": "gesla-jodc-nc", "JODC_GIAJ": "gesla-jodc-nc"}
UNCHECKED = ("SFWMD NCDEM MI_C NHS DA_SAT CCO FMI NWFWMD MI_R NOC ESEAS BfG DA_IDH DA_MM DMI "
             "GLOSS ICG").split()
WANT |= {c: "gesla-unchecked-nc" for c in UNCHECKED}
ALLOWED = ("NOAA", "USGS", "RWS", "WSV", "REFMAR", "SMHI", "ISPRA")
# round 4: gesla-derived is split, one licence per allowed originator
WANT |= {c: f"gesla-{c.lower()}" for c in ALLOWED}
# each allowed originator's own credit, which no other originator's licence may carry
CREDIT = {"NOAA": "National Ocean Service", "USGS": "Geological Survey", "RWS": "Rijkswaterstaat", "WSV": "PEGELONLINE",
          "REFMAR": "REFMAR", "SMHI": "SMHI", "ISPRA": "ISPRA"}
SPDX = {"gesla-noaa": "LicenseRef-PublicDomain-USGov", "gesla-usgs": "LicenseRef-PublicDomain-USGov", "gesla-rws": "CC0-1.0",
        "gesla-wsv": "DL-DE-ZERO-2.0", "gesla-refmar": "etalab-2.0", "gesla-smhi": "CC-BY-4.0", "gesla-ispra": "CC-BY-4.0"}
# round 4: withheld from the release until permission (no data or products to third parties without consent)
WITHHELD = {"gesla-bodc-nc", "gesla-sanho-nc", "gesla-jodc-nc", "gesla-bom-nc", "gesla-chs-withheld"}
NON_COMMERCIAL = {"gesla-cmems-nc", "gesla-uz-nc", "gesla-cv-nc", "gesla-unlisted-nc", "gesla-unam-nc", "gesla-bom-nc",
                  "gesla-bodc-nc", "gesla-sanho-nc", "gesla-uhslc-nc", "gesla-jodc-nc", "gesla-cdwr-nc", "gesla-ieo-nc",
                  "gesla-unchecked-nc", "gesla-chs-withheld", "gesla-uhslc-jma-nc", "gesla-uhslc-ntf-nc"}
UNCLEAR_TEXT = ("Non-commercial use only: the originating agency's terms for these records are not documented or do not "
                "clearly cover derived constants; commercial use needs the data owner's permission.")
# the upstream page that sets each restriction (review C1: UNAM's is the SMN site footer)
TERMS = {lid: GESLA_TERMS for lid in NON_COMMERCIAL} | {
    "gesla-unam-nc": "https://www.mareografico.unam.mx/index.php/informacion/",
    "gesla-bom-nc": "https://www.bom.gov.au/other/copyright.shtml",
    "gesla-bodc-nc": "https://www.bodc.ac.uk/data/documents/nodb/267795/",
    "gesla-sanho-nc": "https://uhslc.soest.hawaii.edu/data/",
    "gesla-uhslc-nc": "https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.nodc:0019568",
    "gesla-jodc-nc": "https://www.jodc.go.jp/jodcweb/JDOSS/index.html",
    "gesla-cdwr-nc": "https://cdec.water.ca.gov/intro.html",
    "gesla-chs-withheld": "https://www.dfo-mpo.gc.ca/terms-avis/terms-conditions-avis-eng.htm",
    "gesla-uhslc-jma-nc": "https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.nodc:0019568",
    "gesla-uhslc-ntf-nc": "https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.nodc:0019568"}
# each RESTRICTED row quotes its own clause
CLAUSE = {"gesla-bom-nc": "You must not supply it to any other person or use it for any commercial purpose",
          "gesla-bodc-nc": "Users should not give any data or products to third parties without prior consent from BODC",
          "gesla-sanho-nc": "SANHO requests that anyone downloading this data request permission to use and/or reproduce "
                            "the information",
          "gesla-chs-withheld": "please contact CHS directly",
          "gesla-jodc-nc": "Not to reproduce the data and provide it to the third party without permission"}
EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "example-1.0.json"


def doc(rid, contributor):
    return {"record_id": rid, "contributor": contributor}


def test_every_contributor_has_its_licence():
    with open(ROLES_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    by = contributor_licences()
    assert set(by) == {r["contributor"] for r in rows}
    for r in rows:
        want = WANT[r["contributor"]]
        assert by[r["contributor"]] == want, r
        if r["contributor"] in ALLOWED or r["contributor"] == "UNAM":
            assert r["licence_class"] == "research_consultancy", r


def test_licence_rows_are_schema_valid_and_non_commercial_ones_say_why():
    rows = {x["licence_id"]: x for x in load_licences()}
    from otc_pipeline.gesla_licences import record_overrides
    assert set(rows) == set(contributor_licences().values()) | {v[1] for v in record_overrides().values()}
    for lid, x in rows.items():
        assert x["commercial_use"] is (lid not in NON_COMMERCIAL), lid
        if not x["commercial_use"]:
            assert x["restriction"].startswith("Non-commercial use only") and x["terms_url"] == TERMS[lid]
    assert set(json.loads(LICENCES_JSON.read_text())["sources"]) == set(rows)
    for lid in ("gesla-uhslc-nc", "gesla-cdwr-nc", "gesla-ieo-nc", "gesla-unchecked-nc"):
        assert rows[lid]["restriction"] == UNCLEAR_TEXT, lid
    for lid, clause in CLAUSE.items():
        assert clause in rows[lid]["restriction"], lid


def test_bz_records_get_their_licence_and_the_noaa_control_is_unchanged():
    docs = [doc("venezia-vene-ita-cv", "CV"), doc("bakar-bak-hrv-uz", "UZ"),
            doc("a121tg-a12-nld-cmems", "CMEMS"), doc("aberdeen-9441187-usa-noaa", "NOAA")]
    licences, ids = gesla_licences(docs)
    assert ids == {"venezia-vene-ita-cv": "gesla-cv-nc", "bakar-bak-hrv-uz": "gesla-uz-nc",
                   "a121tg-a12-nld-cmems": "gesla-cmems-nc", "aberdeen-9441187-usa-noaa": "gesla-noaa"}
    assert {x["licence_id"] for x in licences} >= set(ids.values())


def test_an_unknown_contributor_is_refused_never_defaulted():
    with pytest.raises(ValueError, match="contributor 'NEW_AGENCY' is not in contributor_roles.csv"):
        gesla_licences([doc("aberdeen-9441187-usa-noaa", "NOAA"), doc("y-2-xyz-new", "NEW_AGENCY")])
    with pytest.raises(ValueError, match="contributor None"):
        gesla_licences([{"record_id": "z"}])


def _roles(tmp_path, text):
    p = tmp_path / "roles.csv"
    p.write_text(text)
    return p


def test_a_blank_or_missing_licence_id_is_refused(tmp_path):
    with pytest.raises(ValueError, match="'CMEMS' has no licence_id"):
        contributor_licences(_roles(tmp_path, "contributor,role,licence_class,licence_page_entry,licence_id\n"
                                              "CMEMS,redistributor,research_only,CMEMS,\n"))
    with pytest.raises(ValueError, match="no licence_id column"):
        contributor_licences(_roles(tmp_path, "contributor,role,licence_class\nCMEMS,redistributor,research_only\n"))
    with pytest.raises(ValueError, match="listed twice"):
        contributor_licences(_roles(tmp_path, "contributor,licence_id\nCV,gesla-cv-nc\nCV,gesla-derived\n"))


def test_a_licence_id_with_no_row_is_refused(tmp_path):
    roles = _roles(tmp_path, "contributor,licence_id\nCV,gesla-cv-nc-typo\n")
    with pytest.raises(ValueError, match="no row"):
        gesla_licences([doc("venezia-vene-ita-cv", "CV")], roles_path=roles)
    with pytest.raises(ValueError, match="has no licence row"):
        record_licence_ids([doc("a", "CV")], {"CV": "gesla-cv-nc"}, {"gesla-derived": True}, {"a": "research_only"})
    with pytest.raises(ValueError, match="listed twice"):
        record_licence_ids([doc("a", "CV"), doc("a", "CV")], {"CV": "gesla-cv-nc"}, {"gesla-cv-nc": False},
                           {"a": "research_only"}, record_origin=ANY_ORIGIN)


def _licences_file(tmp_path, rows):
    p = tmp_path / "licences.json"
    p.write_text(json.dumps({"licences": rows}))
    return p


def test_a_non_commercial_row_without_restriction_or_terms_is_refused(tmp_path):
    row = {k: v for k, v in next(x for x in load_licences() if x["licence_id"] == "gesla-cmems-nc").items()}
    for key in ("restriction", "terms_url"):
        bad = {k: v for k, v in row.items() if k != key}
        with pytest.raises(ValueError, match=f"'{key}' is a required property"):
            load_licences(_licences_file(tmp_path, [bad]))
    with pytest.raises(ValueError, match="listed twice"):
        load_licences(_licences_file(tmp_path, [row, row]))
    with pytest.raises(ValueError, match="Additional properties"):
        load_licences(_licences_file(tmp_path, [{**row, "note": "x"}]))


# --- the adapter contract: several licences (adapter.py) -------------------------------------------

LIC_A = {"licence_id": "a-open", "spdx": "CC-BY-4.0", "provider": "A", "attribution": "A.", "commercial_use": True}
LIC_B = {"licence_id": "b-nc", "spdx": "CC-BY-NC-4.0", "provider": "B", "attribution": "B.", "commercial_use": False,
         "restriction": "Non-commercial use only.", "terms_url": "https://example.org/terms"}


class _One:
    def licence(self):
        return LIC_A


class _Many:
    def __init__(self, lics):
        self.lics = lics

    def licences(self):
        return self.lics


def _set(rid, lic):
    hint = {"name": rid, "country": "NLD", "lat": 52.0, "lon": 4.0, "type": "reference", "timezone": "Etc/GMT",
            "aliases": {}}
    return ad.SourceSet(station_hint=hint, constant_set={"source_record_id": rid, "licence_id": lic,
                                                         "provenance": {"adapter_version": "1"}})


def _licence_errors(lics, sets):
    errors = []
    fetched = ad.FetchResult(source="s", source_version="1", cache_dir=Path("."), files=())
    attrs = {"source": "s", "source_type": "gauge", "quantity": "water_level", "adapter_version": "1",
             "alias_system": None}
    ad._set_errors(attrs, fetched, sets, {s.constant_set["source_record_id"]: "0" * 64 for s in sets}, {}, lics,
                   set(), errors)
    return [e for e in errors if "licence" in e]


def test_the_contract_allows_several_licences():
    errors = []
    lics = ad._licences(errors, _Many([LIC_A, LIC_B]))
    assert errors == [] and [x["licence_id"] for x in lics] == ["a-open", "b-nc"]
    assert _licence_errors(lics, [_set("r1", "a-open"), _set("r2", "b-nc")]) == []
    assert _licence_errors(lics, [_set("r3", "c-other")]) == \
        ["set 0 (r3): licence_id 'c-other' is not one of the adapter's licences ['a-open', 'b-nc']"]
    assert ad.adapter_licences(_Many([LIC_A, LIC_B])) == [LIC_A, LIC_B]


def test_a_one_licence_adapter_is_unchanged():
    errors = []
    lics = ad._licences(errors, _One())
    assert errors == [] and lics == [LIC_A] and ad.adapter_licences(_One()) == [LIC_A]
    assert _licence_errors(lics, [_set("r1", "a-open")]) == []
    assert _licence_errors(lics, [_set("r2", "b-nc")]) != []


def test_the_contract_refuses_empty_or_duplicate_licences():
    for bad, needle in (([], "one or more licence objects"), ([LIC_A, LIC_A], "more than once"),
                        ([LIC_A, "x"], "one or more licence objects")):
        errors = []
        assert ad._licences(errors, _Many(bad)) is ad._FAILED
        assert len(errors) == 1 and needle in errors[0], errors


def test_commercial_use_and_spdx_agree_both_ways():
    # B5: a non-commercial row carries an NC SPDX id, and a commercial row never does
    for x in load_licences():
        assert ("-NC-" in x["spdx"]) is (not x["commercial_use"]), x["licence_id"]


def test_unam_follows_the_smn_footer():
    # C1/C3: the SMN site allows non-profit reproduction only; both SMN contributors share one NC row
    row = next(x for x in load_licences() if x["licence_id"] == "gesla-unam-nc")
    assert row["commercial_use"] is False and "fines no lucrativos" in row["restriction"]
    src = json.loads(LICENCES_JSON.read_text())["sources"]
    assert "fines no lucrativos" in src["gesla-unam-nc"] and "gesla-derived" not in src
    _, ids = gesla_licences([doc("acapulco-316-mex-unam_hist", "UNAM_HIST"), doc("acapulcoapi_nivelcbs-41-mex-unam", "UNAM")])
    assert set(ids.values()) == {"gesla-unam-nc"}


def test_the_cmems_attribution_cites_the_product_doi():
    # C5: the CMEMS licence asks for the product DOI in the credit line
    row = next(x for x in load_licences() if x["licence_id"] == "gesla-cmems-nc")
    assert row["attribution"].startswith("Generated using E.U. Copernicus Marine Service Information; "
                                         "https://doi.org/10.48670/moi-00036")


def _inventory(tmp_path, rows):
    p = tmp_path / "inventory.csv"
    p.write_text("file_id,contributor,licence_class\n" + "".join(f"{a},{b},{c}\n" for a, b, c in rows))
    return p


class _AnyOrigin(dict):
    """record_origin stand-in for synthetic records: every record has a neutral originator."""
    def __contains__(self, key):
        return True

    def __getitem__(self, key):
        return ("TEST ORIGINATOR", "")

    def get(self, key, default=None):
        return ("TEST ORIGINATOR", "")


ANY_ORIGIN = _AnyOrigin()


def _origins(tmp_path, rows):
    """A record_originators.csv for synthetic records: rows of (record_id, gesla_originator, uhslc_data_source),
    with the sidecar that pins it (as otc_pipeline.gesla_originators writes it)."""
    p = tmp_path / "origins.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["record_id", "contributor", "gesla_originator", "uhslc_data_source", "uhslc_basis"])
        w.writerows((r, "", g, u, "") for r, g, u in rows)
    p.with_suffix(".sources.json").write_text(json.dumps({"csv_sha256": hashlib.sha256(p.read_bytes()).hexdigest()}))
    return p


def _origins_for_inventory(tmp_path, inv):
    with open(inv, newline="") as f:
        return _origins(tmp_path, [(r["file_id"], "TEST ORIGINATOR", "") for r in csv.DictReader(f)])


def _no_overrides(tmp_path):
    p = tmp_path / "no-overrides.csv"
    p.write_text("record_id,contributor,licence_id,reason\n")
    return p


def test_the_inventory_licence_class_is_cross_checked(tmp_path):
    # A3: a research_only or unlisted record never gets a commercial licence; a record not in the inventory is refused
    inv = _inventory(tmp_path, [("a121tg-a12-nld-cmems", "CMEMS", "research_only"), ("c-1-usa-crms", "CRMS", "unlisted"),
                                ("x-1-usa-noaa", "NOAA", "research_consultancy")])
    ok_docs = [doc("a121tg-a12-nld-cmems", "CMEMS"), doc("c-1-usa-crms", "CRMS"), doc("x-1-usa-noaa", "NOAA")]
    _, ids = gesla_licences(ok_docs, inventory_path=inv, overrides_path=_no_overrides(tmp_path),
                       originators_path=_origins_for_inventory(tmp_path, inv))
    assert ids["x-1-usa-noaa"] == "gesla-noaa"
    for c, line in (("CMEMS", "CMEMS,redistributor,research_only,CMEMS,gesla-noaa"),
                    ("CRMS", "CRMS,redistributor,unlisted,,gesla-noaa")):
        text = ROLES_CSV.read_text().splitlines()
        text = [line if r.startswith(f"{c},") else r for r in text]
        roles = _roles(tmp_path, "\n".join(text) + "\n")
        with pytest.raises(ValueError, match="commercial licence 'gesla-noaa'"):
            gesla_licences(ok_docs, roles_path=roles, inventory_path=inv, overrides_path=_no_overrides(tmp_path),
                       originators_path=_origins_for_inventory(tmp_path, inv))
    with pytest.raises(ValueError, match="not in the GESLA inventory"):
        gesla_licences([doc("y-2-usa-noaa", "NOAA")], inventory_path=inv, overrides_path=_no_overrides(tmp_path),
                       originators_path=_origins_for_inventory(tmp_path, inv))
    # unlisted TTW/RWS_HIST are commercial only through their sourced unlisted_open entry
    inv2 = _inventory(tmp_path, [("auckland-auct-nzl-ttw", "TTW", "unlisted"), ("c-1-usa-crms", "CRMS", "unlisted")])
    _, ids = gesla_licences([doc("auckland-auct-nzl-ttw", "TTW")], inventory_path=inv2, overrides_path=_no_overrides(tmp_path),
                       originators_path=_origins_for_inventory(tmp_path, inv2))
    assert ids == {"auckland-auct-nzl-ttw": "gesla-ttw"}
    commercial = {"gesla-ttw": True, "gesla-derived": True}
    with pytest.raises(ValueError, match="commercial licence 'gesla-derived'"):     # allowlisted contributor, other id
        record_licence_ids([doc("auckland-auct-nzl-ttw", "TTW")], {"TTW": "gesla-derived"}, commercial,
                           {"auckland-auct-nzl-ttw": "unlisted"}, {"TTW": "gesla-ttw"}, record_origin=ANY_ORIGIN)
    with pytest.raises(ValueError, match="commercial licence 'gesla-ttw'"):         # not allowlisted
        record_licence_ids([doc("auckland-auct-nzl-ttw", "TTW")], {"TTW": "gesla-ttw"}, commercial,
                           {"auckland-auct-nzl-ttw": "unlisted"}, {}, record_origin=ANY_ORIGIN)
    with pytest.raises(ValueError, match="'research_only'"):                         # research_only: never
        record_licence_ids([doc("a121tg-a12-nld-cmems", "CMEMS")], {"CMEMS": "gesla-ttw"}, commercial,
                           {"a121tg-a12-nld-cmems": "research_only"}, {"CMEMS": "gesla-ttw"}, record_origin=ANY_ORIGIN)
    assert json.loads(LICENCES_JSON.read_text())["unlisted_open"] | {"_why": None} == \
        {"_why": None, "TTW": "gesla-ttw", "RWS_HIST": "gesla-rws-hist"}
    # the real inventory and roles: every record of every contributor passes the check today
    with open(ROLES_CSV, newline="") as f:
        roles = list(csv.DictReader(f))
    with open(INVENTORY_CSV, newline="") as f:
        real = [doc(r["file_id"], r["contributor"]) for r in csv.DictReader(f)]
    _, ids = gesla_licences(real)
    assert len(ids) == len(real) and {ids[d["record_id"]] for d in real} == {r["licence_id"] for r in roles} | {"gesla-sanho-nc", "gesla-uhslc-jma-nc", "gesla-uhslc-ntf-nc"}


# --- the contract end to end: run_contract and assemble_release with two licences (B1) ------------

def _two_licence_adapter(tmp_path, monkeypatch):
    ex = json.loads(EXAMPLE.read_text())
    base = next(cs for st in ex["stations"] for cs in st.get("constant_sets", []) if cs["set_id"] == "OTC-EXAMPLE-0016/gesla-fit")
    conv = next(c for c in ex["conventions"] if c["convention_id"] == "noaa-greenwich-schureman-v1")
    lics = [x for x in ex["licences"] if x["licence_id"] in ("gesla-derived", "gesla-cmems-nc")]
    (tmp_path / "a.json").write_bytes(b"{}")
    sha = hashlib.sha256(b"{}").hexdigest()
    fetched = ad.FetchResult(source="gesla-fit", source_version="4.1", cache_dir=tmp_path,
                             files=(ad.InputFile("a.json", sha, 2, "https://example.org/a", "2026-10-10T00:00:00Z"),))

    def one(rid, lic):
        cs = {k: copy.deepcopy(base[k]) for k in ("source", "source_type", "quantity", "source_version", "record_span",
                                                   "time_base", "constituents")}
        cs.update(source_record_id=rid, licence_id=lic, convention_id=conv["convention_id"], datum={"msl_offset_m": 0.0},
                  qc_status="ok", provenance={"adapter_version": "1", "input_sha256": [sha]})
        return ad.SourceSet(station_hint={"name": rid, "country": "NOR", "lat": 61.0, "lon": 5.5, "type": "reference",
                                          "timezone": "Europe/Oslo", "aliases": {"gesla": [rid]}}, constant_set=cs)

    class TwoLicences:
        source, source_type, quantity, adapter_version, contract_sample = "gesla-fit", "gauge", "water_level", "1", ("r-a",)

        def convention(self):
            return conv

        def licences(self):
            return lics

        def watch_keys(self, f):
            return {"r-a": "0" * 64, "r-b": "1" * 64}

        def sets(self, f):
            return [one("r-a", "gesla-derived"), one("r-b", "gesla-cmems-nc")]

    spec = types.SimpleNamespace(source_type="gauge", quantity="water_level", output="sets")
    monkeypatch.setattr(ad, "_sources", lambda: types.SimpleNamespace(EXPECTED={"gesla-fit": spec}))
    return TwoLicences(), fetched


# The contract shell carries no astro tables (KP0b's), so a convention with f and u always reports this one.
ASTRO_ONLY = ["release: (root): 'astro_tables' is a required property"]


def test_run_contract_and_assemble_release_carry_both_licences(tmp_path, monkeypatch):
    adapter, fetched = _two_licence_adapter(tmp_path, monkeypatch)
    run = ad.run_contract(adapter, fetched)
    assert run.errors == ASTRO_ONLY
    release = ad.assemble_release(adapter, list(adapter.sets(fetched)), fetched)
    assert [x["licence_id"] for x in release["licences"]] == ["gesla-derived", "gesla-cmems-nc"]
    assert sorted(cs["licence_id"] for st in release["stations"] for cs in st["constant_sets"]) == \
        ["gesla-cmems-nc", "gesla-derived"]


def test_an_adapter_with_both_licence_and_licences_is_refused(tmp_path, monkeypatch):
    # A5: licence() is never silently ignored
    adapter, fetched = _two_licence_adapter(tmp_path, monkeypatch)
    type(adapter).licence = lambda self: LIC_A
    with pytest.raises(ValueError, match="defines both licence\\(\\) and licences\\(\\)"):
        ad.adapter_licences(adapter)
    errors = []
    assert ad._licences(errors, adapter) is ad._FAILED
    assert errors == ["the adapter defines both licence() and licences(): define one"]
    assert "the adapter defines both licence() and licences(): define one" in ad.run_contract(adapter, fetched).errors


@pytest.mark.parametrize("bad", ["", "Research_Only", "research only", "unlisted ", "research_consultancy ",
                                 "Research and consultancy", "new_gesla_class"])
def test_an_unknown_inventory_class_is_refused(tmp_path, bad):
    # fix-r2 #1: an exact allow-list of the inventory classes; a stray value never reaches any licence
    inv = _inventory(tmp_path, [("c-1-usa-crms", "CRMS", bad)])
    commercial = {"gesla-derived": True, "gesla-unlisted-nc": False}
    for lid in commercial:
        with pytest.raises(ValueError, match="unknown GESLA licence class"):
            record_licence_ids([doc("c-1-usa-crms", "CRMS")], {"CRMS": lid}, commercial,
                               {"c-1-usa-crms": bad}, {}, record_origin=ANY_ORIGIN)
    with pytest.raises(ValueError, match="unknown GESLA licence class"):
        gesla_licences([doc("c-1-usa-crms", "CRMS")], inventory_path=inv, overrides_path=_no_overrides(tmp_path),
                       originators_path=_origins_for_inventory(tmp_path, inv))


def test_a_protocol_subclass_with_only_licences_is_not_refused():
    # fix-r2 #4: the Adapter Protocol's licence() stub is inherited, not defined by the adapter
    class OnlyLicences(ad.Adapter):
        def licences(self):
            return [LIC_A, LIC_B]

    class Both(ad.Adapter):
        def licence(self):
            return LIC_A

        def licences(self):
            return [LIC_A, LIC_B]

    class OnlyLicence(ad.Adapter):
        def licence(self):
            return LIC_A

    errors = []
    assert ad.adapter_licences(OnlyLicences()) == [LIC_A, LIC_B]
    assert ad._licences(errors, OnlyLicences()) == [LIC_A, LIC_B] and errors == []
    assert ad.adapter_licences(OnlyLicence()) == [LIC_A]
    assert ad._licences(errors, OnlyLicence()) == [LIC_A] and errors == []
    with pytest.raises(ValueError, match="defines both"):
        ad.adapter_licences(Both())
    assert ad._licences(errors, Both()) is ad._FAILED and errors == [ad.BOTH_LICENCE_METHODS]


def test_the_unlisted_open_note_does_not_name_unam_hist_as_open():
    # fix-r2 #5: UNAM_HIST's publisher terms are non-profit; it is never an unlisted_open exception
    why = json.loads(LICENCES_JSON.read_text())["unlisted_open"]["_why"]
    naming = [x for x in why.split(". ") if "UNAM_HIST" in x]
    assert naming and all("non-commercial" in x and "gesla-unam-nc" in x for x in naming), why


# --- round 3: originator terms ----------------------------------------------------------------------

def test_meds_is_withheld_under_the_chs_terms():
    # round 5 (confirm-C #2): CHS owns the data; its licence forbids derivative products for commercial purposes
    # and giving the data to a third party, so MEDS records are withheld; no OGL-Canada claim remains
    rows = {x["licence_id"]: x for x in load_licences()}
    row = rows["gesla-chs-withheld"]
    assert row["commercial_use"] is False and row["restriction"].startswith(
        "Non-commercial use only, and withheld: DFO's terms say 'If you wish to reproduce Canadian Hydrographic Service")
    assert "gesla-meds-ogl-ca" not in rows and not any("OGL" in x["spdx"] for x in rows.values())
    src = json.loads(LICENCES_JSON.read_text())["sources"]["gesla-chs-withheld"]
    assert "charts.gc.ca/copyright-droitdauteur" in src and "tides.gc.ca/en/licence-agreement" in src
    assert "delivered via CHS' website" in src and "This product is not to be used for navigation" in src
    assert "SPDX has no id" not in LICENCES_JSON.read_text()


def test_each_allowed_originator_has_its_own_licence_and_credit():
    # round 4: no set carries a credit that belongs to another originator
    rows = {x["licence_id"]: x for x in load_licences()}
    src = json.loads(LICENCES_JSON.read_text())["sources"]
    assert "gesla-derived" not in rows and "gesla-derived" not in set(contributor_licences().values())
    urls = {"gesla-noaa": "tidesandcurrents.noaa.gov/disclaimers", "gesla-usgs": "usgs.gov/information-policies",
            "gesla-rws": "rijkswaterstaatdata.nl", "gesla-wsv": "pegelonline.wsv.de", "gesla-refmar": "REFMAR_RONIM",
            "gesla-smhi": "smhi.se/data", "gesla-ispra": "dati.isprambiente.it"}
    for c in ALLOWED:
        lid = f"gesla-{c.lower()}"
        row = rows[lid]
        assert row["commercial_use"] is True and row["spdx"] == SPDX[lid], lid
        assert CREDIT[c] in row["attribution"], lid
        assert [o for o, tok in CREDIT.items() if o != c and tok in row["attribution"]] == [], lid
        assert urls[lid] in src[lid], lid
    assert "REFMAR. http://dx.doi.org/10.17183/REFMAR#RONIM" in rows["gesla-refmar"]["attribution"]
    assert "REFMAR. http://dx.doi.org/10.17183/REFMAR#PARTENAIRES" in rows["gesla-refmar"]["attribution"]
    assert rows["gesla-refmar"]["url"] == "https://www.data.gouv.fr/pages/legal/licences/etalab-2.0"
    assert "A21_CCBY40" in src["gesla-ispra"] and "isprambiente.gov.it/it/note-legali" in src["gesla-ispra"]
    assert "WSV office" in src["gesla-wsv"] and "142" in src["gesla-wsv"]
    assert "modified" in rows["gesla-smhi"]["attribution"]
    assert "not official government material" in rows["gesla-noaa"]["attribution"]
    assert "PARTENAIRES" in src["gesla-refmar"] and "none is a station of Hamburg Port Authority" in src["gesla-wsv"]
    # every allowed contributor's records get only its own licence, so only its own credit
    with open(INVENTORY_CSV, newline="") as f:
        real = [doc(r["file_id"], r["contributor"]) for r in csv.DictReader(f) if r["contributor"] in ALLOWED]
    _, ids = gesla_licences(real)
    for d in real:
        att = rows[ids[d["record_id"]]]["attribution"]
        assert [o for o, tok in CREDIT.items() if o != d["contributor"] and tok in att] == [], d
    assert all(c in src["gesla-unchecked-nc"] for c in UNCHECKED)


def test_withheld_licences_name_their_reason(tmp_path):
    from otc_pipeline.gesla_licences import withheld_licences
    w = withheld_licences()
    assert set(w) == WITHHELD
    assert "data or products" in w["gesla-bodc-nc"] and "request permission to use and/or reproduce" in w["gesla-sanho-nc"]
    assert "provide it to the third party without permission" in w["gesla-jodc-nc"]
    raw = json.loads(LICENCES_JSON.read_text())
    for bad, needle in (({"gesla-nope": "x"}, "withheld licence 'gesla-nope' has no licence row"),
                        ({"gesla-bodc-nc": " "}, "withheld licence 'gesla-bodc-nc' has no reason")):
        p = tmp_path / "licences.json"
        p.write_text(json.dumps({**raw, "withheld": bad}))
        with pytest.raises(ValueError, match=needle):
            withheld_licences(p)


SANHO = {"walvis_bay-220-nam-uhslc_fd", "walvis_bay-220a-nam-uhslc_rq", "cape_town-704-zaf-uhslc_fd", "cape_town-704a-zaf-uhslc_rq", "durban-181-zaf-uhslc_fd",
         "durban-181a-zaf-uhslc_rq", "east_london-187-zaf-uhslc_fd", "east_london-187a-zaf-uhslc_rq",
         "knysna-186-zaf-uhslc_fd", "knysna-186a-zaf-uhslc_rq", "luderitz-702-zaf-uhslc_fd", "luderitz-702a-zaf-uhslc_rq",
         "marion_island-920-zaf-uhslc_fd", "mossel_bay-185-zaf-uhslc_fd", "mossel_bay-185a-zaf-uhslc_rq",
         "port_elizabeth-184-zaf-uhslc_fd", "port_elizabeth-184a-zaf-uhslc_rq", "port_nolloth-701-zaf-uhslc_fd",
         "port_nolloth-701a-zaf-uhslc_rq", "richards_bay-188-zaf-uhslc_fd", "richards_bay-188a-zaf-uhslc_rq",
         "saldahna_bay-703-zaf-uhslc_fd", "saldahna_bay-703a-zaf-uhslc_rq", "simons_town-221-zaf-uhslc_fd",
         "simons_town-221a-zaf-uhslc_rq"}


def test_the_uhslc_south_africa_records_are_overridden_per_record():
    from otc_pipeline.gesla_licences import record_overrides
    over = record_overrides()
    assert {r for r, v in over.items() if v[1] == "gesla-sanho-nc"} == SANHO and len(SANHO) == 25
    docs = [doc(r, "UHSLC_FD" if r.endswith("_fd") else "UHSLC_RQ") for r in sorted(SANHO)] + \
        [doc("abashiri-347-jpn-uhslc_fd", "UHSLC_FD")]
    _, ids = gesla_licences(docs)
    assert {ids[r] for r in SANHO} == {"gesla-sanho-nc"} and ids["abashiri-347-jpn-uhslc_fd"] == "gesla-jodc-nc"
    assert gesla_licences([doc("newlyn_cornwall-294-gbr-uhslc_fd", "UHSLC_FD")])[1] == \
        {"newlyn_cornwall-294-gbr-uhslc_fd": "gesla-bodc-nc"}       # by originator: BODC via UHSLC


def _overrides(tmp_path, text):
    p = tmp_path / "overrides.csv"
    p.write_text(text)
    return p


def test_a_bad_record_override_is_refused(tmp_path):
    from otc_pipeline.gesla_licences import record_overrides
    head = "record_id,contributor,licence_id,reason\n"
    d = [doc("cape_town-704-zaf-uhslc_fd", "UHSLC_FD")]
    with pytest.raises(ValueError, match="listed twice"):
        record_overrides(_overrides(tmp_path, head + "a,UHSLC_FD,gesla-sanho-nc,x\na,UHSLC_FD,gesla-sanho-nc,x\n"))
    with pytest.raises(ValueError, match="no licence_id"):
        record_overrides(_overrides(tmp_path, head + "a,UHSLC_FD,,x\n"))
    with pytest.raises(ValueError, match="needs columns"):
        record_overrides(_overrides(tmp_path, "record_id,licence_id\na,gesla-sanho-nc\n"))
    with pytest.raises(ValueError, match="override names contributor 'UHSLC_RQ'"):
        gesla_licences(d, overrides_path=_overrides(tmp_path, head + "cape_town-704-zaf-uhslc_fd,UHSLC_RQ,gesla-sanho-nc,x\n"))
    with pytest.raises(ValueError, match="has no licence row"):
        gesla_licences(d, overrides_path=_overrides(tmp_path, head + "cape_town-704-zaf-uhslc_fd,UHSLC_FD,gesla-nope,x\n"))
    with pytest.raises(ValueError, match="not in the GESLA inventory"):
        gesla_licences(d, overrides_path=_overrides(tmp_path, head + "no-such-record,UHSLC_FD,gesla-sanho-nc,x\n"))


def test_a_duplicate_inventory_file_id_is_refused(tmp_path):
    inv = _inventory(tmp_path, [("x-1-usa-noaa", "NOAA", "research_consultancy"),
                                ("x-1-usa-noaa", "NOAA", "research_only")])
    with pytest.raises(ValueError, match="file_id 'x-1-usa-noaa' is listed twice"):
        gesla_licences([doc("x-1-usa-noaa", "NOAA")], inventory_path=inv, overrides_path=_no_overrides(tmp_path),
                       originators_path=_origins_for_inventory(tmp_path, inv))


# --- round 5: overrides by originator, constrained ---------------------------------------------------------

OVERRIDE_COUNTS = {("CMEMS", "gesla-bodc-nc"): 46, ("DA_IDH", "gesla-bodc-nc"): 4, ("NOC", "gesla-bodc-nc"): 5,
                   ("UHSLC_FD", "gesla-bodc-nc"): 20, ("UHSLC_FD", "gesla-bom-nc"): 36,
                   ("UHSLC_FD", "gesla-chs-withheld"): 9, ("UHSLC_FD", "gesla-jodc-nc"): 9,
                   ("UHSLC_FD", "gesla-sanho-nc"): 13, ("UHSLC_FD", "gesla-uhslc-jma-nc"): 11,
                   ("UHSLC_RQ", "gesla-bodc-nc"): 29, ("UHSLC_RQ", "gesla-bom-nc"): 56,
                   ("UHSLC_RQ", "gesla-chs-withheld"): 9, ("UHSLC_RQ", "gesla-jodc-nc"): 15,
                   ("UHSLC_RQ", "gesla-sanho-nc"): 12, ("UHSLC_RQ", "gesla-uhslc-jma-nc"): 13,
                   ("UHSLC_RQ", "gesla-uhslc-ntf-nc"): 9}
AG_LIC = {"BODC": "gesla-bodc-nc", "SANHO": "gesla-sanho-nc", "JODC": "gesla-jodc-nc", "CHS": "gesla-chs-withheld",
          "BOM": "gesla-bom-nc", "JMA (Public Data License 1.0)": "gesla-uhslc-jma-nc",
          "NTF (acknowledgement required)": "gesla-uhslc-ntf-nc"}


def test_the_overrides_withhold_by_originator_and_name_their_evidence():
    with open(OVERRIDES, newline="") as f:
        rows = list(csv.DictReader(f))
    assert collections.Counter((r["contributor"], r["licence_id"]) for r in rows) == OVERRIDE_COUNTS
    for r in rows:
        ag = r["reason"].split(":")[0].removeprefix("originator ")
        assert AG_LIC[ag] == r["licence_id"], r
        assert "GESLA ORIGINATOR" in r["reason"] or "UHSLC lists" in r["reason"], r
        assert r["contributor"] not in ("BODC", "BOM", "MEDS", "JODC_JMA", "JODC_PAHB", "JODC_JCG", "JODC_GIAJ"), r


HEAD = "record_id,contributor,licence_id,reason\n"


def test_an_override_may_only_narrow_or_follow_the_documented_originator(tmp_path):
    # confirm-A #2 and #8 (round 5), final-confirm-A #5/#6 (round 8): the commercial-override check compares the
    # record's OWN originator field, and its reason must quote the GESLA ORIGINATOR; each half is tested alone
    nl = [doc("newlyn_cornwall-294-gbr-uhslc_fd", "UHSLC_FD")]
    with pytest.raises(ValueError, match="own originator .* is no documented originator of 'gesla-noaa'"):
        gesla_licences(nl, overrides_path=_overrides(tmp_path, HEAD + "newlyn_cornwall-294-gbr-uhslc_fd,UHSLC_FD,gesla-noaa,"
                                                                     "originator NOAA: GESLA ORIGINATOR 'National Ocean "
                                                                     "Service (NOS)'\n"))
    pa = [doc("port_angeles_wa-584a-usa-uhslc_rq", "UHSLC_RQ")]
    _, ids = gesla_licences(pa, overrides_path=_overrides(
        tmp_path, HEAD + "port_angeles_wa-584a-usa-uhslc_rq,UHSLC_RQ,gesla-noaa,originator NOAA: GESLA ORIGINATOR "
                         "'National Ocean Service (NOS)'\n"))
    assert ids == {"port_angeles_wa-584a-usa-uhslc_rq": "gesla-noaa"}
    with pytest.raises(ValueError, match="does not quote the GESLA ORIGINATOR"):
        gesla_licences(pa, overrides_path=_overrides(
            tmp_path, HEAD + "port_angeles_wa-584a-usa-uhslc_rq,UHSLC_RQ,gesla-noaa,National Ocean Service\n"))
    with pytest.raises(ValueError, match="moves it out of the withheld licence 'gesla-bodc-nc'"):
        gesla_licences([doc("newlyn-new-gbr-bodc", "BODC")],
                       overrides_path=_overrides(tmp_path, HEAD + "newlyn-new-gbr-bodc,BODC,gesla-unchecked-nc,typo\n"))
    from otc_pipeline.gesla_licences import record_overrides
    with pytest.raises(ValueError, match="has no licence_id, contributor or reason"):
        record_overrides(_overrides(tmp_path, HEAD + "a,UHSLC_FD,gesla-sanho-nc, \n"))
    # an unlisted TTW record whose own originator is NOAA still may not go to another commercial licence
    with pytest.raises(ValueError, match="maps to the commercial licence 'gesla-noaa'"):
        gesla_licences([doc("auckland-auct-nzl-ttw", "TTW")], overrides_path=_overrides(
            tmp_path, HEAD + "auckland-auct-nzl-ttw,TTW,gesla-noaa,originator NOAA: GESLA ORIGINATOR 'National Ocean "
                             "Service'\n"), originators_path=_origins(tmp_path, [("auckland-auct-nzl-ttw",
                                                                                  "National Ocean Service", "")]))


def test_jma_records_via_uhslc_are_published_jodc_records_are_withheld():
    # rounds 7-8: the ORIGINATOR decides. JMA-originated UHSLC records get gesla-uhslc-jma-nc (JMA PDL 1.0);
    # JODC-originated ones and their FD twins are withheld, and so is Wakkanai 360A (JODC before 2011) with its FD;
    # Malakal and Saipan FD are UHSLC series (007B/028B), not JODC; JODC_JMA via JODC stays withheld
    want = {"aburatsu-354a-jpn-uhslc_rq": "gesla-uhslc-jma-nc", "aburatsu-354-jpn-uhslc_fd": "gesla-uhslc-jma-nc",
            "aburatsu-ma41-jpn-jodc_jma": "gesla-jodc-nc", "abashiri-347a-jpn-uhslc_rq": "gesla-jodc-nc",
            "abashiri-347-jpn-uhslc_fd": "gesla-jodc-nc", "naze-359-jpn-uhslc_fd": "gesla-jodc-nc",
            "wakkanai-360a-jpn-uhslc_rq": "gesla-jodc-nc", "wakkanai-360-jpn-uhslc_fd": "gesla-jodc-nc",
            "malakal-007-plw-uhslc_fd": "gesla-uhslc-nc", "saipan-028-usa-uhslc_fd": "gesla-uhslc-nc"}
    _, ids = gesla_licences([doc(r, "JODC_JMA" if r.endswith("jodc_jma") else
                                 "UHSLC_FD" if r.endswith("_fd") else "UHSLC_RQ") for r in want])
    assert ids == want
    with open(OVERRIDES, newline="") as f:
        rows = list(csv.DictReader(f))
    jodc = [r for r in rows if r["licence_id"] == "gesla-jodc-nc"]
    jma = [r for r in rows if r["licence_id"] == "gesla-uhslc-jma-nc"]
    assert len(jodc) == 24 and len(jma) == 24
    assert all("Japan Meteorological Agency" in r["reason"] and "Japan Oceanographic" not in r["reason"] for r in jma)
    att = next(x for x in load_licences() if x["licence_id"] == "gesla-uhslc-jma-nc")["attribution"]
    assert "Japan Meteorological Agency" in att and "Edited:" in att and "UHSLC" in att
    src = json.loads(LICENCES_JSON.read_text())["sources"]
    assert "Public Data License" in src["gesla-uhslc-jma-nc"] and "jma.go.jp/jma/en/copyright" in src["gesla-uhslc-jma-nc"]
    assert "Derived products are not addressed" not in src["gesla-jodc-nc"] and "other products" in src["gesla-jodc-nc"]


NOC_BODC = {"aden-172-yem-uhslc_fd", "aden-172a-yem-uhslc_rq", "alexandria-807-egy-uhslc_fd", "alexandria-807a-egy-uhslc_rq",
            "ascension-291-gbr-uhslc_fd", "ascension-291a-gbr-uhslc_rq", "ascension-asc-gbr-noc",
            "chabahar-915-irn-uhslc_fd", "chabahar-915a-irn-uhslc_rq", "djibouti-119-dji-uhslc_fd",
            "djibouti-119a-dji-uhslc_rq", "faraday-700-ata-uhslc_fd", "faraday-700a-ata-uhslc_rq",
            "inhambane-900-moz-uhslc_fd", "inhambane-900a-moz-uhslc_rq", "karachi-147-pak-uhslc_fd",
            "karachi-147b-pak-uhslc_rq", "king_edward_point-kin-gbr-noc", "lagos-233-nga-uhslc_fd",
            "lagos-233a-nga-uhslc_rq", "lagos-233c-nga-uhslc_rq", "nouakchott-806-mrt-uhslc_fd",
            "nouakchott-806a-mrt-uhslc_rq", "pemba-192-moz-uhslc_fd", "pemba-192c-moz-uhslc_rq", "plymouth-ply-gbr-cmems",
            "pointe_noire-234-cog-uhslc_fd", "pointe_noire-234b-cog-uhslc_rq", "port_stanley-290-gbr-uhslc_fd",
            "port_stanley-290a-gbr-uhslc_rq", "port_stanley-290b-gbr-uhslc_rq", "porterin-por-gbr-cmems",
            "rothera-832a-gbr-uhslc_rq", "rothera-rot-gbr-noc", "st_helena-292-shn-uhslc_fd", "st_helena-292a-shn-uhslc_rq",
            "st_helena-292b-shn-uhslc_rq", "st_helena-sth-gbr-noc", "stanley-sta-gbr-noc", "tristan_da_cunha-722a-gbr-uhslc_rq"}


def test_noc_is_bodc_everywhere_and_plymouth_port_erin_are_withheld():
    # round 8 #1 and #4: NOC/Proudman-originated records on any channel, and the two CMEMS copies at BODC gauges
    with open(OVERRIDES, newline="") as f:
        bodc = {r["record_id"] for r in csv.DictReader(f) if r["licence_id"] == "gesla-bodc-nc"}
    assert NOC_BODC <= bodc
    _, ids = gesla_licences([doc("plymouth-ply-gbr-cmems", "CMEMS"), doc("porterin-por-gbr-cmems", "CMEMS"),
                             doc("stanley-sta-gbr-noc", "NOC")])
    assert set(ids.values()) == {"gesla-bodc-nc"}


def test_every_inventory_record_has_its_originator():
    from otc_pipeline.gesla_licences import record_originators
    orig = record_originators()
    with open(INVENTORY_CSV, newline="") as f:
        inv = {r["file_id"] for r in csv.DictReader(f)}
    assert set(orig) == inv
    assert "Japan Oceanographic Data Center" in orig["wakkanai-360a-jpn-uhslc_rq"][1]
    assert "UH Sea Level Center" in orig["malakal-007-plw-uhslc_fd"][1] and "Japan" not in orig["malakal-007-plw-uhslc_fd"][1]


def test_a_withheld_originator_never_gets_a_published_licence(tmp_path):
    # round 8 #6: the build invariant, on the GESLA ORIGINATOR and on the UHSLC Data_Source; a missing row stops too
    inv = _inventory(tmp_path, [("x-1-usa-noaa", "NOAA", "research_consultancy"),
                                ("y-1-usa-uhslc_rq", "UHSLC_RQ", "research_consultancy")])
    kw = dict(inventory_path=inv, overrides_path=_no_overrides(tmp_path))
    for rid, c, g, u in (("x-1-usa-noaa", "NOAA", "British Oceanographic Data Centre", ""),
                         ("x-1-usa-noaa", "NOAA", "Proudman Oceanographic Laboratory", ""),
                         ("y-1-usa-uhslc_rq", "UHSLC_RQ", "University of Hawaii", "National Tidal Centre, Bureau of Meteorology"),
                         ("y-1-usa-uhslc_rq", "UHSLC_RQ", "Unspecified", "Japan Oceanographic Data Center (before 2011)")):
        with pytest.raises(ValueError, match="names the withheld agency of .* but its licence .* is not withheld"):
            gesla_licences([doc(rid, c)], originators_path=_origins(tmp_path, [(rid, g, u)]), **kw)
    # JMA alone is never withheld: the build asks for the JMA licence, not a withheld one (round 9 #5)
    with pytest.raises(ValueError, match="calls for the licence 'gesla-uhslc-jma-nc'"):
        gesla_licences([doc("y-1-usa-uhslc_rq", "UHSLC_RQ")], originators_path=_origins(
            tmp_path, [("y-1-usa-uhslc_rq", "Japan Meteorological Agency", "Japan Meteorological Agency")]), **kw)
    with pytest.raises(ValueError, match="not in record_originators.csv"):
        gesla_licences([doc("x-1-usa-noaa", "NOAA")], originators_path=_origins(tmp_path, []), **kw)


def test_a_withheld_switch_needs_evidence_and_originator_patterns(tmp_path):
    # final-confirm-A #5: the evidence check (a surviving mutant) and the new patterns requirement
    from otc_pipeline.gesla_licences import withheld_licences
    raw = json.loads(LICENCES_JSON.read_text())
    for key, needle in (("evidence", "has no evidence"), ("originator_patterns", "has no originator_patterns")):
        for bad in ([], None):
            w = json.loads(json.dumps(raw["withheld"]))
            if bad is None:
                del w["gesla-bom-nc"][key]
            else:
                w["gesla-bom-nc"][key] = bad
            p = tmp_path / "licences.json"
            p.write_text(json.dumps({**raw, "withheld": w}))
            with pytest.raises(ValueError, match=needle):
                withheld_licences(p)


# --- round 9 ------------------------------------------------------------------------------------------------

def test_ios_bidston_is_in_the_noc_lineage():
    # round 9 #1: IOS Bidston Laboratory -> Proudman Oceanographic Laboratory -> NOC (NERC C75 POL)
    _, ids = gesla_licences([doc(r, "UHSLC_RQ") for r in ("port_louis-103a-mus-uhslc_rq", "port_louis-103b-mus-uhslc_rq",
                                                         "port_victoria-111a-syc-uhslc_rq")])
    assert set(ids.values()) == {"gesla-bodc-nc"}
    src = json.loads(LICENCES_JSON.read_text())["sources"]["gesla-bodc-nc"]
    assert "vocab.nerc.ac.uk/collection/C75/current/POL" in src and "Institute of Oceanographic Sciences Bidston" in src


def test_ntf_records_stay_published_with_ntf_acknowledgement():
    # round 9 #1: the National Tidal Facility (Flinders) is not BOM
    recs = ("tarawa_betio-002c-kir-uhslc_rq", "wewak-063a-png-uhslc_rq", "rabaul-010a-png-uhslc_rq", "funafuti-025a-tuv-uhslc_rq")
    _, ids = gesla_licences([doc(r, "UHSLC_RQ") for r in recs])
    assert set(ids.values()) == {"gesla-uhslc-ntf-nc"}
    row = next(x for x in load_licences() if x["licence_id"] == "gesla-uhslc-ntf-nc")
    assert "supplied by the National Tidal Facility, The Flinders University of South Australia, Copyright reserved" \
        in row["attribution"] and "Version 5.5" in row["citation"]


def _tampered_table(tmp_path, blank, keep_sidecar=True):
    """The real originator table with `blank` records' originators set to 'Unspecified' and no UHSLC source."""
    from otc_pipeline.gesla_licences import ORIGINATORS_CSV
    p = tmp_path / "record_originators.csv"
    with open(ORIGINATORS_CSV, newline="") as f:
        rows = list(csv.reader(f))
    for r in rows[1:]:
        if r[0] in blank:
            r[2], r[3] = "Unspecified", ""
    with open(p, "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)
    side = json.loads(ORIGINATORS_CSV.with_suffix(".sources.json").read_text())
    if not keep_sidecar:
        side["csv_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    p.with_suffix(".sources.json").write_text(json.dumps(side))
    return p


BLANKED = ("alexandria-807a-egy-uhslc_rq", "alexandria-807-egy-uhslc_fd", "aden-172a-yem-uhslc_rq")


def test_a_hand_edited_originator_table_stops_the_build(tmp_path):
    # round 9 #2 (r8-confirm #2): blank three rows and delete their overrides; the pinned SHA-256 refuses it
    with open(OVERRIDES, newline="") as f:
        text = [ln for ln in f.read().splitlines() if not ln.startswith(BLANKED)]
    ovr = _overrides(tmp_path, "\n".join(text) + "\n")
    docs = [doc(r, "UHSLC_FD" if r.endswith("_fd") else "UHSLC_RQ") for r in BLANKED]
    with pytest.raises(ValueError, match="is not the .* pinned in record_originators.sources.json"):
        gesla_licences(docs, overrides_path=ovr, originators_path=_tampered_table(tmp_path, BLANKED))
    with pytest.raises(ValueError, match="no sidecar"):
        p = tmp_path / "x" / "record_originators.csv"
        p.parent.mkdir()
        p.write_bytes(b"record_id,contributor,gesla_originator,uhslc_data_source,uhslc_basis\n")
        gesla_licences(docs, overrides_path=ovr, originators_path=p)
    blank = _origins(tmp_path, [("x-1-usa-noaa", " ", "")])
    inv = _inventory(tmp_path, [("x-1-usa-noaa", "NOAA", "research_consultancy")])
    with pytest.raises(ValueError, match="has an empty gesla_originator"):
        gesla_licences([doc("x-1-usa-noaa", "NOAA")], inventory_path=inv, overrides_path=_no_overrides(tmp_path),
                       originators_path=blank)


@pytest.mark.skipif(not (os.environ.get("OTC_GESLA_METADATA") and os.environ.get("OTC_UHSLC_YAML_DIR")),
                    reason="needs OTC_GESLA_METADATA (GESLA4-1_ALL.csv) and OTC_UHSLC_YAML_DIR (UHSLC metadata snapshot)")
def test_the_originator_table_regenerates_byte_for_byte_from_its_pinned_sources():
    from otc_pipeline import gesla_originators as go
    from otc_pipeline.gesla_licences import ORIGINATORS_CSV
    meta, yd = Path(os.environ["OTC_GESLA_METADATA"]), Path(os.environ["OTC_UHSLC_YAML_DIR"])
    side = json.loads(ORIGINATORS_CSV.with_suffix(".sources.json").read_text())
    assert hashlib.sha256(meta.read_bytes()).hexdigest() == side["gesla_metadata"]["sha256"]
    assert go.snapshot_sha256(yd) == (side["uhslc_metadata_snapshot"]["sha256"], side["uhslc_metadata_snapshot"]["files"])
    assert go.build_csv(meta, yd) == ORIGINATORS_CSV.read_bytes()


def test_a_matched_agency_needs_its_own_withheld_licence(tmp_path):
    # round 9 #3: a BODC-originated record under another withheld licence is refused (the report must name its owner)
    inv = _inventory(tmp_path, [("x-1-gbr-cmems", "CMEMS", "research_only")])
    org = _origins(tmp_path, [("x-1-gbr-cmems", "National Oceanography Centre", "")])
    ovr = _overrides(tmp_path, HEAD + "x-1-gbr-cmems,CMEMS,gesla-chs-withheld,originator CHS: mistaken\n")
    with pytest.raises(ValueError, match=r"names the withheld agency of \['gesla-bodc-nc'\], but its licence "
                                         r"'gesla-chs-withheld' is not that one"):
        gesla_licences([doc("x-1-gbr-cmems", "CMEMS")], inventory_path=inv, overrides_path=ovr, originators_path=org)


def test_agency_patterns_match_any_case(tmp_path):
    # round 9 #4
    inv = _inventory(tmp_path, [("x-1-usa-noaa", "NOAA", "research_consultancy")])
    for g in ("NATIONAL OCEANOGRAPHY CENTRE", "bureau of meteorology", "institute of oceanographic sciences bidston"):
        with pytest.raises(ValueError, match="names the withheld agency"):
            gesla_licences([doc("x-1-usa-noaa", "NOAA")], inventory_path=inv, overrides_path=_no_overrides(tmp_path),
                           originators_path=_origins(tmp_path, [("x-1-usa-noaa", g, "")]))


def test_a_jma_originated_record_must_carry_the_jma_licence(tmp_path):
    # round 9 #5: the PDL 1.0 attribution is enforced by the build, not only by a count
    inv = _inventory(tmp_path, [("y-1-jpn-uhslc_rq", "UHSLC_RQ", "research_consultancy")])
    org = _origins(tmp_path, [("y-1-jpn-uhslc_rq", "Japan Meteorological Agency Japan", "JAPAN METEOROLOGICAL AGENCY")])
    with pytest.raises(ValueError, match="calls for the licence 'gesla-uhslc-jma-nc'"):
        gesla_licences([doc("y-1-jpn-uhslc_rq", "UHSLC_RQ")], inventory_path=inv, overrides_path=_no_overrides(tmp_path),
                       originators_path=org)
    ok = _overrides(tmp_path, HEAD + "y-1-jpn-uhslc_rq,UHSLC_RQ,gesla-uhslc-jma-nc,originator JMA: GESLA ORIGINATOR "
                                     "'Japan Meteorological Agency'\n")
    _, ids = gesla_licences([doc("y-1-jpn-uhslc_rq", "UHSLC_RQ")], inventory_path=inv, overrides_path=ok,
                            originators_path=org)
    assert ids == {"y-1-jpn-uhslc_rq": "gesla-uhslc-jma-nc"}
    assert "Japan Meteorological Agency" in json.loads(LICENCES_JSON.read_text())["originator_licences"][
        "gesla-uhslc-jma-nc"]["originator_patterns"]


def test_the_backlog_is_recorded():
    bl = " ".join(json.loads(LICENCES_JSON.read_text())["backlog"])
    for k in ("NOAA stations outside US territory", "NWIS", "gesla-ispra url", "derivation", "Wakkanai"):
        assert k in bl, k
