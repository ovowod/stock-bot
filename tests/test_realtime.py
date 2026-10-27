"""공유 실시간 연결: 여러 기능이 한 연결에 구독을 싣는 동작."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from stock_bot import realtime as realtime_module
from stock_bot.account import AccountService
from stock_bot.auto_sell import AutoSell
from stock_bot.config import ENVIRONMENTS, Environment
from stock_bot.fill_feed import FillFeed
from stock_bot.fill_watch import FillWatcher
from stock_bot.kiwoom import KiwoomClient
from stock_bot.notification import DiscordNotifier
from stock_bot.open_order import OpenOrderService
from stock_bot.order import OrderService
from stock_bot.realtime import EventKind, RealtimeEvent, RealtimeHub, Subscription
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom
from tests.fake_realtime import OK_REG, FakeRealtime, fill_event
from tests.test_fill_notifications import (
    DISCORD_ENV,
    DOMESTIC_PAPER_ENV,
    REPLACED,
    FakeDiscord,
    fill_kiwoom,
    wait_until,
)

pytestmark = pytest.mark.anyio

DOMESTIC_PAPER = ENVIRONMENTS[Environment.DOMESTIC_PAPER]
FILLS = Subscription("1", [""], ["00"])
QUOTES = Subscription("2", ["005930", "000660"], ["0B"], refresh="0")
REJECTED_REG = {"trnm": "REG", "return_code": 1, "return_msg": "등록 실패"}
START = datetime(2026, 10, 26, 0, 30, tzinfo=UTC)


class Recorder:
    """채널이 받은 이벤트를 모은다. gate를 닫아 두면 처리를 멈춘다."""

    def __init__(self) -> None:
        self.events: list[RealtimeEvent] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def __call__(self, event: RealtimeEvent) -> None:
        await self.gate.wait()
        self.events.append(event)

    def kinds(self) -> list[EventKind]:
        return [e.kind for e in self.events]

    def real(self) -> list[RealtimeEvent]:
        return [e for e in self.events if e.kind is EventKind.REAL]


class Clock:
    def __init__(self) -> None:
        self.at = START

    def __call__(self) -> datetime:
        return self.at


@asynccontextmanager
async def hub_with(
    realtime: FakeRealtime,
    discord: FakeDiscord | None = None,
    now: Callable[[], datetime] | None = None,
) -> AsyncIterator[RealtimeHub]:
    kiwoom = KiwoomClient(DOMESTIC_PAPER_ENV, transport=httpx.MockTransport(FakeKiwoom()))
    notifier = DiscordNotifier(
        DISCORD_ENV if discord else {}, transport=httpx.MockTransport(discord or FakeDiscord())
    )
    hub = RealtimeHub(kiwoom, notifier, realtime, now or Clock())
    try:
        yield hub
    finally:
        await hub.stop()


def quote(code: str, price: str = "+60000") -> dict:
    return {
        "trnm": "REAL",
        "data": [{"type": "0B", "name": "주식체결", "item": code, "values": {"10": price}}],
    }


def order_fill() -> dict:
    return {"trnm": "REAL", "data": [{"type": "00", "name": "주문체결", "item": "", "values": {}}]}


@pytest.fixture
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(realtime_module, "RECONNECT_MAX_SECONDS", 0.04)


async def test_does_not_connect_without_subscriptions():
    realtime = FakeRealtime()
    async with hub_with(realtime) as hub:
        hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", Recorder())
        await asyncio.sleep(0.05)

    assert realtime.connections == []


async def test_two_features_register_one_at_a_time():
    pending: list[dict] = []
    realtime = FakeRealtime(responder=lambda message: pending.append(message))
    async with hub_with(realtime) as hub:
        fills = hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", Recorder())
        quotes = hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", Recorder())
        fills.subscribe(FILLS)
        quotes.subscribe(QUOTES)
        await wait_until(lambda: len(pending) == 1)
        [connection] = realtime.connections
        await asyncio.sleep(0.05)
        # 첫 REG의 응답이 오기 전에는 다음 REG를 보내지 않는다.
        assert len(connection.requests("REG")) == 1
        connection.push(OK_REG)
        await wait_until(lambda: len(connection.requests("REG")) == 2)

    assert len(realtime.connections) == 1
    first, second = connection.requests("REG")
    assert first == {
        "trnm": "REG",
        "grp_no": "1",
        "refresh": "1",
        "data": [{"item": [""], "type": ["00"]}],
    }
    assert second == {
        "trnm": "REG",
        "grp_no": "2",
        "refresh": "0",
        "data": [{"item": ["005930", "000660"], "type": ["0B"]}],
    }


async def test_rejection_reaches_only_the_feature_that_subscribed():
    realtime = FakeRealtime(
        responder=lambda message: REJECTED_REG if message["grp_no"] == "2" else OK_REG
    )
    fills, quotes = Recorder(), Recorder()
    async with hub_with(realtime) as hub:
        hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", fills).subscribe(FILLS)
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", quotes).subscribe(QUOTES)
        await wait_until(lambda: EventKind.REJECTED in quotes.kinds())
        [connection] = realtime.connections
        connection.push(order_fill())
        await wait_until(lambda: len(fills.real()) == 1)
        # 거부돼도 연결은 그대로다.
        assert not connection.closed

    assert EventKind.REJECTED not in fills.kinds()
    assert EventKind.REGISTERED in fills.kinds()
    rejected = next(e for e in quotes.events if e.kind is EventKind.REJECTED)
    assert rejected.detail == {"return_code": 1, "return_msg": "등록 실패"}
    assert len(realtime.connections) == 1


async def test_events_go_only_to_the_feature_that_subscribed_the_type():
    realtime = FakeRealtime()
    fills, quotes = Recorder(), Recorder()
    async with hub_with(realtime) as hub:
        hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", fills).subscribe(FILLS)
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", quotes).subscribe(QUOTES)
        connection = (await realtime.wait_registered())[0]
        await wait_until(lambda: len(connection.requests("REG")) == 2)
        connection.push({"trnm": "REAL", "data": [*quote("005930")["data"], *order_fill()["data"]]})
        await wait_until(lambda: len(fills.real()) == 1 and len(quotes.real()) == 1)

    assert [i["type"] for i in fills.real()[0].items] == ["00"]
    assert [i["type"] for i in quotes.real()[0].items] == ["0B"]


async def test_immediate_channel_runs_before_queued_channels_for_the_same_message():
    realtime = FakeRealtime()
    order: list[str] = []

    async def quotes(event: RealtimeEvent) -> None:
        if event.kind is EventKind.REAL:
            order.append("quote")

    def fills(event: RealtimeEvent) -> None:
        if event.kind is EventKind.REAL:
            order.append("fill")

    async with hub_with(realtime) as hub:
        # 대기열 채널을 먼저 열어, 메시지를 나눌 때 대기열 채널이 먼저 받게 한다.
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", quotes).subscribe(QUOTES)
        hub.immediate_channel(DOMESTIC_PAPER, "fill_feed", "체결 감시", fills).subscribe(FILLS)
        connection = (await realtime.wait_registered())[0]
        await wait_until(lambda: len(connection.requests("REG")) == 2)
        connection.push({"trnm": "REAL", "data": [*quote("005930")["data"], *order_fill()["data"]]})
        await wait_until(lambda: len(order) == 2)

    assert order == ["fill", "quote"]


async def test_immediate_handler_error_is_logged_and_receiving_continues(caplog):
    realtime = FakeRealtime()
    fills: list[RealtimeEvent] = []
    quotes = Recorder()

    def failing(event: RealtimeEvent) -> None:
        if event.kind is EventKind.REAL:
            fills.append(event)
            if len(fills) == 1:
                raise RuntimeError("boom")

    async with hub_with(realtime) as hub:
        hub.immediate_channel(DOMESTIC_PAPER, "fill_feed", "체결 감시", failing).subscribe(FILLS)
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", quotes).subscribe(QUOTES)
        connection = (await realtime.wait_registered())[0]
        await wait_until(lambda: len(connection.requests("REG")) == 2)
        mixed = {"trnm": "REAL", "data": [*order_fill()["data"], *quote("005930")["data"]]}
        connection.push(mixed)
        connection.push(mixed)
        await wait_until(lambda: len(fills) == 2 and len(quotes.real()) == 2)
        assert not connection.closed

    failed = [r for r in caplog.records if r.getMessage() == "realtime_handler_failed"]
    assert len(failed) == 1
    assert failed[0].fields["feature"] == "fill_feed"


def ignore(value: object) -> None:
    pass


async def test_fill_feed_registers_only_the_real_markets_that_are_subscribed():
    # 실전 연결은 국내·미국이 하나다. 가짜 서버만 쓰고 주문 TR은 부르지 않는다.
    realtime = FakeRealtime()
    kiwoom = KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(FakeKiwoom()))
    hub = RealtimeHub(kiwoom, DiscordNotifier({}), realtime, Clock())
    feed = FillFeed(hub)
    try:
        domestic = feed.subscribe(ENVIRONMENTS[Environment.DOMESTIC_REAL], "체결 감시", ignore)
        [connection] = await realtime.wait_registered()
        us = feed.subscribe(ENVIRONMENTS[Environment.US_REAL], "체결 감시", ignore)
        await wait_until(lambda: len(connection.requests("REG")) == 2)
        domestic()
        us()
        await wait_until(lambda: connection.closed)
    finally:
        await feed.stop()
        await hub.stop()

    assert [m["data"][0]["type"] for m in connection.requests("REG")] == [["00"], ["00", "F5"]]
    # 마지막 신청이 빠지면 해지하고, 다른 구독이 없으니 연결을 닫는다.
    assert [m["grp_no"] for m in connection.requests("REMOVE")] == ["1"]


async def test_slow_fill_lookup_does_not_delay_another_features_events():
    # 체결 알림의 미체결 조회(ka10075) 응답을 막아 두어도 다른 기능의 시세는 바로 전달된다.
    lookup_started, lookup_gate = asyncio.Event(), asyncio.Event()
    kiwoom = fill_kiwoom()

    async def slow_kiwoom(request: httpx.Request) -> httpx.Response:
        if request.headers.get("api-id") == "ka10075":
            lookup_started.set()
            await lookup_gate.wait()
        return kiwoom(request)

    realtime = FakeRealtime()
    discord = FakeDiscord()
    client = KiwoomClient(DOMESTIC_PAPER_ENV, transport=httpx.MockTransport(slow_kiwoom))
    notifier = DiscordNotifier(DISCORD_ENV, transport=httpx.MockTransport(discord))
    hub = RealtimeHub(client, notifier, realtime, Clock())
    open_orders = OpenOrderService(client)
    orders = OrderService(client, AccountService(client).holding, open_orders.orders)
    fills = FillWatcher(client, open_orders, notifier, FillFeed(hub), AutoSell(orders))
    quotes = Recorder()
    try:
        fills.start()
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", quotes).subscribe(QUOTES)
        [connection] = await realtime.wait_registered()
        await wait_until(lambda: len(connection.requests("REG")) == 2)
        connection.push(fill_event())
        await wait_until(lookup_started.is_set)
        connection.push(quote("005930"))
        await wait_until(lambda: len(quotes.real()) == 1)
        assert discord.payloads == []
        lookup_gate.set()
        await discord.wait_for(1)
    finally:
        await fills.stop()
        await hub.stop()


async def test_events_carry_receive_time_and_connection_number(fast_reconnect):
    clock = Clock()
    realtime = FakeRealtime()
    quotes = Recorder()
    async with hub_with(realtime, now=clock) as hub:
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", quotes).subscribe(QUOTES)
        [first] = await realtime.wait_registered()
        first.push(quote("005930"))
        await wait_until(lambda: len(quotes.real()) == 1)
        clock.at = START + timedelta(seconds=5)
        first.drop()
        second = (await realtime.wait_registered(2))[1]
        second.push(quote("005930"))
        await wait_until(lambda: len(quotes.real()) == 2)

    before, after = quotes.real()
    assert (before.connection, before.received_at) == (1, START)
    assert (after.connection, after.received_at) == (2, START + timedelta(seconds=5))
    # 서버가 꺼질 때의 끊김이 뒤에 더 붙을 수 있다.
    assert quotes.kinds()[:7] == [
        EventKind.CONNECTED,
        EventKind.REGISTERED,
        EventKind.REAL,
        EventKind.DISCONNECTED,
        EventKind.CONNECTED,
        EventKind.REGISTERED,
        EventKind.REAL,
    ]


async def test_reconnect_registers_every_live_subscription_again(fast_reconnect):
    realtime = FakeRealtime()
    async with hub_with(realtime) as hub:
        hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", Recorder()).subscribe(FILLS)
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", Recorder()).subscribe(QUOTES)
        [first] = await realtime.wait_registered()
        await wait_until(lambda: len(first.requests("REG")) == 2)
        first.drop()
        second = (await realtime.wait_registered(2))[1]
        await wait_until(lambda: len(second.requests("REG")) == 2)

    assert second.sent_trnm() == ["LOGIN", "REG", "REG"]
    assert {r["grp_no"] for r in second.requests("REG")} == {"1", "2"}


async def test_no_reply_to_registration_reconnects_and_registers_all(fast_reconnect, monkeypatch):
    monkeypatch.setattr(realtime_module, "RESPONSE_TIMEOUT_SECONDS", 0.05)
    replies = iter([OK_REG, None])
    realtime = FakeRealtime(responder=lambda message: next(replies, OK_REG))
    async with hub_with(realtime) as hub:
        hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", Recorder()).subscribe(FILLS)
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", Recorder()).subscribe(QUOTES)
        await wait_until(lambda: len(realtime.connections) == 2)
        second = realtime.connections[1]
        await wait_until(lambda: len(second.requests("REG")) == 2)

    first = realtime.connections[0]
    # 늦은 응답을 다음 요청의 응답으로 착각하지 않게 연결을 닫고 처음부터 다시 등록한다.
    assert first.closed
    assert {r["grp_no"] for r in second.requests("REG")} == {"1", "2"}


async def test_unsubscribe_sends_remove_and_keeps_other_subscriptions():
    realtime = FakeRealtime()
    async with hub_with(realtime) as hub:
        hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", Recorder()).subscribe(FILLS)
        quotes = hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", Recorder())
        quotes.subscribe(QUOTES)
        [connection] = await realtime.wait_registered()
        await wait_until(lambda: len(connection.requests("REG")) == 2)
        quotes.subscribe(None)
        await wait_until(lambda: len(connection.requests("REMOVE")) == 1)
        assert not connection.closed

    assert connection.requests("REMOVE") == [
        {
            "trnm": "REMOVE",
            "grp_no": "2",
            "refresh": "0",
            "data": [{"item": ["005930", "000660"], "type": ["0B"]}],
        }
    ]


async def test_changed_subscription_is_registered_again():
    realtime = FakeRealtime()
    async with hub_with(realtime) as hub:
        quotes = hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", Recorder())
        quotes.subscribe(QUOTES)
        [connection] = await realtime.wait_registered()
        quotes.subscribe(Subscription("2", ["005930"], ["0B"], refresh="0"))
        await wait_until(lambda: len(connection.requests("REG")) == 2)

    assert connection.requests("REG")[1]["data"] == [{"item": ["005930"], "type": ["0B"]}]
    assert connection.requests("REMOVE") == []


async def test_replaced_connection_stops_every_feature_and_notifies_once(fast_reconnect):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    fills, quotes = Recorder(), Recorder()
    async with hub_with(realtime, discord) as hub:
        hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", fills).subscribe(FILLS)
        hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", quotes).subscribe(QUOTES)
        [connection] = await realtime.wait_registered()
        connection.push(REPLACED)
        await discord.wait_for(1)
        await wait_until(lambda: EventKind.STOPPED in quotes.kinds())
        await asyncio.sleep(0.1)

    assert len(realtime.connections) == 1
    assert EventKind.STOPPED in fills.kinds()
    assert len(discord.payloads) == 1
    assert "체결 감시·SL / TP" in discord.payloads[0]["embeds"][0]["description"]


def reconnect_waits(caplog) -> list[float]:
    return [
        r.fields["wait_seconds"]
        for r in caplog.records
        if r.getMessage() == "realtime_reconnect_wait"
    ]


async def test_unanswered_registration_keeps_growing_the_reconnect_wait(
    fast_reconnect, monkeypatch, caplog
):
    # LOGIN은 되지만 REG 응답이 끝내 오지 않으면 재연결 간격이 처음으로 돌아가지 않아야 한다.
    monkeypatch.setattr(realtime_module, "RESPONSE_TIMEOUT_SECONDS", 0.02)
    realtime = FakeRealtime(responder=lambda message: None)
    async with hub_with(realtime) as hub:
        hub.channel(DOMESTIC_PAPER, "fill_watch", "체결 감시", Recorder()).subscribe(FILLS)
        await wait_until(lambda: len(reconnect_waits(caplog)) >= 3)

    assert reconnect_waits(caplog)[:3] == [0.01, 0.02, 0.04]


async def test_connection_closes_when_the_last_subscription_is_removed_and_reopens_later():
    # 구독이 하나도 없는 인증정보는 연결해 두지 않는다.
    # ADR 0001: 같은 앱 키를 쓰는 다른 서버를 끊지 않게 한다.
    realtime = FakeRealtime()
    async with hub_with(realtime) as hub:
        quotes = hub.channel(DOMESTIC_PAPER, "sl_tp", "SL / TP", Recorder())
        quotes.subscribe(QUOTES)
        [first] = await realtime.wait_registered()
        quotes.subscribe(None)
        await wait_until(lambda: first.closed)
        assert first.requests("REMOVE")
        await asyncio.sleep(0.05)
        assert len(realtime.connections) == 1
        quotes.subscribe(QUOTES)
        second = (await realtime.wait_registered(2))[1]

    assert second.sent_trnm() == ["LOGIN", "REG"]
