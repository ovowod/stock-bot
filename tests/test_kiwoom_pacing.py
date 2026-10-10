"""같은 인증정보·같은 TR의 호출 간격 맞추기. 가짜 시계로 실제로 기다리지 않고 검증한다."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator

import httpx
import pytest

from stock_bot import kiwoom
from stock_bot.config import ENVIRONMENTS, Environment
from stock_bot.kiwoom import KiwoomClient
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom, body_of, kiwoom_error, page_response

DOMESTIC_PAPER = ENVIRONMENTS[Environment.DOMESTIC_PAPER]
PATH = "/api/dostk/acnt"


class FakeClock:
    """sleep이 실제로 기다리지 않고 시계를 깨어날 시각으로 옮긴다."""

    def __init__(self) -> None:
        self.now = 0.0
        # 다음 sleep이 시작될 때 한 번 실행한다. 기다리는 도중에 일어나는 일을 흉내 낼 때 쓴다.
        self.on_sleep: Callable[[], Awaitable[None]] | None = None

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        wake = self.now + seconds
        if self.on_sleep is not None:
            hook, self.on_sleep = self.on_sleep, None
            await hook()
        await asyncio.sleep(0)
        self.now = max(self.now, wake)


@pytest.fixture(autouse=True)
def real_interval(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(kiwoom, "CALL_INTERVAL_SECONDS", 1.0)
    yield


def make(fake: FakeKiwoom, clock: FakeClock) -> tuple[KiwoomClient, list[tuple[str, float]]]:
    sent: list[tuple[str, float]] = []

    def record(request: httpx.Request) -> None:
        sent.append((request.headers.get("api-id", "au10001"), clock()))

    fake.on_request = record
    client = KiwoomClient(
        FAKE_ENV, transport=httpx.MockTransport(fake), clock=clock, sleep=clock.sleep
    )
    return client, sent


def times(sent: list[tuple[str, float]], api_id: str) -> list[float]:
    return [at for sent_id, at in sent if sent_id == api_id]


@pytest.mark.anyio
async def test_same_tr_twice_is_sent_one_second_apart():
    clock = FakeClock()
    client, sent = make(FakeKiwoom().reply("ka00001", {"acctNo": "1"}), clock)

    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})
    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})

    assert times(sent, "ka00001") == [0.0, 1.0]


@pytest.mark.anyio
async def test_different_tr_or_credentials_do_not_wait():
    clock = FakeClock()
    fake = FakeKiwoom().reply("ka00001", {"acctNo": "1"}).reply("kt00001", {"entr": "0"})
    client, sent = make(fake, clock)

    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})
    await client.call(DOMESTIC_PAPER, "kt00001", PATH, {})
    await client.call(ENVIRONMENTS[Environment.US_PAPER], "ka00001", PATH, {})

    assert times(sent, "ka00001") == [0.0, 0.0]
    assert times(sent, "kt00001") == [0.0]


@pytest.mark.anyio
async def test_domestic_real_and_us_real_share_one_unit():
    clock = FakeClock()
    client, sent = make(FakeKiwoom().reply("ka00001", {"acctNo": "1"}), clock)

    await client.call(ENVIRONMENTS[Environment.DOMESTIC_REAL], "ka00001", PATH, {})
    await client.call(ENVIRONMENTS[Environment.US_REAL], "ka00001", PATH, {})

    assert times(sent, "ka00001") == [0.0, 1.0]


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

    assert [entry for entry in order if entry[0]] == [("a", 0.0), ("b", 1.0), ("c", 2.0)]


@pytest.mark.anyio
async def test_request_needing_more_than_five_seconds_is_rejected_without_sending(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock()
    client, sent = make(FakeKiwoom().reply("ka00001", {"acctNo": "1"}), clock)

    results = await asyncio.gather(
        *(client.call(DOMESTIC_PAPER, "ka00001", PATH, {}) for _ in range(7)),
        return_exceptions=True,
    )

    assert times(sent, "ka00001") == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    rejected = results[6]
    assert isinstance(rejected, kiwoom.AppError)
    assert (rejected.kind, rejected.status) == ("rate_limited", 429)
    events = [r.getMessage() for r in caplog.records]
    assert events.count("kiwoom_pacing_wait") == 5
    assert events.count("kiwoom_pacing_rejected") == 1

    # 거부된 요청은 자리를 차지하지 않으므로 다음 요청은 6초에 나간다.
    await client.call(DOMESTIC_PAPER, "ka00001", PATH, {})
    assert times(sent, "ka00001")[-1] == 6.0


@pytest.mark.anyio
async def test_continuation_page_is_not_delayed_but_counts_for_the_next_request():
    clock = FakeClock()
    fake = FakeKiwoom().reply(
        "kt00018",
        page_response({"rows": [1]}, cont_yn="Y", next_key="p2"),
        page_response({"rows": [2]}),
    )
    client, sent = make(fake, clock)

    await client.call(DOMESTIC_PAPER, "kt00018", PATH, {})
    clock.now = 0.5
    fake.replies["kt00018"] = [page_response({"rows": [3]})]
    await client.call(DOMESTIC_PAPER, "kt00018", PATH, {})

    assert times(sent, "kt00018") == [0.0, 0.0, 1.0]


@pytest.mark.anyio
async def test_waiting_request_counts_from_a_continuation_page_sent_meanwhile():
    """기다리는 도중 앞 조회의 다음 페이지가 나가면, 그 페이지 시각에서 1초 뒤에 나간다."""
    clock = FakeClock()
    fake = FakeKiwoom().reply(
        "kt00018",
        page_response({"rows": [1]}, cont_yn="Y", next_key="p2"),
        page_response({"rows": [2]}),
        page_response({"rows": [3]}),
    )
    client, sent = make(fake, clock)
    first_page_released = asyncio.Event()

    async def slow_first_page(response: httpx.Response) -> httpx.Response:
        await first_page_released.wait()
        return response

    def handler(request: httpx.Request) -> httpx.Response | Awaitable[httpx.Response]:
        is_first_page = request.headers.get("api-id") == "kt00018" and not fake.calls("kt00018")
        response = fake(request)
        return slow_first_page(response) if is_first_page else response

    async def page_arrives_while_waiting() -> None:
        # 기다리던 요청이 잠들자마자 0.9초에 첫 페이지 응답이 와서 다음 페이지가 나간다.
        clock.now = 0.9
        first_page_released.set()
        for _ in range(10):
            await asyncio.sleep(0)

    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clock.on_sleep = page_arrives_while_waiting
    await asyncio.gather(
        client.call(DOMESTIC_PAPER, "kt00018", PATH, {}),
        client.call(DOMESTIC_PAPER, "kt00018", PATH, {}),
    )

    assert times(sent, "kt00018") == [0.0, 0.9, 1.9]


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

    assert times(sent, "au10001") == [0.0, 5.0]
    assert times(sent, "kt00018") == [5.0, 5.0, 6.0]


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
    assert times(sent, "kt10000") == [0.0, 1.0]


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
