"""The adapter contract (manifest §2.4, GATE-ADP) and the source registry.

Every adapter runs this contract: `check_contract` here on stubs, `assert_contract` in each
adapter's own tests on its saved real responses, and `test_registered_adapter_live` on live
data for every adapter in SOURCES (set OTC_LIVE=1).
"""
from __future__ import annotations

import os
from dataclasses import replace

import pytest

from gesla.record import SOURCES as GAUGE_RECORD_SOURCES
from otc_pipeline.adapter import (
    Adapter,
    ContractError,
    FetchResult,
    SourceSet,
    assemble_release,
    assert_contract,
    check_contract,
)
from otc_pipeline.sources import EXPECTED, SOURCES, missing_sources, registry_errors

from contract_stubs import (
    GaugeStub,
    MissingConventionStub,
    OfficialStub,
    SubordinateStub,
    WrongRecordSourceStub,
)

LIVE = os.environ.get("OTC_LIVE") == "1"


def run(adapter, tmp_path, only=None):
    fetched = adapter.fetch(tmp_path / adapter.source, only=only)
    return check_contract(adapter, fetched)


# --- the contract on known-good and known-bad stubs -------------------------------------

def test_stubs_satisfy_the_protocol():
    for cls in (OfficialStub, SubordinateStub, GaugeStub):
        assert isinstance(cls(), Adapter)


def test_good_official_stub_passes(tmp_path):
    assert run(OfficialStub(), tmp_path) == []


def test_set_without_convention_id_fails(tmp_path):
    errors = run(MissingConventionStub(), tmp_path)
    assert errors, "a set without convention_id must fail the contract"
    assert any("convention_id" in e for e in errors), errors


def test_assert_contract_raises_with_every_error(tmp_path):
    adapter = MissingConventionStub()
    fetched = adapter.fetch(tmp_path / "kv")
    with pytest.raises(ContractError) as exc:
        assert_contract(adapter, fetched)
    assert "convention_id" in str(exc.value)
    assert sum("convention_id" in e for e in exc.value.errors) >= 2  # one per set


def test_good_gauge_stub_passes(tmp_path):
    assert run(GaugeStub(), tmp_path) == []


def test_subordinate_offsets_resolve_to_the_reference_station(tmp_path):
    adapter = SubordinateStub()
    fetched = adapter.fetch(tmp_path / "noaa")
    assert check_contract(adapter, fetched) == []
    release = assemble_release(adapter, list(adapter.sets(fetched)), fetched)
    by_name = {st["name"]: st for st in release["stations"]}
    sub = by_name["Bravo Harbour"]
    assert sub["recommended_set_id"] is None
    assert sub["subordinate_offsets"]["reference_station_id"] == by_name["Alpha Harbour"]["station_id"]
    assert "reference_source_record_id" not in sub["subordinate_offsets"]


def test_unresolved_subordinate_reference_fails(tmp_path):
    adapter = SubordinateStub()
    fetched = adapter.fetch(tmp_path / "noaa", only=["BBB"])
    errors = check_contract(adapter, fetched)
    assert any("reference_source_record_id 'AAA'" in e for e in errors), errors


class _Mutated(OfficialStub):
    def __init__(self, mutate):
        self.mutate = mutate

    def sets(self, fetched):
        for ss in super().sets(fetched):
            yield self.mutate(ss)


def _cs(**changes):
    return lambda ss: replace(ss, constant_set={**ss.constant_set, **changes})


@pytest.mark.parametrize("mutate, needle", [
    (_cs(convention_id="other"), "convention_id 'other'"),
    (_cs(licence_id="other"), "licence_id 'other'"),
    (_cs(source="noaa"), "source 'noaa'"),
    (_cs(source_type="model"), "source_type 'model'"),
    (_cs(quantity="current"), "quantity 'current'"),
    (_cs(source_version="19990101"), "source_version '19990101'"),
    (_cs(set_id="x/y"), "set_id is assigned by the build"),
    (_cs(provenance={}), "provenance.adapter_version"),
    (_cs(source_record_id="AAA"), "duplicate source_record_id 'AAA'"),
    (_cs(source_record_id="ZZZ"), "no watch key for 'ZZZ'"),
    (_cs(qc_status="maybe"), "qc_status"),
    (lambda ss: replace(ss, station_hint={k: v for k, v in ss.station_hint.items() if k != "timezone"}),
     "station_hint is missing timezone"),
    (lambda ss: replace(ss, station_hint={**ss.station_hint, "country": "NO"}), "country"),
    (lambda ss: "not a SourceSet", "is not a SourceSet"),
])
def test_bad_sets_fail(tmp_path, mutate, needle):
    errors = run(_Mutated(mutate), tmp_path)
    assert any(needle in e for e in errors), errors


