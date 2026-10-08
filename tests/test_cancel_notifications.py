import asyncio
import json

import httpx
import pytest

from stock_bot.app import create_app
from stock_bot.notification import DiscordNotifier
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom, kiwoom_error
from tests.test_cancel_orders import CANCEL_REPLY, LIMIT_SELL, cancel
from tests.test_login_notifications import DISCORD_ENV, FakeDiscord

pytestmark = pytest.mark.anyio

URL = "/api/environments/domestic_paper/cancellations"


class HeldDiscord(FakeDiscord):
    """release를 부를 때까지 응답하지 않는 Discord."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:  # type: ignore[override]
        self.payloads.append(json.loads(request.content))
        await self.release.wait()
        return httpx.Response(200, json={"id": "1"})


def _kiwoom() -> FakeKiwoom:
    return FakeKiwoom().reply("ka10075", {"oso": [LIMIT_SELL]}).reply("kt10003", CANCEL_REPLY)


def _client(kiwoom: FakeKiwoom, discord: FakeDiscord) -> httpx.AsyncClient:
    app = create_app(
        environ=FAKE_ENV,
        transport=httpx.MockTransport(kiwoom),
        static_dir=None,
        log_dir=None,
        notifier=DiscordNotifier(DISCORD_ENV, transport=httpx.MockTransport(discord)),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _login(client: httpx.AsyncClient) -> None:
    await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})


def _fields(payload: dict) -> dict[str, str]:
    return {field["name"]: field["value"] for field in payload["embeds"][0]["fields"]}


async def _payloads_after_login(discord: FakeDiscord) -> list[dict]:
    """로그인 알림을 뺀 Discord 요청."""
    await asyncio.sleep(0.05)
    return [p for p in discord.payloads if p["embeds"][0]["title"] != "로그인"]


async def test_accepted_cancel_is_sent_to_discord():
    discord = FakeDiscord()
    async with _client(_kiwoom(), discord) as client:
        await _login(client)
        response = await client.post(URL, json=cancel())
        await discord.wait_for(2)

    assert response.status_code == 200
    [payload] = await _payloads_after_login(discord)
    assert payload["embeds"][0]["title"] == "주문 취소 접수"
    fields = _fields(payload)
    assert fields["투자 환경"] == "국내 모의"
    assert fields["종목"] == "SK하이닉스 (000660)"
    assert fields["원래 주문"] == "매도"
    assert fields["취소 수량"] == "3주"
    assert fields["주문번호"] == "0000070 → 0000141"
    assert fields["시각"].endswith("KST")


async def test_unconfirmed_cancel_quantity_is_shown_as_all_remaining():
    discord = FakeDiscord()
    kiwoom = _kiwoom().reply("kt10003", {**CANCEL_REPLY, "cncl_qty": "000000000000"})
    async with _client(kiwoom, discord) as client:
        await _login(client)
        await client.post(URL, json=cancel())
        await discord.wait_for(2)

    [payload] = await _payloads_after_login(discord)
    assert _fields(payload)["취소 수량"] == "남은 수량 전부"


def _broken(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    ("url", "kiwoom"),
    [
        (URL, _kiwoom().reply("kt10003", kiwoom_error(1, "[2000:취소 가능 수량이 없습니다]"))),
        (URL, _kiwoom().respond("kt10003", _broken)),
        (URL, FakeKiwoom().reply("ka10075", kiwoom_error(1, "조회 실패"))),
        (URL, _kiwoom().reply("ka10075", {"oso": []})),
        ("/api/environments/domestic_real/cancellations", _kiwoom()),
    ],
    ids=["rejected", "unknown", "recheck_failed", "not_found", "real"],
)
async def test_cancels_that_were_not_accepted_send_no_discord_message(url, kiwoom):
    discord = FakeDiscord()
    async with _client(kiwoom, discord) as client:
        await _login(client)
        response = await client.post(url, json=cancel())

    assert response.status_code != 200
    assert await _payloads_after_login(discord) == []


async def test_duplicate_cancel_sends_one_discord_message():
    discord = FakeDiscord()
    async with _client(_kiwoom(), discord) as client:
        await _login(client)
        await client.post(URL, json=cancel())
        duplicate = await client.post(URL, json=cancel())
        await discord.wait_for(2)

    assert duplicate.status_code == 409
    assert len(await _payloads_after_login(discord)) == 1


async def test_failed_discord_does_not_fail_the_cancel():
    discord = FakeDiscord(status=500)
    async with _client(_kiwoom(), discord) as client:
        await _login(client)
        response = await client.post(URL, json=cancel())
        await discord.wait_for(2)

    assert response.status_code == 200


async def test_cancel_response_does_not_wait_for_discord():
    discord = HeldDiscord()
    async with _client(_kiwoom(), discord) as client:
        await _login(client)
        response = await asyncio.wait_for(client.post(URL, json=cancel()), timeout=2)
        await discord.wait_for(2)
        # 응답은 이미 왔고, Discord는 아직 붙잡혀 있다.
        assert response.status_code == 200
        assert not discord.release.is_set()
        discord.release.set()
