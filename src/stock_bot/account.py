"""계좌 확인: 키움 계좌 TR을 호출해 화면에 필요한 필드만 정리한다."""

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_bot.config import EnvironmentSpec, Market
from stock_bot.errors import AppError, response_format_error
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import log
from stock_bot.masking import mask_account_no, secrets

logger = logging.getLogger("stock_bot.account")

DOMESTIC_ACCOUNT_PATH = "/api/dostk/acnt"
US_ACCOUNT_PATH = "/api/us/acnt"


class AccountService:
    def __init__(self, kiwoom: KiwoomClient) -> None:
        self._kiwoom = kiwoom

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
        data = await self._kiwoom.call(spec, "ka00001", DOMESTIC_ACCOUNT_PATH, {})
        actual = _required(data, "acctNo", "ka00001")
        secrets.add(str(actual))
        if not _same_account(str(actual), expected):
            raise AppError(
                "account_mismatch",
                "조회된 계좌번호가 설정한 계좌번호와 다릅니다. 조회를 중단했습니다.",
                409,
                {"account_no": mask_account_no(str(actual))},
            )
        return str(actual)

    async def _domestic(self, spec: EnvironmentSpec) -> dict[str, Any]:
        balance = await self._kiwoom.call(
            spec, "kt00018", DOMESTIC_ACCOUNT_PATH, {"qry_tp": "1", "dmst_stex_tp": "KRX"}
        )
        deposit = await self._kiwoom.call(spec, "kt00001", DOMESTIC_ACCOUNT_PATH, {"qry_tp": "3"})
        b = _Reader(balance, "kt00018")
        d = _Reader(deposit, "kt00001")
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

    async def _us(self, spec: EnvironmentSpec) -> dict[str, Any]:
        balance = await self._kiwoom.call(
            spec, "ust21070", US_ACCOUNT_PATH, {"stex_tp": "", "stk_cd": ""}
        )
        deposit = await self._kiwoom.call(spec, "ust21110", US_ACCOUNT_PATH, {})
        b = _Reader(balance, "ust21070")
        d = _Reader(deposit, "ust21110")
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
                    "name": h.text("frgn_stk_nm"),
                    "exchange": h.text("stex_nm"),
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
                for h in b.rows("result_list")
            ],
        }


class _Reader:
    """키움 응답에서 필드를 꺼낸다. 필요한 필드가 없으면 응답 형식 오류로 처리한다."""

    def __init__(self, data: dict[str, Any], api_id: str) -> None:
        self._data = data
        self._api_id = api_id

    def text(self, key: str) -> str:
        return str(_required(self._data, key, self._api_id)).strip()

    def integer(self, key: str) -> int | None:
        value = self._number(key)
        return None if value is None else int(value)

    def decimal(self, key: str) -> float | None:
        value = self._number(key)
        return None if value is None else float(value)

    def rows(self, key: str) -> list[_Reader]:
        value = _required(self._data, key, self._api_id)
        if not isinstance(value, list):
            raise response_format_error(self._api_id, f"{key} 필드가 목록이 아닙니다.")
        return [_Reader(row, self._api_id) for row in value if isinstance(row, dict)]

    def _number(self, key: str) -> Decimal | None:
        """'-00000000196888', '156464.6701' 같은 문자열을 숫자로 바꾼다. 빈 값은 None."""
        raw = self.text(key)
        if raw == "":
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            raise response_format_error(self._api_id, f"{key} 값이 숫자가 아닙니다.") from None


def _required(data: dict[str, Any], key: str, api_id: str) -> Any:
    if key not in data or data[key] is None:
        raise response_format_error(api_id, f"{key} 필드가 없습니다.")
    return data[key]


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
