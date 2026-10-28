"""SL / TP 트리거가 한꺼번에 몰릴 때: 호출 한도 대기, 잔고 조회 함께 쓰기, 재확인 재시도.

2026-10-26 미국 모의 장 시작 때 트리거 20여 건이 1초 안에 몰렸다. 같은 TR 1.2초 간격 대기가
5초를 넘는 요청은 보내지도 않고 실패해, 기준을 넘은 종목 18건이 팔리지 않았다.
"""

from typing import Any

import httpx
import pytest

from stock_bot.kiwoom import kiwoom as kiwoom_module
from tests.fake_kiwoom import FakeKiwoom, body_of, kiwoom_error, page_response, usa10099_row
from tests.fake_realtime import FakeRealtime
from tests.fill.test_fill_notifications import US_PAPER_ENV, wait_until
from tests.sl_tp.test_sl_tp_auto_sell import (
    balance,
    messages,
    position,
    quote,
    running,
    sl_tp_kiwoom,
    watching,
    write_settings,
)
from tests.sl_tp.test_sl_tp_sessions import nth_call_gate
from tests.sl_tp.test_sl_tp_us import (
    NY_10,
    Clock,
    us_balance,
    us_kiwoom,
    us_position,
    us_quote,
    watching_us,
    write_us_settings,
)

pytestmark = pytest.mark.anyio

CODES = ["000660", "005380", "005930", "035420", "035720", "051910", "068270", "207940"]
TICKERS = ["AAPL", "AMD", "AMZN", "GOOGL", "INTC", "MSFT", "NVDA", "TSLA"]
SERVICE_DELAY = kiwoom_error(20, "[2000](RC9002:모의투자 서비스가 지연되고 있습니다.)")


@pytest.fixture
def real_pacing(monkeypatch):
    """같은 TR 사이에 간격을 두고, 화면용 대기 한도를 아주 짧게 줄인다.

    8건이 몰리면 한도 안에 들어가는 요청은 처음 몇 개뿐이라, 예전처럼 한도를 넘는 요청을
    보내지 않고 실패시키면 대부분이 팔리지 않는다.
    """
    monkeypatch.setattr(kiwoom_module, "CALL_INTERVAL_SECONDS", 0.03)
    monkeypatch.setattr(kiwoom_module, "MAX_PACING_WAIT_SECONDS", 0.05)


def burst(*items: dict[str, Any]) -> dict[str, Any]:
    """여러 종목의 체결가가 한 메시지로 온다."""
    return {"trnm": "REAL", "data": [item["data"][0] for item in items]}


def finished(caplog) -> list[dict[str, Any]]:
    return [r.fields for r in caplog.records if r.getMessage() == "sl_tp_auto_sell_finished"]


async def test_domestic_burst_sells_every_triggered_stock(data_dir, caplog, real_pacing):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(*(position(code) for code in CODES)))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(burst(*(quote(code) for code in CODES)))
        await wait_until(lambda: len(finished(caplog)) == len(CODES))

    assert {f["outcome"] for f in finished(caplog)} == {"accepted"}
    assert sorted(_body_codes(kiwoom.calls("kt10001"))) == CODES
    # 처음 한 번 + 몰린 자동 매도들이 함께 쓴 재확인 몇 번. 종목마다 읽지 않는다.
    assert len(kiwoom.calls("kt00018")) <= 3
    shared = [r.fields["waiters"] for r in caplog.records if r.getMessage() == "balance_shared"]
    assert sum(shared) == len(CODES)
    assert "kiwoom_pacing_rejected" not in messages(caplog)


