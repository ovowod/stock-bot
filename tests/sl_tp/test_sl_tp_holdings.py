"""SL / TP 보유종목 다시 읽기: 내 계좌 체결과 재연결 때, 조회 실패 뒤 재시도(국내 모의)."""

import asyncio
import json
from datetime import timedelta
from typing import Any

import pytest

from stock_bot.fill import feed as fill_feed
from stock_bot.kiwoom import realtime as realtime_module
from stock_bot.sl_tp import watch as sl_tp_watch
from tests.fake_kiwoom import kiwoom_error
from tests.fake_realtime import FakeConnection, FakeRealtime, fill_event
from tests.fill.test_fill_notifications import REPLACED, FakeDiscord, SlowDiscord, wait_until
from tests.log_capture import JsonLines
from tests.sl_tp.test_sl_tp_auto_sell import (
    AT_10,
    Clock,
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
from tests.sl_tp.test_sl_tp_sessions import nth_call_gate

pytestmark = pytest.mark.anyio

LOOKUP_FAILED = kiwoom_error(1, "조회 실패")


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    monkeypatch.setattr(sl_tp_watch, "SESSION_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(sl_tp_watch, "HOLDINGS_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(sl_tp_watch, "HOLDINGS_RETRY_MAX_SECONDS", 0.04)
    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)


def fill_regs(connection: FakeConnection) -> list[dict[str, Any]]:
    return [m for m in connection.requests("REG") if m["data"][0]["type"] == ["00"]]


def resumed(caplog) -> int:
    return messages(caplog).count("sl_tp_judging_resumed")


def at_price(price: str) -> dict[str, str]:
    """매입가가 price원인 삼성전자 현금잔고."""
    return position(pur_pric=f"{int(price):015d}")


async def test_sl_tp_registers_order_fills_itself_when_discord_is_off(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, sl_tp_kiwoom(), data_dir):
        connection = await watching(realtime)
        await wait_until(lambda: bool(fill_regs(connection)))

    assert fill_regs(connection) == [
        {"trnm": "REG", "grp_no": "1", "refresh": "1", "data": [{"item": [""], "type": ["00"]}]}
    ]


async def test_order_fills_are_registered_once_when_fill_alerts_are_on(data_dir):
    # 체결 알림과 SL / TP가 함께 받는다. 같은 항목을 그룹 둘에 겹쳐 등록하지 않는다.
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom().reply("ka10075", {"oso": []})
    async with running(realtime, kiwoom, data_dir, discord=FakeDiscord()):
        connection = await watching(realtime)
        await settle()

    assert len(fill_regs(connection)) == 1


async def test_fill_rereads_holdings_and_judges_with_the_new_purchase_price(data_dir):
    write_settings(data_dir, session(stop_loss="3"))
    realtime = FakeRealtime()
    # 추가 매수로 평균 매입가가 60,000원에서 50,000원으로 내려갔다.
    kiwoom = sl_tp_kiwoom(balance(at_price("60000")), balance(at_price("50000")))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(fill_event())
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 2)
        await settle()
        connection.push(quote(price="-58200"))  # 60,000원 기준이면 -3%지만 50,000원 기준은 +16.4%
        await settle()
        assert kiwoom.calls("kt10001") == []
        connection.push(quote(price="-48500"))  # 50,000원 기준 -3%
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)


async def test_fills_that_arrive_together_are_read_once(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        burst = fill_event()
        burst["data"] = burst["data"] * 3
        connection.push(burst)
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 2)
        await settle()

    assert len(kiwoom.calls("kt00018")) == 2


