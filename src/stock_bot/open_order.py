"""미체결 주문 조회: 국내 ka10075, 미국 ust21050을 불러 화면·취소·체결 알림에 쓸 필드만 정리한다."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from stock_bot.config import EnvironmentSpec, Market
from stock_bot.errors import AppError, response_format_error
from stock_bot.holdings import DOMESTIC_ACCOUNT_PATH, US_ACCOUNT_PATH, StockListings
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
# 실전 ust21050은 미체결이 없으면 빈 목록 대신 return_code 20과 이 메시지 코드로 답한다.
# 2026-10-26 실전 로그: "[2000](571758:해당 계좌의미체결내역이 없습니다.)". 모의는 빈 목록이다.
US_NO_OPEN_ORDERS_CODE = "571758:"


def is_us_no_open_orders(exc: AppError) -> bool:
    """ust21050의 "미체결 없음" 응답이면 True다. 오류가 아니라 빈 목록으로 다룬다."""
    return (
        exc.kind == "kiwoom_error"
        and exc.detail.get("return_code") == 20
        and US_NO_OPEN_ORDERS_CODE in exc.message
    )


@dataclass(frozen=True)
class OpenOrder:
    """미체결 주문 한 건. 계좌 화면, 취소 재확인, 체결 알림이 같은 목록을 쓴다."""

    order_no: str
    code: str
    name: str
    side: str | None  # "buy" | "sell"
    side_label: str  # 매수, 매도정정 등
    order_type: str
    # 시장가 등 가격이 없는 주문은 None이다.
    price: Decimal | None
    ordered_quantity: int | None
    remaining_quantity: int
    time: str  # HH:MM:SS로 바꾸지 못하면 받은 그대로
    exchange: str | None
    # 이 앱에서 취소할 수 없는 이유. 취소할 수 있으면 None이다.
    blocked_reason: str | None
    market: Market

    @property
    def cancelable(self) -> bool:
        return self.blocked_reason is None


class OpenOrderService:
    def __init__(self, kiwoom: KiwoomClient, listings: StockListings | None = None) -> None:
        self._kiwoom = kiwoom
        # 미국 미체결 응답은 거래소를 "미국"으로만 준다.
        # 취소에 쓸 거래소와 이름을 종목 목록에서 찾는다.
        self._listings = listings

    async def fetch(self, spec: EnvironmentSpec) -> dict[str, Any]:
        return {
            "orders": [_json(order) for order in await self.orders(spec)],
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    async def orders(
        self, spec: EnvironmentSpec, listing_required: bool = False
    ) -> list[OpenOrder]:
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

    async def _us_orders(self, spec: EnvironmentSpec, listing_required: bool) -> list[OpenOrder]:
        try:
            data = await self._kiwoom.call(spec, US_API_ID, US_ACCOUNT_PATH, US_REQUEST_BODY)
        except AppError as exc:
            if not is_us_no_open_orders(exc):
                raise
            log(logger, logging.INFO, "open_orders_none_reply", api_id=US_API_ID, cause=exc.message)
            data = {"result_list": []}
        rows = [
            row
            for row in Reader(data, US_API_ID).rows("result_list")
            if row.optional("ord_cntr_tp") != US_CANCEL_ORDER
        ]
        listings = await self._us_listings(spec, listing_required)
        orders = [_us_order(row, spec, listings) for row in rows]
        unlisted = sorted({o.code for o in orders if o.exchange is None})
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


def _json(order: OpenOrder) -> dict[str, Any]:
    """화면에 보내는 미체결 주문. 가격은 국내는 정수, 미국은 소수로 보낸다."""
    price: int | float | None = None
    if order.price is not None:
        price = int(order.price) if order.market is Market.DOMESTIC else float(order.price)
    return {
        "order_no": order.order_no,
        "code": order.code,
        "name": order.name,
        "side": order.side,
        "side_label": order.side_label,
        "order_type": order.order_type,
        "price": price,
        "ordered_quantity": order.ordered_quantity,
        "remaining_quantity": order.remaining_quantity,
        "time": order.time,
        "exchange": order.exchange,
        "cancelable": order.cancelable,
        "blocked_reason": order.blocked_reason,
    }


def _order(row: Reader, spec: EnvironmentSpec) -> OpenOrder:
    order_no = row.text("ord_no")
    code = row.text("stk_cd")
    remaining = row.integer("oso_qty")
    if not order_no or not code or remaining is None:
        raise response_format_error(API_ID, "ord_no, stk_cd, oso_qty 중 빈 값이 있습니다.")
    # +, -는 색 표시용이다.
    side_label = row.text("io_tp_nm").lstrip("+-")
    order_type = row.text("trde_tp")
    price = _optional_number(row, "ord_pric")
    tm = row.text("tm")
    return OpenOrder(
        order_no=order_no,
        code=code,
        name=row.text("stk_nm"),
        side="buy" if "매수" in side_label else "sell" if "매도" in side_label else None,
        side_label=side_label,
        # 키움 국내 문서의 "보통"은 이 프로젝트에서 지정가라고 부른다.
        order_type="지정가" if order_type == "보통" else order_type,
        # 시장가 등 가격이 없는 주문은 0으로 온다.
        price=abs(price) if price else None,
        ordered_quantity=_optional_integer(row, "ord_qty"),
        remaining_quantity=remaining,
        time=_hhmmss(tm) or tm,
        exchange=row.text("stex_tp_txt"),
        blocked_reason=_blocked_reason(row, spec, side_label),
        market=Market.DOMESTIC,
    )


def _us_order(row: Reader, spec: EnvironmentSpec, listings: dict[str, dict[str, Any]]) -> OpenOrder:
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
    price = _optional_number(row, "ord_uv")
    if not spec.orders_allowed:
        blocked: str | None = "real"
    elif row.optional("rsrv_tp") in US_RESERVED:
        # 예약주문은 다른 TR(ust21203)로 취소한다.
        blocked = "reserved"
    elif exchange is None:
        blocked = "exchange"
    else:
        blocked = None
    return OpenOrder(
        order_no=order_no,
        code=code,
        name=listing.get("name") or row.text("frgn_stk_nm"),
        side="sell" if slby_tp == "1" else "buy" if slby_tp == "2" else None,
        side_label=row.text("slby_tp_nm") + modified,
        order_type=row.text("frgn_trde_nm"),
        price=price or None,
        ordered_quantity=_optional_integer(row, "ord_qty"),
        remaining_quantity=remaining,
        # 이미 HH:mm:ss(KST)로 온다.
        time=row.text("ord_time"),
        exchange=exchange,
        blocked_reason=blocked,
        market=Market.US,
    )


def _optional_number(row: Reader, key: str) -> Decimal | None:
    """화면에 보여주기만 하는 가격 칸. 깨진 값이면 그 칸만 비운다."""
    try:
        return row.number(key)
    except AppError:
        log(logger, logging.WARNING, "open_order_value_unreadable", key=key)
        return None


def _optional_integer(row: Reader, key: str) -> int | None:
    """화면에 보여주기만 하는 숫자 칸. 깨진 값이면 목록 전체를 실패시키지 않고 그 칸만 비운다.

    모의 서버가 숫자 칸을 '. 950'처럼 깨진 값으로 보낸 적이 있다(holdings.py의 _HoldingReader).
    취소에 쓰는 주문번호·종목코드·미체결 수량은 여기에 넣지 않는다.
    """
    try:
        return row.integer(key)
    except AppError:
        log(logger, logging.WARNING, "open_order_value_unreadable", key=key)
        return None


def _blocked_reason(row: Reader, spec: EnvironmentSpec, side_label: str) -> str | None:
    """이 앱에서 취소할 수 없는 이유. 여러 이유가 겹치면 실전, 신용, 거래소 순으로 하나만 준다.

    실전은 주문 허용이 꺼진 환경이다.
    """
    if not spec.orders_allowed:
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
