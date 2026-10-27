"""공유 실시간 연결: 키움 실시간 연결을 인증정보마다 하나 열고 여러 기능의 구독을 함께 싣는다.

같은 앱 키로 연결을 하나 더 열면 키움이 앞 연결을 끊으므로(ADR 0001), 기능은 연결을 직접 열지 않고
채널을 받아 구독을 등록·변경·해지한다. 연결, LOGIN, PING 응답, 재연결, 밀려남은 이 모듈이 맡는다.
"""

import asyncio
import contextvars
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from websockets.asyncio.client import connect as websocket_connect

from stock_bot.config import ENVIRONMENTS, EnvironmentSpec
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import environment_var, log
from stock_bot.notification import DiscordNotifier, Embed

logger = logging.getLogger("stock_bot.realtime")


class RealtimeConnection(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


RealtimeConnect = Callable[[str], Awaitable[RealtimeConnection]]

# LOGIN·REG·REMOVE 응답을 기다리는 시간. 넘으면 연결을 닫고 다시 연결한다.
# 응답에 어느 요청의 것인지 알 단서가 없어, 늦은 응답을 다음 요청의 응답으로 착각하지 않게 한다.
RESPONSE_TIMEOUT_SECONDS = 10.0
# 다시 연결하기 전 기다리는 시간. 실패가 이어지면 두 배씩 늘리고,
# LOGIN에 성공하면 처음으로 되돌린다.
RECONNECT_INITIAL_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 60.0

# 같은 앱 키로 다른 연결이 들어왔을 때 키움이 기존 연결에 보내는 SYSTEM 코드(모의 서버에서 확인).
SESSION_REPLACED_CODE = "R10001"

NEUTRAL_COLOR = 0x8B8F98


async def connect_websocket(url: str) -> RealtimeConnection:
    return await websocket_connect(url)


@dataclass(frozen=True)
class Subscription:
    """REG에 싣는 구독 하나. refresh는 키움 값 그대로다(0: 그룹을 비우고 등록, 1: 그룹에 더함)."""

    grp_no: str
    items: list[Any]
    types: list[str]
    refresh: str = "1"


class EventKind(StrEnum):
    CONNECTED = "connected"  # LOGIN에 성공했다. 살아 있는 구독을 이어서 다시 등록한다.
    DISCONNECTED = "disconnected"
    REGISTERED = "registered"
    REJECTED = "rejected"  # 키움이 이 채널의 REG를 거부했다. 연결은 그대로다.
    REAL = "real"
    STOPPED = "stopped"  # 밀려나서 이 연결을 다시 열지 않는다.


@dataclass(frozen=True)
class RealtimeEvent:
    kind: EventKind
    # 이 인증정보의 연결 번호. LOGIN에 성공할 때마다 1씩 는다. 재연결 전후의 이벤트를 가른다.
    connection: int
    # 서버가 받은(또는 상태가 바뀐) 시각. 앱에 넣은 현재 시각 함수로 구한다.
    received_at: datetime
    # REAL의 data 가운데 이 채널이 구독한 실시간 항목만.
    items: list[dict[str, Any]] = field(default_factory=list)
    # REJECTED의 return_code·return_msg.
    detail: dict[str, Any] = field(default_factory=dict)
    # REGISTERED·REJECTED가 어느 그룹의 등록 결과인지.
    group: str | None = None
    # 받은 때의 경과 시간 시계(time.monotonic). 큐에서 기다린 시간까지 재는 데 쓴다.
    received_clock: float = field(default_factory=time.monotonic)


EventHandler = Callable[[RealtimeEvent], Awaitable[None]]
# 대기열 없이 받는 채널의 handler. 받는 자리에서 바로 부르므로 기다리지 않고 빨리 끝나야 한다.
ImmediateHandler = Callable[[RealtimeEvent], None]


class Channel:
    """한 기능이 한 연결에 내는 구독. 이벤트는 채널마다 따로 큐에 쌓아 순서대로 handler에 넘긴다.

    채널마다 큐가 따로라서 한 기능의 처리가 늦어도 다른 기능의 이벤트는 늦어지지 않는다.
    immediate handler로 연 채널은 큐 없이 받는 자리에서 바로 넘긴다. 그래서 같은 메시지의
    다른 항목이 다른 채널의 큐에서 처리되기 전에 끝난다.
    """

    def __init__(
        self,
        link: _Link,
        feature: str,
        label: str,
        handler: EventHandler | None = None,
        immediate: ImmediateHandler | None = None,
    ) -> None:
        self.feature = feature
        # 밀려남 중단 알림에 쓰는 기능 이름. 여러 기능이 함께 쓰는 채널은 이름이 여럿이다.
        self.labels = [label] if label else []
        self.subscription: Subscription | None = None
        # 구독을 바꿀 때마다 는다. 연결은 보낸 버전과 비교해 다시 보낼지 정한다.
        self.version = 0
        self._link = link
        self._immediate = immediate
        self._queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._worker = asyncio.create_task(self._work(handler)) if handler is not None else None

    def subscribe(self, subscription: Subscription | None) -> None:
        """구독을 바꾼다. None이면 해지한다. 같은 구독을 다시 넣으면 REG를 다시 보낸다."""
        self.subscription = subscription
        self.version += 1
        self._link.changed()

    @property
    def stopped(self) -> bool:
        """연결이 밀려나거나 서버가 꺼져 멈췄다.

        STOPPED 이벤트가 큐에서 처리되기 전에도 바로 안다.
        """
        return self._link.stopped

    @property
    def connection(self) -> int | None:
        """지금 LOGIN까지 된 연결의 번호. 끊겨 있으면 None이다.

        큐에 밀린 이벤트가 이전 연결의 것인지 CONNECTED·DISCONNECTED를 처리하기 전에도 가른다.
        """
        return self._link.connection

    def put(self, event: RealtimeEvent) -> None:
        if self._immediate is None:
            self._queue.put_nowait(event)
            return
        try:
            # handler가 바꾼 요청 ID 같은 로그 context가 받는 쪽의 다음 로그에 남지 않게 한다.
            contextvars.copy_context().run(self._immediate, event)
        except Exception as exc:
            # 받는 자리에서 오류가 새면 연결이 끊긴다. 남기고 다음 이벤트를 받는다.
            self._failed(event, exc)

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)

    async def _work(self, handler: EventHandler) -> None:
        while True:
            event = await self._queue.get()
            try:
                await handler(event)
            except Exception as exc:
                # 이벤트 하나의 처리 오류로 작업자가 멈추면 그 뒤 이벤트를 모두 놓친다.
                self._failed(event, exc)

    def _failed(self, event: RealtimeEvent, exc: Exception) -> None:
        logger.error(
            "realtime_handler_failed",
            exc_info=exc,
            extra={
                "fields": {
                    "feature": self.feature,
                    "kind": event.kind.value,
                    "error_type": type(exc).__name__,
                }
            },
        )


