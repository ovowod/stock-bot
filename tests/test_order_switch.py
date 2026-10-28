"""주문 허용 스위치와 KiwoomClient 관문.

스위치가 꺼진 투자 환경에서 돈이 움직이는 TR은 어느 호출 경로로 와도 키움에 나가지 않는다.
"""

import dataclasses

import httpx
import pytest

from stock_bot.config import ENVIRONMENTS, Environment
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom
from tests.fake_realtime import FakeRealtime
from tests.test_fill_notifications import REAL_ENV
from tests.test_open_orders import OPEN_ORDER
from tests.test_orders import domestic_order
from tests.test_sl_tp_auto_sell import running, session, sl_tp_kiwoom, watching, write_settings

DOMESTIC_REAL = ENVIRONMENTS[Environment.DOMESTIC_REAL]
ORDER_REPLY = {"ord_no": "00024", "return_code": 0, "return_msg": "정상"}


@pytest.mark.anyio
async def test_real_environment_order_never_reaches_kiwoom():
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    client = KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(fake))

    with pytest.raises(AppError) as error:
        await client.call_once(DOMESTIC_REAL, "kt10000", "/api/dostk/ordr", {})

    assert error.value.kind == "order_not_allowed"
    assert error.value.status == 403
    assert fake.calls("kt10000") == []


MONEY_MOVING = [
    ("kt10000", "/api/dostk/ordr"),
    ("kt10001", "/api/dostk/ordr"),
    ("kt10002", "/api/dostk/ordr"),
    ("kt10003", "/api/dostk/ordr"),
    ("kt50000", "/api/dostk/ordr"),
    ("kt50001", "/api/dostk/ordr"),
    ("kt50002", "/api/dostk/ordr"),
    ("kt50003", "/api/dostk/ordr"),
    ("kt10006", "/api/dostk/crdordr"),
    ("kt10007", "/api/dostk/crdordr"),
    ("kt10008", "/api/dostk/crdordr"),
    ("kt10009", "/api/dostk/crdordr"),
    ("ust20000", "/api/us/ordr"),
    ("ust20001", "/api/us/ordr"),
    ("ust20002", "/api/us/ordr"),
    ("ust20003", "/api/us/ordr"),
    ("ust21200", "/api/us/ordr"),
    ("ust21201", "/api/us/ordr"),
    ("ust21203", "/api/us/ordr"),
    ("ust31302", "/api/us/exchange"),
]
REAL = [ENVIRONMENTS[Environment.DOMESTIC_REAL], ENVIRONMENTS[Environment.US_REAL]]
PAPER = [ENVIRONMENTS[Environment.DOMESTIC_PAPER], ENVIRONMENTS[Environment.US_PAPER]]


def _paths(client: KiwoomClient):
    return {
        "call": client.call,
        "call_first_page": client.call_first_page,
        "call_once": client.call_once,
    }


@pytest.mark.anyio
@pytest.mark.parametrize("path_name", ["call", "call_first_page", "call_once"])
@pytest.mark.parametrize("spec", REAL, ids=lambda s: s.environment.value)
async def test_every_money_moving_tr_is_blocked_on_every_call_path(spec, path_name):
    fake = FakeKiwoom()
    for api_id, _ in MONEY_MOVING:
        fake.reply(api_id, ORDER_REPLY)
    send = _paths(KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(fake)))[path_name]

    for api_id, path in MONEY_MOVING:
        with pytest.raises(AppError) as error:
            await send(spec, api_id, path, {})
        assert error.value.kind == "order_not_allowed", api_id

    assert [r for r in fake.requests if r.url.path != "/oauth2/token"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("spec", PAPER, ids=lambda s: s.environment.value)
async def test_paper_environment_sends_money_moving_trs(spec):
    fake = FakeKiwoom()
    for api_id, _ in MONEY_MOVING:
        fake.reply(api_id, ORDER_REPLY)
    client = KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(fake))

    for api_id, path in MONEY_MOVING:
        await client.call_once(spec, api_id, path, {})

    assert [r.headers["api-id"] for r in fake.requests if r.url.path != "/oauth2/token"] == [
        api_id for api_id, _ in MONEY_MOVING
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "api_id,path",
    [
        ("kt00018", "/api/dostk/acnt"),
        ("ka10075", "/api/dostk/acnt"),
        ("ust21070", "/api/us/acnt"),
        # 주문·환전 주소에 섞인 조회 TR
        ("ust21204", "/api/us/ordr"),
        ("ust21205", "/api/us/ordr"),
        ("ust31490", "/api/us/ordr"),
        ("ust31300", "/api/us/exchange"),
        ("ust31301", "/api/us/exchange"),
    ],
)
@pytest.mark.parametrize("spec", REAL, ids=lambda s: s.environment.value)
async def test_real_environment_still_sends_inquiries(spec, api_id, path):
    fake = FakeKiwoom().reply(api_id, {"return_code": 0, "return_msg": "정상"})
    client = KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(fake))

    await client.call(spec, api_id, path, {})

    assert len(fake.calls(api_id)) == 1


