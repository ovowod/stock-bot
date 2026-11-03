"""Dead Cross 차트: 키움 분봉·일봉 조회 요청을 만들고, 응답 행을 완료 봉으로 읽어 이동평균을 낸다.

모의투자 실측(2026-11, 명세에 없음)으로 정한 규칙이다.
- 행은 최신 봉부터 온다. 맨 앞 행은 아직 만들어지는 중인 봉일 수 있다.
- 분봉의 cntr_tm은 봉의 시작 시각이다. 미국은 뉴욕 시각이며, 다음 날 새벽을 24시 넘는
  시각으로 적는다.
- 국내 분봉 가격에는 전일 대비 부호(+/-)가 붙는다.
- 분봉에는 정규장 밖 봉도 섞인다. 키움 차트처럼 그대로 쓴다.
로그는 남기지 않는다. 쓰는 쪽이 자기 이벤트 이름으로 남긴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from stock_bot.auto_trading.settings import TIMEZONES
from stock_bot.core.config import Market
from stock_bot.kiwoom.us_exchange import US_EXCHANGE_CODES

DOMESTIC_PATH = "/api/dostk/chart"
US_PATH = "/api/us/chart"
DAILY = "1d"
# 분봉이 끝나는 시각을 시계로 셀 때의 기준. 국내는 장 시작, 미국은 키움 분봉이 시작하는 04:00이다.
# 120분봉이 04:00부터 2시간 간격인 것은 실측으로 확인했다.
BAR_ANCHOR = {Market.DOMESTIC: time(9, 0), Market.US: time(4, 0)}


@dataclass(frozen=True)
class Bar:
    # 봉의 시작 시각(그 시장 시간대). 일봉은 그날 0시다.
    start: datetime
    close: Decimal


@dataclass(frozen=True)
class ChartRequest:
    api_id: str
    path: str
    body: dict[str, str]
    list_key: str


def minutes(bar_period: str) -> int | None:
    """분봉이면 분 수, 일봉이면 None."""
    return None if bar_period == DAILY else int(bar_period.removesuffix("m"))


def chart_request(
    market: Market, code: str, exchange: str | None, bar_period: str, today: date
) -> ChartRequest:
    """today는 그 시장의 오늘이다. 오늘부터 과거로 받는다. 수정주가를 쓴다."""
    day = today.strftime("%Y%m%d")
    tic = minutes(bar_period)
    if market is Market.DOMESTIC:
        if tic is None:
            body = {"stk_cd": code, "base_dt": day, "upd_stkpc_tp": "1"}
            return ChartRequest("ka10081", DOMESTIC_PATH, body, "stk_dt_pole_chart_qry")
        body = {"stk_cd": code, "tic_scope": str(tic), "upd_stkpc_tp": "1", "base_dt": day}
        return ChartRequest("ka10080", DOMESTIC_PATH, body, "stk_min_pole_chart_qry")
    assert exchange is not None
    body = {
        "stex_tp": US_EXCHANGE_CODES[exchange],
        "stk_cd": code,
        "strt_dt": day,
        "upd_stkpc_tp": "1",
        "exrt_appl_tp": "0",
    }
    if tic is None:
        return ChartRequest("usa06012", US_PATH, body, "result_list")
    return ChartRequest("usa06011", US_PATH, {**body, "tic_scope": str(tic)}, "result_list")


def read_bars(market: Market, bar_period: str, rows: Sequence[Any]) -> list[Bar]:
    """응답 행을 최신 봉부터의 봉으로 읽는다. 읽을 수 없는 행이 있으면 ValueError를 낸다.

    같은 시작 시각의 행이 또 오면 앞(최신) 행만 쓴다.
    """
    zone = ZoneInfo(TIMEZONES[market])
    daily = bar_period == DAILY
    bars: list[Bar] = []
    seen: set[datetime] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("봉 행이 객체가 아닙니다")
        start = _read_day(row.get("dt"), zone) if daily else _read_time(row.get("cntr_tm"), zone)
        if start in seen:
            continue
        seen.add(start)
        bars.append(Bar(start, _read_close(row.get("cur_prc"))))
    return bars


def completed(bars: Sequence[Bar], bar_period: str, now: datetime) -> list[Bar]:
    """완료 봉만 남긴다.

    분봉은 시작 시각 + 봉 주기가 지났고, 일봉은 그 시장의 오늘보다 앞선 날이다.
    """
    tic = minutes(bar_period)
    if not bars:
        return []
    local = now.astimezone(bars[0].start.tzinfo)
    if tic is None:
        return [bar for bar in bars if bar.start.date() < local.date()]
    return [bar for bar in bars if bar.start + timedelta(minutes=tic) <= local]


def moving_average(bars: Sequence[Bar], index: int, count: int) -> Decimal:
    """index 봉(0이 최신)까지의 완료 봉 count개 종가의 단순 평균. 봉이 모자라면 부르지 않는다."""
    window = bars[index : index + count]
    assert len(window) == count
    return sum((bar.close for bar in window), Decimal(0)) / count


def next_bar_end(bar_period: str, market: Market, now: datetime) -> datetime | None:
    """now 뒤에 처음 끝나는 분봉의 끝 시각. 일봉은 None.

    시장의 기준 시각부터 봉 주기 간격으로 센다.
    """
    tic = minutes(bar_period)
    if tic is None:
        return None
    zone = ZoneInfo(TIMEZONES[market])
    local = now.astimezone(zone)
    anchor = datetime.combine(local.date(), BAR_ANCHOR[market], zone)
    step = timedelta(minutes=tic)
    if local < anchor:
        # 기준 시각 앞(국내 새벽 등)은 전날 기준에서 이어 센다.
        anchor -= timedelta(days=1)
    passed = (local - anchor) // step
    return anchor + step * (passed + 1)


def _read_time(value: Any, zone: ZoneInfo) -> datetime:
    """YYYYMMDDHHmmss. 시가 24 이상이면 다음 날로 넘긴다(미국 야간 봉)."""
    text = str(value or "")
    if len(text) != 14 or not text.isdigit():
        raise ValueError(f"cntr_tm을 읽을 수 없습니다: {text[:20]}")
    day = datetime.strptime(text[:8], "%Y%m%d").date()
    hour, minute, second = int(text[8:10]), int(text[10:12]), int(text[12:14])
    if minute > 59 or second > 59 or hour > 47:
        raise ValueError(f"cntr_tm을 읽을 수 없습니다: {text}")
    base = datetime.combine(day, time(0), zone)
    return base + timedelta(hours=hour, minutes=minute, seconds=second)


def _read_day(value: Any, zone: ZoneInfo) -> datetime:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"dt를 읽을 수 없습니다: {text[:20]}")
    return datetime.combine(datetime.strptime(text, "%Y%m%d").date(), time(0), zone)


def _read_close(value: Any) -> Decimal:
    """국내 분봉의 부호(+/-)는 전일 대비 표시라 뗀다."""
    text = str(value or "").strip().lstrip("+-")
    try:
        price = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"종가를 읽을 수 없습니다: {text[:20]}") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError(f"종가가 양수가 아닙니다: {text[:20]}")
    return price
