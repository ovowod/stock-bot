"""계좌 확인: 키움 계좌 TR을 호출해 화면에 필요한 필드만 정리한다."""

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from stock_bot.config import Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.holdings import DOMESTIC_ACCOUNT_PATH, US_ACCOUNT_PATH, Holdings
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.masking import mask_account_no, secrets
from stock_bot.reader import Reader, required

logger = logging.getLogger("stock_bot.account")


class AccountService:
    def __init__(self, kiwoom: KiwoomClient, holdings: Holdings) -> None:
        self._kiwoom = kiwoom
        self._holdings = holdings
        # 투자 환경 -> (확인에 쓴 토큰, 확인한 계좌번호). 어느 계좌를 조회할지는 토큰이 정하므로
        # 같은 토큰을 쓰는 동안은 다시 확인하지 않는다.
        self._verified: dict[Environment, tuple[str, str]] = {}

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
        snapshot, deposit = await asyncio.gather(
            self._holdings.read(spec),
            self._kiwoom.call(spec, "kt00001", DOMESTIC_ACCOUNT_PATH, {"qry_tp": "3"}),
        )
        b = Reader(snapshot.response, "kt00018")
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
                    "code": h.code,
                    "name": h.name,
                    "quantity": h.quantity,
                    "tradable_quantity": h.sellable_quantity,
                    "purchase_price": _integer(h.purchase_price),
                    "current_price": _integer(h.current_price),
                    "purchase_amount": _integer(h.purchase_amount),
                    "evaluation_amount": _integer(h.evaluation_amount),
                    "profit_loss": _integer(h.profit_loss),
                    "return_rate": _float(h.return_rate),
                    "weight": _float(h.weight),
                }
                for h in snapshot.rows
            ],
        }

    async def _us(self, spec: EnvironmentSpec) -> dict[str, Any]:
        # 서로 다른 TR은 호출 간격을 기다리지 않으므로 함께 보낸다.
        snapshot, deposit = await asyncio.gather(
            self._holdings.read(spec),
            self._kiwoom.call(spec, "ust21110", US_ACCOUNT_PATH, {}),
        )
        b = Reader(snapshot.response, "ust21070")
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
                    "code": h.code,
                    "name": h.name,
                    # 종목 목록에 없으면 None이다. 화면은 "거래소 확인 불가"로 보이고 매도를 막는다.
                    "exchange": h.exchange,
                    "currency": h.currency,
                    "quantity": h.quantity,
                    "sellable_quantity": h.sellable_quantity,
                    "purchase_price": _float(h.purchase_price),
                    "current_price": _float(h.current_price),
                    "purchase_amount": _float(h.purchase_amount),
                    "evaluation_amount": _float(h.evaluation_amount),
                    "profit_loss": _float(h.profit_loss),
                    "return_rate": _float(h.return_rate),
                    "evaluation_amount_krw": _integer(h.evaluation_amount_krw),
                    "profit_loss_krw": _integer(h.profit_loss_krw),
                }
                for h in snapshot.rows
            ],
        }


def _integer(value: Decimal | None) -> int | None:
    return None if value is None else int(value)


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _same_account(actual: str, expected: str) -> bool:
    """ka00001은 10자리를 돌려주고, 뒤 2자리는 키움의 계좌 분류값이다.
    설정이 8자리면 앞 8자리만, 그 밖에는 전체를 비교한다."""
    actual_digits, expected_digits = _digits(actual), _digits(expected)
    if len(expected_digits) == 8 and len(actual_digits) == 10:
        return actual_digits[:8] == expected_digits
    return actual_digits == expected_digits
