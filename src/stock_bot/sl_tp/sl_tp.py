"""SL / TP 설정: 자동매매 설정 파일의 sl_tp 칸. SL/TP 세션의 기준 칸(익절·손절 기준)을 검사한다."""

from decimal import Decimal
from typing import Any

from stock_bot.auto_trading.settings import Feature, Invalid, percent

# 익절·손절 기준(%)의 범위. 손절 기준은 양수로 두고 손실률을 뜻한다.
TAKE_PROFIT_RANGE = (Decimal("0.01"), Decimal("1000"))
STOP_LOSS_RANGE = (Decimal("0.01"), Decimal("99.99"))


def _criteria(item: dict[str, Any], enabled: bool, where: str) -> dict[str, Any]:
    take_profit = percent(item.get("take_profit"), TAKE_PROFIT_RANGE, f"{where}: 익절 기준")
    stop_loss = percent(item.get("stop_loss"), STOP_LOSS_RANGE, f"{where}: 손절 기준")
    if enabled and take_profit is None and stop_loss is None:
        raise Invalid(f"{where}: 켜진 세션에는 익절 기준이나 손절 기준이 하나 이상 있어야 합니다.")
    return {"take_profit": take_profit, "stop_loss": stop_loss}


SL_TP = Feature(key="sl_tp", name="SL / TP", session_name="SL/TP 세션", criteria=_criteria)
