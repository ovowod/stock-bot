"""미체결 주문 조회: 국내 ka10075, 미국 ust21050을 불러 화면과 취소에 필요한 필드만 정리한다."""

import logging
from datetime import UTC, datetime
from typing import Any

from stock_bot.account import DOMESTIC_ACCOUNT_PATH, US_ACCOUNT_PATH, StockListings
from stock_bot.config import EnvironmentSpec, Market
from stock_bot.errors import AppError, response_format_error
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.open_order")

API_ID = "ka10075"
# stex_tp=0(통합)은 문서의 허용값이다. 모의투자에서 받아들이는지는 아직 확인하지 못했다.
REQUEST_BODY = {"all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "0"}
US_API_ID = "ust21050"
# ord_dt 빈 값은 오늘이다. 체결 알림과 같은 본문이다.
US_REQUEST_BODY = {"ord_dt": "", "slby_tp": "0", "stex_tp": "", "stk_cd": ""}
# ust21050의 주문종류(ord_cntr_tp). 취소주문 줄은 거둬들일 수 없으므로 보여주지 않는다.
US_CANCEL_ORDER = "12"
US_MODIFY_ORDER = "11"
US_RESERVED = {"예약", "1"}


class OpenOrderService:
    def __init__(self, kiwoom: KiwoomClient, listings: StockListings | None = None) -> None:
        self._kiwoom = kiwoom
        # 미국 미체결 응답은 거래소를 "미국"으로만 준다.
        # 취소에 쓸 거래소와 이름을 종목 목록에서 찾는다.
        self._listings = listings

    async def fetch(self, spec: EnvironmentSpec) -> dict[str, Any]:
        return {
            "orders": await self.orders(spec),
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    async def orders(
        self, spec: EnvironmentSpec, listing_required: bool = False
    ) -> list[dict[str, Any]]:
        """그 투자 환경의 미체결 주문 전부.

        listing_required면 미국 종목 목록을 받지 못했을 때 오류를 던진다. 취소 재확인은 이것을 써서,
        일시적인 목록 장애를 "취소할 수 없는 주문"이 아니라 다시 시도할 수 있는 실패로 돌려준다.
        """
        if spec.market is Market.US:
            return await self._us_orders(spec, listing_required)
        data = await self._kiwoom.call(spec, API_ID, DOMESTIC_ACCOUNT_PATH, REQUEST_BODY)
        orders = [_order(row, spec) for row in Reader(data, API_ID).rows("oso")]
        log(logger, logging.INFO, "open_orders_fetched", api_id=API_ID, count=len(orders))
        return orders

    async def _us_orders(
        self, spec: EnvironmentSpec, listing_required: bool
    ) -> list[dict[str, Any]]:
        data = await self._kiwoom.call(spec, US_API_ID, US_ACCOUNT_PATH, US_REQUEST_BODY)
        rows = [
            row
            for row in Reader(data, US_API_ID).rows("result_list")
            if row.optional("ord_cntr_tp") != US_CANCEL_ORDER
        ]
        listings = await self._us_listings(spec, listing_required)
        orders = [_us_order(row, spec, listings) for row in rows]
        unlisted = sorted({o["code"] for o in orders if o["exchange"] is None})
        if unlisted:
            # 조회마다 한 줄만 남긴다. 그 주문의 취소가 막힌 이유다.
            log(
                logger,
                logging.WARNING,
                "open_order_tickers_unlisted",
                api_id=US_API_ID,
                tickers=",".join(unlisted),
                count=len(unlisted),
            )
        log(logger, logging.INFO, "open_orders_fetched", api_id=US_API_ID, count=len(orders))
        return orders

    async def _us_listings(
        self, spec: EnvironmentSpec, required: bool
    ) -> dict[str, dict[str, Any]]:
        """종목 목록을 받지 못해도 미체결 목록은 보여준다. 그때는 거래소를 모른다."""
        if self._listings is None:
            return {}
        try:
            return await self._listings(spec)
        except AppError as error:
            log(
                logger,
                logging.WARNING,
                "stock_list_unavailable",
                kind=error.kind,
                cause=error.message,
                required=required,
            )
            if required:
                raise
            return {}


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


def _us_order(
    row: Reader, spec: EnvironmentSpec, listings: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    order_no = row.text("ord_no")
    code = row.text("stk_cd")
    remaining = row.integer("ord_remnq")
    if not order_no or not code or remaining is None:
        raise response_format_error(US_API_ID, "ord_no, stk_cd, ord_remnq 중 빈 값이 있습니다.")
    slby_tp = row.text("slby_tp")
    # 국내 표기(매수정정·매도정정)와 맞춘다.
    modified = "정정" if row.optional("ord_cntr_tp") == US_MODIFY_ORDER else ""
    listing = listings.get(code, {})
    exchange = listing.get("exchange")
    price = _optional_decimal(row, "ord_uv")
    if spec.is_real:
        blocked: str | None = "real"
    elif row.optional("rsrv_tp") in US_RESERVED:
        # 예약주문은 다른 TR(ust21203)로 취소한다.
        blocked = "reserved"
    elif exchange is None:
        blocked = "exchange"
    else:
        blocked = None
    return {
        "order_no": order_no,
        "code": code,
        "name": listing.get("name") or row.text("frgn_stk_nm"),
        "side": "sell" if slby_tp == "1" else "buy" if slby_tp == "2" else None,
        "side_label": row.text("slby_tp_nm") + modified,
        "order_type": row.text("frgn_trde_nm"),
        "price": price or None,
        "ordered_quantity": _optional_integer(row, "ord_qty"),
        "remaining_quantity": remaining,
        # 이미 HH:mm:ss(KST)로 온다.
        "time": row.text("ord_time"),
        "exchange": exchange,
        "cancelable": blocked is None,
        "blocked_reason": blocked,
    }


def _optional_decimal(row: Reader, key: str) -> float | None:
    """화면에 보여주기만 하는 소수 칸. 깨진 값이면 그 칸만 비운다."""
    try:
        return row.decimal(key)
    except AppError:
        log(logger, logging.WARNING, "open_order_value_unreadable", key=key)
        return None


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
