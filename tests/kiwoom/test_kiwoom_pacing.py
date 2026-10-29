"""투자 환경별 호출 간격 맞추기. 가짜 시계로 실제로 기다리지 않고 검증한다.

모의투자와 토큰 발급은 같은 TR 1회씩, 실전투자는 시장·요청 구분별 묶음 한도를 따른다.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta

import httpx
import pytest

from stock_bot.core.config import ENVIRONMENTS, Environment
from stock_bot.kiwoom import kiwoom
from stock_bot.kiwoom.kiwoom import KiwoomClient
from stock_bot.order import order
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom, body_of, kiwoom_error, page_response

DOMESTIC_PAPER = ENVIRONMENTS[Environment.DOMESTIC_PAPER]
# 실전 주문 한도를 보려고 주문 허용을 켠다. 요청은 가짜 서버로만 간다.
DOMESTIC_REAL = dataclasses.replace(ENVIRONMENTS[Environment.DOMESTIC_REAL], orders_allowed=True)
US_REAL = dataclasses.replace(ENVIRONMENTS[Environment.US_REAL], orders_allowed=True)
PATH = "/api/dostk/acnt"
INTERVAL = 1.2
# 피크타임(한국시간 9시대)이 아닌 기본 시각.
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=kiwoom.KST)


class FakeClock:
    """sleep이 실제로 기다리지 않고 시계를 깨어날 시각으로 옮긴다."""

    def __init__(self, kst_start: datetime = NOON) -> None:
        self.now = 0.0
        self.kst_start = kst_start
        # 다음 sleep이 시작될 때 한 번 실행한다. 기다리는 도중에 일어나는 일을 흉내 낼 때 쓴다.
        self.on_sleep: Callable[[], Awaitable[None]] | None = None

    def __call__(self) -> float:
        return self.now

    def kst(self) -> datetime:
        return self.kst_start + timedelta(seconds=self.now)

    async def sleep(self, seconds: float) -> None:
        wake = self.now + seconds
        if self.on_sleep is not None:
            hook, self.on_sleep = self.on_sleep, None
            await hook()
        await asyncio.sleep(0)
        self.now = max(self.now, wake)


@pytest.fixture(autouse=True)
def real_interval(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(kiwoom, "CALL_INTERVAL_SECONDS", INTERVAL)
    yield


def make(fake: FakeKiwoom, clock: FakeClock) -> tuple[KiwoomClient, list[tuple[str, float]]]:
    sent: list[tuple[str, float]] = []

    def record(request: httpx.Request) -> None:
        sent.append((request.headers.get("api-id", "au10001"), clock()))

    fake.on_request = record
    client = KiwoomClient(
        FAKE_ENV,
        transport=httpx.MockTransport(fake),
        clock=clock,
        sleep=clock.sleep,
        kst_now=clock.kst,
    )
    return client, sent


def times(sent: list[tuple[str, float]], api_id: str) -> list[float]:
    return [round(at, 6) for sent_id, at in sent if sent_id == api_id]


def pacing_logs(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, object]]:
    return [r.fields for r in caplog.records if r.getMessage() == event]  # type: ignore[attr-defined]


# 서로 다른 국내 조회 TR.
INQUIRY_TRS = ["ka00001", "kt00018", "kt00001", "ka10075", "ka10077", "ka10001"]


def fake_replying(*api_ids: str) -> FakeKiwoom:
    fake = FakeKiwoom()
    for api_id in api_ids:
        fake.reply(api_id, {"rows": []})
    return fake


@pytest.mark.anyio
async def test_paper_same_tr_twice_is_sent_one_interval_apart():
    clock = FakeClock()
    client, sent = make(FakeKiwoom().reply("ka00001", {"acctNo": "1"}), clock)

    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})
    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})

    assert times(sent, "ka00001") == [0.0, 1.2]


@pytest.mark.anyio
async def test_paper_different_tr_or_credentials_do_not_wait():
    clock = FakeClock()
    fake = FakeKiwoom().reply("ka00001", {"acctNo": "1"}).reply("kt00001", {"entr": "0"})
    client, sent = make(fake, clock)

    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})
    await client.call(DOMESTIC_PAPER, "kt00001", PATH, {})
    await client.call(ENVIRONMENTS[Environment.US_PAPER], "ka00001", PATH, {})

    assert times(sent, "ka00001") == [0.0, 0.0]
    assert times(sent, "kt00001") == [0.0]


@pytest.mark.anyio
async def test_ka00001_from_us_real_counts_as_domestic_inquiry():
    clock = FakeClock()
    fake = FakeKiwoom().reply("ka00001", {"acctNo": "1"}).reply("ust21070", {"rows": []})
    client, sent = make(fake, clock)

    await asyncio.gather(
        client.call(DOMESTIC_REAL, "ka00001", PATH, {}),
        client.call(US_REAL, "ka00001", PATH, {}),
        *(client.call(DOMESTIC_REAL, "ka00001", PATH, {}) for _ in range(4)),
        *(client.call(US_REAL, "ust21070", "/api/us/acnt", {}) for _ in range(5)),
    )

    assert times(sent, "ka00001") == [0.0] * 5 + [1.2]
    assert times(sent, "ust21070") == [0.0] * 5


@pytest.mark.anyio
async def test_queued_requests_go_out_in_arrival_order():
    clock = FakeClock()
    fake = FakeKiwoom().reply("ka00001", {"acctNo": "1"})
    client, _ = make(fake, clock)
    order: list[tuple[str, float]] = []
    fake.on_request = lambda r: order.append((body_of(r).get("tag", ""), clock()))

    await asyncio.gather(
        *(client.call(DOMESTIC_PAPER, "ka00001", PATH, {"tag": tag}) for tag in "abc")
    )

    assert [(tag, round(at, 6)) for tag, at in order if tag] == [
        ("a", 0.0),
        ("b", 1.2),
        ("c", 2.4),
    ]


@pytest.mark.anyio
async def test_request_needing_more_than_five_seconds_is_rejected_without_sending(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock()
    client, sent = make(FakeKiwoom().reply("ka00001", {"acctNo": "1"}), clock)

    results = await asyncio.gather(
        *(client.call(DOMESTIC_PAPER, "ka00001", PATH, {}) for _ in range(6)),
        return_exceptions=True,
    )

    assert times(sent, "ka00001") == [0.0, 1.2, 2.4, 3.6, 4.8]
    rejected = results[5]
    assert isinstance(rejected, kiwoom.AppError)
    assert (rejected.kind, rejected.status) == ("rate_limited", 429)
    assert len(pacing_logs(caplog, "kiwoom_pacing_wait")) == 4
    assert pacing_logs(caplog, "kiwoom_pacing_rejected") == [
        {"api_id": "ka00001", "wait_ms": 6000, "pacing_unit": "tr"}
    ]

    # 거부된 요청은 자리를 차지하지 않으므로 다음 요청은 6초에 나간다.
    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})
    assert times(sent, "ka00001")[-1] == 6.0


@pytest.mark.anyio
async def test_continuation_page_waits_like_any_request():
    """모의 서버는 1페이지 0.4초 뒤에 보낸 다음 페이지를 1700으로 거부했다."""
    clock = FakeClock()
    fake = FakeKiwoom().reply(
        "kt00018",
        page_response({"rows": [1]}, cont_yn="Y", next_key="p2"),
        page_response({"rows": [2]}),
    )
    client, sent = make(fake, clock)

    data = await client.call(DOMESTIC_PAPER, "kt00018", PATH, {})

    assert data["rows"] == [1, 2]
    assert times(sent, "kt00018") == [0.0, 1.2]


@pytest.mark.anyio
async def test_retry_of_a_continuation_page_after_invalid_token_waits():
    clock = FakeClock()
    fake = (
        FakeKiwoom()
        .reply("ka00001", {"acctNo": "1"})
        .reply(
            "kt00018",
            page_response({"rows": [1]}, cont_yn="Y", next_key="p2"),
            kiwoom_error(8005, "Token이 유효하지 않습니다"),
            page_response({"rows": [2]}),
        )
    )
    client, sent = make(fake, clock)
    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})  # 토큰을 미리 받아 둔다.
    clock.now = 5.0

    await client.call(DOMESTIC_PAPER, "kt00018", PATH, {})

    assert times(sent, "au10001") == [0.0, 6.2]
    assert times(sent, "kt00018") == [5.0, 6.2, 7.4]


@pytest.mark.anyio
async def test_orders_are_paced_and_never_resent_after_a_rate_limit_error():
    clock = FakeClock()
    fake = FakeKiwoom().reply(
        "kt10000",
        {"ord_no": "0000001"},
        kiwoom_error(5, "허용된 요청 개수를 초과하였습니다[1700:허용된 API 요청 개수]", 429),
    )
    client, sent = make(fake, clock)

    await client.call_once(DOMESTIC_PAPER, "kt10000", "/api/dostk/ordr", {})
    with pytest.raises(kiwoom.AppError) as raised:
        await client.call_once(DOMESTIC_PAPER, "kt10000", "/api/dostk/ordr", {})

    assert raised.value.kind == "rate_limited"
    assert times(sent, "kt10000") == [0.0, 1.2]


@pytest.mark.anyio
async def test_request_that_does_not_wait_leaves_no_wait_log(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")

    class TickingClock(FakeClock):
        """실제 시계처럼 읽을 때마다 조금씩 흐른다."""

        def __call__(self) -> float:
            self.now += 0.000001
            return self.now

    client, _ = make(FakeKiwoom().reply("ka00001", {"acctNo": "1"}), TickingClock())

    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})

    assert "kiwoom_pacing_wait" not in [r.getMessage() for r in caplog.records]


@pytest.mark.anyio
async def test_domestic_real_sixth_inquiry_in_an_interval_waits():
    clock = FakeClock()
    client, sent = make(fake_replying(*INQUIRY_TRS), clock)

    await asyncio.gather(*(client.call(DOMESTIC_REAL, tr, PATH, {}) for tr in INQUIRY_TRS))

    assert [at for tr in INQUIRY_TRS for at in times(sent, tr)] == [0.0] * 5 + [1.2]


@pytest.mark.anyio
async def test_domestic_real_same_inquiry_twice_does_not_wait():
    clock = FakeClock()
    client, sent = make(fake_replying("ka00001"), clock)

    await client.call(DOMESTIC_REAL, "ka00001", PATH, {})
    await client.call(DOMESTIC_REAL, "ka00001", PATH, {})

    assert times(sent, "ka00001") == [0.0, 0.0]


@pytest.mark.anyio
async def test_domestic_real_orders_and_inquiries_do_not_wait_for_each_other():
    clock = FakeClock()
    client, sent = make(fake_replying("ka00001", "kt10000", "kt10003"), clock)

    await asyncio.gather(
        *(client.call(DOMESTIC_REAL, "ka00001", PATH, {}) for _ in range(5)),
        *(client.call_once(DOMESTIC_REAL, "kt10000", "/api/dostk/ordr", {}) for _ in range(4)),
        client.call_once(DOMESTIC_REAL, "kt10003", "/api/dostk/ordr", {}),
    )

    assert times(sent, "ka00001") == [0.0] * 5
    assert times(sent, "kt10000") + times(sent, "kt10003") == [0.0] * 5


@pytest.mark.anyio
async def test_us_real_sends_ten_orders_and_five_inquiries_per_interval():
    clock = FakeClock()
    client, sent = make(fake_replying("ust20000", "ust21070"), clock)

    await asyncio.gather(
        *(client.call_once(US_REAL, "ust20000", "/api/us/ordr", {}) for _ in range(11)),
        *(client.call(US_REAL, "ust21070", "/api/us/acnt", {}) for _ in range(6)),
    )

    assert times(sent, "ust20000") == [0.0] * 10 + [1.2]
    assert times(sent, "ust21070") == [0.0] * 5 + [1.2]


@pytest.mark.anyio
async def test_real_token_issuance_is_one_per_interval():
    clock = FakeClock()
    client, sent = make(FakeKiwoom(), clock)

    token = await client.access_token(DOMESTIC_REAL)
    await client.discard_access_token(DOMESTIC_REAL, token)
    await client.access_token(US_REAL)

    assert times(sent, "au10001") == [0.0, 1.2]


@pytest.mark.anyio
async def test_real_request_needing_more_than_five_seconds_is_rejected(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock()
    client, sent = make(fake_replying("ka00001"), clock)
    await client.access_token(DOMESTIC_REAL)

    results = await asyncio.gather(
        *(client.call(DOMESTIC_REAL, "ka00001", PATH, {}) for _ in range(26)),
        return_exceptions=True,
    )

    assert times(sent, "ka00001") == [round(INTERVAL * (i // 5), 6) for i in range(25)]
    rejected = results[25]
    assert isinstance(rejected, kiwoom.AppError)
    assert (rejected.kind, rejected.status) == ("rate_limited", 429)
    assert pacing_logs(caplog, "kiwoom_pacing_rejected") == [
        {"api_id": "ka00001", "wait_ms": 6000, "pacing_unit": "domestic_inquiry"}
    ]
    assert {log["pacing_unit"] for log in pacing_logs(caplog, "kiwoom_pacing_wait")} == {
        "domestic_inquiry"
    }

    # 거부된 요청은 자리를 차지하지 않으므로 다음 5개는 모두 6초에 나간다.
    await asyncio.gather(*(client.call(DOMESTIC_REAL, "ka00001", PATH, {}) for _ in range(5)))
    assert times(sent, "ka00001")[25:] == [6.0] * 5


def test_order_features_use_only_order_trs():
    used = {api_id for ids in order.ORDER_API_IDS.values() for api_id in ids.values()}
    used |= set(order.CANCEL_API_IDS.values())

    assert used <= kiwoom.ORDER_TR_IDS


@pytest.mark.anyio
async def test_us_real_peak_time_allows_three_orders_and_inquiries(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock(datetime(2026, 10, 26, 9, 30, tzinfo=kiwoom.KST))
    client, sent = make(fake_replying("ust20000", "ust21070", "ka00001"), clock)
    await client.access_token(US_REAL)

    await asyncio.gather(
        *(client.call_once(US_REAL, "ust20000", "/api/us/ordr", {}) for _ in range(4)),
        *(client.call(US_REAL, "ust21070", "/api/us/acnt", {}) for _ in range(4)),
        *(client.call(DOMESTIC_REAL, "ka00001", PATH, {}) for _ in range(5)),
    )

    assert times(sent, "ust20000") == [0.0] * 3 + [1.2]
    assert times(sent, "ust21070") == [0.0] * 3 + [1.2]
    assert times(sent, "ka00001") == [0.0] * 5
    waits = pacing_logs(caplog, "kiwoom_pacing_wait")
    assert sorted((log["pacing_unit"], log.get("peak")) for log in waits) == [
        ("us_inquiry", True),
        ("us_order", True),
    ]


@pytest.mark.anyio
async def test_us_real_limit_rises_when_peak_time_ends(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock(datetime(2026, 10, 26, 9, 59, 59, tzinfo=kiwoom.KST))
    client, sent = make(fake_replying("ust21070"), clock)
    await client.access_token(US_REAL)

    await asyncio.gather(*(client.call(US_REAL, "ust21070", "/api/us/acnt", {}) for _ in range(6)))

    # 09:59:59에는 3회, 10:00:00부터는 5회까지 보낸다.
    assert times(sent, "ust21070") == [0.0] * 3 + [1.0, 1.0, 1.2]
    # 피크타임 한도 때문에 기다렸으므로 10시 넘어 나가도 peak를 남긴다.
    assert [log.get("peak") for log in pacing_logs(caplog, "kiwoom_pacing_wait")] == [True] * 3


@pytest.mark.anyio
async def test_queued_us_real_requests_follow_the_peak_limit_once_it_starts(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock(datetime(2026, 10, 26, 8, 59, 59, tzinfo=kiwoom.KST))
    client, sent = make(fake_replying("ust21070"), clock)
    await client.access_token(US_REAL)

    await asyncio.gather(*(client.call(US_REAL, "ust21070", "/api/us/acnt", {}) for _ in range(9)))

    # 08:59:59에는 5회, 09:00:00.2부터는 피크타임 한도 3회를 따른다.
    assert times(sent, "ust21070") == [0.0] * 5 + [1.2] * 3 + [2.4]
    assert {log.get("peak") for log in pacing_logs(caplog, "kiwoom_pacing_wait")} == {True}


@pytest.mark.anyio
async def test_paper_wait_log_names_the_tr_unit(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock(datetime(2026, 10, 26, 9, 30, tzinfo=kiwoom.KST))
    client, _ = make(fake_replying("ka00001"), clock)

    await client.call(ENVIRONMENTS[Environment.US_PAPER], "ka00001", PATH, {})
    await client.call(ENVIRONMENTS[Environment.US_PAPER], "ka00001", PATH, {})

    assert pacing_logs(caplog, "kiwoom_pacing_wait") == [
        {"api_id": "ka00001", "wait_ms": 1200, "pacing_unit": "tr"}
    ]


@pytest.mark.anyio
async def test_us_real_peak_time_rejection_log_marks_peak(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock(datetime(2026, 10, 26, 9, 30, tzinfo=kiwoom.KST))
    client, sent = make(fake_replying("ust21070"), clock)
    await client.access_token(US_REAL)

    await asyncio.gather(
        *(client.call(US_REAL, "ust21070", "/api/us/acnt", {}) for _ in range(16)),
        return_exceptions=True,
    )

    assert len(times(sent, "ust21070")) == 15
    assert pacing_logs(caplog, "kiwoom_pacing_rejected") == [
        {"api_id": "ust21070", "wait_ms": 6000, "pacing_unit": "us_inquiry", "peak": True}
    ]


@pytest.mark.anyio
async def test_cancelled_request_gives_up_its_place():
    clock = FakeClock()
    client, sent = make(fake_replying("ka00001"), clock)
    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})

    waiting = asyncio.create_task(client.call(DOMESTIC_PAPER, "ka00001", PATH, {}))

    async def cancel_waiting() -> None:
        waiting.cancel()

    clock.on_sleep = cancel_waiting
    with pytest.raises(asyncio.CancelledError):
        await waiting

    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})
    assert times(sent, "ka00001") == [0.0, 1.2]
