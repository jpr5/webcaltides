"""The adapters' licence objects say what the providers' terms ask for (ADPORT review C).

- CC BY 4.0 (Kartverket, LINZ, Marine Institute): the attribution says the data were modified
  (licence 3(a)(1)(B)); LINZ's own wording for adapted work; Kartverket's credit "© Kartverket" with
  a link, and its terms page (kartverket.no/api-og-data/vilkar-for-bruk).
- NOAA: not "unmodified" (units are converted); not official NOAA material (NOAA's disclaimer).
- JMA: the attribution names the page it comes from (JMA's PDL 1.0 example form).
- PEGELONLINE and MI: `url` is the provider's page that applies the licence to this data.
"""
from __future__ import annotations

import pytest

from otc_pipeline.sources import SOURCES

LINZ_ADAPTED = ("This work is based on Toitū Te Whenua Land Information New Zealand data which are licensed by "
                "Toitū Te Whenua Land Information New Zealand for re-use under the Creative Commons Attribution "
                "4.0 International licence.")


def lic(source):
    return SOURCES[source]().licence()


@pytest.mark.parametrize("source", ["kartverket", "linz", "marine-institute"])
def test_cc_by_attributions_say_the_data_were_modified(source):
    a = lic(source)["attribution"]
    assert "Modified by OpenTideConstants" in a or "Adapted by OpenTideConstants" in a, a


def test_kartverket_credit_link_and_terms_page():
    lc = lic("kartverket")
    assert lc["attribution"].startswith("© Kartverket (https://kartverket.no)"), lc["attribution"]
    assert lc["url"] == "https://kartverket.no/api-og-data/vilkar-for-bruk"


def test_linz_uses_its_wording_for_adapted_work():
    assert lic("linz")["attribution"].startswith(LINZ_ADAPTED)


@pytest.mark.parametrize("source", ["noaa", "noaa-currents"])
def test_noaa_attribution_is_accurate(source):
    a = lic(source)["attribution"]
    assert "nmodified" not in a and "not official NOAA material" in a and "converted" in a, a
    assert "not subject to copyright" in a


def test_jma_attribution_names_its_page():
    assert "(https://www.data.jma.go.jp/kaiyou/db/tide/suisan/)" in lic("jma")["attribution"]


def test_pegelonline_and_mi_point_at_the_provider_pages_that_apply_the_licence():
    assert lic("pegelonline")["url"] == "https://pegelonline.wsv.de/gast/nutzungsbedingungen"
    assert lic("marine-institute")["url"] == \
        "https://erddap.marine.ie/erddap/info/IrishNationalTideGaugeNetwork/index.html"


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_every_licence_still_states_commercial_use(source):
    assert lic(source)["commercial_use"] is True
