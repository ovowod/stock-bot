"""SL / TP Discord 알림: 자동 매도 접수·실패·결과 불명, 체결 알림 표시, 판정 중단, 밀려남."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest

from stock_bot.fill import feed as fill_feed
from stock_bot.kiwoom import realtime as realtime_module
from stock_bot.order import auto_sell
from stock_bot.sl_tp import watch as sl_tp_watch
from tests.fake_kiwoom import kiwoom_error, page_response
from tests.fake_realtime import OK_REG, FakeRealtime, fill_event
from tests.fill.test_fill_notifications import (
    BLUE,
    GRAY,
    RED,
    REPLACED,
    US_PAPER_ENV,
    FakeDiscord,
    fields,
    wait_until,
)
from tests.log_capture import JsonLines
from tests.sl_tp.test_sl_tp_auto_sell import (
    AT_10,
    Clock,
    balance,
    messages,
    position,
    quote,
    running,
    session,
    settle,
    sl_tp_kiwoom,
    watching,
    write_settings,
)
from tests.sl_tp.test_sl_tp_sessions import nth_call_gate, save
from tests.sl_tp.test_sl_tp_us import NY_10, us_kiwoom, us_quote, watching_us, write_us_settings

pytestmark = pytest.mark.anyio

AUTO_SELL_TITLES = (
    "SL / TP 자동 매도 접수",
    "SL / TP 자동 매도 실패",
    "SL / TP 자동 매도 결과 확인 불가",
)


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    monkeypatch.setattr(sl_tp_watch, "SESSION_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(sl_tp_watch, "HOLDINGS_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(sl_tp_watch, "HOLDINGS_RETRY_MAX_SECONDS", 0.02)
    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)


def notify_kiwoom(*balances: dict[str, Any]) -> Any:
    """자동 매도와 체결 알림이 부르는 TR. 체결 알림은 미체결·당일 실현손익을 조회한다."""
    return (
        sl_tp_kiwoom(*balances)
        .reply("ka10075", {"oso": []})
        .reply("ka10077", {"tdy_rlzt_pl": "0", "tdy_rlzt_pl_dtl": []})
    )


def titled(discord: FakeDiscord, title: str) -> list[dict[str, Any]]:
    return [p for p in discord.payloads if p["embeds"][0].get("title") == title]


def auto_sell_alerts(discord: FakeDiscord) -> list[dict[str, Any]]:
    return [p for p in discord.payloads if p["embeds"][0].get("title") in AUTO_SELL_TITLES]


def auto_sell_fill(**overrides: str) -> dict[str, Any]:
    """자동 매도 주문(0000138) 3주 시장가 매도의 체결."""
    values = {
        "9203": "0000138",
        "905": "-매도",
        "906": "시장가",
        "907": "1",
        "900": "3",
        "910": "-58200",
        "911": "3",
        "902": "0",
    }
    return fill_event(**{**values, **overrides})


async def test_accepted_stop_loss_alert_lists_why_it_sold(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()
    async with running(realtime, kiwoom, data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote(price="-58200"))
        await wait_until(lambda: bool(auto_sell_alerts(discord)))

    [payload] = auto_sell_alerts(discord)
    assert payload["content"] == "[국내 모의]"
    embed = payload["embeds"][0]
    assert embed["title"] == "SL / TP 자동 매도 접수"
    assert embed["color"] == BLUE
    assert fields(payload) == {
        "투자 환경": "국내 모의",
        "종목": "삼성전자 (005930)",
        "SL/TP 세션": "09:00–15:30 (한국시간)",
        "트리거": "손절 트리거 · 기준 -3%",
        "트리거 체결가": "58,200원",
        "매입가": "60,000원",
        "수익률": "-3.00% · 수수료·세금 제외",
        "주문 수량": "3주",
        "주문번호": "0000138",
    }


async def test_accepted_take_profit_alert_is_red(data_dir):
    write_settings(data_dir, session(take_profit="5", stop_loss=None))
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, notify_kiwoom(), data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote(price="+63000"))
        await wait_until(lambda: bool(auto_sell_alerts(discord)))

    [payload] = auto_sell_alerts(discord)
    assert payload["embeds"][0]["color"] == RED
    assert fields(payload)["트리거"] == "익절 트리거 · 기준 +5%"
    assert fields(payload)["수익률"] == "+5.00% · 수수료·세금 제외"


async def test_us_accepted_alert_shows_exchange_dollars_and_new_york_time(data_dir):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = us_kiwoom().reply("ust21050", {"result_list": []})
    async with running(
        realtime,
        kiwoom,
        data_dir,
        Clock(NY_10),
        environ=US_PAPER_ENV,
        discord=discord,
    ):
        connection = await watching_us(realtime)
        connection.push(us_quote(price="-194.0000"))
        await wait_until(lambda: bool(auto_sell_alerts(discord)))

    [payload] = auto_sell_alerts(discord)
    assert payload["content"] == "[미국 모의]"
    assert fields(payload)["종목"] == "애플 (AAPL · NASDAQ)"
    assert fields(payload)["SL/TP 세션"] == "09:30–16:00 (뉴욕시간)"
    assert fields(payload)["트리거 체결가"] == "$194"
    assert fields(payload)["매입가"] == "$200"
    assert fields(payload)["주문번호"] == "000000300"


@pytest.mark.parametrize(
    ("reply", "cause"),
    [
        (kiwoom_error(1, "주문가능수량 부족"), "키움 오류 [1] 주문가능수량 부족"),
        ("holdings", "잔고를 확인하지 못해 주문하지 않았습니다."),
    ],
    ids=["kiwoom-rejected", "recheck-failed"],
)
async def test_failed_alert_gives_the_cause_and_says_no_retry(data_dir, reply, cause):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    if reply == "holdings":
        kiwoom = notify_kiwoom(balance(), kiwoom_error(1, "잔고 조회 오류"))
    else:
        kiwoom = notify_kiwoom().reply("kt10001", reply)
    async with running(realtime, kiwoom, data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(auto_sell_alerts(discord)))

    [payload] = auto_sell_alerts(discord)
    embed = payload["embeds"][0]
    assert embed["title"] == "SL / TP 자동 매도 실패"
    assert embed["color"] == GRAY
    assert embed["description"] == "이번 SL/TP 세션 동안 이 종목은 다시 시도하지 않습니다."
    assert fields(payload)["원인"].startswith(cause)
    assert fields(payload)["종목"] == "삼성전자 (005930)"


async def test_unknown_result_alert_says_to_check_kiwoom(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    kiwoom.respond("kt10001", down)
    async with running(realtime, kiwoom, data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(auto_sell_alerts(discord)))

    [payload] = auto_sell_alerts(discord)
    embed = payload["embeds"][0]
    assert embed["title"] == "SL / TP 자동 매도 결과 확인 불가"
    assert embed["color"] == GRAY
    assert (
        embed["description"] == "접수 여부를 확인할 수 없습니다. 키움에서 주문 내역을 확인하세요."
    )
    assert fields(payload)["주문 수량"] == "3주"


async def test_skipped_and_cancelled_sells_are_not_announced(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    # 주문 직전 잔고 재확인에서 매도 가능 수량이 0이다.
    kiwoom = notify_kiwoom(balance(), balance(position(trde_able_qty="000000000000000")))
    async with running(realtime, kiwoom, data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: "sl_tp_auto_sell_finished" in messages(caplog))
        await settle()

    assert auto_sell_alerts(discord) == []


async def test_auto_sell_fill_is_marked_and_manual_fill_is_not(data_dir, json_logs: JsonLines):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, notify_kiwoom(), data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(auto_sell_alerts(discord)))
        # 자동 매도 주문이 두 번에 나눠 체결되고, 사용자가 낸 다른 매도도 체결된다.
        connection.push(auto_sell_fill(**{"911": "1", "902": "2"}))
        connection.push(auto_sell_fill(**{"911": "2", "902": "0"}))
        connection.push(auto_sell_fill(**{"9203": "0000200", "906": "보통"}))
        await wait_until(lambda: len(titled(discord, "체결 · 매도")) == 3)

    first, second, manual = titled(discord, "체결 · 매도")
    assert first["embeds"][0]["description"] == "SL / TP 자동 매도"
    assert second["embeds"][0]["description"] == "SL / TP 자동 매도"
    assert "description" not in manual["embeds"][0]
    triggered = next(line for line in json_logs.lines if line["event"] == "sl_tp_triggered")
    notified = [line for line in json_logs.lines if line["event"] == "fill_notified"]
    # 부분 체결 모두에 트리거 요청 ID가 남고, 체결 처리는 체결마다 자기 요청 ID를 쓴다.
    assert [n["trigger_request_id"] for n in notified] == [
        triggered["request_id"],
        triggered["request_id"],
        None,
    ]
    assert len({n["request_id"] for n in notified}) == 3


async def test_fill_before_the_order_reply_is_marked_when_accepted_in_time(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()
    gate = nth_call_gate(kiwoom, "kt10001", 1)
    async with running(realtime, gate, data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        # 시장가는 주문 응답보다 체결 통보가 먼저 올 수 있다.
        connection.push(auto_sell_fill())
        await asyncio.sleep(0.1)
        gate.open.set()
        await wait_until(lambda: bool(titled(discord, "체결 · 매도")))

    [payload] = titled(discord, "체결 · 매도")
    assert payload["embeds"][0]["description"] == "SL / TP 자동 매도"


async def test_fill_is_sent_without_the_mark_when_the_reply_is_too_late(
    data_dir, caplog, monkeypatch
):
    monkeypatch.setattr(auto_sell, "RESULT_WAIT_SECONDS", 0.1)
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()
    gate = nth_call_gate(kiwoom, "kt10001", 1)
    async with running(realtime, gate, data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        connection.push(auto_sell_fill())
        await wait_until(lambda: bool(titled(discord, "체결 · 매도")))
        gate.open.set()
        await wait_until(lambda: bool(auto_sell_alerts(discord)))

    [payload] = titled(discord, "체결 · 매도")
    assert "description" not in payload["embeds"][0]
    assert "fill_auto_sell_wait_timeout" in messages(caplog)


def _down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down")


@pytest.mark.parametrize(
    "reply",
    [kiwoom_error(1, "주문 거부"), "unknown", {"ord_no": "0000999"}],
    ids=["rejected", "unknown", "other-order-number"],
)
async def test_fill_is_not_marked_when_the_auto_sell_was_not_that_order(data_dir, reply):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()
    if reply == "unknown":
        kiwoom.respond("kt10001", _down)
    else:
        kiwoom.reply("kt10001", reply)
    gate = nth_call_gate(kiwoom, "kt10001", 1)
    async with running(realtime, gate, data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        connection.push(auto_sell_fill())
        await asyncio.sleep(0.05)
        gate.open.set()
        await wait_until(lambda: bool(titled(discord, "체결 · 매도")))

    [payload] = titled(discord, "체결 · 매도")
    assert "description" not in payload["embeds"][0]


async def test_auto_sell_alert_logs_share_the_trigger_request_id(data_dir, json_logs: JsonLines):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, notify_kiwoom(), data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: any(line["event"] == "sl_tp_notified" for line in json_logs.lines))

    lines = {line["event"]: line for line in json_logs.lines}
    assert lines["sl_tp_notified"]["request_id"] == lines["sl_tp_triggered"]["request_id"]
    assert lines["sl_tp_notified"]["sent"] is True
    discord_requests = [
        line
        for line in json_logs.lines
        if line["event"] == "discord_request"
        and line["request_id"] == lines["sl_tp_triggered"]["request_id"]
    ]
    assert len(discord_requests) == 1


async def test_holdings_failing_for_five_minutes_is_announced_once(data_dir, caplog):
    write_settings(data_dir)
    clock = Clock()
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom(balance(), kiwoom_error(1, "잔고 조회 오류"))
    async with running(realtime, kiwoom, data_dir, clock, discord=discord):
        connection = await watching(realtime)
        connection.push(fill_event(**{"905": "+매수", "907": "2"}))
        await wait_until(lambda: "sl_tp_holdings_failed" in messages(caplog))
        failures = messages(caplog).count("sl_tp_holdings_failed")
        clock.at = AT_10 + timedelta(minutes=4, seconds=59)
        await wait_until(lambda: messages(caplog).count("sl_tp_holdings_failed") >= failures + 2)
        assert titled(discord, "SL / TP 판정 중단") == []
        clock.at = AT_10 + timedelta(minutes=5)
        await wait_until(lambda: bool(titled(discord, "SL / TP 판정 중단")))
        failures = messages(caplog).count("sl_tp_holdings_failed")
        await wait_until(lambda: messages(caplog).count("sl_tp_holdings_failed") >= failures + 3)

    [payload] = titled(discord, "SL / TP 판정 중단")
    assert payload["content"] == "[국내 모의]"
    assert payload["embeds"][0]["color"] == GRAY
    assert "5분" in payload["embeds"][0]["description"]
    assert fields(payload)["첫 실패"] == "2026-10-26 10:00:00 (한국시간)"
    assert fields(payload)["원인"].startswith("키움 오류")


async def test_holdings_failure_can_be_announced_again_after_a_success(data_dir, caplog):
    write_settings(data_dir)
    clock = Clock()
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()
    failing = {"now": False}

    def holdings(request: httpx.Request) -> httpx.Response:
        if failing["now"]:
            return kiwoom_error(1, "잔고 조회 오류")
        return page_response(balance())

    kiwoom.respond("kt00018", holdings)

    def stops() -> int:
        return len(titled(discord, "SL / TP 판정 중단"))

    async with running(realtime, kiwoom, data_dir, clock, discord=discord):
        connection = await watching(realtime)
        for round_no in (1, 2):
            failures = messages(caplog).count("sl_tp_holdings_failed")
            failing["now"] = True
            connection.push(fill_event(**{"905": "+매수", "907": "2"}))
            await wait_until(lambda f=failures: messages(caplog).count("sl_tp_holdings_failed") > f)
            clock.at += timedelta(minutes=5)
            await wait_until(lambda n=round_no: stops() == n)
            # 한 번이라도 성공하면 다시 알릴 수 있는 상태로 돌아간다.
            failing["now"] = False
            await wait_until(
                lambda n=round_no: messages(caplog).count("sl_tp_holdings_recovered") == n
            )

    assert stops() == 2


async def test_rejected_quote_subscription_is_announced_once(data_dir):
    write_settings(data_dir)

    def respond(message: dict[str, Any]) -> dict[str, Any]:
        if message["grp_no"] == "2":
            return {"trnm": "REG", "return_code": 1, "return_msg": "등록 한도 초과"}
        return OK_REG

    realtime = FakeRealtime(responder=respond)
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()
    async with running(realtime, kiwoom, data_dir, discord=discord) as app:
        await wait_until(lambda: bool(titled(discord, "SL / TP 판정 중단")))
        # 다시 시도해도 거부가 이어지는 동안은 다시 알리지 않는다.
        await save(app, session(), excluded=("000660",))
        await settle()

    [payload] = titled(discord, "SL / TP 판정 중단")
    assert "실시간 시세 구독이 거부" in payload["embeds"][0]["description"]
    assert fields(payload)["원인"] == "등록 한도 초과"


async def test_rejected_fill_registration_is_announced_once_while_retrying(
    data_dir, monkeypatch, caplog
):
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_MAX_SECONDS", 0.02)
    write_settings(data_dir)

    def respond(message: dict[str, Any]) -> dict[str, Any]:
        if message["grp_no"] == "1":
            return {"trnm": "REG", "return_code": 1, "return_msg": "등록 한도 초과"}
        return OK_REG

    realtime = FakeRealtime(responder=respond)
    discord = FakeDiscord()
    async with running(realtime, notify_kiwoom(), data_dir, discord=discord):
        await wait_until(lambda: bool(titled(discord, "SL / TP 판정 중단")))
        # 체결 feed가 다시 시도해 거부가 이어져도 다시 알리지 않는다.
        await wait_until(lambda: messages(caplog).count("fill_feed_register_retry") >= 4)
        await settle()

    [payload] = titled(discord, "SL / TP 판정 중단")
    description = payload["embeds"][0]["description"]
    assert "주문체결 실시간 등록이 거부" in description
    assert "자동으로 다시 시도합니다" in description
    assert fields(payload)["원인"] == "등록 한도 초과"


async def test_unreadable_settings_file_is_announced_once(data_dir):
    path = data_dir / "sl-tp" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, notify_kiwoom(), data_dir, discord=discord):
        await wait_until(lambda: bool(titled(discord, "SL / TP 판정 중단")))
        await settle()

    [payload] = titled(discord, "SL / TP 판정 중단")
    assert payload["content"] == "[국내 모의]"
    assert "설정 파일을 읽지 못해" in payload["embeds"][0]["description"]


async def test_replaced_connection_names_every_stopped_feature_once(data_dir):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, notify_kiwoom(), data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(REPLACED)
        await wait_until(lambda: bool(titled(discord, "체결 감시·SL / TP 중단")))
        await settle()

    [payload] = titled(discord, "체결 감시·SL / TP 중단")
    assert payload["content"] == "[국내 모의]"
    assert "체결 감시·SL / TP를 멈춥니다" in payload["embeds"][0]["description"]


async def test_discord_off_still_sells_and_only_skips_the_alert(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = notify_kiwoom()
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: "sl_tp_notified" in messages(caplog))

    assert len(kiwoom.calls("kt10001")) == 1
    notified = next(r for r in caplog.records if r.getMessage() == "sl_tp_notified")
    assert notified.fields["sent"] is False
    assert "discord_request" not in messages(caplog)


async def test_cancelled_sell_is_not_announced(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir, discord=discord) as app:
        connection = await watching(realtime)
        connection.push(quote())
        await gate.hit.wait()
        await save(app, session(), excluded=("005930",))
        gate.open.set()
        await wait_until(lambda: "sl_tp_auto_sell_finished" in messages(caplog))
        await settle()

    finished = next(r for r in caplog.records if r.getMessage() == "sl_tp_auto_sell_finished")
    assert finished.fields["outcome"] == "cancelled"
    assert auto_sell_alerts(discord) == []


async def test_unexpected_error_while_selling_is_announced_as_unknown(data_dir, monkeypatch):
    from stock_bot.order.order import OrderService

    async def broken(self, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(OrderService, "sell_all_at_market", broken)
    write_settings(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, notify_kiwoom(), data_dir, discord=discord):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(auto_sell_alerts(discord)))

    [payload] = auto_sell_alerts(discord)
    assert payload["embeds"][0]["title"] == "SL / TP 자동 매도 결과 확인 불가"


async def test_holdings_failure_alert_is_rearmed_by_the_next_session(data_dir, caplog):
    write_settings(
        data_dir,
        session(start="09:00", end="10:30"),
        session(start="10:30", end="11:30"),
    )
    clock = Clock()
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = notify_kiwoom()
    failing = {"now": False}

    def holdings(request: httpx.Request) -> httpx.Response:
        if failing["now"]:
            return kiwoom_error(1, "잔고 조회 오류")
        return page_response(balance())

    kiwoom.respond("kt00018", holdings)
    async with running(realtime, kiwoom, data_dir, clock, discord=discord):
        connection = await watching(realtime)
        failing["now"] = True
        connection.push(fill_event(**{"905": "+매수", "907": "2"}))
        await wait_until(lambda: "sl_tp_holdings_failed" in messages(caplog))
        clock.at = AT_10 + timedelta(minutes=5)
        await wait_until(lambda: len(titled(discord, "SL / TP 판정 중단")) == 1)
        # 실패가 이어진 채 다음 세션이 시작되면 실패 기록이 지워지고, 5분이 지나면 다시 알린다.
        clock.at = AT_10 + timedelta(minutes=30)
        await wait_until(lambda: "sl_tp_holdings_retry_stopped" in messages(caplog))
        clock.at = AT_10 + timedelta(minutes=35)
        await wait_until(lambda: len(titled(discord, "SL / TP 판정 중단")) == 2)


async def test_rejected_subscription_is_announced_again_in_the_next_session(data_dir):
    write_settings(
        data_dir,
        session(start="09:00", end="10:01"),
        session(start="10:01", end="11:00"),
    )

    def respond(message: dict[str, Any]) -> dict[str, Any]:
        if message["grp_no"] == "2":
            return {"trnm": "REG", "return_code": 1, "return_msg": "등록 한도 초과"}
        return OK_REG

    clock = Clock()
    realtime = FakeRealtime(responder=respond)
    discord = FakeDiscord()
    async with running(realtime, notify_kiwoom(), data_dir, clock, discord=discord):
        await wait_until(lambda: len(titled(discord, "SL / TP 판정 중단")) == 1)
        clock.at = AT_10 + timedelta(minutes=1)
        await wait_until(lambda: len(titled(discord, "SL / TP 판정 중단")) == 2)
