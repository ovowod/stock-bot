import logging

import pytest

from tests.fake_kiwoom import (
    FAKE_ENV,
    KT00001_REPLY,
    KT00018_REPLY,
    body_of,
    domestic_fake,
    kiwoom_error,
    us_fake,
)

URL = "/api/environments/domestic_paper/account"


def test_domestic_paper_uses_paper_domain_and_paper_kr_credentials(make_client):
    fake = domestic_fake()
    response = make_client(fake).get(URL)

    assert response.status_code == 200
    assert {r.url.host for r in fake.requests} == {"mockapi.kiwoom.com"}
    token_request = body_of(fake.token_requests()[0])
    assert token_request == {
        "grant_type": "client_credentials",
        "appkey": FAKE_ENV["PAPER_KR_APP_KEY"],
        "secretkey": FAKE_ENV["PAPER_KR_APP_SECRET"],
    }


@pytest.mark.parametrize(
    ("environment", "host", "prefix", "account_no"),
    [
        ("domestic_real", "api.kiwoom.com", "REAL", "5012345611"),
        ("us_real", "api.kiwoom.com", "REAL", "5012345611"),
        ("domestic_paper", "mockapi.kiwoom.com", "PAPER_KR", "8100000111"),
        ("us_paper", "mockapi.kiwoom.com", "PAPER_US", "8200000222"),
    ],
)
def test_each_environment_uses_its_own_domain_and_credentials(
    make_client, environment, host, prefix, account_no
):
    fake = domestic_fake(account_no) if "domestic" in environment else us_fake(account_no)
    response = make_client(fake).get(f"/api/environments/{environment}/account")

    assert response.status_code == 200
    assert {r.url.host for r in fake.requests} == {host}
    assert body_of(fake.token_requests()[0])["appkey"] == FAKE_ENV[f"{prefix}_APP_KEY"]


def test_kiwoom_requests_carry_common_headers(make_client):
    fake = domestic_fake()
    make_client(fake).get(URL)

    request = fake.calls("kt00018")[0]
    assert request.method == "POST"
    assert request.url.path == "/api/dostk/acnt"
    assert request.headers["authorization"] == "Bearer token-1-XYZW9876"
    assert request.headers["content-type"] == "application/json;charset=UTF-8"
    assert body_of(request) == {"qry_tp": "1", "dmst_stex_tp": "KRX"}
    assert body_of(fake.calls("kt00001")[0]) == {"qry_tp": "3"}


def test_domestic_account_is_converted_for_the_screen(make_client):
    response = make_client(domestic_fake()).get(URL)
    body = response.json()

    assert body["environment"] == "domestic_paper"
    assert body["market"] == "domestic"
    assert body["account_no"] == "8100****11"
    assert body["fetched_at"]
    assert body["summary"] == {
        "estimated_assets": 1012632507,
        "total_evaluation": 25789890,
        "total_purchase": 17598258,
        "total_profit_loss": 8138825,
        "total_return_rate": 46.25,
    }
    assert body["deposit"] == {
        "deposit": 17534,
        "orderable": 85341,
        "withdrawable": 85341,
        "d1_estimated": 17450,
        "d2_estimated": 12550,
    }
    assert body["holdings"] == [
        {
            "code": "005930",
            "name": "삼성전자",
            "quantity": 3,
            "tradable_quantity": 3,
            "purchase_price": 124500,
            "current_price": 59000,
            "purchase_amount": 373500,
            "evaluation_amount": 177000,
            "profit_loss": -196888,
            "return_rate": -52.71,
            "weight": 2.12,
        }
    ]


def test_token_is_reused_across_requests(make_client):
    fake = domestic_fake()
    client = make_client(fake)

    client.get(URL)
    client.get(URL)

    assert len(fake.token_requests()) == 1


def test_account_number_is_checked_once_per_token(make_client):
    fake = domestic_fake()
    client = make_client(fake)

    assert client.get(URL).status_code == 200
    assert client.get(URL).status_code == 200

    assert len(fake.calls("ka00001")) == 1


