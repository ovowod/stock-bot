"""Dead Cross 상태 조회 API. 감시를 시작하지 않은 채 응답 모양과 이유를 본다."""

from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from stock_bot.app import create_app
from tests.dead_cross.helpers import dc_session, write_dc
from tests.fake_kiwoom import FAKE_ENV, REAL_ENV, US_PAPER_ENV, FakeKiwoom
from tests.sl_tp.helpers import AT_10

URL = "/api/environments/{}/dead-cross/status"


@pytest.fixture
def kiwoom() -> FakeKiwoom:
    """응답을 하나도 두지 않았다. 상태 조회가 키움을 부르면 요청이 남는다."""
    return FakeKiwoom()


@pytest.fixture
def status_client(data_dir: Path, kiwoom: FakeKiwoom) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(at: datetime = AT_10, environ: dict[str, str] = FAKE_ENV) -> TestClient:
        app = create_app(
            environ=environ,
            transport=httpx.MockTransport(kiwoom),
            static_dir=None,
            log_dir=None,
            data_dir=data_dir,
            now=lambda: at,
        )
        client = TestClient(app)
        client.post("/api/auth/login", json={"password": environ["PASSWORD"]})
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


def test_paper_environment_answers_with_the_session_covering_now(status_client, data_dir, kiwoom):
    off = dc_session("09:00", "10:00", enabled=False)
    write_dc(data_dir, off, dc_session("10:00", "11:00"), excluded=("000660",))

    response = status_client().get(URL.format("domestic_paper"))

    assert response.status_code == 200
    # 서버 시작(lifespan) 없이 띄워 감시가 시작되지 않았다. 감시 중인 상태는 test_watch.py가 본다.
    assert response.json() == {
        "environment": "domestic_paper",
        "market": "domestic",
        "timezone": "Asia/Seoul",
        "watching": False,
        "not_watching_reason": "no_session",
        "session": dc_session("10:00", "11:00"),
        "excluded": ["000660"],
        "stocks": [],
    }
    assert kiwoom.requests == []


def test_a_session_on_a_weekend_is_not_shown(status_client, data_dir):
    write_dc(data_dir, dc_session("09:00", "11:00"))
    saturday = datetime(2026, 10, 31, 10, 0, tzinfo=AT_10.tzinfo)

    response = status_client(at=saturday).get(URL.format("domestic_paper"))

    assert response.json()["session"] is None


def test_a_disabled_session_covering_now_is_still_shown(status_client, data_dir):
    off = dc_session("09:00", "11:00", enabled=False, bar_period=None)
    write_dc(data_dir, off)

    body = status_client().get(URL.format("domestic_paper")).json()

    assert body["session"] == off


@pytest.mark.parametrize(
    ("environment", "environ", "reason"),
    [
        ("domestic_real", FAKE_ENV, "real"),
        ("us_real", FAKE_ENV, "real"),
        ("us_paper", FAKE_ENV, "no_session"),
        ("domestic_paper", US_PAPER_ENV, "no_credentials"),
        ("us_paper", REAL_ENV, "no_credentials"),
    ],
)
def test_each_environment_says_why_it_is_not_watching(
    status_client, kiwoom, environment, environ, reason
):
    body = status_client(environ=environ).get(URL.format(environment)).json()

    assert body["environment"] == environment
    assert body["watching"] is False
    assert body["not_watching_reason"] == reason
    assert body["stocks"] == []
    assert kiwoom.requests == []


def test_unreadable_settings_file_is_a_reason_not_an_error(status_client, data_dir):
    path = data_dir / "auto-trading" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    response = status_client().get(URL.format("domestic_paper"))

    assert response.status_code == 200
    assert response.json()["not_watching_reason"] == "settings_unreadable"
    assert response.json()["session"] is None


def test_status_requires_login(data_dir):
    app = create_app(
        environ=FAKE_ENV,
        transport=httpx.MockTransport(FakeKiwoom()),
        static_dir=None,
        log_dir=None,
        data_dir=data_dir,
    )
    with TestClient(app) as client:
        assert client.get(URL.format("domestic_paper")).status_code == 401
        client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        assert client.get(URL.format("nowhere")).status_code == 404
