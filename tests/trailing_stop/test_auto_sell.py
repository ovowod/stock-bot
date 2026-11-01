"""Trailing Stop 자동 매도(모의): 트리거 때 키움 주문, Discord 알림, 상태 조회 API를 본다."""

from datetime import timedelta
from typing import Any

import httpx
import pytest

from tests.fake_discord import GRAY, RED, FakeDiscord, fields
from tests.fake_kiwoom import REAL_ENV, US_PAPER_ENV, body_of, kiwoom_error
from tests.fake_realtime import REPLACED, FakeRealtime, fill_event
from tests.log_capture import JsonLines
from tests.sl_tp.helpers import (
    AT_10,
    NY_10,
    SELL_REPLY,
    Clock,
    Gate,
    api,
    balance,
    messages,
    nth_call_gate,
    position,
    quote,
    running,
    session,
    settle,
    us_balance,
    us_position,
    us_quote,
)
from tests.support import wait_until
from tests.trailing_stop.helpers import (
    held,
    put_ts,
    read_record,
    rejecting,
    remembered,
    sell_fill,
    ts_kiwoom,
    ts_session,
    ts_us_kiwoom,
    until_status,
    utc,
    watching,
    write_record,
    write_ts,
)

pytestmark = pytest.mark.anyio

SELL_TITLES = (
    "Trailing Stop 자동 매도 접수",
    "Trailing Stop 자동 매도 실패",
    "Trailing Stop 자동 매도 결과 확인 불가",
)


def sell_alerts(discord: FakeDiscord) -> list[dict[str, Any]]:
    return [p for p in discord.payloads if p["embeds"][0].get("title") in SELL_TITLES]


def auto_sell_state(body: dict[str, Any]) -> str | None:
    auto_sell = body["stocks"][0]["auto_sell"] if body["stocks"] else None
    return None if auto_sell is None else auto_sell["state"]


async def test_trigger_sells_everything_at_market_and_announces_it(data_dir):
    write_ts(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir, discord=discord) as app:
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        connection.push(quote(price="-61000"))
        body = await until_status(app, lambda b: auto_sell_state(b) == "accepted")
        await wait_until(lambda: bool(sell_alerts(discord)))

    [order] = kiwoom.calls("kt10001")
    assert order.url.host == "mockapi.kiwoom.com"
    assert body_of(order) == {
        "dmst_stex_tp": "KRX",
        "stk_cd": "005930",
        "ord_qty": "3",
        "ord_uv": "",
        "trde_tp": "3",
        "cond_uv": "",
    }
    [item] = body["stocks"]
    assert item["status"] == "triggered"
    assert item["auto_sell"] == {
        "state": "accepted",
        "reason": None,
        "quantity": 3,
        "order_no": "0000138",
        "at": utc(AT_10),
    }
    [payload] = sell_alerts(discord)
    assert payload["content"] == "[국내 모의]"
    embed = payload["embeds"][0]
    assert embed["title"] == "Trailing Stop 자동 매도 접수"
    assert embed["color"] == RED
    assert fields(payload) == {
        "투자 환경": "국내 모의",
        "종목": "삼성전자 (005930)",
        "Trailing Stop 세션": "09:00–15:30 (한국시간)",
        "트리거 체결가": "61,000원",
        "고점": "63,000원",
        "고점 대비": "-3.17% · 기준 -3%",
        "활성화 수익률": "기준 +5%",
        "매입가": "60,000원",
        "수익률": "+1.67% · 수수료·세금 제외",
        "주문 수량": "3주",
        "주문번호": "0000138",
    }


def triggered(connection) -> None:
    """63,000원에 활성화하고, 고점 대비 -3.17%인 61,000원에 트리거한다."""
    connection.push(quote(price="+63000"))
    connection.push(quote(price="-61000"))


async def test_more_quotes_in_the_same_session_do_not_sell_again(data_dir):
    write_ts(data_dir)
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        triggered(connection)
        await until_status(app, lambda b: auto_sell_state(b) == "accepted")
        connection.push(quote(price="-59000"))
        connection.push(quote(price="+64000"))
        connection.push(quote(price="-60000"))
        await until_status(app, lambda b: b["stocks"][0]["last_price"] == "60000")
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1


