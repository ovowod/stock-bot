import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from stock_bot.app import create_app
from tests.fake_kiwoom import FAKE_ENV, domestic_fake

PASSWORD = FAKE_ENV["PASSWORD"]
ACCOUNT = "/api/environments/domestic_paper/account"
LOGIN = "/api/auth/login"
SESSION = "/api/auth/session"


@pytest.fixture
def anonymous(make_client):
    """로그인하지 않은 클라이언트와 가짜 키움."""

    def factory(environ: dict[str, str] | None = None) -> tuple[TestClient, object]:
        fake = domestic_fake()
        client = make_client(fake, environ=environ)
        client.cookies.clear()
        return client, fake

    return factory


def _log_text(caplog) -> str:
    return json.dumps(
        [r.getMessage() for r in caplog.records]
        + [getattr(r, "fields", {}) for r in caplog.records],
        ensure_ascii=False,
        default=str,
    )


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", ACCOUNT),
        ("GET", "/api/environments/domestic_paper/holdings/005930"),
        ("GET", "/api/environments/domestic_paper/rankings/gainers"),
        ("GET", "/api/environments/domestic_paper/stocks?q=삼성"),
        ("GET", "/api/environments/domestic_paper/quote?code=005930"),
        ("POST", "/api/environments/domestic_paper/orders"),
        ("GET", "/api/environments"),
    ],
)
def test_api_without_login_is_401_and_kiwoom_is_not_called(anonymous, method, path):
    client, fake = anonymous()

    response = client.request(method, path)

    assert response.status_code == 401
    assert response.json()["error"]["kind"] == "unauthorized"
    assert fake.requests == []


def test_correct_password_sets_a_browser_session_cookie(anonymous):
    client, _ = anonymous()

    response = client.post(LOGIN, json={"password": PASSWORD})

    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("stock_bot_session=")
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "samesite=strict" in lowered
    assert "path=/" in lowered
    assert "max-age" not in lowered
    assert "expires" not in lowered
    assert "secure" not in lowered
    assert client.get(ACCOUNT).status_code == 200
    assert client.get(SESSION).status_code == 200


def test_cookie_is_secure_over_https(make_client):
    client = make_client(domestic_fake())
    client.base_url = "https://testserver"

    response = client.post(LOGIN, json={"password": PASSWORD})

    assert "secure" in response.headers["set-cookie"].lower()


def test_wrong_password_is_401(anonymous):
    client, _ = anonymous()

    response = client.post(LOGIN, json={"password": PASSWORD + "x"})

    assert response.status_code == 401
    assert response.json()["error"]["kind"] == "invalid_password"
    assert "set-cookie" not in response.headers
    assert client.get(SESSION).status_code == 401


def test_password_is_compared_as_is_without_trimming(anonymous):
    client, _ = anonymous()

    assert client.post(LOGIN, json={"password": f" {PASSWORD} "}).status_code == 401


