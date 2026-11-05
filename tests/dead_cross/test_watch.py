"""Dead Cross 감시(모의): 앱을 띄운 채 차트 응답과 시계를 바꾸며 상태 조회 API로 본다.

이동평균은 단기 2봉, 장기 3봉으로 잰다. closes는 최신 봉부터다.
"""

from datetime import datetime, timedelta

import pytest

from tests.dead_cross.helpers import (
    KST,
    NY,
    Chart,
    daily_rows,
    dc_kiwoom,
    dc_session,
    dc_us_kiwoom,
    first,
    judged,
    minute_rows,
    put_dc,
    read_record,
    record_path,
    remembered,
    status,
    until_status,
    utc,
    write_dc,
    write_legacy_record,
    write_record,
)
from tests.fake_kiwoom import REAL_ENV, US_PAPER_ENV, body_of
from tests.fake_realtime import REPLACED, FakeRealtime
from tests.log_capture import JsonLines
from tests.sl_tp.helpers import (
    AT_10,
    NY_10,
    Clock,
    balance,
    nth_call_gate,
    position,
    running,
    settle,
)
from tests.support import wait_until
from tests.trailing_stop.helpers import rejecting, sell_fill

pytestmark = pytest.mark.anyio

# 10:00에 받는 5분봉. 맨 앞 10:00 봉은 아직 만들어지는 중이다.
IN_PROGRESS = "1"
# 09:55 봉에서 단기(95)가 장기(96.67) 아래로 내려갔다. 09:50 봉은 단기 = 장기 = 100이다.
CROSSING = [IN_PROGRESS, "90", "100", "100", "100"]
# 09:50 봉에서 이미 단기가 장기 아래였다.
ALREADY_BELOW = [IN_PROGRESS, "80", "90", "100", "100"]
BAR_0955 = datetime(2026, 10, 26, 9, 55, tzinfo=KST)
# 세션 시작 조회 뒤, 09:55 봉이 끝난 뒤의 확인(10:00:05)도 지난 시각. 다음 확인은 10:05:05다.
AFTER_FIRST_CHECK = AT_10 + timedelta(seconds=6)


def chart_at(closes: list[str], newest: datetime = AT_10) -> Chart:
    chart = Chart()
    chart.rows["005930"] = minute_rows(newest, closes)
    return chart


async def test_session_fetches_completed_bars_and_shows_moving_averages(data_dir):
    write_dc(data_dir)
    chart = chart_at(["1", "110", "100", "90", "80"])
    kiwoom = dc_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, judged)

    [request] = kiwoom.calls("ka10080")
    assert request.url.host == "mockapi.kiwoom.com"
    assert body_of(request) == {
        "stk_cd": "005930",
        "tic_scope": "5",
        "upd_stkpc_tp": "1",
        "base_dt": "20261026",
    }
    assert body["watching"] is True
    assert body["session"] == dc_session()
    # 진행 중인 10:00 봉(종가 1)은 넣지 않는다.
    assert body["stocks"] == [
        {
            "code": "005930",
            "status": "tracking",
            "reason": None,
            "ineligible_reason": None,
            "short_ma": "105.00",
            "long_ma": "100.00",
            "basis": {"bar_period": "5m", "short_period": 2, "long_period": 3},
            "judged_bar": utc(BAR_0955),
            "crossed_at": None,
            "crossed_bar": None,
        }
    ]
    assert kiwoom.calls("kt10001") == []


async def test_crossing_below_on_the_latest_bar_is_a_dead_cross(data_dir):
    write_dc(data_dir)
    async with running(FakeRealtime(), dc_kiwoom(chart_at(CROSSING)), data_dir) as app:
        body = await until_status(app, judged)

    item = first(body)
    assert item["status"] == "crossed"
    assert item["crossed_bar"] == utc(BAR_0955)
    assert item["crossed_at"] == utc(AT_10)
    assert read_record(data_dir)["stocks"]["005930"] == remembered(
        BAR_0955, crossed_at=AT_10, crossed_bar=BAR_0955, purchase_price=None
    )


