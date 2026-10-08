import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from stock_bot import fill_watch
from stock_bot.app import create_app
from stock_bot.notification import DiscordNotifier
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom, body_of, page_response
from tests.fake_realtime import (
    OK_LOGIN,
    OK_REG,
    FakeRealtime,
    fill_event,
    open_order,
    realized_row,
    us_fill_event,
    us_open_order,
)

pytestmark = pytest.mark.anyio

# 기본은 국내 모의 인증정보만 둔다. 다른 투자 환경 연결은 그 환경을 다루는 테스트에서 연다.
DOMESTIC_PAPER_ENV = {k: v for k, v in FAKE_ENV.items() if not k.startswith(("PAPER_US_", "REAL_"))}
US_PAPER_ENV = {k: v for k, v in FAKE_ENV.items() if not k.startswith(("PAPER_KR_", "REAL_"))}
DISCORD_ENV = {"DISCORD_BOT_TOKEN": "discord-token-ZZZZ9999", "DISCORD_CHANNEL_ID": "123456789"}
PAPER_REALTIME = "wss://mockapi.kiwoom.com:10000/api/websocket"
RED = 0xE42939
BLUE = 0x2F6FEB
GRAY = 0x8B8F98


def fields(payload: dict) -> dict[str, str]:
    return {field["name"]: field["value"] for field in payload["embeds"][0]["fields"]}


class FakeDiscord:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(request.content))
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(200, json={"id": "1"})

    async def wait_for(self, count: int) -> None:
        for _ in range(200):
            if len(self.payloads) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Discord 요청 {count}개를 기다렸지만 {len(self.payloads)}개")


async def wait_until(condition: Callable[[], bool]) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("조건을 기다렸지만 만족하지 않았습니다.")


def fill_kiwoom(*open_order_pages: dict | httpx.Response) -> FakeKiwoom:
    """체결 알림이 부르는 조회 TR의 응답. 기본은 미체결 주문과 당일 실현손익이 없다."""
    return (
        FakeKiwoom()
        .reply("ka10075", *(open_order_pages or ({"oso": []},)))
        .reply("ka10077", {"tdy_rlzt_pl": "0", "tdy_rlzt_pl_dtl": []})
        .reply("ust21050", {"result_list": []})
    )


@asynccontextmanager
async def running(
    realtime: FakeRealtime,
    discord: FakeDiscord | None = None,
    kiwoom: FakeKiwoom | None = None,
    environ: dict[str, str] = DOMESTIC_PAPER_ENV,
    discord_env: dict[str, str] = DISCORD_ENV,
) -> AsyncIterator[FastAPI]:
    """앱 수명(lifespan)을 실행한 채로 둔다. 체결 감시는 앱이 시작할 때 뜬다."""
    app = create_app(
        environ=environ,
        transport=httpx.MockTransport(kiwoom or fill_kiwoom()),
        static_dir=None,
        log_dir=None,
        notifier=DiscordNotifier(
            discord_env, transport=httpx.MockTransport(discord or FakeDiscord())
        ),
        realtime_connect=realtime,
    )
    async with app.router.lifespan_context(app):
        yield app


async def test_connects_to_paper_realtime_logs_in_then_registers_order_fills():
    realtime = FakeRealtime()
    kiwoom = FakeKiwoom()
    async with running(realtime, kiwoom=kiwoom):
        [connection] = await realtime.wait_registered()

    assert connection.url == PAPER_REALTIME
    assert connection.sent == [
        {"trnm": "LOGIN", "token": kiwoom.issued_tokens[0]},
        {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [{"item": [""], "type": ["00"]}],
        },
    ]
    assert len(realtime.connections) == 1
    assert connection.closed


async def test_does_not_watch_when_discord_is_off(caplog):
    realtime = FakeRealtime()
    async with running(realtime, discord_env={}):
        await asyncio.sleep(0.05)

    assert realtime.connections == []
    assert "fill_watch_disabled" in [r.getMessage() for r in caplog.records]


async def test_skips_environment_without_credentials(caplog):
    environ = {k: v for k, v in DOMESTIC_PAPER_ENV.items() if not k.startswith("PAPER_KR_")}
    realtime = FakeRealtime()
    async with running(realtime, environ=environ):
        await asyncio.sleep(0.05)

    assert realtime.connections == []
    skipped = [r for r in caplog.records if r.getMessage() == "fill_watch_skipped"]
    assert skipped and skipped[0].fields["environment"] == "domestic_paper"


