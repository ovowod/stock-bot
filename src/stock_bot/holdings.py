"""보유종목: 잔고 TR을 부르고 한 줄을 해석하는 곳. 계좌 화면·매도 확인·SL / TP가 이 결과를 쓴다.

한 줄은 모든 쓰는 곳이 필요로 하는 칸을 담는다. 얼마나 엄격하게 볼지는 쓰는 곳이 정한다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from stock_bot.config import Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError, response_format_error
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.reader import Reader

logger = logging.getLogger("stock_bot.holdings")

DOMESTIC_ACCOUNT_PATH = "/api/dostk/acnt"
US_ACCOUNT_PATH = "/api/us/acnt"

StockListings = Callable[[EnvironmentSpec], Awaitable[dict[str, dict[str, Any]]]]


@dataclass(frozen=True)
class Holding:
    """잔고 TR 한 줄. 읽지 못한 숫자 칸은 None이다. 이름은 늘 있다(없으면 종목코드)."""

    code: str
    name: str
    quantity: int | None
    sellable_quantity: int | None
    purchase_price: Decimal | None
    current_price: Decimal | None
    purchase_amount: Decimal | None
    evaluation_amount: Decimal | None
    profit_loss: Decimal | None
    return_rate: Decimal | None
    # 국내 신용 보유분(crd_tp != 00). 현금 매도(kt10001) 대상이 아니다. 미국은 늘 False다.
    credit: bool = False
    # 미국 종목 목록의 거래소. 목록에 없거나 국내면 None이다.
    exchange: str | None = None
    weight: Decimal | None = None
    currency: str | None = None
    evaluation_amount_krw: Decimal | None = None
    profit_loss_krw: Decimal | None = None


@dataclass(frozen=True)
class HoldingsSnapshot:
    rows: list[Holding]
    # 원래 잔고 응답. 계좌 요약은 화면이 여기서 직접 읽는다.
    response: dict[str, Any]
    # 미국에서 종목 목록을 받았는지. 국내와 목록 없이 읽은 조회는 True다.
    listings_ok: bool = True


class _HoldingReader(Reader):
    """보유종목 숫자 칸 하나가 깨지거나 빠져도 계좌 전체를 실패시키지 않고 그 칸만 비운다.

    모의 서버가 장중에 pl_amt를 '. 950'처럼 깨진 값으로 보낸 적이 있다.
    뜻을 알 수 없는 값은 추측하지 않는다. 꼭 필요한 칸은 _count와 text로 읽는다.
    """

    def number(self, key: str) -> Decimal | None:
        raw = self.optional(key)
        if raw is None:
            return None
        try:
            return super().number(key)
        except AppError:
            log(logger, logging.WARNING, "holding_value_unreadable", key=key, raw=raw[:40])
            return None


class Holdings:
    def __init__(self, kiwoom: KiwoomClient, listings: StockListings | None = None) -> None:
        self._kiwoom = kiwoom
        # 잔고 TR은 미국 ETF를 영문명으로, NYSE Arca ETF를 아멕스로 준다.
        # 순위·검색과 같은 이름과 거래소를 쓰려고 종목 목록에서 찾는다.
        self._listings = listings
        # 자동 매도용 잔고 조회 묶음. 다음에 읽을 묶음(그 결과를 기다리는 Future)과 기다리는 수.
        self._next_balance: dict[Environment, asyncio.Future[HoldingsSnapshot]] = {}
        self._balance_waiters: dict[Environment, int] = {}
        self._reading_balance: set[Environment] = set()
        self._balance_tasks: set[asyncio.Task[None]] = set()

    async def read(
        self, spec: EnvironmentSpec, *, patient: bool = False, with_listings: bool = True
    ) -> HoldingsSnapshot:
        """잔고 TR을 부르는 유일한 곳. 미국은 with_listings면 종목 목록도 함께 받는다."""
        if spec.market is Market.DOMESTIC:
            data = await self._kiwoom.call(
                spec,
                "kt00018",
                DOMESTIC_ACCOUNT_PATH,
                {"qry_tp": "1", "dmst_stex_tp": "KRX"},
                patient=patient,
            )
            rows = Reader(data, "kt00018").rows("acnt_evlt_remn_indv_tot", _HoldingReader)
            return HoldingsSnapshot([_domestic(row) for row in rows], data)
        # 종목코드를 넣으면 거래소도 넣어야 한다(모의 서버 1517).
        # 그런데 잔고의 거래소 표기가 주문과 다를 수 있어(NYSE Arca ETF를 아멕스로 준다)
        # 전체를 받아 티커로 찾는다.
        balance = self._kiwoom.call(
            spec, "ust21070", US_ACCOUNT_PATH, {"stex_tp": "", "stk_cd": ""}, patient=patient
        )
        listings: dict[str, dict[str, Any]] | None = {}
        if with_listings:
            # 서로 다른 TR은 호출 간격을 기다리지 않으므로 함께 보낸다.
            data, listings = await asyncio.gather(balance, self._stock_listings(spec))
        else:
            data = await balance
        rows = Reader(data, "ust21070").rows("result_list", _HoldingReader)
        return HoldingsSnapshot(
            [_us(row, listings or {}) for row in rows], data, listings_ok=listings is not None
        )

    async def holding(self, spec: EnvironmentSpec, code: str) -> dict[str, Any]:
        """한 종목의 보유수량과 매도 가능 수량. 매도 주문 직전 확인에 쓴다.

        보유하지 않으면 두 수량이 0이다. 수량을 읽지 못하면 추측하지 않고 오류로 돌려준다.
        """
        return _holding(spec, code, await self.read(spec, with_listings=False))

    async def fresh_holding(self, spec: EnvironmentSpec, code: str) -> dict[str, Any]:
        """holding과 같지만, 동시에 부른 종목들이 잔고 조회 한 번을 함께 쓴다(자동 매도용).

        부른 뒤에 시작한 조회의 결과만 쓴다. 이미 진행 중인 조회는 기다리지 않고 다음 조회에 낀다.
        호출 한도 대기에 시간 제한을 두지 않는다. 화면을 붙잡지 않는 서버 작업이기 때문이다.
        """
        environment = spec.environment
        batch = self._next_balance.get(environment)
        if batch is None:
            batch = self._next_balance[environment] = asyncio.get_running_loop().create_future()
            self._balance_waiters[environment] = 0
            if environment not in self._reading_balance:
                task = asyncio.create_task(self._read_balances(spec))
                self._balance_tasks.add(task)
                task.add_done_callback(self._balance_tasks.discard)
        self._balance_waiters[environment] += 1
        # 기다리던 쪽 하나가 취소돼도 함께 쓰는 조회는 계속한다.
        return _holding(spec, code, await asyncio.shield(batch))

    async def _read_balances(self, spec: EnvironmentSpec) -> None:
        """기다리는 묶음이 없어질 때까지 묶음마다 한 번씩 읽는다."""
        environment = spec.environment
        self._reading_balance.add(environment)
        try:
            while (batch := self._next_balance.pop(environment, None)) is not None:
                waiters = self._balance_waiters.pop(environment, 0)
                try:
                    snapshot = await self.read(spec, patient=True, with_listings=False)
                except Exception as error:
                    batch.set_exception(error)
                    log(logger, logging.WARNING, "balance_shared_failed", waiters=waiters)
                    continue
                batch.set_result(snapshot)
                log(logger, logging.INFO, "balance_shared", waiters=waiters)
        finally:
            self._reading_balance.discard(environment)

    async def positions(self, spec: EnvironmentSpec) -> list[dict[str, Any]]:
        """SL / TP가 지켜볼 보유종목 줄. 매입가를 읽지 못한 칸은 None이다.

        미국은 종목 목록의 거래소를 붙이고, 목록에 없으면 None이다.
        종목 목록을 받지 못하면 오류다. 거래소 없이 모든 종목을 조용히 빼지 않게 한다.
        """
        if spec.market is Market.US and self._listings is None:
            raise _stock_list_unavailable()
        snapshot = await self.read(spec)
        if not snapshot.listings_ok:
            raise _stock_list_unavailable()
        rows = [
            {
                "code": h.code,
                "name": h.name,
                "purchase_price": h.purchase_price,
                "credit": h.credit,
                "exchange": h.exchange,
            }
            for h in snapshot.rows
        ]
        api_id = "kt00018" if spec.market is Market.DOMESTIC else "ust21070"
        log(logger, logging.INFO, "positions_fetched", api_id=api_id, count=len(rows))
        return rows

    async def _stock_listings(self, spec: EnvironmentSpec) -> dict[str, dict[str, Any]] | None:
        """종목 목록을 받지 못하면 None이다. 계좌 확인은 계속하고 잔고 TR의 이름을 쓴다."""
        if self._listings is None:
            return None
        try:
            return await self._listings(spec)
        except AppError as error:
            log(
                logger,
                logging.WARNING,
                "stock_list_unavailable",
                kind=error.kind,
                cause=error.message,
            )
            return None


def _domestic(row: Reader) -> Holding:
    code = _strip_prefix(row.text("stk_cd"))
    return Holding(
        code=code,
        name=row.optional("stk_nm") or code,
        quantity=_count(row, "rmnd_qty"),
        sellable_quantity=_count(row, "trde_able_qty"),
        purchase_price=row.number("pur_pric"),
        current_price=row.number("cur_prc"),
        purchase_amount=row.number("pur_amt"),
        evaluation_amount=row.number("evlt_amt"),
        profit_loss=row.number("evltv_prft"),
        return_rate=row.number("prft_rt"),
        # 현금 매도(kt10001)로 팔 수 있는 현금잔고는 crd_tp=00이다.
        credit=row.text("crd_tp") != "00",
        weight=row.number("poss_rt"),
    )


def _us(row: Reader, listings: dict[str, dict[str, Any]]) -> Holding:
    code = row.text("stk_cd")
    listing = listings.get(code, {})
    return Holding(
        code=code,
        # 화면과 같은 이름을 쓰도록 종목 목록의 이름을 먼저 쓴다.
        name=listing.get("name") or row.optional("frgn_stk_nm") or code,
        quantity=_count(row, "poss_qty"),
        sellable_quantity=_count(row, "sell_alowq"),
        # 2026-10-25 모의 계좌 25종목에서 매입금액 ÷ 보유수량과 같음을 확인했다.
        purchase_price=row.number("frgn_stk_book_uv"),
        current_price=row.number("now_pric"),
        purchase_amount=row.number("frgn_stk_book_amt"),
        evaluation_amount=row.number("evlt_amt"),
        profit_loss=row.number("pl_amt"),
        return_rate=row.number("pl_rt"),
        exchange=listing.get("exchange"),
        currency=row.optional("crnc_code"),
        evaluation_amount_krw=row.number("evlt_amt_krw"),
        profit_loss_krw=row.number("pl_amt_krw"),
    )


def _count(row: Reader, key: str) -> int | None:
    """꼭 있어야 하는 수량 칸. 없으면 응답 형식 오류이고, 깨진 값은 None이다."""
    row.text(key)
    return row.integer(key)


def _holding(spec: EnvironmentSpec, code: str, snapshot: HoldingsSnapshot) -> dict[str, Any]:
    """잔고에서 한 종목의 보유수량과 매도 가능 수량을 고른다."""
    if spec.market is Market.DOMESTIC:
        api_id, quantity_key, sellable_key = "kt00018", "rmnd_qty", "trde_able_qty"
    else:
        api_id, quantity_key, sellable_key = "ust21070", "poss_qty", "sell_alowq"
    # 국내는 현금 매도(kt10001) 대상인 현금잔고 줄만 센다. 신용 보유분은 세지 않는다.
    rows = [h for h in snapshot.rows if h.code == code and not h.credit]
    if len(rows) > 1:
        # kt00018 문서 예제가 합산 조회인데도 같은 종목 두 줄을 준다.
        # 뜻을 모르므로 더하지도 고르지도 않는다.
        raise AppError(
            "unsupported_holding",
            "지원하지 않는 잔고 형태입니다. (같은 종목이 여러 줄)",
            502,
            {"api_id": api_id},
        )
    quantity = sellable = 0
    if rows:
        quantity = _required_count(rows[0].quantity, quantity_key, api_id)
        sellable = _required_count(rows[0].sellable_quantity, sellable_key, api_id)
    result = {"code": code, "held": bool(rows), "quantity": quantity, "sellable_quantity": sellable}
    log(logger, logging.INFO, "holding_checked", api_id=api_id, **result)
    return result


def _required_count(value: int | None, key: str, api_id: str) -> int:
    if value is None:
        raise response_format_error(api_id, f"{key} 값이 비어 있습니다.")
    return value


def _stock_list_unavailable() -> AppError:
    return AppError(
        "stock_list_unavailable",
        "종목 목록이 없어 미국 종목의 거래소를 정하지 못했습니다.",
        503,
    )


def _strip_prefix(code: str) -> str:
    """국내 종목번호는 접두어 1자리(A: 주식, J: ELW, Q: ETN) + 종목코드 6자리다."""
    return code[1:] if len(code) == 7 and code[0] in "AJQ" else code
