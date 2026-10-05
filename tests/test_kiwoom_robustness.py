import asyncio
import logging
import threading

import httpx
import pytest

from stock_bot.app import create_app
from tests.fake_kiwoom import (
    FAKE_ENV,
    KT00018_HOLDING,
    KT00018_REPLY,
    ThreadedTransport,
    domestic_fake,
    kiwoom_error,
    page_response,
)

URL = "/api/environments/domestic_paper/account"


def test_continuation_pages_are_joined(make_client):
    second = {**KT00018_HOLDING, "stk_cd": "A000660", "stk_nm": "SK하이닉스"}
    fake = domestic_fake().reply(
        "kt00018",
        page_response(KT00018_REPLY, cont_yn="Y", next_key="page-2"),
        page_response({**KT00018_REPLY, "acnt_evlt_remn_indv_tot": [second]}),
    )
    response = make_client(fake).get(URL)

    assert response.status_code == 200
    assert [h["code"] for h in response.json()["holdings"]] == ["005930", "000660"]
    follow_up = fake.calls("kt00018")[1]
    assert follow_up.headers["cont-yn"] == "Y"
    assert follow_up.headers["next-key"] == "page-2"


def test_more_than_ten_pages_is_an_incomplete_result_error(make_client):
    fake = domestic_fake().reply(
        "kt00018", page_response(KT00018_REPLY, cont_yn="Y", next_key="more")
    )
    response = make_client(fake).get(URL)

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "incomplete_result"
    assert len(fake.calls("kt00018")) == 10


def test_invalid_token_is_reissued_and_retried_once(make_client):
    fake = domestic_fake().reply(
        "kt00018", kiwoom_error(8005, "Token이 유효하지 않습니다"), KT00018_REPLY
    )
    response = make_client(fake).get(URL)

    assert response.status_code == 200
    assert len(fake.token_requests()) == 2
    assert fake.calls("kt00018")[1].headers["authorization"] == "Bearer token-2-XYZW9876"


def test_invalid_token_twice_gives_up(make_client):
    fake = domestic_fake().reply("kt00018", kiwoom_error(8005, "Token이 유효하지 않습니다"))
    response = make_client(fake).get(URL)

    assert response.status_code == 502
    assert response.json()["error"]["return_code"] == 8005
    assert len(fake.calls("kt00018")) == 2


@pytest.mark.parametrize(
    ("code", "status", "kind"),
    [
        (8030, 503, "config_error"),
        (8031, 503, "config_error"),
        (1700, 429, "rate_limited"),
        (1701, 429, "rate_limited"),
        (1702, 429, "rate_limited"),
        (1511, 502, "kiwoom_error"),
    ],
)
def test_kiwoom_errors_are_classified_without_retry(make_client, code, status, kind):
    fake = domestic_fake().reply("kt00018", kiwoom_error(code, "오류", status=500))
    response = make_client(fake).get(URL)

    assert response.status_code == status
    error = response.json()["error"]
    assert error["kind"] == kind
    assert error["request_id"]
    assert len(fake.calls("kt00018")) == 1
    assert len(fake.token_requests()) == 1


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(500, text="<html>Internal Server Error</html>"),
        httpx.Response(200, json={"unexpected": True}),
        page_response({k: v for k, v in KT00018_REPLY.items() if k != "tot_evlt_amt"}),
    ],
    ids=["not-json", "no-return-code", "missing-field"],
)
def test_malformed_responses_are_format_errors(make_client, reply):
    response = make_client(domestic_fake().reply("kt00018", reply)).get(URL)

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "response_format_error"


