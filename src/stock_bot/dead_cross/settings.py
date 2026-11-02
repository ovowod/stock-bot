"""Dead Cross 설정: 자동매매 설정 파일의 dead_cross 칸.

Dead Cross 세션의 기준 칸(봉 주기, 단기·장기 이동평균 기간)을 검사한다.
매도 동작은 아직 없다. 설정만 저장한다.
"""

from typing import Any

from stock_bot.auto_trading.settings import Feature, Invalid
from stock_bot.core.config import Market

# 키움 차트가 주는 분봉 단위(국내 ka10080, 미국 usa06011의 tic_scope)와 일봉(ka10081, usa06012).
BAR_PERIODS = {
    Market.DOMESTIC: ("1m", "3m", "5m", "10m", "15m", "30m", "45m", "60m", "1d"),
    Market.US: ("1m", "3m", "5m", "10m", "15m", "30m", "40m", "60m", "120m", "1d"),
}
# 봉 개수. 키움 명세에 조회 한도가 없어 앱이 정한 값이다. 매도 단계에서 모의투자로 재서 조정한다.
SHORT_PERIOD_RANGE = (2, 119)
LONG_PERIOD_RANGE = (3, 120)


def _criteria(item: dict[str, Any], enabled: bool, where: str, market: Market) -> dict[str, Any]:
    bar_period = item.get("bar_period")
    if bar_period is not None and bar_period not in BAR_PERIODS[market]:
        raise Invalid(f"{where}: 봉 주기가 이 시장에서 고를 수 있는 값이 아닙니다.")
    short_period = _bar_count(
        item.get("short_period"), SHORT_PERIOD_RANGE, f"{where}: 단기 이동평균 기간"
    )
    long_period = _bar_count(
        item.get("long_period"), LONG_PERIOD_RANGE, f"{where}: 장기 이동평균 기간"
    )
    # 켜짐 여부와 관계없이 비교한다. 단기가 장기 이상이면 하향 돌파가 뜻을 잃는다.
    if short_period is not None and long_period is not None and short_period >= long_period:
        raise Invalid(f"{where}: 단기 이동평균 기간은 장기 이동평균 기간보다 작아야 합니다.")
    if enabled and None in (bar_period, short_period, long_period):
        raise Invalid(
            f"{where}: 켜진 세션에는 봉 주기와 단기·장기 이동평균 기간이 모두 있어야 합니다."
        )
    return {"bar_period": bar_period, "short_period": short_period, "long_period": long_period}


def _bar_count(value: Any, bounds: tuple[int, int], name: str) -> int | None:
    """정하지 않았으면 None. JSON의 true/false는 파이썬에서 int이므로 따로 거른다."""
    if value is None:
        return None
    low, high = bounds
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise Invalid(f"{name}은 {low}~{high} 사이의 정수여야 합니다.")
    return value


DEAD_CROSS = Feature(
    key="dead_cross", name="Dead Cross", session_name="Dead Cross 세션", criteria=_criteria
)
