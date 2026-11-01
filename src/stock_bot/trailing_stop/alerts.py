"""Trailing Stop Discord 알림 문구: 자동 매도 접수·실패·결과 확인 불가.

모양은 SL / TP 자동 매도 알림과 같다. 접수는 수익률이 0 이상이면 이익 색(빨강),
음수면 손실 색(파랑)이고, 실패·확인 불가는 회색이다.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from stock_bot.core.config import EnvironmentSpec
from stock_bot.core.notification import Embed, EmbedField
from stock_bot.fill.watch import LOSS_COLOR, NEUTRAL_COLOR, PRICE_FORMATS, PROFIT_COLOR
from stock_bot.order.order import AutoSellResult
from stock_bot.sl_tp.alerts import UNKNOWN_RESULT, ZONE_LABELS, format_rate, local_time

RETRY_NEXT_SESSION = "다음 Trailing Stop 세션에 다시 시도합니다."


@dataclass(frozen=True)
class SellTrigger:
    """자동 매도를 낸 트리거. 결과 알림에 담는다."""

    code: str
    name: str
    exchange: str | None  # 미국만
    price: Decimal  # 트리거 체결가
    triggered_at: datetime
    peak: Decimal
    purchase_price: Decimal
    activation_profit: Decimal
    drop_from_peak: Decimal
    session: str  # HH:MM-HH:MM, 그 시장 시간대
    # 앞 실행에서 트리거된 종목을 새 실행에서 다시 파는 것인가.
    retry: bool = False

    @property
    def rate(self) -> Decimal:
        """평균 매입가 대비 수익률(%). 수수료·세금은 빼지 않는다."""
        return (self.price - self.purchase_price) / self.purchase_price * 100

    @property
    def drop(self) -> Decimal:
        """고점 대비 트리거 체결가의 변화율(%). 0 이하다."""
        return (self.price - self.peak) / self.peak * 100


def accepted_embed(spec: EnvironmentSpec, trigger: SellTrigger, result: AutoSellResult) -> Embed:
    return Embed(
        title="Trailing Stop 자동 매도 접수",
        color=PROFIT_COLOR if trigger.rate >= 0 else LOSS_COLOR,
        fields=(
            *_trigger_fields(spec, trigger),
            EmbedField("주문 수량", f"{result.quantity:,}주", inline=True),
            EmbedField("주문번호", result.order_no or "-", inline=True),
        ),
    )


def failed_embed(spec: EnvironmentSpec, trigger: SellTrigger, result: AutoSellResult) -> Embed:
    return Embed(
        title="Trailing Stop 자동 매도 실패",
        description=RETRY_NEXT_SESSION,
        color=NEUTRAL_COLOR,
        fields=(*_trigger_fields(spec, trigger), EmbedField("원인", result.cause or "확인 불가")),
    )


def unknown_embed(spec: EnvironmentSpec, trigger: SellTrigger, result: AutoSellResult) -> Embed:
    quantity = "확인 불가" if result.quantity is None else f"{result.quantity:,}주"
    return Embed(
        title="Trailing Stop 자동 매도 결과 확인 불가",
        description=UNKNOWN_RESULT,
        color=NEUTRAL_COLOR,
        fields=(*_trigger_fields(spec, trigger), EmbedField("주문 수량", quantity, inline=True)),
    )


RESULT_EMBEDS = {"accepted": accepted_embed, "rejected": failed_embed, "unknown": unknown_embed}


def _trigger_fields(spec: EnvironmentSpec, trigger: SellTrigger) -> tuple[EmbedField, ...]:
    price = PRICE_FORMATS[spec.market]
    stock = f"{trigger.name} ({trigger.code})"
    if trigger.exchange:
        stock = f"{trigger.name} ({trigger.code} · {trigger.exchange})"
    return (
        EmbedField("투자 환경", spec.label, inline=True),
        EmbedField("종목", stock, inline=True),
        EmbedField(
            "Trailing Stop 세션",
            f"{trigger.session.replace('-', '–')} ({ZONE_LABELS[spec.market]})",
            inline=True,
        ),
        EmbedField("트리거 체결가", price(trigger.price), inline=True),
        EmbedField("고점", price(trigger.peak), inline=True),
        # 판정은 반올림하지 않은 값으로 했다. 표시만 소수 둘째 자리로 줄인다.
        EmbedField(
            "고점 대비",
            f"{format_rate(trigger.drop)} · 기준 -{trigger.drop_from_peak}%",
            inline=True,
        ),
        EmbedField("활성화 수익률", f"기준 +{trigger.activation_profit}%", inline=True),
        EmbedField("매입가", price(trigger.purchase_price), inline=True),
        EmbedField("수익률", f"{format_rate(trigger.rate)} · 수수료·세금 제외", inline=True),
        # 다시 시도는 앞 실행의 트리거라, 언제 트리거됐는지 함께 보인다.
        *(
            (
                EmbedField("시도", "다시 시도", inline=True),
                EmbedField("트리거 시각", local_time(spec, trigger.triggered_at), inline=True),
            )
            if trigger.retry
            else ()
        ),
    )