async def test_us_burst_sells_every_triggered_stock(data_dir, caplog, real_pacing):
    write_us_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = us_kiwoom(us_balance(*(us_position(ticker) for ticker in TICKERS)))
    kiwoom.reply("usa10099", {"list": [_listing(ticker) for ticker in TICKERS]})
    async with running(realtime, kiwoom, data_dir, Clock(NY_10), US_PAPER_ENV):
        connection = await watching_us(realtime)
        connection.push(burst(*(us_quote(ticker) for ticker in TICKERS)))
        await wait_until(lambda: len(finished(caplog)) == len(TICKERS))

    assert {f["outcome"] for f in finished(caplog)} == {"accepted"}
    assert sorted(_body_codes(kiwoom.calls("ust20001"))) == TICKERS
    assert len(kiwoom.calls("ust21070")) <= 3
    assert "kiwoom_pacing_rejected" not in messages(caplog)


async def test_a_read_already_in_flight_is_not_reused_by_a_later_trigger(data_dir, caplog):
    # 진행 중인 조회는 그 뒤에 트리거된 종목의 판정 뒤 잔고를 담지 못할 수 있어 쓰지 않는다.
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom(balance(position("005930"), position("000660")))
    gate = nth_call_gate(kiwoom, "kt00018", 2)
    async with running(realtime, gate, data_dir):
        connection = await watching(realtime)
        connection.push(quote("005930"))
        await gate.hit.wait()
        connection.push(quote("000660"))
        await wait_until(lambda: "auto_sell_requested" in messages(caplog))
        gate.open.set()
        await wait_until(lambda: len(finished(caplog)) == 2)

    # 처음 읽기, 005930의 재확인, 그 뒤에 시작한 000660의 재확인.
    assert len(kiwoom.calls("kt00018")) == 3
    assert len(kiwoom.calls("kt10001")) == 2


def flaky_recheck(*failures: httpx.Response) -> FakeKiwoom:
    """처음 읽기는 성공하고, 주문 전 재확인은 failures를 차례로 돌려준 뒤 성공한다."""
    kiwoom = sl_tp_kiwoom()
    replies = [page_response(balance()), *failures]

    def respond(request: httpx.Request) -> httpx.Response:
        return replies.pop(0) if replies else page_response(balance())

    kiwoom.respond("kt00018", respond)
    return kiwoom


async def test_transient_recheck_failure_is_retried_and_the_sell_goes(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = flaky_recheck(SERVICE_DELAY)
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(finished(caplog)))

    assert finished(caplog)[0]["outcome"] == "accepted"
    assert len(kiwoom.calls("kt10001")) == 1
    retries = [r.fields for r in caplog.records if r.getMessage() == "sellable_check_retry"]
    assert [(r["attempt"], r["kind"]) for r in retries] == [(1, "kiwoom_error")]


async def test_recheck_gives_up_after_three_attempts(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    kiwoom = flaky_recheck(SERVICE_DELAY, SERVICE_DELAY, SERVICE_DELAY)
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(finished(caplog)))

    assert finished(caplog)[0]["outcome"] == "rejected"
    assert len(kiwoom.calls("kt00018")) == 4  # 처음 읽기 + 재확인 3번
    assert kiwoom.calls("kt10001") == []


async def test_non_transient_recheck_failure_is_not_retried(data_dir, caplog):
    write_settings(data_dir)
    realtime = FakeRealtime()
    # 재확인에서 같은 종목 현금잔고가 두 줄로 온다. 다시 읽어도 뜻을 알 수 없다.
    kiwoom = flaky_recheck(page_response(balance(position(), position())))
    async with running(realtime, kiwoom, data_dir):
        connection = await watching(realtime)
        connection.push(quote())
        await wait_until(lambda: bool(finished(caplog)))

    assert finished(caplog)[0]["outcome"] == "rejected"
    assert len(kiwoom.calls("kt00018")) == 2
    assert "sellable_check_retry" not in messages(caplog)


def _body_codes(requests: list[httpx.Request]) -> list[str]:
    return [body_of(r)["stk_cd"] for r in requests]


def _listing(ticker: str) -> dict[str, str]:
    return usa10099_row(ticker, ticker, ticker, "ND")
