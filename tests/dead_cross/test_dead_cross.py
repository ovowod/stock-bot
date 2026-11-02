import json
import logging

import pytest

URL = "/api/environments/{}/dead-cross"
SL_TP = "/api/environments/{}/sl-tp"
TRAILING_STOP = "/api/environments/{}/trailing-stop"
SAMSUNG = {"code": "005930", "name": "삼성전자", "exchange": None}
APPLE = {"code": "AAPL", "name": "애플", "exchange": "NASDAQ"}
ON = {"enabled": True, "bar_period": "5m", "short_period": 5, "long_period": 20}


def session(start: str = "09:00", end: str = "10:00", **extra):
    return {
        "start": start,
        "end": end,
        "enabled": False,
        "bar_period": None,
        "short_period": None,
        "long_period": None,
        **extra,
    }


def stored(data_dir, environment="domestic_paper"):
    return json.loads((data_dir / "auto-trading" / f"{environment}.json").read_text())


def test_unsaved_environment_opens_as_empty_settings(make_client):
    response = make_client().get(URL.format("us_paper"))

    assert response.status_code == 200
    assert response.json() == {
        "environment": "us_paper",
        "market": "us",
        "timezone": "America/New_York",
        "regular_hours": {"start": "09:30", "end": "16:00"},
        "can_enable": True,
        "updated_at": None,
        "excluded": [],
        "sessions": [],
    }


def test_saved_settings_survive_a_server_restart(make_client, data_dir):
    on = session("09:00", "15:30", **ON)

    saved = make_client().put(
        URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": [on], "extra": 1}
    )

    assert saved.status_code == 200
    assert saved.json()["updated_at"]
    restarted = make_client().get(URL.format("domestic_paper")).json()
    assert restarted["excluded"] == [SAMSUNG]
    assert restarted["sessions"] == [on]
    assert restarted["updated_at"] == saved.json()["updated_at"]
    assert stored(data_dir)["dead_cross"]["sessions"] == [on]


def test_each_environment_keeps_its_own_settings(make_client):
    client = make_client()

    client.put(URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})
    client.put(URL.format("us_paper"), json={"excluded": [APPLE], "sessions": []})

    assert client.get(URL.format("domestic_paper")).json()["excluded"] == [SAMSUNG]
    assert client.get(URL.format("us_paper")).json()["excluded"] == [APPLE]
    assert client.get(URL.format("domestic_real")).json()["updated_at"] is None


def test_saving_dead_cross_keeps_the_other_features(make_client, data_dir):
    client = make_client()
    sl_tp = client.put(SL_TP.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})
    trailing_stop = client.put(
        TRAILING_STOP.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []}
    )

    client.put(URL.format("domestic_paper"), json={"excluded": [], "sessions": [session()]})

    file = stored(data_dir)
    assert file["sl_tp"]["excluded"] == [SAMSUNG]
    assert file["sl_tp"]["updated_at"] == sl_tp.json()["updated_at"]
    assert file["trailing_stop"]["excluded"] == [SAMSUNG]
    assert file["trailing_stop"]["updated_at"] == trailing_stop.json()["updated_at"]
    assert file["dead_cross"]["excluded"] == []


