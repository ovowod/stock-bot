import os
from pathlib import Path

import pytest

from stock_bot.core.config import PROJECT_ROOT, load_env_file
from tests.fake_kiwoom import domestic_fake, us_fake


def test_lists_the_four_trading_environments_without_credentials(make_client):
    response = make_client().get("/api/environments")

    assert response.status_code == 200
    assert response.json() == [
        {
            "value": "domestic_real",
            "label": "국내 실전",
            "market": "domestic",
            "is_real": True,
            "orders_allowed": False,
        },
        {
            "value": "us_real",
            "label": "미국 실전",
            "market": "us",
            "is_real": True,
            "orders_allowed": False,
        },
        {
            "value": "domestic_paper",
            "label": "국내 모의",
            "market": "domestic",
            "is_real": False,
            "orders_allowed": True,
        },
        {
            "value": "us_paper",
            "label": "미국 모의",
            "market": "us",
            "is_real": False,
            "orders_allowed": True,
        },
    ]
    assert "key" not in response.text.lower()


def test_rejects_unknown_environment_without_calling_kiwoom(make_client):
    response = make_client().get("/api/environments/real/account")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["kind"] == "unknown_environment"
    assert body["error"]["request_id"]


@pytest.mark.parametrize(
    ("environment", "fake"), [("domestic_paper", domestic_fake), ("us_paper", us_fake)]
)
def test_request_logs_carry_the_environment(make_client, json_logs, environment, fake):
    client = make_client(fake())

    assert client.get(f"/api/environments/{environment}/account").status_code == 200
    response = client.post(f"/api/environments/{environment}/orders", content=b"not json")
    assert response.status_code == 400

    kiwoom = [line["env"] for line in json_logs.lines if line["event"] == "kiwoom_request"]
    errors = [line["env"] for line in json_logs.lines if line["event"] == "api_error"]
    assert kiwoom and set(kiwoom) == {environment}
    assert errors == [environment]


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
