"""Discord 채널로 알림을 보낸다.

Bot token으로 REST API만 호출하고 Gateway에는 연결하지 않는다.
알림은 편의 기능이므로 보내지 못해도 로그만 남기고 호출한 쪽에 오류를 던지지 않는다.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from stock_bot.config import EnvironmentSpec
from stock_bot.logging_setup import log
from stock_bot.masking import secrets

logger = logging.getLogger("stock_bot.notification")

API_BASE = "https://discord.com/api/v10"
# Discord는 bot 요청에 "DiscordBot ($url, $versionNumber)" 형식의 User-Agent를 요구한다.
USER_AGENT = "DiscordBot (https://github.com/ovowod/stock-bot, 0.1.0)"
# 429에서 이보다 오래 기다리라고 하면 다시 보내지 않는다. 호출한 쪽을 오래 붙잡지 않기 위해서다.
MAX_RETRY_WAIT_SECONDS = 5.0
_LOG_BODY_LIMIT = 200

# Discord 메시지·embed 한도.
CONTENT_LIMIT = 2000
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_COUNT_LIMIT = 25
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024


@dataclass(frozen=True)
class EmbedField:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True)
class Embed:
    title: str = ""
    description: str = ""
    color: int | None = None
    fields: tuple[EmbedField, ...] = ()


class DiscordNotifier:
    def __init__(
        self,
        environ: Mapping[str, str],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        token = environ.get("DISCORD_BOT_TOKEN", "").strip()
        channel_id = environ.get("DISCORD_CHANNEL_ID", "").strip()
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)
        self._url = f"{API_BASE}/channels/{channel_id}/messages"
        self._headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
        secrets.add(token)

        missing = [
            name
            for name, value in (("DISCORD_BOT_TOKEN", token), ("DISCORD_CHANNEL_ID", channel_id))
            if not value
        ]
        if missing:
            self.enabled = False
            log(logger, logging.INFO, "discord_disabled", missing=missing)
        elif not channel_id.isdigit():
            self.enabled = False
            log(logger, logging.WARNING, "discord_disabled", cause="channel_id_not_numeric")
        else:
            self.enabled = True
            log(logger, logging.INFO, "discord_enabled", channel_id=channel_id)

    async def send(
        self,
        text: str = "",
        *,
        embed: Embed | None = None,
        spec: EnvironmentSpec | None = None,
    ) -> bool:
        """알림을 보낸다. 보냈으면 True, 꺼져 있거나 실패했으면 False다. 오류를 던지지 않는다."""
        environment = spec.environment.value if spec else "-"
        if not self.enabled:
            return False
        content = f"[{spec.label}] {text}".rstrip() if spec else text
        payload: dict[str, Any] = {
            "content": _cut(content, CONTENT_LIMIT, "content"),
            "allowed_mentions": {"parse": []},
        }
        if embed is not None:
            payload["embeds"] = [_embed_payload(embed)]
        if not payload["content"] and "embeds" not in payload:
            log(logger, logging.WARNING, "discord_skipped_empty", environment=environment)
            return False

        for attempt in (1, 2):
            retry_after = await self._post(payload, environment, attempt)
            if retry_after is None:
                return True
            if attempt == 2 or retry_after < 0 or retry_after > MAX_RETRY_WAIT_SECONDS:
                return False
            await asyncio.sleep(retry_after)
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, payload: dict[str, Any], environment: str, attempt: int) -> float | None:
        """성공이면 None, 429면 기다릴 초, 그 밖의 실패면 -1을 돌려준다."""
        log(
            logger,
            logging.INFO,
            "discord_request",
            environment=environment,
            target=self._url,
            attempt=attempt,
        )
        started = time.perf_counter()
        try:
            response = await self._http.post(self._url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            log(
                logger,
                logging.ERROR,
                "discord_connection_failed",
                environment=environment,
                target=self._url,
                attempt=attempt,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            return -1
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        if response.is_success:
            log(
                logger,
                logging.INFO,
                "discord_response",
                environment=environment,
                target=self._url,
                attempt=attempt,
                http_status=response.status_code,
                elapsed_ms=elapsed_ms,
            )
            return None
        if response.status_code == 429:
            retry_after = _retry_after(response)
            log(
                logger,
                logging.WARNING,
                "discord_rate_limited",
                environment=environment,
                target=self._url,
                attempt=attempt,
                retry_after=retry_after,
                scope=response.headers.get("X-RateLimit-Scope"),
                elapsed_ms=elapsed_ms,
            )
            return retry_after
        log(
            logger,
            logging.ERROR,
            "discord_response_error",
            environment=environment,
            target=self._url,
            attempt=attempt,
            http_status=response.status_code,
            body_head=response.text[:_LOG_BODY_LIMIT],
            elapsed_ms=elapsed_ms,
        )
        return -1


def _embed_payload(embed: Embed) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if embed.title:
        data["title"] = _cut(embed.title, TITLE_LIMIT, "embed.title")
    if embed.description:
        data["description"] = _cut(embed.description, DESCRIPTION_LIMIT, "embed.description")
    if embed.color is not None:
        data["color"] = embed.color
    if len(embed.fields) > FIELD_COUNT_LIMIT:
        log(
            logger,
            logging.WARNING,
            "discord_truncated",
            part="embed.fields",
            original_length=len(embed.fields),
            limit=FIELD_COUNT_LIMIT,
        )
    if embed.fields:
        data["fields"] = [
            {
                "name": _cut(f.name, FIELD_NAME_LIMIT, "embed.field.name"),
                "value": _cut(f.value, FIELD_VALUE_LIMIT, "embed.field.value"),
                "inline": f.inline,
            }
            for f in embed.fields[:FIELD_COUNT_LIMIT]
        ]
    return data


def _cut(text: str, limit: int, part: str) -> str:
    """한도를 넘으면 끝을 "…"로 바꿔 한도에 맞춘다."""
    if len(text) <= limit:
        return text
    log(
        logger,
        logging.WARNING,
        "discord_truncated",
        part=part,
        original_length=len(text),
        limit=limit,
    )
    return text[: limit - 1] + "…"


def _retry_after(response: httpx.Response) -> float:
    try:
        value = response.json().get("retry_after")
        return float(value)
    except ValueError, TypeError, AttributeError:
        return -1
