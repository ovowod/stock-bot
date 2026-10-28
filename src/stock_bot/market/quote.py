"""현재가 조회: 주문 패널이 열릴 때 종목 하나의 현재가를 가져온다."""

import logging
from datetime import UTC, datetime
from typing import Any

from stock_bot.core.config import EnvironmentSpec, Market
from stock_bot.core.errors import AppError
from stock_bot.core.logging_setup import log
from stock_bot.kiwoom.kiwoom import KiwoomClient
from stock_bot.kiwoom.reader import Reader
from stock_bot.kiwoom.us_exchange import US_EXCHANGE_CODES

logger = logging.getLogger("stock_bot.quote")

DOMESTIC_QUOTE_PATH = "/api/dostk/stkinfo"
US_QUOTE_PATH = "/api/us/mrkcond"


class QuoteService:
    def __init__(self, kiwoom: KiwoomClient) -> None:
        self._kiwoom = kiwoom

    async def fetch(
        self, spec: EnvironmentSpec, code: str | None, exchange: str | None
    ) -> dict[str, Any]:
        code = (code or "").strip()
        if not code:
            raise AppError("invalid_request", "종목코드가 필요합니다.", 400)
        if spec.market is Market.DOMESTIC:
            api_id, path, body = "ka10001", DOMESTIC_QUOTE_PATH, {"stk_cd": code}
        else:
            stex_tp = US_EXCHANGE_CODES.get(exchange or "")
            if stex_tp is None:
                raise AppError(
                    "invalid_request",
                    "미국 현재가는 NYSE·NASDAQ·AMEX 종목만 조회할 수 있습니다.",
                    400,
                )
            api_id, path, body = "usa20101", US_QUOTE_PATH, {"stex_tp": stex_tp, "stk_cd": code}
        data = await self._kiwoom.call_first_page(spec, api_id, path, body)
        # 가격의 부호는 등락 방향을 뜻하므로 절댓값을 현재가로 쓴다.
        value = Reader(data, api_id).number("cur_prc")
        price: int | float | None = None
        if value is not None:
            price = int(abs(value)) if spec.market is Market.DOMESTIC else float(abs(value))
        log(logger, logging.INFO, "quote_fetched", api_id=api_id, code=code, price=price)
        return {
            "code": code,
            "price": price,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
