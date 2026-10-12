"""The vector wrapper uses the existing validated own-TCD table builder."""
import importlib.util
from pathlib import Path

import pytest

from otc_pipeline import astro_tables as at
from synthetic_congen import write_run
from test_astro_tables import dump


def wrapper():
    path = Path(__file__).parents[1] / "vectors" / "astro_entry.py"
    assert path.is_file(), "the native own-TCD wrapper is missing"
    spec = importlib.util.spec_from_file_location("vector_astro_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_maps_release_names_and_reuses_validated_builder(tmp_path):
    table = dump(at.TCD_SHA256, speeds={"LDA2": 29.4556253})
    run_dir = write_run(tmp_path / "run", table)
    entry = wrapper().build_entry(run_dir, ["LAM2"], "test-table", table.tcd_sha256)
    run = at.load_congen_run(run_dir, tcd_sha256=table.tcd_sha256)
    assert entry["constituents"]["LAM2"]["speed_deg_per_hour"] == 29.4556253
    assert at.release_table_errors(entry, *run) == []


def test_rejects_unknown_name_and_wrong_tcd_pin(tmp_path):
    table = dump(at.TCD_SHA256, speeds={"LDA2": 29.4556253})
    run_dir = write_run(tmp_path / "run", table)
    with pytest.raises(at.AstroTableMismatch):
        wrapper().build_entry(run_dir, ["LAM2"], "test-table", "0" * 64)
    with pytest.raises((ValueError, KeyError, at.AstroTableMismatch)):
        wrapper().build_entry(run_dir, ["UNKNOWN"], "test-table", table.tcd_sha256)
