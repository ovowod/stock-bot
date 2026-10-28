from decimal import Decimal

import httpx
import pytest

from stock_bot.config import ENVIRONMENTS, Environment
from stock_bot.errors import AppError
from stock_bot.holdings import Holdings
from stock_bot.kiwoom import KiwoomClient
from tests.fake_kiwoom import (
    FAKE_ENV,
    KT00018_HOLDING,
    KT00018_REPLY,
    UST21070_HOLDING,
    UST21070_REPLY,
    FakeKiwoom,
    body_of,
    domestic_fake,
    kiwoom_error,
    page_response,
    us_fake,
)


def test_domestic_holding_returns_the_cash_quantities(make_client):
    held = {**KT00018_HOLDING, "rmnd_qty": "000000000000010", "trde_able_qty": "000000000000007"}
    fake = FakeKiwoom().reply("kt00018", {**KT00018_REPLY, "acnt_evlt_remn_indv_tot": [held]})
    response = make_client(fake).get("/api/environments/domestic_paper/holdings/005930")

    assert response.status_code == 200
    body = response.json()
    assert {k: v for k, v in body.items() if k != "fetched_at"} == {
        "code": "005930",
        "held": True,
        "quantity": 10,
        "sellable_quantity": 7,
    }
    assert body["fetched_at"]
    assert fake.calls("kt00018")[0].url.host == "mockapi.kiwoom.com"


def us_balance_like_kiwoom(request: httpx.Request) -> httpx.Response:
    """모의 서버는 종목코드만 넣고 거래소를 비우면 1517로 거부했다(2026-10-06 EWZ 매도 확인)."""
    body = body_of(request)
    if body["stk_cd"] and not body["stex_tp"]:
        return kiwoom_error(
            1517,
            "입력 값 형식이 올바르지 않습니다. 파라미터=stex_tp 실패사유= 거래소 구분값이 없습니다",
        )
    other = {**UST21070_HOLDING, "stk_cd": "EWZ", "frgn_stk_nm": "MSCI 브라질", "sell_alowq": "1"}
    return page_response({**UST21070_REPLY, "result_list": [other, UST21070_HOLDING]})


def test_us_holding_is_found_by_ticker_in_the_whole_balance(make_client):
    fake = FakeKiwoom().respond("ust21070", us_balance_like_kiwoom)
    response = make_client(fake).get("/api/environments/us_paper/holdings/AAPL")

    assert response.status_code == 200
    assert response.json()["sellable_quantity"] == 395
    # 거래소 표기가 주문과 다를 수 있어 거래소를 지정하지 않고 전체를 받는다.
    assert body_of(fake.calls("ust21070")[0]) == {"stex_tp": "", "stk_cd": ""}


def test_a_stock_not_held_is_not_an_error(make_client):
    fake = FakeKiwoom().reply("kt00018", {**KT00018_REPLY, "acnt_evlt_remn_indv_tot": []})
    response = make_client(fake).get("/api/environments/domestic_paper/holdings/005930")

    assert response.status_code == 200
    body = response.json()
    assert (body["held"], body["quantity"], body["sellable_quantity"]) == (False, 0, 0)


def test_holding_failures_follow_the_usual_error_kinds(make_client):
    fake = FakeKiwoom().reply("kt00018", kiwoom_error(1700, "허용된 요청 개수를 초과하였습니다"))
    client = make_client(fake)
    limited = client.get("/api/environments/domestic_paper/holdings/005930")
    assert limited.status_code == 429
    assert limited.json()["error"]["kind"] == "rate_limited"

    two_rows = {**KT00018_REPLY, "acnt_evlt_remn_indv_tot": [KT00018_HOLDING, KT00018_HOLDING]}
    fake.reply("kt00018", two_rows)
    unsupported = client.get("/api/environments/domestic_paper/holdings/005930")
    assert unsupported.status_code == 502
    assert "같은 종목이 여러 줄" in unsupported.json()["error"]["message"]

    assert client.get("/api/environments/nowhere/holdings/005930").status_code == 404


DOMESTIC_PAPER = ENVIRONMENTS[Environment.DOMESTIC_PAPER]
US_PAPER = ENVIRONMENTS[Environment.US_PAPER]


def _without(row: dict[str, str], key: str) -> dict[str, str]:
    return {k: v for k, v in row.items() if k != key}


def _domestic_with(row: dict[str, str]) -> FakeKiwoom:
    return domestic_fake().reply("kt00018", {**KT00018_REPLY, "acnt_evlt_remn_indv_tot": [row]})


def _us_with(row: dict[str, str]) -> FakeKiwoom:
    return us_fake().reply("ust21070", {**UST21070_REPLY, "result_list": [row]})


def _holdings(fake: FakeKiwoom, listings=None) -> Holdings:
    return Holdings(KiwoomClient(FAKE_ENV, transport=httpx.MockTransport(fake)), listings)


