"""순위: 선택한 투자 환경의 시장에서 순위 TR을 호출해 화면에 필요한 필드만 정리한다."""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from stock_bot.config import EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.ranking")

RANK_LIMIT = 30
DOMESTIC_RANKING_PATH = "/api/dostk/rkinfo"
US_RANKING_PATH = "/api/us/rkinfo"

# 화면의 거래소 값 -> 키움 코드. 국내는 mrkt_tp, 미국은 stex_tp에 들어간다.
EXCHANGES: dict[Market, dict[str, str]] = {
    Market.DOMESTIC: {"all": "000", "kospi": "001", "kosdaq": "101"},
    Market.US: {"all": "0", "nyse": "1", "nasdaq": "2", "amex": "3"},
}
US_EXCHANGE_NAMES = {"NY": "NYSE", "ND": "NASDAQ", "NA": "AMEX"}
# 전일대비기호: 1 상한가, 2 상승, 3 보합, 4 하한가, 5 하락
DIRECTIONS = {"1": "up", "2": "up", "3": "flat", "4": "down", "5": "down"}
# 통합(stex_tp=3) 조회는 국내 종목코드에 거래소 접미어를 붙여 돌려준다(예: 005930_AL).
_EXCHANGE_SUFFIX = re.compile(r"_(AL|NX)$")


@dataclass(frozen=True)
class _Tr:
    api_id: str
    path: str
    list_key: str
    # 순위 필드. 없는 TR은 응답 목록 순서를 순위로 쓴다.
    rank_key: str | None
    body: Callable[[str], dict[str, str]]
    extra: Callable[[Reader, Market], dict[str, Any]]


def _trading_value_extra(row: Reader, market: Market) -> dict[str, Any]:
    # 거래대금 단위: 국내 백만원, 미국 천 USD
    value = row.number("trde_prica")
    if market is Market.DOMESTIC:
        return {
            "trading_value": None if value is None else int(value * 1_000_000),
            "previous_rank": row.integer("pred_rank"),
        }
    return {
        "trading_value": None if value is None else float(value * 1000),
        "previous_rank": None,
    }


def _volume_extra(key: str) -> Callable[[Reader, Market], dict[str, Any]]:
    return lambda row, market: {"volume": row.integer(key)}


TRS: dict[tuple[str, Market], _Tr] = {
    ("trading_value", Market.DOMESTIC): _Tr(
        "ka10032",
        DOMESTIC_RANKING_PATH,
        "trde_prica_upper",
        "now_rank",
        lambda exchange: {"mrkt_tp": exchange, "mang_stk_incls": "1", "stex_tp": "3"},
        _trading_value_extra,
    ),
    ("trading_value", Market.US): _Tr(
        "usa20540",
        US_RANKING_PATH,
        "result_list",
        "rank",
        lambda exchange: {
            "stex_tp": exchange,
            "inds_cd": "000",
            "stk_tp": "0",
            "trde_qty_tp": "0",
            "stk_cnd": "0",
            "pric_cnd": "0",
            "trde_prica_cnd": "0",
        },
        _trading_value_extra,
    ),
    ("gainers", Market.DOMESTIC): _Tr(
        "ka10027",
        DOMESTIC_RANKING_PATH,
        "pred_pre_flu_rt_upper",
        None,
        lambda exchange: {
            "mrkt_tp": exchange,
            "sort_tp": "1",  # 상승률
            "trde_qty_cnd": "0000",
            "stk_cnd": "0",
            "crd_cnd": "0",
            "updown_incls": "1",
            "pric_cnd": "0",
            "trde_prica_cnd": "0",
            "stex_tp": "3",
        },
        _volume_extra("now_trde_qty"),
    ),
    ("gainers", Market.US): _Tr(
        "usa20910",
        US_RANKING_PATH,
        "result_list",
        "rank",
        lambda exchange: {
            "stex_tp": exchange,
            "inds_cd": "000",
            "inds_cls_tp": "0",
            "sort_tp": "1",  # 전일대비 상승률
            "stk_tp": "0",
            "stk_cnd": "0",
            "pric_cnd": "0",
            "trde_prica_cnd": "0",
            "trde_qty_tp": "0",
        },
        _volume_extra("trde_qty"),
    ),
    ("volume", Market.DOMESTIC): _Tr(
        "ka10030",
        DOMESTIC_RANKING_PATH,
        "tdy_trde_qty_upper",
        None,
        lambda exchange: {
            "mrkt_tp": exchange,
            "sort_tp": "1",  # 거래량
            "mang_stk_incls": "0",
            "crd_tp": "0",
            "trde_qty_tp": "0",
            "pric_tp": "0",
            "trde_prica_tp": "0",
            "mrkt_open_tp": "0",
            "stex_tp": "3",
        },
        _volume_extra("trde_qty"),
    ),
    ("volume", Market.US): _Tr(
        "usa20530",
        US_RANKING_PATH,
        "result_list",
        "rank",
        lambda exchange: {
            "stex_tp": exchange,
            "inds_cd": "000",
            "stk_tp": "0",
            "trde_qty_tp": "0",
            "qry_tp": "0",  # 거래량상위
            "stk_cnd": "0",
            "pric_cnd": "0",
            "trde_prica_cnd": "0",
        },
        _volume_extra("acc_trde_qty"),
    ),
}
KINDS = {kind for kind, _ in TRS}


