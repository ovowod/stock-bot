"""순위: 선택한 투자 환경의 시장에서 순위 TR을 호출해 화면에 필요한 필드만 정리한다."""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from stock_bot.config import EnvironmentSpec, Market
from stock_bot.errors import AppError, response_format_error
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.ranking")

KST = ZoneInfo("Asia/Seoul")
RANK_LIMIT = 30
DOMESTIC_RANKING_PATH = "/api/dostk/rkinfo"
DOMESTIC_STOCK_INFO_PATH = "/api/dostk/stkinfo"
US_RANKING_PATH = "/api/us/rkinfo"

# 화면의 거래소 값 -> 키움 코드. 국내는 mrkt_tp, 미국은 stex_tp에 들어간다.
EXCHANGES: dict[Market, dict[str, str]] = {
    Market.DOMESTIC: {"all": "000", "kospi": "001", "kosdaq": "101"},
    Market.US: {"all": "0", "nyse": "1", "nasdaq": "2", "amex": "3"},
}
# 인기 종목 집계 구간 -> 키움 코드. 국내는 qry_tp, 미국은 svc_type에 들어간다.
PERIODS: dict[Market, dict[str, str]] = {
    Market.DOMESTIC: {"30s": "5", "1m": "1", "10m": "2", "1h": "3", "today": "4"},
    Market.US: {"30s": "B286", "1m": "B281", "10m": "B282", "1h": "B283", "today": "B284"},
}
CONDITIONS = {"exchange": (EXCHANGES, "all"), "period": (PERIODS, "1h")}
US_EXCHANGE_NAMES = {"NY": "NYSE", "ND": "NASDAQ", "NA": "AMEX"}
# 전일대비기호: 1 상한가, 2 상승, 3 보합, 4 하한가, 5 하락
DIRECTIONS = {"1": "up", "2": "up", "3": "flat", "4": "down", "5": "down"}
# 미국 인기 종목의 부호: + 상승, - 하락, 빈값 보합
SIGN_DIRECTIONS = {"+": "up", "-": "down", "": "flat"}
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
    # 조회 조건: 거래소(exchange) 또는 인기 종목의 집계 구간(period)
    condition: str = "exchange"
    price_key: str = "cur_prc"
    direction_key: str = "pred_pre_sig"
    directions: dict[str, str] = field(default_factory=lambda: DIRECTIONS)
    rate_key: str = "flu_rt"
    # 등락률에 부호가 없고 방향을 따로 주는 TR(usa01980)은 하락이면 음수로 바꾼다.
    unsigned_rate: bool = False
    base_time: Callable[[dict[str, Any], list[Reader]], str | None] | None = None


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


def _rank_change(value_key: str, sign_key: str) -> Callable[[Reader, Market], dict[str, Any]]:
    """순위 변동: 올라간 칸 수는 양수, 내려간 칸 수는 음수, 변동 없음은 0.

    방향은 부호 필드를 따르고, 크기는 값의 절댓값을 쓴다.
    """

    def extra(row: Reader, market: Market) -> dict[str, Any]:
        sign = row.text(sign_key)
        size = row.number(value_key)
        if sign == "":
            return {"rank_change": 0}
        if sign not in ("+", "-") or size is None:
            return {"rank_change": None}
        return {"rank_change": int(abs(size)) * (1 if sign == "+" else -1)}

    return extra


def _kst(date: str, time: str, api_id: str) -> str | None:
    """YYYYMMDD, HHmmss(한국시간)를 ISO 문자열로 바꾼다. 값이 없으면 None."""
    if not date or not time:
        return None
    try:
        return datetime.strptime(date + time, "%Y%m%d%H%M%S").replace(tzinfo=KST).isoformat()
    except ValueError:
        cause = f"집계 시각 형식이 올바르지 않습니다: {date} {time}"
        raise response_format_error(api_id, cause) from None


def _first_row_time(data: dict[str, Any], rows: list[Reader]) -> str | None:
    # 국내 인기 종목은 항목마다 집계 시각이 있다.
    # 모의 서버에서 모두 같았으므로 1위 항목의 시각을 쓴다.
    return _kst(rows[0].text("dt"), rows[0].text("tm"), "ka00198") if rows else None


def _top_level_time(data: dict[str, Any], rows: list[Reader]) -> str | None:
    date, time = str(data.get("base_date") or ""), str(data.get("base_time") or "")
    return _kst(date, time, "usa01980")


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
    ("popular", Market.DOMESTIC): _Tr(
        "ka00198",
        DOMESTIC_STOCK_INFO_PATH,
        "item_inq_rank",
        "bigd_rank",
        lambda period: {"qry_tp": period},
        _rank_change("rank_chg", "rank_chg_sign"),
        condition="period",
        price_key="past_curr_prc",
        direction_key="base_comp_sign",
        rate_key="base_comp_chgr",
        base_time=_first_row_time,
    ),
    ("popular", Market.US): _Tr(
        "usa01980",
        US_RANKING_PATH,
        "result_list",
        "rank",
        lambda period: {"svc_type": period},
        _rank_change("chg_val", "sign"),
        condition="period",
        price_key="curr_pric",
        direction_key="sign_for_gjga",
        directions=SIGN_DIRECTIONS,
        rate_key="diff_rate_for_gjga",
        unsigned_rate=True,
        base_time=_top_level_time,
    ),
}
KINDS = {kind for kind, _ in TRS}


class RankingService:
    def __init__(self, kiwoom: KiwoomClient) -> None:
        self._kiwoom = kiwoom

    async def fetch(
        self, spec: EnvironmentSpec, kind: str, conditions: dict[str, str | None]
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise AppError("unknown_ranking", "알 수 없는 순위입니다.", 404, {"ranking": kind})
        tr = TRS[(kind, spec.market)]
        table, default = CONDITIONS[tr.condition]
        value = conditions.get(tr.condition) or default
        code = table[spec.market].get(value)
        if code is None:
            raise AppError(
                "bad_request",
                f"쓸 수 없는 조회 조건입니다: {tr.condition}={value}",
                400,
                {tr.condition: value},
            )

        data = await self._kiwoom.call_first_page(spec, tr.api_id, tr.path, tr.body(code))
        rows = Reader(data, tr.api_id).rows(tr.list_key)[:RANK_LIMIT]
        items = [
            {**_common(row, index, tr, spec.market), **tr.extra(row, spec.market)}
            for index, row in enumerate(rows, start=1)
        ]
        log(logger, logging.INFO, "ranking_fetched", kind=kind, api_id=tr.api_id, items=len(items))
        result: dict[str, Any] = {
            "environment": spec.environment.value,
            "market": spec.market.value,
            "kind": kind,
            tr.condition: value,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "items": items,
        }
        if tr.base_time:
            result["base_time"] = tr.base_time(data, rows)
        return result


def _common(row: Reader, index: int, tr: _Tr, market: Market) -> dict[str, Any]:
    direction = tr.directions.get(row.text(tr.direction_key), "unknown")
    rate = row.decimal(tr.rate_key)
    if tr.unsigned_rate and rate is not None and direction == "down":
        rate = -abs(rate)
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
        "price": _price(row.number(tr.price_key), market),
        "direction": direction,
        "change_rate": rate,
    }


def _price(value: Decimal | None, market: Market) -> int | float | None:
    if value is None:
        return None
    return int(abs(value)) if market is Market.DOMESTIC else float(abs(value))