def test_skipped_account_check_is_logged(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    client = make_client(domestic_fake())

    client.get(URL)
    client.get(URL)

    assert [r.getMessage() for r in caplog.records].count("account_check_skipped") == 1


def test_account_number_is_checked_again_after_the_token_changes(make_client):
    fake = domestic_fake().reply(
        "kt00018", KT00018_REPLY, kiwoom_error(8005, "Token이 유효하지 않습니다"), KT00018_REPLY
    )
    client = make_client(fake)

    client.get(URL)
    client.get(URL)  # 잔고 조회 중 토큰이 바뀐다.
    client.get(URL)

    assert len(fake.calls("ka00001")) == 2
    assert fake.calls("ka00001")[1].headers["authorization"] == "Bearer token-2-XYZW9876"


def test_token_changed_during_the_account_check_is_checked_again(make_client):
    fake = domestic_fake().reply(
        "ka00001", kiwoom_error(8005, "Token이 유효하지 않습니다"), {"acctNo": "8100000111"}
    )
    client = make_client(fake)

    assert client.get(URL).status_code == 200
    assert client.get(URL).status_code == 200

    assert len(fake.calls("ka00001")) == 3


def test_account_mismatch_is_checked_again_every_time(make_client):
    fake = domestic_fake(account_no="9999999999")
    client = make_client(fake)

    client.get(URL)
    client.get(URL)

    assert len(fake.calls("ka00001")) == 2


def test_environments_sharing_a_token_check_the_account_separately(make_client):
    fake = us_fake("5012345611").reply("kt00018", KT00018_REPLY).reply("kt00001", KT00001_REPLY)
    client = make_client(fake)

    assert client.get("/api/environments/domestic_real/account").status_code == 200
    assert client.get("/api/environments/us_real/account").status_code == 200

    assert len(fake.token_requests()) == 1
    assert len(fake.calls("ka00001")) == 2


def test_account_mismatch_stops_before_balance_lookup(make_client):
    fake = domestic_fake(account_no="9999999999")
    response = make_client(fake).get(URL)

    assert response.status_code == 409
    assert response.json()["error"]["kind"] == "account_mismatch"
    assert fake.calls("kt00018") == []
    assert fake.calls("kt00001") == []
    assert "9999999999" not in response.text


def test_account_number_comparison_ignores_separators(make_client):
    environ = {**FAKE_ENV, "PAPER_KR_ACCOUNT_NO": "8100000-111"}
    response = make_client(domestic_fake(), environ=environ).get(URL)

    assert response.status_code == 200


def test_empty_holdings_is_a_normal_result(make_client):
    fake = domestic_fake().reply("kt00018", {**KT00018_REPLY, "acnt_evlt_remn_indv_tot": []})
    response = make_client(fake).get(URL)

    assert response.status_code == 200
    assert response.json()["holdings"] == []


def test_missing_environment_variables_are_named_without_values(make_client):
    environ = {k: v for k, v in FAKE_ENV.items() if k != "PAPER_KR_APP_SECRET"}
    response = make_client(environ=environ).get(URL)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["kind"] == "config_error"
    assert error["missing"] == ["PAPER_KR_APP_SECRET"]
    assert FAKE_ENV["PAPER_KR_APP_KEY"] not in response.text


def test_secrets_never_reach_responses_or_logs(make_client, caplog):
    caplog.set_level(logging.DEBUG, logger="stock_bot")
    fake = domestic_fake()
    response = make_client(fake).get(URL)

    logged = caplog.text + "".join(str(getattr(r, "fields", "")) for r in caplog.records)
    for secret in (
        FAKE_ENV["PAPER_KR_APP_KEY"],
        FAKE_ENV["PAPER_KR_APP_SECRET"],
        FAKE_ENV["PAPER_KR_ACCOUNT_NO"],
        "token-1-XYZW9876",
    ):
        assert secret not in response.text
        assert secret not in logged
    assert "kt00018" in logged


def test_eight_digit_account_number_matches_the_first_eight_digits(make_client):
    """ka00001은 10자리(뒤 2자리는 키움의 계좌 분류값)를 돌려준다. 8자리 설정도 허용한다."""
    environ = {**FAKE_ENV, "PAPER_KR_ACCOUNT_NO": "81000001"}
    assert make_client(domestic_fake("8100000111"), environ=environ).get(URL).status_code == 200


def test_eight_digit_account_number_still_detects_a_different_account(make_client):
    environ = {**FAKE_ENV, "PAPER_KR_ACCOUNT_NO": "81000002"}
    fake = domestic_fake("8100000111")
    response = make_client(fake, environ=environ).get(URL)

    assert response.status_code == 409
    assert fake.calls("kt00018") == []
