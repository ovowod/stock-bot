import json
import logging

import pytest

GET = "/api/environments/{}/sl-tp"


def test_unsaved_environment_opens_as_empty_settings(make_client):
    response = make_client().get(GET.format("domestic_paper"))

    assert response.status_code == 200
    assert response.json() == {
        "environment": "domestic_paper",
        "market": "domestic",
        "timezone": "Asia/Seoul",
        "regular_hours": {"start": "09:00", "end": "15:30"},
        "can_enable": True,
        "updated_at": None,
        "excluded": [],
        "sessions": [],
    }


def test_us_and_real_environments_report_their_market_and_enable_rule(make_client):
    client = make_client()

    us_real = client.get(GET.format("us_real")).json()
    domestic_real = client.get(GET.format("domestic_real")).json()

    assert us_real["timezone"] == "America/New_York"
    assert us_real["regular_hours"] == {"start": "09:30", "end": "16:00"}
    assert us_real["can_enable"] is False
    assert domestic_real["can_enable"] is False


SAMSUNG = {"code": "005930", "name": "삼성전자", "exchange": None}
APPLE = {"code": "AAPL", "name": "애플", "exchange": "NASDAQ"}


def test_saved_excluded_stocks_survive_a_server_restart(make_client, data_dir):
    saved = make_client().put(
        GET.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": [], "extra": 1}
    )

    assert saved.status_code == 200
    assert saved.json()["excluded"] == [SAMSUNG]
    assert saved.json()["updated_at"]
    restarted = make_client().get(GET.format("domestic_paper")).json()
    assert restarted["excluded"] == [SAMSUNG]
    assert restarted["updated_at"] == saved.json()["updated_at"]
    stored = json.loads((data_dir / "sl-tp" / "domestic_paper.json").read_text())
    assert stored["version"] == 1
    assert "extra" not in stored


def test_each_environment_keeps_its_own_file(make_client, data_dir):
    client = make_client()

    client.put(GET.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})
    client.put(GET.format("us_paper"), json={"excluded": [APPLE], "sessions": []})
    client.put(GET.format("domestic_real"), json={"excluded": [], "sessions": []})

    assert client.get(GET.format("domestic_paper")).json()["excluded"] == [SAMSUNG]
    assert client.get(GET.format("us_paper")).json()["excluded"] == [APPLE]
    assert client.get(GET.format("domestic_real")).json()["excluded"] == []
    assert client.get(GET.format("us_real")).json()["updated_at"] is None
    assert sorted(p.name for p in (data_dir / "sl-tp").iterdir()) == [
        "domestic_paper.json",
        "domestic_real.json",
        "us_paper.json",
    ]


@pytest.mark.parametrize(
    ("environment", "excluded", "message"),
    [
        ("domestic_paper", [SAMSUNG, SAMSUNG], "2번째 제외종목: 이미 추가된 종목입니다(005930)."),
        ("domestic_paper", [{**SAMSUNG, "code": " "}], "1번째 제외종목: 종목코드가 비어 있습니다."),
        ("domestic_paper", [{**SAMSUNG, "name": ""}], "1번째 제외종목: 종목명이 비어 있습니다."),
        ("domestic_paper", [{**SAMSUNG, "exchange": "KRX"}], "1번째 제외종목: 국내 종목에는"),
        ("us_paper", [{**APPLE, "exchange": "NP"}], "1번째 제외종목: 거래소는 NYSE·NASDAQ·AMEX"),
        ("us_paper", [{**APPLE, "exchange": None}], "1번째 제외종목: 거래소는"),
        ("domestic_paper", "005930", "제외종목 형식이 올바르지 않습니다."),
    ],
)
def test_invalid_excluded_stocks_are_rejected_without_writing(
    make_client, data_dir, environment, excluded, message
):
    response = make_client().put(
        GET.format(environment), json={"excluded": excluded, "sessions": []}
    )

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "invalid_sl_tp_settings"
    assert response.json()["error"]["message"].startswith(message)
    assert not (data_dir / "sl-tp").exists()


def test_broken_file_is_reported_and_left_untouched(make_client, data_dir):
    path = data_dir / "sl-tp" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    response = make_client().get(GET.format("domestic_paper"))

    assert response.status_code == 500
    assert response.json()["error"]["kind"] == "sl_tp_settings_unreadable"
    assert path.read_text() == "{not json"


def test_file_breaking_the_rules_is_unreadable(make_client, data_dir):
    path = data_dir / "sl-tp" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "excluded": [SAMSUNG, SAMSUNG], "sessions": []}))

    response = make_client().get(GET.format("domestic_paper"))

    assert response.json()["error"]["kind"] == "sl_tp_settings_unreadable"


def test_write_failure_keeps_the_previous_file(make_client, data_dir):
    client = make_client()
    client.put(GET.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})
    folder = data_dir / "sl-tp"
    before = (folder / "domestic_paper.json").read_text()
    folder.chmod(0o500)
    try:
        response = client.put(GET.format("domestic_paper"), json={"excluded": [], "sessions": []})
    finally:
        folder.chmod(0o700)

    assert response.status_code == 500
    assert response.json()["error"]["kind"] == "sl_tp_settings_write_failed"
    assert (folder / "domestic_paper.json").read_text() == before
    assert [p.name for p in folder.iterdir()] == ["domestic_paper.json"]


@pytest.mark.parametrize("method", ["get", "put"])
def test_requests_without_login_are_401_and_write_nothing(make_client, data_dir, method):
    client = make_client()
    client.cookies.clear()

    response = client.request(
        method.upper(), GET.format("domestic_paper"), json={"excluded": [], "sessions": []}
    )

    assert response.status_code == 401
    assert not data_dir.exists()


@pytest.mark.parametrize("method", ["get", "put"])
def test_unknown_environment_is_rejected_and_writes_nothing(make_client, data_dir, method):
    response = make_client().request(
        method.upper(), GET.format("paper"), json={"excluded": [], "sessions": []}
    )

    assert response.status_code == 404
    assert response.json()["error"]["kind"] == "unknown_environment"
    assert not data_dir.exists()


def test_put_from_another_origin_is_403_even_when_logged_in(make_client, data_dir):
    response = make_client().put(
        GET.format("domestic_paper"),
        json={"excluded": [SAMSUNG], "sessions": []},
        headers={"Origin": "http://evil.test"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["kind"] == "forbidden_origin"
    assert not data_dir.exists()


def test_save_and_rejection_are_logged_with_counts(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    client = make_client()

    client.put(GET.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})
    client.put(GET.format("domestic_paper"), json={"excluded": [SAMSUNG, SAMSUNG], "sessions": []})

    events = {r.getMessage(): getattr(r, "fields", {}) for r in caplog.records}
    assert events["sl_tp_settings_saved"]["excluded_count"] == 1
    assert events["sl_tp_settings_saved"]["enabled_count"] == 0
    assert "2번째 제외종목" in events["sl_tp_settings_rejected"]["cause"]
    assert "삼성전자" not in str(events["sl_tp_settings_saved"])
