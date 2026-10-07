import json
import logging

import httpx
import pytest

from stock_bot import notification
from stock_bot.config import ENVIRONMENTS, Environment
from stock_bot.notification import DiscordNotifier, Embed, EmbedField

TOKEN = "discord-token-ZZZZ9999"
ENV = {"DISCORD_BOT_TOKEN": TOKEN, "DISCORD_CHANNEL_ID": "123456789012345678"}
PAPER_KR = ENVIRONMENTS[Environment.DOMESTIC_PAPER]

pytestmark = pytest.mark.anyio


class FakeDiscord:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses) or [httpx.Response(200, json={"id": "1"})]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def payload(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def notifier(fake: FakeDiscord, environ: dict[str, str] = ENV) -> DiscordNotifier:
    return DiscordNotifier(environ, transport=httpx.MockTransport(fake))


async def test_sends_text_with_bot_token_and_no_mentions():
    fake = FakeDiscord()

    assert await notifier(fake).send("@everyone 체결") is True

    request = fake.requests[0]
    assert str(request.url) == "https://discord.com/api/v10/channels/123456789012345678/messages"
    assert request.headers["Authorization"] == f"Bot {TOKEN}"
    assert request.headers["User-Agent"].startswith("DiscordBot (")
    assert fake.payload() == {"content": "@everyone 체결", "allowed_mentions": {"parse": []}}


async def test_environment_label_is_prefixed():
    fake = FakeDiscord()

    await notifier(fake).send("매수 체결", spec=PAPER_KR)

    assert fake.payload()["content"] == "[국내 모의] 매수 체결"


async def test_embed_is_sent_with_environment_label():
    fake = FakeDiscord()
    embed = Embed(
        title="매수 체결",
        description="삼성전자",
        color=0x2ECC71,
        fields=(EmbedField("수량", "10", inline=True),),
    )

    await notifier(fake).send(embed=embed, spec=PAPER_KR)

    payload = fake.payload()
    assert payload["content"] == "[국내 모의]"
    assert payload["embeds"] == [
        {
            "title": "매수 체결",
            "description": "삼성전자",
            "color": 0x2ECC71,
            "fields": [{"name": "수량", "value": "10", "inline": True}],
        }
    ]


async def test_long_text_is_cut_at_2000_characters(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = FakeDiscord()

    await notifier(fake).send("가" * 2500)

    content = fake.payload()["content"]
    assert len(content) == 2000
    assert content.endswith("…")
    assert any(r.getMessage() == "discord_truncated" for r in caplog.records)


async def test_embed_parts_are_cut_to_discord_limits():
    fake = FakeDiscord()
    fields = tuple(EmbedField("n" * 300, "v" * 1100) for _ in range(30))

    await notifier(fake).send(embed=Embed(title="t" * 300, description="d" * 5000, fields=fields))

    embed = fake.payload()["embeds"][0]
    assert len(embed["title"]) == 256
    assert len(embed["description"]) == 4096
    assert len(embed["fields"]) == 25
    assert len(embed["fields"][0]["name"]) == 256
    assert len(embed["fields"][0]["value"]) == 1024


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"DISCORD_BOT_TOKEN": TOKEN},
        {"DISCORD_CHANNEL_ID": "123"},
        {"DISCORD_BOT_TOKEN": TOKEN, "DISCORD_CHANNEL_ID": "not-a-number"},
    ],
)
async def test_missing_or_invalid_settings_disable_sending(environ):
    fake = FakeDiscord()
    discord = notifier(fake, environ)

    assert discord.enabled is False
    assert await discord.send("체결") is False
    assert fake.requests == []


async def test_rate_limit_waits_and_retries_once(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(notification.asyncio, "sleep", fake_sleep)
    fake = FakeDiscord(
        httpx.Response(429, json={"retry_after": 0.5, "global": False}),
        httpx.Response(200, json={"id": "1"}),
    )

    assert await notifier(fake).send("체결") is True
    assert waits == [0.5]
    assert len(fake.requests) == 2


async def test_rate_limit_twice_gives_up(monkeypatch):
    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(notification.asyncio, "sleep", fake_sleep)
    fake = FakeDiscord(httpx.Response(429, json={"retry_after": 0.5}))

    assert await notifier(fake).send("체결") is False
    assert len(fake.requests) == 2


async def test_long_rate_limit_wait_is_not_retried():
    fake = FakeDiscord(httpx.Response(429, json={"retry_after": 60}))

    assert await notifier(fake).send("체결") is False
    assert len(fake.requests) == 1


async def test_error_response_is_logged_without_token_and_not_retried(caplog):
    caplog.set_level(logging.INFO, logger="stock_bot")
    fake = FakeDiscord(httpx.Response(401, json={"message": f"401: Unauthorized {TOKEN}"}))

    assert await notifier(fake).send("체결") is False

    assert len(fake.requests) == 1
    errors = [r for r in caplog.records if r.getMessage() == "discord_response_error"]
    assert errors[0].fields["http_status"] == 401
    assert TOKEN not in json.dumps([r.fields for r in caplog.records if hasattr(r, "fields")])


async def test_connection_error_does_not_raise():
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    discord = DiscordNotifier(ENV, transport=httpx.MockTransport(fail))

    assert await discord.send("체결") is False
