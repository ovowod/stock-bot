"""계좌 확인: 키움 계좌 TR을 호출해 화면에 필요한 필드만 정리한다."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from stock_bot.config import Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError, response_format_error
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.masking import mask_account_no, secrets
from stock_bot.reader import Reader, required

logger = logging.getLogger("stock_bot.account")

# 잔고 TR(ust21070)은 거래소를 한글명으로 준다. 순위·검색과 같은 영문명으로 바꾸고,
# 모르는 이름은 그대로 둔다. 이름은 모의 서버 응답에서 확인했다.
# 시세 데이터가 NYSE로 주는 NYSE Arca ETF(SPY 등)를 잔고는 아멕스로 준다.
US_EXCHANGE_NAMES = {"뉴욕": "NYSE", "나스닥": "NASDAQ", "아멕스": "AMEX"}

DOMESTIC_ACCOUNT_PATH = "/api/dostk/acnt"
US_ACCOUNT_PATH = "/api/us/acnt"


class _HoldingReader(Reader):
    """보유종목 숫자 칸 하나가 깨져도 계좌 화면 전체를 실패시키지 않고 그 칸만 비운다.

    모의 서버가 장중에 pl_amt를 '. 950'처럼 깨진 값으로 보낸 적이 있다.
    뜻을 알 수 없는 값은 추측하지 않는다. 필드가 아예 없으면 지금처럼 응답 형식 오류다.
    """

    def number(self, key: str) -> Decimal | None:
        raw = self.text(key)
        try:
            return super().number(key)
        except AppError:
            log(logger, logging.WARNING, "holding_value_unreadable", key=key, raw=raw[:40])
            return None


StockListings = Callable[[EnvironmentSpec], Awaitable[dict[str, dict[str, Any]]]]


class AccountService:
    def __init__(self, kiwoom: KiwoomClient, listings: StockListings | None = None) -> None:
        # 잔고 TR은 미국 ETF를 영문명으로, NYSE Arca ETF를 아멕스로 준다.
        # 순위·검색과 같은 이름과 거래소를 쓰려고 종목 목록에서 찾는다.
        self._listings = listings
        self._kiwoom = kiwoom
        # 투자 환경 -> (확인에 쓴 토큰, 확인한 계좌번호). 어느 계좌를 조회할지는 토큰이 정하므로
        # 같은 토큰을 쓰는 동안은 다시 확인하지 않는다.
        self._verified: dict[Environment, tuple[str, str]] = {}
        # 자동 매도용 잔고 조회 묶음. 다음에 읽을 묶음(그 결과를 기다리는 Future)과 기다리는 수.
        self._next_balance: dict[Environment, asyncio.Future[dict[str, Any]]] = {}
        self._balance_waiters: dict[Environment, int] = {}
        self._reading_balance: set[Environment] = set()
        self._balance_tasks: set[asyncio.Task[None]] = set()

    async def fetch(self, spec: EnvironmentSpec) -> dict[str, Any]:
        account_no = await self._verified_account_no(spec)
        result: dict[str, Any] = {
            "environment": spec.environment.value,
            "market": spec.market.value,
            "account_no": mask_account_no(account_no),
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if spec.market is Market.DOMESTIC:
            result.update(await self._domestic(spec))
        else:
            result.update(await self._us(spec))
        log(
            logger,
            logging.INFO,
            "account_fetched",
            market=spec.market.value,
            holdings=len(result["holdings"]),
        )
        return result

    async def _verified_account_no(self, spec: EnvironmentSpec) -> str:
        """토큰이 가리키는 계좌가 .env의 계좌번호와 같을 때만 진행한다."""
        expected = self._kiwoom.credentials(spec).account_no
        # 확인 도중 토큰이 바뀌면 다음 요청이 새 토큰으로 한 번 더 확인한다.
        token = await self._kiwoom.access_token(spec)
        verified = self._verified.get(spec.environment)
        if verified is not None and verified[0] == token:
            log(logger, logging.INFO, "account_check_skipped", api_id="ka00001")
            return verified[1]
        data = await self._kiwoom.call(spec, "ka00001", DOMESTIC_ACCOUNT_PATH, {})
        actual = required(data, "acctNo", "ka00001")
        secrets.add(str(actual))
        if not _same_account(str(actual), expected):
            raise AppError(
                "account_mismatch",
                "조회된 계좌번호가 설정한 계좌번호와 다릅니다. 조회를 중단했습니다.",
                409,
                {"account_no": mask_account_no(str(actual))},
            )
        self._verified[spec.environment] = (token, str(actual))
        return str(actual)

    async def _domestic(self, spec: EnvironmentSpec) -> dict[str, Any]:
        # 서로 다른 TR은 호출 간격을 기다리지 않으므로 함께 보낸다.
        balance, deposit = await asyncio.gather(
            self._kiwoom.call(
                spec, "kt00018", DOMESTIC_ACCOUNT_PATH, {"qry_tp": "1", "dmst_stex_tp": "KRX"}
            ),
            self._kiwoom.call(spec, "kt00001", DOMESTIC_ACCOUNT_PATH, {"qry_tp": "3"}),
        )
        b = Reader(balance, "kt00018")
        d = Reader(deposit, "kt00001")
        return {
            "summary": {
                "estimated_assets": b.integer("prsm_dpst_aset_amt"),
                "total_evaluation": b.integer("tot_evlt_amt"),
                "total_purchase": b.integer("tot_pur_amt"),
                "total_profit_loss": b.integer("tot_evlt_pl"),
                "total_return_rate": b.decimal("tot_prft_rt"),
            },
            "deposit": {
                "deposit": d.integer("entr"),
                "orderable": d.integer("ord_alow_amt"),
                "withdrawable": d.integer("pymn_alow_amt"),
                "d1_estimated": d.integer("d1_entra"),
                "d2_estimated": d.integer("d2_entra"),
            },
            "holdings": [
                {
                    "code": _strip_prefix(h.text("stk_cd")),
                    "name": h.text("stk_nm"),
                    "quantity": h.integer("rmnd_qty"),
                    "tradable_quantity": h.integer("trde_able_qty"),
                    "purchase_price": h.integer("pur_pric"),
                    "current_price": h.integer("cur_prc"),
                    "purchase_amount": h.integer("pur_amt"),
                    "evaluation_amount": h.integer("evlt_amt"),
                    "profit_loss": h.integer("evltv_prft"),
                    "return_rate": h.decimal("prft_rt"),
                    "weight": h.decimal("poss_rt"),
                }
                for h in b.rows("acnt_evlt_remn_indv_tot")
            ],
        }

    async def holding(self, spec: EnvironmentSpec, code: str) -> dict[str, Any]:
        """한 종목의 보유수량과 매도 가능 수량. 매도 주문 직전 확인에 쓴다.

        보유하지 않으면 두 수량이 0이다. 수량을 읽지 못하면 추측하지 않고 오류로 돌려준다.
        """
        return _holding(spec, code, await self._balance(spec))

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
                    data = await self._balance(spec, patient=True)
                except Exception as error:
                    batch.set_exception(error)
                    log(logger, logging.WARNING, "balance_shared_failed", waiters=waiters)
                    continue
                batch.set_result(data)
                log(logger, logging.INFO, "balance_shared", waiters=waiters)
        finally:
            self._reading_balance.discard(environment)

    async def _balance(self, spec: EnvironmentSpec, patient: bool = False) -> dict[str, Any]:
        if spec.market is Market.DOMESTIC:
            return await self._kiwoom.call(
                spec,
                "kt00018",
                DOMESTIC_ACCOUNT_PATH,
                {"qry_tp": "1", "dmst_stex_tp": "KRX"},
                patient=patient,
            )
        # 종목코드를 넣으면 거래소도 넣어야 한다(모의 서버 1517).
        # 그런데 잔고의 거래소 표기가 주문과 다를 수 있어(NYSE Arca ETF를 아멕스로 준다)
        # 계좌 확인처럼 전체를 받아 티커로 찾는다.
        return await self._kiwoom.call(
            spec, "ust21070", US_ACCOUNT_PATH, {"stex_tp": "", "stk_cd": ""}, patient=patient
        )

    async def positions(self, spec: EnvironmentSpec) -> list[dict[str, Any]]:
        """SL / TP가 지켜볼 보유종목 줄. 매입가를 읽지 못한 칸은 None이다.

        미국은 종목 목록의 거래소를 붙이고, 목록에 없으면 None이다.
        종목 목록을 받지 못하면 오류다. 거래소 없이 모든 종목을 조용히 빼지 않게 한다.
        """
        if spec.market is Market.DOMESTIC:
            data = await self._kiwoom.call(
                spec, "kt00018", DOMESTIC_ACCOUNT_PATH, {"qry_tp": "1", "dmst_stex_tp": "KRX"}
            )
            rows = [
                {
                    "code": _strip_prefix(row.text("stk_cd")),
                    "name": row.text("stk_nm"),
                    "purchase_price": row.number("pur_pric"),
                    # 현금 매도(kt10001)로 팔 수 있는 현금잔고(crd_tp=00)만 SL / TP가 다룬다.
                    "credit": row.text("crd_tp") != "00",
                    "exchange": None,
                }
                for row in Reader(data, "kt00018").rows("acnt_evlt_remn_indv_tot", _HoldingReader)
            ]
            api_id = "kt00018"
        else:
            if self._listings is None:
                raise AppError(
                    "stock_list_unavailable",
                    "종목 목록이 없어 미국 종목의 거래소를 정하지 못했습니다.",
                    503,
                )
            balance, listings = await asyncio.gather(
                self._kiwoom.call(spec, "ust21070", US_ACCOUNT_PATH, {"stex_tp": "", "stk_cd": ""}),
                self._listings(spec),
            )
            rows = [
                {
                    "code": row.text("stk_cd"),
                    # 화면과 같은 이름을 쓰도록 종목 목록의 이름을 먼저 쓴다.
                    "name": listings.get(row.text("stk_cd"), {}).get("name")
                    or row.text("frgn_stk_nm"),
                    # 2026-10-25 모의 계좌 25종목에서 매입금액 ÷ 보유수량과 같음을 확인했다.
                    "purchase_price": row.number("frgn_stk_book_uv"),
                    "credit": False,
                    "exchange": listings.get(row.text("stk_cd"), {}).get("exchange"),
                }
                for row in Reader(balance, "ust21070").rows("result_list", _HoldingReader)
            ]
            api_id = "ust21070"
        log(logger, logging.INFO, "positions_fetched", api_id=api_id, count=len(rows))
        return rows

    async def _stock_listings(self, spec: EnvironmentSpec) -> dict[str, dict[str, Any]]:
        """종목 목록을 받지 못해도 계좌 확인은 계속하고, 잔고 TR의 이름·거래소를 쓴다."""
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
            )
            return {}

    async def _us(self, spec: EnvironmentSpec) -> dict[str, Any]:
        # 서로 다른 TR은 호출 간격을 기다리지 않으므로 함께 보낸다.
        balance, deposit, listings = await asyncio.gather(
            self._kiwoom.call(spec, "ust21070", US_ACCOUNT_PATH, {"stex_tp": "", "stk_cd": ""}),
            self._kiwoom.call(spec, "ust21110", US_ACCOUNT_PATH, {}),
            self._stock_listings(spec),
        )
        b = Reader(balance, "ust21070")
        d = Reader(deposit, "ust21110")
        return {
            "currency": b.text("crnc_code"),
            "summary": {
                "total_evaluation": b.decimal("tot_evlt_amt"),
                "total_purchase": b.decimal("tot_prch_amt"),
                "total_profit_loss": b.decimal("tot_pl_amt"),
                "total_return_rate": b.decimal("tot_pl_rt"),
                "today_realized_profit_loss": b.decimal("tdy_pl_amt"),
                "today_realized_return_rate": b.decimal("tdy_pl_rt"),
            },
            "summary_krw": {
                "total_evaluation": b.integer("tot_evlt_amt_krw"),
                "total_purchase": b.integer("tot_prch_amt_krw"),
                "total_profit_loss": b.integer("tot_pl_amt_krw"),
                "today_realized_profit_loss": b.integer("tdy_pl_amt_krw"),
            },
            "deposit": {
                "krw_deposit": d.integer("krw_entra"),
                "currencies": [
                    {
                        "currency": c.text("crnc_code"),
                        "currency_name": c.text("crnc_nm"),
                        "deposit": c.decimal("fc_entra"),
                        "orderable": c.decimal("fc_ord_alowa"),
                        "withdrawable": c.decimal("fc_pymn_alowa"),
                    }
                    for c in d.rows("result_list")
                ],
            },
            "holdings": [
                {
                    "code": h.text("stk_cd"),
                    "name": listings.get(h.text("stk_cd"), {}).get("name") or h.text("frgn_stk_nm"),
                    "exchange": listings.get(h.text("stk_cd"), {}).get("exchange")
                    or US_EXCHANGE_NAMES.get(h.text("stex_nm"), h.text("stex_nm")),
                    "currency": h.text("crnc_code"),
                    "quantity": h.integer("poss_qty"),
                    "sellable_quantity": h.integer("sell_alowq"),
                    "purchase_price": h.decimal("frgn_stk_book_uv"),
                    "current_price": h.decimal("now_pric"),
                    "purchase_amount": h.decimal("frgn_stk_book_amt"),
                    "evaluation_amount": h.decimal("evlt_amt"),
                    "profit_loss": h.decimal("pl_amt"),
                    "return_rate": h.decimal("pl_rt"),
                    "evaluation_amount_krw": h.integer("evlt_amt_krw"),
                    "profit_loss_krw": h.integer("pl_amt_krw"),
                }
                for h in b.rows("result_list", _HoldingReader)
            ],
        }


def _holding(spec: EnvironmentSpec, code: str, data: dict[str, Any]) -> dict[str, Any]:
    """잔고 응답에서 한 종목의 보유수량과 매도 가능 수량을 고른다."""
    if spec.market is Market.DOMESTIC:
        api_id, quantity_key, sellable_key = "kt00018", "rmnd_qty", "trde_able_qty"
        # 현금 매도(kt10001) 대상인 현금잔고(crd_tp=00) 줄만 센다. 신용 보유분은 세지 않는다.
        rows = [
            row
            for row in Reader(data, api_id).rows("acnt_evlt_remn_indv_tot")
            if _strip_prefix(row.text("stk_cd")) == code and row.text("crd_tp") == "00"
        ]
    else:
        api_id, quantity_key, sellable_key = "ust21070", "poss_qty", "sell_alowq"
        rows = [
            row for row in Reader(data, api_id).rows("result_list") if row.text("stk_cd") == code
        ]
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
        quantity = _required_count(rows[0], quantity_key, api_id)
        sellable = _required_count(rows[0], sellable_key, api_id)
    result = {"code": code, "held": bool(rows), "quantity": quantity, "sellable_quantity": sellable}
    log(logger, logging.INFO, "holding_checked", api_id=api_id, **result)
    return result


def _required_count(row: Reader, key: str, api_id: str) -> int:
    value = row.integer(key)
    if value is None:
        raise response_format_error(api_id, f"{key} 값이 비어 있습니다.")
    return value


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _same_account(actual: str, expected: str) -> bool:
    """ka00001은 10자리를 돌려주고, 뒤 2자리는 키움의 계좌 분류값이다.
    설정이 8자리면 앞 8자리만, 그 밖에는 전체를 비교한다."""
    actual_digits, expected_digits = _digits(actual), _digits(expected)
    if len(expected_digits) == 8 and len(actual_digits) == 10:
        return actual_digits[:8] == expected_digits
    return actual_digits == expected_digits


def _strip_prefix(code: str) -> str:
    """국내 종목번호는 접두어 1자리(A: 주식, J: ELW, Q: ETN) + 종목코드 6자리다."""
    return code[1:] if len(code) == 7 and code[0] in "AJQ" else code
