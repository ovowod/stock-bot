"""Trailing Stop 감시(모의): 앱을 띄운 채 시세 체결과 내 계좌 체결을 넣고 상태 조회 API로 본다.

매도 주문은 내지 않는다. 모든 테스트가 끝날 때 주문 TR(kt10001, ust20001)이 하나도 없었는지 본다.
"""

import json
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from stock_bot.fill import feed as fill_feed
from stock_bot.kiwoom import realtime as realtime_module
from stock_bot.trailing_stop import watch as ts_watch
from tests.fake_kiwoom import FAKE_ENV, REAL_ENV, US_PAPER_ENV, FakeKiwoom, body_of
from tests.fake_realtime import REPLACED, FakeConnection, FakeRealtime, fill_event
from tests.log_capture import JsonLines
from tests.sl_tp.helpers import (
    AT_10,
    LISTING,
    NY_10,
    Clock,
    balance,
    messages,
    nth_call_gate,
    position,
    quote,
    running,
    settle,
    us_balance,
    us_position,
    us_quote,
)
from tests.support import wait_until

pytestmark = pytest.mark.anyio

ORDER_TRS = ("kt10001", "ust20001")
_made: list[FakeKiwoom] = []


@pytest.fixture(autouse=True)
def no_sell_orders():
    """Trailing Stop 트리거가 일어나도 매도 주문은 나가지 않는다."""
    _made.clear()
    yield
    for kiwoom in _made:
        assert [api for api in ORDER_TRS if kiwoom.calls(api)] == []


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    monkeypatch.setattr(ts_watch, "SESSION_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(ts_watch, "HOLDINGS_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(ts_watch, "HOLDINGS_RETRY_MAX_SECONDS", 0.04)
    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_MAX_SECONDS", 0.04)


def ts_session(
    start: str = "09:00",
    end: str = "15:30",
    enabled: bool = True,
    activation_profit: str | None = "5",
    drop_from_peak: str | None = "3",
) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "enabled": enabled,
        "activation_profit": activation_profit,
        "drop_from_peak": drop_from_peak,
    }


def write_ts(
    data_dir: Path,
    *sessions: dict[str, Any],
    excluded: tuple[str, ...] = (),
    environment: str = "domestic_paper",
    sl_tp: dict[str, Any] | None = None,
) -> None:
    """Trailing Stop 칸만 둔 자동매매 설정 파일. SL / TP는 sl_tp를 줄 때만 켠다."""
    exchange = "NASDAQ" if environment.startswith("us") else None
    path = data_dir / "auto-trading" / f"{environment}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content: dict[str, Any] = {
        "version": 2,
        "trailing_stop": {
            "updated_at": None,
            "excluded": [{"code": c, "name": "제외", "exchange": exchange} for c in excluded],
            "sessions": list(sessions or (ts_session(),)),
        },
    }
    if sl_tp is not None:
        content["sl_tp"] = sl_tp
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def ts_kiwoom(*balances: Any) -> FakeKiwoom:
    """잔고 응답만 둔다. 주문 TR 응답은 두지 않는다."""
    kiwoom = FakeKiwoom().reply("kt00018", *(balances or (balance(),)))
    _made.append(kiwoom)
    return kiwoom


def ts_us_kiwoom(*balances: Any) -> FakeKiwoom:
    kiwoom = (
        FakeKiwoom()
        .reply("ust21070", *(balances or (us_balance(),)))
        .reply("usa10099", {"list": LISTING})
    )
    _made.append(kiwoom)
    return kiwoom


def held(code: str = "005930", price: str = "60000", quantity: str = "3", **extra: str):
    """매입가 price원, quantity주인 현금잔고."""
    return position(
        code,
        pur_pric=f"{int(price):015d}",
        rmnd_qty=f"{int(quantity):015d}",
        trde_able_qty=f"{int(quantity):015d}",
        **extra,
    )


