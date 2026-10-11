"""The build's time-base annotation reads TB-SIB's verdict rows (f5a026a): comparator kind 'w' with the
sibling_constants summary (site siblings, provider family, EOT20), so its disputed and corrected rows are
annotated, never refused for "no evidence". Rows copied from TB-SIB full-5d."""
import pytest

from otc_pipeline import gesla_sets as gs
from otc_pipeline.release_writer import pinned_schema

from test_format_1_0 import tb_row


def _row(code, sibling, status, published="utc_instant", **over):
    kw = dict(time_base_code=code, published_time_base=published, comparator_kind="w", comparator=code,
              official_lag="", lag_median="", weak_eot20="n/a", weak_served="n/a", weak_dst="n/a",
              sibling_constants=sibling, in_pool="0", time_base_reason=f"declared kept; {sibling}")
    return tb_row(status, **{**kw, **over})


SITE = ("fitted constants +39.0 min against 2 record(s) of the site (margaree_harbour-1546-can-meds, "
        "margaree_trailer-1545-can-meds); side by eot20_one_side (EOT20 +51.5 here, +15.2 there); not unambiguous")
FAMILY = ("EOT20 -484.5 min (all four) = -8 h, as 6 records of provider family bom/aus/aaaaa99 (2 confirmed by site "
          "siblings, 4 on UTC)")
WHOLE = ("EOT20 lag +542.5 min (all four) is +9 h, a whole number of hours beyond what a harbour does to the tide; "
         "disputed on EOT20 alone, never corrected (owner rule)")


def _ann(row, rid="x-1-nor-test"):
    return gs.time_base_annotation(row, 0, rid, decided_in="20261011")


def test_a_site_sibling_dispute_is_annotated_with_its_siblings_and_eot20():
    tb = _ann(_row("disputed:sibling_constants_offset", SITE, "disputed"))
    assert tb["code"] == "disputed"
    assert [(e["comparator_kind"], e["comparator_id"], e["lag_min"]) for e in tb["evidence"]] == [
        ("sibling_record", "margaree_harbour-1546-can-meds", 39.0), ("sibling_record", "margaree_trailer-1545-can-meds", 39.0),
        ("model", "eot20", 51.5)]


def test_a_family_correction_publishes_corrected():
    # Lancelin lalan01: corrected to utc_minus_8 on EOT20 and its provider family
    tb = _ann(_row("corrected:utc_instant->utc_minus_8:eot20_family+sibling_constants", FAMILY, "corrected",
                   published="utc_minus_8"))
    assert tb["code"] == "corrected" and tb["published"] == "utc_minus_8"
    assert tb["correction"] == {"from": "utc_instant", "to": "utc_minus_8"}
    assert [(e["comparator_kind"], e["comparator_id"]) for e in tb["evidence"]] == [("model", "eot20"), ("provider", "bom/aus/aaaaa99")]
    assert tb["evidence"][0]["lag_min"] == -484.5


def test_a_whole_hour_dispute_is_annotated_with_its_eot20_lag():
    tb = _ann(_row("disputed:eot20_whole_hour", WHOLE, "disputed", disputed_reason="eot20_whole_hour"))
    assert tb["code"] == "disputed" and [(e["comparator_kind"], e["lag_min"]) for e in tb["evidence"]] == [("model", 542.5)]


def test_the_evidence_is_valid_format_1_0():
    from jsonschema import Draft202012Validator
    s = pinned_schema()
    v = Draft202012Validator({"$defs": s["$defs"], "$ref": "#/$defs/time_base_annotation"})
    for row in (_row("disputed:sibling_constants_offset", SITE, "disputed"),
                _row("corrected:utc_instant->utc_minus_8:eot20_family+sibling_constants", FAMILY, "corrected", published="utc_minus_8"),
                _row("disputed:eot20_whole_hour", WHOLE, "disputed")):
        assert list(v.iter_errors(_ann(row))) == []


def test_a_weak_check_and_the_sibling_summary_do_not_duplicate_eot20():
    row = _row("disputed:eot20_whole_hour", WHOLE, "disputed", weak_eot20="+9 (+542.5)")
    kinds = [(e["comparator_kind"], e["comparator_id"]) for e in _ann(row)["evidence"]]
    assert kinds.count(("model", "eot20")) == 1


def test_a_w_row_with_no_evidence_at_all_is_still_refused():
    with pytest.raises(gs.NotPublishable, match="no evidence"):
        _ann(_row("disputed:sibling_constants_offset", "", "disputed"))
    with pytest.raises(gs.NotPublishable, match="no evidence"):
        _ann(_row("disputed:sibling_constants_offset", "something TB-SIB has not said before", "disputed"))


def test_a_negative_site_shift_keeps_its_sign():
    # Wewak BOM 56070: corrected utc_plus_1 -> utc_instant against 063A, -61 min
    text = ("fitted constants -61.0 min against 1 record(s) of the site (wewak-063a-png-uhslc_rq); side by eot20 "
            "(EOT20 -61.0 here, +0.0 there)")
    tb = _ann(_row("disputed:sibling_constants_offset", text, "disputed"))
    assert [(e["comparator_id"], e["lag_min"]) for e in tb["evidence"]] == [("wewak-063a-png-uhslc_rq", -61.0), ("eot20", -61.0)]
