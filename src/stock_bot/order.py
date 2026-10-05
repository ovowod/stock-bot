"""매수 주문 접수: 모의투자 환경에서만 키움에 매수 주문을 보낸다(국내 kt10000, 미국 ust20000).

주문은 중복될 수 있으므로 키움 호출을 자동으로 다시 보내지 않는다.
"""

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from stock_bot.config import EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.quote import US_EXCHANGE_CODES
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.order")

DOMESTIC_ORDER_PATH = "/api/dostk/ordr"
US_ORDER_PATH = "/api/us/ordr"
MAX_ORDER_KEY_LENGTH = 64
# 키움 문서의 ord_qty·ord_uv 길이(12)를 따른다.
MAX_NUMBER_LENGTH = 12
_INTEGER = re.compile(r"^\d+$")
_DECIMAL = re.compile(r"^\d+(\.\d+)?$")
# 지금은 지정가·시장가만 지원한다. 다른 매매구분은 .scratch/order-panel/spec.md의 Out of Scope 참고.
TRADE_TYPES: dict[Market, dict[str, str]] = {
    Market.DOMESTIC: {"limit": "0", "market": "3"},
    Market.US: {"limit": "00", "market": "03"},
}


class OrderService:
    def __init__(self, kiwoom: KiwoomClient) -> None:
        self._kiwoom = kiwoom

    async def place(self, spec: EnvironmentSpec, request: Any) -> dict[str, Any]:
        """request는 브라우저가 보낸 JSON 본문이다. 해석하지 못했으면 None이다."""
        # 실전투자는 입력 검사보다 먼저, 키움을 부르기 전에 막는다.
        if spec.is_real:
            order_key = request.get("order_key") if isinstance(request, dict) else None
            log(logger, logging.WARNING, "order_blocked_real", order_key=order_key)
            raise AppError("order_not_allowed", "실전투자에서는 주문할 수 없습니다.", 403)
        order = _validate(request, spec.market)
        log(logger, logging.INFO, "order_requested", **order)
        trde_tp = TRADE_TYPES[spec.market][order["order_type"]]
        if spec.market is Market.DOMESTIC:
            api_id, path = "kt10000", DOMESTIC_ORDER_PATH
            body = {
                "dmst_stex_tp": "KRX",
                "stk_cd": order["code"],
                "ord_qty": order["quantity"],
                "ord_uv": order["price"],
                "trde_tp": trde_tp,
                "cond_uv": "",
            }
        else:
            api_id, path = "ust20000", US_ORDER_PATH
            body = {
                "stex_tp": US_EXCHANGE_CODES[order["exchange"]],
                "stk_cd": order["code"],
                "ord_qty": order["quantity"],
                "ord_uv": order["price"],
                "trde_tp": trde_tp,
            }
        try:
            data = await self._kiwoom.call_once(spec, api_id, path, body)
        except AppError as error:
            log(
                logger,
                logging.WARNING,
                "order_rejected",
                order_key=order["order_key"],
                kind=error.kind,
                cause=error.message,
                return_code=error.detail.get("return_code"),
            )
            raise
        order_no = Reader(data, api_id).text("ord_no")
        log(logger, logging.INFO, "order_accepted", order_key=order["order_key"], order_no=order_no)
        return {
            "order_key": order["order_key"],
            "order_no": order_no,
            "accepted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }


def _validate(request: Any, market: Market) -> dict[str, str]:
    if not isinstance(request, dict):
        raise _invalid("주문 내용이 올바르지 않습니다.")
    order_key = _text(request, "order_key")
    if not order_key or len(order_key) > MAX_ORDER_KEY_LENGTH:
        raise _invalid("주문 키가 필요합니다.")
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
        if market is Market.US and not (_number(price, _DECIMAL) and Decimal(price) > 0):
            raise _invalid("가격은 0보다 크고 12글자 이하인 숫자로 입력하세요.")
    return {
        "order_key": order_key,
        "code": code,
        "exchange": exchange,
        "order_type": order_type,
        "quantity": quantity,
        "price": price,
    }


def _number(text: str, pattern: re.Pattern[str]) -> bool:
    return len(text) <= MAX_NUMBER_LENGTH and pattern.match(text) is not None


def _text(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    return value.strip() if isinstance(value, str) else ""


def _invalid(message: str) -> AppError:
    return AppError("invalid_request", message, 400)
