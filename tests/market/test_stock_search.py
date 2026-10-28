import asyncio
import threading
from datetime import date

import httpx
import pytest

from stock_bot.app import create_app
from tests.fake_kiwoom import (
    FAKE_ENV,
    KOSPI_ROWS,
    ThreadedTransport,
    body_of,
    ka10099_row,
    kiwoom_error,
    page_response,
    stock_list_fake,
    usa10099_row,
)

DOMESTIC = "/api/environments/domestic_paper/stocks"
US = "/api/environments/us_paper/stocks"


def search(client, url: str, query: str) -> httpx.Response:
    return client.get(url, params={"q": query})


def test_domestic_search_fetches_kospi_and_kosdaq_lists(make_client):
    fake = stock_list_fake()
    response = search(make_client(fake), DOMESTIC, "삼성")

    assert response.status_code == 200
    calls = fake.calls("ka10099")
    assert [body_of(r) for r in calls] == [{"mrkt_tp": "0"}, {"mrkt_tp": "10"}]
    assert {r.url.host for r in calls} == {"mockapi.kiwoom.com"}
    assert {r.url.path for r in calls} == {"/api/dostk/stkinfo"}


def test_us_search_fetches_the_whole_us_list(make_client):
    fake = stock_list_fake()
    response = search(make_client(fake), US, "aapl")

    assert response.status_code == 200
    request = fake.calls("usa10099")[0]
    assert request.url.path == "/api/us/stkinfo"
    assert body_of(request) == {"stex_tp": "%"}


def test_real_environment_uses_the_real_domain(make_client):
    fake = stock_list_fake()
    response = search(make_client(fake), "/api/environments/domestic_real/stocks", "삼성")

    assert response.status_code == 200
    assert {r.url.host for r in fake.requests} == {"api.kiwoom.com"}


def test_domestic_result_is_converted_for_the_screen(make_client):
    body = search(make_client(stock_list_fake()), DOMESTIC, "005930").json()

    assert body["environment"] == "domestic_paper"
    assert body["market"] == "domestic"
    assert body["query"] == "005930"
    assert body["total"] == 1
    assert body["truncated"] is False
    assert body["list_fetched_at"]
    assert body["items"] == [
        {
            "code": "005930",
            "name": "삼성전자",
            "english_name": None,
            "exchange": None,
            "category": "코스피",
            "industry": "전기전자",
            "status": None,
            "is_etf": None,
        }
    ]


def test_domestic_category_status_and_empty_industry(make_client):
    client = make_client(stock_list_fake())
    etf = search(client, DOMESTIC, "KODEX").json()["items"][0]
    managed = search(client, DOMESTIC, "KR모터스").json()["items"][0]
    kosdaq = search(client, DOMESTIC, "에코프로").json()["items"][0]

    assert etf["category"] == "ETF"
    assert etf["industry"] is None
    assert managed["status"] == "관리종목"
    assert kosdaq["category"] == "코스닥"


def test_us_result_is_converted_and_filtered(make_client):
    client = make_client(stock_list_fake())
    apple = search(client, US, "AAPL").json()["items"][0]
    spy = search(client, US, "SPY").json()
    otc = search(client, US, "OTCX").json()

    assert apple == {
        "code": "AAPL",
        "name": "애플",
        "english_name": "APPLE INC",
        "exchange": "NASDAQ",
        "category": None,
        "industry": "컴퓨터 및 전자장비",
        "status": None,
        "is_etf": False,
    }
    assert spy["total"] == 1
    assert spy["items"][0]["is_etf"] is True
    assert spy["items"][0]["exchange"] == "NYSE"
    assert otc["items"] == []


@pytest.mark.parametrize(
    ("url", "query", "expected"),
    [
        (DOMESTIC, "0059", ["005930"]),
        (DOMESTIC, "하이닉", ["000660"]),
        (DOMESTIC, "kodex200", ["069500"]),
        (DOMESTIC, " 삼성 ", ["005930"]),
        (US, "aap", ["AAPL"]),
        (US, "apple inc", ["AAPL"]),
        (US, "호스피탈", ["APLE"]),
    ],
)
def test_query_matching(make_client, url, query, expected):
    items = search(make_client(stock_list_fake()), url, query).json()["items"]

    assert [item["code"] for item in items] == expected


def test_results_are_ordered_by_match_stage_then_name(make_client):
    rows = [
        ka10099_row("100001", "가나 전자"),  # 이름 중간 포함
        ka10099_row("100002", "전자가"),  # 이름 시작
        ka10099_row("200000", "다른이름"),  # 일치 없음
        ka10099_row("100003", "나전자"),  # 이름 중간 포함
    ]
    fake = stock_list_fake(kospi={"list": rows}, kosdaq={"list": []})
    items = search(make_client(fake), DOMESTIC, "전자").json()["items"]

    assert [item["code"] for item in items] == ["100002", "100001", "100003"]


def test_code_matches_come_before_name_matches(make_client):
    rows = [
        ka10099_row("A10000", "Z종목"),
        ka10099_row("000010", "10번 종목"),
        ka10099_row("100000", "코드시작"),
        ka10099_row("000100", "다른종목"),
    ]
    fake = stock_list_fake(kospi={"list": rows}, kosdaq={"list": []})
    items = search(make_client(fake), DOMESTIC, "000010").json()["items"]

    assert [item["code"] for item in items] == ["000010"]
    items = search(
        make_client(stock_list_fake(kospi={"list": rows}, kosdaq={"list": []})), DOMESTIC, "10"
    ).json()["items"]
    assert [item["code"] for item in items] == ["100000", "000010"]