def test_network_failure_is_connection_error(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    response = make_client(handler).get(URL)

    assert response.status_code == 502
    assert response.json()["error"]["kind"] == "connection_error"


def test_secrets_inside_kiwoom_messages_are_masked(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    leaked = (
        f"잘못된 요청 appkey={FAKE_ENV['PAPER_KR_APP_KEY']} "
        f"secret={FAKE_ENV['PAPER_KR_APP_SECRET']} token=token-1-XYZW9876 "
        f"acct={FAKE_ENV['PAPER_KR_ACCOUNT_NO']}"
    )
    fake = domestic_fake().reply("kt00018", kiwoom_error(1511, leaked))
    response = make_client(fake).get(URL)

    assert response.status_code == 502
    message = response.json()["error"]["message"]
    assert "잘못된 요청" in message
    logged = caplog.text + "".join(str(getattr(r, "fields", "")) for r in caplog.records)
    for secret in (
        FAKE_ENV["PAPER_KR_APP_KEY"],
        FAKE_ENV["PAPER_KR_APP_SECRET"],
        FAKE_ENV["PAPER_KR_ACCOUNT_NO"],
        "token-1-XYZW9876",
    ):
        assert secret not in response.text
        assert secret not in logged


@pytest.mark.anyio
async def test_concurrent_first_requests_issue_a_single_token():
    fake = domestic_fake()
    gate = threading.Event()

    def slow_token(request: httpx.Request) -> None:
        if request.url.path == "/oauth2/token":
            gate.wait(timeout=1)

    fake.on_request = slow_token
    app = create_app(
        environ=FAKE_ENV, transport=ThreadedTransport(fake), static_dir=None, log_dir=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        pending = [asyncio.create_task(client.get(URL)) for _ in range(3)]
        await asyncio.sleep(0.05)
        gate.set()
        responses = await asyncio.gather(*pending)

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert len(fake.token_requests()) == 1


@pytest.mark.anyio
async def test_late_invalid_token_does_not_discard_a_newer_token():
    """먼저 보낸 요청의 8005가 늦게 도착해도, 그사이 갱신된 토큰은 지우지 않는다."""
    fake = domestic_fake()
    first_kt00018 = threading.Event()
    release_first = threading.Event()
    state = {"kt00018": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("api-id") == "kt00018":
            state["kt00018"] += 1
            if state["kt00018"] == 1:
                first_kt00018.set()
                release_first.wait(timeout=1)
                return kiwoom_error(8005, "Token이 유효하지 않습니다")
        return fake(request)

    app = create_app(
        environ=FAKE_ENV, transport=ThreadedTransport(handler), static_dir=None, log_dir=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        slow = asyncio.create_task(client.get(URL))
        await asyncio.to_thread(first_kt00018.wait, 1)
        # 느린 요청이 걸려 있는 동안 토큰이 무효가 되어 다른 요청이 재발급했다고 가정한다.
        fake.reply("kt00018", kiwoom_error(8005, "Token이 유효하지 않습니다"), KT00018_REPLY)
        state["kt00018"] = 1
        fast = await client.get(URL)
        release_first.set()
        slow_response = await slow

    assert fast.status_code == 200
    assert slow_response.status_code == 200
    # 늦게 도착한 8005는 이미 갱신된 token-2를 지우지 않으므로 추가 발급 없이 token-2로 재시도한다.
    assert len(fake.token_requests()) == 2


def test_documented_code_inside_message_is_used_for_classification(make_client):
    """실제 모의 서버는 return_code=5와 함께 메시지 안에 문서의 오류 코드를 넣어 보냈다."""
    message = (
        "허용된 요청 개수를 초과하였습니다"
        "[1700:허용된 API 요청 개수를 초과하였습니다. 유량=1, API ID=ka00001]"
    )
    fake = domestic_fake().reply("kt00018", kiwoom_error(5, message))
    response = make_client(fake).get(URL)

    assert response.status_code == 429
    assert response.json()["error"]["kind"] == "rate_limited"
    assert len(fake.calls("kt00018")) == 1


def test_invalid_token_inside_message_is_retried(make_client):
    fake = domestic_fake().reply(
        "kt00018", kiwoom_error(5, "인증 실패[8005:Token이 유효하지 않습니다]"), KT00018_REPLY
    )
    response = make_client(fake).get(URL)

    assert response.status_code == 200
    assert len(fake.token_requests()) == 2


def test_non_numeric_value_is_shown_in_the_format_error(make_client):
    """숫자가 아닌 값을 받으면 원인을 찾을 수 있게 받은 값을 오류에 담는다."""
    reply = page_response({**KT00018_REPLY, "tot_evlt_amt": "--1,234"})
    response = make_client(domestic_fake().reply("kt00018", reply)).get(URL)

    assert response.status_code == 502
    assert "tot_evlt_amt 값이 숫자가 아닙니다: '--1,234'" in response.json()["error"]["message"]