def sell_fill(code: str = "005930", quantity: str = "3") -> dict[str, Any]:
    return fill_event(
        **{"9001": code, "907": "1", "905": "-매도", "915": quantity, "911": quantity}
    )


def ts_regs(connection: FakeConnection) -> list[dict[str, Any]]:
    return [m for m in connection.requests("REG") if m["grp_no"] == "3"]


async def watching(realtime: FakeRealtime) -> FakeConnection:
    """Trailing Stop의 시세 구독(그룹 3)이 등록된 연결."""
    await wait_until(lambda: any(ts_regs(c) for c in realtime.connections))
    return next(c for c in realtime.connections if ts_regs(c))


async def status(app: FastAPI, environment: str = "domestic_paper") -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        response = await client.get(f"/api/environments/{environment}/trailing-stop/status")
    assert response.status_code == 200, response.text
    return response.json()


async def stock(app: FastAPI, code: str = "005930", environment: str = "domestic_paper"):
    body = await status(app, environment)
    return next((s for s in body["stocks"] if s["code"] == code), None)


async def until_status(app: FastAPI, check, environment: str = "domestic_paper") -> dict:
    found: dict[str, Any] = {}

    async def poll() -> bool:
        found["body"] = await status(app, environment)
        return check(found["body"])

    for _ in range(200):
        if await poll():
            return found["body"]
        await settle()
    raise AssertionError(f"상태를 기다렸지만 만족하지 않았습니다: {found.get('body')}")


def utc(moment) -> str:
    return moment.astimezone(UTC).isoformat()


async def test_paper_session_reads_holdings_and_subscribes_its_own_group(data_dir):
    write_ts(data_dir)
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        body = await until_status(app, lambda b: b["watching"])

    assert kiwoom.calls("kt00018")[0].url.host == "mockapi.kiwoom.com"
    assert body_of(kiwoom.calls("kt00018")[0]) == {"qry_tp": "1", "dmst_stex_tp": "KRX"}
    assert connection.url == "wss://mockapi.kiwoom.com:10000/api/websocket"
    assert ts_regs(connection) == [
        {
            "trnm": "REG",
            "grp_no": "3",
            "refresh": "0",
            "data": [{"item": ["005930"], "type": ["0B"]}],
        }
    ]
    assert body["not_watching_reason"] is None
    assert body["stocks"] == [
        {
            "code": "005930",
            "status": "waiting",
            "ineligible_reason": None,
            "purchase_price": "60000",
            "last_price": None,
            "last_price_at": None,
            "activated_at": None,
            "peak": None,
            "peak_at": None,
            "triggered_at": None,
            "triggered_price": None,
        }
    ]


@pytest.mark.parametrize(
    ("price", "activated"),
    [("+63000", True), ("+62994", False)],
    ids=["plus-5.00", "plus-4.99"],
)
async def test_activation_boundary(data_dir, price, activated):
    write_ts(data_dir, ts_session(activation_profit="5"))
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price=price))
        found = await until_status(
            app, lambda b: b["stocks"] and b["stocks"][0]["last_price"] is not None
        )

    [item] = found["stocks"]
    assert item["last_price"] == price.lstrip("+")
    assert item["last_price_at"] == utc(AT_10)
    if activated:
        assert item["status"] == "tracking"
        assert item["activated_at"] == utc(AT_10)
        assert item["peak"] == "63000"
        assert item["peak_at"] == utc(AT_10)
    else:
        assert item["status"] == "waiting"
        assert item["activated_at"] is None
        assert item["peak"] is None


