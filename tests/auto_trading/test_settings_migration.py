import json
import logging

import pytest

from stock_bot.auto_trading.settings import SettingsMigrationError

SL_TP = "/api/environments/{}/sl-tp"
SAMSUNG = {"code": "005930", "name": "삼성전자", "exchange": None}
SESSION = {"start": "09:00", "end": "10:00", "enabled": True, "take_profit": "5", "stop_loss": None}


def write_legacy(data_dir, environment="domestic_paper", **overrides) -> str:
    """예전 SL / TP 파일(version 1)을 쓰고 그 내용을 돌려준다."""
    path = data_dir / "sl-tp" / f"{environment}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        {
            "version": 1,
            "updated_at": "2026-10-24T01:23:45+00:00",
            "excluded": [SAMSUNG],
            "sessions": [SESSION],
            **overrides,
        },
        ensure_ascii=False,
    )
    path.write_text(content, encoding="utf-8")
    return content


def test_legacy_sl_tp_file_moves_into_the_auto_trading_file(make_client, data_dir, caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    legacy = write_legacy(data_dir)

    settings = make_client().get(SL_TP.format("domestic_paper")).json()

    assert settings["updated_at"] == "2026-10-24T01:23:45+00:00"
    assert settings["excluded"] == [SAMSUNG]
    assert settings["sessions"] == [SESSION]
    stored = json.loads((data_dir / "auto-trading" / "domestic_paper.json").read_text())
    assert stored == {
        "version": 2,
        "sl_tp": {
            "updated_at": "2026-10-24T01:23:45+00:00",
            "excluded": [SAMSUNG],
            "sessions": [SESSION],
        },
    }
    assert (data_dir / "sl-tp" / "domestic_paper.json").read_text(encoding="utf-8") == legacy
    migrated = next(r for r in caplog.records if r.getMessage() == "auto_trading_settings_migrated")
    assert migrated.fields["legacy_path"].endswith("sl-tp/domestic_paper.json")
    assert migrated.fields["path"].endswith("auto-trading/domestic_paper.json")


def test_existing_auto_trading_file_wins_over_the_legacy_file(make_client, data_dir):
    client = make_client()
    client.put(SL_TP.format("domestic_paper"), json={"excluded": [], "sessions": []})
    write_legacy(data_dir)

    settings = make_client().get(SL_TP.format("domestic_paper")).json()

    assert settings["excluded"] == []


@pytest.mark.parametrize(
    ("environment", "content"),
    [
        ("domestic_paper", "{broken"),
        ("domestic_paper", json.dumps({"version": 2, "excluded": [], "sessions": []})),
        (
            "domestic_paper",
            json.dumps({"version": 1, "excluded": [SAMSUNG, SAMSUNG], "sessions": []}),
        ),
        # 실전 환경에 켜진 세션이 있는 예전 파일도 옮기지 않는다.
        ("domestic_real", json.dumps({"version": 1, "excluded": [], "sessions": [SESSION]})),
    ],
)
def test_unmovable_legacy_file_stops_the_server(
    make_client, data_dir, caplog, environment, content
):
    path = data_dir / "sl-tp" / f"{environment}.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    with pytest.raises(SettingsMigrationError):
        make_client()

    assert not (data_dir / "auto-trading").exists()
    assert path.read_text(encoding="utf-8") == content
    failed = next(
        r for r in caplog.records if r.getMessage() == "auto_trading_settings_migration_failed"
    )
    assert failed.fields["legacy_path"].endswith(f"sl-tp/{environment}.json")
    assert failed.fields["cause"]
