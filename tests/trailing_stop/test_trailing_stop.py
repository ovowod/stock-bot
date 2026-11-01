import json
import logging

import pytest

URL = "/api/environments/{}/trailing-stop"
SL_TP = "/api/environments/{}/sl-tp"
SAMSUNG = {"code": "005930", "name": "삼성전자", "exchange": None}
APPLE = {"code": "AAPL", "name": "애플", "exchange": "NASDAQ"}


def session(start: str = "09:00", end: str = "10:00", **extra):
    return {
        "start": start,
        "end": end,
        "enabled": False,
        "activation_profit": None,
        "drop_from_peak": None,
        **extra,
    }


def stored(data_dir, environment="domestic_paper"):
    return json.loads((data_dir / "auto-trading" / f"{environment}.json").read_text())


def test_unsaved_environment_opens_as_empty_settings(make_client):
    response = make_client().get(URL.format("us_real"))

    assert response.status_code == 200
    assert response.json() == {
        "environment": "us_real",
        "market": "us",
        "timezone": "America/New_York",
        "regular_hours": {"start": "09:30", "end": "16:00"},
        "can_enable": False,
        "updated_at": None,
        "excluded": [],
        "sessions": [],
    }


def test_saved_settings_survive_a_server_restart(make_client, data_dir):
    on = session(enabled=True, activation_profit="5", drop_from_peak="2.5")

    saved = make_client().put(
        URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": [on], "extra": 1}
    )

    assert saved.status_code == 200
    assert saved.json()["updated_at"]
    restarted = make_client().get(URL.format("domestic_paper")).json()
    assert restarted["excluded"] == [SAMSUNG]
    assert restarted["sessions"] == [on]
    assert restarted["updated_at"] == saved.json()["updated_at"]
    assert stored(data_dir)["trailing_stop"]["sessions"] == [on]


def test_each_environment_keeps_its_own_settings(make_client):
    client = make_client()

    client.put(URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})
    client.put(URL.format("us_paper"), json={"excluded": [APPLE], "sessions": []})

    assert client.get(URL.format("domestic_paper")).json()["excluded"] == [SAMSUNG]
    assert client.get(URL.format("us_paper")).json()["excluded"] == [APPLE]
    assert client.get(URL.format("domestic_real")).json()["updated_at"] is None


def test_saving_one_feature_keeps_the_other(make_client, data_dir):
    client = make_client()
    sl_tp = client.put(SL_TP.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})

    client.put(URL.format("domestic_paper"), json={"excluded": [], "sessions": [session()]})
    client.put(SL_TP.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})

    assert client.get(URL.format("domestic_paper")).json()["sessions"] == [session()]
    assert client.get(SL_TP.format("domestic_paper")).json()["excluded"] == [SAMSUNG]
    file = stored(data_dir)
    assert file["trailing_stop"]["excluded"] == []
    assert file["sl_tp"]["excluded"] == [SAMSUNG]
    assert sl_tp.json()["updated_at"] <= file["sl_tp"]["updated_at"]


def test_trailing_stop_save_keeps_the_sl_tp_saved_time(make_client, data_dir):
    client = make_client()
    sl_tp = client.put(SL_TP.format("domestic_paper"), json={"excluded": [], "sessions": []})

    client.put(URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})

    assert stored(data_dir)["sl_tp"]["updated_at"] == sl_tp.json()["updated_at"]


def test_excluded_stocks_are_kept_apart_from_sl_tp(make_client):
    client = make_client()

    client.put(SL_TP.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})

    assert client.get(URL.format("domestic_paper")).json()["excluded"] == []


@pytest.mark.parametrize("feature", [URL, SL_TP])
def test_broken_trailing_stop_section_blocks_both_features(make_client, data_dir, feature):
    path = data_dir / "auto-trading" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    content = json.dumps(
        {
            "version": 2,
            "sl_tp": {"updated_at": None, "excluded": [], "sessions": []},
            "trailing_stop": {"excluded": [SAMSUNG, SAMSUNG], "sessions": []},
        }
    )
    path.write_text(content)
    client = make_client()

    read = client.get(feature.format("domestic_paper"))
    write = client.put(feature.format("domestic_paper"), json={"excluded": [], "sessions": []})

    assert read.status_code == 500
    assert write.status_code == 500
    assert path.read_text() == content