@pytest.mark.parametrize(
    ("price", "triggered"),
    [("-62080", True), ("-62081", False)],
    ids=["minus-3.00", "minus-2.99"],
)
async def test_peak_follows_and_trigger_boundary(data_dir, price, triggered):
    write_ts(data_dir, ts_session(activation_profit="5", drop_from_peak="3"))
    clock = Clock()
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir, clock) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")
        clock.at = AT_10 + timedelta(minutes=1)
        connection.push(quote(price="+64000"))
        await until_status(app, lambda b: b["stocks"][0]["peak"] == "64000")
        clock.at = AT_10 + timedelta(minutes=2)
        connection.push(quote(price=price))
        found = await until_status(app, lambda b: b["stocks"][0]["last_price"] == price.lstrip("-"))

    [item] = found["stocks"]
    assert item["activated_at"] == utc(AT_10)
    assert item["peak"] == "64000"
    assert item["peak_at"] == utc(AT_10 + timedelta(minutes=1))
    if triggered:
        assert item["status"] == "triggered"
        assert item["triggered_at"] == utc(AT_10 + timedelta(minutes=2))
        assert item["triggered_price"] == "62080"
    else:
        assert item["status"] == "tracking"
        assert item["triggered_at"] is None


async def test_activation_stays_after_falling_below_the_activation_profit(data_dir):
    write_ts(data_dir, ts_session(activation_profit="5", drop_from_peak="10"))
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        connection.push(quote(price="+60500"))  # +0.8%, 고점 대비 -4.0%
        found = await until_status(app, lambda b: b["stocks"][0]["last_price"] == "60500")

    assert found["stocks"][0]["status"] == "tracking"
    assert found["stocks"][0]["peak"] == "63000"


async def test_triggered_stays_and_the_peak_is_frozen_when_the_price_rises_again(data_dir):
    write_ts(data_dir)
    clock = Clock()
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir, clock) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        connection.push(quote(price="-61000"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "triggered")
        clock.at = AT_10 + timedelta(minutes=1)
        connection.push(quote(price="+70000"))
        found = await until_status(app, lambda b: b["stocks"][0]["last_price"] == "70000")

    [item] = found["stocks"]
    assert item["status"] == "triggered"
    assert item["peak"] == "63000"
    assert item["triggered_price"] == "61000"
    assert item["triggered_at"] == utc(AT_10)


@pytest.mark.parametrize("market", ["1", "3", ""])
async def test_prices_outside_the_regular_market_are_not_judged(data_dir, caplog, market):
    write_ts(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+70000", market=market))
        await wait_until(lambda: "trailing_stop_judge_skipped" in messages(caplog))
        item = await stock(app)

    assert item["status"] == "waiting"
    assert item["last_price"] is None
    skipped = next(r for r in caplog.records if r.getMessage() == "trailing_stop_judge_skipped")
    assert skipped.fields["reason"] == "not_regular_market"


async def test_unreadable_prices_are_skipped_and_the_next_price_is_judged(data_dir, caplog):
    write_ts(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="abc"))
        connection.push(quote(price="0"))
        connection.push(quote(price="+63000"))
        found = await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")

    assert found["stocks"][0]["peak"] == "63000"
    unreadable = [r for r in caplog.records if r.getMessage() == "trailing_stop_price_unreadable"]
    assert [r.fields["reason"] for r in unreadable] == ["not_a_number", "not_positive"]


async def test_prices_after_the_session_end_are_not_judged(data_dir, caplog):
    write_ts(data_dir, ts_session(start="09:00", end="10:01"))
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir, clock) as app:
        connection = await watching(realtime)
        clock.at = AT_10 + timedelta(minutes=1)
        await wait_until(lambda: "trailing_stop_session_ended" in messages(caplog))
        connection.push(quote(price="+70000"))
        await settle()
        body = await status(app)

    assert body["watching"] is False
    assert body["not_watching_reason"] == "no_session"
    # 기억이 없어 보일 종목이 없다.
    assert body["stocks"] == []
    removed = [m for m in connection.requests("REMOVE") if m["grp_no"] == "3"]
    assert len(removed) == 1


