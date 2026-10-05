import logging

from tests.fake_kiwoom import FakeKiwoom, body_of, kiwoom_error

DOMESTIC_URL = "/api/environments/domestic_paper/orders"
ORDER_REPLY = {"ord_no": "00024"}


def domestic_order(**overrides: object) -> dict[str, object]:
    return {
        "order_key": "key-1",
        "code": "005930",
        "order_type": "limit",
        "quantity": "3",
        "price": "61300",
        **overrides,
    }


def test_domestic_limit_order_is_sent_to_kt10000_on_the_paper_domain(make_client):
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    response = make_client(fake).post(DOMESTIC_URL, json=domestic_order())

    assert response.status_code == 200
    request = fake.calls("kt10000")[0]
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/dostk/ordr"
    assert body_of(request) == {
        "dmst_stex_tp": "KRX",
        "stk_cd": "005930",
        "ord_qty": "3",
        "ord_uv": "61300",
        "trde_tp": "0",
        "cond_uv": "",
    }
    body = response.json()
    assert body["order_key"] == "key-1"
    assert body["order_no"] == "00024"
    assert body["accepted_at"]


def test_real_environment_orders_are_refused_before_any_kiwoom_request(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    client = make_client(fake)
    for environment in ("domestic_real", "us_real"):
        # 입력이 틀려도 실전 차단이 먼저다.
        for order in (domestic_order(), {"order_key": "x"}):
            response = client.post(f"/api/environments/{environment}/orders", json=order)
            assert response.status_code == 403
            assert response.json()["error"]["kind"] == "order_not_allowed"
    assert fake.requests == []
    assert any(r.getMessage() == "order_blocked_real" for r in caplog.records)


def test_invalid_orders_are_rejected_without_calling_kiwoom(make_client):
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    client = make_client(fake)
    invalid = [
        domestic_order(order_key=""),
        {k: v for k, v in domestic_order().items() if k != "order_key"},
        domestic_order(code=""),
        domestic_order(order_type="stop"),
        domestic_order(quantity="0"),
        domestic_order(quantity="1.5"),
        domestic_order(quantity="1" * 13),
        domestic_order(quantity=3),
        domestic_order(price="0"),
        domestic_order(price="1000.5"),
        domestic_order(price="1" * 13),
        {k: v for k, v in domestic_order().items() if k != "price"},
    ]
    for order in invalid:
        response = client.post(DOMESTIC_URL, json=order)
        assert response.status_code == 400, order
        assert response.json()["error"]["kind"] == "invalid_request"
    assert client.post(DOMESTIC_URL, content=b"not json").status_code == 400
    assert client.post(DOMESTIC_URL, json=["not", "an", "object"]).status_code == 400
    assert fake.requests == []


def test_twelve_digit_values_are_passed_to_kiwoom_as_typed(make_client):
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    response = make_client(fake).post(
        DOMESTIC_URL, json=domestic_order(quantity="9" * 12, price="9" * 12)
    )

    assert response.status_code == 200
    body = body_of(fake.calls("kt10000")[0])
    assert body["ord_qty"] == "9" * 12
    assert body["ord_uv"] == "9" * 12


def test_kiwoom_rejection_is_returned_without_resending(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = FakeKiwoom().reply("kt10000", kiwoom_error(20, "주문가능금액이 부족합니다"))
    response = make_client(fake).post(DOMESTIC_URL, json=domestic_order())

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["kind"] == "kiwoom_error"
    assert "주문가능금액이 부족합니다" in error["message"]
    assert len(fake.calls("kt10000")) == 1
    assert any(r.getMessage() == "order_rejected" for r in caplog.records)


def test_invalid_token_is_not_resent_and_the_next_order_uses_a_new_token(make_client):
    fake = FakeKiwoom().reply(
        "kt10000", kiwoom_error(8005, "토큰이 유효하지 않습니다"), ORDER_REPLY
    )
    client = make_client(fake)

    first = client.post(DOMESTIC_URL, json=domestic_order(order_key="key-1"))
    assert first.status_code == 502
    assert first.json()["error"]["kind"] == "token_expired"
    assert len(fake.calls("kt10000")) == 1

    second = client.post(DOMESTIC_URL, json=domestic_order(order_key="key-2"))
    assert second.status_code == 200
    assert len(fake.token_requests()) == 2
    assert fake.calls("kt10000")[1].headers["authorization"] == f"Bearer {fake.issued_tokens[1]}"


def test_order_events_are_logged_with_the_order_key(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    make_client(fake).post(DOMESTIC_URL, json=domestic_order(order_key="key-77"))

    events = {r.getMessage(): getattr(r, "fields", {}) for r in caplog.records}
    assert events["order_requested"]["order_key"] == "key-77"
    assert events["order_requested"]["quantity"] == "3"
    assert events["order_accepted"]["order_no"] == "00024"


US_URL = "/api/environments/us_paper/orders"


def us_order(**overrides: object) -> dict[str, object]:
    return {
        "order_key": "key-us-1",
        "code": "NVDA",
        "exchange": "NASDAQ",
        "order_type": "limit",
        "quantity": "10",
        "price": "213.04",
        **overrides,
    }


def test_domestic_market_order_sends_trde_tp_3_without_a_price(make_client):
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    order = {k: v for k, v in domestic_order(order_type="market").items() if k != "price"}
    response = make_client(fake).post(DOMESTIC_URL, json=order)

    assert response.status_code == 200
    body = body_of(fake.calls("kt10000")[0])
    assert body["trde_tp"] == "3"
    assert body["ord_uv"] == ""


def test_market_order_with_a_price_is_rejected(make_client):
    fake = FakeKiwoom()
    client = make_client(fake)
    assert client.post(DOMESTIC_URL, json=domestic_order(order_type="market")).status_code == 400
    assert client.post(US_URL, json=us_order(order_type="market")).status_code == 400
    assert fake.requests == []


def test_us_limit_order_is_sent_to_ust20000(make_client):
    fake = FakeKiwoom().reply("ust20000", {"ord_no": "000000282", "stk_nm": "엔비디아"})
    response = make_client(fake).post(US_URL, json=us_order())

    assert response.status_code == 200
    assert response.json()["order_no"] == "000000282"
    request = fake.calls("ust20000")[0]
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/us/ordr"
    assert body_of(request) == {
        "stex_tp": "ND",
        "stk_cd": "NVDA",
        "ord_qty": "10",
        "ord_uv": "213.04",
        "trde_tp": "00",
    }


def test_us_market_order_sends_trde_tp_03_without_a_price(make_client):
    fake = FakeKiwoom().reply("ust20000", {"ord_no": "000000283"})
    order = {k: v for k, v in us_order(order_type="market").items() if k != "price"}
    response = make_client(fake).post(US_URL, json=order)

    assert response.status_code == 200
    body = body_of(fake.calls("ust20000")[0])
    assert body["trde_tp"] == "03"
    assert body["ord_uv"] == ""


def test_us_price_accepts_decimals_up_to_twelve_characters(make_client):
    fake = FakeKiwoom().reply("ust20000", {"ord_no": "000000284"})
    client = make_client(fake)
    assert client.post(US_URL, json=us_order(price="0.0001")).status_code == 200
    assert body_of(fake.calls("ust20000")[0])["ord_uv"] == "0.0001"
    for price in ("0", "0.0000", "1234567890123", "213.040000000", "1e3", "-1"):
        assert client.post(US_URL, json=us_order(price=price)).status_code == 400, price


def test_us_order_needs_a_supported_exchange(make_client):
    fake = FakeKiwoom()
    client = make_client(fake)
    for exchange in (None, "", "OTC"):
        response = client.post(US_URL, json=us_order(exchange=exchange))
        assert response.status_code == 400, exchange
    assert fake.requests == []
