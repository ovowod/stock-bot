"""SL / TP Discord 알림 문구: 자동 매도 접수·실패·결과 확인 불가와 판정 중단.

색은 기존 알림 관례를 따른다. 이익(익절 트리거) 빨강, 손실(손절 트리거) 파랑,
실패·확인 불가·중단은 회색.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from stock_bot.config import EnvironmentSpec, Market
from stock_bot.fill_watch import LOSS_COLOR, NEUTRAL_COLOR, PRICE_FORMATS, PROFIT_COLOR
from stock_bot.notification import Embed, EmbedField
from stock_bot.order import AutoSellResult
from stock_bot.sl_tp import TIMEZONES

TRIGGER_LABELS = {"take_profit": "익절 트리거", "stop_loss": "손절 트리거"}
ZONE_LABELS = {Market.DOMESTIC: "한국시간", Market.US: "뉴욕시간"}
NO_RETRY = "이번 SL/TP 세션 동안 이 종목은 다시 시도하지 않습니다."
UNKNOWN_RESULT = "접수 여부를 확인할 수 없습니다. 키움에서 주문 내역을 확인하세요."


@dataclass(frozen=True)
class AutoSellTrigger:
    """트리거 하나. 자동 매도 결과 알림에 담는다."""

    code: str
    name: str
    exchange: str | None  # 미국만
    kind: str  # take_profit | stop_loss
    price: Decimal
    purchase_price: Decimal
    rate: Decimal  # %, 반올림하지 않은 값
    threshold: Decimal
    session: str  # HH:MM-HH:MM, 그 시장 시간대


def accepted_embed(
    spec: EnvironmentSpec, trigger: AutoSellTrigger, result: AutoSellResult
) -> Embed:
    color = PROFIT_COLOR if trigger.kind == "take_profit" else LOSS_COLOR
    return Embed(
        title="SL / TP 자동 매도 접수",
        color=color,
        fields=(
            *_trigger_fields(spec, trigger),
            EmbedField("주문 수량", f"{result.quantity:,}주", inline=True),
            EmbedField("주문번호", result.order_no or "-", inline=True),
        ),
    )


def failed_embed(spec: EnvironmentSpec, trigger: AutoSellTrigger, result: AutoSellResult) -> Embed:
    return Embed(
        title="SL / TP 자동 매도 실패",
        description=NO_RETRY,
        color=NEUTRAL_COLOR,
        fields=(
            *_trigger_fields(spec, trigger),
            EmbedField("원인", result.cause or "확인 불가"),
        ),
    )


def unknown_embed(spec: EnvironmentSpec, trigger: AutoSellTrigger, result: AutoSellResult) -> Embed:
    quantity = "확인 불가" if result.quantity is None else f"{result.quantity:,}주"
    return Embed(
        title="SL / TP 자동 매도 결과 확인 불가",
        description=UNKNOWN_RESULT,
        color=NEUTRAL_COLOR,
        fields=(
            *_trigger_fields(spec, trigger),
            EmbedField("주문 수량", quantity, inline=True),
        ),
    )


def stopped_embed(spec: EnvironmentSpec, description: str, *fields: EmbedField) -> Embed:
    """판정 중단: 보유종목 읽기 계속 실패, 구독 거부, 설정 파일 읽기 실패."""
    return Embed(
        title="SL / TP 판정 중단",
        description=description,
        color=NEUTRAL_COLOR,
        fields=(EmbedField("투자 환경", spec.label, inline=True), *fields),
    )


def local_time(spec: EnvironmentSpec, moment: datetime) -> str:
    """그 시장 시간대의 시각."""
    local = moment.astimezone(ZoneInfo(TIMEZONES[spec.market]))
    return f"{local:%Y-%m-%d %H:%M:%S} ({ZONE_LABELS[spec.market]})"


def _trigger_fields(spec: EnvironmentSpec, trigger: AutoSellTrigger) -> tuple[EmbedField, ...]:
    price = PRICE_FORMATS[spec.market]
    stock = f"{trigger.name} ({trigger.code})"
    if trigger.exchange:
        stock = f"{trigger.name} ({trigger.code} · {trigger.exchange})"
    sign = "+" if trigger.kind == "take_profit" else "-"
    return (
        EmbedField("투자 환경", spec.label, inline=True),
        EmbedField("종목", stock, inline=True),
        EmbedField(
            "SL/TP 세션",
            f"{trigger.session.replace('-', '–')} ({ZONE_LABELS[spec.market]})",
            inline=True,
        ),
        EmbedField(
            "트리거",
            f"{TRIGGER_LABELS[trigger.kind]} · 기준 {sign}{trigger.threshold}%",
            inline=True,
        ),
        EmbedField("트리거 체결가", price(trigger.price), inline=True),
        EmbedField("매입가", price(trigger.purchase_price), inline=True),
        # 판정은 반올림하지 않은 값으로 했다. 표시만 소수 둘째 자리로 줄인다.
        EmbedField("수익률", f"{_rate(trigger.rate)} · 수수료·세금 제외", inline=True),
    )


def _rate(rate: Decimal) -> str:
    shown = rate.quantize(Decimal("0.01"), ROUND_HALF_UP)
    return f"{'+' if shown > 0 else ''}{shown}%"
