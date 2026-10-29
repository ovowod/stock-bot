"""SL / TP 자동 매도(국내 모의): 앱을 띄운 채 체결가를 넣고 키움에 나가는 요청을 본다."""

import asyncio
from datetime import timedelta

import httpx
import pytest

from stock_bot.core.config import ENVIRONMENTS, Environment
from stock_bot.kiwoom import kiwoom as kiwoom_module
from stock_bot.kiwoom.kiwoom import KiwoomClient
from stock_bot.order.order import OrderService
from tests.fake_discord import FakeDiscord
from tests.fake_kiwoom import (
    DOMESTIC_PAPER_ENV,
    FAKE_ENV,
    REAL_ENV,
    FakeKiwoom,
    body_of,
    kiwoom_error,
)
from tests.fake_realtime import REPLACED, FakeRealtime
from tests.sl_tp.helpers import (
    AT_10,
    Clock,
    Gate,
    api,
    balance,
    messages,
    position,
    quote,
    quote_regs,
    running,
    session,
    settle,
    sl_tp_kiwoom,
    watching,
    write_settings,
)
from tests.support import wait_until

pytestmark = pytest.mark.anyio


async def test_subscribes_watched_holdings_when_started_inside_a_session(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)

    assert kiwoom.calls("kt00018")[0].url.host == "mockapi.kiwoom.com"
    assert body_of(kiwoom.calls("kt00018")[0]) == {"qry_tp": "1", "dmst_stex_tp": "KRX"}
    assert connection.url == "wss://mockapi.kiwoom.com:10000/api/websocket"
    assert quote_regs(connection) == [
        {
            "trnm": "REG",
            "grp_no": "2",
            "refresh": "0",
            "data": [{"item": ["005930"], "type": ["0B"]}],
        }
    ]


@pytest.mark.parametrize(
    ("price", "sold"),
    [("-58200", True), ("-58206", False)],
    ids=["minus-3.00", "minus-2.99"],
)
async def test_stop_loss_boundary(data_dir, price, sold):
    write_settings(data_dir, session(stop_loss="3"))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote(price=price))
        if sold:
            await wait_until(lambda: bool(kiwoom.calls("kt10001")))
        await settle()

    orders = kiwoom.calls("kt10001")
    assert len(orders) == (1 if sold else 0)
    if sold:
        assert orders[0].url.host == "mockapi.kiwoom.com"
        assert body_of(orders[0]) == {
            "dmst_stex_tp": "KRX",
            "stk_cd": "005930",
            "ord_qty": "3",
            "ord_uv": "",
            "trde_tp": "3",
            "cond_uv": "",
        }


@pytest.mark.parametrize(
    ("price", "sold"),
    [("+63000", True), ("+62994", False)],
    ids=["plus-5.00", "plus-4.99"],
)
async def test_take_profit_boundary(data_dir, price, sold):
    write_settings(data_dir, session(take_profit="5", stop_loss=None))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote(price=price))
        if sold:
            await wait_until(lambda: bool(kiwoom.calls("kt10001")))
        await settle()

    assert len(kiwoom.calls("kt10001")) == (1 if sold else 0)


async def test_session_with_only_take_profit_ignores_losses(data_dir):
    write_settings(data_dir, session(take_profit="5", stop_loss=None))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote(price="-30000"))
        await settle()

    assert kiwoom.calls("kt10001") == []


