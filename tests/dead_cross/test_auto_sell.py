"""Dead Cross 자동 매도(모의): 하향 돌파 때 키움 주문, Discord 알림, 상태 조회 API를 본다.

이동평균은 단기 2봉, 장기 3봉으로 잰다. closes는 최신 봉부터다.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.dead_cross.helpers import (
    KST,
    NY,
    Chart,
    dc_kiwoom,
    dc_session,
    dc_us_kiwoom,
    first,
    judged,
    minute_rows,
    put_dc,
    read_record,
    remembered,
    status,
    until_status,
    utc,
    write_dc,
    write_record,
)
from tests.fake_discord import GRAY, FakeDiscord, fields
from tests.fake_kiwoom import REAL_ENV, US_PAPER_ENV, body_of, kiwoom_error
from tests.fake_realtime import REPLACED, FakeRealtime, fill_event
from tests.log_capture import JsonLines
from tests.sl_tp.helpers import (
    AT_10,
    NY_10,
    SELL_REPLY,
    Clock,
    api,
    balance,
    nth_call_gate,
    position,
    quote,
    quote_regs,
    running,
    session,
    settle,
)
from tests.support import wait_until

pytestmark = pytest.mark.anyio

# 10:00에 받는 5분봉. 09:55 봉에서 단기(95)가 장기(96.67) 아래로 내려갔다.
CROSSING = ["1", "90", "100", "100", "100"]
# 09:50 봉에서 이미 단기가 장기 아래였다.
ALREADY_BELOW = ["1", "80", "90", "100", "100"]
BAR_0955 = datetime(2026, 10, 26, 9, 55, tzinfo=KST)
BAR_0950 = datetime(2026, 10, 26, 9, 50, tzinfo=KST)
BAR_0945 = datetime(2026, 10, 26, 9, 45, tzinfo=KST)
# 10:01에 세션이 바뀐다. 09:55 봉이 끝난 뒤의 확인과 겹치지 않는다.
TWO_SESSIONS = (dc_session("09:00", "10:01"), dc_session("10:01", "15:30"))
# 세션 시작 조회 뒤, 09:55 봉이 끝난 뒤의 확인(10:00:05)도 지난 시각.
AFTER_FIRST_CHECK = AT_10 + timedelta(seconds=6)

SELL_TITLES = (
    "Dead Cross 자동 매도 접수",
    "Dead Cross 자동 매도 실패",
    "Dead Cross 자동 매도 결과 확인 불가",
)


@pytest.fixture(autouse=True)
def known_stock(data_dir):
    """처음 보는 종목은 기준점만 잡으므로, 09:50 봉까지 판정한 기억으로 시작한다."""
    write_record(data_dir, {"005930": remembered(BAR_0950)})


def chart_at(closes: list[str], newest: datetime = AT_10) -> Chart:
    chart = Chart()
    chart.rows["005930"] = minute_rows(newest, closes)
    return chart


def sell_alerts(discord: FakeDiscord) -> list[dict[str, Any]]:
    return [p for p in discord.payloads if p["embeds"][0].get("title") in SELL_TITLES]


def sell_state(body: dict[str, Any]) -> str | None:
    item = first(body)
    auto_sell = item["auto_sell"] if item else None
    return None if auto_sell is None else auto_sell["state"]


async def test_dead_cross_sells_everything_at_market_and_announces_it(data_dir):
    write_dc(data_dir)
    discord = FakeDiscord()
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir, discord=discord) as app:
        body = await until_status(app, lambda b: sell_state(b) == "accepted")
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
    assert first(body)["status"] == "crossed"
    assert first(body)["auto_sell"] == {
        "state": "accepted",
        "reason": None,
        "quantity": 3,
        "order_no": "0000138",
        "at": utc(AT_10),
    }
    [payload] = sell_alerts(discord)
    assert payload["content"] == "[국내 모의]"
    assert payload["embeds"][0]["color"] == GRAY
    assert fields(payload) == {
        "투자 환경": "국내 모의",
        "종목": "삼성전자 (005930)",
        "Dead Cross 세션": "09:00–15:30 (한국시간)",
        "기준": "5분봉 · 단기 2봉 · 장기 3봉",
        "단기 이동평균": "95.00원",
        "장기 이동평균": "96.67원",
        "발생 봉": "2026-10-26 09:55:00 (한국시간)",
        "주문 수량": "3주",
        "주문번호": "0000138",
    }


async def test_us_sell_goes_to_the_exchange_of_the_holding(data_dir):
    write_dc(data_dir, dc_session("09:30", "16:00"), environment="us_paper")
    chart = Chart(list_key="result_list", page_size=100)
    newest = datetime(2026, 10, 26, 10, 0, tzinfo=NY)
    last = remembered(newest - timedelta(minutes=10), purchase_price="200")
    write_record(data_dir, {"AAPL": last}, "us_paper")
    chart.rows["AAPL"] = minute_rows(newest, CROSSING, sign="")
    discord = FakeDiscord()
    kiwoom = dc_us_kiwoom(chart)
    async with running(
        FakeRealtime(), kiwoom, data_dir, Clock(NY_10), US_PAPER_ENV, discord
    ) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted", "us_paper")
        await wait_until(lambda: bool(sell_alerts(discord)))

    [order] = kiwoom.calls("ust20001")
    assert body_of(order)["stk_cd"] == "AAPL"
    assert body_of(order)["stex_tp"] == "ND"
    assert body_of(order)["ord_qty"] == "3"
    alert = fields(sell_alerts(discord)[0])
    assert alert["종목"] == "애플 (AAPL · NASDAQ)"
    assert alert["단기 이동평균"] == "$95.0000"
    assert alert["주문번호"] == "000000300"


async def test_shows_sending_while_the_order_is_on_its_way(data_dir):
    write_dc(data_dir)
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    gate = nth_call_gate(kiwoom, "kt10001", 1)
    async with running(FakeRealtime(), gate, data_dir) as app:
        body = await until_status(app, lambda b: sell_state(b) == "sending")
        gate.open.set()
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))

    assert first(body)["auto_sell"]["quantity"] is None


async def test_dead_cross_from_outside_the_session_sells_when_it_is_still_there(data_dir):
    write_dc(data_dir)
    write_record(data_dir, {"005930": remembered(BAR_0945)})
    kiwoom = dc_kiwoom(chart_at(["1", "90", "90", "100", "100", "100"]))
    discord = FakeDiscord()
    async with running(FakeRealtime(), kiwoom, data_dir, discord=discord) as app:
        body = await until_status(app, lambda b: sell_state(b) == "accepted")
        await wait_until(lambda: bool(sell_alerts(discord)))

    assert first(body)["crossed_bar"] == utc(BAR_0950)
    assert len(kiwoom.calls("kt10001")) == 1
    assert "시도" not in fields(sell_alerts(discord)[0])


async def test_dead_cross_released_in_the_same_judging_does_not_sell(data_dir):
    write_dc(data_dir)
    write_record(data_dir, {"005930": remembered(BAR_0945)})
    kiwoom = dc_kiwoom(chart_at(["1", "200", "90", "100", "100", "100"]))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, lambda b: first(b)["judged_bar"] == utc(BAR_0955))
        await settle()

    assert first(body)["status"] == "tracking"
    assert kiwoom.calls("kt10001") == []


@pytest.mark.parametrize(
    ("excluded", "rows"),
    [(("005930",), (position(),)), ((), (position(), position()))],
    ids=["excluded", "duplicate"],
)
async def test_excluded_and_ineligible_stocks_are_not_sold(data_dir, excluded, rows):
    write_dc(data_dir, excluded=excluded)
    kiwoom = dc_kiwoom(chart_at(CROSSING), balance(*rows))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: b["watching"])
        await settle()

    assert kiwoom.calls("ka10080") == []
    assert kiwoom.calls("kt10001") == []


async def test_stock_without_a_purchase_price_is_sold(data_dir):
    write_dc(data_dir)
    kiwoom = dc_kiwoom(chart_at(CROSSING), balance(position(pur_pric="000000000000000")))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")

    assert len(kiwoom.calls("kt10001")) == 1


async def test_a_new_stock_only_takes_a_baseline_from_its_first_bar(data_dir):
    """가장 최근 완료 봉은 사기 전에 끝났을 수 있어, 하향 돌파여도 팔지 않는다."""
    write_record(data_dir, {})
    write_dc(data_dir)
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, judged)
        await settle()

    assert first(body)["status"] == "tracking"
    assert first(body)["judged_bar"] == utc(BAR_0955)
    assert first(body)["crossed_bar"] is None
    assert kiwoom.calls("kt10001") == []


async def test_a_new_stock_sells_on_a_crossing_after_its_baseline(data_dir):
    write_record(data_dir, {})
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    chart = chart_at(["1", "100", "100", "100", "100"])
    kiwoom = dc_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, judged)
        chart.rows["005930"] = minute_rows(
            AT_10 + timedelta(minutes=5), ["1", "90", "100", "100", "100", "100"]
        )
        clock.at = AT_10 + timedelta(minutes=5, seconds=6)
        body = await until_status(app, lambda b: sell_state(b) == "accepted")

    assert first(body)["crossed_bar"] == utc(AT_10)
    assert len(kiwoom.calls("kt10001")) == 1


async def test_same_crossed_bar_is_not_sold_twice_in_a_session(data_dir):
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    chart = chart_at(CROSSING)
    kiwoom = dc_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")
        clock.at = AT_10 + timedelta(minutes=5, seconds=6)
        await until_status(app, lambda b: first(b)["judged_bar"] == utc(AT_10))
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1


async def test_crossing_again_after_a_release_sells_in_the_same_session(data_dir):
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    chart = chart_at(CROSSING)
    kiwoom = dc_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")
        chart.rows["005930"] = minute_rows(
            AT_10 + timedelta(minutes=5), ["1", "200", "90", "100", "100", "100"]
        )
        clock.at = AT_10 + timedelta(minutes=5, seconds=6)
        await until_status(app, lambda b: first(b)["status"] == "tracking")
        chart.rows["005930"] = minute_rows(
            AT_10 + timedelta(minutes=15), ["1", "10", "10", "200", "90", "100", "100", "100"]
        )
        clock.at = AT_10 + timedelta(minutes=15, seconds=6)
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 2)


async def test_saving_new_criteria_in_the_same_session_does_not_sell_again(data_dir):
    write_dc(data_dir)
    kiwoom = dc_kiwoom(chart_at([*CROSSING, "100", "100"]))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")
        await put_dc(app, dc_session(long_period=4))
        await until_status(app, lambda b: first(b)["basis"]["long_period"] == 4)
        await settle()

    assert len(kiwoom.calls("kt10001")) == 1


async def test_new_criteria_without_enough_bars_do_not_sell_an_old_dead_cross(data_dir):
    write_dc(data_dir, dc_session(long_period=6))
    write_record(data_dir, {"005930": remembered(BAR_0955, AT_10, BAR_0955)})
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: b["watching"])
        await wait_until(lambda: bool(kiwoom.calls("ka10080")))
        await settle()

    assert kiwoom.calls("kt10001") == []


@pytest.mark.parametrize(
    ("reply", "title", "reason", "description"),
    [
        (
            kiwoom_error(1, "주문가능수량 부족"),
            "Dead Cross 자동 매도 실패",
            "rejected",
            "다음 Dead Cross 세션에 다시 시도합니다.",
        ),
        (
            httpx.ConnectError("down"),
            "Dead Cross 자동 매도 결과 확인 불가",
            "unknown",
            "접수 여부를 확인할 수 없습니다. 키움에서 주문 내역을 확인하세요.",
        ),
    ],
    ids=["rejected", "unknown"],
)
async def test_failed_sell_is_announced_and_shown_with_its_reason(
    data_dir, reply, title, reason, description
):
    write_dc(data_dir)
    discord = FakeDiscord()
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    if isinstance(reply, Exception):

        def down(request: httpx.Request) -> httpx.Response:
            raise reply

        kiwoom.respond("kt10001", down)
    else:
        kiwoom.reply("kt10001", reply)
    async with running(FakeRealtime(), kiwoom, data_dir, discord=discord) as app:
        body = await until_status(app, lambda b: sell_state(b) == "failed")
        await wait_until(lambda: bool(sell_alerts(discord)))

    auto_sell = first(body)["auto_sell"]
    assert (auto_sell["reason"], auto_sell["order_no"]) == (reason, None)
    [payload] = sell_alerts(discord)
    embed = payload["embeds"][0]
    assert (embed["title"], embed["color"], embed["description"]) == (title, GRAY, description)
    if reason == "rejected":
        assert fields(payload)["원인"].startswith("키움 오류 [1] 주문가능수량 부족")
    else:
        assert fields(payload)["주문 수량"] == "3주"


async def test_nothing_sellable_is_shown_but_not_announced(data_dir):
    write_dc(data_dir)
    discord = FakeDiscord()
    # 주문 직전 잔고 재확인에서 매도 가능 수량이 0이다(이미 미체결 매도에 묶임).
    kiwoom = dc_kiwoom(
        chart_at(CROSSING), balance(), balance(position(trde_able_qty="000000000000000"))
    )
    async with running(FakeRealtime(), kiwoom, data_dir, discord=discord) as app:
        body = await until_status(app, lambda b: sell_state(b) == "failed")
        await settle()

    assert first(body)["auto_sell"]["reason"] == "no_sellable"
    assert kiwoom.calls("kt10001") == []
    assert sell_alerts(discord) == []


async def test_sl_tp_sell_in_progress_on_the_same_stock_keeps_dead_cross_from_selling(data_dir):
    sl_tp = {"updated_at": None, "excluded": [], "sessions": [session(take_profit="5")]}
    write_dc(data_dir, sl_tp=sl_tp)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    chart_open, order_open, order_hit = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def gate(request: httpx.Request) -> httpx.Response:
        if api(request) == "ka10080":
            await chart_open.wait()
        if api(request) == "kt10001":
            order_hit.set()
            await order_open.wait()
        return kiwoom(request)

    async with running(realtime, gate, data_dir, discord=discord) as app:
        await wait_until(lambda: any(quote_regs(c) for c in realtime.connections))
        connection = next(c for c in realtime.connections if quote_regs(c))
        # 63,000원(+5%)에 SL / TP 익절 트리거가 매도를 내고, 그 주문 응답이 늦다.
        connection.push(quote(price="+63000"))
        await order_hit.wait()
        chart_open.set()
        body = await until_status(app, lambda b: sell_state(b) == "failed")
        order_open.set()
        await wait_until(lambda: bool(kiwoom.calls("kt10001")))
        await settle()

    assert first(body)["auto_sell"]["reason"] == "in_progress"
    assert len(kiwoom.calls("kt10001")) == 1
    assert sell_alerts(discord) == []


@pytest.mark.parametrize("next_run", ["next_session", "next_day", "restart"])
async def test_failed_sell_is_tried_once_more_in_the_next_run(data_dir, next_run):
    write_dc(data_dir, *TWO_SESSIONS)
    clock = Clock()
    discord = FakeDiscord()
    kiwoom = dc_kiwoom(chart_at(CROSSING)).reply("kt10001", kiwoom_error(1, "거부"), SELL_REPLY)
    async with running(FakeRealtime(), kiwoom, data_dir, clock, discord=discord) as app:
        await until_status(app, lambda b: sell_state(b) == "failed")
        await settle()
        assert len(kiwoom.calls("kt10001")) == 1
        if next_run != "restart":
            clock.at = AT_10 + (
                timedelta(minutes=1) if next_run == "next_session" else timedelta(days=1)
            )
            await wait_until(lambda: len(sell_alerts(discord)) == 2)
    if next_run == "restart":
        async with running(FakeRealtime(), kiwoom, data_dir, clock, discord=discord):
            await wait_until(lambda: len(sell_alerts(discord)) == 2)

    assert len(kiwoom.calls("kt10001")) == 2
    failed, accepted = sell_alerts(discord)
    assert failed["embeds"][0]["title"] == "Dead Cross 자동 매도 실패"
    assert "시도" not in fields(failed)
    assert accepted["embeds"][0]["title"] == "Dead Cross 자동 매도 접수"
    assert fields(accepted)["시도"] == "다시 시도"
    assert fields(accepted)["발생 봉"] == "2026-10-26 09:55:00 (한국시간)"


async def test_retry_does_not_sell_when_the_dead_cross_was_released(data_dir):
    write_dc(data_dir)
    clock = Clock()
    chart = chart_at(CROSSING)
    kiwoom = dc_kiwoom(chart).reply("kt10001", kiwoom_error(1, "거부"), SELL_REPLY)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "failed")
        chart.rows["005930"] = minute_rows(
            AT_10 + timedelta(minutes=5), ["200", "200", "90", "100", "100", "100"]
        )
        clock.at = AT_10 + timedelta(days=1)
        body = await until_status(app, lambda b: first(b)["status"] == "tracking")
        await settle()

    assert first(body)["auto_sell"] is None
    assert len(kiwoom.calls("kt10001")) == 1


async def test_retry_waits_for_a_successful_chart(data_dir):
    write_dc(data_dir)
    clock = Clock()
    chart = chart_at(CROSSING)
    kiwoom = dc_kiwoom(chart).reply("kt10001", kiwoom_error(1, "거부"), SELL_REPLY)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "failed")
        chart.failing.add("005930")
        clock.at = AT_10 + timedelta(days=1)
        await until_status(app, lambda b: first(b)["reason"] == "chart_failed")
        await settle()
        assert len(kiwoom.calls("kt10001")) == 1
        chart.failing.clear()
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 2)


async def test_what_is_left_after_an_accepted_sell_is_sold_in_the_next_run(data_dir):
    write_dc(data_dir)
    clock = Clock()
    holdings = {"reply": balance()}
    kiwoom = dc_kiwoom(chart_at(CROSSING)).respond("kt00018", lambda _: holdings["reply"])
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")
        holdings["reply"] = balance(
            position(rmnd_qty="000000000000001", trde_able_qty="000000000000001")
        )
        clock.at = AT_10 + timedelta(days=1)
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 2)

    assert body_of(kiwoom.calls("kt10001")[1])["ord_qty"] == "1"


async def test_stock_that_filled_everything_is_not_tried_again(data_dir):
    write_dc(data_dir)
    clock = Clock()
    holdings = {"reply": balance()}
    kiwoom = dc_kiwoom(chart_at(CROSSING)).respond("kt00018", lambda _: holdings["reply"])
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")
        holdings["reply"] = {**balance(), "acnt_evlt_remn_indv_tot": []}
        clock.at = AT_10 + timedelta(days=1)
        body = await until_status(app, lambda b: b["stocks"] == [] and b["watching"])
        await settle()

    assert body["stocks"] == []
    assert len(kiwoom.calls("kt10001")) == 1


@pytest.mark.parametrize(
    "change", ["session_changed", "basis_changed", "excluded", "released", "stopped"]
)
async def test_sell_is_not_sent_when_things_change_before_sending(data_dir, caplog, change):
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    realtime = FakeRealtime()
    chart = chart_at([*CROSSING, "100"])
    kiwoom = dc_kiwoom(chart)
    # 주문 직전 잔고 재확인을 붙잡는다. 조회 차례의 보유종목 읽기가 첫 번째 호출이다.
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "sending")
        await gate.hit.wait()
        if change == "session_changed":
            clock.at = AT_10.replace(hour=15, minute=31)
            await until_status(app, lambda b: not b["watching"])
        elif change == "basis_changed":
            await put_dc(app, dc_session(long_period=4))
        elif change == "excluded":
            await put_dc(app, dc_session(), excluded=("005930",))
        elif change == "released":
            chart.rows["005930"] = minute_rows(
                AT_10 + timedelta(minutes=5), ["1", "200", "90", "100", "100", "100"]
            )
            clock.at = AT_10 + timedelta(minutes=5, seconds=6)
            await until_status(app, lambda b: first(b)["status"] == "tracking")
        else:
            realtime.connections[0].push(REPLACED)
            await until_status(app, lambda b: b["not_watching_reason"] == "connection_replaced")
        gate.open.set()
        await wait_until(
            lambda: any(r.getMessage() == "dead_cross_send_refused" for r in caplog.records)
        )
        await settle()

    assert kiwoom.calls("kt10001") == []
    reasons = [
        r.fields["reason"] for r in caplog.records if r.getMessage() == "dead_cross_send_refused"
    ]
    assert reasons == [change]


async def test_real_environment_never_sends_dead_cross_orders(data_dir):
    write_dc(data_dir, environment="domestic_real")
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir, environ=REAL_ENV) as app:
        await settle()
        body = await status(app, "domestic_real")

    assert body["not_watching_reason"] == "real"
    assert kiwoom.calls("ka10080") == []
    assert kiwoom.calls("kt10001") == []


async def test_auto_sell_fill_is_marked_and_logs_share_the_request_id(
    data_dir, json_logs: JsonLines
):
    write_dc(data_dir)
    realtime = FakeRealtime()
    discord = FakeDiscord()
    kiwoom = (
        dc_kiwoom(chart_at(CROSSING))
        .reply("ka10075", {"oso": []})
        .reply("ka10077", {"tdy_rlzt_pl": "0", "tdy_rlzt_pl_dtl": []})
    )
    async with running(realtime, kiwoom, data_dir, discord=discord) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")
        await wait_until(lambda: bool(realtime.connections))
        realtime.connections[0].push(
            fill_event(
                **{"9203": "0000138", "905": "-매도", "906": "시장가", "907": "1", "911": "3"}
            )
        )
        await wait_until(
            lambda: any(p["embeds"][0].get("title") == "체결 · 매도" for p in discord.payloads)
        )

    fill = next(p for p in discord.payloads if p["embeds"][0].get("title") == "체결 · 매도")
    assert fill["embeds"][0]["description"] == "Dead Cross 자동 매도"
    lines = {line["event"]: line for line in json_logs.lines}
    request_id = lines["dead_cross_crossed"]["request_id"]
    assert request_id != "-"
    assert lines["dead_cross_auto_sell_started"]["request_id"] == request_id
    assert lines["dead_cross_auto_sell_finished"]["request_id"] == request_id
    assert lines["dead_cross_auto_sell_finished"]["outcome"] == "accepted"
    assert lines["dead_cross_notified"]["sent"] is True


async def test_tracking_stock_remembers_its_purchase_price(data_dir):
    write_dc(data_dir)
    kiwoom = dc_kiwoom(chart_at(["1", "80", "90", "100", "100"]))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, judged)

    assert read_record(data_dir)["stocks"]["005930"]["purchase_price"] == "60000"


async def test_missing_purchase_price_is_filled_without_comparing(data_dir):
    write_dc(data_dir)
    write_record(data_dir, {"005930": remembered(BAR_0955, AT_10, BAR_0955, purchase_price=None)})
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")

    assert read_record(data_dir)["stocks"]["005930"]["purchase_price"] == "60000"


async def test_holding_without_a_purchase_price_is_not_compared(data_dir):
    write_dc(data_dir)
    write_record(data_dir, {"005930": remembered(BAR_0955, AT_10, BAR_0955)})
    kiwoom = dc_kiwoom(chart_at(CROSSING), balance(position(pur_pric="000000000000000")))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")

    assert read_record(data_dir)["stocks"]["005930"]["purchase_price"] == "60000"


@pytest.mark.parametrize(
    "memory",
    [remembered(BAR_0945), remembered(BAR_0945, AT_10, BAR_0945)],
    ids=["tracking", "crossed"],
)
async def test_rebought_while_the_server_was_off_is_not_sold_by_earlier_bars(
    data_dir, memory, json_logs: JsonLines
):
    """재시작 전 09:50 봉에서 하향 돌파했고, 그 뒤 다른 평균 매입가로 다시 샀다."""
    write_dc(data_dir)
    write_record(data_dir, {"005930": memory})
    rebought = balance(position(pur_pric="000000000061000"))
    kiwoom = dc_kiwoom(chart_at(["1", "90", "90", "100", "100", "100"]), rebought)
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, judged)
        await until_status(app, lambda b: first(b)["judged_bar"] == utc(BAR_0955))
        await settle()

    assert first(body)["status"] == "tracking"
    assert first(body)["crossed_bar"] is None
    assert first(body)["short_ma"] == "90.00"
    assert kiwoom.calls("kt10001") == []
    assert read_record(data_dir)["stocks"]["005930"] == remembered(BAR_0955, purchase_price="61000")
    restarted = [
        line for line in json_logs.lines if line["event"] == "dead_cross_judging_restarted"
    ]
    assert [line["purchase_price"] for line in restarted] == ["61000"]


async def test_changed_purchase_price_waits_for_a_successful_chart(data_dir):
    write_dc(data_dir)
    write_record(data_dir, {"005930": remembered(BAR_0945, AT_10, BAR_0945)})
    chart = chart_at(["1", "90", "90", "100", "100", "100"])
    chart.failing.add("005930")
    rebought = balance(position(pur_pric="000000000061000"))
    kiwoom = dc_kiwoom(chart, rebought)
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: first(b)["reason"] == "chart_failed")
        assert read_record(data_dir)["stocks"]["005930"]["purchase_price"] == "60000"
        chart.failing.clear()
        await until_status(app, lambda b: first(b)["judged_bar"] == utc(BAR_0955))
        await settle()

    assert kiwoom.calls("kt10001") == []
    assert read_record(data_dir)["stocks"]["005930"]["purchase_price"] == "61000"


async def test_buying_more_restarts_judging_and_the_next_crossing_sells(data_dir):
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    holdings = {"reply": balance()}
    chart = chart_at(["1", "80", "90", "100", "100"])
    kiwoom = dc_kiwoom(chart).respond("kt00018", lambda _: holdings["reply"])
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, judged)
        holdings["reply"] = balance(position(pur_pric="000000000061000"))
        chart.rows["005930"] = minute_rows(
            AT_10 + timedelta(minutes=5), ["1", "200", "80", "90", "100", "100"]
        )
        clock.at = AT_10 + timedelta(minutes=5, seconds=6)
        await until_status(app, lambda b: first(b)["judged_bar"] == utc(AT_10))
        chart.rows["005930"] = minute_rows(
            AT_10 + timedelta(minutes=15), ["1", "10", "10", "200", "80", "90", "100", "100"]
        )
        clock.at = AT_10 + timedelta(minutes=15, seconds=6)
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 1)


async def test_purchase_price_rechecked_right_before_sending(data_dir, caplog):
    write_dc(data_dir)
    rebought = balance(position(pur_pric="000000000061000"))
    kiwoom = dc_kiwoom(chart_at(CROSSING), balance(), rebought)
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, lambda b: sell_state(b) == "failed")
        await settle()

    assert first(body)["status"] == "tracking"
    assert first(body)["auto_sell"]["reason"] == "cancelled"
    assert kiwoom.calls("kt10001") == []
    reasons = [
        r.fields["reason"] for r in caplog.records if r.getMessage() == "dead_cross_send_refused"
    ]
    assert reasons == ["purchase_price_changed"]
    released = next(r for r in caplog.records if r.getMessage() == "dead_cross_released")
    assert released.fields["previous_purchase_price"] == "60000"
    assert released.fields["purchase_price"] == "61000"


async def test_recent_sells_lists_each_result_and_survives_a_restart(data_dir):
    write_dc(data_dir)
    clock = Clock()
    kiwoom = dc_kiwoom(chart_at(CROSSING)).reply("kt10001", kiwoom_error(1, "거부"), SELL_REPLY)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "failed")
        clock.at = AT_10 + timedelta(days=1)
        body = await until_status(app, lambda b: sell_state(b) == "accepted")
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        restarted = await status(app)

    assert body["recent_sells"] == [
        {
            "code": "005930",
            "name": "삼성전자",
            "crossed_bar": utc(BAR_0955),
            "retry": True,
            "state": "accepted",
            "reason": None,
            "quantity": 3,
            "order_no": "0000138",
            "at": utc(clock.at),
        },
        {
            "code": "005930",
            "name": "삼성전자",
            "crossed_bar": utc(BAR_0955),
            "retry": False,
            "state": "failed",
            "reason": "rejected",
            "quantity": 3,
            "order_no": None,
            "at": utc(AT_10),
        },
    ]
    # 재시작 뒤에는 아직 데드크로스 발생이라 다시 시도가 앞에 더해질 수 있다.
    assert restarted["recent_sells"][-2:] == body["recent_sells"]
    assert read_record(data_dir)["recent_sells"][-2:] == body["recent_sells"]


async def test_recent_sells_keeps_the_latest_ten(data_dir):
    write_dc(data_dir)
    older = [{"code": str(n), "state": "accepted"} for n in range(10)]
    write_record(data_dir, {"005930": remembered(BAR_0950)}, recent_sells=older)
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, lambda b: sell_state(b) == "accepted")

    assert [s["code"] for s in body["recent_sells"]] == ["005930", *map(str, range(9))]


async def test_sell_interrupted_by_a_restart_stays_as_unknown(data_dir):
    write_dc(data_dir)
    kiwoom = dc_kiwoom(chart_at(ALREADY_BELOW))
    sending = dc_kiwoom(chart_at(CROSSING))
    gate = nth_call_gate(sending, "kt10001", 1)
    async with running(FakeRealtime(), gate, data_dir) as app:
        body = await until_status(app, lambda b: sell_state(b) == "sending")
        await gate.hit.wait()
        saved = read_record(data_dir)["recent_sells"]
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        restarted = await until_status(app, judged)

    assert [s["state"] for s in body["recent_sells"]] == ["sending"]
    assert [s["state"] for s in saved] == ["sending"]
    # 재시작 뒤 다시 시도가 앞에 더해질 수 있어, 끊긴 시도는 맨 뒤에서 본다.
    entry = restarted["recent_sells"][-1]
    assert (entry["state"], entry["reason"]) == ("failed", "unknown")


async def test_environment_without_a_watch_has_no_recent_sells(data_dir):
    write_dc(data_dir, environment="domestic_real")
    async with running(
        FakeRealtime(), dc_kiwoom(chart_at(CROSSING)), data_dir, environ=REAL_ENV
    ) as app:
        body = await status(app, "domestic_real")

    assert body["recent_sells"] == []


async def test_failed_holdings_read_still_fetches_every_chart_on_the_retry(data_dir):
    write_dc(data_dir)
    kiwoom = dc_kiwoom(chart_at(CROSSING), kiwoom_error(1, "잔고 조회 실패"), balance())
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: sell_state(b) == "accepted")

    assert len(kiwoom.calls("ka10080")) == 1


async def test_session_change_during_a_round_still_runs_the_new_first_round(data_dir):
    write_dc(data_dir, *TWO_SESSIONS)
    clock = Clock()
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    gate = nth_call_gate(kiwoom, "ka10080", 1)
    async with running(FakeRealtime(), gate, data_dir, clock) as app:
        await gate.hit.wait()
        clock.at = AT_10 + timedelta(minutes=1)
        await until_status(app, lambda b: sell_state(b) == "accepted")

    assert len(kiwoom.calls("kt10001")) == 1


async def test_retry_does_not_sell_on_an_empty_chart(data_dir):
    write_dc(data_dir)
    clock = Clock()
    chart = chart_at(CROSSING)
    rows = chart.rows["005930"]
    kiwoom = dc_kiwoom(chart).reply("kt10001", kiwoom_error(1, "거부"), SELL_REPLY)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, lambda b: sell_state(b) == "failed")
        chart.rows["005930"] = []
        fetched = len(kiwoom.calls("ka10080"))
        clock.at = AT_10 + timedelta(days=1)
        await wait_until(lambda: len(kiwoom.calls("ka10080")) > fetched)
        await settle()
        assert len(kiwoom.calls("kt10001")) == 1
        chart.rows["005930"] = rows
        clock.at = AT_10 + timedelta(days=1, minutes=6)
        await wait_until(lambda: len(kiwoom.calls("kt10001")) == 2)


async def test_purchase_price_changed_outside_the_session_releases_the_dead_cross(data_dir):
    write_dc(data_dir, dc_session("09:00", "09:30"))
    write_record(data_dir, {"005930": remembered(BAR_0945, AT_10, BAR_0945)})
    realtime = FakeRealtime()
    kiwoom = dc_kiwoom(chart_at(CROSSING), balance(position(pur_pric="000000000061000")))
    async with running(realtime, kiwoom, data_dir) as app:
        [connection] = await realtime.wait_registered()
        connection.push(fill_event())
        body = await until_status(app, lambda b: first(b)["crossed_bar"] is None)

    assert first(body)["status"] == "tracking"
    assert kiwoom.calls("kt10001") == []


async def test_a_held_stock_still_sells_when_new_criteria_cross_on_the_latest_bar(data_dir):
    write_dc(data_dir)
    write_record(data_dir, {"005930": remembered(BAR_0950, long_period=4)})
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, lambda b: sell_state(b) == "accepted")

    assert first(body)["crossed_bar"] == utc(BAR_0955)
