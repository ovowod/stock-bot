"""Trailing Stop 상태 조회 API. 감시를 시작하지 않은 채 응답 모양과 이유를 본다."""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from stock_bot.app import create_app
from tests.fake_kiwoom import FAKE_ENV, REAL_ENV, US_PAPER_ENV, FakeKiwoom

URL = "/api/environments/{}/trailing-stop/status"
KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
# 월요일 한국 장중.
AT_10 = datetime(2026, 10, 26, 10, 0, tzinfo=KST)


def session(start: str, end: str, enabled: bool = False, **criteria: str | None) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "enabled": enabled,
        "activation_profit": criteria.get("activation_profit"),
        "drop_from_peak": criteria.get("drop_from_peak"),
    }


ON = {"enabled": True, "activation_profit": "5", "drop_from_peak": "3"}


def write_settings(
    data_dir: Path,
    environment: str,
    *sessions: dict[str, Any],
    excluded: tuple[str, ...] = (),
) -> None:
    exchange = "NASDAQ" if environment.startswith("us") else None
    path = data_dir / "auto-trading" / f"{environment}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    section = {
        "updated_at": None,
        "excluded": [{"code": c, "name": "제외", "exchange": exchange} for c in excluded],
        "sessions": list(sessions),
    }
    path.write_text(json.dumps({"version": 2, "trailing_stop": section}), encoding="utf-8")


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
    write_settings(
        data_dir,
        "domestic_paper",
        session("09:00", "10:00", **ON),
        session("10:00", "11:00", **ON),
        excluded=("000660", "035420"),
    )

    response = status_client().get(URL.format("domestic_paper"))

    assert response.status_code == 200
    # 서버 시작(lifespan) 없이 띄워 감시가 시작되지 않았다. 켜진 세션이 지금 시각을 포함해도
    # 감시 안 함이다. 감시 중인 상태는 test_watch.py가 본다.
    assert response.json() == {
        "environment": "domestic_paper",
        "market": "domestic",
        "timezone": "Asia/Seoul",
        "watching": False,
        "not_watching_reason": "no_session",
        "session": session("10:00", "11:00", **ON),
        "excluded": ["000660", "035420"],
        "stocks": [],
    }
    assert kiwoom.requests == []


@pytest.mark.parametrize(
    ("environment", "environ", "reason"),
    [
        ("domestic_real", FAKE_ENV, "real"),
        ("us_real", FAKE_ENV, "real"),
        ("domestic_paper", FAKE_ENV, "no_session"),
        ("us_paper", FAKE_ENV, "no_session"),
        # 국내 모의 인증정보만 빠졌다.
        ("domestic_paper", US_PAPER_ENV, "no_credentials"),
        ("us_paper", REAL_ENV, "no_credentials"),
        # 실전은 인증정보가 없어도 실전투자가 먼저다.
        ("domestic_real", US_PAPER_ENV, "real"),
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


def test_unreadable_settings_file_is_a_reason_not_an_error(status_client, data_dir, kiwoom):
    path = data_dir / "auto-trading" / "us_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    response = status_client().get(URL.format("us_paper"))

    assert response.status_code == 200
    body = response.json()
    assert body["not_watching_reason"] == "settings_unreadable"
    assert body["session"] is None
    assert body["excluded"] == []
    assert kiwoom.requests == []


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        # 시작 시각은 포함한다. 꺼진 세션도 담는다.
        (9, 0, session("09:00", "10:00")),
        (9, 59, session("09:00", "10:00")),
        # 맞닿은 시각은 뒤 세션이다.
        (10, 0, session("10:00", "11:00", **ON)),
        # 종료 시각은 뺀다.
        (11, 0, None),
        (8, 59, None),
    ],
)
def test_session_is_the_one_covering_now_even_when_off(
    status_client, data_dir, hour, minute, expected
):
    write_settings(
        data_dir, "domestic_paper", session("09:00", "10:00"), session("10:00", "11:00", **ON)
    )
    at = datetime(2026, 10, 26, hour, minute, 30, tzinfo=KST)

    body = status_client(at=at).get(URL.format("domestic_paper")).json()

    assert body["session"] == expected


def test_us_session_is_found_in_new_york_time(status_client, data_dir):
    write_settings(data_dir, "us_paper", session("09:30", "10:00", **ON), excluded=("AAPL",))
    # 2026-10-26 13:30 UTC는 뉴욕 09:30(서머타임), 한국 22:30이다.
    at = datetime(2026, 10, 26, 13, 30, tzinfo=UTC)

    body = status_client(at=at).get(URL.format("us_paper")).json()

    assert body["market"] == "us"
    assert body["timezone"] == "America/New_York"
    assert body["session"] == session("09:30", "10:00", **ON)
    assert body["excluded"] == ["AAPL"]


def test_real_environment_still_shows_the_session_and_excluded_stocks(status_client, data_dir):
    off = session("09:00", "15:30", activation_profit="5", drop_from_peak="3")
    write_settings(data_dir, "domestic_real", off, excluded=("005930",))

    body = status_client().get(URL.format("domestic_real")).json()

    assert body["not_watching_reason"] == "real"
    assert body["session"] == off
    assert body["excluded"] == ["005930"]
    assert body["stocks"] == []


def test_request_without_login_is_401(status_client, kiwoom):
    client = status_client()
    client.cookies.clear()

    response = client.get(URL.format("domestic_paper"))

    assert response.status_code == 401
    assert kiwoom.requests == []


def test_unknown_environment_is_rejected(status_client):
    response = status_client().get(URL.format("paper"))

    assert response.status_code == 404
    assert response.json()["error"]["kind"] == "unknown_environment"


def test_successful_status_polls_stay_below_info(status_client, data_dir, caplog):
    # 화면이 3초마다 부르므로 성공한 조회가 INFO 로그를 채우지 않게 한다.
    write_settings(data_dir, "domestic_paper", session("09:00", "15:30", **ON))
    client = status_client()
    caplog.set_level(logging.DEBUG, logger="stock_bot")
    caplog.clear()

    client.get(URL.format("domestic_paper"))
    client.get(URL.format("domestic_paper"))

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO] == []
    read = [r for r in caplog.records if r.getMessage() == "trailing_stop_status_read"]
    assert len(read) == 2
    assert read[0].fields["not_watching_reason"] == "no_session"


def test_failed_status_reads_are_logged_at_info_or_above(status_client, data_dir, caplog):
    path = data_dir / "auto-trading" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    client = status_client()
    caplog.set_level(logging.INFO, logger="stock_bot")

    client.get(URL.format("domestic_paper"))
    client.get(URL.format("paper"))

    events = {r.getMessage(): r for r in caplog.records}
    assert events["trailing_stop_settings_unreadable"].levelno >= logging.WARNING
    assert events["api_error"].fields["kind"] == "unknown_environment"
    # 실패한 요청은 평소처럼 api_request를 INFO로 남긴다.
    assert events["api_request"].fields["status"] == 404
