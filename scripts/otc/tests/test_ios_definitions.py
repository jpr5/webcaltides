"""IOS-defined SA and S1 converted to the TCD's arguments (otc_pipeline.ios_definitions)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from otc_pipeline.ios_definitions import IOS_TO_TCD, P1_EPOCH, p1_deg, to_tcd_phase


@pytest.mark.parametrize("t, want", [
    # the TCD's own SA - SA-IOS V0 difference (congen: h vs h - p1) at 1 January of each year
    (datetime(2000, 1, 1, tzinfo=timezone.utc), 282.94),
    (datetime(2025, 1, 1, tzinfo=timezone.utc), 283.37),
    (datetime(2026, 1, 1, tzinfo=timezone.utc), 283.39),
    (datetime(2027, 1, 1, tzinfo=timezone.utc), 283.41),
])
def test_p1_is_schureman_and_agrees_with_the_tcd(t, want):
    assert p1_deg(t) == pytest.approx(want, abs=0.006)


def test_the_conversions():
    p1 = p1_deg(P1_EPOCH)
    assert p1 == pytest.approx(283.40, abs=0.01)
    assert to_tcd_phase("SA", 10.0) == pytest.approx((10.0 + p1) % 360)          # h - p1 -> h
    assert to_tcd_phase("S1", 10.0) == pytest.approx((10.0 - p1 + 90.0) % 360)   # T + p1 - 90 -> T
    for name in ("SSA", "MM", "MF", "MSF", "M2", "K1", "M3"):
        assert to_tcd_phase(name, 10.0) == 10.0
    assert set(IOS_TO_TCD) == {"SA", "S1"}