async def test_starting_already_below_is_tracking_not_a_dead_cross(data_dir):
    write_dc(data_dir)
    async with running(FakeRealtime(), dc_kiwoom(chart_at(ALREADY_BELOW)), data_dir) as app:
        body = await until_status(app, judged)

    item = first(body)
    assert item["status"] == "tracking"
    assert item["short_ma"] == "85.00"
    assert item["crossed_at"] is None


async def test_bars_closed_after_the_last_judgment_are_judged_in_order(data_dir):
    """10:05 봉 조회 때 10:00 봉이 아직 없었다. 10:10에 두 봉을 차례로 판정해 하향 돌파를 잡는다."""
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    chart = chart_at([IN_PROGRESS, "100", "100", "100", "100"])
    kiwoom = dc_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await until_status(app, judged)
        clock.at = AT_10 + timedelta(minutes=5, seconds=4)
        await settle()
        assert len(kiwoom.calls("ka10080")) == 1
        # 10:05:05 응답에 10:00 봉이 아직 반영되지 않았다.
        chart.rows["005930"] = minute_rows(BAR_0955, ["100", "100", "100", "100"])
        clock.at = AT_10 + timedelta(minutes=5, seconds=5)
        await wait_until(lambda: len(kiwoom.calls("ka10080")) == 2)
        await settle()
        assert first(await status(app))["judged_bar"] == utc(BAR_0955)
        # 10:10에는 10:00 봉(90, 하향 돌파)과 10:05 봉(85, 계속 아래)이 함께 있다.
        chart.rows["005930"] = minute_rows(
            AT_10 + timedelta(minutes=10), [IN_PROGRESS, "85", "90", "100", "100", "100"]
        )
        clock.at = AT_10 + timedelta(minutes=10, seconds=5)
        body = await until_status(app, lambda b: first(b)["status"] == "crossed")

    item = first(body)
    assert item["crossed_bar"] == utc(AT_10)
    assert item["judged_bar"] == utc(AT_10 + timedelta(minutes=5))


async def test_dead_cross_is_released_when_short_rises_to_long(data_dir):
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    chart = chart_at(CROSSING)
    async with running(FakeRealtime(), dc_kiwoom(chart), data_dir, clock) as app:
        await until_status(app, lambda b: judged(b) and first(b)["status"] == "crossed")
        # 10:00 봉 110: 단기 100 = 장기 100.
        chart.rows["005930"] = minute_rows(
            AT_10 + timedelta(minutes=5), [IN_PROGRESS, "110", "90", "100", "100"]
        )
        clock.at = AT_10 + timedelta(minutes=5, seconds=5)
        body = await until_status(app, lambda b: first(b)["status"] == "tracking")

    assert first(body)["crossed_at"] is None
    assert read_record(data_dir)["stocks"]["005930"]["crossed_bar"] is None


async def test_too_few_bars_waits_with_a_reason(data_dir):
    write_dc(data_dir)
    async with running(FakeRealtime(), dc_kiwoom(chart_at(["1", "100", "100"])), data_dir) as app:
        body = await until_status(app, lambda b: first(b) and first(b)["reason"])

    item = first(body)
    assert item["status"] == "waiting"
    assert item["reason"] == "not_enough_bars"
    assert item["short_ma"] is None


async def test_chart_failure_waits_and_retries_within_the_session(data_dir):
    write_dc(data_dir)
    chart = chart_at(CROSSING)
    chart.failing.add("005930")
    kiwoom = dc_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        failed = await until_status(app, lambda b: first(b) and first(b)["reason"])
        chart.failing.clear()
        body = await until_status(app, judged)

    assert failed["stocks"][0]["status"] == "waiting"
    assert failed["stocks"][0]["reason"] == "chart_failed"
    assert first(body)["status"] == "crossed"
    assert first(body)["reason"] is None
    assert kiwoom.calls("kt10001") == []


