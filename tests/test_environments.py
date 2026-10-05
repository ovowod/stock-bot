import os
from pathlib import Path

import pytest

from stock_bot.config import PROJECT_ROOT, load_env_file


def test_lists_the_four_trading_environments_without_credentials(make_client):
    response = make_client().get("/api/environments")

    assert response.status_code == 200
    assert response.json() == [
        {"value": "domestic_real", "label": "국내 실전", "market": "domestic", "is_real": True},
        {"value": "us_real", "label": "미국 실전", "market": "us", "is_real": True},
        {"value": "domestic_paper", "label": "국내 모의", "market": "domestic", "is_real": False},
        {"value": "us_paper", "label": "미국 모의", "market": "us", "is_real": False},
    ]
    assert "key" not in response.text.lower()


def test_rejects_unknown_environment_without_calling_kiwoom(make_client):
    response = make_client().get("/api/environments/real/account")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["kind"] == "unknown_environment"
    assert body["error"]["request_id"]


def test_env_file_path_is_project_root():
    assert (PROJECT_ROOT / "pyproject.toml").is_file()


def test_env_file_does_not_override_existing_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file = tmp_path / ".env"
    env_file.write_text("STOCK_BOT_TEST_A=from-file\nSTOCK_BOT_TEST_B=from-file\n")
    monkeypatch.setenv("STOCK_BOT_TEST_A", "from-shell")
    monkeypatch.delenv("STOCK_BOT_TEST_B", raising=False)

    load_env_file(env_file)

    assert os.environ["STOCK_BOT_TEST_A"] == "from-shell"
    assert os.environ["STOCK_BOT_TEST_B"] == "from-file"
    monkeypatch.delenv("STOCK_BOT_TEST_B")


def test_missing_env_file_is_ignored(tmp_path: Path):
    load_env_file(tmp_path / "absent.env")