async def test_answers_ping_with_the_same_message():
    realtime = FakeRealtime()
    async with running(realtime):
        [connection] = await realtime.wait_registered()
        connection.push({"trnm": "PING", "seq": 7})
        await wait_until(lambda: connection.sent[-1].get("trnm") == "PING")

    assert connection.sent[-1] == {"trnm": "PING", "seq": 7}


async def test_domestic_buy_fill_is_sent_to_discord():
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = fill_kiwoom()
    async with running(realtime, discord, kiwoom):
        [connection] = await realtime.wait_registered()
        connection.push(fill_event())
        await discord.wait_for(1)

    payload = discord.payloads[0]
    assert payload["content"] == "[국내 모의]"
    embed = payload["embeds"][0]
    assert embed["title"] == "체결 · 매수"
    assert embed["color"] == RED
    assert fields(payload) == {
        "종목": "삼성전자 (005930)",
        "이번 체결": "3주 @ 60,000원 (180,000원)",
        "누적": "3 / 10주 · 남은 7주",
        "체결 시각": "09:41:05",
        "미체결 주문 (0건)": "없음",
    }
    # 매수 체결은 미체결 조회 말고 다른 키움 TR을 부르지 않는다.
    assert [r.headers.get("api-id") for r in kiwoom.requests[1:]] == ["ka10075"]


async def test_last_fill_of_a_sell_order_shows_fully_filled():
    realtime = FakeRealtime()
    discord = FakeDiscord()
    event = fill_event(
        **{"905": "-매도", "907": "1", "910": "-61000", "911": "7", "902": "0", "903": "607000"}
    )
    async with running(realtime, discord):
        [connection] = await realtime.wait_registered()
        connection.push(event)
        await discord.wait_for(1)

    embed = discord.payloads[0]["embeds"][0]
    assert embed["title"] == "체결 · 매도"
    assert embed["color"] == GRAY
    assert fields(discord.payloads[0])["이번 체결"] == "7주 @ 61,000원 (427,000원)"
    assert fields(discord.payloads[0])["누적"] == "10 / 10주 · 전량 체결"


async def test_only_fills_are_notified():
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = fill_kiwoom()
    async with running(realtime, discord, kiwoom):
        [connection] = await realtime.wait_registered()
        for status in ("접수", "확인", "취소", "거부"):
            connection.push(fill_event(**{"913": status, "911": "", "910": ""}))
        connection.push(fill_event(**{"911": "4"}))
        await discord.wait_for(1)
        await asyncio.sleep(0.05)

    assert len(discord.payloads) == 1
    assert fields(discord.payloads[0])["이번 체결"].startswith("4주")
    assert len(kiwoom.calls("ka10075")) == 1


async def test_unreadable_values_are_shown_as_unknown_and_next_fill_still_works(caplog):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord):
        [connection] = await realtime.wait_registered()
        connection.push(fill_event(**{"911": "abc", "902": "", "907": "9"}))
        connection.push(fill_event())
        await discord.wait_for(2)

    broken = discord.payloads[0]
    assert broken["embeds"][0]["title"] == "체결"
    assert fields(broken)["이번 체결"] == "확인 불가 @ 60,000원 (확인 불가)"
    assert fields(broken)["누적"] == "확인 불가"
    assert fields(discord.payloads[1])["이번 체결"] == "3주 @ 60,000원 (180,000원)"
    unreadable = [
        r.fields["key"] for r in caplog.records if r.getMessage() == "fill_value_unreadable"
    ]
    assert set(unreadable) == {"911", "902", "907"}


async def test_fills_arrive_in_order_even_when_discord_asks_to_retry():
    realtime = FakeRealtime()
    discord = FakeDiscord(httpx.Response(429, json={"retry_after": 0.05}))
    async with running(realtime, discord):
        [connection] = await realtime.wait_registered()
        connection.push(fill_event(**{"911": "3", "902": "7"}))
        connection.push(fill_event(**{"911": "7", "902": "0"}))
        await discord.wait_for(3)

    sent = [fields(p)["누적"] for p in discord.payloads]
    assert sent == ["3 / 10주 · 남은 7주", "3 / 10주 · 남은 7주", "10 / 10주 · 전량 체결"]


async def test_failed_login_does_not_register_and_closes(caplog):
    realtime = FakeRealtime(login_reply={"trnm": "LOGIN", "return_code": 1, "return_msg": "x"})
    async with running(realtime):
        await wait_until(lambda: bool(realtime.connections) and realtime.connections[0].closed)

    assert realtime.connections[0].sent_trnm() == ["LOGIN"]
    assert "realtime_login_failed" in [r.getMessage() for r in caplog.records]