async def test_memory_carries_into_the_next_session_with_its_own_drop(data_dir, caplog):
    write_ts(
        data_dir,
        ts_session(start="09:00", end="10:01", drop_from_peak="3"),
        ts_session(start="10:01", end="11:00", drop_from_peak="1"),
    )
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir, clock) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")
        clock.at = AT_10 + timedelta(minutes=1)
        # 새 세션은 보유종목을 다시 읽고 다시 구독한다.
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 2)
        await wait_until(lambda: len(ts_regs(connection)) == 2)
        await until_status(app, lambda b: b["watching"])
        connection.push(quote(price="-62370"))  # 63,000원 대비 -1%
        found = await until_status(app, lambda b: b["stocks"][0]["status"] == "triggered")

    [item] = found["stocks"]
    assert item["activated_at"] == utc(AT_10)
    assert item["triggered_price"] == "62370"


async def test_stock_no_longer_held_on_reread_is_forgotten(data_dir, caplog):
    write_ts(data_dir)
    realtime = FakeRealtime()
    # 앱 밖에서 전량 팔아 다시 읽으니 보유종목에 없다.
    kiwoom = ts_kiwoom(balance(held()), balance(held("000660")))
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")
        connection.push(fill_event(**{"9001": "000660"}))
        found = await until_status(
            app, lambda b: b["watching"] and [s["code"] for s in b["stocks"]] == ["000660"]
        )

    assert found["stocks"][0]["status"] == "waiting"
    forgotten = [r for r in caplog.records if r.getMessage() == "trailing_stop_memory_forgotten"]
    assert [(r.fields["code"], r.fields["reason"]) for r in forgotten] == [("005930", "not_held")]


async def test_selling_everything_then_buying_again_forgets_even_when_rereads_are_merged(
    data_dir, caplog
):
    write_ts(data_dir)
    realtime = FakeRealtime()
    # 전량 매도 직후 다시 사서, 합쳐진 다시 읽기에는 보유 중으로 나온다.
    kiwoom = ts_kiwoom(balance(held()), balance(held(price="64000", quantity="1")))
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")
        both = sell_fill(quantity="3")
        both["data"] = [*both["data"], *fill_event(**{"915": "1"})["data"]]
        connection.push(both)
        found = await until_status(app, lambda b: b["watching"])

    assert len(kiwoom.calls("kt00018")) == 2
    [item] = found["stocks"]
    assert item["status"] == "waiting"
    assert item["peak"] is None
    assert item["purchase_price"] == "64000"
    forgotten = [r for r in caplog.records if r.getMessage() == "trailing_stop_memory_forgotten"]
    assert [r.fields["reason"] for r in forgotten] == ["sold_out"]


async def test_partial_sell_and_buying_more_keep_memory_and_update_the_purchase_price(data_dir):
    write_ts(data_dir)
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom(
        balance(held()),
        balance(held(quantity="2")),
        # 추가 매수로 평균 매입가가 50,000원이 됐다.
        balance(held(price="50000", quantity="5")),
    )
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")
        connection.push(sell_fill(quantity="1"))
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 2)
        await until_status(app, lambda b: b["watching"])
        connection.push(fill_event())
        found = await until_status(
            app, lambda b: b["watching"] and b["stocks"][0]["purchase_price"] == "50000"
        )

    [item] = found["stocks"]
    assert item["status"] == "tracking"
    assert item["peak"] == "63000"
    assert item["activated_at"] == utc(AT_10)