async def test_repeated_prices_sell_only_once(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        twice = quote()
        twice["data"] = twice["data"] * 2
        connection.push(twice)
        connection.push(quote(price="-50000"))
        connection.push(quote())
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1


async def test_trigger_while_the_order_reply_is_slow_does_not_sell_twice(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    gate = Gate(kiwoom, lambda r: api(r) == "kt10001")
    async with running(realtime, gate, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        connection.push(quote(price="-50000"))
        await settle()
        gate.open.set()
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1


async def test_slow_order_does_not_hold_up_other_stocks(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(position("005930"), position("000660")))
    gate = Gate(kiwoom, lambda r: api(r) == "kt10001" and body_of(r)["stk_cd"] == "005930")
    async with running(realtime, gate, data_dir):
        connection = await watching(realtime)
        connection.push(quote("005930"))
        await gate.hit.wait()
        connection.push(quote("000660"))
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)
        assert body_of(kiwoom.calls("kt10001")[0])["stk_cd"] == "000660"
        gate.open.set()
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 2)


@pytest.mark.parametrize(
    "reply",
    [kiwoom_error(1, "주문 거부"), httpx.ConnectError("down")],
    ids=["rejected", "unknown"],
)
async def test_rejected_or_unknown_sell_is_not_tried_again_in_the_session(data_dir, caplog, reply):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()

    def respond(request: httpx.Request) -> httpx.Response:
        if isinstance(reply, Exception):
            raise reply
        return reply

    kiwoom.respond("kt10001", respond)
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: "sl_tp_auto_sell_finished" in messages(caplog))
        connection.push(quote())
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1
    finished = next(r for r in caplog.records if r.getMessage() == "sl_tp_auto_sell_finished")
    assert finished.fields["outcome"] == (
        "rejected" if isinstance(reply, httpx.Response) else "unknown"
    )


@pytest.mark.parametrize(("sellable", "sent"), [("000000000000005", "5"), ("000000000000001", "1")])
async def test_sells_the_rechecked_sellable_quantity(data_dir, sellable, sent):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(), balance(position(trde_able_qty=sellable)))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))

    assert body_of(kiwoom.calls("kt10001")[0])["ord_qty"] == sent


async def test_nothing_sellable_on_recheck_skips_the_order(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(), balance(position(trde_able_qty="000000000000000")))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: "sl_tp_auto_sell_finished" in messages(caplog))

    assert kiwoom.calls("kt10001") == []
    finished = next(r for r in caplog.records if r.getMessage() == "sl_tp_auto_sell_finished")
    assert finished.fields["outcome"] == "skipped"


@pytest.mark.parametrize("market", ["1", "3", ""])
async def test_prices_outside_the_regular_market_are_not_judged(data_dir, caplog, market):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote(market=market))
        connection.push(quote(market=market))
        await settle()

    assert kiwoom.calls("kt10001") == []
    # 같은 이유의 건너뜀은 한 번만 남긴다.
    skipped = [r for r in caplog.records if r.getMessage() == "sl_tp_judge_skipped"]
    assert [r.fields["reason"] for r in skipped] == ["not_regular_market"]


async def test_unreadable_prices_are_skipped_and_the_next_price_is_judged(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        for price in ("", "abc", "0", "abc", "-0"):
            connection.push(quote(price=price))
        await settle()
        assert kiwoom.calls("kt10001") == []
        connection.push(quote())
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))

    reasons = [
        r.fields["reason"] for r in caplog.records if r.getMessage() == "sl_tp_price_unreadable"
    ]
    assert reasons == ["empty", "not_a_number", "not_positive", "not_a_number", "not_positive"]
    assert all(
        r.levelname == "WARNING"
        for r in caplog.records
        if r.getMessage() == "sl_tp_price_unreadable"
    )


async def test_repeated_same_unreadable_price_is_logged_once(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, sl_tp_kiwoom(), data_dir):
        connection = await watching(realtime)
        for _ in range(3):
            connection.push(quote(price="abc"))
        await settle()

    assert messages(caplog).count("sl_tp_price_unreadable") == 1


async def test_excluded_credit_and_unpriced_holdings_are_not_watched(data_dir, caplog):
    write_settings(data_dir, excluded=("000660",))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(
        balance(
            position("005930"),
            position("000660"),
            position("035720", crd_tp="03"),
            position("051910", pur_pric="000000000000000"),
            position("069500"),  # ETF도 주식과 똑같이 지켜본다.
        )
    )
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        for code in ("000660", "035720", "051910"):
            connection.push(quote(code, price="-1000"))
        await settle()

    assert quote_regs(connection)[0]["data"][0]["item"] == ["005930", "069500"]
    assert kiwoom.calls("kt10001") == []
    refreshed = next(r for r in caplog.records if r.getMessage() == "sl_tp_holdings_refreshed")
    # 현금잔고이고 매입가가 있는 종목 수다. 제외종목은 따로 센다.
    assert refreshed.fields["count"] == 3
    assert refreshed.fields["excluded"] == 1
    assert refreshed.fields["credit"] == 1
    assert refreshed.fields["no_purchase_price"] == 1