def test_results_are_cut_to_50_with_the_total(make_client):
    rows = [ka10099_row(f"{i:06d}", f"테스트{i:03d}") for i in range(60)]
    fake = stock_list_fake(kospi={"list": rows}, kosdaq={"list": []})
    body = search(make_client(fake), DOMESTIC, "테스트").json()

    assert len(body["items"]) == 50
    assert body["total"] == 60
    assert body["truncated"] is True


def test_list_is_reused_until_the_date_changes(make_client):
    fake = stock_list_fake()
    today = {"value": date(2026, 10, 5)}
    client = make_client(fake, today=lambda: today["value"])

    search(client, DOMESTIC, "삼성")
    search(client, DOMESTIC, "하이닉")
    assert len(fake.calls("ka10099")) == 2

    today["value"] = date(2026, 10, 6)
    search(client, DOMESTIC, "삼성")
    assert len(fake.calls("ka10099")) == 4


def test_each_environment_keeps_its_own_list(make_client):
    fake = stock_list_fake()
    client = make_client(fake)

    search(client, DOMESTIC, "삼성")
    search(client, "/api/environments/domestic_real/stocks", "삼성")
    search(client, US, "AAPL")
    search(client, "/api/environments/us_real/stocks", "AAPL")

    assert len(fake.calls("ka10099")) == 4
    assert len(fake.calls("usa10099")) == 2


def test_failed_kosdaq_list_is_not_kept(make_client):
    failing = {"value": True}
    fake = stock_list_fake()
    kosdaq_error = kiwoom_error(1511, "테스트 오류")
    fake.respond(
        "ka10099",
        lambda request: (
            kosdaq_error
            if failing["value"] and body_of(request)["mrkt_tp"] == "10"
            else {"list": KOSPI_ROWS if body_of(request)["mrkt_tp"] == "0" else []}
        ),
    )
    client = make_client(fake)

    assert search(client, DOMESTIC, "삼성").status_code == 502
    failing["value"] = False
    assert search(client, DOMESTIC, "삼성").status_code == 200
    assert len(fake.calls("ka10099")) == 4


@pytest.mark.parametrize("query", ["", "   ", "가" * 51])
def test_invalid_query_is_rejected_without_calling_kiwoom(make_client, query):
    response = search(make_client(), DOMESTIC, query)

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "bad_request"


def test_missing_query_is_rejected(make_client):
    response = make_client().get(DOMESTIC)

    assert response.status_code == 400


@pytest.mark.parametrize(
    "kospi",
    [
        {},
        {"list": None},
        {"list": [{k: v for k, v in ka10099_row("005930", "삼성전자").items() if k != "code"}]},
        {"list": [{k: v for k, v in ka10099_row("005930", "삼성전자").items() if k != "name"}]},
    ],
)
def test_malformed_list_is_a_response_format_error(make_client, kospi):
    response = search(make_client(stock_list_fake(kospi=kospi)), DOMESTIC, "삼성")

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "response_format_error"


@pytest.mark.parametrize("second", [{}, {"list": "oops"}])
def test_bad_continuation_page_is_an_error_and_not_kept(make_client, second):
    pages = {"count": 0}

    def kospi(request: httpx.Request):
        pages["count"] += 1
        if request.headers.get("cont-yn") == "Y":
            return page_response(second)
        return page_response({"list": KOSPI_ROWS}, cont_yn="Y", next_key="next")

    fake = stock_list_fake().respond(
        "ka10099",
        lambda request: kospi(request) if body_of(request)["mrkt_tp"] == "0" else {"list": []},
    )
    client = make_client(fake)

    first = search(client, DOMESTIC, "삼성")
    assert first.status_code == 502
    assert first.json()["error"]["kind"] == "response_format_error"
    search(client, DOMESTIC, "삼성")
    assert pages["count"] == 4


def test_rate_limit_is_reported_as_429(make_client):
    fake = stock_list_fake(us=kiwoom_error(1700, "허용된 API 요청 개수를 초과하였습니다"))
    response = search(make_client(fake), US, "AAPL")

    assert response.status_code == 429


async def _concurrent_searches(handler, count: int = 3) -> list[httpx.Response]:
    app = create_app(
        environ=FAKE_ENV, transport=ThreadedTransport(handler), static_dir=None, log_dir=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        pending = [asyncio.create_task(client.get(US, params={"q": "AAPL"})) for _ in range(count)]
        return await asyncio.gather(*pending)


@pytest.mark.anyio
async def test_concurrent_first_searches_fetch_the_list_once():
    fake = stock_list_fake()
    gate = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("api-id") == "usa10099":
            gate.wait(timeout=0.3)
        return fake(request)

    task = asyncio.create_task(_concurrent_searches(handler))
    await asyncio.sleep(0.05)
    gate.set()
    responses = await task

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert len(fake.calls("usa10099")) == 1


@pytest.mark.anyio
async def test_concurrent_searches_share_a_failed_fetch():
    fake = stock_list_fake(us=kiwoom_error(1700, "허용된 API 요청 개수를 초과하였습니다"))
    gate = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("api-id") == "usa10099":
            gate.wait(timeout=0.3)
        return fake(request)

    task = asyncio.create_task(_concurrent_searches(handler))
    await asyncio.sleep(0.05)
    gate.set()
    responses = await task

    assert [r.status_code for r in responses] == [429, 429, 429]
    assert len(fake.calls("usa10099")) == 1


def test_search_after_a_failed_fetch_tries_again(make_client):
    fake = stock_list_fake(us=kiwoom_error(1700, "허용된 API 요청 개수를 초과하였습니다"))
    client = make_client(fake)

    assert search(client, US, "AAPL").status_code == 429
    fake.reply("usa10099", {"list": [usa10099_row("AAPL", "애플", "APPLE INC")]})
    assert search(client, US, "AAPL").status_code == 200
    assert len(fake.calls("usa10099")) == 2
