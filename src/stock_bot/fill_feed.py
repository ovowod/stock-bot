"""체결 feed: 내 계좌의 체결을 실시간으로 받아 체결 하나로 읽고, 신청한 기능에 넘긴다.

키움은 내 계좌의 주문 이벤트를 국내는 00(주문체결), 미국은 F5(실시간 체결)로 보낸다.
체결 알림과 SL / TP는 투자 환경마다 신청만 하고, 등록·거부 시 다시 시도·체결 거르기·읽기는
이 module이 맡는다. 인증정보마다 공유 실시간 연결(realtime)에 채널을 하나 연다.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from stock_bot.config import ENVIRONMENTS, Environment, EnvironmentSpec, Market
from stock_bot.logging_setup import environment_var, log, request_id_var
from stock_bot.realtime import EventKind, RealtimeEvent, RealtimeHub

logger = logging.getLogger("stock_bot.fill_feed")

# 등록이 거부되면 연결은 그대로 두고 이 간격으로 다시 등록한다.
# 실패가 이어지면 두 배씩 늘리고, 등록에 성공하면 처음으로 되돌린다.
REGISTER_RETRY_INITIAL_SECONDS = 1.0
REGISTER_RETRY_MAX_SECONDS = 60.0

# 투자 환경별 내 계좌 체결 실시간 항목. 00은 국내 주문체결, F5는 미국 실시간 체결이다.
# 국내 실전과 미국 실전은 인증정보와 계좌가 같아 연결 하나, 등록 하나로 받는다.
FILL_TYPES = {
    Environment.DOMESTIC_PAPER: "00",
    Environment.US_PAPER: "F5",
    Environment.DOMESTIC_REAL: "00",
    Environment.US_REAL: "F5",
}
# 실시간 항목별로 체결을 뜻하는 주문상태(913). 접수·확인·취소·거부 등은 넘기지 않는다.
# F5의 체결완료는 2026-10-08 모의 서버에서 확인했고, 부분체결은 키움 가이드 설명을 따른다.
FILL_STATUSES = {"00": {"체결"}, "F5": {"부분체결", "체결완료"}}


class FeedState(StrEnum):
    LIVE = "live"  # 등록되어 체결을 받는 중이다.
    REJECTED = "rejected"  # 키움이 등록을 거부했다. 체결 feed가 다시 시도한다.


@dataclass(frozen=True)
class FeedStatus:
    state: FeedState
    # REJECTED의 return_code·return_msg.
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fill:
    """체결 이벤트 하나. 읽지 못한 값은 None이다."""

    spec: EnvironmentSpec
    code: str | None
    name: str | None
    order_no: str | None
    side: str | None  # "buy" | "sell"
    price: Decimal | None
    quantity: Decimal | None
    ordered: Decimal | None
    remaining: Decimal | None
    time: str | None
    # 받은 연결 번호. 재연결 전 연결의 체결을 가른다.
    connection: int
    # 받은 때의 경과 시간 시계(time.monotonic).
    received_clock: float
    # 체결 하나의 수신·처리·알림 로그를 묶는 ID. 체결마다 새로 붙인다.
    request_id: str
    # 미국 매도 체결분의 매입 금액(F5 50724). 국내는 None이다.
    realized_cost: Decimal | None = None
    # 국내만: 이 주문의 지금까지 체결 수량(911)과 평균 체결가(910). 미국은 None이다.
    filled_quantity: Decimal | None = None
    average_price: Decimal | None = None

    @property
    def market(self) -> Market:
        return self.spec.market


# 체결이나 등록 상태를 받는다. 받는 자리에서 바로 부르므로 기다리지 않고 빨리 끝나야 한다.
FillHandler = Callable[[Fill | FeedStatus], None]


class FillFeed:
    def __init__(self, hub: RealtimeHub) -> None:
        self._hub = hub
        # 인증정보(credential_prefix)마다 등록 하나.
        self._lines: dict[str, _Line] = {}

    def subscribe(
        self, spec: EnvironmentSpec, label: str, handler: FillHandler
    ) -> Callable[[], None]:
        """spec 투자 환경의 체결을 신청한다. 돌려준 함수를 부르면 해지한다.

        label은 밀려남 중단 알림에 쓰는 기능 이름이다.
        """
        line = self._lines.get(spec.credential_prefix)
        if line is None:
            line = self._lines[spec.credential_prefix] = _Line(self._hub, spec)
        return line.subscribe(_Subscriber(spec, label, handler))

    async def stop(self) -> None:
        for line in self._lines.values():
            await line.stop()


@dataclass(eq=False)
class _Subscriber:
    spec: EnvironmentSpec
    label: str
    handler: FillHandler


class _Line:
    """인증정보 하나의 체결 등록. 실전은 국내·미국 신청을 한 등록에 싣는다."""

    def __init__(self, hub: RealtimeHub, spec: EnvironmentSpec) -> None:
        self._spec = spec
        # 이 인증정보로 받는 실시간 항목 -> 그 항목의 투자 환경.
        self._environments = {
            FILL_TYPES[s.environment]: s
            for s in ENVIRONMENTS.values()
            if s.credential_prefix == spec.credential_prefix
        }
        self._names = ",".join(s.environment.value for s in self._environments.values())
        self._subscribers: list[_Subscriber] = []
        # 지금 등록을 요청한 항목. 신청들의 항목을 합친 것이다.
        self._types: list[str] = []
        # 마지막 등록 상태. None이면 모른다(등록 결과 전, 끊김, 해지 뒤).
        self._status: FeedStatus | None = None
        self._delay = REGISTER_RETRY_INITIAL_SECONDS
        # 거부된 등록을 다시 보내려고 기다리는 작업. 많아야 하나다.
        self._retry: asyncio.Task[None] | None = None
        self._channel = hub.channel(spec, "fill_feed", "", self._on_event)

    def subscribe(self, subscriber: _Subscriber) -> Callable[[], None]:
        self._subscribers.append(subscriber)
        log(
            logger,
            logging.INFO,
            "fill_feed_subscribed",
            environment=subscriber.spec.environment.value,
            feature=subscriber.label,
        )
        if not self._register() and self._status is not None:
            # 등록 항목이 그대로면 다시 등록하지 않고 기억한 상태를 바로 알린다.
            self._deliver(subscriber, self._status)

        def unsubscribe() -> None:
            if subscriber not in self._subscribers:
                return
            self._subscribers.remove(subscriber)
            log(
                logger,
                logging.INFO,
                "fill_feed_unsubscribed",
                environment=subscriber.spec.environment.value,
                feature=subscriber.label,
            )
            self._register()

        return unsubscribe

    async def stop(self) -> None:
        self._cancel_retry()

    def _register(self) -> bool:
        """신청들의 항목을 합쳐 다시 등록하거나 해지한다. 합친 항목이 바뀌었으면 True다."""
        self._channel.label = "·".join(dict.fromkeys(s.label for s in self._subscribers))
        types = list(dict.fromkeys(FILL_TYPES[s.spec.environment] for s in self._subscribers))
        if types == self._types:
            return False
        self._types = types
        # 새 항목의 등록 결과가 나오기 전에 예전 상태를 알리지 않는다.
        self._status = None
        self._cancel_retry()
        self._delay = REGISTER_RETRY_INITIAL_SECONDS
        self._channel.share(types or None)
        return True

    def _cancel_retry(self) -> None:
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None

    async def _on_event(self, event: RealtimeEvent) -> None:
        # 이 채널의 로그에 이 연결의 투자 환경이 남는다. 체결마다 그 투자 환경으로 다시 정한다.
        environment_var.set(self._names)
        if event.kind is EventKind.REAL:
            for item in event.items:
                try:
                    self._on_item(item, event)
                except Exception as exc:
                    # 한 체결의 오류로 같은 메시지의 다른 체결을 놓치지 않게 한다.
                    logger.error(
                        "fill_feed_item_failed",
                        exc_info=exc,
                        extra={"fields": {"error_type": type(exc).__name__}},
                    )
        elif event.kind is EventKind.REGISTERED:
            # 재연결로 먼저 등록됐으면 기다리던 재등록은 보내지 않는다.
            self._cancel_retry()
            self._delay = REGISTER_RETRY_INITIAL_SECONDS
            self._set_status(FeedStatus(FeedState.LIVE))
        elif event.kind is EventKind.REJECTED:
            log(
                logger,
                logging.WARNING,
                "fill_watch_register_retry",
                target=self._spec.realtime_url,
                type=self._types,
                wait_seconds=self._delay,
            )
            self._cancel_retry()
            self._retry = asyncio.create_task(self._register_later(self._delay))
            self._delay = min(self._delay * 2, REGISTER_RETRY_MAX_SECONDS)
            self._set_status(FeedStatus(FeedState.REJECTED, event.detail))
        elif event.kind is EventKind.DISCONNECTED:
            # 다시 연결되면 등록 결과가 새로 온다. 그 전에는 예전 상태를 알리지 않는다.
            self._status = None

    async def _register_later(self, delay: float) -> None:
        """밀려나 멈춘 연결이면 다시 등록해도 연결을 열지 않는다."""
        await asyncio.sleep(delay)
        self._retry = None
        if self._types:
            self._channel.share(self._types)

    def _set_status(self, status: FeedStatus) -> None:
        self._status = status
        for subscriber in list(self._subscribers):
            self._deliver(subscriber, status)

    def _on_item(self, item: Any, event: RealtimeEvent) -> None:
        # 체결 하나의 수신·처리·알림 로그를 같은 ID로 묶는다.
        request_id = uuid.uuid4().hex[:12]
        request_id_var.set(request_id)
        values = item.get("values") if isinstance(item, dict) else None
        realtime_type = item.get("type") if isinstance(item, dict) else None
        spec = self._environments.get(realtime_type) if isinstance(realtime_type, str) else None
        if not isinstance(values, dict) or spec is None or realtime_type not in FILL_STATUSES:
            log(logger, logging.DEBUG, "realtime_item_skipped", type=realtime_type)
            return
        # 실전 연결은 국내·미국 이벤트가 섞여 오므로 이벤트마다 투자 환경을 정한다.
        environment_var.set(spec.environment.value)
        status = values.get("913")
        if status not in FILL_STATUSES[realtime_type]:
            log(logger, logging.DEBUG, "fill_skipped", type=realtime_type, status=status)
            return
        context = {
            "connection": event.connection,
            "received_clock": event.received_clock,
            "request_id": request_id,
        }
        fill = (
            _domestic_fill(spec, values, **context)
            if spec.market is Market.DOMESTIC
            else _us_fill(spec, values, **context)
        )
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
            filled_quantity=_plain(fill.filled_quantity),
            average_price=_plain(fill.average_price),
            realized_cost=_plain(fill.realized_cost),
        )
        for subscriber in list(self._subscribers):
            if subscriber.spec.environment is spec.environment:
                self._deliver(subscriber, fill)

    def _deliver(self, subscriber: _Subscriber, value: Fill | FeedStatus) -> None:
        try:
            subscriber.handler(value)
        except Exception as exc:
            # 한 기능의 오류로 다른 기능이나 다음 체결을 놓치지 않게 한다.
            logger.error(
                "fill_feed_handler_failed",
                exc_info=exc,
                extra={"fields": {"feature": subscriber.label, "error_type": type(exc).__name__}},
            )


def _domestic_fill(spec: EnvironmentSpec, values: dict[str, Any], **context: Any) -> Fill:
    return Fill(
        spec=spec,
        code=_text(values, "9001"),
        name=_text(values, "302"),
        order_no=_text(values, "9203"),
        side=_side(values),
        # 910·911은 이 주문의 평균 체결가·누적 체결 수량으로 온다. 이번 체결분은 914·915다.
        price=_number(values, "914"),
        quantity=_number(values, "915"),
        ordered=_number(values, "900"),
        remaining=_number(values, "902"),
        time=_time(values, "908"),
        filled_quantity=_number(values, "911"),
        average_price=_number(values, "910"),
        **context,
    )


def _us_fill(spec: EnvironmentSpec, values: dict[str, Any], **context: Any) -> Fill:
    side = _side(values)
    return Fill(
        spec=spec,
        code=_text(values, "9001"),
        name=_text(values, "302"),
        order_no=_text(values, "9203"),
        side=side,
        price=_number(values, "910"),
        quantity=_number(values, "911"),
        ordered=_number(values, "900"),
        remaining=_number(values, "902"),
        # 미국 체결시각도 한국 시간이다.
        time=_time(values, "908"),
        realized_cost=_number(values, "50724") if side == "sell" else None,
        **context,
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
    if text is None:
        return None
    formatted = _hhmmss(text)
    if formatted is None:
        log(logger, logging.WARNING, "fill_value_unreadable", key=key, raw=text[:40])
    return formatted


def _hhmmss(text: str) -> str | None:
    """키움의 HHmmss를 HH:MM:SS로 바꾼다. 형식이 다르면 None이다."""
    if len(text) != 6 or not text.isdigit():
        return None
    return f"{text[:2]}:{text[2:4]}:{text[4:]}"


def _plain(value: Decimal | None) -> str | None:
    """로그용. Decimal을 그대로 넘기면 따옴표가 붙은 JSON 문자열로 남는다."""
    return None if value is None else str(value)
