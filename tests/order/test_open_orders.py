import pytest

from tests.fake_kiwoom import FakeKiwoom, body_of, page_response
from tests.order.helpers import OPEN_ORDER, open_order

URL = "/api/environments/domestic_paper/open-orders"


def test_open_orders_are_listed_from_ka10075_on_the_paper_domain(make_client):
    limit_sell = open_order(
        ord_no="0000070",
        stk_cd="000660",
        stk_nm="SK하이닉스",
        io_tp_nm="-매도",
        trde_tp="보통",
        ord_pric="-201000",
        ord_qty="10",
        oso_qty="3",
        tm="093005",
    )
    fake = FakeKiwoom().reply("ka10075", {"oso": [OPEN_ORDER, limit_sell]})
    client = make_client(fake)

    response = client.get(URL)

    assert response.status_code == 200
    data = response.json()
    assert data["orders"] == [
        {
            "order_no": "0000069",
            "code": "005930",
            "name": "삼성전자",
            "side": "buy",
            "side_label": "매수",
            "order_type": "시장가",
            "price": None,
            "ordered_quantity": 1,
            "remaining_quantity": 1,
            "time": "15:41:13",
            "exchange": "KRX",
            "cancelable": True,
            "blocked_reason": None,
        },
        {
            "order_no": "0000070",
            "code": "000660",
            "name": "SK하이닉스",
            "side": "sell",
            "side_label": "매도",
            "order_type": "지정가",
            "price": 201000,
            "ordered_quantity": 10,
            "remaining_quantity": 3,
            "time": "09:30:05",
            "exchange": "KRX",
            "cancelable": True,
            "blocked_reason": None,
        },
    ]
    assert data["fetched_at"]
    [request] = fake.calls("ka10075")
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/dostk/acnt"
    assert body_of(request) == {"all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "0"}


def test_open_orders_follow_continuation_pages(make_client):
    first = page_response({"oso": [OPEN_ORDER]}, cont_yn="Y", next_key="next-1")
    second = page_response({"oso": [open_order(ord_no="0000071")]})
    fake = FakeKiwoom().reply("ka10075", first, second)
    client = make_client(fake)

    response = client.get(URL)

    assert [o["order_no"] for o in response.json()["orders"]] == ["0000069", "0000071"]
    assert fake.calls("ka10075")[1].headers["next-key"] == "next-1"


def test_no_open_orders_is_an_empty_list(make_client):
    client = make_client(FakeKiwoom().reply("ka10075", {"oso": []}))

    assert client.get(URL).json()["orders"] == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"io_tp_nm": "+매수신용"}, "credit"),
        ({"stex_tp": "2", "stex_tp_txt": "NXT"}, "exchange"),
        ({"stex_tp": "0", "stex_tp_txt": "통합", "sor_yn": "Y"}, "exchange"),
        ({"sor_yn": "Y"}, "exchange"),
        # 신용이면서 NXT여도 이유는 하나만 준다.
        ({"io_tp_nm": "-매도신용", "stex_tp": "2", "stex_tp_txt": "NXT"}, "credit"),
    ],
)
def test_only_krx_cash_orders_are_cancelable(make_client, overrides, reason):
    client = make_client(FakeKiwoom().reply("ka10075", {"oso": [open_order(**overrides)]}))

    [order] = client.get(URL).json()["orders"]

    assert order["cancelable"] is False
    assert order["blocked_reason"] == reason


def test_real_trading_lists_open_orders_but_none_are_cancelable(make_client):
    fake = FakeKiwoom().reply("ka10075", {"oso": [OPEN_ORDER, open_order(io_tp_nm="+매수신용")]})
    client = make_client(fake)

    response = client.get("/api/environments/domestic_real/open-orders")

    assert response.status_code == 200
    assert [(o["cancelable"], o["blocked_reason"]) for o in response.json()["orders"]] == [
        (False, "real"),
        (False, "real"),
    ]
    assert fake.calls("ka10075")[0].url.host == "api.kiwoom.com"


@pytest.mark.parametrize(
    "overrides",
    [{"ord_no": ""}, {"stk_cd": ""}, {"oso_qty": ""}, {"oso_qty": "abc"}],
)
def test_broken_open_order_fields_are_a_response_format_error(make_client, overrides):
    client = make_client(FakeKiwoom().reply("ka10075", {"oso": [open_order(**overrides)]}))

    response = client.get(URL)

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "response_format_error"


@pytest.mark.parametrize("overrides", [{"ord_pric": ". 950"}, {"ord_qty": "abc"}])
def test_an_unreadable_price_or_ordered_quantity_only_blanks_that_value(make_client, overrides):
    client = make_client(FakeKiwoom().reply("ka10075", {"oso": [open_order(**overrides)]}))

    response = client.get(URL)

    assert response.status_code == 200
    [order] = response.json()["orders"]
    key = "price" if "ord_pric" in overrides else "ordered_quantity"
    assert order[key] is None
    assert order["remaining_quantity"] == 1
