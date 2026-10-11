"""Missing real-data inputs: a skip that names them, or a failure when OTC_REQUIRE_REAL_DATA=1.

The real-record tests (bit for bit on real GESLA pairs, 1-vs-3-worker identity) need the verified
GESLA cache, the TCD dump and the gates cache. Without them they skip, naming every missing path
and the switch; a gate that must prove them sets OTC_REQUIRE_REAL_DATA=1, and then a missing input
fails the test instead of skipping it."""

from __future__ import annotations

import os

import pytest


def require(missing: list[str], what: str) -> None:
    if not missing:
        return
    msg = f"{what} not in the cache: {missing} (set OTC_WORK, or OTC_REQUIRE_REAL_DATA=1 to fail instead of skip)"
    if os.environ.get("OTC_REQUIRE_REAL_DATA") == "1":
        pytest.fail(msg)
    pytest.skip(msg)
