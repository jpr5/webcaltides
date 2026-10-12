"""Pinned actual NOAA native loader input; inventory RED is separately recorded."""
import importlib
import os
from pathlib import Path


def test_native_loader_retains_both_exact_official_rows():
    bundle = Path(os.environ["OTC_NOAA_INPUTS"])
    pin = os.environ["OTC_NOAA_INPUTS_SHA"]
    loader = importlib.import_module("otc_pipeline.noaa_historical")
    inputs = loader.load_noaa_historical_inputs(bundle, pin)
    assert len(inputs.profiles) == 2
    assert inputs.manifest_sha256 == pin