class RealtimeHub:
    def __init__(
        self,
        kiwoom: KiwoomClient,
        notifier: DiscordNotifier,
        connect: RealtimeConnect,
        now: Callable[[], datetime],
    ) -> None:
        self._kiwoom = kiwoom
        self._notifier = notifier
        self._connect = connect
        self.now = now
        # 인증정보(credential_prefix)마다 연결 하나.
        self._links: dict[str, _Link] = {}

    def channel(
        self, spec: EnvironmentSpec, feature: str, label: str, handler: EventHandler
    ) -> Channel:
        """spec의 인증정보 연결에 채널을 연다. 연결은 구독이 생길 때 연다."""
        link = self._link(spec)
        return link.add(Channel(link, feature, label, handler))

    def immediate_channel(
        self, spec: EnvironmentSpec, feature: str, label: str, handler: ImmediateHandler
    ) -> Channel:
        """큐 없이 받는 자리에서 바로 handler를 부르는 채널을 연다."""
        link = self._link(spec)
        return link.add(Channel(link, feature, label, immediate=handler))

    def _link(self, spec: EnvironmentSpec) -> _Link:
        link = self._links.get(spec.credential_prefix)
        if link is None:
            link = self._links[spec.credential_prefix] = _Link(self, spec)
        return link

    async def stop(self) -> None:
        for link in self._links.values():
            await link.stop()
        self._links.clear()

    async def _notify_replaced(self, link: _Link, features: list[str]) -> None:
        # 그 연결로 동작하던 기능을 모두 적는다. 체결 감시만 있으면 "체결 감시 중단"이다.
        stopped = "·".join(features) or "실시간 감시"
        await self._notifier.send(
            f"[{link.labels}]",
            embed=Embed(
                title=f"{stopped} 중단",
                description=(
                    f"다른 곳에서 같은 앱 키로 접속해 이 서버의 {stopped}를 멈춥니다. "
                    "다시 지켜보려면 이 서버를 재시작하세요."
                ),
                color=NEUTRAL_COLOR,
            ),
        )