def test_class_attributes_are_checked(tmp_path):
    class Bad(OfficialStub):
        source_type = "model"   # kartverket is registered as official
        adapter_version = ""
    errors = run(Bad(), tmp_path)
    assert any("source_type 'model'" in e and "registry" in e for e in errors), errors
    assert any("adapter_version" in e for e in errors), errors


def test_unregistered_source_fails(tmp_path):
    class Unknown(OfficialStub):
        source = "atlantis"
    errors = run(Unknown(), tmp_path)
    assert any("'atlantis' is not one of the" in e for e in errors), errors


def test_fetch_result_must_belong_to_the_adapter(tmp_path):
    adapter = OfficialStub()
    fetched = adapter.fetch(tmp_path / "kv")
    errors = check_contract(adapter, replace(fetched, source="noaa"))
    assert any("FetchResult.source 'noaa'" in e for e in errors), errors


def test_bad_watch_keys_fail(tmp_path):
    class BadKeys(OfficialStub):
        def watch_keys(self, fetched):
            return {"AAA": "abc", "BBB": "F" * 64}
    errors = run(BadKeys(), tmp_path)
    assert sum("watch key" in e and "SHA-256" in e for e in errors) == 2, errors


def test_fetch_files_must_match_the_cache(tmp_path):
    adapter = OfficialStub()
    fetched = adapter.fetch(tmp_path / "kv")
    (tmp_path / "kv" / "stations.json").write_text("{}")
    errors = check_contract(adapter, fetched)
    assert any("stations.json" in e and "sha256" in e for e in errors), errors


def test_empty_output_fails(tmp_path):
    errors = run(OfficialStub(), tmp_path, only=["nothing"])
    assert any("no sets" in e for e in errors), errors


def test_gauge_records_must_carry_the_adapter_source(tmp_path):
    errors = run(WrongRecordSourceStub(), tmp_path)
    assert any("record source 'rws'" in e for e in errors), errors


def test_the_other_accessor_is_not_called(tmp_path):
    # the registry names the output; the accessor for the other kind is never called, so it
    # may raise or be absent (see test_only_the_applicable_accessor_is_called)
    calls = []

    class OfficialWithRecords(OfficialStub):
        def records(self, fetched):
            calls.append("records")
            return ()
    assert run(OfficialWithRecords(), tmp_path) == [] and calls == []


def test_source_set_needs_constants_or_offsets():
    with pytest.raises(ValueError):
        SourceSet(station_hint={}, constant_set=None, subordinate_offsets=None)


def test_manifest_lists_every_file(tmp_path):
    fetched = OfficialStub().fetch(tmp_path / "kv")
    man = fetched.manifest(dataset="stub")
    assert man["dataset"] == "stub"
    assert [f["path"] for f in man["files"]] == ["stations.json"]
    assert set(man["files"][0]) == {"path", "sha256", "size", "source_url", "fetched_at"}


def test_fetch_result_rejects_a_path_outside_the_cache(tmp_path):
    f = OfficialStub().fetch(tmp_path / "kv").files[0]
    with pytest.raises(ValueError):
        FetchResult(source="kartverket", source_version="1", cache_dir=tmp_path,
                    files=(replace(f, path="../escape.json"),))


# --- the registry ------------------------------------------------------------------------

def test_registry_names_all_sixteen_sources():
    assert len(EXPECTED) == 16
    assert set(EXPECTED) == {
        "noaa", "noaa-currents", "kartverket", "linz", "jma",
        "pegelonline", "rws", "marine-institute", "dmi", "smhi", "fmi", "uhslc-fd", "shom-refmar",
        "eot20", "fes2022", "gesla-fit",
    }


def test_registry_gauge_sources_match_gauge_record():
    observing = {s for s, spec in EXPECTED.items() if spec.output == "records"}
    assert observing == GAUGE_RECORD_SOURCES - {"gesla"}


