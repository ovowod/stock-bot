"""종목 검색: 시장 전체의 종목 목록을 받아 투자 환경별로 보관하고,
그 안에서 종목명·종목코드로 찾는다.

키움에는 이름으로 검색하는 TR이 없어서 목록 TR(ka10099, usa10099)을 하루 한 번 받아 쓴다.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from stock_bot.config import Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.stock_search")

KST = ZoneInfo("Asia/Seoul")
SEARCH_LIMIT = 50
MAX_QUERY_LENGTH = 50
DOMESTIC_STOCK_INFO_PATH = "/api/dostk/stkinfo"
US_STOCK_INFO_PATH = "/api/us/stkinfo"
# ka10099 시장구분: 0 코스피(ETF·ETN·리츠 포함), 10 코스닥
DOMESTIC_MARKETS = ("0", "10")
# ka10099는 코스피 주식의 시장명을 "거래소"로 준다. 용어집의 거래소와 헷갈리지 않게 바꿔 보여준다.
DOMESTIC_CATEGORY_NAMES = {"거래소": "코스피"}
# 문서에 있는 미국 거래소만 남긴다. 모의 서버는 문서에 없는 NP도 보냈다.
US_EXCHANGE_NAMES = {"NY": "NYSE", "ND": "NASDAQ", "NA": "AMEX"}


def today_kst() -> date:
    return datetime.now(KST).date()


def _normalize(text: str) -> str:
    """비교용: 띄어쓰기를 모두 지우고 대소문자를 무시한다."""
    return "".join(text.split()).casefold()


@dataclass(frozen=True)
class _Stock:
    item: dict[str, Any]
    code_key: str
    name_keys: tuple[str, ...]


@dataclass(frozen=True)
class _StockList:
    day: date
    fetched_at: str
    stocks: list[_Stock]


class StockSearchService:
    def __init__(self, kiwoom: KiwoomClient, today: Callable[[], date] = today_kst) -> None:
        self._kiwoom = kiwoom
        self._today = today
        self._lists: dict[Environment, _StockList] = {}
        # 투자 환경별로 진행 중인 목록 받기.
        # 그동안 들어온 검색은 같은 결과(성공·실패)를 함께 기다린다.
        self._loading: dict[Environment, asyncio.Task[_StockList]] = {}

    async def search(self, spec: EnvironmentSpec, query: str | None) -> dict[str, Any]:
        query = (query or "").strip()
        if not query or len(query) > MAX_QUERY_LENGTH:
            raise AppError(
                "bad_request",
                f"검색어는 1자 이상 {MAX_QUERY_LENGTH}자 이하로 입력하세요.",
                400,
                {"query_length": len(query)},
            )
        stock_list = await self._stock_list(spec)
        key = _normalize(query)
        ranked = sorted(
            (
                (stage, stock.item["name"], stock.item["code"], stock.item)
                for stock in stock_list.stocks
                if (stage := _match_stage(stock, key)) is not None
            ),
            key=lambda entry: entry[:3],
        )
        log(logger, logging.INFO, "stock_search", query=query, total=len(ranked))
        return {
            "environment": spec.environment.value,
            "market": spec.market.value,
            "query": query,
            "total": len(ranked),
            "truncated": len(ranked) > SEARCH_LIMIT,
            "list_fetched_at": stock_list.fetched_at,
            "items": [entry[3] for entry in ranked[:SEARCH_LIMIT]],
        }

    async def listings(self, spec: EnvironmentSpec) -> dict[str, dict[str, Any]]:
        """종목코드 -> 종목 목록 항목(이름, 거래소 등).

        다른 화면이 순위·검색과 같은 이름과 거래소를 쓰도록 종목 목록에서 꺼낸다.
        """
        stock_list = await self._stock_list(spec)
        listings: dict[str, dict[str, Any]] = {}
        for stock in stock_list.stocks:
            listings.setdefault(stock.item["code"], stock.item)
        return listings

    async def _stock_list(self, spec: EnvironmentSpec) -> _StockList:
        environment = spec.environment
        today = self._today()
        cached = self._lists.get(environment)
        if cached is not None and cached.day == today:
            return cached
        task = self._loading.get(environment)
        if task is None:
            task = asyncio.create_task(self._fetch(spec, today))
            self._loading[environment] = task
            task.add_done_callback(lambda done: self._finish(environment, done))
        # 기다리던 요청 하나가 취소되어도 다른 요청이 함께 쓰는 받기는 계속한다.
        return await asyncio.shield(task)

    def _finish(self, environment: Environment, task: asyncio.Task[_StockList]) -> None:
        if self._loading.get(environment) is task:
            del self._loading[environment]
        # 실패한 받기는 보관하지 않는다. 기다리던 요청이 모두 취소된 경우에도 예외를 확인 처리한다.
        if not task.cancelled() and task.exception() is None:
            self._lists[environment] = task.result()

    async def _fetch(self, spec: EnvironmentSpec, today: date) -> _StockList:
        if spec.market is Market.DOMESTIC:
            stocks: list[_Stock] = []
            # 코스피와 코스닥이 모두 성공해야 보관한다. 하나라도 실패하면 예외가 그대로 나간다.
            for market in DOMESTIC_MARKETS:
                data = await self._kiwoom.call(
                    spec, "ka10099", DOMESTIC_STOCK_INFO_PATH, {"mrkt_tp": market}
                )
                stocks.extend(_domestic(row) for row in Reader(data, "ka10099").rows("list"))
        else:
            data = await self._kiwoom.call(spec, "usa10099", US_STOCK_INFO_PATH, {"stex_tp": "%"})
            stocks = _us(Reader(data, "usa10099").rows("list"))
        log(logger, logging.INFO, "stock_list_fetched", stocks=len(stocks), day=today.isoformat())
        return _StockList(today, datetime.now(UTC).isoformat(timespec="seconds"), stocks)


def _domestic(row: Reader) -> _Stock:
    code, name = row.text("code"), row.text("name")
    category = row.optional("marketName")
    status = row.optional("auditInfo")
    item = {
        "code": code,
        "name": name,
        "english_name": None,
        "exchange": None,
        "category": DOMESTIC_CATEGORY_NAMES.get(category, category) if category else None,
        "industry": row.optional("upName"),
        "status": None if status == "정상" else status,
        "is_etf": None,
    }
    return _Stock(item, _normalize(code), (_normalize(name),))


def _us(rows: list[Reader]) -> list[_Stock]:
    stocks, seen = [], set()
    for row in rows:
        code, name = row.text("stk_cd"), row.text("stk_nm")
        exchange = US_EXCHANGE_NAMES.get(row.optional("stex_tp") or "")
        if exchange is None or (exchange, code) in seen:
            continue
        seen.add((exchange, code))
        english = row.optional("stk_enm")
        item = {
            "code": code,
            "name": name,
            "english_name": english,
            "exchange": exchange,
            "category": None,
            "industry": row.optional("upgb"),
            "status": None,
            "is_etf": row.optional("isEtf") == "Y",
        }
        names = (_normalize(name),) + ((_normalize(english),) if english else ())
        stocks.append(_Stock(item, _normalize(code), names))
    return stocks


def _match_stage(stock: _Stock, key: str) -> int | None:
    """0 코드 일치, 1 코드 앞부분 일치, 2 이름이 검색어로 시작, 3 이름에 포함. 맞지 않으면 None."""
    if stock.code_key == key:
        return 0
    if stock.code_key.startswith(key):
        return 1
    if any(name.startswith(key) for name in stock.name_keys):
        return 2
    if any(key in name for name in stock.name_keys):
        return 3
    return None
