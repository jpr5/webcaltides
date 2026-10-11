"""TB-SIB disputed_reason eot20_whole_hour: a station whose only sets carry it is withheld (owner rule: no bad
data ships); a station with a clean set publishes the clean one. Synthetic verdicts until TB-SIB lands."""
import inspect

import pytest

from otc_pipeline import gesla_release as gr
from otc_pipeline.gesla_release import disputed_reasons_of

from test_build_integrity import dump, table  # noqa: F401 (fixtures)
from test_station_map import two
from test_station_map_r3 import _kw

WH = "eot20_whole_hour"


def test_disputed_reasons_is_a_required_input():
    assert inspect.signature(gr.build_release).parameters["disputed_reasons"].default is inspect.Parameter.empty


def test_a_station_whose_only_sets_have_a_whole_hour_eot20_lag_is_withheld(table):  # noqa: F811
    docs = two(table)
    other = {d["record_id"]: f"OTC-{i}" for i, d in enumerate(docs)}
    release, report = gr.build_release(docs, **{**_kw(table, docs, other, {}), "disputed_reasons": {"r1-nor-test": WH}})
    assert [s["station_id"] for s in release["stations"]] == ["OTC-0"]
    # TB-SIB writes the reasons space-separated, the code's own first
    both = {"r1-nor-test": "comparators_disagree eot20_whole_hour"}
    release, report = gr.build_release(docs, **{**_kw(table, docs, other, {}), "disputed_reasons": both})
    assert [s["station_id"] for s in release["stations"]] == ["OTC-0"]
    (r,) = report.refused
    assert r["reason"] == "eot20_whole_hour_unresolved" and r["record"] == "r1-nor-test" and "OTC-1" in r["message"]
    assert report.published == ["r0-nor-test"]


def test_a_clean_set_at_the_station_publishes_and_other_reasons_do_not_withhold(table):  # noqa: F811
    docs = two(table)
    one = {d["record_id"]: "OTC-ONE" for d in docs}
    release, report = gr.build_release(docs, **{**_kw(table, docs, one, {}), "disputed_reasons": {"r1-nor-test": WH}})
    assert len(release["stations"][0]["constant_sets"]) == 2 and report.counts() == {}
    with pytest.raises(ValueError, match="no record is publishable"):
        gr.build_release(docs, **{**_kw(table, docs, one, {}), "disputed_reasons": {d["record_id"]: WH for d in docs}})
    release, report = gr.build_release(docs, **{**_kw(table, docs, one, {}),
                                                "disputed_reasons": {d["record_id"]: "comparators_disagree" for d in docs}})
    assert report.counts() == {}


def test_a_station_of_whole_hour_and_clock_offset_sets_is_withheld(table):  # noqa: F811
    docs = two(table)
    one = {d["record_id"]: "OTC-ONE" for d in docs}
    clock = {"kind": "split_off", "verdict": "clock_offset_far", "clock_offset_h": -1.0, "distance_km": 1.5,
             "against": ["x"], "decided_by": "size", "station_id": "OTC-ONE"}
    with pytest.raises(ValueError, match="no record is publishable"):
        gr.build_release(docs, **{**_kw(table, docs, one, {"r0-nor-test": [clock]}), "disputed_reasons": {"r1-nor-test": WH}})


def test_disputed_reasons_from_the_audit():
    rows = {"a": {"time_base_status": "disputed", "disputed_reason": WH, "time_base_code": "disputed:x"},
            "b": {"time_base_status": "disputed", "time_base_code": "disputed:eot20_whole_hour"},
            "c": {"time_base_status": "unverified", "disputed_reason": WH},
            "d": {"time_base_status": "disputed", "time_base_code": "disputed"},
            "e": {"time_base_status": "disputed", "disputed_reason": "comparators_disagree eot20_whole_hour"}}
    assert disputed_reasons_of(rows) == {"a": WH, "b": WH, "e": "comparators_disagree eot20_whole_hour"}