def test_registry_kinds():
    kinds = {s: (spec.source_type, spec.quantity, spec.output) for s, spec in EXPECTED.items()}
    assert kinds["noaa-currents"] == ("official", "current", "sets")
    assert kinds["linz"] == ("official", "water_level", "sets")
    assert kinds["gesla-fit"] == ("gauge", "water_level", "sets")
    assert kinds["shom-refmar"] == ("gauge", "water_level", "records")
    assert {s for s, k in kinds.items() if k[0] == "model"} == {"eot20", "fes2022"}


def test_registered_adapters_are_consistent():
    assert registry_errors() == []
    assert set(missing_sources()) == set(EXPECTED) - set(SOURCES)


def test_registry_errors_catch_a_mismatched_adapter(monkeypatch):
    class Wrong(OfficialStub):
        source = "noaa"
        source_type = "model"
    monkeypatch.setitem(SOURCES, "linz", Wrong)
    monkeypatch.setitem(SOURCES, "atlantis", OfficialStub)
    errors = registry_errors()
    assert any("SOURCES['linz']" in e and "source 'noaa'" in e for e in errors), errors
    assert any("'atlantis'" in e for e in errors), errors


@pytest.mark.skipif(not LIVE, reason="live fetch: set OTC_LIVE=1")
@pytest.mark.parametrize("source", sorted(SOURCES))
def test_registered_adapter_live(source, tmp_path):
    adapter = SOURCES[source]()
    fetched = adapter.fetch(tmp_path / source, only=list(adapter.contract_sample))
    assert_contract(adapter, fetched)


@pytest.mark.skipif(not LIVE, reason="live fetch: set OTC_LIVE=1")
def test_kartverket_station_list_live(tmp_path):
    from kv_station_list import KartverketStationList
    adapter = KartverketStationList()
    fetched = adapter.fetch(tmp_path / "kartverket")
    assert_contract(adapter, fetched)
    codes = {ss.constant_set["source_record_id"] for ss in adapter.sets(fetched)}
    assert {"BGO", "TRD"} <= codes


def test_cli_reports_pass_and_fail(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    tests = Path(__file__).resolve().parent
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(tests), os.environ.get("PYTHONPATH")]))}

    def cli(spec):
        return subprocess.run([sys.executable, "-m", "otc_pipeline.adapter", spec, "--cache",
                               str(tmp_path / spec.split(":")[1])],
                              capture_output=True, text=True, env=env, cwd=tests.parent)
    good, bad = cli("contract_stubs:OfficialStub"), cli("contract_stubs:MissingConventionStub")
    assert good.returncode == 0 and "CONTRACT PASS" in good.stdout, good.stdout + good.stderr
    assert bad.returncode == 1 and "CONTRACT FAIL" in bad.stdout and "convention_id" in bad.stdout


# --- contract robustness (F2 CR) ------------------------------------------------------------

def test_file_hash_is_streamed_not_read_whole(tmp_path, monkeypatch):
    from pathlib import Path

    adapter = OfficialStub()
    fetched = adapter.fetch(tmp_path / "kv")
    real = Path.read_bytes
    cache = (tmp_path / "kv").resolve()

    def no_read_bytes(self):
        # a fetched file is never read whole; other files (the pinned schema copy) may be
        if cache in Path(self).resolve().parents:
            raise MemoryError("read_bytes on a multi-GB grid")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", no_read_bytes)
    assert check_contract(adapter, fetched) == []


@pytest.mark.parametrize("attr", ["source", "source_type", "quantity", "adapter_version"])
def test_a_missing_attribute_is_a_contract_error(tmp_path, attr):
    adapter = OfficialStub()
    fetched = adapter.fetch(tmp_path / "kv")

    class Missing(OfficialStub):
        pass
    bad = Missing()
    setattr(Missing, attr, property(lambda self: (_ for _ in ()).throw(AttributeError(attr))))
    errors = check_contract(bad, fetched)
    assert any(attr in e for e in errors), errors


@pytest.mark.parametrize("method", ["sets", "watch_keys", "convention", "licence"])
def test_an_adapter_exception_is_a_contract_error(tmp_path, method):
    class Raises(OfficialStub):
        pass

    def boom(self, *a):
        raise RuntimeError(f"{method} broke")
    setattr(Raises, method, boom)
    errors = run(Raises(), tmp_path)
    assert any(f"{method}() raised RuntimeError: {method} broke" in e for e in errors), errors
    assert any("CONTRACT" not in e for e in errors)


