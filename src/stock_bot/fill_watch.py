"""키움 실시간 주문체결을 지켜보다가 체결 알림을 Discord로 보낸다.

인증정보마다 실시간 연결을 하나 열고, LOGIN 뒤 주문체결 실시간 항목을 등록한다.
체결 감시는 조회만 하고 주문 TR은 부르지 않는다.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from websockets.asyncio.client import connect as websocket_connect

from stock_bot.config import ENVIRONMENTS, Environment, EnvironmentSpec
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import environment_var, log, request_id_var
from stock_bot.notification import DiscordNotifier, Embed, EmbedField

logger = logging.getLogger("stock_bot.fill_watch")


class RealtimeConnection(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


RealtimeConnect = Callable[[str], Awaitable[RealtimeConnection]]

# LOGIN·REG 응답을 기다리는 시간. 넘으면 연결을 닫는다.
RESPONSE_TIMEOUT_SECONDS = 10.0

# 지켜볼 투자 환경과 실시간 항목. 00은 국내 주문체결이다.
WATCHED = ((ENVIRONMENTS[Environment.DOMESTIC_PAPER], "00"),)


async def connect_websocket(url: str) -> RealtimeConnection:
    return await websocket_connect(url)


class FillWatcher:
    def __init__(
        self, kiwoom: KiwoomClient, notifier: DiscordNotifier, connect: RealtimeConnect
    ) -> None:
        self._kiwoom = kiwoom
        self._notifier = notifier
        self._connect = connect
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        """알림이 꺼져 있으면 지켜보지 않는다. 인증정보가 없는 투자 환경은 건너뛴다."""
        if not self._notifier.enabled:
            log(logger, logging.INFO, "fill_watch_disabled", cause="discord_disabled")
            return
        for spec, realtime_type in WATCHED:
            try:
                self._kiwoom.credentials(spec)
            except AppError as exc:
                log(
                    logger,
                    logging.INFO,
                    "fill_watch_skipped",
                    environment=spec.environment.value,
                    cause=exc.message,
                )
                continue
            log(
                logger,
                logging.INFO,
                "fill_watch_started",
                environment=spec.environment.value,
                target=spec.realtime_url,
                type=realtime_type,
            )
            self._tasks.append(asyncio.create_task(self._watch(spec, realtime_type)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _watch(self, spec: EnvironmentSpec, realtime_type: str) -> None:
        # 작업마다 context가 따로라 여기서 정하면 이 감시의 모든 로그에 투자 환경이 남는다.
        environment_var.set(spec.environment.value)
        # 받는 쪽은 큐에 넣기만 하고 작업자가 순서대로 처리한다.
        # 알림 처리가 늦어도 PING 응답이 막히지 않고, 같은 주문의 체결 순서가 지켜진다.
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        worker = asyncio.create_task(self._work(spec, queue))
        try:
            await self._session(spec, realtime_type, queue)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _session(
        self, spec: EnvironmentSpec, realtime_type: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        target = spec.realtime_url
        try:
            token = await self._kiwoom.access_token(spec)
            connection = await self._connect(target)
        except Exception as exc:
            log(
                logger,
                logging.ERROR,
                "realtime_connect_failed",
                target=target,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            return
        log(logger, logging.INFO, "realtime_connected", target=target)
        try:
            await _send(connection, {"trnm": "LOGIN", "token": token})
            await _expect(connection, "LOGIN")
            log(logger, logging.INFO, "realtime_logged_in", target=target)
            await _send(
                connection,
                {
                    "trnm": "REG",
                    "grp_no": "1",
                    "refresh": "1",
                    "data": [{"item": [""], "type": [realtime_type]}],
                },
            )
            await _expect(connection, "REG")
            log(logger, logging.INFO, "realtime_registered", target=target, type=realtime_type)
            while True:
                message = await _receive(connection)
                if message.get("trnm") == "REAL":
                    queue.put_nowait(message)
        except _RealtimeFailure as failure:
            log(logger, logging.ERROR, failure.event, target=target, **failure.fields)
        except Exception as exc:
            log(
                logger,
                logging.WARNING,
                "realtime_disconnected",
                target=target,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
        finally:
            await connection.close()

    async def _work(self, spec: EnvironmentSpec, queue: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            message = await queue.get()
            data = message.get("data")
            for item in data if isinstance(data, list) else []:
                try:
                    await self._handle(spec, item)
                except Exception as exc:
                    # 한 체결의 처리 오류로 작업자가 멈추면 그 뒤 체결 알림이 모두 끊긴다.
                    logger.error(
                        "fill_handle_failed",
                        exc_info=exc,
                        extra={"fields": {"error_type": type(exc).__name__}},
                    )

    async def _handle(self, spec: EnvironmentSpec, item: Any) -> None:
        # 체결 하나의 수신·처리·알림 로그를 같은 ID로 묶는다.
        request_id_var.set(uuid.uuid4().hex[:12])
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, dict) or item.get("type") != "00":
            log(logger, logging.DEBUG, "realtime_item_skipped")
            return
        status = values.get("913")
        if status != "체결":
            log(logger, logging.DEBUG, "fill_skipped", status=status)
            return
        fill = _domestic_fill(values)
        log(
            logger,
            logging.INFO,
            "fill_received",
            order_no=fill.order_no,
            code=fill.code,
            side=fill.side,
            price=_plain(fill.price),
            quantity=_plain(fill.quantity),
            remaining=_plain(fill.remaining),
        )
        sent = await self._notifier.send(embed=_fill_embed(fill), spec=spec)
        log(logger, logging.INFO, "fill_notified", order_no=fill.order_no, sent=sent)


async def _send(connection: RealtimeConnection, message: dict[str, Any]) -> None:
    await connection.send(json.dumps(message, ensure_ascii=False))


class _RealtimeFailure(Exception):
    def __init__(self, event: str, **fields: Any) -> None:
        super().__init__(event)
        self.event = event
        self.fields = fields


async def _expect(connection: RealtimeConnection, trnm: str) -> dict[str, Any]:
    """trnm 응답을 기다린다. 제한 시간을 넘거나 return_code가 0이 아니면 실패다."""
    try:
        async with asyncio.timeout(RESPONSE_TIMEOUT_SECONDS):
            message = await _receive(connection)
            while message.get("trnm") != trnm:
                message = await _receive(connection)
    except TimeoutError:
        raise _RealtimeFailure(
            "realtime_reply_timeout", trnm=trnm, timeout_seconds=RESPONSE_TIMEOUT_SECONDS
        ) from None
    if str(message.get("return_code")) != "0":
        raise _RealtimeFailure(
            "realtime_login_failed" if trnm == "LOGIN" else "realtime_register_failed",
            return_code=message.get("return_code"),
            return_msg=message.get("return_msg"),
        )
    return message


async def _receive(connection: RealtimeConnection) -> dict[str, Any]:
    """PING이 아닌 다음 메시지를 받는다. PING은 받은 그대로 돌려보낸다."""
    while True:
        raw = await connection.recv()
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            message = json.loads(text)
        except ValueError:
            message = None
        if not isinstance(message, dict):
            log(logger, logging.WARNING, "realtime_message_malformed", body_head=text[:200])
            continue
        if message.get("trnm") == "PING":
            await connection.send(text)
            log(logger, logging.DEBUG, "realtime_ping")
            continue
        return message


BUY_COLOR = 0xE42939
NEUTRAL_COLOR = 0x8B8F98
UNKNOWN = "확인 불가"


@dataclass(frozen=True)
class Fill:
    """체결 이벤트 하나. 읽지 못한 값은 None이다."""

    code: str | None
    name: str | None
    order_no: str | None
    side: str | None  # "buy" | "sell"
    price: Decimal | None
    quantity: Decimal | None
    ordered: Decimal | None
    remaining: Decimal | None
    time: str | None


def _domestic_fill(values: dict[str, Any]) -> Fill:
    return Fill(
        code=_text(values, "9001"),
        name=_text(values, "302"),
        order_no=_text(values, "9203"),
        side=_side(values),
        price=_number(values, "910"),
        quantity=_number(values, "911"),
        ordered=_number(values, "900"),
        remaining=_number(values, "902"),
        time=_time(values, "908"),
    )


def _text(values: dict[str, Any], key: str) -> str | None:
    value = values.get(key)
    text = "" if value is None else str(value).strip()
    if not text:
        log(logger, logging.WARNING, "fill_value_unreadable", key=key, raw=value)
        return None
    return text


def _number(values: dict[str, Any], key: str) -> Decimal | None:
    """키움은 색 표시용으로 가격 앞에 +/-를 붙인다. 부호를 떼고 읽는다."""
    raw = values.get(key)
    text = "" if raw is None else str(raw).strip().lstrip("+-")
    try:
        number = Decimal(text)
    except InvalidOperation:
        number = None
    if number is None or not number.is_finite():
        log(logger, logging.WARNING, "fill_value_unreadable", key=key, raw=str(raw)[:40])
        return None
    return number


def _side(values: dict[str, Any]) -> str | None:
    """매도수구분. 국내는 1·2, 미국은 01·02로 온다."""
    side = {"1": "sell", "2": "buy"}.get(str(values.get("907", "")).strip().lstrip("0"))
    if side is None:
        log(logger, logging.WARNING, "fill_value_unreadable", key="907", raw=values.get("907"))
    return side


def _time(values: dict[str, Any], key: str) -> str | None:
    text = _text(values, key)
    if text is None or len(text) != 6 or not text.isdigit():
        return None
    return f"{text[:2]}:{text[2:4]}:{text[4:]}"


def _fill_embed(fill: Fill) -> Embed:
    side = {"buy": "매수", "sell": "매도"}.get(fill.side or "")
    title = f"체결 · {side}" if side else "체결"
    color = BUY_COLOR if fill.side == "buy" else NEUTRAL_COLOR
    stock = f"{fill.name} ({fill.code})" if fill.name and fill.code else fill.name or fill.code
    amount = (
        _won(fill.price * fill.quantity)
        if fill.price is not None and fill.quantity is not None
        else UNKNOWN
    )
    return Embed(
        title=title,
        color=color,
        fields=(
            EmbedField("종목", stock or UNKNOWN),
            EmbedField("이번 체결", f"{_shares(fill.quantity)} @ {_won(fill.price)} ({amount})"),
            EmbedField("누적", _progress(fill)),
            EmbedField("체결 시각", fill.time or UNKNOWN),
        ),
    )


def _progress(fill: Fill) -> str:
    if fill.ordered is None or fill.remaining is None:
        return UNKNOWN
    filled = f"{fill.ordered - fill.remaining:,.0f} / {fill.ordered:,.0f}주"
    if fill.remaining == 0:
        return f"{filled} · 전량 체결"
    return f"{filled} · 남은 {fill.remaining:,.0f}주"


def _plain(value: Decimal | None) -> str | None:
    """로그용. Decimal을 그대로 넘기면 따옴표가 붙은 JSON 문자열로 남는다."""
    return None if value is None else str(value)


def _shares(value: Decimal | None) -> str:
    return UNKNOWN if value is None else f"{value:,.0f}주"


def _won(value: Decimal | None) -> str:
    return UNKNOWN if value is None else f"{value:,.0f}원"
