"""otc_pipeline.datums: the published-level helpers and build checks 8, 9 and 13 (datums spec §3.6)."""
from __future__ import annotations

import pytest

from otc_pipeline.datums import datum_build_errors, period, published


def test_period():
    assert period("1983-01-01", "2001-12-31", "NTDE 1983-2001") == {
        "start": "1983-01-01", "end": "2001-12-31", "name": "NTDE 1983-2001"}
    assert period("2020-01-01", "2020-01-01") == {"start": "2020-01-01", "end": "2020-01-01"}
    with pytest.raises(ValueError):
        period("2021-02-30", "2021-03-01")          # not a real date
    with pytest.raises(ValueError, match="end before start"):
        period("2002-01-01", "2001-12-31")


def test_published():
    assert published("MSL") == {"kind": "published", "method": "source", "source_name": "MSL"}
    assert published("MHHW", epoch={"start": "1983-01-01", "end": "2001-12-31"}, control_source_id="8443970",
                     provisional=True) == {
        "kind": "published", "method": "source", "source_name": "MHHW",
        "epoch": {"start": "1983-01-01", "end": "2001-12-31"}, "control": {"source_id": "8443970"},
        "flags": ["provisional"]}
    with pytest.raises(ValueError):
        published("x" * 32)
    with pytest.raises(ValueError):
        published("")


GOOD = {"zero": "chart_datum", "chart_datum": "cd", "named": {"cd": 0.0, "msl": 1.0},
        "basis": {"cd": published("CD"), "msl": published("MSL", epoch=period("1996-01-01", "2014-12-31"))}}


def test_build_checks_pass():
    assert datum_build_errors(GOOD) == []
    assert datum_build_errors({"msl_offset_m": 1.0}) == []


def test_check_8_chart_datum_names_a_key_of_named():
    assert [e for e in datum_build_errors({**GOOD, "chart_datum": "lat"}) if "check 8" in e]


def test_check_9_same_keys():
    bad = {**GOOD, "named": {**GOOD["named"], "lat": 0.1}}
    errs = datum_build_errors(bad)
    assert len(errs) == 1 and "check 9" in errs[0] and "['lat']" in errs[0]
    bad = {**GOOD, "basis": {**GOOD["basis"], "hat": published("HAT")}}
    assert [e for e in datum_build_errors(bad) if "check 9" in e]


@pytest.mark.parametrize("ep", [{"start": "2021-02-30", "end": "2021-03-01"},
                                {"start": "2015-01-01", "end": "2014-12-31"}])
def test_check_13_real_dates_in_order(ep):
    bad = {**GOOD, "basis": {**GOOD["basis"], "msl": {**published("MSL"), "epoch": ep}}}
    assert [e for e in datum_build_errors(bad) if "check 13" in e]