async def _us_listing(spec):
    return {"AAPL": {"name": "애플", "exchange": "NASDAQ"}}


def test_a_missing_screen_only_cell_is_blank_and_selling_still_reads_the_holding(make_client):
    """화면에만 쓰는 칸(보유비중)이 빠져도 매도 확인은 계속된다."""
    client = make_client(_domestic_with(_without(KT00018_HOLDING, "poss_rt")))

    account = client.get("/api/environments/domestic_paper/account")
    assert account.status_code == 200
    assert account.json()["holdings"][0]["weight"] is None
    holding = client.get("/api/environments/domestic_paper/holdings/005930")
    assert holding.status_code == 200
    assert holding.json()["sellable_quantity"] == 3


def test_a_missing_us_screen_only_cell_is_blank_and_selling_still_reads_the_holding(make_client):
    client = make_client(_us_with(_without(UST21070_HOLDING, "pl_amt_krw")))

    account = client.get("/api/environments/us_paper/account")
    assert account.status_code == 200
    assert account.json()["holdings"][0]["profit_loss_krw"] is None
    holding = client.get("/api/environments/us_paper/holdings/AAPL")
    assert holding.status_code == 200
    assert holding.json()["sellable_quantity"] == 395


@pytest.mark.parametrize("key", ["stk_cd", "rmnd_qty", "trde_able_qty", "crd_tp"])
def test_a_missing_required_domestic_cell_fails_the_screen_and_selling(make_client, key):
    """종목코드·수량·매도 가능 수량·신용구분은 빠지면 계좌 화면도 응답 형식 오류다."""
    client = make_client(_domestic_with(_without(KT00018_HOLDING, key)))

    for url in ("account", "holdings/005930"):
        response = client.get(f"/api/environments/domestic_paper/{url}")
        assert response.status_code == 502
        assert response.json()["error"]["kind"] == "response_format_error"


@pytest.mark.parametrize("key", ["stk_cd", "poss_qty", "sell_alowq"])
def test_a_missing_required_us_cell_fails_the_screen_and_selling(make_client, key):
    client = make_client(_us_with(_without(UST21070_HOLDING, key)))

    for url in ("account", "holdings/AAPL"):
        response = client.get(f"/api/environments/us_paper/{url}")
        assert response.status_code == 502
        assert response.json()["error"]["kind"] == "response_format_error"


def test_a_holding_without_a_name_shows_its_code(make_client):
    domestic = make_client(_domestic_with(_without(KT00018_HOLDING, "stk_nm")))
    body = domestic.get("/api/environments/domestic_paper/account").json()
    assert body["holdings"][0]["name"] == "005930"

    # 종목 목록에도 없고 잔고 TR의 이름도 없다.
    us = make_client(_us_with(_without(UST21070_HOLDING, "frgn_stk_nm")))
    body = us.get("/api/environments/us_paper/account").json()
    assert body["holdings"][0]["name"] == "AAPL"


@pytest.mark.anyio
async def test_sl_tp_positions_ignore_a_missing_screen_only_cell():
    domestic = _holdings(_domestic_with(_without(KT00018_HOLDING, "poss_rt")))
    assert [p["code"] for p in await domestic.positions(DOMESTIC_PAPER)] == ["005930"]

    us = _holdings(_us_with(_without(UST21070_HOLDING, "pl_amt_krw")), _us_listing)
    assert await us.positions(US_PAPER) == [
        {
            "code": "AAPL",
            "name": "애플",
            "purchase_price": Decimal("282.1603"),
            "credit": False,
            "exchange": "NASDAQ",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("spec", "fake"),
    [
        (DOMESTIC_PAPER, lambda: _domestic_with(_without(KT00018_HOLDING, "rmnd_qty"))),
        (US_PAPER, lambda: _us_with(_without(UST21070_HOLDING, "poss_qty"))),
    ],
    ids=["domestic", "us"],
)
async def test_sl_tp_positions_fail_on_a_missing_quantity(spec, fake):
    holdings = _holdings(fake(), _us_listing)

    with pytest.raises(AppError) as error:
        await holdings.positions(spec)
    assert error.value.kind == "response_format_error"


@pytest.mark.anyio
async def test_us_sl_tp_positions_fail_when_the_stock_list_is_unavailable():
    """거래소 없이 모든 종목을 조용히 빼지 않게, 목록을 받지 못하면 오류다."""

    async def failing(spec):
        raise AppError("kiwoom_unavailable", "키움 서버에 연결하지 못했습니다.", 503)

    holdings = _holdings(_us_with(UST21070_HOLDING), failing)

    with pytest.raises(AppError) as error:
        await holdings.positions(US_PAPER)
    assert error.value.kind == "stock_list_unavailable"