async def test_fill_during_a_slow_read_reads_again_and_uses_the_second_result(data_dir, caplog):
    write_settings(data_dir, session(take_profit="5", stop_loss=None))
    realtime = FakeRealtime()
    # 처음 60,000원, 늦게 오는 첫 다시 읽기 70,000원, 두 번째 다시 읽기 50,000원.
    kiwoom = sl_tp_kiwoom(
        balance(at_price("60000")), balance(at_price("70000")), balance(at_price("50000"))
    )
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir):
        connection = await watching(realtime)
        connection.push(fill_event())
        await gate.hit.wait()
        connection.push(fill_event())
        # 다시 읽는 동안은 기준을 넘어도 판정하지 않는다(50,000원 기준 +5%).
        connection.push(quote(price="+52500"))
        await settle()
        assert kiwoom.calls("kt10001") == []
        gate.open.set()
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 3)
        await wait_until(lambda: resumed(caplog) == 2)
        connection.push(quote(price="+52500"))
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)

    discarded = [r for r in caplog.records if r.getMessage() == "sl_tp_holdings_discarded"]
    assert [r.fields["reason"] for r in discarded] == ["newer_trigger"]


async def test_reread_adds_new_holdings_and_drops_sold_ones(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(position("005930")), balance(position("000660")))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(fill_event())
        await wait_until(lambda: len(quote_regs(connection)) == 2)
        connection.push(quote("000660"))
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)

    assert quote_regs(connection)[1]["data"][0]["item"] == ["000660"]
    refreshed = [r for r in caplog.records if r.getMessage() == "sl_tp_holdings_refreshed"]
    # 로그 필드의 목록은 JSON 문자열로 남는다.
    assert refreshed[-1].fields["removed"] == '["005930"]'


async def test_unchanged_holdings_do_not_register_quotes_again(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(fill_event())
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 2)
        await settle()

    assert len(quote_regs(connection)) == 1


@pytest.mark.parametrize("status", ["접수", "확인", "취소"])
async def test_order_events_other_than_fills_do_not_reread(data_dir, status):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(fill_event(**{"913": status}))
        await settle()

    assert len(kiwoom.calls("kt00018")) == 1


async def test_reconnect_registers_everything_rereads_and_keeps_the_trigger_record(
    data_dir, caplog
):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom().reply("ka10075", {"oso": []})
    async with running(realtime, kiwoom, data_dir, discord=FakeDiscord()):
        first = await watching(realtime)
        first.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)
        first.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        second = realtime.connections[1]
        await wait_until(lambda: {m["grp_no"] for m in second.requests("REG")} == {"1", "2"})
        # 처음 읽기, 주문 직전 확인, 재연결 뒤 다시 읽기.
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 3)
        await wait_until(lambda: resumed(caplog) == 2)
        second.push(quote())
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1


async def test_quotes_left_from_before_a_reconnect_are_not_judged(data_dir, caplog, monkeypatch):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    # 큐 처리가 밀린 것처럼 SL / TP의 시세 처리를 붙잡아 둔다.
    held, release = asyncio.Event(), asyncio.Event()
    original = sl_tp_watch._Watch._on_event

    async def slow(self, event):
        if event.kind is sl_tp_watch.EventKind.REAL and not release.is_set():
            held.set()
            await release.wait()
        await original(self, event)

    monkeypatch.setattr(sl_tp_watch._Watch, "_on_event", slow)
    async with running(realtime, kiwoom, data_dir):
        first = await watching(realtime)
        first.push(quote())
        await held.wait()
        first.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        await wait_until(lambda: bool(quote_regs(realtime.connections[1])))
        release.set()
        await settle()

    assert kiwoom.calls("kt10001") == []