async def test_quote_in_the_same_message_as_a_fill_is_not_judged(data_dir):
    write_ts(data_dir)
    realtime = FakeRealtime()
    # 추가 매수로 평균 매입가가 61,000원이 됐다. 63,000원은 그 기준으로 +3.3%다.
    kiwoom = ts_kiwoom(balance(held()), balance(held(price="61000", quantity="5")))
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir) as app:
        connection = await watching(realtime)
        mixed = quote(price="+63000")
        mixed["data"] = [*mixed["data"], *fill_event()["data"]]
        connection.push(mixed)
        await gate.hit.wait()
        pending = await status(app)
        # 다시 읽는 동안 온 시세 체결도 판정하지 않는다.
        connection.push(quote(price="+64000"))
        await settle()
        assert (await status(app))["not_watching_reason"] == "holdings_pending"
        gate.open.set()
        found = await until_status(app, lambda b: b["watching"])

    assert pending["watching"] is False
    assert pending["not_watching_reason"] == "holdings_pending"
    assert found["stocks"][0]["status"] == "waiting"
    assert found["stocks"][0]["purchase_price"] == "61000"


async def test_fill_during_a_reread_reads_again_and_judges_with_the_last_result(data_dir, caplog):
    write_ts(data_dir)
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom(balance(held()), balance(held(price="70000")), balance(held(price="50000")))
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir) as app:
        connection = await watching(realtime)
        connection.push(fill_event())
        await gate.hit.wait()
        connection.push(fill_event())
        gate.open.set()
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 3)
        await until_status(app, lambda b: b["watching"])
        connection.push(quote(price="+52500"))  # 50,000원 기준 +5%
        found = await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")

    assert found["stocks"][0]["purchase_price"] == "50000"
    assert "trailing_stop_holdings_discarded" in messages(caplog)


