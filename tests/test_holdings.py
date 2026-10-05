from tests.fake_kiwoom import (
    KT00018_HOLDING,
    KT00018_REPLY,
    UST21070_REPLY,
    FakeKiwoom,
    body_of,
    kiwoom_error,
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


def test_us_holding_is_looked_up_by_ticker(make_client):
    fake = FakeKiwoom().reply("ust21070", UST21070_REPLY)
    response = make_client(fake).get("/api/environments/us_paper/holdings/AAPL")

    assert response.status_code == 200
    assert response.json()["sellable_quantity"] == 395
    assert body_of(fake.calls("ust21070")[0]) == {"stex_tp": "", "stk_cd": "AAPL"}


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
