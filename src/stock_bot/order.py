"""주문 접수: 모의투자 환경에서만 키움에 주문을 보낸다.

매수는 국내 kt10000·미국 ust20000, 매도는 국내 kt10001·미국 ust20001이다.
매도는 보내기 직전에 잔고를 다시 확인한다.
국내 주문 취소는 kt10003이고, 보내기 직전에 미체결을 다시 확인한다.
주문은 중복될 수 있으므로 키움 호출을 자동으로 다시 보내지 않는다.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from stock_bot.config import Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError, response_format_error
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.quote import US_EXCHANGE_CODES
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.order")

DOMESTIC_ORDER_PATH = "/api/dostk/ordr"
US_ORDER_PATH = "/api/us/ordr"
MAX_ORDER_KEY_LENGTH = 64
# 키움까지 갔는지 알 수 없는 오류. 접수됐을 수 있으므로 거부가 아니라 확인 불가로 알린다.
UNKNOWN_RESULT_KINDS = {"connection_error", "response_format_error"}
US_DECIMALS_MESSAGE = (
    "미국 주식 가격은 $1 이상이면 소수 둘째 자리, "
    "$1 미만이면 소수 넷째 자리까지 입력할 수 있습니다."
)
UNKNOWN_RESULT_MESSAGE = (
    "접수 여부를 확인할 수 없습니다. 키움에서 주문 내역을 확인한 뒤 다시 주문하세요."
)
CANCEL_UNKNOWN_RESULT_MESSAGE = (
    "접수 여부를 확인할 수 없습니다. 키움에서 주문 내역을 확인한 뒤 다시 시도하세요."
)
CANCEL_API_ID = "kt10003"
# 키움 문서의 orig_ord_no 길이(7)를 따른다.
_ORDER_NO = re.compile(r"^\d{1,7}$")
# 키움 문서의 ord_qty·ord_uv 길이(12)를 따른다.
MAX_NUMBER_LENGTH = 12
_INTEGER = re.compile(r"^\d+$")
_DECIMAL = re.compile(r"^\d+(\.\d+)?$")
SIDES = {"buy", "sell"}
ORDER_API_IDS: dict[Market, dict[str, str]] = {
    Market.DOMESTIC: {"buy": "kt10000", "sell": "kt10001"},
    Market.US: {"buy": "ust20000", "sell": "ust20001"},
}
# 지금은 지정가·시장가만 지원한다. 다른 매매구분은 .scratch/order-panel/spec.md의 Out of Scope 참고.
TRADE_TYPES: dict[Market, dict[str, str]] = {
    Market.DOMESTIC: {"limit": "0", "market": "3"},
    Market.US: {"limit": "00", "market": "03"},
}


HoldingLookup = Callable[[EnvironmentSpec, str], Awaitable[dict[str, Any]]]
OpenOrderLookup = Callable[[EnvironmentSpec], Awaitable[list[dict[str, Any]]]]


class OrderService:
    def __init__(
        self, kiwoom: KiwoomClient, holding: HoldingLookup, open_orders: OpenOrderLookup
    ) -> None:
        self._kiwoom = kiwoom
        # 매도 직전 잔고 확인. 계좌 서비스가 준다.
        self._holding = holding
        # 취소 직전 미체결 확인. 미체결 주문 서비스가 준다.
        self._open_orders = open_orders
        # 받은 주문 키. 처리 중이거나 이미 처리한 키로 다시 오면 키움에 보내지 않는다.
        # 서버가 다시 시작될 때까지만 기억한다.
        self._order_keys: dict[Environment, set[str]] = {}
        self._sell_locks: dict[tuple[Environment, str], asyncio.Lock] = {}
        self._cancel_locks: dict[tuple[Environment, str], asyncio.Lock] = {}

    async def place(self, spec: EnvironmentSpec, request: Any) -> dict[str, Any]:
        """request는 브라우저가 보낸 JSON 본문이다. 해석하지 못했으면 None이다."""
        # 실전투자는 입력 검사보다 먼저, 키움을 부르기 전에 막는다.
        if spec.is_real:
            order_key = request.get("order_key") if isinstance(request, dict) else None
            log(logger, logging.WARNING, "order_blocked_real", order_key=order_key)
            raise AppError("order_not_allowed", "실전투자에서는 주문할 수 없습니다.", 403)
        order = _validate(request, spec.market)
        self._claim_key(spec, order["order_key"])
        log(logger, logging.INFO, "order_requested", **order)
        if order["side"] == "sell":
            # 서로 다른 주문 키라도 같은 종목의 매도가 같은 매도 가능 수량을 함께 쓰지 않게,
            # 잔고 확인부터 주문 응답까지 하나씩 처리한다.
            lock = self._sell_locks.setdefault((spec.environment, order["code"]), asyncio.Lock())
            async with lock:
                await self._check_sellable(spec, order)
                return await self._send(spec, order)
        return await self._send(spec, order)

    async def cancel(self, spec: EnvironmentSpec, request: Any) -> dict[str, Any]:
        """국내 미체결 주문을 취소한다. request는 브라우저가 보낸 JSON 본문이다."""
        # 실전투자는 입력 검사보다 먼저, 키움을 부르기 전에 막는다.
        if spec.is_real:
            order_key = request.get("order_key") if isinstance(request, dict) else None
            log(logger, logging.WARNING, "cancel_blocked_real", order_key=order_key)
            raise AppError("order_not_allowed", "실전투자에서는 주문 취소를 할 수 없습니다.", 403)
        if spec.market is not Market.DOMESTIC:
            raise AppError(
                "unsupported_market", "미국 투자 환경의 주문 취소는 지원하지 않습니다.", 400
            )
        cancel = _validate_cancel(request)
        self._claim_key(spec, cancel["order_key"])
        log(logger, logging.INFO, "cancel_requested", **cancel)
        # 같은 원주문의 취소는 미체결 재확인부터 키움 응답까지 하나씩 처리한다.
        lock = self._cancel_locks.setdefault((spec.environment, cancel["order_no"]), asyncio.Lock())
        async with lock:
            order = await self._check_open_order(spec, cancel)
            return await self._send_cancel(spec, cancel, order)

    def _claim_key(self, spec: EnvironmentSpec, order_key: str) -> None:
        """처음 받은 주문 키만 통과시킨다. 처리 중이거나 이미 처리한 키는 409다."""
        # 검사와 등록 사이에 await가 없으므로 같은 키로 동시에 와도 하나만 통과한다.
        seen = self._order_keys.setdefault(spec.environment, set())
        if order_key in seen:
            log(logger, logging.WARNING, "order_duplicate", order_key=order_key)
            raise AppError(
                "duplicate_order",
                "같은 주문 키로 이미 받은 주문입니다. 키움에서 주문 내역을 확인하세요.",
                409,
            )
        seen.add(order_key)

    async def _check_open_order(
        self, spec: EnvironmentSpec, cancel: dict[str, str]
    ) -> dict[str, Any]:
        """취소 직전에 미체결을 다시 조회해 그 주문을 찾는다. 취소할 수 없으면 보내지 않는다."""
        fields = {"order_key": cancel["order_key"], "order_no": cancel["order_no"]}
        try:
            orders = await self._open_orders(spec)
        except AppError as error:
            log(
                logger,
                logging.WARNING,
                "open_orders_check_failed",
                **fields,
                kind=error.kind,
                cause=error.message,
            )
            # 취소 TR을 부르기 전이므로 접수 여부 확인 불가가 아니라 실패다.
            raise AppError(
                "open_orders_check_failed",
                f"미체결을 확인하지 못해 취소하지 않았습니다. {error.message}",
                502,
                {"api_id": error.detail.get("api_id")},
            ) from error
        order = next((o for o in orders if o["order_no"] == cancel["order_no"]), None)
        if order is None:
            log(logger, logging.WARNING, "cancel_blocked", **fields, reason="not_found")
            raise AppError("open_order_not_found", "이미 체결되었거나 취소된 주문입니다.", 400)
        remaining = order["remaining_quantity"]
        log(logger, logging.INFO, "open_order_checked", **fields, remaining_quantity=remaining)
        if not order["cancelable"]:
            log(
                logger,
                logging.WARNING,
                "cancel_blocked",
                **fields,
                reason=order["blocked_reason"],
            )
            raise AppError(
                "cancel_not_supported",
                "이 앱에서 취소할 수 없는 주문입니다. 키움 앱에서 취소하세요.",
                400,
            )
        if int(cancel["quantity"]) > remaining:
            log(
                logger,
                logging.WARNING,
                "cancel_blocked",
                **fields,
                reason="quantity",
                quantity=cancel["quantity"],
                remaining_quantity=remaining,
            )
            raise AppError(
                "cancel_quantity_exceeded",
                f"미체결 수량({remaining:,}주)을 넘어 취소하지 않았습니다.",
                400,
                {"remaining_quantity": remaining},
            )
        return order

    async def _send_cancel(
        self, spec: EnvironmentSpec, cancel: dict[str, str], order: dict[str, Any]
    ) -> dict[str, Any]:
        """취소 TR을 한 번만 호출한다. 남은 수량 전부면 0(잔량 전부)으로 보낸다."""
        all_remaining = int(cancel["quantity"]) == order["remaining_quantity"]
        body = {
            "dmst_stex_tp": "KRX",
            "orig_ord_no": cancel["order_no"],
            "stk_cd": order["code"],
            "cncl_qty": "0" if all_remaining else cancel["quantity"],
        }
        data, order_no = await self._call_order(
            spec,
            CANCEL_API_ID,
            DOMESTIC_ORDER_PATH,
            body,
            cancel["order_key"],
            CANCEL_UNKNOWN_RESULT_MESSAGE,
        )
        cancelled = _cancelled_quantity(data.get("cncl_qty"))
        log(
            logger,
            logging.INFO,
            "cancel_accepted",
            order_key=cancel["order_key"],
            original_order_no=cancel["order_no"],
            order_no=order_no,
            cancel_quantity=cancelled,
        )
        return {
            "order_key": cancel["order_key"],
            "order_no": order_no,
            "original_order_no": cancel["order_no"],
            "cancel_quantity": cancelled,
            "accepted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    async def _check_sellable(self, spec: EnvironmentSpec, order: dict[str, str]) -> None:
        """매도 주문 직전에 잔고를 다시 조회한다. 확인하지 못하거나 넘치면 주문을 보내지 않는다."""
        try:
            holding = await self._holding(spec, order["code"])
        except AppError as error:
            log(
                logger,
                logging.WARNING,
                "sellable_check_failed",
                order_key=order["order_key"],
                kind=error.kind,
                cause=error.message,
            )
            # 주문 TR을 부르기 전이므로 접수 여부 확인 불가가 아니라 실패다.
            raise AppError(
                "sellable_check_failed",
                f"잔고를 확인하지 못해 주문하지 않았습니다. {error.message}",
                502,
                {"api_id": error.detail.get("api_id")},
            ) from error
        sellable = holding["sellable_quantity"]
        log(
            logger,
            logging.INFO,
            "sellable_checked",
            order_key=order["order_key"],
            code=order["code"],
            quantity=holding["quantity"],
            sellable_quantity=sellable,
        )
        if int(order["quantity"]) > sellable:
            log(
                logger,
                logging.WARNING,
                "order_blocked_sellable",
                order_key=order["order_key"],
                quantity=order["quantity"],
                sellable_quantity=sellable,
            )
            raise AppError(
                "sellable_exceeded",
                f"매도 가능 수량({sellable:,}주)을 넘어 주문하지 않았습니다.",
                400,
                {"sellable_quantity": sellable},
            )

    async def _send(self, spec: EnvironmentSpec, order: dict[str, str]) -> dict[str, Any]:
        """주문 TR을 한 번만 호출하고 결과를 분류한다."""
        api_id = ORDER_API_IDS[spec.market][order["side"]]
        trde_tp = TRADE_TYPES[spec.market][order["order_type"]]
        if spec.market is Market.DOMESTIC:
            path = DOMESTIC_ORDER_PATH
            body = {
                "dmst_stex_tp": "KRX",
                "stk_cd": order["code"],
                "ord_qty": order["quantity"],
                "ord_uv": order["price"],
                "trde_tp": trde_tp,
                "cond_uv": "",
            }
        else:
            path = US_ORDER_PATH
            body = {
                "stex_tp": US_EXCHANGE_CODES[order["exchange"]],
                "stk_cd": order["code"],
                "ord_qty": order["quantity"],
                "ord_uv": order["price"],
                "trde_tp": trde_tp,
            }
            if order["side"] == "sell":
                # 매도 TR(ust20001)에만 있는 칸. STOP 주문만 쓰므로 지정가·시장가는 빈 값이다.
                body["stop_pric"] = ""
        _, order_no = await self._call_order(
            spec, api_id, path, body, order["order_key"], UNKNOWN_RESULT_MESSAGE
        )
        log(logger, logging.INFO, "order_accepted", order_key=order["order_key"], order_no=order_no)
        return {
            "order_key": order["order_key"],
            "order_no": order_no,
            "accepted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    async def _call_order(
        self,
        spec: EnvironmentSpec,
        api_id: str,
        path: str,
        body: dict[str, str],
        order_key: str,
        unknown_message: str,
    ) -> tuple[dict[str, Any], str]:
        """주문류 TR을 한 번만 호출하고 결과를 분류한다. 응답과 새 주문번호를 돌려준다."""
        try:
            data = await self._kiwoom.call_once(spec, api_id, path, body)
            order_no = Reader(data, api_id).text("ord_no")
            if not order_no:
                raise response_format_error(api_id, "ord_no 값이 비어 있습니다.")
        except AppError as error:
            if error.kind in UNKNOWN_RESULT_KINDS:
                log(
                    logger,
                    logging.ERROR,
                    "order_result_unknown",
                    order_key=order_key,
                    kind=error.kind,
                    cause=error.message,
                )
                raise AppError(
                    "order_result_unknown", unknown_message, 502, {"api_id": api_id}
                ) from error
            log(
                logger,
                logging.WARNING,
                "order_rejected",
                order_key=order_key,
                kind=error.kind,
                cause=error.message,
                return_code=error.detail.get("return_code"),
            )
            raise
        return data, order_no


def _validate_cancel(request: Any) -> dict[str, str]:
    if not isinstance(request, dict):
        raise _invalid("취소 내용이 올바르지 않습니다.")
    order_key = _text(request, "order_key")
    if not order_key or len(order_key) > MAX_ORDER_KEY_LENGTH:
        raise _invalid("주문 키가 필요합니다.")
    order_no = _text(request, "order_no")
    if not _ORDER_NO.match(order_no):
        raise _invalid("원주문번호가 올바르지 않습니다.")
    quantity = _text(request, "quantity")
    if not _number(quantity, _INTEGER) or int(quantity) < 1:
        raise _invalid("수량은 1주 이상, 12자리 이하의 정수로 입력하세요.")
    return {"order_key": order_key, "order_no": order_no, "quantity": quantity}


def _cancelled_quantity(value: Any) -> int | None:
    """kt10003의 cncl_qty('000000000003')를 정수로. 0이거나 읽을 수 없으면 None이다.

    잔량 전부(0)로 보냈을 때 키움이 0을 돌려주면 실제로 취소된 수량을 알 수 없다.
    재조회한 미체결 수량은 그사이 체결됐을 수 있어 대신 쓰지 않는다.
    """
    try:
        quantity = int(str(value).strip())
    except ValueError:
        return None
    return quantity or None


def _validate(request: Any, market: Market) -> dict[str, str]:
    if not isinstance(request, dict):
        raise _invalid("주문 내용이 올바르지 않습니다.")
    order_key = _text(request, "order_key")
    if not order_key or len(order_key) > MAX_ORDER_KEY_LENGTH:
        raise _invalid("주문 키가 필요합니다.")
    side = _text(request, "side")
    if side not in SIDES:
        raise _invalid("매수·매도 구분이 올바르지 않습니다.")
    code = _text(request, "code")
    if not code:
        raise _invalid("종목코드가 필요합니다.")
    exchange = ""
    if market is Market.US:
        exchange = _text(request, "exchange")
        if exchange not in US_EXCHANGE_CODES:
            raise _invalid("미국 주문은 NYSE·NASDAQ·AMEX 종목만 할 수 있습니다.")
    order_type = _text(request, "order_type")
    if order_type not in TRADE_TYPES[market]:
        raise _invalid("지원하지 않는 주문 유형입니다.")
    quantity = _text(request, "quantity")
    if not _number(quantity, _INTEGER) or int(quantity) < 1:
        raise _invalid("수량은 1주 이상, 12자리 이하의 정수로 입력하세요.")
    price = ""
    if order_type == "market":
        if request.get("price") is not None:
            raise _invalid("시장가 주문에는 가격을 넣지 않습니다.")
    else:
        price = _text(request, "price")
        if market is Market.DOMESTIC and not (_number(price, _INTEGER) and int(price) >= 1):
            raise _invalid("가격은 1원 이상, 12자리 이하의 정수로 입력하세요.")
        if market is Market.US:
            if not (_number(price, _DECIMAL) and Decimal(price) > 0):
                raise _invalid("가격은 0보다 크고 12글자 이하인 숫자로 입력하세요.")
            if not _us_decimals_ok(price):
                raise _invalid(US_DECIMALS_MESSAGE)
    return {
        "order_key": order_key,
        "side": side,
        "code": code,
        "exchange": exchange,
        "order_type": order_type,
        "quantity": quantity,
        "price": price,
    }


def _us_decimals_ok(price: str) -> bool:
    """키움 1517 응답에 적힌 규칙: $1 미만은 소수 넷째 자리, $1 이상은 소수 둘째 자리까지."""
    decimals = len(price.partition(".")[2])
    return decimals <= (4 if Decimal(price) < 1 else 2)


def _number(text: str, pattern: re.Pattern[str]) -> bool:
    return len(text) <= MAX_NUMBER_LENGTH and pattern.match(text) is not None


def _text(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    return value.strip() if isinstance(value, str) else ""


def _invalid(message: str) -> AppError:
    return AppError("invalid_request", message, 400)
