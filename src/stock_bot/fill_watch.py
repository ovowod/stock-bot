"""체결 feed로 내 계좌의 체결을 받아 체결 알림을 Discord로 보낸다.

체결 감시는 조회만 하고 주문 TR은 부르지 않는다.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from stock_bot.account import DOMESTIC_ACCOUNT_PATH
from stock_bot.auto_sell import AutoSell, AutoSellMatch
from stock_bot.config import ENVIRONMENTS, Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.fill_feed import FILL_TYPES, FeedStatus, Fill, FillFeed
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import environment_var, log, request_id_var
from stock_bot.notification import FIELD_VALUE_LIMIT, DiscordNotifier, Embed, EmbedField
from stock_bot.open_order import OpenOrder, OpenOrderService
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.fill_watch")

# 미체결·매입가 조회가 연결 실패·시간 초과로 끝나면 이만큼 기다렸다가 한 번만 다시 시도한다.
# 조회만 하는 TR이라 다시 보내도 안전하다. 키움 오류 응답은 다시 시도하지 않는다.
LOOKUP_RETRY_SECONDS = 1.0

# 미국 매도 손익 계산용으로 주문번호별로 더해 둔 체결을 마지막 체결 뒤 이만큼 지나면 지운다.
# 일부만 체결된 채 끝난 주문이 쌓이지 않게 하기 위해서다.
# 날짜로 지우지 않는 건 미국 장이 한국 자정을 넘기 때문이다.
FILL_MEMORY_SECONDS = 24 * 3600

# 체결을 알릴 투자 환경. 인증정보마다 묶는다. 국내 실전과 미국 실전은 인증정보와 계좌가 같다.
WATCHES: tuple[tuple[EnvironmentSpec, ...], ...] = (
    (ENVIRONMENTS[Environment.DOMESTIC_PAPER],),
    (ENVIRONMENTS[Environment.US_PAPER],),
    (ENVIRONMENTS[Environment.DOMESTIC_REAL], ENVIRONMENTS[Environment.US_REAL]),
)
# 밀려남 중단 알림에 쓰는 기능 이름.
LABEL = "체결 감시"


def _environments(watch: tuple[EnvironmentSpec, ...]) -> str:
    return ",".join(spec.environment.value for spec in watch)


class FillWatcher:
    def __init__(
        self,
        kiwoom: KiwoomClient,
        open_orders: OpenOrderService,
        notifier: DiscordNotifier,
        feed: FillFeed,
        auto_sell: AutoSell,
    ) -> None:
        self._kiwoom = kiwoom
        # 계좌 화면과 같은 미체결 목록을 알림에 담는다.
        self._open_order_service = open_orders
        self._notifier = notifier
        self._feed = feed
        # 접수된 자동 매도의 주문번호로 체결에 표시를 붙인다.
        self._auto_sell = auto_sell
        # 받은 체결을 순서대로 알린다. 알림은 조회·전송을 기다리므로 체결 feed에서 떼어 둔다.
        self._fills: asyncio.Queue[Fill] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        # (투자 환경, 주문번호)별로 지금까지 받은 미국 매도 체결. 서버를 재시작하면 사라진다.
        self._sell_fills: dict[tuple[str, str], _OrderFills] = {}

    def start(self) -> None:
        """알림이 꺼져 있으면 구독하지 않는다. 인증정보가 없는 투자 환경은 건너뛴다."""
        if not self._notifier.enabled:
            log(logger, logging.INFO, "fill_watch_disabled", cause="discord_disabled")
            return
        self._worker = asyncio.create_task(self._work())
        for watch in WATCHES:
            spec = watch[0]
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
                type=list(dict.fromkeys(FILL_TYPES[s.environment] for s in watch)),
            )
            # 끊긴 동안의 체결은 보충하지 않는다. 공유 연결이 다시 연결해 등록만 한다.
            for each in watch:
                self._feed.subscribe(each, LABEL, self._on_fill)

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    def _on_fill(self, value: Fill | FeedStatus) -> None:
        # 등록 상태는 체결 feed가 다시 시도하며 로그로 남긴다. 체결 알림은 체결만 본다.
        if isinstance(value, Fill):
            self._fills.put_nowait(value)

    async def _work(self) -> None:
        while True:
            fill = await self._fills.get()
            # 체결 feed가 붙인 요청 ID로 수신부터 알림까지 로그를 묶는다.
            environment_var.set(fill.spec.environment.value)
            request_id_var.set(fill.request_id)
            try:
                await self._handle(fill)
            except Exception as exc:
                # 한 체결의 처리 오류로 다음 체결을 놓치지 않게 한다.
                logger.error(
                    "fill_handle_failed",
                    exc_info=exc,
                    extra={"fields": {"error_type": type(exc).__name__}},
                )

    async def _handle(self, fill: Fill) -> None:
        spec = fill.spec
        auto_sell = await self._auto_sell_of(spec, fill)
        profit = await self._sell_profit(spec, fill) if fill.side == "sell" else None
        open_orders = await self._open_orders(spec)
        embed = _fill_embed(fill, profit, open_orders, auto_sell)
        sent = await self._notifier.send(embed=embed, spec=spec)
        log(
            logger,
            logging.INFO,
            "fill_notified",
            order_no=fill.order_no,
            sent=sent,
            trigger_request_id=auto_sell.trigger_request_id if auto_sell else None,
        )

    async def _auto_sell_of(self, spec: EnvironmentSpec, fill: Fill) -> AutoSellMatch | None:
        """이 체결을 낸 자동 매도. 자동 매도의 것이 아니면 None이다.

        체결 처리의 요청 ID는 체결마다 따로 두고, 트리거 ID로 찾으면 부분 체결까지 나오게 한다.
        """
        if fill.side != "sell" or fill.code is None or fill.order_no is None:
            return None
        return await self._auto_sell.match(
            spec.environment, fill.code, fill.order_no, fill.received_clock
        )

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


def _fill_embed(
    fill: Fill,
    profit: SellProfit | None,
    open_orders: list[OpenOrder] | None,
    auto_sell: AutoSellMatch | None = None,
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
        description=f"{auto_sell.source} 자동 매도" if auto_sell else "",
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
