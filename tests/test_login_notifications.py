import asyncio
import json

import httpx
import pytest

from stock_bot.app import create_app
from stock_bot.auth import describe_device
from stock_bot.notification import DiscordNotifier
from tests.fake_kiwoom import FAKE_ENV, domestic_fake

pytestmark = pytest.mark.anyio

PASSWORD = FAKE_ENV["PASSWORD"]
LOGIN = "/api/auth/login"
CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DISCORD_ENV = {"DISCORD_BOT_TOKEN": "discord-token-ZZZZ9999", "DISCORD_CHANNEL_ID": "123456789"}


class FakeDiscord:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.payloads: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(request.content))
        return httpx.Response(self.status, json={"id": "1"})

    async def wait_for(self, count: int) -> None:
        for _ in range(100):
            if len(self.payloads) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Discord 요청 {count}개를 기다렸지만 {len(self.payloads)}개")


def _client(discord: FakeDiscord) -> httpx.AsyncClient:
    app = create_app(
        environ=FAKE_ENV,
        transport=httpx.MockTransport(domestic_fake()),
        static_dir=None,
        log_dir=None,
        notifier=DiscordNotifier(DISCORD_ENV, transport=httpx.MockTransport(discord)),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.7", 51000)),
        base_url="http://test",
        headers={"User-Agent": CHROME_WINDOWS},
    )


def _fields(payload: dict) -> dict[str, str]:
    return {field["name"]: field["value"] for field in payload["embeds"][0]["fields"]}


async def test_successful_login_sends_access_info_to_discord():
    discord = FakeDiscord()
    async with _client(discord) as client:
        assert (await client.post(LOGIN, json={"password": PASSWORD})).status_code == 204
        await discord.wait_for(1)

    payload = discord.payloads[0]
    assert payload["content"] == ""
    assert payload["embeds"][0]["title"] == "로그인"
    fields = _fields(payload)
    assert fields["IP"] == "10.0.0.7"
    assert fields["브라우저·OS"] == "Chrome / Windows"
    assert "시각" in fields
    assert PASSWORD not in json.dumps(payload, ensure_ascii=False)


async def test_lock_sends_one_notification_without_the_typed_password():
    discord = FakeDiscord()
    async with _client(discord) as client:
        for index in range(5):
            await client.post(LOGIN, json={"password": f"guess-{index}"})
        for _ in range(3):
            assert (await client.post(LOGIN, json={"password": "guess-x"})).status_code == 429
        await discord.wait_for(1)
        await asyncio.sleep(0.05)

    assert len(discord.payloads) == 1
    payload = discord.payloads[0]
    assert payload["embeds"][0]["title"] == "로그인 시도 제한"
    fields = _fields(payload)
    assert fields["IP"] == "10.0.0.7"
    assert fields["연속 실패"] == "5회"
    assert fields["브라우저·OS"] == "Chrome / Windows"
    assert "풀리는 시각" in fields
    assert "guess-" not in json.dumps(payload, ensure_ascii=False)


async def test_failed_discord_does_not_block_login():
    discord = FakeDiscord(status=500)
    async with _client(discord) as client:
        response = await client.post(LOGIN, json={"password": PASSWORD})
        await discord.wait_for(1)

    assert response.status_code == 204


async def test_login_works_when_discord_is_not_configured():
    app = create_app(
        environ=FAKE_ENV,
        transport=httpx.MockTransport(domestic_fake()),
        static_dir=None,
        log_dir=None,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post(LOGIN, json={"password": PASSWORD})).status_code == 204


@pytest.mark.parametrize(
    "user_agent, expected",
    [
        (CHROME_WINDOWS, "Chrome / Windows"),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
            "Edge / Windows",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Safari / iOS",
        ),
        (
            "Mozilla/5.0 (Linux; Android 15; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36",
            "Chrome / Android",
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:131.0) Gecko/20100101 Firefox/131.0",
            "Firefox / macOS",
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
            "Firefox / Linux",
        ),
        ("curl/8.5.0", "알 수 없음 / 알 수 없음"),
        ("", "알 수 없음 / 알 수 없음"),
    ],
)
def test_describe_device(user_agent, expected):
    assert describe_device(user_agent) == expected