def test_records_exception_is_a_contract_error_for_a_gauge(tmp_path):
    class Raises(GaugeStub):
        def records(self, fetched):
            raise RuntimeError("parse failed")
            yield
    errors = run(Raises(), tmp_path)
    assert any("records() raised RuntimeError: parse failed" in e for e in errors), errors


def test_only_the_applicable_accessor_is_called(tmp_path):
    class OfficialNoRecords(OfficialStub):
        def records(self, fetched):
            raise NotImplementedError("official sources emit sets")

    class GaugeNoSets(GaugeStub):
        def sets(self, fetched):
            raise NotImplementedError("gauge sources emit records")
    assert run(OfficialNoRecords(), tmp_path) == []
    assert run(GaugeNoSets(), tmp_path) == []


class _SubMutated(SubordinateStub):
    def __init__(self, mutate):
        self.mutate = mutate

    def sets(self, fetched):
        for ss in super().sets(fetched):
            yield self.mutate(ss) if ss.constant_set is None else ss


def test_a_subordinate_only_set_needs_an_id(tmp_path):
    errors = run(_SubMutated(lambda ss: replace(ss, station_hint={**ss.station_hint, "aliases": {}})),
                 tmp_path)
    assert any("subordinate" in e and "id" in e for e in errors), errors


def test_a_subordinate_only_set_needs_a_watch_key(tmp_path):
    class NoKey(SubordinateStub):
        def watch_keys(self, fetched):
            return {k: v for k, v in super().watch_keys(fetched).items() if k != "BBB"}
    errors = run(NoKey(), tmp_path)
    assert any("no watch key for 'BBB'" in e for e in errors), errors


def test_duplicate_subordinate_ids_fail(tmp_path):
    class Twice(SubordinateStub):
        def sets(self, fetched):
            sets = list(super().sets(fetched))
            return sets + [s for s in sets if s.constant_set is None]
    errors = run(Twice(), tmp_path)
    assert any("duplicate source_record_id 'BBB'" in e for e in errors), errors


def test_a_missing_subordinate_reference_fails(tmp_path):
    def drop_ref(ss):
        off = {k: v for k, v in ss.subordinate_offsets.items() if k != "reference_source_record_id"}
        return replace(ss, subordinate_offsets=off)

    class NoneRef(SubordinateStub):
        # a constant_set without source_record_id puts None among the known ids
        def sets(self, fetched):
            sets = list(super().sets(fetched))
            sets[0] = replace(sets[0], constant_set={k: v for k, v in sets[0].constant_set.items()
                                                     if k != "source_record_id"})
            return [drop_ref(s) if s.constant_set is None else s for s in sets]
    errors = run(NoneRef(), tmp_path)
    assert any("reference_source_record_id" in e and "missing" in e for e in errors), errors


def test_a_non_dict_constant_set_still_checks_the_offsets(tmp_path):
    def bad(ss):
        return replace(ss, constant_set="nope",
                       subordinate_offsets={"reference_station_id": "OTC-X", "height_adjusted_type": "R"})
    errors = run(_Mutated(bad), tmp_path)
    assert any("constant_set is not a dict" in e for e in errors), errors
    assert any("reference_station_id is assigned by the build" in e for e in errors), errors


def test_conflicting_constituent_speeds_are_reported(tmp_path):
    def speed(ss):
        cons = [{**c, "speed_deg_per_hour": 29.0} if c["name"] == "M2" else c
                for c in ss.constant_set["constituents"]]
        return replace(ss, constant_set={**ss.constant_set, "constituents": cons}) \
            if ss.constant_set["source_record_id"] == "BBB" else ss
    errors = run(_Mutated(speed), tmp_path)
    assert any("M2" in e and "speed" in e and "29.0" in e for e in errors), errors


def test_no_made_up_m2_row():
    adapter = OfficialStub()
    fetched = FetchResult(source="kartverket", source_version="1", cache_dir=".", files=())
    release = assemble_release(adapter, [], fetched)
    assert release["constituents"] == []


