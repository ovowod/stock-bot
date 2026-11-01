"""자동매매 기능들이 함께 쓰는 판정 부품.

보유종목의 대상 아님 분류, 그 시각을 포함하는 세션 찾기, 시세 체결 메시지의 가격·장구분 읽기를
SL / TP와 Trailing Stop이 같은 규칙으로 쓰게 한다.
로그는 남기지 않는다. 쓰는 쪽이 자기 이벤트 이름으로 남긴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from stock_bot.auto_trading.settings import TIMEZONES
from stock_bot.core.config import Market
from stock_bot.kiwoom.us_exchange import US_EXCHANGE_CODES

# 대상 아님의 이유. 로그의 칸 이름으로도 쓴다.
INELIGIBLE_REASONS = ("credit", "no_purchase_price", "duplicate", "unknown_exchange")
# 시세 체결 메시지의 체결가(10)와 장구분(290) 칸.
PRICE_FIELD = "10"
MARKET_TYPE_FIELD = "290"
# 장구분(290)의 정규장 값. 국내 2는 장중, 미국 0은 정규장이다.
# 국내 장전·장후 시간외, 미국 프리마켓·애프터마켓·주간거래 체결은 판정하지 않는다.
REGULAR_MARKET = {Market.DOMESTIC: "2", Market.US: "0"}
# 시세 실시간 항목. 0B는 국내 주식체결, FE는 미국주식 실시간 체결가다.
QUOTE_TYPES = {Market.DOMESTIC: "0B", Market.US: "FE"}


@dataclass(frozen=True)
class Holding:
    """판정할 수 있는 보유종목 한 줄."""

    name: str
    purchase_price: Decimal
    # 미국 종목의 거래소(NYSE·NASDAQ·AMEX). 국내는 None이다.
    exchange: str | None


@dataclass(frozen=True)
class HoldingsCheck:
    # 종목코드 -> 판정할 수 있는 현금잔고. 제외종목도 담는다.
    eligible: dict[str, Holding]
    # 판정할 수 없는 종목코드 -> 이유. 같은 코드의 현금잔고가 판정 대상이면 담지 않는다.
    # 신용 보유분만 있는 코드는 "credit", 현금잔고가 대상 아님이면 그 이유다.
    ineligible: dict[str, str]
    # 이유마다 대상 아님 줄 수. 같은 종목 여러 줄은 첫 줄을 빼고 센다.
    counts: dict[str, int]
    # 거래소를 찾지 못한 미국 종목코드(줄 순서).
    unknown_exchange_codes: list[str]


def check_holdings(market: Market, rows: Sequence[dict[str, Any]]) -> HoldingsCheck:
    """보유종목 줄들을 판정할 수 있는 종목과 대상 아님으로 나눈다."""
    eligible: dict[str, Holding] = {}
    ineligible: dict[str, str] = {}
    counts = dict.fromkeys(INELIGIBLE_REASONS, 0)
    duplicated: set[str] = set()
    unknown_exchange: list[str] = []
    for row in rows:
        code, price, exchange = row["code"], row["purchase_price"], row["exchange"]
        if row["credit"]:
            # 현금 매도 주문으로 신용 보유분을 팔려다 실패하지 않게 다루지 않는다.
            reason = "credit"
        elif price is None or price <= 0:
            reason = "no_purchase_price"
        elif market is Market.US and exchange not in US_EXCHANGE_CODES:
            # 종목 목록에서 거래소를 찾지 못한 종목은 다루지 않는다.
            # 거래소 없이 주문을 보내다 실패하지 않기 위해서다.
            reason = "unknown_exchange"
            unknown_exchange.append(code)
        elif code in eligible or code in duplicated:
            # 같은 종목의 현금잔고가 여러 줄이면 어느 매입가인지 모른다.
            duplicated.add(code)
            eligible.pop(code, None)
            reason = "duplicate"
        else:
            eligible[code] = Holding(row["name"], price, exchange)
            continue
        counts[reason] += 1
        # 현금잔고의 이유가 신용보다 앞서고, 같은 종목 여러 줄이 다른 이유보다 앞선다.
        if reason == "duplicate" or ineligible.get(code, "credit") == "credit":
            ineligible[code] = reason
    return HoldingsCheck(
        eligible,
        {code: reason for code, reason in ineligible.items() if code not in eligible},
        counts,
        unknown_exchange,
    )


def session_at(
    sessions: Sequence[dict[str, Any]],
    moment: datetime,
    market: Market,
    *,
    include_disabled: bool = False,
) -> tuple[date, dict[str, Any]] | None:
    """그 시각(그 시장 시간대)을 포함하는 세션과 그 시장의 날짜.

    세션은 자동매매 설정의 세션 항목이다. 기준 칸은 기능마다 달라 보지 않는다.
    시작은 포함하고 종료는 빼므로 맞닿은 시각은 뒤 세션이다.
    include_disabled가 아니면 켜진 세션만 찾는다.
    """
    local = moment.astimezone(ZoneInfo(TIMEZONES[market]))
    at = local.time()
    for session in sessions:
        if not include_disabled and not session["enabled"]:
            continue
        if time.fromisoformat(session["start"]) <= at < time.fromisoformat(session["end"]):
            return local.date(), session
    return None


def is_regular_market(market: Market, values: dict[str, Any]) -> bool:
    """시세 체결 메시지의 장구분이 정규장인가."""
    return str(values.get(MARKET_TYPE_FIELD, "")).strip() == REGULAR_MARKET[market]


def read_price(values: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    """체결가(10)의 절댓값과, 판정에 쓸 수 없으면 그 이유. 부호는 등락 방향이다.

    쓸 수 있으면 (가격, None), 쓸 수 없으면 (None, 이유)다.
    """
    raw = values.get(PRICE_FIELD)
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None, "empty"
    try:
        price = abs(Decimal(text))
    except InvalidOperation:
        return None, "not_a_number"
    if not price.is_finite() or price <= 0:
        return None, "not_positive"
    return price, None


def quote_items(market: Market, codes: Sequence[str], holdings: dict[str, Holding]) -> list[Any]:
    """시세 등록 항목. 국내는 접두어 없는 종목코드, 미국은 {티커, 거래소 코드} 객체다."""
    if market is Market.DOMESTIC:
        return list(codes)
    return [
        {"jmcode": code, "stex_tp": US_EXCHANGE_CODES[holdings[code].exchange or ""]}
        for code in codes
    ]