async def test_read_failure_pauses_judging_and_retries_with_a_growing_wait(data_dir, caplog):
    write_settings(data_dir)
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(), LOOKUP_FAILED, LOOKUP_FAILED, LOOKUP_FAILED, balance())
    async with running(realtime, kiwoom, data_dir, clock):
        connection = await watching(realtime)
        connection.push(fill_event())
        await wait_until(lambda: "sl_tp_holdings_failed" in messages(caplog))
        connection.push(quote())
        await settle()
        assert kiwoom.calls("kt10001") == []
        await wait_until(lambda: "sl_tp_holdings_recovered" in messages(caplog))
        await wait_until(lambda: resumed(caplog) == 2)
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)

    failures = [r.fields for r in caplog.records if r.getMessage() == "sl_tp_holdings_failed"]
    assert [f["wait_seconds"] for f in failures] == [0.01, 0.02, 0.04]
    # 첫 실패 시각을 계속 기억하고, 성공하면 지운다.
    assert {f["failed_since"] for f in failures} == {AT_10.isoformat()}
    recovered = next(r for r in caplog.records if r.getMessage() == "sl_tp_holdings_recovered")
    assert recovered.fields["failed_since"] == AT_10.isoformat()


async def test_session_end_stops_retrying_the_read(data_dir, caplog):
    write_settings(data_dir, session(start="09:00", end="10:01"))
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(), LOOKUP_FAILED)
    async with running(realtime, kiwoom, data_dir, clock):
        connection = await watching(realtime)
        connection.push(fill_event())
        await wait_until(lambda: messages(caplog).count("sl_tp_holdings_failed") >= 2)
        clock.at = AT_10 + timedelta(minutes=1)
        await wait_until(lambda: "sl_tp_holdings_retry_stopped" in messages(caplog))
        calls = len(kiwoom.calls("kt00018"))
        await asyncio.sleep(0.1)
        assert len(kiwoom.calls("kt00018")) == calls


async def test_reconnect_during_a_read_discards_that_result_and_reads_again(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir):
        first = await watching(realtime)
        first.push(fill_event())
        await gate.hit.wait()
        first.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        await wait_until(lambda: len(realtime.connections[1].requests("REG")) >= 1)
        gate.open.set()
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 3)
        await wait_until(lambda: resumed(caplog) == 2)

    assert "sl_tp_holdings_discarded" in messages(caplog)


REJECTED_REG = {"trnm": "REG", "return_code": 1, "return_msg": "등록 실패"}


def rejecting_fills(times: int | None = None) -> FakeRealtime:
    """주문체결 그룹(grp_no 1)의 REG를 처음 times번(None이면 늘) 거부한다."""
    left = {"count": times}

    def respond(message: dict[str, Any]) -> dict[str, Any]:
        if message["grp_no"] == "1" and (left["count"] is None or left["count"] > 0):
            if left["count"] is not None:
                left["count"] -= 1
            return REJECTED_REG
        return {"trnm": message["trnm"], "return_code": 0, "return_msg": ""}

    return FakeRealtime(responder=respond)


@pytest.fixture
def fast_register_retry(monkeypatch):
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_MAX_SECONDS", 0.04)


async def test_rejected_fill_registration_is_retried_and_rereads_before_judging(
    data_dir, caplog, fast_register_retry
):
    # Discord가 꺼져 있어 체결 알림 없이 SL / TP만 체결을 신청한다.
    write_settings(data_dir, session(stop_loss="3"))
    realtime = rejecting_fills(times=1)
    # 거부된 동안 추가 매수로 평균 매입가가 60,000원에서 50,000원으로 내려갔다.
    kiwoom = sl_tp_kiwoom(balance(at_price("60000")), balance(at_price("50000")))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        await wait_until(lambda: "sl_tp_subscription_rejected" in messages(caplog))
        # 체결 feed가 스스로 다시 등록하고, SL / TP는 보유종목을 다시 읽은 뒤 판정을 잇는다.
        await wait_until(lambda: len(fill_regs(connection)) == 2)
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 2)
        await wait_until(lambda: resumed(caplog) == 2)
        connection.push(quote(price="-58200"))  # 60,000원 기준이면 -3%지만 50,000원 기준은 +16.4%
        await settle()
        assert kiwoom.calls("kt10001") == []
        connection.push(quote(price="-48500"))  # 50,000원 기준 -3%
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)

    rejected = next(r for r in caplog.records if r.getMessage() == "sl_tp_subscription_rejected")
    assert rejected.fields["subscription"] == "fills"
    reread = next(r for r in caplog.records if r.getMessage() == "sl_tp_holdings_refreshed")
    assert reread is not None


