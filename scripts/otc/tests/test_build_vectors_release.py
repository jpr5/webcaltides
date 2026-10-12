"""Native vector release proof on the copied, pinned NOAA and own-TCD inputs."""
import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest


def builder():
    path = Path(__file__).parents[1] / "vectors" / "build_vectors_release.py"
    assert path.is_file(), "the native vector release builder is missing"
    spec = importlib.util.spec_from_file_location("build_vectors_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs():
    return Path(os.environ["OTC_VECTOR_INPUTS"])


def test_real_release_regenerates_identically_and_keeps_source_values(inputs):
    module = builder()
    first = module.build_release(inputs / "cache", inputs / "congen", inputs / "otc-1.0.schema.json")
    second = module.build_release(inputs / "cache", inputs / "congen", inputs / "otc-1.0.schema.json")
    assert module.encode(first) == module.encode(second)
    stations = {s.get("aliases", {}).get("noaa"): s for s in first["stations"] if "noaa" in s.get("aliases", {})}
    assert set(stations) == set(module.TIDE_STATIONS) | set(module.CURRENT_BINS)
    for sid in ("8443970", "9447130", "9414290"):
        cs = stations[sid]["constant_sets"][0]
        raw = json.loads((inputs / "cache" / "noaa" / "harcon" / f"{sid}.json").read_text())
        original = {c["name"]: c for c in raw["HarmonicConstituents"]}
        for c in cs["constituents"]:
            assert c["amplitude_m"] == float(original[c["name"]]["amplitude"])
            assert c["phase_deg"] == float(original[c["name"]]["phase_GMT"]) % 360
        datums = json.loads((inputs / "cache" / "noaa" / "datums" / f"{sid}.json").read_text())
        levels = {d["name"]: d["value"] for d in datums["datums"]}
        assert cs["datum"]["msl_offset_m"] == round(levels["MSL"] - levels["MLLW"], 6)
    assert {c["source"] for s in first["stations"] for c in s["constant_sets"]} == {"noaa", "noaa-currents"}
    synthetic = next(s for s in first["stations"] if s["station_id"] == module.SYNTHETIC_ID)
    assert synthetic["name"] == "SYNTHETIC: Boston 8443970 + MA2/MB2 (vectors only)"
    cs = synthetic["constant_sets"][0]
    assert [c["name"] for c in cs["constituents"][:2]] == ["MA2", "MB2"]
    assert [c["amplitude_m"] for c in cs["constituents"][:2]] == [0.05, 0.03]
    assert cs["provenance"]["selection_reason"] == module.SYNTHETIC_REASON
    assert synthetic["aliases"] == {}


def test_entry_assertion_rejects_a_changed_native_value_and_checks_renamed_rows(inputs):
    module = builder()
    release = module.build_release(inputs / "cache", inputs / "congen", inputs / "otc-1.0.schema.json")
    module.verify_entry(release, inputs / "congen")
    changed = copy.deepcopy(release)
    changed["astro_tables"][0]["constituents"]["M2"]["v0u_deg"][310] += 0.01
    with pytest.raises(ValueError, match="M2"):
        module.verify_entry(changed, inputs / "congen")
    for name in ("LAM2", "RHO"):
        changed = copy.deepcopy(release)
        changed["astro_tables"][0]["constituents"][name]["v0u_deg"][310] += 0.01
        with pytest.raises(ValueError, match=name):
            module.verify_entry(changed, inputs / "congen")


def test_offline_input_corruption_is_refused(inputs, tmp_path):
    module = builder()
    source = inputs / "cache" / "noaa"
    (tmp_path / "MANIFEST.json").write_bytes((source / "MANIFEST.json").read_bytes())
    (tmp_path / "stations.json").write_bytes((source / "stations.json").read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        module.load_offline(tmp_path, "noaa", ["8443970"])