async def test_failed_registration_is_logged_and_closes(caplog):
    realtime = FakeRealtime(reg_reply={"trnm": "REG", "return_code": 1, "return_msg": "x"})
    async with running(realtime):
        await wait_until(lambda: bool(realtime.connections) and realtime.connections[0].closed)

    assert "realtime_register_failed" in [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("silent", ["login_reply", "reg_reply"])
async def test_no_reply_to_login_or_registration_times_out(monkeypatch, caplog, silent):
    monkeypatch.setattr(fill_watch, "RESPONSE_TIMEOUT_SECONDS", 0.05)
    realtime = FakeRealtime()
    setattr(realtime, silent, None)
    async with running(realtime):
        await wait_until(lambda: bool(realtime.connections) and realtime.connections[0].closed)

    assert "realtime_reply_timeout" in [r.getMessage() for r in caplog.records]


async def test_connection_and_fill_steps_are_logged_without_secrets(caplog):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = fill_kiwoom()
    async with running(realtime, discord, kiwoom):
        [connection] = await realtime.wait_registered()
        connection.push(fill_event())
        await discord.wait_for(1)
        await wait_until(lambda: "fill_notified" in [r.getMessage() for r in caplog.records])

    events = [r.getMessage() for r in caplog.records if r.name == "stock_bot.fill_watch"]
    assert events == [
        "fill_watch_started",
        "fill_watch_skipped",
        "realtime_connected",
        "realtime_logged_in",
        "realtime_registered",
        "fill_received",
        "open_orders_fetched",
        "fill_notified",
    ]
    received = next(r for r in caplog.records if r.getMessage() == "fill_received")
    assert received.fields["order_no"] == "0000018"
    assert received.fields["price"] == "60000"
    logged = caplog.text + "".join(str(getattr(r, "fields", "")) for r in caplog.records)
    assert FAKE_ENV["PAPER_KR_ACCOUNT_NO"] not in logged
    assert kiwoom.issued_tokens[0] not in logged


async def test_malformed_message_is_logged_and_watching_continues(caplog):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord):
        [connection] = await realtime.wait_registered()
        connection.push_raw("not json")
        connection.push(fill_event())
        await discord.wait_for(1)

    assert "realtime_message_malformed" in [r.getMessage() for r in caplog.records]
    assert "realtime_disconnected" not in [r.getMessage() for r in caplog.records]


FAILED_LOGIN = {"trnm": "LOGIN", "return_code": 1, "return_msg": "token invalid"}
FAILED_REG = {"trnm": "REG", "return_code": 1, "return_msg": "x"}


@pytest.fixture
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(fill_watch, "RECONNECT_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_watch, "RECONNECT_MAX_SECONDS", 0.04)


def waits(caplog) -> list[float]:
    return [
        r.fields["wait_seconds"]
        for r in caplog.records
        if r.getMessage() == "realtime_reconnect_wait"
    ]


async def test_reconnects_and_registers_again_after_drop(fast_reconnect):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord):
        [first] = await realtime.wait_registered()
        first.drop()
        second = (await realtime.wait_registered(2))[1]
        second.push(fill_event())
        await discord.wait_for(1)

    assert first.closed
    assert second.sent_trnm() == ["LOGIN", "REG"]
    assert fields(discord.payloads[0])["이번 체결"] == "3주 @ 60,000원 (180,000원)"


async def test_retries_when_connection_is_refused(fast_reconnect, caplog):
    realtime = FakeRealtime(refuse=2)
    async with running(realtime):
        await realtime.wait_registered()

    assert realtime.attempts == 3
    assert [r.getMessage() for r in caplog.records].count("realtime_connect_failed") == 2


async def test_failed_login_gets_a_new_token_before_reconnecting(fast_reconnect):
    realtime = FakeRealtime(login_reply=[FAILED_LOGIN, OK_LOGIN])
    kiwoom = FakeKiwoom()
    async with running(realtime, kiwoom=kiwoom):
        await realtime.wait_registered()

    first, second = realtime.connections
    assert first.sent[0]["token"] == kiwoom.issued_tokens[0]
    assert second.sent[0]["token"] == kiwoom.issued_tokens[1]