def test_an_unprefixed_record_id_fails(tmp_path):
    class Unprefixed(GaugeStub):
        def records(self, fetched):
            for r in super().records(fetched):
                yield replace(r, record_id=r.record_id.split(":", 1)[1])
    errors = run(Unprefixed(), tmp_path)
    assert any("must start with 'pegelonline:'" in e for e in errors), errors


def test_input_sha256_must_name_the_fetched_files(tmp_path):
    errors = run(_Mutated(_cs(provenance={"adapter_version": "stub-1", "input_sha256": ["0" * 64]})),
                 tmp_path)
    assert any("input_sha256" in e for e in errors), errors


def test_duplicate_file_paths_are_reported(tmp_path):
    adapter = OfficialStub()
    fetched = adapter.fetch(tmp_path / "kv")
    errors = check_contract(adapter, replace(fetched, files=fetched.files * 2))
    assert any("duplicate" in e and "stations.json" in e for e in errors), errors


@pytest.mark.parametrize("path", [".", "./", "a//b", "a/./b"])
def test_fetch_result_rejects_dot_paths(tmp_path, path):
    f = OfficialStub().fetch(tmp_path / "kv").files[0]
    with pytest.raises(ValueError):
        FetchResult(source="kartverket", source_version="1", cache_dir=tmp_path,
                    files=(replace(f, path=path),))


def test_fetch_result_is_hashable(tmp_path):
    fetched = OfficialStub().fetch(tmp_path / "kv")
    assert hash(fetched) == hash(replace(fetched))


def test_registry_checks_instance_attributes(monkeypatch):
    class InitSets(OfficialStub):
        def __init__(self):
            self.source, self.source_type, self.quantity = "kartverket", "official", "water_level"
    del_attrs = {k: None for k in ("source", "source_type", "quantity")}
    InitSetsCls = type("InitSetsCls", (InitSets,), del_attrs)
    monkeypatch.setitem(SOURCES, "kartverket", InitSetsCls)
    assert registry_errors() == []


def test_registry_reports_a_class_that_breaks_the_protocol(monkeypatch):
    class NoVersion(OfficialStub):
        adapter_version = ""
    monkeypatch.setitem(SOURCES, "kartverket", NoVersion)
    assert any("adapter_version" in e for e in registry_errors())


def _cli(tmp_path, *args):
    import subprocess
    import sys
    from pathlib import Path
    tests = Path(__file__).resolve().parent
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(tests), os.environ.get("PYTHONPATH")]))}
    return subprocess.run([sys.executable, "-m", "otc_pipeline.adapter", *args, "--cache", str(tmp_path / "c")],
                          capture_output=True, text=True, env=env, cwd=tests.parent)


def test_cli_rejects_empty_only(tmp_path):
    got = _cli(tmp_path, "contract_stubs:OfficialStub", "--only")
    assert got.returncode == 2 and "--only" in got.stderr


def test_cli_names_an_unregistered_source(tmp_path):
    got = _cli(tmp_path, "fes2022")   # registered last (A-FES waits on owner step O1)
    assert got.returncode == 1 and "no registered adapter" in got.stdout + got.stderr
    assert "CONTRACT FAIL" in got.stdout


def test_cli_prints_fail_when_fetch_returns_garbage(tmp_path):
    got = _cli(tmp_path, "contract_stubs:GarbageFetchStub")
    assert got.returncode == 1 and "CONTRACT FAIL" in got.stdout, got.stdout + got.stderr
    assert "not a FetchResult" in got.stdout


def test_cli_prints_fail_when_watch_keys_is_none(tmp_path):
    got = _cli(tmp_path, "contract_stubs:NoneKeysStub")
    assert got.returncode == 1 and "CONTRACT FAIL" in got.stdout, got.stdout + got.stderr
    assert "watch_keys returned no keys" in got.stdout


# --- schema 0.3 alias systems ---------------------------------------------------------------

class LinzStub(OfficialStub):
    """An official set from LINZ: its station_hint carries a `linz` alias (schema 0.3)."""

    source = "linz"

    def sets(self, fetched):
        for i, ss in enumerate(super().sets(fetched), 1):   # LINZ port ids look like "1AAA"
            yield replace(ss, station_hint={**ss.station_hint, "country": "NZL",
                                            "timezone": "Pacific/Auckland",
                                            "aliases": {"linz": f"{i}{ss.constant_set['source_record_id']}"}})