@pytest.mark.anyio
async def test_blocked_request_is_logged(json_logs):
    client = KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(FakeKiwoom()))

    with pytest.raises(AppError):
        await client.call_once(ENVIRONMENTS[Environment.US_REAL], "ust20001", "/api/us/ordr", {})

    blocked = [line for line in json_logs.lines if line["event"] == "kiwoom_order_blocked"]
    assert len(blocked) == 1
    assert blocked[0]["level"] == "WARNING"
    assert blocked[0]["api_id"] == "ust20001"
    assert blocked[0]["target"] == "https://api.kiwoom.com/api/us/ordr"


@pytest.mark.anyio
async def test_switching_orders_on_lets_real_orders_through():
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)
    client = KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(fake))

    await client.call_once(
        dataclasses.replace(DOMESTIC_REAL, orders_allowed=True), "kt10000", "/api/dostk/ordr", {}
    )

    assert len(fake.calls("kt10000")) == 1


@pytest.fixture
def real_orders_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """실전 두 환경의 주문 허용을 켠다. 요청은 가짜 키움 서버로만 간다."""
    for environment in (Environment.DOMESTIC_REAL, Environment.US_REAL):
        on = dataclasses.replace(ENVIRONMENTS[environment], orders_allowed=True)
        monkeypatch.setitem(ENVIRONMENTS, environment, on)


def test_environment_list_reports_the_switch(make_client, real_orders_on):
    environments = make_client().get("/api/environments").json()

    assert {e["value"]: e["orders_allowed"] for e in environments} == {
        "domestic_real": True,
        "us_real": True,
        "domestic_paper": True,
        "us_paper": True,
    }


def test_switching_orders_on_lets_real_manual_orders_through(make_client, real_orders_on):
    fake = FakeKiwoom().reply("kt10000", ORDER_REPLY)

    response = make_client(fake).post(
        "/api/environments/domestic_real/orders", json=domestic_order()
    )

    assert response.status_code == 200
    [request] = fake.calls("kt10000")
    assert request.url.host == "api.kiwoom.com"


def test_switching_orders_on_makes_real_open_orders_cancelable(make_client, real_orders_on):
    fake = FakeKiwoom().reply("ka10075", {"oso": [OPEN_ORDER]})

    [order] = make_client(fake).get("/api/environments/domestic_real/open-orders").json()["orders"]

    assert (order["cancelable"], order["blocked_reason"]) == (True, None)


def test_switching_orders_on_lets_real_sl_tp_sessions_be_enabled(make_client, real_orders_on):
    client = make_client()
    url = "/api/environments/domestic_real/sl-tp"

    assert client.get(url).json()["can_enable"] is True
    response = client.put(url, json={"excluded": [], "sessions": [session(enabled=True)]})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_switching_orders_on_makes_sl_tp_watch_real_holdings(data_dir, real_orders_on):
    write_settings(data_dir, session(), environment="domestic_real")
    realtime = FakeRealtime()
    kiwoom = sl_tp_kiwoom()
    async with running(realtime, kiwoom, data_dir, environ=REAL_ENV):
        connection = await watching(realtime)

    assert kiwoom.calls("kt00018")[0].url.host == "api.kiwoom.com"
    assert connection.url == "wss://api.kiwoom.com:10000/api/websocket"