@pytest.mark.parametrize(
    ("reply", "title", "reason", "description"),
    [
        (
            kiwoom_error(1, "주문가능수량 부족"),
            "Trailing Stop 자동 매도 실패",
            "rejected",
            "다음 Trailing Stop 세션에 다시 시도합니다.",
        ),
        (
            httpx.ConnectError("down"),
            "Trailing Stop 자동 매도 결과 확인 불가",
            "unknown",
            "접수 여부를 확인할 수 없습니다. 키움에서 주문 내역을 확인하세요.",
        ),
    ],
    ids=["rejected", "unknown"],
)
async def test_failed_sell_is_announced_and_shown_with_its_reason(
    data_dir, reply, title, reason, description
):
    write_ts(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = ts_kiwoom()
    if isinstance(reply, Exception):

        def down(request: httpx.Request) -> httpx.Response:
            raise reply

        kiwoom.respond("kt10001", down)
    else:
        kiwoom.reply("kt10001", reply)
    async with running(realtime, kiwoom, data_dir, discord=discord) as app:
        connection = await watching(realtime)
        triggered(connection)
        body = await until_status(app, lambda b: auto_sell_state(b) == "failed")
        await wait_until(lambda: bool(sell_alerts(discord)))

    auto_sell = body["stocks"][0]["auto_sell"]
    assert (auto_sell["reason"], auto_sell["order_no"]) == (reason, None)
    [payload] = sell_alerts(discord)
    embed = payload["embeds"][0]
    assert (embed["title"], embed["color"], embed["description"]) == (title, GRAY, description)
    assert fields(payload)["트리거 체결가"] == "61,000원"
    if reason == "rejected":
        assert fields(payload)["원인"].startswith("키움 오류 [1] 주문가능수량 부족")
    else:
        assert fields(payload)["주문 수량"] == "3주"


async def test_nothing_sellable_is_not_announced(data_dir, caplog):
    write_ts(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    # 주문 직전 잔고 재확인에서 매도 가능 수량이 0이다(이미 미체결 매도에 묶임).
    kiwoom = ts_kiwoom(
        balance(held()),
        balance(
            position(
                pur_pric="000000000060000",
                rmnd_qty="000000000000003",
                trde_able_qty="000000000000000",
            )
        ),
    )
    async with running(realtime, kiwoom, data_dir, discord=discord) as app:
        connection = await watching(realtime)
        triggered(connection)
        body = await until_status(app, lambda b: auto_sell_state(b) == "failed")
        await settle()

    assert body["stocks"][0]["auto_sell"]["reason"] == "no_sellable"
    assert kiwoom.calls("kt10001") == []
    assert sell_alerts(discord) == []
    assert "trailing_stop_auto_sell_finished" in messages(caplog)


async def test_sl_tp_sell_in_progress_on_the_same_stock_keeps_trailing_stop_from_selling(
    data_dir,
):
    sl_tp = {"updated_at": None, "excluded": [], "sessions": [session(take_profit="5")]}
    write_ts(data_dir, sl_tp=sl_tp)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = ts_kiwoom()
    gate = Gate(kiwoom, lambda r: api(r) == "kt10001")
    async with running(realtime, gate, data_dir, discord=discord) as app:
        connection = await watching(realtime)
        # 63,000원(+5%)에 SL / TP 익절 트리거가 매도를 내고, 그 주문 응답이 늦다.
        connection.push(quote(price="+63000"))
        await gate.hit.wait()
        connection.push(quote(price="-61000"))
        body = await until_status(app, lambda b: auto_sell_state(b) == "failed")
        gate.open.set()
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))
        await settle()

    assert body["stocks"][0]["auto_sell"]["reason"] == "in_progress"
    assert len(kiwoom.calls("kt10001")) == 1
    assert sell_alerts(discord) == []


