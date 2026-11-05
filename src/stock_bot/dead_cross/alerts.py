"""Dead Cross Discord 알림 문구: 자동 매도 접수·실패·결과 확인 불가.

모양은 Trailing Stop 자동 매도 알림과 같다. 판정에 수익률을 쓰지 않아 수익률 칸이 없고
색은 모두 회색이다. 손익은 체결 알림에서 본다.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from stock_bot.core.config import EnvironmentSpec, Market
from stock_bot.core.notification import Embed, EmbedField
from stock_bot.dead_cross.bars import minutes
from stock_bot.dead_cross.record import Basis
from stock_bot.fill.watch import NEUTRAL_COLOR
from stock_bot.order.order import AutoSellResult
from stock_bot.sl_tp.alerts import UNKNOWN_RESULT, ZONE_LABELS, local_time

RETRY_NEXT_SESSION = "다음 Dead Cross 세션에 다시 시도합니다."


@dataclass(frozen=True)
class SellTrigger:
    """자동 매도를 낸 데드크로스 발생. 결과 알림에 담는다."""

    code: str
    name: str
    exchange: str | None  # 미국만
    crossed_bar: datetime
    basis: Basis
    # 판정한 가장 최근 완료 봉의 이동평균. 화면과 같은 자리수로 줄인 값이다.
    short_ma: Decimal
    long_ma: Decimal
    session: str  # HH:MM-HH:MM, 그 시장 시간대
    # 앞 실행에서 일어난 하향 돌파를 새 실행에서 다시 파는 것인가.
    retry: bool = False


def accepted_embed(spec: EnvironmentSpec, trigger: SellTrigger, result: AutoSellResult) -> Embed:
    return Embed(
        title="Dead Cross 자동 매도 접수",
        color=NEUTRAL_COLOR,
        fields=(
            *_trigger_fields(spec, trigger),
            EmbedField("주문 수량", f"{result.quantity:,}주", inline=True),
            EmbedField("주문번호", result.order_no or "-", inline=True),
        ),
    )


def failed_embed(spec: EnvironmentSpec, trigger: SellTrigger, result: AutoSellResult) -> Embed:
    return Embed(
        title="Dead Cross 자동 매도 실패",
        description=RETRY_NEXT_SESSION,
        color=NEUTRAL_COLOR,
        fields=(*_trigger_fields(spec, trigger), EmbedField("원인", result.cause or "확인 불가")),
    )


def unknown_embed(spec: EnvironmentSpec, trigger: SellTrigger, result: AutoSellResult) -> Embed:
    quantity = "확인 불가" if result.quantity is None else f"{result.quantity:,}주"
    return Embed(
        title="Dead Cross 자동 매도 결과 확인 불가",
        description=UNKNOWN_RESULT,
        color=NEUTRAL_COLOR,
        fields=(*_trigger_fields(spec, trigger), EmbedField("주문 수량", quantity, inline=True)),
    )


RESULT_EMBEDS = {"accepted": accepted_embed, "rejected": failed_embed, "unknown": unknown_embed}


def _basis_text(basis: Basis) -> str:
    count = minutes(basis.bar_period)
    period = "일봉" if count is None else f"{count}분봉"
    return f"{period} · 단기 {basis.short_period}봉 · 장기 {basis.long_period}봉"


def _average_text(market: Market, value: Decimal) -> str:
    return f"${value:,}" if market is Market.US else f"{value:,}원"


def _trigger_fields(spec: EnvironmentSpec, trigger: SellTrigger) -> tuple[EmbedField, ...]:
    stock = f"{trigger.name} ({trigger.code})"
    if trigger.exchange:
        stock = f"{trigger.name} ({trigger.code} · {trigger.exchange})"
    return (
        EmbedField("투자 환경", spec.label, inline=True),
        EmbedField("종목", stock, inline=True),
        EmbedField(
            "Dead Cross 세션",
            f"{trigger.session.replace('-', '–')} ({ZONE_LABELS[spec.market]})",
            inline=True,
        ),
        EmbedField("기준", _basis_text(trigger.basis), inline=True),
        EmbedField("단기 이동평균", _average_text(spec.market, trigger.short_ma), inline=True),
        EmbedField("장기 이동평균", _average_text(spec.market, trigger.long_ma), inline=True),
        EmbedField("발생 봉", local_time(spec, trigger.crossed_bar), inline=True),
        *((EmbedField("시도", "다시 시도", inline=True),) if trigger.retry else ()),
    )