async def test_no_login_reply_reconnects_with_a_new_token(fast_reconnect, monkeypatch):
    monkeypatch.setattr(fill_watch, "RESPONSE_TIMEOUT_SECONDS", 0.05)
    realtime = FakeRealtime(login_reply=[None, OK_LOGIN])
    async with running(realtime):
        await realtime.wait_registered()

    first, second = realtime.connections
    assert first.closed
    assert second.sent_trnm() == ["LOGIN", "REG"]
    # 응답 없이 무시된 토큰일 수 있으므로 다음 연결은 새 토큰으로 한다.
    assert first.sent[0]["token"] != second.sent[0]["token"]


async def test_wait_grows_while_registration_fails_and_resets_after_success(fast_reconnect, caplog):
    realtime = FakeRealtime(reg_reply=[FAILED_REG, FAILED_REG, FAILED_REG, FAILED_REG, OK_REG])
    async with running(realtime):
        registered = (await realtime.wait_registered(5))[4]
        registered.drop()
        await wait_until(lambda: len(waits(caplog)) >= 5)

    # 연결은 되지만 REG가 실패하면 간격이 늘어나고, 등록에 성공한 뒤 끊기면 처음으로 돌아간다.
    assert waits(caplog)[:5] == [0.01, 0.02, 0.04, 0.04, 0.01]


async def test_shutdown_stops_waiting_to_reconnect(monkeypatch):
    monkeypatch.setattr(fill_watch, "RECONNECT_INITIAL_SECONDS", 30)
    realtime = FakeRealtime(reg_reply=FAILED_REG)
    started = asyncio.get_running_loop().time()
    async with running(realtime):
        await wait_until(lambda: bool(realtime.connections) and realtime.connections[0].closed)

    assert asyncio.get_running_loop().time() - started < 5
    assert len(realtime.connections) == 1


async def test_web_api_keeps_working_while_realtime_fails(fast_reconnect):
    realtime = FakeRealtime(reg_reply=FAILED_REG)
    async with running(realtime) as app:
        await wait_until(lambda: len(realtime.connections) >= 2)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            login = await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
            environments = await client.get("/api/environments")

    assert login.status_code == 204
    assert environments.status_code == 200


async def test_error_while_closing_still_reconnects(fast_reconnect, caplog):
    realtime = FakeRealtime()
    async with running(realtime):
        [first] = await realtime.wait_registered()
        first.close_error = OSError("close failed")
        first.drop()
        await realtime.wait_registered(2)

    assert "realtime_close_failed" in [r.getMessage() for r in caplog.records]


async def test_undecodable_frame_is_skipped_without_reconnecting(caplog):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord):
        [connection] = await realtime.wait_registered()
        connection.push_raw(b"\xff\xfe")
        connection.push(fill_event())
        await discord.wait_for(1)

    messages = [r.getMessage() for r in caplog.records]
    assert "realtime_message_malformed" in messages
    assert "realtime_disconnected" not in messages


async def test_malformed_fill_time_is_logged(caplog):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord):
        [connection] = await realtime.wait_registered()
        connection.push(fill_event(**{"908": "9410"}))
        await discord.wait_for(1)

    assert fields(discord.payloads[0])["체결 시각"] == "확인 불가"
    unreadable = [
        r.fields["key"] for r in caplog.records if r.getMessage() == "fill_value_unreadable"
    ]
    assert unreadable == ["908"]


REPLACED = {
    "trnm": "SYSTEM",
    "code": "R10001",
    "message": "동일한 App key로 접속이 되었습니다. 기존 세션은 종료가 됩니다",
}


async def test_stops_watching_when_another_connection_takes_the_app_key(fast_reconnect, caplog):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord):
        [connection] = await realtime.wait_registered()
        connection.push(REPLACED)
        connection.drop()
        await discord.wait_for(1)
        await asyncio.sleep(0.1)

    # 서로 끊어내지 않도록 다시 연결하지 않는다.
    assert len(realtime.connections) == 1
    assert connection.closed
    payload = discord.payloads[0]
    assert payload["content"] == "[국내 모의]"
    assert payload["embeds"][0]["title"] == "체결 감시 중단"
    assert "같은 앱 키" in payload["embeds"][0]["description"]
    replaced = [r for r in caplog.records if r.getMessage() == "realtime_session_replaced"]
    assert replaced and replaced[0].levelname == "ERROR"


async def test_other_system_messages_are_logged_and_watching_continues(caplog):
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord):
        [connection] = await realtime.wait_registered()
        connection.push({"trnm": "SYSTEM", "code": "R99999", "message": "공지"})
        connection.push(fill_event())
        await discord.wait_for(1)

    assert discord.payloads[0]["embeds"][0]["title"] == "체결 · 매수"
    system = [r for r in caplog.records if r.getMessage() == "realtime_system_message"]
    assert system and system[0].fields["code"] == "R99999"


