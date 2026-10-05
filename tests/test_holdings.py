import httpx

from tests.fake_kiwoom import (
    KT00018_HOLDING,
    KT00018_REPLY,
    UST21070_HOLDING,
    UST21070_REPLY,
    FakeKiwoom,
    body_of,
    kiwoom_error,
    page_response,
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