async def test_quotes_are_not_judged_while_fill_registration_is_rejected(data_dir, caplog):
    write_settings(data_dir)
    realtime = rejecting_fills()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        await wait_until(lambda: "sl_tp_subscription_rejected" in messages(caplog))
        connection.push(quote())
        await settle()

    # 체결을 받지 못하면 바뀐 매입가를 모르므로 판정하지 않는다.
    assert kiwoom.calls("kt10001") == []


async def test_joining_a_rejected_fill_registration_learns_the_rejection(
    data_dir, caplog, monkeypatch
):
    # 체결 알림의 주문체결 등록이 거부돼 재시도를 기다리는 동안 SL/TP 세션이 시작된다.
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_INITIAL_SECONDS", 30)
    write_settings(data_dir, session(start="10:00", end="11:00"))
    clock = Clock(AT_10 - timedelta(seconds=1))
    realtime = rejecting_fills()
    kiwoom = sl_tp_kiwoom().reply("ka10075", {"oso": []})
    async with running(realtime, kiwoom, data_dir, clock, discord=FakeDiscord()):
        await wait_until(lambda: "fill_feed_register_retry" in messages(caplog))
        clock.at = AT_10
        connection = await watching(realtime)
        await wait_until(lambda: "sl_tp_subscription_rejected" in messages(caplog))
        connection.push(quote())
        await settle()

    assert kiwoom.calls("kt10001") == []
    # 다시 등록하지 않고 체결 feed가 기억한 거부 상태를 바로 받았다.
    assert len(fill_regs(connection)) == 1


async def test_quote_before_a_fill_in_one_message_is_not_judged_with_the_old_price(
    data_dir, caplog
):
    write_settings(data_dir, session(stop_loss="3"))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(at_price("60000")), balance(at_price("50000")))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        # 60,000원 기준 -3%인 시세 체결과 매입가를 바꾼 내 계좌 체결이 한 메시지로 온다.
        mixed = quote(price="-58200")
        mixed["data"] = [*mixed["data"], *fill_event()["data"]]
        connection.push(mixed)
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 2)
        await wait_until(lambda: resumed(caplog) == 2)
        assert kiwoom.calls("kt10001") == []
        connection.push(quote(price="-48500"))  # 50,000원 기준 -3%
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)


async def test_quote_waiting_before_a_fill_is_skipped_while_rereading(data_dir, caplog):
    write_settings(data_dir, session(stop_loss="3"))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(at_price("60000")), balance(at_price("50000")))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        # 시세 체결이 SL / TP 대기열에서 처리되기 전에 내 계좌 체결이 먼저 처리된다.
        connection.push(quote(price="-58200"))
        connection.push(fill_event())
        await wait_until(lambda: resumed(caplog) == 2)

    assert kiwoom.calls("kt10001") == []
    skipped = [r for r in caplog.records if r.getMessage() == "sl_tp_judge_skipped"]
    assert [r.fields["reason"] for r in skipped] == ["holdings_pending"]