async def notify_fills(
    kiwoom: FakeKiwoom,
    *events: dict,
    environ: dict[str, str] = DOMESTIC_PAPER_ENV,
    expect: int | None = None,
) -> list[dict]:
    """체결 이벤트를 보내고 Discord로 나간 알림을 돌려준다. 이벤트가 없으면 매수 체결 하나다.
    expect는 기다릴 알림 수이고, 없으면 이벤트 수만큼 기다린다."""
    events = events or (fill_event(),)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord, kiwoom, environ=environ):
        [connection] = await realtime.wait_registered()
        for event in events:
            connection.push(event)
        await discord.wait_for(len(events) if expect is None else expect)
        await asyncio.sleep(0.05)
    return discord.payloads


async def test_open_orders_are_listed_oldest_first():
    kiwoom = fill_kiwoom(
        {
            "oso": [
                open_order(
                    stk_nm="SK하이닉스",
                    io_tp_nm="-매도",
                    ord_pric="+201000",
                    tm="101500",
                    ord_qty="5",
                    oso_qty="5",
                ),
                open_order(tm="094022"),
                open_order(
                    stk_nm="카카오",
                    trde_tp="시장가",
                    ord_pric="0",
                    tm="100000",
                    ord_qty="3",
                    oso_qty="1",
                ),
            ]
        }
    )
    [payload] = await notify_fills(kiwoom)

    assert body_of(kiwoom.calls("ka10075")[0]) == {
        "all_stk_tp": "0",
        "trde_tp": "0",
        "stk_cd": "",
        "stex_tp": "0",
    }
    assert kiwoom.calls("ka10075")[0].url.host == "mockapi.kiwoom.com"
    assert fields(payload)["미체결 주문 (3건)"] == "\n".join(
        [
            "삼성전자 · 매수 · 지정가 60,000원 · 미체결 7/10주 · 09:40:22",
            "카카오 · 매수 · 시장가 · 미체결 1/3주 · 10:00:00",
            "SK하이닉스 · 매도 · 지정가 201,000원 · 미체결 5/5주 · 10:15:00",
        ]
    )


async def test_more_than_ten_open_orders_are_summarized():
    rows = [open_order(tm=f"09{minute:02d}00") for minute in range(12)]
    [payload] = await notify_fills(fill_kiwoom({"oso": rows}))

    lines = fields(payload)["미체결 주문 (12건)"].split("\n")
    assert len(lines) == 11
    assert lines[0].endswith("09:00:00")
    assert lines[9].endswith("09:09:00")
    assert lines[10] == "외 2건"


async def test_open_orders_stop_before_the_discord_field_limit():
    rows = [open_order(stk_nm="가" * 120, tm=f"09{minute:02d}00") for minute in range(10)]
    [payload] = await notify_fills(fill_kiwoom({"oso": rows}))

    value = fields(payload)["미체결 주문 (10건)"]
    lines = value.split("\n")
    assert len(value) <= 1024
    assert "…" not in value
    shown = len(lines) - 1
    assert lines[-1] == f"외 {10 - shown}건"


async def test_open_orders_follow_continuation_pages():
    kiwoom = fill_kiwoom(
        page_response({"oso": [open_order(tm="090000")]}, cont_yn="Y", next_key="k1"),
        page_response({"oso": [open_order(tm="091000")]}),
    )
    [payload] = await notify_fills(kiwoom)

    calls = kiwoom.calls("ka10075")
    assert [c.headers["cont-yn"] for c in calls] == ["N", "Y"]
    assert calls[1].headers["next-key"] == "k1"
    assert "미체결 주문 (2건)" in fields(payload)


@pytest.mark.parametrize(
    "reply",
    [
        {"return_code": 1, "return_msg": "조회 오류"},
        httpx.Response(500, text="oops"),
        {"oso": "not a list"},
    ],
)
async def test_open_order_lookup_failure_still_sends_the_fill(reply, caplog):
    kiwoom = fill_kiwoom(reply)
    [payload] = await notify_fills(kiwoom)

    assert fields(payload)["이번 체결"] == "3주 @ 60,000원 (180,000원)"
    assert fields(payload)["미체결 주문"] == "조회 실패"
    assert "open_orders_failed" in [r.getMessage() for r in caplog.records]


