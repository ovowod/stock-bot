"""국내 미체결 주문 조회: 키움 ka10075를 불러 화면과 주문 취소에 필요한 필드만 정리한다."""

import logging
from datetime import UTC, datetime
from typing import Any

from stock_bot.account import DOMESTIC_ACCOUNT_PATH
from stock_bot.config import EnvironmentSpec, Market
from stock_bot.errors import AppError, response_format_error
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.open_order")

API_ID = "ka10075"
# stex_tp=0(통합)은 문서의 허용값이다. 모의투자에서 받아들이는지는 아직 확인하지 못했다.
REQUEST_BODY = {"all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "0"}


class OpenOrderService:
    def __init__(self, kiwoom: KiwoomClient) -> None:
        self._kiwoom = kiwoom

    async def fetch(self, spec: EnvironmentSpec) -> dict[str, Any]:
        return {
            "orders": await self.orders(spec),
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    async def orders(self, spec: EnvironmentSpec) -> list[dict[str, Any]]:
        """그 투자 환경의 미체결 주문 전부. 국내만 지원한다."""
        if spec.market is not Market.DOMESTIC:
            raise AppError(
                "unsupported_market", "미국 투자 환경의 미체결 주문은 지원하지 않습니다.", 400
            )
        data = await self._kiwoom.call(spec, API_ID, DOMESTIC_ACCOUNT_PATH, REQUEST_BODY)
        orders = [_order(row, spec) for row in Reader(data, API_ID).rows("oso")]
        log(logger, logging.INFO, "open_orders_fetched", api_id=API_ID, count=len(orders))
        return orders


def _order(row: Reader, spec: EnvironmentSpec) -> dict[str, Any]:
    order_no = row.text("ord_no")
    code = row.text("stk_cd")
    remaining = row.integer("oso_qty")
    if not order_no or not code or remaining is None:
        raise response_format_error(API_ID, "ord_no, stk_cd, oso_qty 중 빈 값이 있습니다.")
    # +, -는 색 표시용이다.
    side_label = row.text("io_tp_nm").lstrip("+-")
    order_type = row.text("trde_tp")
    price = _optional_integer(row, "ord_pric")
    tm = row.text("tm")
    blocked = _blocked_reason(row, spec, side_label)
    return {
        "order_no": order_no,
        "code": code,
        "name": row.text("stk_nm"),
        "side": "buy" if "매수" in side_label else "sell" if "매도" in side_label else None,
        "side_label": side_label,
        # 키움 국내 문서의 "보통"은 이 프로젝트에서 지정가라고 부른다.
        "order_type": "지정가" if order_type == "보통" else order_type,
        # 시장가 등 가격이 없는 주문은 0으로 온다.
        "price": abs(price) if price else None,
        "ordered_quantity": _optional_integer(row, "ord_qty"),
        "remaining_quantity": remaining,
        "time": _hhmmss(tm) or tm,
        "exchange": row.text("stex_tp_txt"),
        "cancelable": blocked is None,
        "blocked_reason": blocked,
    }


def _optional_integer(row: Reader, key: str) -> int | None:
    """화면에 보여주기만 하는 숫자 칸. 깨진 값이면 목록 전체를 실패시키지 않고 그 칸만 비운다.

    모의 서버가 숫자 칸을 '. 950'처럼 깨진 값으로 보낸 적이 있다(account.py의 _HoldingReader).
    취소에 쓰는 주문번호·종목코드·미체결 수량은 여기에 넣지 않는다.
    """
    try:
        return row.integer(key)
    except AppError:
        log(logger, logging.WARNING, "open_order_value_unreadable", key=key)
        return None


def _blocked_reason(row: Reader, spec: EnvironmentSpec, side_label: str) -> str | None:
    """이 앱에서 취소할 수 없는 이유. 여러 이유가 겹치면 실전, 신용, 거래소 순으로 하나만 준다."""
    if spec.is_real:
        return "real"
    if "신용" in side_label:
        return "credit"
    # 모의투자는 KRX만 지원해 NXT·SOR 취소의 거래소 값은 확인할 수 없다. KRX 주문만 취소한다.
    if row.text("stex_tp") != "1" or row.text("sor_yn") == "Y":
        return "exchange"
    return None


def _hhmmss(text: str) -> str | None:
    """키움의 HHmmss를 HH:MM:SS로 바꾼다. 형식이 다르면 None이다."""
    if len(text) != 6 or not text.isdigit():
        return None
    return f"{text[:2]}:{text[2:4]}:{text[4:]}"