def test_unreadable_file_is_reported_as_trailing_stop_error(make_client, data_dir, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    path = data_dir / "auto-trading" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    response = make_client().get(URL.format("domestic_paper"))

    assert response.json()["error"]["kind"] == "trailing_stop_settings_unreadable"
    assert response.json()["error"]["message"] == "Trailing Stop 설정 파일을 읽지 못했습니다."
    events = {r.getMessage(): getattr(r, "fields", {}) for r in caplog.records}
    assert events["trailing_stop_settings_unreadable"]["error_type"] == "JSONDecodeError"


def test_real_file_with_an_enabled_trailing_stop_session_blocks_both_features(
    make_client, data_dir
):
    path = data_dir / "auto-trading" / "us_real.json"
    path.parent.mkdir(parents=True)
    on = session("09:30", "10:00", enabled=True, activation_profit="5", drop_from_peak="3")
    path.write_text(json.dumps({"version": 2, "trailing_stop": {"excluded": [], "sessions": [on]}}))
    client = make_client()

    assert client.get(URL.format("us_real")).status_code == 500
    assert client.get(SL_TP.format("us_real")).status_code == 500


def test_write_failure_keeps_the_previous_file(make_client, data_dir):
    client = make_client()
    client.put(URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})
    folder = data_dir / "auto-trading"
    before = (folder / "domestic_paper.json").read_text()
    folder.chmod(0o500)
    try:
        response = client.put(URL.format("domestic_paper"), json={"excluded": [], "sessions": []})
    finally:
        folder.chmod(0o700)

    assert response.status_code == 500
    assert response.json()["error"]["kind"] == "trailing_stop_settings_write_failed"
    assert (folder / "domestic_paper.json").read_text() == before


@pytest.mark.parametrize(
    ("environment", "excluded", "message"),
    [
        ("domestic_paper", [SAMSUNG, SAMSUNG], "2번째 제외종목: 이미 추가된 종목입니다(005930)."),
        ("domestic_paper", [{**SAMSUNG, "code": ""}], "1번째 제외종목: 종목코드가 비어 있습니다."),
        ("domestic_paper", [{**SAMSUNG, "name": " "}], "1번째 제외종목: 종목명이 비어 있습니다."),
        ("domestic_paper", [{**SAMSUNG, "exchange": "KRX"}], "1번째 제외종목: 국내 종목에는"),
        ("us_paper", [{**APPLE, "exchange": "NP"}], "1번째 제외종목: 거래소는 NYSE·NASDAQ·AMEX"),
    ],
)
def test_invalid_excluded_stocks_are_rejected_without_writing(
    make_client, data_dir, environment, excluded, message
):
    response = make_client().put(
        URL.format(environment), json={"excluded": excluded, "sessions": []}
    )

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "invalid_trailing_stop_settings"
    assert response.json()["error"]["message"].startswith(message)
    assert not data_dir.exists()


@pytest.mark.parametrize(
    ("environment", "start", "end"),
    [("domestic_paper", "09:00", "15:30"), ("us_paper", "09:30", "16:00")],
)
def test_session_may_span_the_whole_regular_hours(make_client, environment, start, end):
    response = make_client().put(
        URL.format(environment), json={"excluded": [], "sessions": [session(start, end)]}
    )

    assert response.status_code == 200


def test_sessions_may_touch_and_may_overlap_sl_tp_sessions(make_client):
    client = make_client()
    sl_tp_session = {**session("09:00", "15:30"), "take_profit": None, "stop_loss": None}
    del sl_tp_session["activation_profit"], sl_tp_session["drop_from_peak"]
    client.put(SL_TP.format("domestic_paper"), json={"excluded": [], "sessions": [sl_tp_session]})
    sessions = [session("09:00", "10:00"), session("10:00", "11:00")]

    response = client.put(URL.format("domestic_paper"), json={"excluded": [], "sessions": sessions})

    assert response.status_code == 200
    assert response.json()["sessions"] == sessions


@pytest.mark.parametrize(
    ("environment", "sessions", "message"),
    [
        (
            "domestic_paper",
            [session("08:59", "10:00")],
            "1번째 Trailing Stop 세션: 정규장(09:00–15:30)",
        ),
        (
            "domestic_paper",
            [session("15:00", "15:31")],
            "1번째 Trailing Stop 세션: 정규장(09:00–15:30)",
        ),
        ("us_paper", [session("09:29", "10:00")], "1번째 Trailing Stop 세션: 정규장(09:30–16:00)"),
        ("us_paper", [session("15:00", "16:01")], "1번째 Trailing Stop 세션: 정규장(09:30–16:00)"),
        (
            "domestic_paper",
            [session("10:00", "10:00")],
            "1번째 Trailing Stop 세션: 시작 시각이 종료",
        ),
        (
            "domestic_paper",
            [session("09:00", "10:30"), session("10:00", "11:00")],
            "2번째 Trailing Stop 세션: 시작 시각이 앞 세션의 종료 시각보다 이릅니다.",
        ),
        ("domestic_paper", [session("9:00", "10:00")], "1번째 Trailing Stop 세션: 시각은 HH:MM"),
        ("domestic_paper", "09:00", "Trailing Stop 세션 형식이 올바르지 않습니다."),
    ],
)
def test_invalid_session_times_are_rejected(make_client, data_dir, environment, sessions, message):
    response = make_client().put(
        URL.format(environment), json={"excluded": [], "sessions": sessions}
    )

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "invalid_trailing_stop_settings"
    assert response.json()["error"]["message"].startswith(message)
    assert not data_dir.exists()


