"""C1 (combine): the agency sets in the release next to the GESLA build (RELEASE-AGENCY)."""
from __future__ import annotations

from otc_pipeline.adapters.noaa import NoaaAdapter
from otc_pipeline.adapters.noaa_currents import NoaaCurrentsAdapter


def test_noaa_sources_share_one_licence_object():
    # one release holds one licence per licence_id: both NOAA adapters must give the same object
    assert NoaaAdapter().licence() == NoaaCurrentsAdapter().licence()