async def test_chart_failure_keeps_a_dead_cross_and_adds_the_reason(data_dir):
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    chart = chart_at(CROSSING)
    async with running(FakeRealtime(), dc_kiwoom(chart), data_dir, clock) as app:
        await until_status(app, judged)
        chart.failing.add("005930")
        clock.at = AT_10 + timedelta(minutes=5, seconds=5)
        body = await until_status(app, lambda b: first(b)["reason"] == "chart_failed")

    item = first(body)
    assert item["status"] == "crossed"
    assert item["short_ma"] == "95.00"


async def test_excluded_and_ineligible_holdings_are_not_fetched(data_dir):
    write_dc(data_dir, excluded=("000660",))
    chart = chart_at(CROSSING)
    rows = balance(
        position("005930", crd_tp="03"),
        position("005930"),
        position("000660"),
        position("035420"),
        position("035420"),
        position("051910", crd_tp="03"),
        # 매입가가 없어도 이동평균은 계산한다.
        position("068270", pur_pric="000000000000000"),
    )
    chart.rows["068270"] = chart.rows["005930"]
    kiwoom = dc_kiwoom(chart, rows)
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(
            app, lambda b: sum(1 for s in b["stocks"] if s["judged_bar"]) == 2
        )

    by_code = {s["code"]: s for s in body["stocks"]}
    assert sorted(by_code) == ["000660", "005930", "035420", "068270"]
    assert by_code["000660"]["status"] == "waiting"
    assert by_code["000660"]["reason"] == "excluded"
    assert by_code["035420"]["status"] == "ineligible"
    assert by_code["035420"]["ineligible_reason"] == "duplicate"
    assert by_code["005930"]["status"] == "crossed"
    assert by_code["068270"]["status"] == "crossed"
    fetched = sorted(body_of(r)["stk_cd"] for r in kiwoom.calls("ka10080"))
    assert fetched == ["005930", "068270"]


async def test_saving_a_stock_as_excluded_forgets_its_dead_cross(data_dir):
    write_dc(data_dir)
    async with running(FakeRealtime(), dc_kiwoom(chart_at(CROSSING)), data_dir) as app:
        await until_status(app, judged)
        await put_dc(app, dc_session(), excluded=("005930",))
        body = await until_status(app, lambda b: first(b)["reason"] == "excluded")

    assert first(body)["status"] == "waiting"
    assert first(body)["crossed_at"] is None
    assert read_record(data_dir)["stocks"] == {}


async def test_stock_excluded_while_the_server_was_off_is_forgotten_at_start(data_dir):
    write_dc(data_dir, excluded=("005930",))
    write_record(data_dir, {"005930": remembered(BAR_0955, AT_10, BAR_0955)})
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, lambda b: b["watching"])

    assert first(body)["status"] == "waiting"
    assert first(body)["reason"] == "excluded"
    assert read_record(data_dir)["stocks"] == {}
    assert kiwoom.calls("ka10080") == []


async def test_selling_out_during_the_session_forgets_the_stock(data_dir):
    write_dc(data_dir)
    realtime = FakeRealtime()
    kiwoom = dc_kiwoom(chart_at(CROSSING), balance(), balance(position("000660")))
    async with running(realtime, kiwoom, data_dir) as app:
        await until_status(app, judged)
        [connection] = await realtime.wait_registered()
        connection.push(sell_fill())
        body = await until_status(app, lambda b: all(s["code"] != "005930" for s in b["stocks"]))

    assert "005930" not in read_record(data_dir)["stocks"]
    assert [s["code"] for s in body["stocks"]] == ["000660"]


async def test_selling_out_after_the_session_forgets_the_dead_cross(data_dir):
    write_dc(data_dir, dc_session("09:00", "10:01"))
    realtime = FakeRealtime()
    clock = Clock(AT_10)
    kiwoom = dc_kiwoom(chart_at(CROSSING), balance(), balance(position("000660")))
    async with running(realtime, kiwoom, data_dir, clock) as app:
        await until_status(app, judged)
        clock.at = AT_10 + timedelta(minutes=2)
        outside = await until_status(app, lambda b: b["not_watching_reason"] == "no_session")
        [connection] = await realtime.wait_registered()
        connection.push(sell_fill())
        await wait_until(lambda: read_record(data_dir)["stocks"] == {})
        body = await status(app)

    assert first(outside)["status"] == "crossed"
    assert first(outside)["reason"] == "no_session"
    assert body["stocks"] == []
    async with running(FakeRealtime(), dc_kiwoom(Chart()), data_dir, clock) as app:
        assert (await status(app))["stocks"] == []