async def test_replaced_connection_stops_judging_and_keeps_the_memory(data_dir, caplog):
    write_ts(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")
        connection.push(REPLACED)
        found = await until_status(app, lambda b: not b["watching"])

    assert found["not_watching_reason"] == "connection_replaced"
    [item] = found["stocks"]
    assert item["status"] == "tracking"
    assert item["peak"] == "63000"
    # 감시 중이 아니면 판정에 쓰는 매입가와 가격을 돌려주지 않는다. 화면은 계좌 값을 쓴다.
    assert item["purchase_price"] is None
    assert item["last_price"] is None
    replaced = next(r for r in caplog.records if r.getMessage() == "realtime_session_replaced")
    assert json.loads(replaced.fields["names"]) == ["Trailing Stop"]


REJECTED_REG = {"trnm": "REG", "return_code": 1, "return_msg": "등록 실패"}


def rejecting(group: str, flag: dict[str, bool]) -> FakeRealtime:
    """flag["on"]인 동안 그 그룹의 REG를 거부한다."""

    def respond(message: dict[str, Any]) -> dict[str, Any]:
        if message["trnm"] == "REG" and message["grp_no"] == group and flag["on"]:
            return REJECTED_REG
        return {"trnm": message["trnm"], "return_code": 0, "return_msg": ""}

    return FakeRealtime(responder=respond)


async def test_rejected_fill_registration_stops_judging_until_it_is_registered(data_dir, caplog):
    write_ts(data_dir)
    flag = {"on": True}
    realtime = rejecting("1", flag)
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        found = await until_status(app, lambda b: b["not_watching_reason"] == "fills_rejected")
        connection.push(quote(price="+63000"))
        await settle()
        assert (await stock(app)) is None
        flag["on"] = False
        # 체결 feed가 다시 등록하면 거부된 동안의 체결을 반영하려고 다시 읽은 뒤 판정한다.
        await until_status(app, lambda b: b["watching"])
        connection.push(quote(price="+63000"))
        resumed = await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")

    assert found["watching"] is False
    assert len(kiwoom.calls("kt00018")) >= 2
    assert resumed["not_watching_reason"] is None


async def test_rejected_quote_registration_stops_judging_until_registered_again(data_dir, caplog):
    write_ts(data_dir)
    flag = {"on": True}
    realtime = rejecting("3", flag)
    async with running(realtime, ts_kiwoom(), data_dir) as app:
        first = await watching(realtime)
        await until_status(app, lambda b: b["not_watching_reason"] == "quotes_rejected")
        first.push(quote(price="+63000"))
        await settle()
        flag["on"] = False
        # 다시 연결되면 공유 연결이 모든 구독을 다시 등록한다.
        first.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        second = realtime.connections[1]
        await wait_until(lambda: bool(ts_regs(second)))
        await until_status(app, lambda b: b["watching"])
        assert (await stock(app))["status"] == "waiting"
        second.push(quote(price="+63000"))
        found = await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")

    assert found["not_watching_reason"] is None
    rejected = next(
        r for r in caplog.records if r.getMessage() == "trailing_stop_subscription_rejected"
    )
    assert rejected.fields["subscription"] == "quotes"


async def test_ineligible_stocks_are_listed_with_their_reason(data_dir, caplog):
    write_ts(data_dir)
    realtime = FakeRealtime()
    rows = balance(
        held(),
        # 같은 종목의 신용 보유분. 현금 보유분은 감시한다.
        held(crd_tp="03"),
        # 신용 보유분만 있는 종목은 stocks에 넣지 않는다.
        held("051910", crd_tp="03"),
        held("000660", price="0"),
        held("035420"),
        held("035420", price="61000"),
    )
    async with running(realtime, ts_kiwoom(rows), data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        found = await until_status(
            app, lambda b: any(s["status"] == "tracking" for s in b["stocks"])
        )

    assert ts_regs(connection)[0]["data"] == [{"item": ["005930"], "type": ["0B"]}]
    assert [
        (s["code"], s["status"], s["ineligible_reason"], s["purchase_price"])
        for s in found["stocks"]
    ] == [
        ("000660", "ineligible", "no_purchase_price", None),
        ("005930", "tracking", None, "60000"),
        ("035420", "ineligible", "duplicate", None),
    ]
    refreshed = next(
        r for r in caplog.records if r.getMessage() == "trailing_stop_holdings_refreshed"
    )
    assert refreshed.fields["count"] == 1
    assert refreshed.fields["credit"] == 2
    assert refreshed.fields["duplicate"] == 1


async def test_excluded_stock_is_listed_but_not_subscribed(data_dir):
    write_ts(data_dir, excluded=("000660",))
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(balance(held(), held("000660"))), data_dir) as app:
        connection = await watching(realtime)
        found = await until_status(app, lambda b: b["watching"])

    assert ts_regs(connection)[0]["data"] == [{"item": ["005930"], "type": ["0B"]}]
    assert found["excluded"] == ["000660"]
    assert [s["code"] for s in found["stocks"]] == ["000660", "005930"]


async def test_real_environments_are_never_watched(data_dir):
    # 실전 파일에는 켜진 세션을 둘 수 없다. 모의 인증정보 없이 실전만 있으면 아무것도 읽지 않는다.
    write_ts(data_dir, ts_session(enabled=False), environment="domestic_real")
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir, environ=REAL_ENV) as app:
        await settle()
        body = await status(app, "domestic_real")

    assert kiwoom.requests == []
    assert realtime.connections == []
    assert body["not_watching_reason"] == "real"
    assert body["stocks"] == []


async def test_no_enabled_session_means_no_lookup_and_no_subscription(data_dir):
    write_ts(data_dir, ts_session(enabled=False))
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        await settle()
        body = await status(app)

    assert kiwoom.requests == []
    assert realtime.connections == []
    assert body["not_watching_reason"] == "no_session"


async def test_saved_settings_apply_right_away(data_dir):
    write_ts(data_dir, ts_session(enabled=False))
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir) as app:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
            response = await client.put(
                "/api/environments/domestic_paper/trailing-stop",
                json={"excluded": [], "sessions": [ts_session(activation_profit="10")]},
            )
        assert response.status_code == 200
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))  # 기준 10%면 미도달
        connection.push(quote(price="+66000"))
        found = await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")

    assert found["stocks"][0]["peak"] == "66000"