async def test_us_paper_sells_on_the_exchange_from_the_listing(data_dir):
    write_ts(data_dir, ts_session(start="09:30", end="16:00"), environment="us_paper")
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = ts_us_kiwoom(us_balance(us_position()))
    clock = Clock(NY_10)
    async with running(realtime, kiwoom, data_dir, clock, US_PAPER_ENV, discord) as app:
        connection = await watching(realtime)
        connection.push(us_quote(price="+210.0000"))  # $200 기준 +5%
        connection.push(us_quote(price="-203.0000"))  # 고점 $210 대비 -3.33%
        await until_status(app, lambda b: auto_sell_state(b) == "accepted", "us_paper")
        await wait_until(lambda: bool(sell_alerts(discord)))

    [order] = kiwoom.calls("ust20001")
    assert order.url.host == "mockapi.kiwoom.com"
    assert body_of(order) == {
        "stex_tp": "ND",
        "stk_cd": "AAPL",
        "ord_qty": "3",
        "ord_uv": "",
        "trde_tp": "03",
        "stop_pric": "",
    }
    [payload] = sell_alerts(discord)
    assert payload["content"] == "[미국 모의]"
    assert fields(payload)["종목"] == "애플 (AAPL · NASDAQ)"
    assert fields(payload)["Trailing Stop 세션"] == "09:30–16:00 (뉴욕시간)"
    assert fields(payload)["트리거 체결가"] == "$203"
    assert fields(payload)["고점"] == "$210"
    assert fields(payload)["주문번호"] == "000000300"


async def test_auto_sell_fill_is_marked_and_logs_share_the_trigger_request_id(
    data_dir, json_logs: JsonLines
):
    write_ts(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = (
        ts_kiwoom()
        .reply("ka10075", {"oso": []})
        .reply("ka10077", {"tdy_rlzt_pl": "0", "tdy_rlzt_pl_dtl": []})
    )
    async with running(realtime, kiwoom, data_dir, discord=discord) as app:
        connection = await watching(realtime)
        triggered(connection)
        await until_status(app, lambda b: auto_sell_state(b) == "accepted")
        connection.push(
            fill_event(
                **{"9203": "0000138", "905": "-매도", "906": "시장가", "907": "1", "911": "3"}
            )
        )
        await wait_until(
            lambda: any(p["embeds"][0].get("title") == "체결 · 매도" for p in discord.payloads)
        )

    fill = next(p for p in discord.payloads if p["embeds"][0].get("title") == "체결 · 매도")
    assert fill["embeds"][0]["description"] == "Trailing Stop 자동 매도"
    lines = {line["event"]: line for line in json_logs.lines}
    request_id = lines["trailing_stop_triggered"]["request_id"]
    assert lines["trailing_stop_auto_sell_started"]["request_id"] == request_id
    assert lines["trailing_stop_auto_sell_finished"]["request_id"] == request_id
    assert lines["trailing_stop_auto_sell_finished"]["outcome"] == "accepted"
    assert lines["trailing_stop_auto_sell_finished"]["order_no"] == "0000138"
    assert lines["fill_notified"]["trigger_request_id"] == request_id


async def test_real_environment_never_sends_trailing_stop_orders(data_dir):
    write_ts(data_dir, environment="domestic_real")
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir, environ=REAL_ENV) as app:
        body = await until_status(app, lambda b: True, "domestic_real")
        for connection in realtime.connections:
            triggered(connection)
        await settle()

    assert body["not_watching_reason"] == "real"
    assert kiwoom.calls("kt10001") == []


TWO_SESSIONS = (
    ts_session(start="09:00", end="10:01"),
    ts_session(start="10:01", end="11:00"),
)


def sell_body(kiwoom) -> list[str]:
    """보낸 매도 주문의 수량."""
    return [body_of(r)["ord_qty"] for r in kiwoom.calls("kt10001")]


async def judging(caplog, times: int) -> None:
    """보유종목을 times번째로 다시 읽고 판정을 이어 갈 때까지 기다린다."""
    await wait_until(lambda: messages(caplog).count("trailing_stop_judging_resumed") >= times)


