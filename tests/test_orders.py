import asyncio
import logging
import threading

import httpx
import pytest

from stock_bot.app import create_app
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom, ThreadedTransport, body_of, kiwoom_error

DOMESTIC_URL = "/api/environments/domestic_paper/orders"
ORDER_REPLY = {"ord_no": "00024"}


def domestic_order(**overrides: object) -> dict[str, object]:
    return {
        "order_key": "key-1",
        "side": "buy",
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
        {k: v for k, v in domestic_order().items() if k != "side"},
        domestic_order(side="short"),
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
        "side": "buy",
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


def test_the_same_order_key_is_sent_to_kiwoom_only_once(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    client = make_client(fake)

    first = client.post(DOMESTIC_URL, json=domestic_order(order_key="key-dup"))
    second = client.post(DOMESTIC_URL, json=domestic_order(order_key="key-dup"))

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["kind"] == "duplicate_order"
    assert len(fake.calls("kt10000")) == 1
    duplicates = [r for r in caplog.records if r.getMessage() == "order_duplicate"]
    assert duplicates[0].fields["order_key"] == "key-dup"  # type: ignore[attr-defined]


def test_a_rejected_order_key_is_still_not_reused(make_client):
    fake = FakeKiwoom().reply("kt10000", kiwoom_error(20, "거부"), ORDER_REPLY)
    client = make_client(fake)
    assert client.post(DOMESTIC_URL, json=domestic_order(order_key="k")).status_code == 502
    assert client.post(DOMESTIC_URL, json=domestic_order(order_key="k")).status_code == 409
    assert len(fake.calls("kt10000")) == 1


def test_order_keys_are_kept_per_environment(make_client):
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY).reply("ust20000", {"ord_no": "1"})
    client = make_client(fake)
    assert client.post(DOMESTIC_URL, json=domestic_order(order_key="same")).status_code == 200
    assert client.post(US_URL, json=us_order(order_key="same")).status_code == 200


@pytest.mark.anyio
async def test_concurrent_requests_with_the_same_key_send_one_order():
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    gate = threading.Event()

    def slow_order(request: httpx.Request) -> None:
        if request.headers.get("api-id") == "kt10000":
            gate.wait(timeout=1)

    fake.on_request = slow_order
    app = create_app(
        environ=FAKE_ENV, transport=ThreadedTransport(fake), static_dir=None, log_dir=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        order = domestic_order(order_key="key-race")
        pending = [asyncio.create_task(client.post(DOMESTIC_URL, json=order)) for _ in range(3)]
        await asyncio.sleep(0.05)
        gate.set()
        responses = await asyncio.gather(*pending)

    assert sorted(r.status_code for r in responses) == [200, 409, 409]
    assert len(fake.calls("kt10000")) == 1


def test_connection_failure_is_an_unknown_result_without_resending(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = FakeKiwoom()

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    fake.respond("kt10000", broken)
    response = make_client(fake).post(DOMESTIC_URL, json=domestic_order())

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "order_result_unknown"
    assert len(fake.calls("kt10000")) == 1
    unknown = [r for r in caplog.records if r.getMessage() == "order_result_unknown"]
    assert unknown[0].fields["cause"]  # type: ignore[attr-defined]


def test_malformed_order_responses_are_unknown_results(make_client):
    for reply in (
        {},  # return_code=0인데 주문번호가 없음
        {"ord_no": ""},  # 주문번호가 빈 값
        httpx.Response(200, text="<html>bad gateway</html>"),
        httpx.Response(502, json={"message": "no return_code"}),
    ):
        fake = FakeKiwoom().reply("kt10000", reply)
        response = make_client(fake).post(DOMESTIC_URL, json=domestic_order())
        assert response.status_code == 502, reply
        assert response.json()["error"]["kind"] == "order_result_unknown"
        assert len(fake.calls("kt10000")) == 1


def test_us_price_decimals_follow_kiwoom_tick_rule(make_client):
    """키움 1517 응답: $1 미만은 소수 넷째 자리, $1 이상은 소수 둘째 자리까지 받는다."""
    fake = FakeKiwoom().reply("ust20000", {"ord_no": "000000290"})
    client = make_client(fake)
    for price in ("629.71", "629.7", "629", "0.1234", "0.99"):
        order = us_order(order_key=f"ok-{price}", price=price)
        assert client.post(US_URL, json=order).status_code == 200, price
    for price in ("629.71323", "629.715", "1.001", "1.0000", "0.12345"):
        response = client.post(US_URL, json=us_order(order_key=f"bad-{price}", price=price))
        assert response.status_code == 400, price
        assert "소수" in response.json()["error"]["message"]
    assert len(fake.calls("ust20000")) == 5