@pytest.mark.parametrize(
    "sessions",
    [
        [session(enabled=False)],
        [session(start="11:00", end="12:00")],
        [session(start="09:00", end="10:00")],
    ],
    ids=["switched-off", "before-session", "at-session-end"],
)
async def test_no_session_at_start_means_no_lookup_and_no_subscription(data_dir, sessions):
    write_settings(data_dir, *sessions)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        await settle()

    assert realtime.connections == []
    assert kiwoom.calls("kt00018") == []


async def test_session_start_time_is_inside_and_touching_sessions_use_the_later_one(data_dir):
    # 10:00에 끝나는 세션(손절 10)과 10:00에 시작하는 세션(손절 3)이 맞닿아 있다.
    write_settings(
        data_dir,
        session(start="09:00", end="10:00", stop_loss="10"),
        session(start="10:00", end="11:00", stop_loss="3"),
    )
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote(price="-58200"))
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))


async def test_prices_received_after_the_session_end_are_not_judged(data_dir):
    write_settings(data_dir, session(start="10:00", end="10:30"))
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir, clock):
        connection = await watching(realtime)
        clock.at = AT_10 + timedelta(minutes=30)
        connection.push(quote())
        await settle()

    assert kiwoom.calls("kt10001") == []


def recheck_gate(kiwoom: FakeKiwoom) -> Gate:
    """주문 직전 잔고 재확인(두 번째 kt00018)을 붙잡는다."""
    return Gate(kiwoom, lambda r: api(r) == "kt00018" and len(kiwoom.calls("kt00018")) >= 1)


async def test_replaced_connection_during_the_recheck_cancels_the_order(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    gate = recheck_gate(kiwoom)
    async with running(realtime, gate, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        connection.push(REPLACED)
        await wait_until(lambda: "realtime_session_replaced" in messages(caplog))
        gate.open.set()
        await wait_until(lambda: "sl_tp_auto_sell_finished" in messages(caplog))

    assert kiwoom.calls("kt10001") == []
    finished = next(r for r in caplog.records if r.getMessage() == "sl_tp_auto_sell_finished")
    assert finished.fields["outcome"] == "cancelled"


async def test_disconnect_alone_during_the_recheck_still_sells(data_dir, monkeypatch):
    from stock_bot.kiwoom import realtime as realtime_module

    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    gate = recheck_gate(kiwoom)
    async with running(realtime, gate, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        connection.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        gate.open.set()
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))


async def test_shutdown_during_the_recheck_sends_no_order(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    gate = recheck_gate(kiwoom)
    async with running(realtime, gate, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
    gate.open.set()
    await settle()

    assert kiwoom.calls("kt10001") == []


async def test_replaced_connection_while_issuing_the_order_token_cancels_the_order(
    data_dir, caplog
):
    write_settings(data_dir)
    realtime = FakeRealtime()
    # 이미 지난 만료 시각이라 호출마다 토큰을 새로 받는다.
    kiwoom = sl_tp_kiwoom()
    kiwoom.expires_dt = "20000101000000"
    gate = Gate(
        kiwoom,
        lambda r: r.url.path == "/oauth2/token" and len(kiwoom.calls("kt00018")) >= 2,
    )
    async with running(realtime, gate, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        connection.push(REPLACED)
        await wait_until(lambda: "realtime_session_replaced" in messages(caplog))
        gate.open.set()
        await wait_until(lambda: "sl_tp_auto_sell_finished" in messages(caplog))

    assert kiwoom.calls("kt10001") == []


async def test_replaced_connection_while_waiting_for_the_call_limit_cancels_the_order(
    data_dir, caplog, monkeypatch
):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    waiting, release = asyncio.Event(), asyncio.Event()
    original = kiwoom_module._Pacer.wait

    async def slow_wait(self, unit, api_id, patient=False):
        await original(self, unit, api_id, patient)
        if api_id == "kt10001":
            waiting.set()
            await release.wait()

    monkeypatch.setattr(kiwoom_module._Pacer, "wait", slow_wait)
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await waiting.wait()
        connection.push(REPLACED)
        await wait_until(lambda: "realtime_session_replaced" in messages(caplog))
        release.set()
        await wait_until(lambda: "sl_tp_auto_sell_finished" in messages(caplog))

    assert kiwoom.calls("kt10001") == []
    assert "kiwoom_request_cancelled" in messages(caplog)


async def test_disconnect_alone_while_waiting_for_the_call_limit_still_sells(data_dir, monkeypatch):
    from stock_bot.kiwoom import realtime as realtime_module

    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    waiting, release = asyncio.Event(), asyncio.Event()
    original = kiwoom_module._Pacer.wait

    async def slow_wait(self, unit, api_id, patient=False):
        await original(self, unit, api_id, patient)
        if api_id == "kt10001":
            waiting.set()
            await release.wait()

    monkeypatch.setattr(kiwoom_module._Pacer, "wait", slow_wait)
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await waiting.wait()
        connection.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        release.set()
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))


async def test_environment_without_credentials_is_skipped(data_dir, caplog):
    write_settings(data_dir)
    environ = {k: v for k, v in DOMESTIC_PAPER_ENV.items() if not k.startswith("PAPER_KR_")}
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir, environ=environ):
        await settle()

    assert realtime.connections == []
    assert kiwoom.requests == []
    assert "sl_tp_watch_skipped" in messages(caplog)


async def test_real_environment_is_never_watched(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir, environ=REAL_ENV, discord=discord):
        [connection] = await realtime.wait_registered()
        await settle()

    # 실전 연결에는 체결 감시 구독만 있다.
    assert [m["data"][0]["type"] for m in connection.requests("REG")] == [["00", "F5"]]
    assert kiwoom.calls("kt00018") == []
    assert kiwoom.calls("kt10001") == []


async def test_unreadable_settings_file_stops_the_environment(data_dir, caplog):
    path = data_dir / "sl-tp" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        await settle()

    assert realtime.connections == []
    assert kiwoom.calls("kt00018") == []
    stopped = next(r for r in caplog.records if r.getMessage() == "sl_tp_watch_stopped")
    assert stopped.fields["reason"] == "sl_tp_settings_unreadable"


async def test_auto_sell_shares_the_connection_with_fill_watch(data_dir):
    # Discord가 켜져 있으면 체결 감시와 SL / TP가 연결 하나를 함께 쓴다.
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom().reply("ka10075", {"oso": []})
    async with running(realtime, kiwoom, data_dir, discord=FakeDiscord()):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))

    assert len(realtime.connections) == 1
    assert {m["grp_no"] for m in connection.requests("REG")} == {"1", "2"}


