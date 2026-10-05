import asyncio
import logging
import threading

import httpx
import pytest

from stock_bot.app import create_app
from tests.fake_kiwoom import (
    FAKE_ENV,
    KT00018_HOLDING,
    KT00018_REPLY,
    UST21070_HOLDING,
    UST21070_REPLY,
    FakeKiwoom,
    ThreadedTransport,
    body_of,
    kiwoom_error,
    page_response,
)

DOMESTIC_URL = "/api/environments/domestic_paper/orders"
SELL_REPLY = {"ord_no": "0000138", "dmst_stex_tp": "KRX"}


def sell_order(**overrides: object) -> dict[str, object]:
    return {
        "order_key": "sell-1",
        "side": "sell",
        "code": "005930",
        "order_type": "limit",
        "quantity": "3",
        "price": "61300",
        **overrides,
    }


def balance(*holdings: dict[str, object]) -> dict[str, object]:
    return {**KT00018_REPLY, "acnt_evlt_remn_indv_tot": list(holdings)}


def holding(**overrides: object) -> dict[str, object]:
    return {**KT00018_HOLDING, **overrides}


def test_domestic_limit_sell_checks_the_balance_then_sends_kt10001(make_client):
    fake = FakeKiwoom().reply("kt00018", balance(holding())).reply("kt10001", SELL_REPLY)
    response = make_client(fake).post(DOMESTIC_URL, json=sell_order())

    assert response.status_code == 200
    assert response.json()["order_no"] == "0000138"
    assert [r.headers["api-id"] for r in fake.requests if "api-id" in r.headers] == [
        "kt00018",
        "kt10001",
    ]
    assert body_of(fake.calls("kt00018")[0]) == {"qry_tp": "1", "dmst_stex_tp": "KRX"}
    request = fake.calls("kt10001")[0]
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


def test_selling_exactly_the_sellable_quantity_is_accepted(make_client):
    fake = FakeKiwoom().reply(
        "kt00018", balance(holding(rmnd_qty="000000000000010", trde_able_qty="000000000000007"))
    )
    fake.reply("kt10001", SELL_REPLY)
    response = make_client(fake).post(DOMESTIC_URL, json=sell_order(quantity="7"))

    assert response.status_code == 200
    assert body_of(fake.calls("kt10001")[0])["ord_qty"] == "7"


def test_selling_more_than_the_sellable_quantity_is_refused_without_ordering(make_client):
    fake = FakeKiwoom().reply(
        "kt00018", balance(holding(rmnd_qty="000000000000010", trde_able_qty="000000000000007"))
    )
    response = make_client(fake).post(DOMESTIC_URL, json=sell_order(quantity="8"))

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["kind"] == "sellable_exceeded"
    assert "7주" in error["message"]
    assert fake.calls("kt10001") == []


def test_a_stock_not_held_has_nothing_to_sell(make_client):
    fake = FakeKiwoom().reply("kt00018", balance(holding(stk_cd="A000660")))
    response = make_client(fake).post(DOMESTIC_URL, json=sell_order(quantity="1"))

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "sellable_exceeded"
    assert fake.calls("kt10001") == []


def test_credit_holdings_are_not_counted_as_cash_sellable(make_client):
    credit = holding(crd_tp="03", rmnd_qty="000000000000005", trde_able_qty="000000000000005")
    fake = FakeKiwoom().reply("kt00018", balance(credit))
    client = make_client(fake)
    only_credit = client.post(DOMESTIC_URL, json=sell_order(order_key="a", quantity="1"))
    assert only_credit.status_code == 400
    assert only_credit.json()["error"]["kind"] == "sellable_exceeded"

    cash = holding(rmnd_qty="000000000000002", trde_able_qty="000000000000002")
    fake.reply("kt00018", balance(credit, cash)).reply("kt10001", SELL_REPLY)
    over = client.post(DOMESTIC_URL, json=sell_order(order_key="b", quantity="3"))
    exact = client.post(DOMESTIC_URL, json=sell_order(order_key="c", quantity="2"))
    assert over.status_code == 400
    assert exact.status_code == 200
    assert len(fake.calls("kt10001")) == 1


def test_the_balance_is_read_across_pages(make_client):
    other = holding(stk_cd="A000660")
    fake = FakeKiwoom().reply(
        "kt00018",
        page_response(balance(other), cont_yn="Y", next_key="next-1"),
        balance(holding()),
    )
    fake.reply("kt10001", SELL_REPLY)
    response = make_client(fake).post(DOMESTIC_URL, json=sell_order())

    assert response.status_code == 200
    assert len(fake.calls("kt00018")) == 2


