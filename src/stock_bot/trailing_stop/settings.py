"""Trailing Stop 설정: 자동매매 설정 파일의 trailing_stop 칸.

Trailing Stop 세션의 기준 칸(활성화 수익률, 고점 대비 하락률)을 검사한다.
매도 동작은 아직 없다. 설정만 저장한다.
"""

from decimal import Decimal
from typing import Any

from stock_bot.auto_trading.settings import Feature, Invalid, percent
from stock_bot.core.config import Market

# 두 값(%) 모두 양수로 둔다. 고점 대비 하락률 3은 고점보다 3% 낮은 가격을 뜻한다.
ACTIVATION_PROFIT_RANGE = (Decimal("0.01"), Decimal("1000"))
DROP_FROM_PEAK_RANGE = (Decimal("0.01"), Decimal("99.99"))


def _criteria(item: dict[str, Any], enabled: bool, where: str, market: Market) -> dict[str, Any]:
    """두 값의 크기는 서로 비교하지 않는다. 하락률이 더 크면 손실로 팔릴 수 있지만 받는다."""
    activation_profit = percent(
        item.get("activation_profit"), ACTIVATION_PROFIT_RANGE, f"{where}: 활성화 수익률"
    )
    drop_from_peak = percent(
        item.get("drop_from_peak"), DROP_FROM_PEAK_RANGE, f"{where}: 고점 대비 하락률"
    )
    if enabled and (activation_profit is None or drop_from_peak is None):
        raise Invalid(
            f"{where}: 켜진 세션에는 활성화 수익률과 고점 대비 하락률이 모두 있어야 합니다."
        )
    return {"activation_profit": activation_profit, "drop_from_peak": drop_from_peak}


TRAILING_STOP = Feature(
    key="trailing_stop", name="Trailing Stop", session_name="Trailing Stop 세션", criteria=_criteria
)