def sell(**overrides: str) -> dict:
    """삼성전자 10주 매도 주문의 체결. 기본은 61,000원에 10주 전량 체결이다."""
    values = {"905": "-매도", "907": "1", "910": "-61000", "911": "10", "902": "0"}
    return fill_event(**{**values, **overrides})


def with_realized(*rows: dict) -> FakeKiwoom:
    return fill_kiwoom().reply("ka10077", {"tdy_rlzt_pl": "0", "tdy_rlzt_pl_dtl": list(rows)})


@pytest.mark.parametrize(
    ("price", "title", "color", "profit"),
    [
        ("-61000", "체결 · 매도 · 익절", RED, "+10,000원 (+1.67%)"),
        ("+59000", "체결 · 매도 · 손절", BLUE, "-10,000원 (-1.67%)"),
        ("60000", "체결 · 매도 · 본절", GRAY, "0원 (0.00%)"),
    ],
)
async def test_sell_fill_shows_profit_against_the_purchase_price(price, title, color, profit):
    kiwoom = with_realized(realized_row(), realized_row())
    [payload] = await notify_fills(kiwoom, sell(**{"910": price}))

    assert body_of(kiwoom.calls("ka10077")[0]) == {"stk_cd": "005930"}
    assert kiwoom.calls("ka10077")[0].url.host == "mockapi.kiwoom.com"
    assert payload["embeds"][0]["title"] == title
    assert payload["embeds"][0]["color"] == color
    assert fields(payload)["매도 손익"] == f"{profit} · 매입가 60,000원 · 수수료·세금 제외"
    names = list(fields(payload))
    assert names.index("매도 손익") == names.index("체결 시각") + 1


async def test_buy_fill_does_not_look_up_profit():
    kiwoom = with_realized(realized_row())
    [payload] = await notify_fills(kiwoom, fill_event())

    assert kiwoom.calls("ka10077") == []
    assert "매도 손익" not in fields(payload)


async def test_split_sell_shows_profit_for_everything_filled_so_far():
    kiwoom = with_realized(realized_row(buy_uv="97602.9573459"))
    first, second = await notify_fills(
        kiwoom,
        sell(**{"910": "100000", "911": "3", "902": "7"}),
        sell(**{"910": "101000", "911": "7", "902": "0"}),
    )

    # 3주 x 100,000 = 300,000 - 매입 292,808.87 = 7,191.13 → 7,191원
    assert fields(first)["매도 손익"].startswith("+7,191원 (+2.46%) · 매입가 97,603원")
    # 300,000 + 707,000 = 1,007,000 - 매입 976,029.57 = 30,970.43 → 30,970원
    assert fields(second)["매도 손익"].startswith("+30,970원 (+3.17%)")
    assert fields(second)["누적"] == "10 / 10주 · 전량 체결"


async def test_profit_is_unknown_when_earlier_fills_of_the_order_were_missed():
    # 서버가 앞선 3주 체결을 받지 못한 채(재시작 등) 나머지 7주 체결만 받은 경우.
    kiwoom = with_realized(realized_row())
    [payload] = await notify_fills(kiwoom, sell(**{"911": "7", "902": "0"}))

    assert payload["embeds"][0]["title"] == "체결 · 매도"
    assert payload["embeds"][0]["color"] == GRAY
    assert fields(payload)["매도 손익"] == "조회 실패"


async def test_stale_partial_fills_are_forgotten(monkeypatch):
    monkeypatch.setattr(fill_watch, "FILL_MEMORY_SECONDS", 0)
    kiwoom = with_realized(realized_row())
    _, second = await notify_fills(
        kiwoom,
        sell(**{"911": "3", "902": "7"}),
        sell(**{"911": "7", "902": "0"}),
    )

    assert fields(second)["매도 손익"] == "조회 실패"


@pytest.mark.parametrize(
    "reply",
    [
        {"return_code": 1, "return_msg": "조회 오류"},
        {"tdy_rlzt_pl": "0", "tdy_rlzt_pl_dtl": []},
        {"tdy_rlzt_pl": "0", "tdy_rlzt_pl_dtl": [realized_row(stk_cd="A000660")]},
        {
            "tdy_rlzt_pl": "0",
            "tdy_rlzt_pl_dtl": [realized_row(buy_uv="60000"), realized_row(buy_uv="59000")],
        },
    ],
    ids=["kiwoom-error", "no-rows", "other-stock", "different-purchase-prices"],
)
async def test_profit_lookup_failure_still_sends_the_fill(reply, caplog):
    kiwoom = fill_kiwoom().reply("ka10077", reply)
    [payload] = await notify_fills(kiwoom, sell())

    assert payload["embeds"][0]["title"] == "체결 · 매도"
    assert payload["embeds"][0]["color"] == GRAY
    assert fields(payload)["매도 손익"] == "조회 실패"
    assert fields(payload)["이번 체결"] == "10주 @ 61,000원 (610,000원)"
    assert "sell_profit_failed" in [r.getMessage() for r in caplog.records]