async def test_dead_cross_survives_a_restart_and_values_come_back(data_dir):
    write_dc(data_dir)
    async with running(FakeRealtime(), dc_kiwoom(chart_at(CROSSING)), data_dir) as app:
        await until_status(app, judged)
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, lambda b: first(b) and first(b)["short_ma"])

    item = first(body)
    assert item["status"] == "crossed"
    assert item["crossed_at"] == utc(AT_10)
    assert item["short_ma"] == "95.00"
    assert item["judged_bar"] == utc(BAR_0955)


async def test_bars_closed_outside_the_session_are_judged_at_the_next_session(data_dir):
    write_dc(data_dir)
    # 앞 날 15:25 봉까지 판정했다. 그 뒤 시간외 봉(15:30~)에서 하향 돌파가 일어났다.
    last = datetime(2026, 10, 23, 15, 25, tzinfo=KST)
    write_record(data_dir, {"005930": remembered(last)})
    chart = Chart()
    chart.rows["005930"] = minute_rows(AT_10, [IN_PROGRESS, "70", "75"]) + minute_rows(
        datetime(2026, 10, 23, 15, 35, tzinfo=KST), ["80", "90", "100", "100", "100"]
    )
    async with running(FakeRealtime(), dc_kiwoom(chart), data_dir) as app:
        body = await until_status(app, lambda b: judged(b) and first(b)["judged_bar"] != utc(last))

    item = first(body)
    assert item["status"] == "crossed"
    assert item["crossed_bar"] == utc(datetime(2026, 10, 23, 15, 30, tzinfo=KST))
    assert item["judged_bar"] == utc(BAR_0955)


async def test_catching_up_fetches_pages_until_the_last_judged_bar(data_dir):
    write_dc(data_dir)
    last = AT_10 - timedelta(minutes=30)
    write_record(data_dir, {"005930": remembered(last)})
    chart = Chart(page_size=3)
    chart.rows["005930"] = minute_rows(AT_10, [IN_PROGRESS] + ["100"] * 12)
    kiwoom = dc_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, lambda b: judged(b) and first(b)["judged_bar"] == utc(BAR_0955))

    # 09:30 봉 뒤 다섯 봉(09:35~09:55)과, 그 첫 봉의 직전 봉 장기 이동평균에 쓰는 3봉:
    # 완료 봉 8개 = 진행 중인 봉까지 행 9개 = 3페이지. 남은 행은 받지 않는다.
    assert len(kiwoom.calls("ka10080")) == 3


async def test_history_ending_before_the_last_judged_bar_judges_the_latest_only(data_dir, caplog):
    write_dc(data_dir)
    write_record(data_dir, {"005930": remembered(AT_10 - timedelta(days=30))})
    async with running(FakeRealtime(), dc_kiwoom(chart_at(CROSSING)), data_dir) as app:
        body = await until_status(app, lambda b: first(b) and first(b)["status"] == "crossed")

    assert first(body)["judged_bar"] == utc(BAR_0955)
    assert "dead_cross_catch_up_truncated" in [r.getMessage() for r in caplog.records]


async def test_changing_the_periods_recomputes_and_keeps_the_dead_cross(data_dir):
    write_dc(data_dir)
    chart = chart_at([IN_PROGRESS, "90", "100", "100", "100", "100"])
    async with running(FakeRealtime(), dc_kiwoom(chart), data_dir) as app:
        await until_status(app, lambda b: judged(b) and first(b)["status"] == "crossed")
        await put_dc(app, dc_session(short_period=3, long_period=4))
        body = await until_status(app, lambda b: first(b)["basis"]["long_period"] == 4)

    item = first(body)
    assert item["status"] == "crossed"
    assert item["short_ma"] == "96.67"
    assert item["long_ma"] == "97.50"
    assert read_record(data_dir)["stocks"]["005930"]["judged_bar"]["long_period"] == 4


