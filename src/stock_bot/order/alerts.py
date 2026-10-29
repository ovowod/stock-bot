"""주문 취소의 Discord 알림 내용."""

from datetime import datetime
from typing import Any

from stock_bot.core.config import EnvironmentSpec
from stock_bot.core.notification import Embed, EmbedField, format_kst
from stock_bot.kiwoom.kiwoom import KST
from stock_bot.order.open_order import OpenOrder

_CANCEL_COLOR = 0x8B95A1


def cancel_embed(spec: EnvironmentSpec, order: OpenOrder, result: dict[str, Any]) -> Embed:
    """접수된 주문 취소의 Discord 알림."""
    quantity = result["cancel_quantity"]
    return Embed(
        title="주문 취소 접수",
        color=_CANCEL_COLOR,
        fields=(
            EmbedField("투자 환경", spec.label, inline=True),
            EmbedField("종목", f"{order.name} ({order.code})", inline=True),
            EmbedField("원래 주문", order.side_label, inline=True),
            # 잔량 전부로 보내 키움이 수량을 확정해 주지 않으면 null이다.
            EmbedField(
                "취소 수량",
                "남은 수량 전부" if quantity is None else f"{quantity:,}주",
                inline=True,
            ),
            EmbedField(
                "주문번호", f"{result['original_order_no']} → {result['order_no']}", inline=True
            ),
            EmbedField("시각", format_kst(datetime.now(KST)), inline=True),
        ),
    )