async def test_shares_the_connection_with_sl_tp_in_its_own_group(data_dir):
    # SL / TP는 +50%에만 팔도록 켠다. Trailing Stop 판정은 SL / TP 주문을 부르지 않는다.
    sl_tp = {
        "updated_at": None,
        "excluded": [],
        "sessions": [
            {
                "start": "09:00",
                "end": "15:30",
                "enabled": True,
                "take_profit": "50",
                "stop_loss": None,
            }
        ],
    }
    write_ts(data_dir, sl_tp=sl_tp)
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        await wait_until(
            lambda: {m["grp_no"] for m in connection.requests("REG")} == {"1", "2", "3"}
        )
        connection.push(quote(price="+63000"))
        connection.push(quote(price="-61000"))
        found = await until_status(app, lambda b: b["stocks"][0]["status"] == "triggered")

    assert len(realtime.connections) == 1
    # 내 계좌 체결은 SL / TP와 Trailing Stop이 함께 받아도 한 번만 등록한다.
    assert len([m for m in connection.requests("REG") if m["grp_no"] == "1"]) == 1
    assert found["stocks"][0]["triggered_price"] == "61000"


async def test_activation_and_trigger_logs_carry_a_request_id_and_the_criteria(
    data_dir, json_logs: JsonLines
):
    write_ts(data_dir, ts_session(start="09:00", end="15:30"))
    realtime = FakeRealtime()
    async with running(realtime, ts_kiwoom(), data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        connection.push(quote(price="+64000"))
        connection.push(quote(price="-62080"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "triggered")

    events = {line["event"]: line for line in json_logs.lines}
    activated = events["trailing_stop_activated"]
    triggered = events["trailing_stop_triggered"]
    assert activated["env"] == triggered["env"] == "domestic_paper"
    assert activated["request_id"] not in ("-", triggered["request_id"])
    assert triggered["request_id"] != "-"
    assert {k: activated[k] for k in ("code", "price", "purchase_price", "peak", "session")} == {
        "code": "005930",
        "price": "63000",
        "purchase_price": "60000",
        "peak": "63000",
        "session": "09:00-15:30",
    }
    assert activated["activation_profit"] == "5"
    assert {k: triggered[k] for k in ("code", "price", "purchase_price", "peak")} == {
        "code": "005930",
        "price": "62080",
        "purchase_price": "60000",
        "peak": "64000",
    }
    assert triggered["drop_from_peak"] == "3"
    # 고점 갱신은 시세 체결마다 남기지 않는다.
    assert not any("peak" in line["event"] for line in json_logs.lines)


async def test_us_paper_subscribes_fe_and_judges_in_dollars(data_dir):
    write_ts(data_dir, ts_session(start="09:30", end="16:00"), environment="us_paper")
    realtime = FakeRealtime()
    rows = us_balance(us_position(), us_position("ZZZZ"))
    kiwoom = ts_us_kiwoom(rows)
    async with running(realtime, kiwoom, data_dir, Clock(NY_10), environ=US_PAPER_ENV) as app:
        connection = await watching(realtime)
        connection.push(us_quote(price="+210.0000"))  # $200 기준 +5%
        found = await until_status(
            app, lambda b: any(s["status"] == "tracking" for s in b["stocks"]), "us_paper"
        )

    assert ts_regs(connection) == [
        {
            "trnm": "REG",
            "grp_no": "3",
            "refresh": "0",
            "data": [{"item": [{"jmcode": "AAPL", "stex_tp": "ND"}], "type": ["FE"]}],
        }
    ]
    assert found["timezone"] == "America/New_York"
    assert [
        (s["code"], s["status"], s["ineligible_reason"], s["purchase_price"], s["peak"])
        for s in found["stocks"]
    ] == [
        ("AAPL", "tracking", None, "200", "210"),
        ("ZZZZ", "ineligible", "unknown_exchange", None, None),
    ]
    assert found["stocks"][0]["activated_at"] == utc(NY_10)