@pytest.mark.parametrize("next_run", ["next_session", "next_day", "restart"])
async def test_failed_sell_is_tried_once_more_in_the_next_run(data_dir, caplog, next_run):
    write_ts(data_dir, *TWO_SESSIONS)
    clock = Clock()
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = ts_kiwoom().reply("kt10001", kiwoom_error(1, "주문 거부"), SELL_REPLY)
    async with running(realtime, kiwoom, data_dir, clock, discord=discord) as app:
        connection = await watching(realtime)
        triggered(connection)
        await until_status(app, lambda b: auto_sell_state(b) == "failed")
        # 같은 실행에서는 다시 내지 않는다.
        connection.push(quote(price="-60000"))
        await until_status(app, lambda b: b["stocks"][0]["last_price"] == "60000")
        await settle()
        assert len(kiwoom.calls("kt10001")) == 1
        if next_run != "restart":
            clock.at = AT_10 + (
                timedelta(minutes=1) if next_run == "next_session" else timedelta(days=1)
            )
            await judging(caplog, 2)
            # 다음 실행의 첫 정규장 시세 체결이면 가격과 상관없이 판다.
            connection.push(quote(price="+70000"))
            body = await until_status(app, lambda b: auto_sell_state(b) == "accepted")
    if next_run == "restart":
        second = FakeRealtime()
        async with running(second, kiwoom, data_dir, clock, discord=discord) as app:
            connection = await watching(second)
            await until_status(app, lambda b: b["watching"])
            connection.push(quote(price="+70000"))
            body = await until_status(app, lambda b: auto_sell_state(b) == "accepted")
    await wait_until(lambda: len(sell_alerts(discord)) == 2)

    assert len(kiwoom.calls("kt10001")) == 2
    assert body["stocks"][0]["status"] == "triggered"
    failed, accepted = sell_alerts(discord)
    assert failed["embeds"][0]["title"] == "Trailing Stop 자동 매도 실패"
    assert "시도" not in fields(failed)
    assert accepted["embeds"][0]["title"] == "Trailing Stop 자동 매도 접수"
    # 다시 시도 알림의 트리거 칸은 기억한 트리거 가격과 고점이다.
    assert fields(accepted)["시도"] == "다시 시도"
    assert fields(accepted)["트리거 시각"] == "2026-10-26 10:00:00 (한국시간)"
    assert "트리거 시각" not in fields(failed)
    assert fields(accepted)["트리거 체결가"] == "61,000원"
    assert fields(accepted)["고점"] == "63,000원"
    started = [
        r.fields for r in caplog.records if r.getMessage() == "trailing_stop_auto_sell_started"
    ]
    assert [f["attempt"] for f in started] == ["trigger", "retry"]
    assert started[0] != started[1]


async def test_accepted_sell_that_filled_everything_is_not_tried_again(data_dir, caplog):
    write_ts(data_dir, *TWO_SESSIONS)
    clock = Clock()
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom()
    async with running(realtime, kiwoom, data_dir, clock) as app:
        connection = await watching(realtime)
        triggered(connection)
        await until_status(app, lambda b: auto_sell_state(b) == "accepted")
        connection.push(sell_fill(quantity="3"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "waiting")
        clock.at = AT_10 + timedelta(minutes=1)
        await judging(caplog, 3)
        connection.push(quote(price="+61000"))
        await until_status(app, lambda b: b["stocks"][0]["last_price"] == "61000")
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1


@pytest.mark.parametrize(
    ("sellable", "sent"),
    [("2", ["3", "2"]), ("0", ["3"])],
    ids=["partly-filled", "unfilled-order-still-open"],
)
async def test_what_is_left_after_an_accepted_sell_is_sold_in_the_next_run(
    data_dir, caplog, sellable, sent
):
    write_ts(data_dir, *TWO_SESSIONS)
    clock = Clock()
    realtime = FakeRealtime()
    left = position(
        pur_pric="000000000060000",
        # 미체결 매도에 묶였으면 보유 수량은 그대로 3주다.
        rmnd_qty=f"{int(sellable) or 3:015d}",
        trde_able_qty=f"{int(sellable):015d}",
    )
    # 1: 세션 시작, 2: 트리거 매도 직전, 3: 다음 세션 시작, 4: 다시 시도 직전
    kiwoom = ts_kiwoom(balance(held()), balance(held()), balance(left))
    async with running(realtime, kiwoom, data_dir, clock) as app:
        connection = await watching(realtime)
        triggered(connection)
        await until_status(app, lambda b: auto_sell_state(b) == "accepted")
        clock.at = AT_10 + timedelta(minutes=1)
        await judging(caplog, 2)
        connection.push(quote(price="+61000"))
        expected = "accepted" if sellable != "0" else "failed"
        body = await until_status(app, lambda b: auto_sell_state(b) == expected)

    assert sell_body(kiwoom) == sent
    if sellable == "0":
        # 미체결 매도에 묶여 매도 가능 수량이 0이면 보내지 않는다.
        assert body["stocks"][0]["auto_sell"]["reason"] == "no_sellable"


async def test_saving_new_criteria_in_the_same_session_does_not_try_again(data_dir):
    write_ts(data_dir)
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom().reply("kt10001", kiwoom_error(1, "주문 거부"), SELL_REPLY)
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        triggered(connection)
        await until_status(app, lambda b: auto_sell_state(b) == "failed")
        await put_ts(app, ts_session(activation_profit="4", drop_from_peak="2"))
        connection.push(quote(price="-60000"))
        body = await until_status(app, lambda b: b["stocks"][0]["last_price"] == "60000")
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1
    assert body["stocks"][0]["auto_sell"]["reason"] == "rejected"


@pytest.mark.parametrize("change", ["excluded", "session_changed", "forgotten", "stopped"])
async def test_sell_is_not_sent_when_things_change_before_sending(data_dir, caplog, change):
    write_ts(data_dir, ts_session(start="09:00", end="10:01"))
    clock = Clock()
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = ts_kiwoom()
    # 주문 직전 잔고 재확인(두 번째 잔고 조회)을 붙잡아 둔 사이에 상황이 바뀐다.
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir, clock, discord=discord) as app:
        connection = await watching(realtime)
        triggered(connection)
        await gate.hit.wait()
        if change == "excluded":
            await put_ts(app, ts_session(start="09:00", end="10:01"), excluded=("005930",))
        elif change == "session_changed":
            clock.at = AT_10 + timedelta(minutes=1)
            await until_status(app, lambda b: b["not_watching_reason"] == "no_session")
        elif change == "forgotten":
            connection.push(sell_fill(quantity="3"))
            await until_status(app, lambda b: b["stocks"][0]["status"] == "waiting")
        else:
            connection.push(REPLACED)
            await until_status(app, lambda b: b["not_watching_reason"] == "connection_replaced")
        gate.open.set()
        await wait_until(lambda: "trailing_stop_auto_sell_finished" in messages(caplog))
        await settle()

    assert kiwoom.calls("kt10001") == []
    assert sell_alerts(discord) == []
    finished = next(
        r.fields for r in caplog.records if r.getMessage() == "trailing_stop_auto_sell_finished"
    )
    assert finished["outcome"] == "cancelled"
    refused = [r.fields for r in caplog.records if r.getMessage() == "trailing_stop_send_refused"]
    expected = {"excluded": "excluded", "forgotten": "forgotten"}.get(change, change)
    assert refused[0]["reason"] == expected