async def test_trigger_order_and_result_logs_share_one_request_id(data_dir, json_logs):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(
            lambda: "sl_tp_auto_sell_finished" in [line["event"] for line in json_logs.lines]
        )

    lines = {line["event"]: line for line in json_logs.lines}
    triggered = lines["sl_tp_triggered"]
    assert triggered["env"] == "domestic_paper"
    assert triggered["trigger"] == "stop_loss"
    assert triggered["price"] == "58200"
    assert triggered["purchase_price"] == "60000"
    assert triggered["threshold"] == "3"
    assert triggered["session"] == "09:00-15:30"
    flow = [
        "sl_tp_triggered",
        "auto_sell_requested",
        "sellable_checked",
        "order_accepted",
        "sl_tp_auto_sell_finished",
    ]
    assert {lines[event]["request_id"] for event in flow} == {triggered["request_id"]}
    assert triggered["request_id"] != "-"
    assert lines["sl_tp_auto_sell_finished"]["outcome"] == "accepted"
    assert lines["sl_tp_auto_sell_finished"]["order_no"] == "0000138"
    assert "sl_tp_quote_received" not in lines  # 체결가 수신은 DEBUG라 기본 수준에서 남지 않는다.


async def test_real_auto_sell_never_reaches_kiwoom():
    kiwoom = FakeKiwoom().reply("kt10001", {"ord_no": "0000141"})
    client = KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(kiwoom))

    async def holding(spec, code):
        return {"quantity": 10, "sellable_quantity": 10}

    orders = OrderService(client, holding, lambda spec: asyncio.sleep(0, []))
    result = await orders.sell_all_at_market(
        ENVIRONMENTS[Environment.DOMESTIC_REAL], "005930", "", "sl-tp-x", lambda: True
    )

    assert result.outcome == "rejected"
    assert kiwoom.calls("kt10001") == []
