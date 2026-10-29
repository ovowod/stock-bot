"""가짜 Discord 서버와 알림 내용을 읽는 도우미."""

import asyncio
import json

import httpx

DISCORD_ENV = {"DISCORD_BOT_TOKEN": "discord-token-ZZZZ9999", "DISCORD_CHANNEL_ID": "123456789"}
RED = 0xE42939
BLUE = 0x2F6FEB
GRAY = 0x8B8F98


def fields(payload: dict) -> dict[str, str]:
    return {field["name"]: field["value"] for field in payload["embeds"][0]["fields"]}


class FakeDiscord:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(request.content))
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(200, json={"id": "1"})

    async def wait_for(self, count: int) -> None:
        for _ in range(200):
            if len(self.payloads) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Discord 요청 {count}개를 기다렸지만 {len(self.payloads)}개")


class SlowDiscord(FakeDiscord):
    """첫 요청의 응답을 gate가 열릴 때까지 늦춘다."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:  # type: ignore[override]
        if not self.payloads:
            self.payloads.append(json.loads(request.content))
            await self.gate.wait()
            return httpx.Response(200, json={"id": "1"})
        return super().__call__(request)