async def test_late_result_of_the_previous_run_does_not_change_the_current_run(data_dir, caplog):
    write_ts(data_dir, *TWO_SESSIONS)
    clock = Clock()
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = ts_kiwoom()
    gate = Gate(kiwoom, lambda r: api(r) == "kt10001")
    async with running(realtime, gate, data_dir, clock, discord=discord) as app:
        connection = await watching(realtime)
        triggered(connection)
        await gate.hit.wait()
        clock.at = AT_10 + timedelta(minutes=1)
        await judging(caplog, 2)
        gate.open.set()
        await wait_until(lambda: bool(sell_alerts(discord)))
        body = await until_status(app, lambda b: b["watching"])

    # 앞 실행의 접수는 알리지만, 새 실행에서는 아직 시도하지 않은 것으로 보인다.
    assert sell_alerts(discord)[0]["embeds"][0]["title"] == "Trailing Stop 자동 매도 접수"
    assert body["stocks"][0]["auto_sell"] is None


def forgotten(caplog) -> list[dict[str, Any]]:
    return [r.fields for r in caplog.records if r.getMessage() == "trailing_stop_memory_forgotten"]


@pytest.mark.parametrize(("purchase", "kept"), [("60000", True), ("57000", False)])
async def test_purchase_price_changed_while_the_server_was_off_forgets_and_does_not_sell(
    data_dir, caplog, purchase, kept
):
    """서버가 꺼진 사이 팔고 57,000원에 다시 샀다. 예전 고점 63,000원으로 팔지 않는다."""
    write_ts(data_dir)
    write_record(data_dir, {"005930": remembered(AT_10, "63000", AT_10)})
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom(balance(held(price=purchase)))
    async with running(realtime, kiwoom, data_dir) as app:
        connection = await watching(realtime)
        await until_status(app, lambda b: b["watching"])
        connection.push(quote(price="-58000"))  # 고점 대비 -7.9%, 57,000원 대비 +1.75%
        body = await until_status(app, lambda b: b["stocks"][0]["last_price"] == "58000")
        await settle()

    if kept:
        assert body["stocks"][0]["status"] == "triggered"
        assert len(kiwoom.calls("kt10001")) == 1
        assert forgotten(caplog) == []
    else:
        assert body["stocks"][0]["status"] == "waiting"
        assert kiwoom.calls("kt10001") == []
        [log] = forgotten(caplog)
        assert (log["code"], log["reason"]) == ("005930", "purchase_price_changed")
        assert (log["previous_purchase_price"], log["purchase_price"]) == ("60000", "57000")
        assert read_record(data_dir) == {"version": 2, "stocks": {}}


