import logging

import pytest

from tests.fake_kiwoom import FakeKiwoom, body_of, kiwoom_error, usa10099_row

URL = "/api/environments/us_paper/open-orders"

# kra-docs ust21050 responseExample의 한 줄.
US_OPEN_ORDER = {
    "ord_cntr_tp": "10",
    "ord_no": "000000282",
    "orig_ord_no": "000000000",
    "frgn_ord_id": "NF.19ef9aa9-6f51-0000-19ef-9aa96f510000",
    "stex_nm": "미국",
    "crnc_code": "USD",
    "stk_cd": "NVDA",
    "frgn_stk_nm": "엔비디아",
    "frgn_trde_tp": "00",
    "frgn_trde_nm": "지정가",
    "slby_tp": "2",
    "slby_tp_nm": "매수",
    "ord_qty": "000000000005",
    "ord_uv": "200.0000",
    "stop_pric": "0.0000",
    "cntr_qty": "000000000000",
    "cntr_uv": "0.0000",
    "mdfy_qty": "000000000000",
    "mdfy_uv": "0.0000",
    "cncl_qty": "000000000000",
    "ord_remnq": "000000000005",
    "ord_time": "21:46:06",
    "ord_resp_time": "21:46:07",
    "ord_stat": "접수",
    "rsrv_tp": "일반",
    "natn_nm": "미국",
}

LISTING = [
    usa10099_row("NVDA", "엔비디아", "NVIDIA CORP"),
    usa10099_row("EWZ", "아이셰어즈 MSCI 브라질 ETF", "ISHARES MSCI BRAZIL ETF", "NY"),
]


def us_open_order(**overrides: object) -> dict[str, object]:
    return {**US_OPEN_ORDER, **overrides}


def us_kiwoom(*orders: dict[str, object], listing: object = None) -> FakeKiwoom:
    rows = list(orders) if orders else [US_OPEN_ORDER]
    return (
        FakeKiwoom()
        .reply("ust21050", {"result_list": rows})
        .reply("usa10099", listing if listing is not None else {"list": LISTING})
    )


def test_us_open_orders_are_listed_from_ust21050_with_listing_names(make_client):
    sell = us_open_order(
        ord_no="000000283",
        stk_cd="EWZ",
        frgn_stk_nm="ISHARES MSCI BRAZIL",
        slby_tp="1",
        slby_tp_nm="매도",
        ord_qty="000000000010",
        ord_remnq="000000000003",
        ord_uv="31.2500",
        ord_time="22:01:30",
    )
    fake = us_kiwoom(US_OPEN_ORDER, sell)

    response = make_client(fake).get(URL)

    assert response.status_code == 200
    assert response.json()["orders"] == [
        {
            "order_no": "000000282",
            "code": "NVDA",
            "name": "엔비디아",
            "side": "buy",
            "side_label": "매수",
            "order_type": "지정가",
            "price": 200.0,
            "ordered_quantity": 5,
            "remaining_quantity": 5,
            "time": "21:46:06",
            "exchange": "NASDAQ",
            "cancelable": True,
            "blocked_reason": None,
        },
        {
            "order_no": "000000283",
            "code": "EWZ",
            "name": "아이셰어즈 MSCI 브라질 ETF",
            "side": "sell",
            "side_label": "매도",
            "order_type": "지정가",
            "price": 31.25,
            "ordered_quantity": 10,
            "remaining_quantity": 3,
            "time": "22:01:30",
            "exchange": "NYSE",
            "cancelable": True,
            "blocked_reason": None,
        },
    ]
    [request] = fake.calls("ust21050")
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/us/acnt"
    assert body_of(request) == {"ord_dt": "", "slby_tp": "0", "stex_tp": "", "stk_cd": ""}


def test_cancel_order_rows_are_hidden_and_modified_orders_are_labelled(make_client):
    fake = us_kiwoom(
        us_open_order(ord_no="000000300", ord_cntr_tp="12"),
        us_open_order(ord_no="000000301", ord_cntr_tp="11", slby_tp="1", slby_tp_nm="매도"),
    )

    orders = make_client(fake).get(URL).json()["orders"]

    assert [(o["order_no"], o["side_label"]) for o in orders] == [("000000301", "매도정정")]


@pytest.mark.parametrize(
    "listing",
    [{"list": [usa10099_row("AAPL", "애플", "APPLE INC")]}, kiwoom_error(1, "목록 실패")],
    ids=["ticker_not_listed", "listing_failed"],
)
def test_orders_whose_exchange_is_unknown_are_listed_but_not_cancelable(
    make_client, caplog, listing
):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = us_kiwoom(US_OPEN_ORDER, listing=listing)

    response = make_client(fake).get(URL)

    assert response.status_code == 200
    [order] = response.json()["orders"]
    assert order["name"] == "엔비디아"
    assert order["exchange"] is None
    assert order["cancelable"] is False
    assert order["blocked_reason"] == "exchange"
    unlisted = [r for r in caplog.records if r.getMessage() == "open_order_tickers_unlisted"]
    assert len(unlisted) == 1
    assert unlisted[0].fields["tickers"] == "NVDA"  # type: ignore[attr-defined]


@pytest.mark.parametrize("rsrv_tp", ["예약", "1"])
def test_reserved_orders_are_not_cancelable(make_client, rsrv_tp):
    fake = us_kiwoom(us_open_order(rsrv_tp=rsrv_tp))

    [order] = make_client(fake).get(URL).json()["orders"]

    assert order["blocked_reason"] == "reserved"


def test_us_real_trading_lists_open_orders_but_none_are_cancelable(make_client):
    fake = us_kiwoom(US_OPEN_ORDER, us_open_order(ord_no="000000290", rsrv_tp="예약"))

    response = make_client(fake).get("/api/environments/us_real/open-orders")

    assert [o["blocked_reason"] for o in response.json()["orders"]] == ["real", "real"]
    assert fake.calls("ust21050")[0].url.host == "api.kiwoom.com"


def test_market_orders_have_no_price(make_client):
    fake = us_kiwoom(us_open_order(frgn_trde_tp="03", frgn_trde_nm="시장가", ord_uv="0.0000"))

    [order] = make_client(fake).get(URL).json()["orders"]

    assert order["order_type"] == "시장가"
    assert order["price"] is None


@pytest.mark.parametrize(
    "overrides", [{"ord_no": ""}, {"stk_cd": ""}, {"ord_remnq": ""}, {"ord_remnq": "abc"}]
)
def test_broken_us_required_fields_are_a_response_format_error(make_client, overrides):
    response = make_client(us_kiwoom(us_open_order(**overrides))).get(URL)

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "response_format_error"


@pytest.mark.parametrize("overrides", [{"ord_uv": ". 950"}, {"ord_qty": "abc"}])
def test_unreadable_us_price_or_quantity_only_blanks_that_value(make_client, overrides):
    response = make_client(us_kiwoom(us_open_order(**overrides))).get(URL)

    assert response.status_code == 200
    [order] = response.json()["orders"]
    key = "price" if "ord_uv" in overrides else "ordered_quantity"
    assert order[key] is None