async def test_slow_fill_alert_does_not_delay_the_reread(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = SlowDiscord()
    kiwoom = sl_tp_kiwoom().reply("ka10075", {"oso": []})
    async with running(realtime, kiwoom, data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(fill_event())
        await discord.wait_for(1)
        # 체결 알림이 Discord 응답을 기다리는 동안 다음 체결이 와도 SL / TP는 바로 다시 읽는다.
        connection.push(fill_event())
        await wait_until(lambda: len(kiwoom.calls("kt00018")) >= 2)
        assert len(discord.payloads) == 1
        discord.gate.set()


async def test_fill_seen_log_carries_the_fill_request_id(data_dir, json_logs: JsonLines):
    write_settings(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, sl_tp_kiwoom(), data_dir):
        connection = await watching(realtime)
        connection.push(fill_event())
        await wait_until(
            lambda: any(line["event"] == "sl_tp_fill_seen" for line in json_logs.lines)
        )

    received = next(line for line in json_logs.lines if line["event"] == "fill_received")
    seen = next(line for line in json_logs.lines if line["event"] == "sl_tp_fill_seen")
    assert seen["request_id"] == received["request_id"] != "-"


async def test_session_starting_while_disconnected_reads_holdings_after_reconnecting(
    data_dir, caplog, monkeypatch
):
    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.3)
    write_settings(data_dir, session(start="10:00", end="11:00"))
    clock = Clock(AT_10 - timedelta(seconds=1))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom().reply("ka10075", {"oso": []})
    async with running(realtime, kiwoom, data_dir, clock, discord=FakeDiscord()):
        [first] = await realtime.wait_registered()
        first.drop()
        await wait_until(lambda: "realtime_reconnect_wait" in messages(caplog))
        clock.at = AT_10
        await wait_until(lambda: "sl_tp_session_started" in messages(caplog))
        await settle()
        # 연결이 없는 동안은 읽지 않는다.
        assert kiwoom.calls("kt00018") == []
        second = await watching(realtime)
        await settle()

    assert second is realtime.connections[1]
    # 다시 붙은 연결로 한 번 읽었고, 그 연결의 CONNECTED로 또 읽지 않았다.
    assert len(kiwoom.calls("kt00018")) == 1


async def test_reconnect_registers_fills_before_quotes(data_dir):
    # Discord가 꺼져 있어도 예전처럼 체결(그룹 1)을 시세(그룹 2)보다 먼저 다시 등록한다.
    write_settings(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, sl_tp_kiwoom(), data_dir):
        first = await watching(realtime)
        first.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        second = realtime.connections[1]
        await wait_until(lambda: len(second.requests("REG")) == 2)

    assert [m["grp_no"] for m in second.requests("REG")] == ["1", "2"]


async def test_reread_logs_do_not_carry_the_fill_request_id(data_dir, json_logs: JsonLines):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(fill_event())
        await wait_until(lambda: len(kiwoom.calls("kt00018")) == 2)
        await settle()

    # 다시 읽기는 여러 체결을 합치므로 어느 한 체결의 요청 ID로 남기지 않는다.
    received = next(line for line in json_logs.lines if line["event"] == "fill_received")
    refreshed = [line for line in json_logs.lines if line["event"] == "sl_tp_holdings_refreshed"]
    assert len(refreshed) == 2
    assert all(line["request_id"] != received["request_id"] for line in refreshed)


async def test_replaced_connection_names_sl_tp_once_when_it_is_alone(data_dir, caplog):
    # Discord가 꺼져 있어 중단 알림은 가지 않는다. SL / TP는 시세와 체결 feed 두 채널을 쓴다.
    write_settings(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, sl_tp_kiwoom(), data_dir):
        connection = await watching(realtime)
        connection.push(REPLACED)
        await wait_until(lambda: "realtime_session_replaced" in messages(caplog))

    replaced = next(r for r in caplog.records if r.getMessage() == "realtime_session_replaced")
    assert json.loads(replaced.fields["names"]) == ["SL / TP"]


async def test_replaced_connection_stops_the_read_retries(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(), LOOKUP_FAILED)
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(fill_event())
        await wait_until(lambda: "sl_tp_holdings_failed" in messages(caplog))
        connection.push(REPLACED)
        await wait_until(lambda: "realtime_session_replaced" in messages(caplog))
        await asyncio.sleep(0.1)
        calls = len(kiwoom.calls("kt00018"))
        await asyncio.sleep(0.1)
        assert len(kiwoom.calls("kt00018")) == calls