def test_a_set_with_a_linz_alias_passes(tmp_path):
    assert run(LinzStub(), tmp_path) == []


# --- format 1.0: licence, build-owned astro tables, time base --------------------------

def _astro(names=("M2", "S2", "K1"), table_id="contract-test-astro"):
    """TEST ONLY: an astro table shaped as the schema needs; the values are not real V0+u or f."""
    speeds = {"M2": 28.9841042, "S2": 30.0, "K1": 15.0410686}
    return {"astro_table_id": table_id, "v0_model": "schureman_tcd", "tables_sha256": "c" * 64, "generator": "TEST ONLY",
            "first_year": 2020, "last_year": 2021,
            "constituents": {n: {"speed_deg_per_hour": speeds[n], "v0u_deg": [0.0, 0.0], "f": [1.0, 1.0]}
                             for n in names}}


def test_a_licence_without_commercial_use_fails(tmp_path):
    class NoCommercialUse(OfficialStub):
        def licence(self):
            return {k: v for k, v in super().licence().items() if k != "commercial_use"}
    errors = run(NoCommercialUse(), tmp_path)
    assert "release: licences/0: 'commercial_use' is a required property" in errors, errors


def test_a_non_commercial_licence_needs_restriction_and_terms(tmp_path):
    class NonCommercial(OfficialStub):
        def licence(self):
            return {**super().licence(), "commercial_use": False}
    errors = run(NonCommercial(), tmp_path)
    assert "release: licences/0: 'restriction' is a required property" in errors, errors
    assert "release: licences/0: 'terms_url' is a required property" in errors, errors


def test_the_astro_tables_are_the_builds(tmp_path):
    adapter = OfficialStub()
    fetched = adapter.fetch(tmp_path / "kv")
    release = assemble_release(adapter, list(adapter.sets(fetched)), fetched)
    assert "astro_tables" not in release and "astro_table_id" not in release["conventions"][0]
    # with the build's tables the shell is checked whole, and the convention names the table
    astro = [_astro()]
    assert check_contract(adapter, fetched, astro_tables=astro) == []
    whole = assemble_release(adapter, list(adapter.sets(fetched)), fetched, astro_tables=astro)
    assert whole["astro_tables"] == astro and whole["conventions"][0]["astro_table_id"] == "contract-test-astro"


def test_only_the_astro_errors_are_left_to_the_build(tmp_path):
    # the same stub with a bad qc_status still fails: only the two build-owned errors are not the adapter's
    class Accepted(OfficialStub):
        def sets(self, fetched):
            for ss in super().sets(fetched):
                yield replace(ss, constant_set={**ss.constant_set, "qc_status": "accepted"})
    errors = run(Accepted(), tmp_path)
    assert any("'accepted' is not one of" in e for e in errors), errors
    assert not any(e in errors for e in __import__("otc_pipeline.adapter").adapter.BUILD_OWNED_ASTRO_ERRORS)


def test_an_adapter_never_names_an_astro_table(tmp_path):
    class NamesATable(OfficialStub):
        def convention(self):
            return {**super().convention(), "astro_table_id": "made-up"}
    errors = run(NamesATable(), tmp_path)
    assert any("astro_table_id 'made-up' is assigned by the build" in e for e in errors), errors
    # and its missing table is then reported, not left to the build
    assert "release: (root): 'astro_tables' is a required property" in errors, errors


@pytest.mark.parametrize("label", ["UTC", "utc_plus_0", "local_dst:Oslo", "utc_instant\n"])
def test_a_gauge_record_declares_a_1_0_time_base(tmp_path, label):
    class BadLabel(GaugeStub):
        def records(self, fetched):
            for r in super().records(fetched):
                yield replace(r, declared_time_base=label)
    errors = run(BadLabel(), tmp_path)
    assert any("is not a format 1.0 time_base_label" in e for e in errors), errors


@pytest.mark.parametrize("label", ["utc_instant", "utc_mean_start60", "utc_plus_1", "local_dst:Europe/Berlin"])
def test_a_1_0_time_base_passes(tmp_path, label):
    class Label(GaugeStub):
        def records(self, fetched):
            for r in super().records(fetched):
                yield replace(r, declared_time_base=label)
    assert run(Label(), tmp_path) == []
