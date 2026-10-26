"""SL / TP 자동 매도(미국 모의): FE 체결가, ust20001 시장가, 뉴욕시간, 종목 목록의 거래소."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from stock_bot import realtime as realtime_module
from stock_bot import sl_tp_watch
from tests.fake_kiwoom import (
    FAKE_ENV,
    UST21070_HOLDING,
    UST21070_REPLY,
    FakeKiwoom,
    body_of,
    kiwoom_error,
    usa10099_row,
)
from tests.fake_realtime import FakeConnection, FakeRealtime, us_fill_event
from tests.test_fill_notifications import (
    PAPER_ENV,
    REPLACED,
    US_PAPER_ENV,
    wait_until,
)
from tests.test_sl_tp_auto_sell import (
    KST,
    Clock,
    messages,
    quote,
    running,
    session,
    settle,
    sl_tp_kiwoom,
    write_settings,
)
from tests.test_sl_tp_sessions import nth_call_gate

pytestmark = pytest.mark.anyio

# 2026-10-26 14:00 UTC는 뉴욕 10:00(서머타임), 한국 23:00이다.
NY_10 = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)
US_SELL_REPLY = {"ord_no": "000000300"}
LISTING = [
    usa10099_row("AAPL", "애플", "APPLE INC", "ND"),
    usa10099_row("SPY", "SPDR S&P 500", "SPDR S&P 500 ETF", "NY"),
    usa10099_row("MSFT", "마이크로소프트", "MICROSOFT CORP", "ND"),
]


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    monkeypatch.setattr(sl_tp_watch, "SESSION_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(sl_tp_watch, "HOLDINGS_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)


def us_session(start: str = "09:30", end: str = "16:00", **overrides: Any) -> dict[str, Any]:
    return session(start=start, end=end, **overrides)


def write_us_settings(
    data_dir: Path, *sessions: dict[str, Any], excluded: tuple[str, ...] = ()
) -> None:
    path = data_dir / "sl-tp" / "us_paper.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "version": 1,
        "updated_at": None,
        "excluded": [{"code": c, "name": "제외", "exchange": "NASDAQ"} for c in excluded],
        "sessions": list(sessions or (us_session(),)),
    }
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def us_position(code: str = "AAPL", **overrides: str) -> dict[str, str]:
    """ust21070 보유종목 한 줄. 기본은 매입단가 $200, 매도 가능 3주다."""
    return {
        **UST21070_HOLDING,
        "stk_cd": code,
        "frgn_stk_book_uv": "200.0000",
        "poss_qty": "000000000003",
        "sell_alowq": "000000000003",
        **overrides,
    }


def us_balance(*rows: dict[str, str]) -> dict[str, Any]:
    return {**UST21070_REPLY, "result_list": list(rows or (us_position(),))}


def us_kiwoom(*balances: dict[str, Any] | httpx.Response) -> FakeKiwoom:
    return (
        FakeKiwoom()
        .reply("ust21070", *(balances or (us_balance(),)))
        .reply("usa10099", {"list": LISTING})
        .reply("ust20001", US_SELL_REPLY)
    )


def us_quote(code: str = "AAPL", price: str = "-194.0000", market: str = "0") -> dict[str, Any]:
    """FE 미국주식 실시간 체결가. 체결가(10)의 부호는 등락 방향이다."""
    return {
        "trnm": "REAL",
        "data": [
            {
                "type": "FE",
                "name": "미국주식 실시간 체결가",
                "stexTp": "ND",
                "item": code,
                "values": {"10": price, "290": market, "20": "100000", "22": "20261026"},
            }
        ],
    }


def us_quote_regs(connection: FakeConnection) -> list[dict[str, Any]]:
    return [m for m in connection.requests("REG") if m["data"][0]["type"] == ["FE"]]


async def watching_us(realtime: FakeRealtime) -> FakeConnection:
    await wait_until(lambda: any(us_quote_regs(c) for c in realtime.connections))
    return next(c for c in realtime.connections if us_quote_regs(c))


def us_running(realtime, kiwoom, data_dir, clock=None, environ=US_PAPER_ENV, discord=None):
    return running(realtime, kiwoom, data_dir, clock or Clock(NY_10), environ, discord)


async def save_us(app: FastAPI, *sessions: dict[str, Any], excluded: tuple[str, ...] = ()) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        response = await client.put(
            "/api/environments/us_paper/sl-tp",
            json={
                "excluded": [{"code": c, "name": "제외", "exchange": "NASDAQ"} for c in excluded],
                "sessions": list(sessions),
            },
        )
    assert response.status_code == 200, response.text


def finished(caplog) -> list[dict[str, Any]]:
    return [r.fields for r in caplog.records if r.getMessage() == "sl_tp_auto_sell_finished"]


async def test_subscribes_fe_with_listing_exchanges_and_skips_unknown_ones(data_dir, caplog):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom(us_balance(us_position("AAPL"), us_position("SPY"), us_position("ZZZZ")))
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)

    assert connection.url == "wss://mockapi.kiwoom.com:10000/api/websocket"
    assert body_of(kiwoom.calls("ust21070")[0]) == {"stex_tp": "", "stk_cd": ""}
    assert us_quote_regs(connection) == [
        {
            "trnm": "REG",
            "grp_no": "2",
            "refresh": "0",
            "data": [
                {
                    "item": [
                        {"jmcode": "AAPL", "stex_tp": "ND"},
                        # 잔고는 NYSE Arca ETF를 아멕스로 주지만 종목 목록의 거래소(NYSE)를 쓴다.
                        {"jmcode": "SPY", "stex_tp": "NY"},
                    ],
                    "type": ["FE"],
                }
            ],
        }
    ]
    refreshed = next(r for r in caplog.records if r.getMessage() == "sl_tp_holdings_refreshed")
    assert refreshed.fields["unknown_exchange"] == 1
    assert refreshed.fields["unknown_exchange_codes"] == '["ZZZZ"]'


@pytest.mark.parametrize(
    ("price", "sold"),
    [("-194.0000", True), ("-194.0200", False)],
    ids=["minus-3.00", "minus-2.99"],
)
async def test_stop_loss_boundary_sells_with_ust20001_market(data_dir, price, sold):
    write_us_settings(data_dir, us_session(stop_loss="3"))
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_quote(price=price))
        if sold:
            await wait_until(lambda: bool(kiwoom.calls("ust20001")))
        await settle()

    orders = kiwoom.calls("ust20001")
    assert len(orders) == (1 if sold else 0)
    if sold:
        assert orders[0].url.host == "mockapi.kiwoom.com"
        assert orders[0].url.path == "/api/us/ordr"
        assert body_of(orders[0]) == {
            "stex_tp": "ND",
            "stk_cd": "AAPL",
            "ord_qty": "3",
            "ord_uv": "",
            "trde_tp": "03",
            "stop_pric": "",
        }


@pytest.mark.parametrize(
    ("price", "sold"),
    [("+210.0000", True), ("+209.9800", False)],
    ids=["plus-5.00", "plus-4.99"],
)
async def test_take_profit_boundary(data_dir, price, sold):
    write_us_settings(data_dir, us_session(take_profit="5", stop_loss=None))
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_quote(price=price))
        if sold:
            await wait_until(lambda: bool(kiwoom.calls("ust20001")))
        await settle()

    assert len(kiwoom.calls("ust20001")) == (1 if sold else 0)


async def test_sells_the_rechecked_sellable_quantity_on_the_listing_exchange(data_dir):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom(
        us_balance(us_position("SPY")), us_balance(us_position("SPY", sell_alowq="000000000002"))
    )
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_quote("SPY"))
        await wait_until(lambda: bool(kiwoom.calls("ust20001")))

    assert body_of(kiwoom.calls("ust20001")[0])["stex_tp"] == "NY"
    assert body_of(kiwoom.calls("ust20001")[0])["ord_qty"] == "2"


@pytest.mark.parametrize(
    ("clock", "started"),
    [
        (NY_10, True),  # 뉴욕 10:00 = 한국 23:00
        (NY_10 - timedelta(minutes=1), False),  # 뉴욕 09:59
        (NY_10 + timedelta(hours=1), False),  # 뉴욕 11:00 (종료 시각은 빼므로 세션 밖)
        # 서머타임이 끝난 뒤(11월 1일)에는 15:00 UTC가 뉴욕 10:00이다.
        (datetime(2026, 11, 2, 15, 0, tzinfo=UTC), True),
        (datetime(2026, 11, 2, 14, 0, tzinfo=UTC), False),
    ],
    ids=["ny-10-00", "ny-09-59", "ny-11-00", "est-10-00", "est-09-00"],
)
async def test_session_times_are_new_york_time(data_dir, clock, started):
    write_us_settings(data_dir, us_session(start="10:00", end="11:00"))
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir, Clock(clock)):
        if started:
            await watching_us(realtime)
        await settle()

    assert bool(kiwoom.calls("ust21070")) is started


@pytest.mark.parametrize("market", ["1", "2", "4"], ids=["pre", "after", "day"])
async def test_extended_hours_prices_are_not_judged(data_dir, market):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_quote(market=market))
        await settle()

    assert kiwoom.calls("ust20001") == []


async def test_repeated_prices_sell_only_once(data_dir):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        twice = us_quote()
        twice["data"] = twice["data"] * 2
        connection.push(twice)
        connection.push(us_quote(price="-150.0000"))
        await wait_until(lambda: bool(kiwoom.calls("ust20001")))
        await settle()

    assert len(kiwoom.calls("ust20001")) == 1


async def test_rejected_sell_is_not_tried_again_in_the_session(data_dir, caplog):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom().reply("ust20001", kiwoom_error(1, "주문 거부"))
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_quote())
        await wait_until(lambda: bool(finished(caplog)))
        connection.push(us_quote())
        await settle()

    assert len(kiwoom.calls("ust20001")) == 1
    assert finished(caplog)[0]["outcome"] == "rejected"


async def test_excluded_stock_is_not_watched(data_dir):
    write_us_settings(data_dir, excluded=("AAPL",))
    realtime = FakeRealtime()
    kiwoom = us_kiwoom(us_balance(us_position("AAPL"), us_position("MSFT")))
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_quote("AAPL"))
        await settle()

    assert us_quote_regs(connection)[0]["data"][0]["item"] == [{"jmcode": "MSFT", "stex_tp": "ND"}]
    assert kiwoom.calls("ust20001") == []


async def test_unreadable_prices_are_skipped_and_the_next_price_is_judged(data_dir):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        for price in ("", "abc", "0", "0.0000"):
            connection.push(us_quote(price=price))
        await settle()
        assert kiwoom.calls("ust20001") == []
        connection.push(us_quote())
        await wait_until(lambda: bool(kiwoom.calls("ust20001")))


async def test_replaced_connection_during_the_recheck_cancels_the_order(data_dir, caplog):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    gate = nth_call_gate(kiwoom, "ust21070", 2)
    async with us_running(realtime, gate, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_quote())
        await gate.hit.wait()
        connection.push(REPLACED)
        await wait_until(lambda: "realtime_session_replaced" in messages(caplog))
        gate.open.set()
        await wait_until(lambda: bool(finished(caplog)))

    assert kiwoom.calls("ust20001") == []
    assert finished(caplog)[0]["outcome"] == "cancelled"


async def test_session_end_removes_and_next_session_judges_again(data_dir, caplog):
    write_us_settings(
        data_dir,
        us_session(start="09:30", end="10:01", stop_loss="3"),
        us_session(start="10:01", end="11:00", stop_loss="3"),
    )
    clock = Clock(NY_10)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir, clock):
        connection = await watching_us(realtime)
        connection.push(us_quote())
        await wait_until(lambda: len(kiwoom.calls("ust20001")) == 1)
        clock.at = NY_10 + timedelta(minutes=1)
        await wait_until(lambda: len(us_quote_regs(connection)) == 2)
        connection.push(us_quote())
        await wait_until(lambda: len(kiwoom.calls("ust20001")) == 2)
        clock.at = NY_10 + timedelta(hours=1)
        await wait_until(lambda: bool(connection.requests("REMOVE")))


async def test_saved_settings_apply_right_away(data_dir):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom(us_balance(us_position("AAPL"), us_position("MSFT")))
    async with us_running(realtime, kiwoom, data_dir) as app:
        connection = await watching_us(realtime)
        connection.push(us_quote("MSFT"))
        await wait_until(lambda: len(kiwoom.calls("ust20001")) == 1)
        # 기준만 바꾸면 이미 트리거된 종목은 다시 주문하지 않는다.
        await save_us(app, us_session(stop_loss="1"))
        connection.push(us_quote("MSFT"))
        # 제외종목을 더하면 그 종목은 구독에서 빠진다.
        await save_us(app, us_session(stop_loss="1"), excluded=("AAPL",))
        await wait_until(lambda: len(us_quote_regs(connection)) == 2)
        connection.push(us_quote("AAPL"))
        await settle()
        assert len(kiwoom.calls("ust20001")) == 1
        # 세션을 끄면 해지한다.
        await save_us(app, us_session(enabled=False))
        await wait_until(lambda: bool(connection.requests("REMOVE")))

    assert us_quote_regs(connection)[1]["data"][0]["item"] == [{"jmcode": "MSFT", "stex_tp": "ND"}]


async def test_f5_fill_rereads_holdings_and_adds_new_stocks(data_dir):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom(
        us_balance(us_position("AAPL")), us_balance(us_position("AAPL"), us_position("MSFT"))
    )
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        # 미국은 F5 체결로 다시 읽는다. 함께 받는 주문체결 항목도 F5다.
        assert any(m["data"][0]["type"] == ["F5"] for m in connection.requests("REG"))
        connection.push(us_fill_event())
        await wait_until(lambda: len(us_quote_regs(connection)) == 2)
        connection.push(us_quote("MSFT"))
        await wait_until(lambda: len(kiwoom.calls("ust20001")) == 1)

    assert body_of(kiwoom.calls("ust20001")[0])["stk_cd"] == "MSFT"


async def test_reconnect_rereads_and_keeps_the_trigger_record(data_dir, caplog):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir):
        first = await watching_us(realtime)
        first.push(us_quote())
        await wait_until(lambda: len(kiwoom.calls("ust20001")) == 1)
        first.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        second = realtime.connections[1]
        await wait_until(lambda: len(kiwoom.calls("ust21070")) == 3)
        await wait_until(lambda: messages(caplog).count("sl_tp_judging_resumed") == 2)
        second.push(us_quote())
        await settle()

    assert len(kiwoom.calls("ust20001")) == 1


async def test_listing_failure_pauses_judging_until_the_listing_is_back(data_dir, caplog):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom().reply("usa10099", kiwoom_error(1, "목록 실패"), {"list": LISTING})
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_quote())
        await wait_until(lambda: bool(kiwoom.calls("ust20001")))

    # 종목 목록을 받지 못하면 거래소 없이 모든 종목을 빼지 않고, 조회 실패로 다시 시도했다.
    assert "sl_tp_holdings_failed" in messages(caplog)


async def test_domestic_and_us_paper_run_apart(data_dir, caplog):
    write_settings(data_dir)
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    # 같은 시각이 한국 23:00(정규장 밖), 뉴욕 10:00이다. 국내는 한국 장중인 시각으로 따로 본다.
    domestic = sl_tp_kiwoom()
    us = us_kiwoom()

    def both(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token" or request.headers.get("api-id", "").startswith(
            ("ust", "usa")
        ):
            return us(request)
        return domestic(request)

    clock = Clock(datetime(2026, 10, 26, 14, 0, tzinfo=UTC))
    async with us_running(realtime, both, data_dir, clock, environ=PAPER_ENV):
        us_connection = await watching_us(realtime)
        await settle()
        # 국내는 한국 23:00이라 세션 밖이다. 국내 연결은 열리지 않는다.
        assert len(realtime.connections) == 1
        us_connection.push(us_quote())
        await wait_until(lambda: len(us.calls("ust20001")) == 1)
        us_connection.push(REPLACED)
        await wait_until(lambda: "realtime_session_replaced" in messages(caplog))
        # 미국 연결이 밀려나도 국내 SL / TP는 그대로 동작한다.
        clock.at = datetime(2026, 10, 26, 10, 0, tzinfo=KST)
        await wait_until(lambda: len(realtime.connections) == 2)
        domestic_connection = realtime.connections[1]
        await wait_until(
            lambda: any(m["data"][0]["type"] == ["0B"] for m in domestic_connection.requests("REG"))
        )
        domestic_connection.push(quote())
        await wait_until(lambda: len(domestic.calls("kt10001")) == 1)

    assert us_connection is not domestic_connection
    assert len(us.calls("ust20001")) == 1


async def test_prices_received_after_the_session_end_are_not_judged(data_dir):
    write_us_settings(data_dir, us_session(start="10:00", end="10:30"))
    clock = Clock(NY_10)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir, clock):
        connection = await watching_us(realtime)
        clock.at = NY_10 + timedelta(minutes=30)
        connection.push(us_quote())
        await settle()

    assert kiwoom.calls("ust20001") == []


@pytest.mark.parametrize("change", ["exclude", "switch_off"])
async def test_changes_during_the_recheck_cancel_the_order(data_dir, caplog, change):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    gate = nth_call_gate(kiwoom, "ust21070", 2)
    async with us_running(realtime, gate, data_dir) as app:
        connection = await watching_us(realtime)
        connection.push(us_quote())
        await gate.hit.wait()
        if change == "exclude":
            await save_us(app, us_session(), excluded=("AAPL",))
        else:
            await save_us(app, us_session(enabled=False))
        gate.open.set()
        await wait_until(lambda: bool(finished(caplog)))

    assert finished(caplog)[0]["outcome"] == "cancelled"
    assert kiwoom.calls("ust20001") == []


@pytest.mark.parametrize("status", ["접수", "취소완료"])
async def test_f5_events_other_than_fills_do_not_reread(data_dir, status):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom()
    async with us_running(realtime, kiwoom, data_dir):
        connection = await watching_us(realtime)
        connection.push(us_fill_event(**{"913": status}))
        await settle()

    assert len(kiwoom.calls("ust21070")) == 1