async def test_failed_fetch_after_a_settings_change_keeps_the_old_basis(data_dir):
    write_dc(data_dir)
    chart = chart_at(CROSSING)
    async with running(FakeRealtime(), dc_kiwoom(chart), data_dir) as app:
        await until_status(app, judged)
        chart.failing.add("005930")
        await put_dc(app, dc_session(bar_period="10m"))
        body = await until_status(app, lambda b: first(b)["reason"] == "chart_failed")

    assert body["session"]["bar_period"] == "10m"
    assert first(body)["basis"]["bar_period"] == "5m"


async def test_response_arriving_after_exclusion_is_discarded(data_dir):
    write_dc(data_dir)
    chart = chart_at(CROSSING)
    gate = nth_call_gate(dc_kiwoom(chart), "ka10080", 1)
    async with running(FakeRealtime(), gate, data_dir) as app:
        await gate.hit.wait()
        await put_dc(app, dc_session(), excluded=("005930",))
        gate.open.set()
        body = await until_status(app, lambda b: first(b) and first(b)["reason"] == "excluded")
        await settle()

    assert first(body)["status"] == "waiting"
    assert not record_path(data_dir).exists() or read_record(data_dir)["stocks"] == {}


async def test_rounds_do_not_overlap_when_a_fetch_outlasts_the_bar(data_dir):
    write_dc(data_dir)
    clock = Clock(AFTER_FIRST_CHECK)
    chart = chart_at(CROSSING)
    kiwoom = dc_kiwoom(chart)
    gate = nth_call_gate(kiwoom, "ka10080", 1)
    async with running(FakeRealtime(), gate, data_dir, clock) as app:
        await gate.hit.wait()
        for minutes in (5, 10, 15):
            clock.at = AT_10 + timedelta(minutes=minutes, seconds=5)
            await settle()
        # 첫 조회가 붙잡힌 동안 다른 조회가 나가지 않는다.
        assert kiwoom.calls("ka10080") == []
        gate.open.set()
        await until_status(app, judged)
        await wait_until(lambda: len(kiwoom.calls("ka10080")) == 2)
        await settle()

    # 붙잡힌 동안 끝난 봉 셋은 한 차례로 합쳐진다.
    assert len(kiwoom.calls("ka10080")) == 2


async def test_us_minute_bars_use_the_exchange_and_new_york_time(data_dir):
    write_dc(data_dir, dc_session("09:30", "16:00", bar_period="1m"), environment="us_paper")
    newest = NY_10.astimezone(NY)
    chart = Chart(list_key="result_list", page_size=100)
    chart.rows["AAPL"] = minute_rows(newest, ["1", "190.5", "200", "200", "200"], 1, sign="")
    kiwoom = dc_us_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir, Clock(NY_10), US_PAPER_ENV) as app:
        body = await until_status(app, judged, "us_paper")

    assert body_of(kiwoom.calls("usa06011")[0]) == {
        "stex_tp": "ND",
        "stk_cd": "AAPL",
        "strt_dt": "20261026",
        "upd_stkpc_tp": "1",
        "exrt_appl_tp": "0",
        "tic_scope": "1",
    }
    item = first(body)
    assert item["status"] == "crossed"
    assert item["short_ma"] == "195.2500"
    assert item["long_ma"] == "196.8333"
    assert body["timezone"] == "America/New_York"