class _Link:
    """인증정보 하나의 실시간 연결. 끊기면 다시 연결하고, 밀려나면 멈춘다."""

    def __init__(self, hub: RealtimeHub, spec: EnvironmentSpec) -> None:
        self._hub = hub
        self._spec = spec
        # 같은 인증정보를 쓰는 투자 환경들. 실전은 국내·미국이 연결 하나를 함께 쓴다.
        specs = [s for s in ENVIRONMENTS.values() if s.credential_prefix == spec.credential_prefix]
        self.environments = ",".join(s.environment.value for s in specs)
        self.labels = "·".join(s.label for s in specs)
        self.channels: list[Channel] = []
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._connection_no = 0
        # 지금 LOGIN까지 된 연결의 번호. 끊겨 있으면 None이다.
        self.connection: int | None = None
        self.stopped = False
        # 마지막 구독이 해지돼 연결을 닫았다. 그사이 새 구독이 생겼으면 기다리지 않고 다시 연다.
        self._closed_idle = False

    def add(self, channel: Channel) -> Channel:
        self.channels.append(channel)
        return channel

    def changed(self) -> None:
        """구독이 바뀌었다. 구독이 하나라도 생기면 그때 연결을 연다."""
        self._changed.set()
        if self._task is None and not self.stopped and not self._idle():
            self._task = asyncio.create_task(self._run())

    def _idle(self) -> bool:
        """구독이 하나도 없다. 같은 앱 키의 다른 서버를 끊지 않게 연결해 두지 않는다."""
        return all(c.subscription is None for c in self.channels)

    def _features(self) -> list[Channel]:
        """지금 이 연결로 무언가를 받고 있는 기능."""
        return [c for c in self.channels if c.subscription is not None]

    async def stop(self) -> None:
        self.stopped = True
        tasks = [self._task] if self._task else []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for channel in self.channels:
            await channel.close()

    def _broadcast(self, kind: EventKind, connection: int) -> None:
        event = RealtimeEvent(kind, connection, self._hub.now())
        for channel in self.channels:
            channel.put(event)

    async def _run(self) -> None:
        # 작업마다 context가 따로라 여기서 정하면 이 연결의 모든 로그에 투자 환경이 남는다.
        environment_var.set(self.environments)
        try:
            # 끊긴 동안의 이벤트는 보충하지 않는다. 다시 연결해 등록만 한다.
            delay = RECONNECT_INITIAL_SECONDS
            while not self._idle():
                if await self._session():
                    delay = RECONNECT_INITIAL_SECONDS
                if self._closed_idle:
                    # 실패로 끊긴 것이 아니므로 재연결 대기 없이 루프 조건만 다시 본다.
                    self._closed_idle = False
                    continue
                if self._idle():
                    # 끊긴 동안 구독이 모두 해지됐다. 다시 연결하지 않는다.
                    break
                log(logger, logging.WARNING, "realtime_reconnect_wait", wait_seconds=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_SECONDS)
            # 확인과 비우기 사이에 await가 없어, 그 뒤에 생긴 구독은 changed()가 새로 연결한다.
            self._task = None
            log(logger, logging.INFO, "realtime_idle_closed")
        except _SessionReplaced as replaced:
            # 같은 앱 키로 서버가 둘 떠 있으면 다시 연결할 때마다 서로를 끊어낸다.
            # 나중에 연결한 쪽이 지켜보도록 이 서버는 멈추고, 조용히 끊기지 않게 알린다.
            self.stopped = True
            stopped = self._features()
            # 한 기능이 채널 여럿을 쓰면 이름이 겹치므로 한 번만 적는다.
            names = list(dict.fromkeys(name for c in stopped for name in c.labels))
            log(
                logger,
                logging.ERROR,
                "realtime_session_replaced",
                code=replaced.code,
                message=replaced.message,
                features=[c.feature for c in stopped],
                names=names,
            )
            self._broadcast(EventKind.STOPPED, self._connection_no)
            await self._hub._notify_replaced(self, names)

    async def _session(self) -> bool:
        """연결 하나가 끝날 때까지 받는다. LOGIN에 성공하고 응답 시간 초과 없이 끝났으면 True다.

        REG 응답이 오지 않아 끝난 연결까지 성공으로 세면 재연결 간격이 늘지 않는다.
        """
        spec = self._spec
        target = spec.realtime_url
        try:
            token = await self._hub._kiwoom.access_token(spec)
            connection = await self._hub._connect(target)
        except Exception as exc:
            log(
                logger,
                logging.ERROR,
                "realtime_connect_failed",
                target=target,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            return False
        log(logger, logging.INFO, "realtime_connected", target=target)
        logged_in = healthy = False
        try:
            await _send(connection, {"trnm": "LOGIN", "token": token})
            await _expect_login(connection)
            log(logger, logging.INFO, "realtime_logged_in", target=target)
            logged_in = healthy = True
            self._connection_no += 1
            self.connection = self._connection_no
            self._broadcast(EventKind.CONNECTED, self._connection_no)
            await self._serve(connection, self._connection_no)
        except _SessionReplaced:
            raise
        except _RealtimeFailure as failure:
            healthy = False
            log(logger, logging.ERROR, failure.event, target=target, **failure.fields)
            if failure.fields.get("trnm") == "LOGIN":
                # LOGIN이 거부되거나 응답이 없으면 만료·무효 토큰일 수 있다.
                # 다음 연결은 새 토큰으로 한다.
                await self._hub._kiwoom.discard_access_token(spec, token)
                log(logger, logging.WARNING, "realtime_token_discarded", target=target)
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
            if logged_in:
                self.connection = None
                self._broadcast(EventKind.DISCONNECTED, self._connection_no)
            try:
                await connection.close()
            except Exception as exc:
                # 여기서 오류가 새면 재연결 반복이 끝나 실시간 기능이 조용히 멈춘다.
                log(
                    logger,
                    logging.WARNING,
                    "realtime_close_failed",
                    target=target,
                    error_type=type(exc).__name__,
                    cause=str(exc),
                )
        return healthy

    async def _serve(self, connection: RealtimeConnection, number: int) -> None:
        """받기와 구독 요청을 함께 돌린다. 어느 쪽이든 끝나면(오류) 연결을 끝낸다."""
        replies: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        reader = asyncio.create_task(self._read(connection, number, replies))
        control = asyncio.create_task(self._control(connection, number, replies))
        try:
            done, _ = await asyncio.wait({reader, control}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (reader, control):
                task.cancel()
            await asyncio.gather(reader, control, return_exceptions=True)
        next(iter(done)).result()

    async def _read(
        self,
        connection: RealtimeConnection,
        number: int,
        replies: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """받는 쪽은 나눠 담기만 한다. 처리가 늦어도 PING 응답이 막히지 않는다."""
        while True:
            message = await _receive(connection)
            if message.get("trnm") == "REAL":
                self._dispatch(message, number)
            else:
                replies.put_nowait(message)

    def _dispatch(self, message: dict[str, Any], number: int) -> None:
        received_at = self._hub.now()
        data = message.get("data")
        items = [
            item for item in (data if isinstance(data, list) else []) if isinstance(item, dict)
        ]
        routed: set[int] = set()
        for channel in self.channels:
            subscription = channel.subscription
            types = set(subscription.types if subscription else [])
            mine = [item for item in items if item.get("type") in types]
            routed.update(id(item) for item in mine)
            if mine:
                channel.put(RealtimeEvent(EventKind.REAL, number, received_at, mine))
        for item in items:
            if id(item) not in routed:
                log(logger, logging.DEBUG, "realtime_item_unrouted", type=item.get("type"))

    async def _control(
        self,
        connection: RealtimeConnection,
        number: int,
        replies: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """구독 변경을 하나씩 보낸다. 새 연결은 살아 있는 구독을 모두 다시 등록한다.

        키움의 REG/REMOVE 응답에는 어느 요청의 응답인지 알 단서가 없어,
        응답을 받은 뒤 다음을 보낸다.
        """
        sent: dict[Channel, int] = {}
        registered: dict[Channel, Subscription] = {}
        while True:
            self._changed.clear()
            for channel in list(self.channels):
                if sent.get(channel, 0) == channel.version:
                    continue
                sent[channel] = channel.version
                await self._apply(connection, number, replies, channel, registered)
            if not registered and self._idle():
                # 마지막 구독까지 해지했다. 연결을 닫는다.
                self._closed_idle = True
                return
            await self._changed.wait()

    async def _apply(
        self,
        connection: RealtimeConnection,
        number: int,
        replies: asyncio.Queue[dict[str, Any]],
        channel: Channel,
        registered: dict[Channel, Subscription],
    ) -> None:
        wanted = channel.subscription
        current = registered.get(channel)
        if current is not None and (wanted is None or wanted.grp_no != current.grp_no):
            reply = await self._request(connection, replies, "REMOVE", current, channel)
            # 해지가 거부돼도 그 그룹의 이벤트는 채널이 구독한 항목으로만 넘기므로 잊는다.
            del registered[channel]
            if _ok(reply):
                log(
                    logger,
                    logging.INFO,
                    "realtime_removed",
                    target=self._spec.realtime_url,
                    **_fields(channel, current),
                )
            else:
                log(
                    logger,
                    logging.WARNING,
                    "realtime_remove_failed",
                    target=self._spec.realtime_url,
                    **_fields(channel, current),
                    **_result(reply),
                )
        if wanted is None:
            return
        reply = await self._request(connection, replies, "REG", wanted, channel)
        if _ok(reply):
            registered[channel] = wanted
            log(
                logger,
                logging.INFO,
                "realtime_registered",
                target=self._spec.realtime_url,
                **_fields(channel, wanted),
            )
            channel.put(
                RealtimeEvent(EventKind.REGISTERED, number, self._hub.now(), group=wanted.grp_no)
            )
            return
        log(
            logger,
            logging.ERROR,
            "realtime_register_failed",
            target=self._spec.realtime_url,
            **_fields(channel, wanted),
            **_result(reply),
        )
        channel.put(
            RealtimeEvent(
                EventKind.REJECTED,
                number,
                self._hub.now(),
                detail=_result(reply),
                group=wanted.grp_no,
            )
        )

    async def _request(
        self,
        connection: RealtimeConnection,
        replies: asyncio.Queue[dict[str, Any]],
        trnm: str,
        subscription: Subscription,
        channel: Channel,
    ) -> dict[str, Any]:
        await _send(
            connection,
            {
                "trnm": trnm,
                "grp_no": subscription.grp_no,
                "refresh": subscription.refresh,
                "data": [{"item": subscription.items, "type": subscription.types}],
            },
        )
        try:
            async with asyncio.timeout(RESPONSE_TIMEOUT_SECONDS):
                reply = await replies.get()
                while reply.get("trnm") != trnm:
                    log(
                        logger, logging.WARNING, "realtime_reply_unexpected", trnm=reply.get("trnm")
                    )
                    reply = await replies.get()
        except TimeoutError:
            raise _RealtimeFailure(
                "realtime_reply_timeout",
                trnm=trnm,
                feature=channel.feature,
                timeout_seconds=RESPONSE_TIMEOUT_SECONDS,
            ) from None
        return reply


def _ok(reply: dict[str, Any]) -> bool:
    return str(reply.get("return_code")) == "0"


def _result(reply: dict[str, Any]) -> dict[str, Any]:
    return {"return_code": reply.get("return_code"), "return_msg": reply.get("return_msg")}


def _fields(channel: Channel, subscription: Subscription) -> dict[str, Any]:
    return {
        "feature": channel.feature,
        "grp_no": subscription.grp_no,
        "type": subscription.types,
        "item_count": len(subscription.items),
    }


async def _send(connection: RealtimeConnection, message: dict[str, Any]) -> None:
    await connection.send(json.dumps(message, ensure_ascii=False))


class _SessionReplaced(Exception):
    """같은 앱 키로 다른 연결이 들어와 키움이 이 연결을 끊었다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class _RealtimeFailure(Exception):
    def __init__(self, event: str, **fields: Any) -> None:
        super().__init__(event)
        self.event = event
        self.fields = fields


async def _expect_login(connection: RealtimeConnection) -> None:
    """LOGIN 응답을 기다린다. 제한 시간을 넘거나 return_code가 0이 아니면 실패다."""
    try:
        async with asyncio.timeout(RESPONSE_TIMEOUT_SECONDS):
            message = await _receive(connection)
            while message.get("trnm") != "LOGIN":
                message = await _receive(connection)
    except TimeoutError:
        raise _RealtimeFailure(
            "realtime_reply_timeout", trnm="LOGIN", timeout_seconds=RESPONSE_TIMEOUT_SECONDS
        ) from None
    if not _ok(message):
        raise _RealtimeFailure("realtime_login_failed", trnm="LOGIN", **_result(message))


async def _receive(connection: RealtimeConnection) -> dict[str, Any]:
    """PING이 아닌 다음 메시지를 받는다. PING은 받은 그대로 돌려보낸다."""
    while True:
        raw = await connection.recv()
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
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
        if message.get("trnm") == "SYSTEM":
            code, notice = str(message.get("code", "")), str(message.get("message", ""))
            if code == SESSION_REPLACED_CODE:
                raise _SessionReplaced(code, notice)
            log(logger, logging.WARNING, "realtime_system_message", code=code, message=notice)
            continue
        return message
