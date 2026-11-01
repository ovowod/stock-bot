"""Trailing Stop 자동 매도(모의): 트리거 때 키움 주문, Discord 알림, 상태 조회 API를 본다."""

from typing import Any

import httpx
import pytest

from tests.fake_discord import GRAY, RED, FakeDiscord, fields
from tests.fake_kiwoom import REAL_ENV, US_PAPER_ENV, body_of, kiwoom_error
from tests.fake_realtime import FakeRealtime, fill_event
from tests.log_capture import JsonLines
from tests.sl_tp.helpers import (
    AT_10,
    NY_10,
    Clock,
    Gate,
    api,
    balance,
    messages,
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
    ts_kiwoom,
    ts_session,
    ts_us_kiwoom,
    until_status,
    utc,
    watching,
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