async def test_us_night_bars_after_midnight_read_as_the_next_day(data_dir):
    """미국 분봉은 다음 날 새벽을 24시 넘는 시각으로 적는다(20261026 27:00 = 10/27 03:00).

    10/27 02:00 봉까지 판정했다. 03:00 야간 봉에서 일어난 하향 돌파를 다음 세션에서 잡는다.
    """
    write_dc(data_dir, dc_session("09:30", "16:00", bar_period="60m"), environment="us_paper")
    night = datetime(2026, 10, 27, 2, 0, tzinfo=NY)
    write_record(data_dir, {"AAPL": remembered(night, bar_period="60m")}, "us_paper")
    chart = Chart(list_key="result_list", page_size=100)
    chart.rows["AAPL"] = [
        {"cntr_tm": "20261027100000", "cur_prc": "1"},
        {"cntr_tm": "20261027090000", "cur_prc": "85"},
        {"cntr_tm": "20261026270000", "cur_prc": "90"},
        {"cntr_tm": "20261026260000", "cur_prc": "100"},
        {"cntr_tm": "20261026250000", "cur_prc": "100"},
        {"cntr_tm": "20261026240000", "cur_prc": "100"},
    ]
    at = Clock(datetime(2026, 10, 27, 10, 30, tzinfo=NY))
    async with running(FakeRealtime(), dc_us_kiwoom(chart), data_dir, at, US_PAPER_ENV) as app:
        body = await until_status(
            app, lambda b: first(b) and first(b)["status"] == "crossed", "us_paper"
        )

    item = first(body)
    assert item["crossed_bar"] == utc(datetime(2026, 10, 27, 3, 0, tzinfo=NY))
    assert item["judged_bar"] == utc(datetime(2026, 10, 27, 9, 0, tzinfo=NY))


async def test_us_fetch_continues_to_a_second_page_for_long_periods(data_dir):
    write_dc(
        data_dir,
        dc_session("09:30", "16:00", bar_period="1m", short_period=5, long_period=120),
        environment="us_paper",
    )
    newest = NY_10.astimezone(NY)
    chart = Chart(list_key="result_list", page_size=100)
    chart.rows["AAPL"] = minute_rows(newest, ["1"] + ["200"] * 150, 1, sign="")
    kiwoom = dc_us_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir, Clock(NY_10), US_PAPER_ENV) as app:
        await until_status(app, judged, "us_paper")

    assert len(kiwoom.calls("usa06011")) == 2


async def test_daily_bars_leave_out_today(data_dir):
    write_dc(data_dir, dc_session(bar_period="1d"))
    chart = Chart(list_key="stk_dt_pole_chart_qry", page_size=600)
    # 오늘(10/26) 행은 아직 끝나지 않은 봉이다.
    chart.rows["005930"] = daily_rows(AT_10, ["1", "90", "100", "100", "100"])
    kiwoom = dc_kiwoom(chart, api_id="ka10081")
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, judged)

    assert body_of(kiwoom.calls("ka10081")[0]) == {
        "stk_cd": "005930",
        "base_dt": "20261026",
        "upd_stkpc_tp": "1",
    }
    item = first(body)
    assert item["status"] == "crossed"
    assert item["judged_bar"] == utc(datetime(2026, 10, 25, tzinfo=KST))


async def test_connection_replaced_stops_the_watch(data_dir):
    write_dc(data_dir)
    realtime = FakeRealtime()
    async with running(realtime, dc_kiwoom(chart_at(CROSSING)), data_dir) as app:
        await until_status(app, judged)
        [connection] = await realtime.wait_registered()
        connection.push(REPLACED)
        body = await until_status(app, lambda b: not b["watching"])

    assert body["not_watching_reason"] == "connection_replaced"
    assert first(body)["status"] == "crossed"
    assert first(body)["reason"] == "connection_replaced"


async def test_rejected_fill_registration_waits_until_registered(data_dir):
    write_dc(data_dir)
    flag = {"on": True}
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(rejecting("1", flag), kiwoom, data_dir) as app:
        rejected = await until_status(app, lambda b: b["not_watching_reason"] == "fills_rejected")
        flag["on"] = False
        body = await until_status(app, judged)

    assert all(s["reason"] == "fills_rejected" for s in rejected["stocks"])
    assert first(body)["status"] == "crossed"


async def test_real_environment_is_never_watched(data_dir):
    write_dc(data_dir, environment="domestic_real")
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir, environ=REAL_ENV) as app:
        await settle()
        body = await status(app, "domestic_real")

    assert body["not_watching_reason"] == "real"
    assert body["stocks"] == []
    assert kiwoom.calls("ka10080") == []


