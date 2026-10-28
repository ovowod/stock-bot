from tests.fake_kiwoom import FakeKiwoom, body_of

DOMESTIC_URL = "/api/environments/domestic_paper/quote"
US_URL = "/api/environments/us_paper/quote"


def test_domestic_quote_calls_ka10001_and_drops_the_direction_sign(make_client):
    fake = FakeKiwoom().reply("ka10001", {"stk_cd": "005930", "cur_prc": "-61300"})
    response = make_client(fake).get(DOMESTIC_URL, params={"code": "005930"})

    assert response.status_code == 200
    request = fake.calls("ka10001")[0]
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/dostk/stkinfo"
    assert body_of(request) == {"stk_cd": "005930"}
    body = response.json()
    assert body["code"] == "005930"
    assert body["price"] == 61300
    assert body["fetched_at"]


def test_us_quote_calls_usa20101_with_the_exchange_code(make_client):
    fake = FakeKiwoom().reply("usa20101", {"stk_cd": "NVDA", "cur_prc": "+201.4863"})
    response = make_client(fake).get(US_URL, params={"code": "NVDA", "exchange": "NASDAQ"})

    assert response.status_code == 200
    request = fake.calls("usa20101")[0]
    assert request.url.host == "mockapi.kiwoom.com"
    assert request.url.path == "/api/us/mrkcond"
    assert body_of(request) == {"stex_tp": "ND", "stk_cd": "NVDA"}
    assert response.json()["price"] == 201.4863


def test_us_quote_without_a_supported_exchange_is_rejected_without_calling_kiwoom(make_client):
    client = make_client()
    for params in ({"code": "ABCD"}, {"code": "ABCD", "exchange": "OTC"}):
        response = client.get(US_URL, params=params)
        assert response.status_code == 400
        assert response.json()["error"]["kind"] == "invalid_request"


def test_quote_without_a_code_is_rejected(make_client):
    assert make_client().get(DOMESTIC_URL).status_code == 400


def test_empty_price_is_null(make_client):
    fake = FakeKiwoom().reply("ka10001", {"stk_cd": "005930", "cur_prc": ""})
    assert make_client(fake).get(DOMESTIC_URL, params={"code": "005930"}).json()["price"] is None


def test_missing_or_non_numeric_price_is_a_response_format_error(make_client):
    for reply in ({"stk_cd": "005930"}, {"stk_cd": "005930", "cur_prc": "abc"}):
        fake = FakeKiwoom().reply("ka10001", reply)
        response = make_client(fake).get(DOMESTIC_URL, params={"code": "005930"})
        assert response.status_code == 502
        assert response.json()["error"]["kind"] == "response_format_error"
