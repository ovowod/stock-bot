"""키움 실시간 주문체결을 지켜보다가 체결 알림을 Discord로 보낸다.

인증정보마다 실시간 연결을 하나 열고, LOGIN 뒤 주문체결 실시간 항목을 등록한다.
체결 감시는 조회만 하고 주문 TR은 부르지 않는다.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

from websockets.asyncio.client import connect as websocket_connect

from stock_bot.account import DOMESTIC_ACCOUNT_PATH
from stock_bot.config import ENVIRONMENTS, Environment, EnvironmentSpec
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import environment_var, log, request_id_var
from stock_bot.notification import FIELD_VALUE_LIMIT, DiscordNotifier, Embed, EmbedField
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.fill_watch")


class RealtimeConnection(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


RealtimeConnect = Callable[[str], Awaitable[RealtimeConnection]]

# LOGIN·REG 응답을 기다리는 시간. 넘으면 연결을 닫고 다시 연결한다.
RESPONSE_TIMEOUT_SECONDS = 10.0
# 다시 연결하기 전 기다리는 시간. 실패가 이어지면 두 배씩 늘리고, 등록에 성공하면 처음으로 되돌린다.
RECONNECT_INITIAL_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 60.0

# 매도 손익 계산용으로 주문번호별로 더해 둔 체결을 마지막 체결 뒤 이만큼 지나면 지운다.
# 일부만 체결된 채 끝난 주문이 쌓이지 않게 하기 위해서다.
# 날짜로 지우지 않는 건 미국 장이 한국 자정을 넘기 때문이다.
FILL_MEMORY_SECONDS = 24 * 3600

# 같은 앱 키로 다른 연결이 들어왔을 때 키움이 기존 연결에 보내는 SYSTEM 코드(모의 서버에서 확인).
SESSION_REPLACED_CODE = "R10001"

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
        # (투자 환경, 주문번호)별로 지금까지 받은 매도 체결. 서버를 재시작하면 사라진다.
        self._sell_fills: dict[tuple[str, str], _OrderFills] = {}

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
            # 끊긴 동안의 체결은 보충하지 않는다. 다시 연결해 등록만 한다.
            delay = RECONNECT_INITIAL_SECONDS
            while True:
                if await self._session(spec, realtime_type, queue):
                    delay = RECONNECT_INITIAL_SECONDS
                log(logger, logging.WARNING, "realtime_reconnect_wait", wait_seconds=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_SECONDS)
        except _SessionReplaced as replaced:
            # 같은 앱 키로 서버가 둘 떠 있으면 다시 연결할 때마다 서로를 끊어낸다.
            # 나중에 연결한 쪽이 지켜보도록 이 서버는 멈추고, 알림이 조용히 끊기지 않게 알린다.
            log(
                logger,
                logging.ERROR,
                "realtime_session_replaced",
                code=replaced.code,
                message=replaced.message,
            )
            await self._notifier.send(
                embed=Embed(
                    title="체결 감시 중단",
                    description=(
                        "다른 곳에서 같은 앱 키로 접속해 이 서버의 체결 감시를 멈춥니다. "
                        "다시 지켜보려면 이 서버를 재시작하세요."
                    ),
                    color=NEUTRAL_COLOR,
                ),
                spec=spec,
            )
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _session(
        self, spec: EnvironmentSpec, realtime_type: str, queue: asyncio.Queue[dict[str, Any]]
    ) -> bool:
        """연결 하나가 끝날 때까지 받는다. LOGIN과 REG까지 성공했으면 True다."""
        registered = False
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
            return registered
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
            registered = True
            while True:
                message = await _receive(connection)
                if message.get("trnm") == "REAL":
                    queue.put_nowait(message)
        except _SessionReplaced:
            raise
        except _RealtimeFailure as failure:
            log(logger, logging.ERROR, failure.event, target=target, **failure.fields)
            if failure.fields.get("trnm") == "LOGIN":
                # LOGIN이 거부되거나 응답이 없으면 만료·무효 토큰일 수 있다.
                # 다음 연결은 새 토큰으로 한다.
                await self._kiwoom.discard_access_token(spec, token)
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
            try:
                await connection.close()
            except Exception as exc:
                # 여기서 오류가 새면 재연결 반복이 끝나 체결 감시가 조용히 멈춘다.
                log(
                    logger,
                    logging.WARNING,
                    "realtime_close_failed",
                    target=target,
                    error_type=type(exc).__name__,
                    cause=str(exc),
                )
        return registered

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
        profit = await self._sell_profit(spec, fill) if fill.side == "sell" else None
        open_orders = await self._open_orders(spec)
        sent = await self._notifier.send(embed=_fill_embed(fill, profit, open_orders), spec=spec)
        log(logger, logging.INFO, "fill_notified", order_no=fill.order_no, sent=sent)

    async def _sell_profit(self, spec: EnvironmentSpec, fill: Fill) -> SellProfit:
        """지금까지 체결된 수량 전체의 매도 손익. 수수료·세금은 빼지 않는다."""
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

        purchase = await self._purchase_price(spec, fill.code)
        if purchase is None:
            return SellProfit(failure=LOOKUP_FAILED)
        cost = purchase * filled
        return SellProfit(
            amount=(entry.amount - cost).quantize(Decimal(1), ROUND_HALF_UP),
            rate=((entry.amount - cost) / cost * 100).quantize(Decimal("0.01"), ROUND_HALF_UP),
            purchase=purchase,
        )

    async def _purchase_price(self, spec: EnvironmentSpec, code: str) -> Decimal | None:
        """당일 실현손익 상세(ka10077)에서 그 종목의 매입가를 찾는다. 못 찾으면 None이다."""
        try:
            data = await self._kiwoom.call(spec, "ka10077", DOMESTIC_ACCOUNT_PATH, {"stk_cd": code})
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

    async def _open_orders(self, spec: EnvironmentSpec) -> list[OpenOrder] | None:
        """그 투자 환경의 미체결 주문. 조회하지 못하면 None이다."""
        # stex_tp=0(통합)은 문서의 허용값이다. 모의투자에서 받아들이는지는 아직 확인하지 못했다.
        body = {"all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "0"}
        try:
            data = await self._kiwoom.call(spec, "ka10075", DOMESTIC_ACCOUNT_PATH, body)
            orders = [_domestic_open_order(row) for row in Reader(data, "ka10075").rows("oso")]
        except AppError as exc:
            log(
                logger,
                logging.WARNING,
                "open_orders_failed",
                api_id="ka10075",
                kind=exc.kind,
                cause=exc.message,
            )
            return None
        log(logger, logging.INFO, "open_orders_fetched", api_id="ka10075", count=len(orders))
        return orders


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
            trnm=trnm,
            return_code=message.get("return_code"),
            return_msg=message.get("return_msg"),
        )
    return message


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


# 한국 증권 앱 관례: 빨강은 매수·이익, 파랑은 손실.
BUY_COLOR = 0xE42939
PROFIT_COLOR = 0xE42939
LOSS_COLOR = 0x2F6FEB
NEUTRAL_COLOR = 0x8B8F98
UNKNOWN = "확인 불가"
LOOKUP_FAILED = "조회 실패"


@dataclass
class _OrderFills:
    amount: Decimal = Decimal(0)
    quantity: Decimal = Decimal(0)
    updated: float = 0.0


@dataclass(frozen=True)
class SellProfit:
    """매도 손익. 계산하지 못했으면 failure에 이유를 담는다."""

    amount: Decimal | None = None  # 원 단위로 반올림
    rate: Decimal | None = None  # %, 소수 둘째 자리
    purchase: Decimal | None = None
    failure: str | None = None


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


@dataclass(frozen=True)
class OpenOrder:
    name: str
    side: str  # 매수, 매도, 매수정정 등
    order_type: str
    price: Decimal | None
    remaining: Decimal | None
    ordered: Decimal | None
    time: str  # HHmmss


def _domestic_open_order(row: Reader) -> OpenOrder:
    return OpenOrder(
        name=row.text("stk_nm"),
        # +, -는 색 표시용이다.
        side=row.text("io_tp_nm").lstrip("+-"),
        order_type=row.text("trde_tp"),
        price=_absolute(row.number("ord_pric")),
        remaining=row.number("oso_qty"),
        ordered=row.number("ord_qty"),
        time=row.text("tm"),
    )


def _absolute(value: Decimal | None) -> Decimal | None:
    return None if value is None else abs(value)


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
    if text is None:
        return None
    if len(text) != 6 or not text.isdigit():
        log(logger, logging.WARNING, "fill_value_unreadable", key=key, raw=text[:40])
        return None
    return f"{text[:2]}:{text[2:4]}:{text[4:]}"


def _fill_embed(
    fill: Fill, profit: SellProfit | None, open_orders: list[OpenOrder] | None
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
            *([] if profit is None else [EmbedField("매도 손익", _profit_text(profit))]),
            _open_orders_field(open_orders),
        ),
    )


def _profit_text(profit: SellProfit) -> str:
    if profit.amount is None or profit.rate is None or profit.purchase is None:
        return profit.failure or LOOKUP_FAILED
    sign = "+" if profit.amount > 0 else "-" if profit.amount < 0 else ""
    return (
        f"{sign}{abs(profit.amount):,}원 ({sign}{abs(profit.rate):.2f}%)"
        f" · 매입가 {_won(profit.purchase)} · 수수료·세금 제외"
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
    # 키움 국내 문서의 "보통"은 이 프로젝트에서 지정가라고 부른다.
    order_type = "지정가" if order.order_type == "보통" else order.order_type
    pricing = order_type if order_type == "시장가" else f"{order_type} {_won(order.price)}"
    remaining = UNKNOWN if order.remaining is None else f"{order.remaining:,.0f}"
    ordered = UNKNOWN if order.ordered is None else f"{order.ordered:,.0f}"
    time = order.time
    if len(time) == 6 and time.isdigit():
        time = f"{time[:2]}:{time[2:4]}:{time[4:]}"
    return f"{order.name} · {order.side} · {pricing} · 미체결 {remaining}/{ordered}주 · {time}"


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
