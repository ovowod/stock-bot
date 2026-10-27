"""키움 실시간 주문체결을 지켜보다가 체결 알림을 Discord로 보낸다.

공유 실시간 연결(realtime)에 인증정보마다 주문체결 실시간 항목을 구독한다.
체결 감시는 조회만 하고 주문 TR은 부르지 않는다.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from stock_bot.account import DOMESTIC_ACCOUNT_PATH
from stock_bot.auto_sell import AutoSellGuard
from stock_bot.config import ENVIRONMENTS, Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import environment_var, log, request_id_var
from stock_bot.notification import FIELD_VALUE_LIMIT, DiscordNotifier, Embed, EmbedField
from stock_bot.open_order import OpenOrder, OpenOrderService
from stock_bot.reader import Reader
from stock_bot.realtime import EventKind, RealtimeEvent, RealtimeHub

logger = logging.getLogger("stock_bot.fill_watch")

# 구독이 거부되면 연결은 그대로 두고 이 간격으로 다시 등록한다.
# 실패가 이어지면 두 배씩 늘리고, 등록에 성공하면 처음으로 되돌린다.
REGISTER_RETRY_INITIAL_SECONDS = 1.0
REGISTER_RETRY_MAX_SECONDS = 60.0

# 미체결·매입가 조회가 연결 실패·시간 초과로 끝나면 이만큼 기다렸다가 한 번만 다시 시도한다.
# 조회만 하는 TR이라 다시 보내도 안전하다. 키움 오류 응답은 다시 시도하지 않는다.
LOOKUP_RETRY_SECONDS = 1.0

# 미국 매도 손익 계산용으로 주문번호별로 더해 둔 체결을 마지막 체결 뒤 이만큼 지나면 지운다.
# 일부만 체결된 채 끝난 주문이 쌓이지 않게 하기 위해서다.
# 날짜로 지우지 않는 건 미국 장이 한국 자정을 넘기 때문이다.
FILL_MEMORY_SECONDS = 24 * 3600

# 매도 체결이 온 종목에 진행 중인 자동 매도가 있으면, 체결 이벤트를 받은 때부터
# 이만큼 그 주문 결과를 기다린 뒤 주문번호를 맞춰 본다.
# 시장가는 주문 응답보다 체결 통보가 먼저 올 수 있다.
AUTO_SELL_RESULT_WAIT_SECONDS = 5.0
AUTO_SELL_MARK = "SL / TP 자동 매도"

# 실시간 연결 하나에 구독할 실시간 항목과 그 이벤트의 투자 환경.
# 00은 국내 주문체결, F5는 미국 실시간 체결이다.
# 국내 실전과 미국 실전은 인증정보와 계좌가 같아 연결 하나로 받는다.
Watch = dict[str, EnvironmentSpec]
WATCHES: tuple[Watch, ...] = (
    {"00": ENVIRONMENTS[Environment.DOMESTIC_PAPER]},
    {"F5": ENVIRONMENTS[Environment.US_PAPER]},
    {"00": ENVIRONMENTS[Environment.DOMESTIC_REAL], "F5": ENVIRONMENTS[Environment.US_REAL]},
)
# 실시간 항목별로 체결을 뜻하는 주문상태(913). 접수·확인·취소·거부 등은 알리지 않는다.
# F5의 체결완료는 2026-10-08 모의 서버에서 확인했고, 부분체결은 키움 가이드 설명을 따른다.
FILL_STATUSES = {"00": {"체결"}, "F5": {"부분체결", "체결완료"}}


def _connection_spec(watch: Watch) -> EnvironmentSpec:
    """연결에 쓸 투자 환경. 한 연결의 투자 환경들은 인증정보·도메인이 같다."""
    return next(iter(watch.values()))


def _environments(watch: Watch) -> str:
    return ",".join(spec.environment.value for spec in watch.values())


class FillWatcher:
    def __init__(
        self,
        kiwoom: KiwoomClient,
        open_orders: OpenOrderService,
        notifier: DiscordNotifier,
        hub: RealtimeHub,
        guard: AutoSellGuard,
    ) -> None:
        self._kiwoom = kiwoom
        # 계좌 화면과 같은 미체결 목록을 알림에 담는다.
        self._open_order_service = open_orders
        self._notifier = notifier
        self._hub = hub
        # 자동 매도 공통 장치. 접수된 자동 매도의 주문번호로 체결에 표시를 붙인다.
        self._guard = guard
        # 거부된 구독을 다시 등록하려고 기다리는 작업. 감시마다 많아야 하나다.
        self._retries: set[asyncio.Task[None]] = set()
        # (투자 환경, 주문번호)별로 지금까지 받은 미국 매도 체결. 서버를 재시작하면 사라진다.
        self._sell_fills: dict[tuple[str, str], _OrderFills] = {}

    def start(self) -> None:
        """알림이 꺼져 있으면 구독하지 않는다. 인증정보가 없는 투자 환경은 건너뛴다."""
        if not self._notifier.enabled:
            log(logger, logging.INFO, "fill_watch_disabled", cause="discord_disabled")
            return
        for watch in WATCHES:
            spec = _connection_spec(watch)
            try:
                self._kiwoom.credentials(spec)
            except AppError as exc:
                log(
                    logger,
                    logging.INFO,
                    "fill_watch_skipped",
                    environment=_environments(watch),
                    cause=exc.message,
                )
                continue
            log(
                logger,
                logging.INFO,
                "fill_watch_started",
                environment=_environments(watch),
                target=spec.realtime_url,
                type=list(watch),
            )
            self._watch(watch)

    async def stop(self) -> None:
        for task in self._retries:
            task.cancel()
        await asyncio.gather(*self._retries, return_exceptions=True)
        self._retries.clear()

    def _watch(self, watch: Watch) -> None:
        # 끊긴 동안의 체결은 보충하지 않는다. 공유 연결이 다시 연결해 등록만 한다.
        # 주문체결은 계좌 단위 항목이라 SL / TP와 함께 받는다.
        types = list(watch)
        delay = REGISTER_RETRY_INITIAL_SECONDS
        retry: asyncio.Task[None] | None = None

        async def on_event(event: RealtimeEvent) -> None:
            nonlocal delay, retry
            # 채널 작업자의 로그에 이 감시의 투자 환경이 남는다. 체결마다 다시 정한다.
            environment_var.set(_environments(watch))
            if event.kind is EventKind.REAL:
                for item in event.items:
                    try:
                        await self._handle(watch, item, event)
                    except Exception as exc:
                        # 한 체결의 처리 오류로 같은 메시지의 다른 체결을 놓치지 않게 한다.
                        logger.error(
                            "fill_handle_failed",
                            exc_info=exc,
                            extra={"fields": {"error_type": type(exc).__name__}},
                        )
            elif event.kind is EventKind.REGISTERED:
                # 재연결로 먼저 등록됐으면 기다리던 재등록은 보내지 않는다.
                delay = REGISTER_RETRY_INITIAL_SECONDS
                if retry is not None:
                    retry.cancel()
                    retry = None
            elif event.kind is EventKind.REJECTED:
                log(
                    logger,
                    logging.WARNING,
                    "fill_watch_register_retry",
                    target=_connection_spec(watch).realtime_url,
                    type=list(watch),
                    wait_seconds=delay,
                )
                if retry is not None:
                    retry.cancel()
                retry = self._retry(channel.share, types, delay)
                delay = min(delay * 2, REGISTER_RETRY_MAX_SECONDS)

        channel = self._hub.channel(_connection_spec(watch), "fill_watch", "체결 감시", on_event)
        channel.share(types)

    def _retry(
        self, share: Callable[[list[str]], None], types: list[str], delay: float
    ) -> asyncio.Task[None]:
        """밀려나 멈춘 연결이면 다시 구독해도 연결을 열지 않는다."""

        async def later() -> None:
            await asyncio.sleep(delay)
            share(types)

        task = asyncio.create_task(later())
        self._retries.add(task)
        task.add_done_callback(self._retries.discard)
        return task

    async def _handle(self, watch: Watch, item: Any, event: RealtimeEvent) -> None:
        # 체결 하나의 수신·처리·알림 로그를 같은 ID로 묶는다.
        request_id_var.set(uuid.uuid4().hex[:12])
        values = item.get("values") if isinstance(item, dict) else None
        realtime_type = item.get("type") if isinstance(item, dict) else None
        spec = watch.get(realtime_type) if isinstance(realtime_type, str) else None
        if not isinstance(values, dict) or spec is None or realtime_type not in FILL_STATUSES:
            log(logger, logging.DEBUG, "realtime_item_skipped", type=realtime_type)
            return
        # 실전 연결은 국내·미국 이벤트가 섞여 오므로 이벤트마다 투자 환경을 정한다.
        environment_var.set(spec.environment.value)
        status = values.get("913")
        if status not in FILL_STATUSES[realtime_type]:
            log(logger, logging.DEBUG, "fill_skipped", type=realtime_type, status=status)
            return
        fill = _domestic_fill(values) if realtime_type == "00" else _us_fill(values)
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
        trigger_request_id = await self._auto_sell(spec, fill, event)
        profit = await self._sell_profit(spec, fill) if fill.side == "sell" else None
        open_orders = await self._open_orders(spec)
        embed = _fill_embed(fill, profit, open_orders, auto_sell=trigger_request_id is not None)
        sent = await self._notifier.send(embed=embed, spec=spec)
        log(
            logger,
            logging.INFO,
            "fill_notified",
            order_no=fill.order_no,
            sent=sent,
            trigger_request_id=trigger_request_id,
        )

    async def _auto_sell(
        self, spec: EnvironmentSpec, fill: Fill, event: RealtimeEvent
    ) -> str | None:
        """이 체결이 SL / TP 자동 매도의 것이면 그 트리거 요청 ID다.

        체결 처리의 요청 ID는 체결마다 따로 두고, 트리거 ID로 찾으면 부분 체결까지 나오게 한다.
        """
        if fill.side != "sell" or fill.code is None or fill.order_no is None:
            return None
        waited = time.monotonic() - event.received_clock
        settled = await self._guard.settled(
            spec.environment, fill.code, AUTO_SELL_RESULT_WAIT_SECONDS - waited
        )
        accepted = self._guard.accepted(spec.environment, fill.order_no)
        if accepted is None:
            if not settled:
                log(
                    logger,
                    logging.WARNING,
                    "fill_auto_sell_wait_timeout",
                    order_no=fill.order_no,
                    # 큐에서 기다린 시간을 포함해 체결 이벤트를 받은 뒤 지난 시간이다.
                    waited_seconds=round(time.monotonic() - event.received_clock, 3),
                    limit_seconds=AUTO_SELL_RESULT_WAIT_SECONDS,
                )
            return None
        log(
            logger,
            logging.INFO,
            "fill_auto_sell_matched",
            order_no=fill.order_no,
            trigger_request_id=accepted.trigger_request_id,
        )
        return accepted.trigger_request_id

    async def _sell_profit(self, spec: EnvironmentSpec, fill: Fill) -> SellProfit:
        """지금까지 체결된 수량 전체의 매도 손익. 수수료·세금은 빼지 않는다."""
        if fill.market is Market.DOMESTIC:
            return await self._domestic_sell_profit(spec, fill)
        now = time.monotonic()
        for key, entry in list(self._sell_fills.items()):
            if now - entry.updated > FILL_MEMORY_SECONDS:
                del self._sell_fills[key]
        if (
            fill.code is None
            or fill.order_no is None
            or fill.price is None
            or fill.quantity is None
            or fill.ordered is None
            or fill.remaining is None
        ):
            return SellProfit(failure=UNKNOWN)

        key = (spec.environment.value, fill.order_no)
        entry = self._sell_fills.setdefault(key, _OrderFills())
        entry.amount += fill.price * fill.quantity
        entry.quantity += fill.quantity
        entry.updated = now
        entry.add_realized_cost(fill.realized_cost, fill.quantity)
        if fill.remaining == 0:
            del self._sell_fills[key]
        filled = fill.ordered - fill.remaining
        if entry.quantity != filled:
            # 재시작 등으로 이 주문의 앞선 체결을 받지 못했다. 이번 체결만으로 늘려 추정하지 않는다.
            log(
                logger,
                logging.WARNING,
                "sell_profit_failed",
                order_no=fill.order_no,
                cause="missed_fills",
                received=_plain(entry.quantity),
                filled=_plain(filled),
            )
            return SellProfit(failure=LOOKUP_FAILED)

        # 미국은 체결 이벤트의 실현손익매입금(50724)이 이번 체결분의 매입 금액이다.
        # ust21630은 모의투자에서 매도 뒤 -994로 실패해 쓰지 않는다(spec Implementation Notes).
        if entry.cost is None:
            log(
                logger,
                logging.WARNING,
                "sell_profit_failed",
                order_no=fill.order_no,
                cause="realized_cost_unusable",
                realized_cost=_plain(fill.realized_cost),
            )
            return SellProfit(failure=LOOKUP_FAILED)
        return _profit(entry.amount, entry.cost, entry.cost / filled, fill.market)

    async def _domestic_sell_profit(self, spec: EnvironmentSpec, fill: Fill) -> SellProfit:
        """국내 00은 910·911로 이 주문의 평균 체결가와 누적 체결 수량을 준다(2026-10-26 모의 로그).
        그래서 앞선 체결을 받지 못했어도 이번 이벤트만으로 계산한다."""
        if (
            fill.code is None
            or fill.average_price is None
            or fill.filled_quantity is None
            or fill.ordered is None
            or fill.remaining is None
        ):
            return SellProfit(failure=UNKNOWN)
        filled = fill.ordered - fill.remaining
        if fill.filled_quantity != filled:
            # 911이 누적 체결 수량이 아니면 이 계산은 맞지 않는다. 추측하지 않는다.
            log(
                logger,
                logging.WARNING,
                "sell_profit_failed",
                order_no=fill.order_no,
                cause="filled_quantity_mismatch",
                filled_quantity=_plain(fill.filled_quantity),
                filled=_plain(filled),
            )
            return SellProfit(failure=LOOKUP_FAILED)
        purchase = await self._purchase_price(spec, fill.code)
        if purchase is None:
            return SellProfit(failure=LOOKUP_FAILED)
        # 평균 체결가는 원 단위로 반올림돼 오므로 체결 금액이 수량만큼 몇 원 다를 수 있다.
        amount = fill.average_price * filled
        return _profit(amount, purchase * filled, purchase, fill.market)

    async def _purchase_price(self, spec: EnvironmentSpec, code: str) -> Decimal | None:
        """당일 실현손익 상세(ka10077)에서 그 종목의 매입가를 찾는다. 못 찾으면 None이다."""
        try:
            data = await self._lookup(
                lambda: self._kiwoom.call(spec, "ka10077", DOMESTIC_ACCOUNT_PATH, {"stk_cd": code})
            )
            prices = {
                row.number("buy_uv")
                # 응답 종목코드에는 A가 붙어 온다(A005930).
                for row in Reader(data, "ka10077").rows("tdy_rlzt_pl_dtl")
                if row.text("stk_cd").removeprefix("A") == code
            }
        except AppError as exc:
            log(
                logger,
                logging.WARNING,
                "sell_profit_failed",
                api_id="ka10077",
                kind=exc.kind,
                cause=exc.message,
            )
            return None
        # 매도 체결마다 줄이 하나씩 온다.
        # 줄마다 매입가가 다르면 어느 값이 이번 매도의 것인지 모른다.
        price = next(iter(prices)) if len(prices) == 1 else None
        if price is None or price <= 0:
            log(
                logger,
                logging.WARNING,
                "sell_profit_failed",
                api_id="ka10077",
                cause="purchase_price_ambiguous" if len(prices) > 1 else "purchase_price_not_found",
                prices=[_plain(p) for p in prices],
            )
            return None
        log(logger, logging.INFO, "purchase_price_fetched", api_id="ka10077", price=_plain(price))
        return price

    async def _lookup[T](self, call: Callable[[], Awaitable[T]]) -> T:
        try:
            return await call()
        except AppError as exc:
            if exc.kind != "connection_error":
                raise
            log(
                logger,
                logging.WARNING,
                "lookup_retry",
                api_id=exc.detail.get("api_id"),
                wait_seconds=LOOKUP_RETRY_SECONDS,
                cause=exc.message,
            )
        await asyncio.sleep(LOOKUP_RETRY_SECONDS)
        return await call()

    async def _open_orders(self, spec: EnvironmentSpec) -> list[OpenOrder] | None:
        """그 투자 환경의 미체결 주문. 조회하지 못하면 None이다."""
        try:
            return await self._lookup(lambda: self._open_order_service.orders(spec))
        except AppError as exc:
            log(
                logger,
                logging.WARNING,
                "open_orders_failed",
                api_id=exc.detail.get("api_id"),
                kind=exc.kind,
                cause=exc.message,
            )
            return None


# 한국 증권 앱 관례: 빨강은 매수·이익, 파랑은 손실.
BUY_COLOR = 0xE42939
PROFIT_COLOR = 0xE42939
LOSS_COLOR = 0x2F6FEB
NEUTRAL_COLOR = 0x8B8F98
UNKNOWN = "확인 불가"
LOOKUP_FAILED = "조회 실패"


# 금액 비교·표시 단위. 원은 1원, 달러는 1센트.
MONEY_UNITS = {Market.DOMESTIC: Decimal(1), Market.US: Decimal("0.01")}
# 같은 주문의 체결마다 실현손익매입금 ÷ 체결량(매입단가)이 이 비율보다 더 다르면
# 체결분 값이 아니라고(누계 값이라고) 본다. 체결분 값이어도 체결마다 조금씩 다르게 온다.
# 2026-10-26 모의: 20주 뒤 1주 체결의 값이 4.93달러로, 평균 매입단가 4.8658달러와 1.3% 달랐다.
# 누계 값이면 비율이 (지금까지 체결 수량 ÷ 이번 체결량)배가 되어 이보다 훨씬 크다.
UNIT_COST_TOLERANCE = Decimal("0.05")


@dataclass
class _OrderFills:
    amount: Decimal = Decimal(0)
    quantity: Decimal = Decimal(0)
    updated: float = 0.0
    # 미국만 쓴다. 쓸 수 없는 값을 한 번이라도 받으면 None이 되어 그 주문의 손익은 조회 실패다.
    cost: Decimal | None = Decimal(0)
    unit_cost: Decimal | None = None

    def add_realized_cost(self, cost: Decimal | None, quantity: Decimal) -> None:
        """체결분 매입 금액을 더한다. 매도로는 평균 매입단가가 바뀌지 않으므로,
        체결마다 매입 금액 ÷ 체결량이 같아야 체결분 값이다. 누계 값이면 비율이 달라진다."""
        if self.cost is None:
            return
        if cost is None or cost <= 0 or quantity <= 0:
            self.cost = None
            return
        unit_cost = cost / quantity
        if (
            self.unit_cost is not None
            and abs(unit_cost - self.unit_cost) > self.unit_cost * UNIT_COST_TOLERANCE
        ):
            self.cost = None
            return
        if self.unit_cost is None:
            self.unit_cost = unit_cost
        self.cost += cost


@dataclass(frozen=True)
class SellProfit:
    """매도 손익. 계산하지 못했으면 failure에 이유를 담는다."""

    amount: Decimal | None = None  # 원 또는 센트 단위로 반올림
    rate: Decimal | None = None  # %, 소수 둘째 자리
    purchase: Decimal | None = None
    failure: str | None = None


@dataclass(frozen=True)
class Fill:
    """체결 이벤트 하나. 읽지 못한 값은 None이다."""

    market: Market
    code: str | None
    name: str | None
    order_no: str | None
    side: str | None  # "buy" | "sell"
    price: Decimal | None
    quantity: Decimal | None
    ordered: Decimal | None
    remaining: Decimal | None
    time: str | None
    # 미국 매도 체결분의 매입 금액(F5 50724). 국내는 None이다.
    realized_cost: Decimal | None = None
    # 국내만: 이 주문의 지금까지 체결 수량(911)과 평균 체결가(910). 미국은 None이다.
    filled_quantity: Decimal | None = None
    average_price: Decimal | None = None


def _domestic_fill(values: dict[str, Any]) -> Fill:
    return Fill(
        market=Market.DOMESTIC,
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
    )


def _us_fill(values: dict[str, Any]) -> Fill:
    side = _side(values)
    return Fill(
        market=Market.US,
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


def _fill_embed(
    fill: Fill,
    profit: SellProfit | None,
    open_orders: list[OpenOrder] | None,
    auto_sell: bool = False,
) -> Embed:
    side = {"buy": "매수", "sell": "매도"}.get(fill.side or "")
    title = f"체결 · {side}" if side else "체결"
    color = BUY_COLOR if fill.side == "buy" else NEUTRAL_COLOR
    if profit is not None and profit.amount is not None:
        if profit.amount > 0:
            title, color = f"{title} · 익절", PROFIT_COLOR
        elif profit.amount < 0:
            title, color = f"{title} · 손절", LOSS_COLOR
        else:
            title = f"{title} · 본절"
    stock = f"{fill.name} ({fill.code})" if fill.name and fill.code else fill.name or fill.code
    money = MONEY_FORMATS[fill.market]
    amount = (
        money(fill.price * fill.quantity)
        if fill.price is not None and fill.quantity is not None
        else UNKNOWN
    )
    return Embed(
        title=title,
        # 사용자가 직접 낸 매도와 구분한다.
        description=AUTO_SELL_MARK if auto_sell else "",
        color=color,
        fields=(
            EmbedField("종목", stock or UNKNOWN),
            EmbedField(
                "이번 체결",
                f"{_shares(fill.quantity)} @ {PRICE_FORMATS[fill.market](fill.price)} ({amount})",
            ),
            EmbedField("누적", _progress(fill)),
            EmbedField("체결 시각", fill.time or UNKNOWN),
            *(
                []
                if profit is None
                else [EmbedField("매도 손익", _profit_text(profit, fill.market))]
            ),
            _open_orders_field(open_orders),
        ),
    )


def _profit(amount: Decimal, cost: Decimal, purchase: Decimal, market: Market) -> SellProfit:
    return SellProfit(
        amount=(amount - cost).quantize(MONEY_UNITS[market], ROUND_HALF_UP),
        rate=((amount - cost) / cost * 100).quantize(Decimal("0.01"), ROUND_HALF_UP),
        purchase=purchase,
    )


def _profit_text(profit: SellProfit, market: Market) -> str:
    if profit.amount is None or profit.rate is None or profit.purchase is None:
        return profit.failure or LOOKUP_FAILED
    money = MONEY_FORMATS[market]
    sign = "+" if profit.amount > 0 else "-" if profit.amount < 0 else ""
    return (
        f"{sign}{money(abs(profit.amount))} ({sign}{abs(profit.rate):.2f}%)"
        f" · 매입가 {PRICE_FORMATS[market](profit.purchase)} · 수수료·세금 제외"
    )


# 미체결 주문은 이 수까지만 한 줄씩 보여주고 나머지는 "외 N건"으로 줄인다.
OPEN_ORDER_LINES = 10


def _open_orders_field(orders: list[OpenOrder] | None) -> EmbedField:
    if orders is None:
        return EmbedField("미체결 주문", "조회 실패")
    name = f"미체결 주문 ({len(orders)}건)"
    if not orders:
        return EmbedField(name, "없음")
    lines: list[str] = []
    for order in sorted(orders, key=lambda o: o.time)[:OPEN_ORDER_LINES]:
        line = _open_order_line(order)
        rest = len(orders) - len(lines) - 1
        # 다음 줄과 "외 N건"까지 들어갈 자리가 없으면 멈춘다. 잘린 줄 대신 생략 건수로 보여준다.
        candidate = [*lines, line, *([f"외 {rest}건"] if rest else [])]
        if len("\n".join(candidate)) > FIELD_VALUE_LIMIT:
            break
        lines.append(line)
    if len(lines) < len(orders):
        lines.append(f"외 {len(orders) - len(lines)}건")
    return EmbedField(name, "\n".join(lines))


def _open_order_line(order: OpenOrder) -> str:
    price = PRICE_FORMATS[order.market](order.price)
    pricing = order.order_type if order.order_type == "시장가" else f"{order.order_type} {price}"
    ordered = UNKNOWN if order.ordered_quantity is None else f"{order.ordered_quantity:,}"
    return (
        f"{order.name} · {order.side_label} · {pricing} · "
        f"미체결 {order.remaining_quantity:,}/{ordered}주 · {order.time}"
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
    if value is None:
        return UNKNOWN
    return f"{value.quantize(MONEY_UNITS[Market.DOMESTIC], ROUND_HALF_UP):,}원"


def _usd(value: Decimal | None) -> str:
    if value is None:
        return UNKNOWN
    return f"${value.quantize(MONEY_UNITS[Market.US], ROUND_HALF_UP):,}"


def _usd_price(value: Decimal | None) -> str:
    """1주 가격. 키움이 주는 소수 넷째 자리까지 보여주고 끝의 0은 지운다($12.0522, $1)."""
    if value is None:
        return UNKNOWN
    text = f"{value.quantize(Decimal('0.0001'), ROUND_HALF_UP):,}".rstrip("0").rstrip(".")
    return f"${text}"


# 금액·손익 표시
MONEY_FORMATS = {Market.DOMESTIC: _won, Market.US: _usd}
# 1주 가격 표시. 미국은 금액보다 자릿수가 많아 따로 둔다.
PRICE_FORMATS = {Market.DOMESTIC: _won, Market.US: _usd_price}