@pytest.mark.parametrize("fill_at", ["before_the_gap", "after_the_gap"])
async def test_fills_around_a_reconnect_do_not_explain_a_changed_purchase_price(
    data_dir, caplog, fill_at
):
    write_ts(data_dir)
    realtime = FakeRealtime()
    kiwoom = ts_kiwoom(balance(held()), balance(held(price="57000")))
    # 두 번째 잔고 조회를 붙잡아 둔 사이 연결이 끊겨, 그 결과는 버려진다.
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir) as app:
        first = await watching(realtime)
        first.push(quote(price="+63000"))
        await until_status(app, lambda b: b["stocks"][0]["status"] == "tracking")
        if fill_at == "before_the_gap":
            first.push(fill_event())  # 매수 체결을 받고 다시 읽는 중에 끊긴다.
        first.drop()
        await wait_until(lambda: len(realtime.connections) == 2)
        second = realtime.connections[1]
        if fill_at == "after_the_gap":
            # 다시 연결된 뒤 첫 비교 전에 매수 체결을 받는다.
            await gate.hit.wait()
            second.push(fill_event())
        gate.open.set()
        await until_status(app, lambda b: b["watching"] and b["stocks"][0]["status"] == "waiting")
        second.push(quote(price="-58000"))
        await until_status(app, lambda b: b["stocks"][0]["last_price"] == "58000")
        await settle()

    assert kiwoom.calls("kt10001") == []
    assert [f["reason"] for f in forgotten(caplog)] == ["purchase_price_changed"]


async def test_fills_rejected_while_the_purchase_price_changed_forget_the_memory(data_dir, caplog):
    write_ts(data_dir)
    write_record(data_dir, {"005930": remembered(AT_10, "63000", AT_10)})
    flag = {"on": True}
    realtime = rejecting("1", flag)
    kiwoom = ts_kiwoom(balance(held(price="57000")))
    async with running(realtime, kiwoom, data_dir) as app:
        await until_status(app, lambda b: b["not_watching_reason"] == "fills_rejected")
        flag["on"] = False
        body = await until_status(app, lambda b: b["watching"])

    assert body["stocks"][0]["status"] == "waiting"
    assert [f["reason"] for f in forgotten(caplog)] == ["purchase_price_changed"]


@pytest.mark.parametrize("bought", [False, True], ids=["unexplained", "after-a-buy-fill"])
async def test_purchase_price_rechecked_right_before_sending(data_dir, caplog, bought):
    write_ts(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    # 주문 직전 잔고 재확인에서 평균 매입가가 57,000원이다.
    kiwoom = ts_kiwoom(balance(held()), balance(held(price="57000")))
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir, discord=discord):
        connection = await watching(realtime)
        triggered(connection)
        await gate.hit.wait()
        if bought:
            connection.push(fill_event())  # 공백 없이 받은 매수 체결로 설명된다.
        gate.open.set()
        await wait_until(lambda: "trailing_stop_auto_sell_finished" in messages(caplog))
        await settle()

    finished = next(
        r.fields for r in caplog.records if r.getMessage() == "trailing_stop_auto_sell_finished"
    )
    if bought:
        assert finished["outcome"] == "accepted"
        assert len(kiwoom.calls("kt10001")) == 1
        assert forgotten(caplog) == []
    else:
        assert finished["outcome"] == "cancelled"
        assert kiwoom.calls("kt10001") == []
        assert sell_alerts(discord) == []
        assert [f["reason"] for f in forgotten(caplog)] == ["purchase_price_changed"]
        refused = [
            r.fields for r in caplog.records if r.getMessage() == "trailing_stop_send_refused"
        ]
        assert refused[0]["reason"] == "purchase_price_changed"