def test_two_cash_rows_for_one_stock_are_an_unsupported_balance(make_client):
    """kt00018 문서 예제 그대로: 합산 조회인데 같은 종목의 현금잔고가 두 줄이다."""
    fake = FakeKiwoom().reply(
        "kt00018",
        balance(
            holding(rmnd_qty="000000000000003", trde_able_qty="000000000000003"),
            holding(rmnd_qty="000000000000008", trde_able_qty="000000000000008"),
        ),
    )
    response = make_client(fake).post(DOMESTIC_URL, json=sell_order(quantity="1"))

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["kind"] == "sellable_check_failed"
    assert "같은 종목이 여러 줄" in error["message"]
    assert fake.calls("kt10001") == []


def broken(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    "reply",
    [
        kiwoom_error(20, "조회 실패"),
        httpx.Response(200, text="<html>bad gateway</html>"),
        balance(holding(trde_able_qty="")),
        balance(holding(trde_able_qty=". 950")),
        balance({k: v for k, v in KT00018_HOLDING.items() if k != "crd_tp"}),
    ],
)
def test_a_failed_balance_check_is_a_failure_not_an_unknown_result(make_client, reply):
    fake = FakeKiwoom().reply("kt00018", reply)
    response = make_client(fake).post(DOMESTIC_URL, json=sell_order())

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["kind"] == "sellable_check_failed"
    assert error["message"].startswith("잔고를 확인하지 못해 주문하지 않았습니다.")
    assert fake.calls("kt10001") == []


def test_a_balance_connection_failure_does_not_send_the_order(make_client):
    fake = FakeKiwoom().respond("kt00018", broken)
    response = make_client(fake).post(DOMESTIC_URL, json=sell_order())

    assert response.json()["error"]["kind"] == "sellable_check_failed"
    assert fake.calls("kt10001") == []


def test_real_environment_sells_send_nothing_to_kiwoom(make_client):
    fake = FakeKiwoom().reply("kt00018", balance(holding())).reply("kt10001", SELL_REPLY)
    response = make_client(fake).post("/api/environments/domestic_real/orders", json=sell_order())

    assert response.status_code == 403
    assert fake.requests == []


def test_domestic_market_sell_sends_trde_tp_3_without_a_price(make_client):
    fake = FakeKiwoom().reply("kt00018", balance(holding())).reply("kt10001", SELL_REPLY)
    client = make_client(fake)
    order = {k: v for k, v in sell_order(order_type="market").items() if k != "price"}
    assert client.post(DOMESTIC_URL, json=order).status_code == 200
    body = body_of(fake.calls("kt10001")[0])
    assert body["trde_tp"] == "3"
    assert body["ord_uv"] == ""

    with_price = sell_order(order_key="priced", order_type="market")
    assert client.post(DOMESTIC_URL, json=with_price).status_code == 400
    assert len(fake.calls("kt10001")) == 1


US_URL = "/api/environments/us_paper/orders"
US_SELL_REPLY = {"ord_no": "000000283", "stk_nm": "애플", "poss_qty": "000000000395"}


def us_sell(**overrides: object) -> dict[str, object]:
    return {
        "order_key": "us-sell-1",
        "side": "sell",
        "code": "AAPL",
        "exchange": "NASDAQ",
        "order_type": "limit",
        "quantity": "10",
        "price": "275.24",
        **overrides,
    }


def us_balance(*holdings: dict[str, object]) -> dict[str, object]:
    return {**UST21070_REPLY, "result_list": list(holdings)}


def us_holding(**overrides: object) -> dict[str, object]:
    return {**UST21070_HOLDING, **overrides}


def test_us_limit_sell_checks_the_ticker_balance_then_sends_ust20001(make_client):
    fake = FakeKiwoom().reply("ust21070", us_balance(us_holding()))
    fake.reply("ust20001", US_SELL_REPLY)
    response = make_client(fake).post(US_URL, json=us_sell())

    assert response.status_code == 200
    assert response.json()["order_no"] == "000000283"
    assert body_of(fake.calls("ust21070")[0]) == {"stex_tp": "", "stk_cd": "AAPL"}
    request = fake.calls("ust20001")[0]
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/us/ordr"
    assert body_of(request) == {
        "stex_tp": "ND",
        "stk_cd": "AAPL",
        "ord_qty": "10",
        "ord_uv": "275.24",
        "stop_pric": "",
        "trde_tp": "00",
    }


def test_us_market_sell_sends_trde_tp_03_without_a_price(make_client):
    fake = FakeKiwoom().reply("ust21070", us_balance(us_holding()))
    fake.reply("ust20001", US_SELL_REPLY)
    order = {k: v for k, v in us_sell(order_type="market").items() if k != "price"}
    assert make_client(fake).post(US_URL, json=order).status_code == 200
    body = body_of(fake.calls("ust20001")[0])
    assert body["trde_tp"] == "03"
    assert body["ord_uv"] == ""


def test_us_sell_inputs_follow_the_buy_rules(make_client):
    fake = FakeKiwoom()
    client = make_client(fake)
    for order in (
        us_sell(order_key="a", exchange="OTC"),
        us_sell(order_key="b", exchange=None),
        us_sell(order_key="c", price="275.245"),
    ):
        assert client.post(US_URL, json=order).status_code == 400, order
    assert fake.requests == []