def test_non_ascii_password_works(anonymous):
    client, _ = anonymous({**FAKE_ENV, "PASSWORD": "주식-봇-비밀번호"})

    assert client.post(LOGIN, json={"password": "주식-봇-틀림"}).status_code == 401
    assert client.post(LOGIN, json={"password": "주식-봇-비밀번호"}).status_code == 204


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b'["' + PASSWORD.encode() + b'"]',
        b'{"pass": "' + PASSWORD.encode() + b'"}',
        b'{"password": ["' + PASSWORD.encode() + b'"]}',
    ],
)
def test_malformed_login_body_is_400_without_echoing_input(anonymous, caplog, content):
    caplog.set_level(logging.INFO, logger="stock_bot")
    client, _ = anonymous()

    response = client.post(LOGIN, content=content, headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "invalid_request"
    assert PASSWORD not in response.text
    assert PASSWORD not in _log_text(caplog)


def test_new_login_replaces_the_previous_session(make_client):
    first = make_client(domestic_fake())
    second = TestClient(first.app)

    assert second.post(LOGIN, json={"password": PASSWORD}).status_code == 204

    assert first.get(ACCOUNT).status_code == 401
    assert second.get(ACCOUNT).status_code == 200


def test_post_from_another_origin_is_403(make_client):
    client = make_client(domestic_fake())

    response = client.post(
        LOGIN, json={"password": PASSWORD}, headers={"Origin": "http://evil.test"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["kind"] == "forbidden_origin"


def test_post_from_same_origin_is_allowed(anonymous):
    client, _ = anonymous()

    response = client.post(
        LOGIN, json={"password": PASSWORD}, headers={"Origin": "http://testserver"}
    )

    assert response.status_code == 204


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_password_refuses_to_start(value):
    environ = {k: v for k, v in FAKE_ENV.items() if k != "PASSWORD"}
    if value is not None:
        environ["PASSWORD"] = value

    with pytest.raises(RuntimeError, match="PASSWORD"):
        create_app(environ=environ, static_dir=None, log_dir=None)


def test_password_and_session_id_are_not_logged(anonymous, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    client, _ = anonymous()

    client.post(LOGIN, json={"password": PASSWORD + "x"})
    client.post(LOGIN, json={"password": PASSWORD})
    client.get(ACCOUNT)
    session_id = client.cookies["stock_bot_session"]

    text = _log_text(caplog)
    assert PASSWORD not in text
    assert session_id not in text
    events = [r.getMessage() for r in caplog.records]
    assert "login_failed" in events
    assert "login_succeeded" in events


LOGOUT = "/api/auth/logout"


def test_logout_ends_the_session_and_clears_the_cookie(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    client = make_client(domestic_fake())
    old_cookie = client.cookies["stock_bot_session"]

    response = client.post(LOGOUT)

    assert response.status_code == 204
    assert 'stock_bot_session=""' in response.headers["set-cookie"]
    stale = TestClient(client.app)
    stale.cookies.set("stock_bot_session", old_cookie)
    assert stale.get(ACCOUNT).status_code == 401
    assert "logout" in [r.getMessage() for r in caplog.records]


def test_logout_without_cookie_keeps_the_current_session(make_client):
    owner = make_client(domestic_fake())
    stranger = TestClient(owner.app)

    assert stranger.post(LOGOUT).status_code == 204

    assert owner.get(ACCOUNT).status_code == 200


def test_logout_with_an_old_cookie_keeps_the_current_session(make_client):
    old = make_client(domestic_fake())
    old_cookie = old.cookies["stock_bot_session"]
    current = TestClient(old.app)
    current.post(LOGIN, json={"password": PASSWORD})
    stale = TestClient(old.app)
    stale.cookies.set("stock_bot_session", old_cookie)

    response = stale.post(LOGOUT)

    assert response.status_code == 204
    assert current.get(ACCOUNT).status_code == 200


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, *, minutes: float = 0, hours: float = 0) -> None:
        self.now += minutes * 60 + hours * 3600


def _app_with_clock(clock: FakeClock):
    return create_app(
        environ=FAKE_ENV,
        transport=httpx.MockTransport(domestic_fake()),
        static_dir=None,
        log_dir=None,
        clock=clock,
    )


def _logged_in_with_clock(clock: FakeClock) -> TestClient:
    client = TestClient(_app_with_clock(clock))
    client.post(LOGIN, json={"password": PASSWORD})
    return client


def test_session_expires_after_30_idle_minutes(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock()
    client = _logged_in_with_clock(clock)

    clock.advance(minutes=29)
    assert client.get(SESSION).status_code == 200
    clock.advance(minutes=29)
    assert client.get(ACCOUNT).status_code == 200
    clock.advance(minutes=30)
    assert client.get(ACCOUNT).status_code == 401
    clock.advance(minutes=1)
    assert client.get(SESSION).status_code == 401

    expired = [r for r in caplog.records if r.getMessage() == "session_expired"]
    assert [r.fields["reason"] for r in expired] == ["idle"]


def test_session_expires_12_hours_after_login_even_when_active(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock()
    client = _logged_in_with_clock(clock)

    for _ in range(24 * 2 - 1):
        clock.advance(minutes=15)
        assert client.get(SESSION).status_code == 200
    clock.advance(minutes=15)

    assert client.get(SESSION).status_code == 401
    expired = [r for r in caplog.records if r.getMessage() == "session_expired"]
    assert [r.fields["reason"] for r in expired] == ["absolute"]


def _fail(client: TestClient, times: int) -> None:
    for _ in range(times):
        assert client.post(LOGIN, json={"password": "wrong"}).status_code == 401


def _lock(client: TestClient) -> httpx.Response:
    _fail(client, 4)
    return client.post(LOGIN, json={"password": "wrong"})


def test_fifth_failure_locks_the_ip_for_15_minutes(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    clock = FakeClock()
    client = TestClient(_app_with_clock(clock))

    fifth = _lock(client)

    assert fifth.status_code == 429
    error = fifth.json()["error"]
    assert error["kind"] == "login_locked"
    assert error["retry_after_seconds"] == 15 * 60
    assert "15분 후" in error["message"]

    locked = client.post(LOGIN, json={"password": PASSWORD})
    assert locked.status_code == 429
    events = [r.getMessage() for r in caplog.records]
    assert events.count("login_locked") == 1
    assert "login_rejected_locked" in events

    clock.advance(minutes=14)
    assert (
        client.post(LOGIN, json={"password": PASSWORD}).json()["error"]["retry_after_seconds"] == 60
    )
    clock.advance(minutes=1)
    assert client.post(LOGIN, json={"password": PASSWORD}).status_code == 204


def test_attempts_while_locked_are_not_counted_and_count_restarts_after_unlock():
    clock = FakeClock()
    client = TestClient(_app_with_clock(clock))
    _lock(client)
    for _ in range(3):
        assert client.post(LOGIN, json={"password": "wrong"}).status_code == 429

    clock.advance(minutes=15)
    _fail(client, 4)

    assert client.post(LOGIN, json={"password": PASSWORD}).status_code == 204


def test_success_resets_the_failure_count():
    client = TestClient(_app_with_clock(FakeClock()))
    _fail(client, 4)
    assert client.post(LOGIN, json={"password": PASSWORD}).status_code == 204

    _fail(client, 4)

    assert client.post(LOGIN, json={"password": PASSWORD}).status_code == 204


def test_lock_is_per_ip():
    app = _app_with_clock(FakeClock())
    attacker = TestClient(app, client=("10.0.0.9", 50000))
    owner = TestClient(app, client=("10.0.0.2", 50000))
    _lock(attacker)

    assert attacker.post(LOGIN, json={"password": PASSWORD}).status_code == 429
    assert owner.post(LOGIN, json={"password": PASSWORD}).status_code == 204


def test_malformed_body_does_not_count_as_a_failure():
    client = TestClient(_app_with_clock(FakeClock()))
    for _ in range(6):
        assert (
            client.post(
                LOGIN, content=b"{bad", headers={"Content-Type": "application/json"}
            ).status_code
            == 400
        )

    assert client.post(LOGIN, json={"password": PASSWORD}).status_code == 204


def test_forwarded_for_header_is_ignored():
    client = TestClient(_app_with_clock(FakeClock()))
    for index in range(5):
        client.post(
            LOGIN, json={"password": "wrong"}, headers={"X-Forwarded-For": f"10.1.1.{index}"}
        )

    assert client.post(LOGIN, json={"password": PASSWORD}).status_code == 429


def test_session_and_rejection_logs_carry_access_info(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    agent = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"}
    client = make_client(domestic_fake())
    TestClient(client.app).get(ACCOUNT, headers=agent)
    client.post(LOGIN, json={"password": PASSWORD}, headers=agent)
    client.post(LOGOUT, headers=agent)
    client.post(LOGIN, json={"password": PASSWORD}, headers={**agent, "Origin": "http://evil.test"})

    for event in ("unauthorized_request", "session_replaced", "logout", "origin_rejected"):
        record = next(r for r in caplog.records if r.getMessage() == event)
        assert record.fields["ip"] == "testclient", event
        assert "device" in record.fields, event
        assert "user_agent" in record.fields, event
    logout = next(r for r in caplog.records if r.getMessage() == "logout")
    assert logout.fields["device"] == "Firefox / Linux"