@pytest.mark.parametrize(
    ("activation_profit", "drop_from_peak", "enabled"),
    [
        ("5", "2.5", True),
        ("0.01", "99.99", True),
        ("1000", "0.01", True),
        # 하락률이 활성화 수익률보다 커도 받는다(손실로 팔릴 수 있다).
        ("3", "5", True),
        (None, None, False),
        ("5", None, False),
    ],
)
def test_paper_sessions_save_switch_and_thresholds(
    make_client, activation_profit, drop_from_peak, enabled
):
    client = make_client()
    item = session(
        enabled=enabled, activation_profit=activation_profit, drop_from_peak=drop_from_peak
    )

    response = client.put(URL.format("domestic_paper"), json={"excluded": [], "sessions": [item]})

    assert response.status_code == 200
    assert client.get(URL.format("domestic_paper")).json()["sessions"] == [item]


ACTIVATION = "1번째 Trailing Stop 세션: 활성화 수익률은 0.01~1000"
DROP = "1번째 Trailing Stop 세션: 고점 대비 하락률은 0.01~99.99"
BOTH = "1번째 Trailing Stop 세션: 켜진 세션에는 활성화 수익률과 고점 대비 하락률이 모두"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"activation_profit": "1.234"}, ACTIVATION),
        ({"activation_profit": "0"}, ACTIVATION),
        ({"activation_profit": "-1"}, ACTIVATION),
        ({"activation_profit": "1000.01"}, ACTIVATION),
        ({"activation_profit": 5}, ACTIVATION),
        ({"activation_profit": "abc"}, ACTIVATION),
        ({"drop_from_peak": "100"}, DROP),
        ({"drop_from_peak": "0"}, DROP),
        ({"drop_from_peak": "0.001"}, DROP),
        ({"drop_from_peak": "٥"}, DROP),
        ({"enabled": "yes"}, "1번째 Trailing Stop 세션: 켜짐 여부가 올바르지 않습니다."),
        ({"enabled": True, "activation_profit": "5"}, BOTH),
        ({"enabled": True, "drop_from_peak": "3"}, BOTH),
        ({"enabled": True}, BOTH),
    ],
)
def test_invalid_thresholds_are_rejected(make_client, data_dir, change, message):
    response = make_client().put(
        URL.format("domestic_paper"), json={"excluded": [], "sessions": [session(**change)]}
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"].startswith(message)
    assert not data_dir.exists()


def test_real_environment_refuses_enabled_sessions_and_keeps_the_file(make_client, data_dir):
    client = make_client()
    off = session(activation_profit="5", drop_from_peak="3")
    assert client.put(
        URL.format("domestic_real"), json={"excluded": [], "sessions": [off]}
    ).is_success
    before = stored(data_dir, "domestic_real")

    response = client.put(
        URL.format("domestic_real"), json={"excluded": [], "sessions": [{**off, "enabled": True}]}
    )

    assert response.status_code == 403
    assert response.json()["error"]["kind"] == "trailing_stop_enable_not_allowed"
    assert (
        response.json()["error"]["message"] == "실전투자에서는 Trailing Stop 세션을 켤 수 없습니다."
    )
    assert stored(data_dir, "domestic_real") == before


@pytest.mark.parametrize("method", ["get", "put"])
def test_requests_without_login_are_401_and_write_nothing(make_client, data_dir, method):
    client = make_client()
    client.cookies.clear()

    response = client.request(
        method.upper(), URL.format("domestic_paper"), json={"excluded": [], "sessions": []}
    )

    assert response.status_code == 401
    assert not data_dir.exists()


@pytest.mark.parametrize("method", ["get", "put"])
def test_unknown_environment_is_rejected_and_writes_nothing(make_client, data_dir, method):
    response = make_client().request(
        method.upper(), URL.format("paper"), json={"excluded": [], "sessions": []}
    )

    assert response.status_code == 404
    assert not data_dir.exists()


def test_put_from_another_origin_is_403_even_when_logged_in(make_client, data_dir):
    response = make_client().put(
        URL.format("domestic_paper"),
        json={"excluded": [SAMSUNG], "sessions": []},
        headers={"Origin": "http://evil.test"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["kind"] == "forbidden_origin"
    assert not data_dir.exists()


def test_save_rejection_and_real_refusal_are_logged_with_counts(make_client, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    client = make_client()
    on = session(enabled=True, activation_profit="5", drop_from_peak="3")

    client.put(URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": [on]})
    client.put(URL.format("domestic_paper"), json={"excluded": [SAMSUNG, SAMSUNG], "sessions": []})
    client.put(URL.format("domestic_real"), json={"excluded": [], "sessions": [on]})

    events = {r.getMessage(): getattr(r, "fields", {}) for r in caplog.records}
    assert events["trailing_stop_settings_saved"]["excluded_count"] == 1
    assert events["trailing_stop_settings_saved"]["enabled_count"] == 1
    assert "2번째 제외종목" in events["trailing_stop_settings_rejected"]["cause"]
    assert events["trailing_stop_enable_refused"]["enabled_count"] == 1
    assert "삼성전자" not in str(events["trailing_stop_settings_saved"])