class RankingService:
    def __init__(self, kiwoom: KiwoomClient) -> None:
        self._kiwoom = kiwoom
        # 순위 화면은 TR 여러 개를 이어 부른다. 다시 시도나 조건 변경이 겹쳐도
        # 호출 한도에 덜 걸리도록 순위용 키움 호출은 한 번에 하나만 실행한다.
        self._lock = asyncio.Lock()

    async def fetch(self, spec: EnvironmentSpec, kind: str, exchange: str | None) -> dict[str, Any]:
        if kind not in KINDS:
            raise AppError("unknown_ranking", "알 수 없는 순위입니다.", 404, {"ranking": kind})
        tr = TRS[(kind, spec.market)]
        exchange = exchange or "all"
        code = EXCHANGES[spec.market].get(exchange)
        if code is None:
            raise AppError(
                "bad_request",
                f"이 시장에서 쓸 수 없는 거래소입니다: {exchange}",
                400,
                {"exchange": exchange},
            )

        async with self._lock:
            data = await self._kiwoom.call_first_page(spec, tr.api_id, tr.path, tr.body(code))
        rows = Reader(data, tr.api_id).rows(tr.list_key)[:RANK_LIMIT]
        items = [
            {**_common(row, index, tr, spec.market), **tr.extra(row, spec.market)}
            for index, row in enumerate(rows, start=1)
        ]
        log(logger, logging.INFO, "ranking_fetched", kind=kind, api_id=tr.api_id, items=len(items))
        return {
            "environment": spec.environment.value,
            "market": spec.market.value,
            "kind": kind,
            "exchange": exchange,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "items": items,
        }


def _common(row: Reader, index: int, tr: _Tr, market: Market) -> dict[str, Any]:
    exchange = None
    if market is Market.US:
        raw = row.text("stex_tp")
        exchange = US_EXCHANGE_NAMES.get(raw, raw)
    return {
        "rank": row.integer(tr.rank_key) if tr.rank_key else index,
        "code": _EXCHANGE_SUFFIX.sub("", row.text("stk_cd")),
        "name": row.text("stk_nm"),
        "exchange": exchange,
        # 가격의 부호는 등락 방향을 뜻하므로 절댓값을 가격으로 쓴다.
        "price": _price(row.number("cur_prc"), market),
        "direction": DIRECTIONS.get(row.text("pred_pre_sig"), "unknown"),
        "change_rate": row.decimal("flu_rt"),
    }


def _price(value: Decimal | None, market: Market) -> int | float | None:
    if value is None:
        return None
    return int(abs(value)) if market is Market.DOMESTIC else float(abs(value))