async def test_unreadable_sell_values_make_profit_unknown():
    kiwoom = with_realized(realized_row())
    [payload] = await notify_fills(kiwoom, sell(**{"910": "abc"}))

    assert payload["embeds"][0]["title"] == "체결 · 매도"
    assert payload["embeds"][0]["color"] == GRAY
    assert fields(payload)["매도 손익"] == "확인 불가"


US_BUY_COLOR = RED


async def test_paper_environments_each_get_their_own_connection():
    realtime = FakeRealtime()
    kiwoom = fill_kiwoom()
    async with running(realtime, kiwoom=kiwoom, environ=FAKE_ENV):
        domestic, us = await realtime.wait_registered(2)

    registered = {c.sent[1]["data"][0]["type"][0]: c for c in (domestic, us)}
    assert set(registered) == {"00", "F5"}
    assert registered["F5"].url == PAPER_REALTIME
    assert registered["F5"].sent[1]["data"][0]["item"] == [""]
    # 국내 모의와 미국 모의는 인증정보가 달라 토큰도 다르다.
    assert registered["00"].sent[0]["token"] != registered["F5"].sent[0]["token"]
    assert {c.sent[0]["token"] for c in (domestic, us)} == set(kiwoom.issued_tokens)


async def us_fills(kiwoom: FakeKiwoom, *events: dict, expect: int | None = None) -> list[dict]:
    return await notify_fills(kiwoom, *events, environ=US_PAPER_ENV, expect=expect)


async def test_us_buy_fill_is_sent_in_usd_with_us_open_orders():
    kiwoom = fill_kiwoom().reply("ust21050", {"result_list": [us_open_order()]})
    [payload] = await us_fills(kiwoom, us_fill_event())

    assert payload["content"] == "[미국 모의]"
    assert payload["embeds"][0]["title"] == "체결 · 매수"
    assert payload["embeds"][0]["color"] == US_BUY_COLOR
    assert fields(payload) == {
        "종목": "포드 모터 (F)",
        "이번 체결": "1주 @ $12.075 ($12.08)",
        "누적": "1 / 1주 · 전량 체결",
        "체결 시각": "23:23:08",
        "미체결 주문 (1건)": "포드 모터 · 매수 · 지정가 $1 · 미체결 1/1주 · 23:22:11",
    }
    assert body_of(kiwoom.calls("ust21050")[0]) == {
        "ord_dt": "",
        "slby_tp": "0",
        "stex_tp": "",
        "stk_cd": "",
    }
    assert kiwoom.calls("ust21050")[0].url.host == "mockapi.kiwoom.com"
    assert kiwoom.calls("ka10075") == []


async def test_us_order_events_other_than_fills_are_ignored():
    kiwoom = fill_kiwoom()
    payloads = await us_fills(
        kiwoom,
        us_fill_event(**{"913": "접수", "911": "0", "910": "000000000000", "902": "1"}),
        us_fill_event(**{"913": "취소완료", "911": "0", "910": "000000000000"}),
        us_fill_event(**{"913": "부분체결", "900": "30", "911": "10", "902": "20"}),
        expect=1,
    )

    assert len(payloads) == 1
    assert fields(payloads[0])["누적"] == "10 / 30주 · 남은 20주"
    assert len(kiwoom.calls("ust21050")) == 1


def us_sell(**overrides: str) -> dict:
    """포드 모터 31주 매도의 체결. 기본은 12.50달러 전량 체결, 매입단가 12.075달러다."""
    values = {
        "907": "01",
        "50072": "매도",
        "9203": "000002675",
        "900": "31",
        "911": "31",
        "902": "0",
        "910": "0000012.5000",
        "50724": "0000374.3250",
    }
    return us_fill_event(**{**values, **overrides})