def test_us_sells_are_limited_to_the_sellable_quantity(make_client):
    held = us_holding(poss_qty="000000000010", sell_alowq="000000000004")
    fake = FakeKiwoom().reply("ust21070", us_balance(held)).reply("ust20001", US_SELL_REPLY)
    client = make_client(fake)

    over = client.post(US_URL, json=us_sell(order_key="over", quantity="5"))
    assert over.status_code == 400
    assert over.json()["error"]["kind"] == "sellable_exceeded"
    assert "4주" in over.json()["error"]["message"]
    assert client.post(US_URL, json=us_sell(order_key="exact", quantity="4")).status_code == 200
    assert len(fake.calls("ust20001")) == 1

    fake.reply("ust21070", us_balance())
    missing = client.post(US_URL, json=us_sell(order_key="missing", quantity="1"))
    assert missing.json()["error"]["kind"] == "sellable_exceeded"
    assert len(fake.calls("ust20001")) == 1


@pytest.mark.parametrize(
    "reply",
    [
        kiwoom_error(20, "조회 실패"),
        us_balance(us_holding(sell_alowq="")),
        us_balance(us_holding(sell_alowq=". 950")),
        us_balance(us_holding(), us_holding()),
    ],
)
def test_a_failed_us_balance_check_does_not_send_the_order(make_client, reply):
    fake = FakeKiwoom().reply("ust21070", reply)
    response = make_client(fake).post(US_URL, json=us_sell())

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "sellable_check_failed"
    assert fake.calls("ust20001") == []


def test_us_real_sells_send_nothing_and_repeated_keys_order_once(make_client):
    fake = FakeKiwoom().reply("ust21070", us_balance(us_holding()))
    fake.reply("ust20001", US_SELL_REPLY)
    client = make_client(fake)
    assert client.post("/api/environments/us_real/orders", json=us_sell()).status_code == 403
    assert fake.requests == []

    assert client.post(US_URL, json=us_sell()).status_code == 200
    assert client.post(US_URL, json=us_sell()).status_code == 409
    assert len(fake.calls("ust21070")) == 1
    assert len(fake.calls("ust20001")) == 1


def test_a_repeated_sell_key_checks_the_balance_and_orders_once(make_client):
    fake = FakeKiwoom().reply("kt00018", balance(holding())).reply("kt10001", SELL_REPLY)
    client = make_client(fake)

    assert client.post(DOMESTIC_URL, json=sell_order()).status_code == 200
    assert client.post(DOMESTIC_URL, json=sell_order()).status_code == 409
    assert len(fake.calls("kt00018")) == 1
    assert len(fake.calls("kt10001")) == 1


def test_sell_events_are_logged_with_the_order_key(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = FakeKiwoom().reply(
        "kt00018", balance(holding(rmnd_qty="000000000000010", trde_able_qty="000000000000007"))
    )
    client = make_client(fake)
    client.post(DOMESTIC_URL, json=sell_order(order_key="over", quantity="8"))
    fake.reply("kt00018", kiwoom_error(20, "조회 실패"))
    client.post(DOMESTIC_URL, json=sell_order(order_key="fail"))

    events = {r.getMessage(): getattr(r, "fields", {}) for r in caplog.records}
    assert events["order_requested"]["side"] == "sell"
    assert events["sellable_checked"] == {
        "order_key": "over",
        "code": "005930",
        "quantity": 10,
        "sellable_quantity": 7,
    }
    assert events["order_blocked_sellable"]["sellable_quantity"] == 7
    assert events["sellable_check_failed"]["order_key"] == "fail"


@pytest.mark.anyio
async def test_concurrent_sells_of_one_stock_check_the_balance_one_at_a_time():
    """서로 다른 주문 키라도 같은 종목의 매도는 앞 주문의 응답 뒤에 잔고를 다시 확인한다."""
    fake = FakeKiwoom().reply("kt00018", balance(holding())).reply("kt10001", SELL_REPLY)
    gate = threading.Event()

    def slow_order(request: httpx.Request) -> None:
        if request.headers.get("api-id") == "kt10001":
            gate.wait(timeout=1)

    fake.on_request = slow_order
    app = create_app(
        environ=FAKE_ENV, transport=ThreadedTransport(fake), static_dir=None, log_dir=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        orders = [sell_order(order_key=f"race-{n}") for n in range(2)]
        pending = [asyncio.create_task(client.post(DOMESTIC_URL, json=o)) for o in orders]
        await asyncio.sleep(0.05)
        # 첫 주문이 키움 응답을 기다리는 동안 두 번째 잔고 조회는 나가지 않는다.
        assert len(fake.calls("kt00018")) == 1
        gate.set()
        await asyncio.gather(*pending)

    api_ids = [r.headers["api-id"] for r in fake.requests if "api-id" in r.headers]
    assert api_ids == ["kt00018", "kt10001", "kt00018", "kt10001"]