def test_saving_other_features_keeps_dead_cross(make_client, data_dir):
    client = make_client()
    saved = client.put(URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": []})

    client.put(SL_TP.format("domestic_paper"), json={"excluded": [], "sessions": []})
    client.put(TRAILING_STOP.format("domestic_paper"), json={"excluded": [], "sessions": []})

    assert stored(data_dir)["dead_cross"]["excluded"] == [SAMSUNG]
    assert stored(data_dir)["dead_cross"]["updated_at"] == saved.json()["updated_at"]
    assert client.get(URL.format("domestic_paper")).json()["excluded"] == [SAMSUNG]


@pytest.mark.parametrize("feature", [URL, SL_TP, TRAILING_STOP])
def test_broken_dead_cross_section_blocks_every_feature(make_client, data_dir, feature):
    path = data_dir / "auto-trading" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    content = json.dumps(
        {"version": 2, "dead_cross": {"excluded": [], "sessions": [session(short_period=1)]}}
    )
    path.write_text(content)
    client = make_client()

    read = client.get(feature.format("domestic_paper"))
    write = client.put(feature.format("domestic_paper"), json={"excluded": [], "sessions": []})

    assert read.status_code == 500
    assert write.status_code == 500
    assert path.read_text() == content


def test_unreadable_file_is_reported_as_dead_cross_error(make_client, data_dir):
    path = data_dir / "auto-trading" / "domestic_paper.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    response = make_client().get(URL.format("domestic_paper"))

    assert response.status_code == 500
    assert response.json()["error"]["kind"] == "dead_cross_settings_unreadable"
    assert response.json()["error"]["message"] == "Dead Cross 설정 파일을 읽지 못했습니다."


@pytest.mark.parametrize("feature", [URL, SL_TP, TRAILING_STOP])
def test_real_file_with_an_enabled_dead_cross_session_blocks_every_feature(
    make_client, data_dir, feature
):
    path = data_dir / "auto-trading" / "us_real.json"
    path.parent.mkdir(parents=True)
    on = session("09:30", "10:00", **ON)
    path.write_text(json.dumps({"version": 2, "dead_cross": {"excluded": [], "sessions": [on]}}))

    assert make_client().get(feature.format("us_real")).status_code == 500


@pytest.mark.parametrize(
    ("environment", "bar_period"),
    [
        ("domestic_paper", "1m"),
        ("domestic_paper", "45m"),
        ("domestic_paper", "60m"),
        ("domestic_paper", "1d"),
        ("us_paper", "40m"),
        ("us_paper", "120m"),
        ("us_paper", "1d"),
    ],
)
def test_bar_periods_the_market_offers_are_saved(make_client, environment, bar_period):
    item = session("10:00", "11:00", bar_period=bar_period)

    response = make_client().put(URL.format(environment), json={"excluded": [], "sessions": [item]})

    assert response.status_code == 200
    assert response.json()["sessions"] == [item]


BAR_PERIOD = "1번째 Dead Cross 세션: 봉 주기가 이 시장에서 고를 수 있는 값이 아닙니다."


@pytest.mark.parametrize(
    ("environment", "bar_period"),
    [
        ("domestic_paper", "40m"),
        ("domestic_paper", "120m"),
        ("us_paper", "45m"),
        ("domestic_paper", "2m"),
        ("domestic_paper", "1w"),
        ("domestic_paper", "1"),
        ("domestic_paper", 5),
    ],
)
def test_bar_periods_the_market_does_not_offer_are_rejected(
    make_client, data_dir, environment, bar_period
):
    item = session("10:00", "11:00", bar_period=bar_period)

    response = make_client().put(URL.format(environment), json={"excluded": [], "sessions": [item]})

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "invalid_dead_cross_settings"
    assert response.json()["error"]["message"] == BAR_PERIOD
    assert not data_dir.exists()


@pytest.mark.parametrize(
    ("short_period", "long_period", "enabled"),
    [
        (5, 20, True),
        (2, 3, True),
        (119, 120, True),
        (None, None, False),
        (5, None, False),
    ],
)
def test_paper_sessions_save_switch_and_periods(make_client, short_period, long_period, enabled):
    client = make_client()
    item = session(
        enabled=enabled,
        bar_period="5m" if enabled else None,
        short_period=short_period,
        long_period=long_period,
    )

    response = client.put(URL.format("domestic_paper"), json={"excluded": [], "sessions": [item]})

    assert response.status_code == 200
    assert client.get(URL.format("domestic_paper")).json()["sessions"] == [item]


SHORT = "1번째 Dead Cross 세션: 단기 이동평균 기간은 2~119 사이의 정수여야 합니다."
LONG = "1번째 Dead Cross 세션: 장기 이동평균 기간은 3~120 사이의 정수여야 합니다."
ORDER = "1번째 Dead Cross 세션: 단기 이동평균 기간은 장기 이동평균 기간보다 작아야 합니다."
ALL = "1번째 Dead Cross 세션: 켜진 세션에는 봉 주기와 단기·장기 이동평균 기간이 모두 있어야 합니다."


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"short_period": 1}, SHORT),
        ({"short_period": 120}, SHORT),
        ({"short_period": 0}, SHORT),
        ({"short_period": -5}, SHORT),
        ({"short_period": 5.5}, SHORT),
        ({"short_period": 5.0}, SHORT),
        ({"short_period": "5"}, SHORT),
        ({"short_period": True}, SHORT),
        ({"long_period": 2}, LONG),
        ({"long_period": 121}, LONG),
        ({"long_period": False}, LONG),
        # 꺼진 세션이어도 단기가 장기 이상이면 거부한다.
        ({"short_period": 20, "long_period": 20}, ORDER),
        ({"short_period": 30, "long_period": 20}, ORDER),
        ({**ON, "bar_period": None}, ALL),
        ({**ON, "short_period": None}, ALL),
        ({**ON, "long_period": None}, ALL),
        ({"enabled": True}, ALL),
    ],
)
def test_invalid_periods_are_rejected(make_client, data_dir, change, message):
    response = make_client().put(
        URL.format("domestic_paper"), json={"excluded": [], "sessions": [session(**change)]}
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == message
    assert not data_dir.exists()


def test_sessions_may_touch_and_may_overlap_other_features(make_client):
    client = make_client()
    trailing_stop_session = {
        "start": "09:00",
        "end": "15:30",
        "enabled": False,
        "activation_profit": None,
        "drop_from_peak": None,
    }
    client.put(
        TRAILING_STOP.format("domestic_paper"),
        json={"excluded": [], "sessions": [trailing_stop_session]},
    )
    sessions = [session("09:00", "10:00"), session("10:00", "11:00")]

    response = client.put(URL.format("domestic_paper"), json={"excluded": [], "sessions": sessions})

    assert response.status_code == 200
    assert response.json()["sessions"] == sessions


@pytest.mark.parametrize(
    ("environment", "body", "message"),
    [
        (
            "domestic_paper",
            {"excluded": [], "sessions": [session("08:59", "10:00")]},
            "1번째 Dead Cross 세션: 정규장(09:00–15:30)",
        ),
        (
            "us_paper",
            {"excluded": [], "sessions": [session("15:00", "16:01")]},
            "1번째 Dead Cross 세션: 정규장(09:30–16:00)",
        ),
        (
            "domestic_paper",
            {"excluded": [], "sessions": [session("09:00", "10:30"), session("10:00", "11:00")]},
            "2번째 Dead Cross 세션: 시작 시각이 앞 세션의 종료 시각보다 이릅니다.",
        ),
        (
            "domestic_paper",
            {"excluded": [SAMSUNG, SAMSUNG], "sessions": []},
            "2번째 제외종목: 이미 추가된 종목입니다(005930).",
        ),
        (
            "us_paper",
            {"excluded": [{**APPLE, "exchange": None}], "sessions": []},
            "1번째 제외종목: 거래소는 NYSE·NASDAQ·AMEX",
        ),
    ],
)
def test_session_times_and_excluded_stocks_follow_the_shared_rules(
    make_client, data_dir, environment, body, message
):
    response = make_client().put(URL.format(environment), json=body)

    assert response.status_code == 400
    assert response.json()["error"]["kind"] == "invalid_dead_cross_settings"
    assert response.json()["error"]["message"].startswith(message)
    assert not data_dir.exists()


def test_real_environment_refuses_enabled_sessions_and_keeps_the_file(make_client, data_dir):
    client = make_client()
    off = session(bar_period="1d", short_period=5, long_period=20)
    assert client.put(
        URL.format("domestic_real"), json={"excluded": [], "sessions": [off]}
    ).is_success
    before = stored(data_dir, "domestic_real")

    response = client.put(
        URL.format("domestic_real"), json={"excluded": [], "sessions": [{**off, "enabled": True}]}
    )

    assert response.status_code == 403
    assert response.json()["error"]["kind"] == "dead_cross_enable_not_allowed"
    assert response.json()["error"]["message"] == "실전투자에서는 Dead Cross 세션을 켤 수 없습니다."
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
    on = session(**ON)

    client.put(URL.format("domestic_paper"), json={"excluded": [SAMSUNG], "sessions": [on]})
    client.put(
        URL.format("domestic_paper"), json={"excluded": [], "sessions": [session(long_period=2)]}
    )
    client.put(URL.format("domestic_real"), json={"excluded": [], "sessions": [on]})

    events = {r.getMessage(): getattr(r, "fields", {}) for r in caplog.records}
    assert events["dead_cross_settings_saved"]["excluded_count"] == 1
    assert events["dead_cross_settings_saved"]["enabled_count"] == 1
    assert "장기 이동평균 기간" in events["dead_cross_settings_rejected"]["cause"]
    assert events["dead_cross_enable_refused"]["enabled_count"] == 1