async def test_unreadable_record_stops_judging_until_settings_are_saved(data_dir):
    write_dc(data_dir)
    record_path(data_dir).parent.mkdir(parents=True)
    record_path(data_dir).write_text("{broken")
    kiwoom = dc_kiwoom(chart_at(CROSSING))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, lambda b: b["not_watching_reason"] == "state_unreadable")
        assert kiwoom.calls("ka10080") == []
        record_path(data_dir).unlink()
        await put_dc(app, dc_session())
        recovered = await until_status(app, judged)

    assert body["stocks"] == []
    assert first(recovered)["status"] == "crossed"


async def test_logs_carry_request_ids_and_moving_averages(data_dir, json_logs: JsonLines):
    write_dc(data_dir)
    async with running(FakeRealtime(), dc_kiwoom(chart_at(CROSSING)), data_dir) as app:
        await until_status(app, judged)

    events = {line["event"]: line for line in json_logs.lines}
    fetched, crossed = events["dead_cross_chart_fetched"], events["dead_cross_crossed"]
    assert fetched["env"] == crossed["env"] == "domestic_paper"
    assert fetched["request_id"] != "-"
    assert {k: fetched[k] for k in ("api_id", "code", "bar_period", "pages", "completed")} == {
        "api_id": "ka10080",
        "code": "005930",
        "bar_period": "5m",
        "pages": 1,
        "completed": 4,
    }
    assert {k: crossed[k] for k in ("code", "bar", "short_period", "long_period")} == {
        "code": "005930",
        "bar": utc(BAR_0955),
        "short_period": 2,
        "long_period": 3,
    }


async def test_legacy_record_is_cleared_once_and_its_dead_cross_does_not_carry_over(
    data_dir, json_logs: JsonLines
):
    write_dc(data_dir)
    write_legacy_record(
        data_dir, {"005930": remembered(BAR_0955, crossed_at=AT_10, crossed_bar=BAR_0955)}
    )
    kiwoom = dc_kiwoom(chart_at(ALREADY_BELOW))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, judged)
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        await until_status(app, judged)

    assert first(body)["status"] == "tracking"
    assert read_record(data_dir)["version"] == 2
    migrated = [line for line in json_logs.lines if line["event"] == "dead_cross_record_migrated"]
    assert [line["cleared_codes"] for line in migrated] == ['["005930"]']


async def test_memory_survives_a_restart_on_the_current_record_version(data_dir):
    write_dc(data_dir)
    write_record(
        data_dir,
        {"005930": remembered(BAR_0955, crossed_at=AT_10, crossed_bar=BAR_0955)},
    )
    kiwoom = dc_kiwoom(chart_at(ALREADY_BELOW))
    async with running(FakeRealtime(), kiwoom, data_dir) as app:
        body = await until_status(app, judged)

    assert first(body)["status"] == "crossed"


async def test_no_session_on_weekends_then_monday_judges_the_bars_before_it(data_dir):
    write_dc(data_dir)
    saturday = datetime(2026, 10, 31, 10, 0, tzinfo=KST)
    monday = datetime(2026, 11, 2, 9, 0, 6, tzinfo=KST)
    friday_bar = datetime(2026, 10, 30, 15, 25, tzinfo=KST)
    write_record(data_dir, {"005930": remembered(friday_bar - timedelta(minutes=5))})
    chart = chart_at(["90", "100", "100", "100"], friday_bar)
    clock = Clock(saturday)
    kiwoom = dc_kiwoom(chart)
    async with running(FakeRealtime(), kiwoom, data_dir, clock) as app:
        await settle()
        weekend = await status(app)
        clock.at = saturday + timedelta(days=1)
        await settle()
        assert await status(app) == weekend
        assert kiwoom.calls("ka10080") == []
        clock.at = monday
        body = await until_status(app, lambda b: first(b)["status"] == "crossed")

    assert weekend["watching"] is False
    assert weekend["not_watching_reason"] == "no_session"
    assert weekend["session"] is None
    assert first(body)["crossed_bar"] == utc(friday_bar)
