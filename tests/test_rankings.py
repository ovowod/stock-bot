import asyncio
import threading

import httpx
import pytest

from stock_bot.app import create_app
from tests.fake_kiwoom import (
    FAKE_ENV,
    KA10032_ROW,
    USA20540_ROW,
    ThreadedTransport,
    body_of,
    kiwoom_error,
    page_response,
    ranking_fake,
)

DOMESTIC_URL = "/api/environments/domestic_paper/rankings/trading_value"
US_URL = "/api/environments/us_paper/rankings/trading_value"


def test_domestic_trading_value_calls_ka10032_on_the_paper_domain(make_client):
    fake = ranking_fake("ka10032", {"trde_prica_upper": [KA10032_ROW]})
    response = make_client(fake).get(DOMESTIC_URL)

    assert response.status_code == 200
    request = fake.calls("ka10032")[0]
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/dostk/rkinfo"
    assert body_of(request) == {"mrkt_tp": "000", "mang_stk_incls": "1", "stex_tp": "3"}


def test_domestic_trading_value_is_converted_for_the_screen(make_client):
    fake = ranking_fake("ka10032", {"trde_prica_upper": [KA10032_ROW]})
    body = make_client(fake).get(DOMESTIC_URL).json()

    assert body["environment"] == "domestic_paper"
    assert body["market"] == "domestic"
    assert body["kind"] == "trading_value"
    assert body["exchange"] == "all"
    assert body["fetched_at"]
    assert body["items"] == [
        {
            "rank": 1,
            "code": "000660",
            "name": "SK하이닉스",
            "exchange": None,
            "price": 1841000,
            "direction": "up",
            "change_rate": 0.44,
            "trading_value": 5_359_250_000_000,
            "previous_rank": 2,
        }
    ]


def test_us_trading_value_calls_usa20540_and_converts_usd(make_client):
    fake = ranking_fake("usa20540", {"result_list": [USA20540_ROW]})
    response = make_client(fake).get(US_URL)

    assert response.status_code == 200
    request = fake.calls("usa20540")[0]
    assert request.url.path == "/api/us/rkinfo"
    assert body_of(request) == {
        "stex_tp": "0",
        "inds_cd": "000",
        "stk_tp": "0",
        "trde_qty_tp": "0",
        "stk_cnd": "0",
        "pric_cnd": "0",
        "trde_prica_cnd": "0",
    }
    assert response.json()["items"] == [
        {
            "rank": 1,
            "code": "SOXL",
            "name": "미국 반도체 3배 디렉시온 ETF",
            "exchange": "NYSE",
            "price": 162.6,
            "direction": "down",
            "change_rate": -0.68,
            "trading_value": 104_125_000,
            "previous_rank": None,
        }
    ]


@pytest.mark.parametrize(
    ("environment", "api_id", "field", "exchange", "code"),
    [
        ("domestic_paper", "ka10032", "mrkt_tp", "kospi", "001"),
        ("domestic_paper", "ka10032", "mrkt_tp", "kosdaq", "101"),
        ("domestic_paper", "ka10032", "mrkt_tp", "all", "000"),
        ("us_paper", "usa20540", "stex_tp", "nyse", "1"),
        ("us_paper", "usa20540", "stex_tp", "nasdaq", "2"),
        ("us_paper", "usa20540", "stex_tp", "amex", "3"),
    ],
)
def test_exchange_is_mapped_to_the_kiwoom_code(
    make_client, environment, api_id, field, exchange, code
):
    list_key = "trde_prica_upper" if api_id == "ka10032" else "result_list"
    fake = ranking_fake(api_id, {list_key: []})
    url = f"/api/environments/{environment}/rankings/trading_value?exchange={exchange}"
    response = make_client(fake).get(url)

    assert response.status_code == 200
    assert response.json()["exchange"] == exchange
    assert body_of(fake.calls(api_id)[0])[field] == code


def test_real_environment_uses_the_real_domain(make_client):
    fake = ranking_fake("ka10032", {"trde_prica_upper": []})
    response = make_client(fake).get("/api/environments/domestic_real/rankings/trading_value")

    assert response.status_code == 200
    assert {r.url.host for r in fake.requests} == {"api.kiwoom.com"}


def test_only_the_first_page_is_fetched_and_cut_to_30(make_client):
    rows = [{**KA10032_ROW, "now_rank": str(i)} for i in range(1, 101)]
    fake = ranking_fake(
        "ka10032", page_response({"trde_prica_upper": rows}, cont_yn="Y", next_key="next")
    )
    response = make_client(fake).get(DOMESTIC_URL)

    assert response.status_code == 200
    assert len(fake.calls("ka10032")) == 1
    assert [item["rank"] for item in response.json()["items"]] == list(range(1, 31))


