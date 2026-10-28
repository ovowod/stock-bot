"""SL / TP 세션 전환과 설정 즉시 반영(국내 모의)."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from stock_bot.kiwoom import kiwoom as kiwoom_module
from stock_bot.sl_tp import watch as sl_tp_watch
from tests.fake_discord import FakeDiscord
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom, body_of
from tests.fake_realtime import OK_REG, FakeConnection, FakeRealtime, fill_event
from tests.sl_tp.helpers import (
    AT_10,
    Clock,
    Gate,
    balance,
    messages,
    nth_call_gate,
    position,
    quote,
    quote_regs,
    running,
    save,
    session,
    settle,
    sl_tp_kiwoom,
    watching,
    write_settings,
)
from tests.support import wait_until

pytestmark = pytest.mark.anyio

REJECTED_REG = {"trnm": "REG", "return_code": 1, "return_msg": "등록 실패"}


@pytest.fixture(autouse=True)
def fast_session_check(monkeypatch):
    monkeypatch.setattr(sl_tp_watch, "SESSION_CHECK_SECONDS", 0.01)


def latest(realtime: FakeRealtime) -> FakeConnection:
    return realtime.connections[-1]


def finished(caplog) -> list[dict[str, Any]]:
    return [r.fields for r in caplog.records if r.getMessage() == "sl_tp_auto_sell_finished"]


def runs_started(caplog) -> list[int]:
    return [r.fields["run"] for r in caplog.records if r.getMessage() == "sl_tp_session_started"]


async def test_session_start_subscribes_and_session_end_removes(data_dir, caplog):
    write_settings(data_dir, session(start="10:00", end="10:30"))
    clock = Clock(AT_10 - timedelta(seconds=1))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir, clock):
        await settle()
        assert realtime.connections == []
        assert kiwoom.calls("kt00018") == []
        clock.at = AT_10
        connection = await watching(realtime)
        clock.at = AT_10 + timedelta(minutes=30)
        await wait_until(lambda: bool(connection.requests("REMOVE")))
        # 구독이 하나도 남지 않으면 연결을 닫는다.
        await wait_until(lambda: connection.closed)

    # 시세(그룹 2)와 세션 동안 함께 받던 주문체결(그룹 1)을 모두 해지한다.
    assert {m["grp_no"] for m in connection.requests("REMOVE")} == {"1", "2"}
    assert "sl_tp_session_ended" in messages(caplog)


async def test_next_session_judges_again_with_its_own_criteria(data_dir, caplog):
    write_settings(
        data_dir,
        session(start="09:00", end="10:00", stop_loss="3"),
        session(start="10:00", end="11:00", stop_loss="5"),
    )
    clock = Clock(AT_10 - timedelta(minutes=30))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir, clock):
        connection = await watching(realtime)
        connection.push(quote(price="-58200"))
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)
        clock.at = AT_10
        # 새 세션은 보유종목을 다시 읽고 구독을 다시 맞춘다.
        await wait_until(lambda: len(quote_regs(connection)) == 2)
        connection.push(quote(price="-58200"))
        await settle()
        assert len(kiwoom.calls("kt10001")) == 1
        connection.push(quote(price="-57000"))
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 2)

    assert runs_started(caplog) == [1, 2]
    assert len(kiwoom.calls("kt00018")) == 4  # 세션 시작 2번 + 주문 직전 확인 2번


async def test_price_received_before_a_switch_is_not_judged_by_the_next_session(
    data_dir, caplog, monkeypatch
):
    write_settings(
        data_dir,
        session(start="09:00", end="10:00", take_profit="10", stop_loss=None),
        session(start="10:00", end="11:00", take_profit="3", stop_loss=None),
    )
    clock = Clock(AT_10 - timedelta(seconds=1))
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
    async with running(realtime, kiwoom, data_dir, clock):
        connection = await watching(realtime)
        connection.push(quote(price="+62400"))  # +4%, 09:59:59에 받았다.
        await held.wait()
        clock.at = AT_10 + timedelta(seconds=1)
        await wait_until(lambda: runs_started(caplog) == [1, 2])
        await wait_until(lambda: len(quote_regs(connection)) == 2)
        release.set()
        await settle()
        assert kiwoom.calls("kt10001") == []
        connection.push(quote(price="+62400"))  # 10:00:01에 받은 시세는 새 세션 기준이다.
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)


async def test_excluding_a_stock_unsubscribes_it_and_unexcluding_subscribes_again(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(position("005930"), position("000660")))
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        await save(app, session(), excluded=("000660",))
        await wait_until(lambda: len(quote_regs(connection)) == 2)
        assert quote_regs(connection)[1]["data"][0]["item"] == ["005930"]
        connection.push(quote("000660"))
        await settle()
        assert kiwoom.calls("kt10001") == []
        await save(app, session())
        await wait_until(lambda: len(quote_regs(connection)) == 3)
        assert quote_regs(connection)[2]["data"][0]["item"] == ["000660", "005930"]
        connection.push(quote("000660"))
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)

    # 같은 세션 안의 제외종목 변경은 보유종목을 다시 읽지 않는다.
    assert len(kiwoom.calls("kt00018")) == 2


async def test_changing_criteria_in_the_same_session_keeps_the_trigger_record(data_dir, caplog):
    write_settings(data_dir, session(stop_loss="3"))
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)
        await save(app, session(stop_loss="1"))
        connection.push(quote())
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1
    assert runs_started(caplog) == [1]


async def test_changing_only_excluded_stocks_keeps_the_trigger_record(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(position("005930"), position("000660")))
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote("005930"))
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)
        await save(app, session(), excluded=("000660",))
        await wait_until(lambda: len(quote_regs(connection)) == 2)
        connection.push(quote("005930"))
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1
    assert runs_started(caplog) == [1]


async def test_old_runs_queued_price_is_not_judged_after_switching_off_and_on(
    data_dir, caplog, monkeypatch
):
    # 체결 감시가 연결을 붙잡고 있어 껐다 켜도 같은 연결이다. 그 연결 번호만으로는 거를 수 없다.
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom().reply("ka10075", {"oso": []})
    held, release = asyncio.Event(), asyncio.Event()
    original = sl_tp_watch._Watch._on_event

    async def slow(self, event):
        if event.kind is sl_tp_watch.EventKind.REAL and not release.is_set():
            held.set()
            await release.wait()
        await original(self, event)

    monkeypatch.setattr(sl_tp_watch._Watch, "_on_event", slow)
    clock = Clock()
    async with running(realtime, kiwoom, data_dir, clock, discord=FakeDiscord()) as app:
        connection = await watching(realtime)
        connection.push(quote())  # 10:00:00에 받았다.
        await held.wait()
        clock.at = AT_10 + timedelta(seconds=1)
        await save(app, session(enabled=False))
        await save(app, session())
        # 새 실행이 보유종목을 다 읽어 판정할 수 있는 상태가 된 뒤에 밀린 시세를 처리한다.
        await wait_until(lambda: messages(caplog).count("sl_tp_holdings_refreshed") == 2)
        release.set()
        await settle()
        assert kiwoom.calls("kt10001") == []
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)

    assert len(realtime.connections) == 1


async def test_switching_off_removes_and_switching_on_starts_a_fresh_run(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        first = await watching(realtime)
        first.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)
        await save(app, session(enabled=False))
        await wait_until(lambda: bool(first.requests("REMOVE")))
        await save(app, session())
        await wait_until(
            lambda: len(realtime.connections) == 2 and bool(quote_regs(latest(realtime)))
        )
        latest(realtime).push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 2)

    assert runs_started(caplog) == [1, 2]


async def test_saving_does_not_cancel_an_order_already_sent(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)
        await save(app, session(enabled=False), excluded=("005930",))
        await settle()

    assert kiwoom.calls("kt10003") == []


def holdings_recheck_gate(kiwoom: FakeKiwoom) -> Gate:
    """트리거 뒤 주문 직전 잔고 재확인(두 번째 kt00018)만 붙잡는다."""
    return nth_call_gate(kiwoom, "kt00018", 2)


FIRST = session(start="09:00", end="10:01")
SECOND = session(start="10:01", end="11:00")
CHANGES = [
    ("exclude", "cancelled"),
    ("switch_off", "cancelled"),
    ("session_ends", "cancelled"),
    ("next_session", "cancelled"),
    ("criteria_only", "accepted"),
]


async def apply_change(app: FastAPI, clock: Clock, caplog, change: str) -> None:
    """트리거 뒤 주문이 대기하는 동안 일어나는 변경. 설정은 FIRST·SECOND로 시작한다."""
    if change == "exclude":
        await save(app, FIRST, SECOND, excluded=("005930",))
    elif change == "switch_off":
        await save(app, {**FIRST, "enabled": False}, SECOND)
    elif change == "session_ends":
        await save(app, FIRST)
        clock.at = AT_10 + timedelta(minutes=1)
        await wait_until(lambda: "sl_tp_session_ended" in messages(caplog))
    elif change == "next_session":
        clock.at = AT_10 + timedelta(minutes=1)
        await wait_until(lambda: runs_started(caplog) == [1, 2])
    else:
        await save(app, {**FIRST, "stop_loss": "1"}, SECOND)


@pytest.mark.parametrize(("change", "outcome"), CHANGES)
async def test_changes_during_the_recheck_decide_whether_the_order_goes(
    data_dir, caplog, change, outcome
):
    write_settings(data_dir, FIRST, SECOND)
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    gate = holdings_recheck_gate(kiwoom)
    async with running(realtime, gate, data_dir, clock) as app:
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        await apply_change(app, clock, caplog, change)
        gate.open.set()
        await wait_until(lambda: bool(finished(caplog)))

    assert finished(caplog)[0]["outcome"] == outcome
    assert len(kiwoom.calls("kt10001")) == (1 if outcome == "accepted" else 0)


@pytest.mark.parametrize(("change", "outcome"), CHANGES)
async def test_changes_while_waiting_for_the_sell_lock_decide_whether_the_order_goes(
    data_dir, caplog, change, outcome
):
    # 사용자가 직접 낸 같은 종목 매도가 매도 잠금을 잡고 있는 동안 트리거가 일어난다.
    write_settings(data_dir, FIRST, SECOND)
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    gate = nth_call_gate(kiwoom, "kt10001", 1)
    async with running(realtime, gate, data_dir, clock) as app:
        connection = await watching(realtime)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
            manual = asyncio.create_task(
                client.post(
                    "/api/environments/domestic_paper/orders",
                    json={
                        "order_key": "manual-1",
                        "side": "sell",
                        "code": "005930",
                        "order_type": "limit",
                        "quantity": "1",
                        "price": "61000",
                    },
                )
            )
            await gate.hit.wait()
            connection.push(quote())
            await wait_until(lambda: "auto_sell_requested" in messages(caplog))
            await apply_change(app, clock, caplog, change)
            gate.open.set()
            assert (await manual).status_code == 200
            await wait_until(lambda: bool(finished(caplog)))

    assert finished(caplog)[0]["outcome"] == outcome
    # 사용자가 낸 지정가 매도(trde_tp 0) 뒤에 자동 매도(시장가 3)가 나갔는지 본다.
    expected = ["0", "3"] if outcome == "accepted" else ["0"]
    assert [body_of(r)["trde_tp"] for r in kiwoom.calls("kt10001")] == expected


@pytest.mark.parametrize(("change", "outcome"), CHANGES)
async def test_changes_while_waiting_for_the_call_limit_decide_whether_the_order_goes(
    data_dir, caplog, monkeypatch, change, outcome
):
    write_settings(data_dir, FIRST, SECOND)
    clock = Clock()
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
    async with running(realtime, kiwoom, data_dir, clock) as app:
        connection = await watching(realtime)
        connection.push(quote())
        await waiting.wait()
        await apply_change(app, clock, caplog, change)
        release.set()
        await wait_until(lambda: bool(finished(caplog)))

    assert finished(caplog)[0]["outcome"] == outcome
    assert len(kiwoom.calls("kt10001")) == (1 if outcome == "accepted" else 0)


async def test_switching_off_and_on_during_the_recheck_drops_the_old_runs_order(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    gate = holdings_recheck_gate(kiwoom)
    async with running(realtime, gate, data_dir) as app:
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        # 시작·종료 시각이 같아도 껐다 켜면 새 실행이다.
        await save(app, session(enabled=False))
        await save(app, session())
        await wait_until(lambda: runs_started(caplog) == [1, 2])
        gate.open.set()
        await wait_until(lambda: bool(finished(caplog)))

    assert finished(caplog)[0]["outcome"] == "cancelled"
    assert kiwoom.calls("kt10001") == []


async def test_holdings_read_by_an_old_run_are_not_used(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    # 가짜 키움은 응답을 기록된 순서로 고른다. 붙잡아 둔 첫 실행의 조회는 두 번째로 기록되므로
    # 두 번째 응답(005930)을 받고, 둘째 실행의 조회가 첫 응답(000660)을 받는다.
    kiwoom = sl_tp_kiwoom(balance(position("000660")), balance(position("005930")))
    gate = nth_call_gate(kiwoom, "kt00018", 1)
    async with running(realtime, gate, data_dir) as app:
        await gate.hit.wait()
        await save(app, session(enabled=False))
        await save(app, session())
        connection = await watching(realtime)
        gate.open.set()
        await settle()

    # 첫 실행의 조회는 세션을 끌 때 멈췄고, 그 결과(005930)는 쓰지 않았다.
    assert [m["data"][0]["item"] for m in quote_regs(connection)] == [["000660"]]


async def test_unreadable_settings_answer_no_until_a_valid_save(data_dir, caplog):
    path = data_dir / "sl-tp" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        await settle()
        assert realtime.connections == []
        await save(app, session())
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)

    assert "sl_tp_watch_resumed" in messages(caplog)


def reject_first_quote_reg() -> FakeRealtime:
    rejected_once = [REJECTED_REG]

    def respond(message: dict[str, Any]) -> dict[str, Any]:
        # SL / TP 구독(grp_no 2)의 첫 REG만 거부한다.
        if message["grp_no"] == "2" and rejected_once:
            return rejected_once.pop()
        return OK_REG

    return FakeRealtime(responder=respond)


async def test_rejected_quote_subscription_pauses_only_sl_tp(data_dir, caplog):
    write_settings(data_dir)
    realtime = reject_first_quote_reg()
    discord = FakeDiscord()
    kiwoom = sl_tp_kiwoom(balance(position("005930"), position("000660"))).reply(
        "ka10075", {"oso": []}
    )
    async with running(realtime, kiwoom, data_dir, discord=discord) as app:
        connection = await watching(realtime)
        await wait_until(lambda: "sl_tp_subscription_rejected" in messages(caplog))
        connection.push(quote())
        connection.push(fill_event())
        # 구독 거부의 판정 중단 알림과 체결 알림.
        await discord.wait_for(2)
        await settle()
        assert kiwoom.calls("kt10001") == []
        # 구독 종목이 바뀔 때 다시 시도한다.
        await save(app, session(), excluded=("000660",))
        await wait_until(lambda: len(quote_regs(connection)) == 2)
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)

    assert quote_regs(connection)[1]["data"][0]["item"] == ["005930"]
    titles = [p["embeds"][0]["title"] for p in discord.payloads]
    assert titles.count("SL / TP 판정 중단") == 1
    assert "체결 · 매수" in titles


async def test_rejected_quote_subscription_is_tried_again_when_the_next_session_starts(
    data_dir, caplog
):
    write_settings(data_dir, FIRST, SECOND)
    clock = Clock()
    realtime = reject_first_quote_reg()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir, clock):
        connection = await watching(realtime)
        await wait_until(lambda: "sl_tp_subscription_rejected" in messages(caplog))
        clock.at = AT_10 + timedelta(minutes=1)
        await wait_until(lambda: len(quote_regs(connection)) == 2)
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)


async def test_saving_still_succeeds_when_applying_the_settings_fails(
    data_dir, caplog, monkeypatch
):
    def broken(self, spec, settings):
        raise RuntimeError("apply failed")

    monkeypatch.setattr(sl_tp_watch.SlTpWatcher, "settings_saved", broken)
    write_settings(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, sl_tp_kiwoom(), data_dir) as app:
        await save(app, session(stop_loss="1"))

    assert "sl_tp_settings_listener_failed" in messages(caplog)


async def test_the_same_session_on_the_next_day_is_a_new_run(data_dir, caplog):
    write_settings(data_dir)
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir, clock):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)
        clock.at = AT_10 + timedelta(days=1)
        await wait_until(lambda: runs_started(caplog) == [1, 2])
        await wait_until(lambda: len(quote_regs(connection)) == 2)
        connection.push(quote())
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 2)