@pytest.mark.parametrize(
    ("price", "title", "color", "profit"),
    [
        ("0000012.5000", "체결 · 매도 · 익절", RED, "+$13.18 (+3.52%)"),
        ("0000011.9000", "체결 · 매도 · 손절", BLUE, "-$5.43 (-1.45%)"),
        ("0000012.0750", "체결 · 매도 · 본절", GRAY, "$0.00 (0.00%)"),
    ],
)
async def test_us_sell_profit_uses_realized_purchase_amount(price, title, color, profit):
    kiwoom = fill_kiwoom()
    [payload] = await us_fills(kiwoom, us_sell(**{"910": price}))

    assert payload["embeds"][0]["title"] == title
    assert payload["embeds"][0]["color"] == color
    # 1주 가격은 받은 대로 소수 넷째 자리까지, 금액·손익은 센트까지 보여준다.
    assert fields(payload)["매도 손익"] == f"{profit} · 매입가 $12.075 · 수수료·세금 제외"
    # 매입 금액은 체결 이벤트에 들어 있어 따로 조회하지 않는다.
    assert kiwoom.calls("ust21630") == []
    assert kiwoom.calls("ka10077") == []


async def test_us_split_sell_adds_up_fills_across_midnight():
    first, second = await us_fills(
        fill_kiwoom(),
        us_sell(
            **{
                "911": "10",
                "902": "21",
                "910": "0000012.5000",
                "50724": "0000120.7500",
                "908": "235959",
            }
        ),
        us_sell(
            **{
                "911": "21",
                "902": "0",
                "910": "0000012.6000",
                "50724": "0000253.5750",
                "908": "000001",
            }
        ),
    )

    # 10 x 12.50 - 120.75 = 4.25
    assert fields(first)["매도 손익"].startswith("+$4.25 (+3.52%)")
    # (125.00 + 264.60) - 374.325 = 15.275
    assert fields(second)["매도 손익"].startswith("+$15.28 (+4.08%)")


@pytest.mark.parametrize(
    "second_cost",
    ["0000374.3250", "0000000.0000", ""],
    ids=["looks-cumulative", "zero", "missing"],
)
async def test_us_profit_fails_when_purchase_amount_is_not_per_fill(second_cost):
    _, second = await us_fills(
        fill_kiwoom(),
        us_sell(**{"911": "10", "902": "21", "50724": "0000120.7500"}),
        us_sell(**{"911": "21", "902": "0", "50724": second_cost}),
    )

    assert second["embeds"][0]["title"] == "체결 · 매도"
    assert fields(second)["매도 손익"] == "조회 실패"


async def test_us_profit_fails_when_earlier_fills_were_missed():
    [payload] = await us_fills(fill_kiwoom(), us_sell(**{"911": "21", "50724": "0000253.5750"}))

    assert fields(payload)["매도 손익"] == "조회 실패"


async def test_same_order_number_in_two_environments_is_kept_apart():
    realtime = FakeRealtime()
    discord = FakeDiscord()
    async with running(realtime, discord, fill_kiwoom(), environ=FAKE_ENV):
        connections = await realtime.wait_registered(2)
        by_type = {c.sent[1]["data"][0]["type"][0]: c for c in connections}
        # 국내 모의 매도 주문과 미국 모의 매도 주문의 주문번호가 같다.
        by_type["00"].push(sell(**{"9203": "000002675", "911": "3", "902": "7"}))
        await discord.wait_for(1)
        by_type["F5"].push(us_sell())
        await discord.wait_for(2)

    us = discord.payloads[1]
    assert us["content"] == "[미국 모의]"
    assert fields(us)["매도 손익"].startswith("+$13.18")


async def test_us_unreadable_values_are_shown_as_unknown_and_next_fill_still_works():
    broken, ok = await us_fills(fill_kiwoom(), us_fill_event(**{"911": "abc"}), us_fill_event())

    assert fields(broken)["이번 체결"] == "확인 불가 @ $12.075 (확인 불가)"
    assert fields(ok)["이번 체결"] == "1주 @ $12.075 ($12.08)"


async def test_us_unit_prices_keep_up_to_four_decimals():
    kiwoom = fill_kiwoom().reply(
        "ust21050",
        {"result_list": [us_open_order(ord_uv="1234.5000"), us_open_order(ord_uv="12.0522")]},
    )
    [payload] = await us_fills(kiwoom, us_fill_event(**{"910": "0000012.0522", "911": "2"}))

    assert fields(payload)["이번 체결"] == "2주 @ $12.0522 ($24.10)"
    lines = fields(payload)["미체결 주문 (2건)"].split("\n")
    assert "지정가 $1,234.5 ·" in lines[0]
    assert "지정가 $12.0522 ·" in lines[1]