def test_empty_list_is_a_normal_response(make_client):
    fake = ranking_fake("ka10032", {"trde_prica_upper": []})
    response = make_client(fake).get(DOMESTIC_URL)

    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.parametrize(
    ("url", "status", "kind"),
    [
        ("/api/environments/us_paper/rankings/trading_value?exchange=kospi", 400, "bad_request"),
        (
            "/api/environments/domestic_paper/rankings/trading_value?exchange=nyse",
            400,
            "bad_request",
        ),
        ("/api/environments/domestic_paper/rankings/unknown", 404, "unknown_ranking"),
        ("/api/environments/nowhere/rankings/trading_value", 404, "unknown_environment"),
    ],
)
def test_invalid_conditions_are_rejected_without_calling_kiwoom(make_client, url, status, kind):
    response = make_client().get(url)

    assert response.status_code == status
    assert response.json()["error"]["kind"] == kind


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        ({}, 502),
        ({"trde_prica_upper": None}, 502),
        ({"trde_prica_upper": {"stk_cd": "005930"}}, 502),
        ({"trde_prica_upper": [{k: v for k, v in KA10032_ROW.items() if k != "stk_cd"}]}, 502),
        ({"trde_prica_upper": [{k: v for k, v in KA10032_ROW.items() if k != "stk_nm"}]}, 502),
        ({"trde_prica_upper": [{**KA10032_ROW, "cur_prc": "abc"}]}, 502),
    ],
)
def test_malformed_responses_are_response_format_errors(make_client, reply, status):
    response = make_client(ranking_fake("ka10032", reply)).get(DOMESTIC_URL)

    assert response.status_code == status
    assert response.json()["error"]["kind"] == "response_format_error"


def test_empty_numbers_become_null(make_client):
    row = {**KA10032_ROW, "cur_prc": "", "flu_rt": "", "trde_prica": "", "pred_rank": ""}
    item = (
        make_client(ranking_fake("ka10032", {"trde_prica_upper": [row]}))
        .get(DOMESTIC_URL)
        .json()["items"][0]
    )

    assert item["price"] is None
    assert item["change_rate"] is None
    assert item["trading_value"] is None
    assert item["previous_rank"] is None


@pytest.mark.parametrize(
    ("sign", "direction"),
    [("1", "up"), ("2", "up"), ("3", "flat"), ("4", "down"), ("5", "down"), ("9", "unknown")],
)
def test_direction_follows_the_sign_code(make_client, sign, direction):
    row = {**KA10032_ROW, "pred_pre_sig": sign}
    item = (
        make_client(ranking_fake("ka10032", {"trde_prica_upper": [row]}))
        .get(DOMESTIC_URL)
        .json()["items"][0]
    )

    assert item["direction"] == direction


def test_unknown_us_exchange_code_is_kept(make_client):
    row = {**USA20540_ROW, "stex_tp": "XX"}
    item = (
        make_client(ranking_fake("usa20540", {"result_list": [row]})).get(US_URL).json()["items"][0]
    )

    assert item["exchange"] == "XX"


def test_rate_limit_is_reported_as_429(make_client):
    fake = ranking_fake("ka10032", kiwoom_error(1700, "허용된 API 요청 개수를 초과하였습니다"))
    response = make_client(fake).get(DOMESTIC_URL)

    assert response.status_code == 429
    assert response.json()["error"]["kind"] == "rate_limited"


@pytest.mark.anyio
async def test_concurrent_ranking_requests_reach_kiwoom_one_at_a_time():
    fake = ranking_fake("ka10032", {"trde_prica_upper": [KA10032_ROW]})
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("api-id") != "ka10032":
            return fake(request)
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        release.wait(timeout=0.2)
        with lock:
            state["active"] -= 1
        return fake(request)

    app = create_app(
        environ=FAKE_ENV, transport=ThreadedTransport(handler), static_dir=None, log_dir=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        pending = [asyncio.create_task(client.get(DOMESTIC_URL)) for _ in range(2)]
        await asyncio.sleep(0.05)
        release.set()
        responses = await asyncio.gather(*pending)

    assert [r.status_code for r in responses] == [200, 200]
    assert len(fake.calls("ka10032")) == 2
    assert state["max_active"] == 1
