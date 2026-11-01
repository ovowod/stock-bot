"""Trailing Stop 고점 기록 파일: 투자 환경마다 JSON 파일 하나에 종목마다의 기억을 둔다.

자동매매 설정 파일과 따로 둔다. 설정은 사용자가 저장할 때만 바뀌지만 고점 기록은 장중에 계속 바뀐다.
파일은 서버가 시작할 때와 읽지 못한 상태에서 복구할 때만 읽는다. 그 밖에는 감시의 메모리가 기준이다.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from stock_bot.auto_trading.settings import write_atomic

FORMAT_VERSION = 2
# 상태만 계산하던 때의 형식. 평균 매입가가 없어, 읽으면 기억을 모두 버린다.
LEGACY_VERSION = 1


@dataclass
class Memory:
    """종목 하나의 기억. 보유하는 동안 세션과 날짜를 넘겨 이어 간다.

    시각은 시세 체결을 받은 시각이다.
    """

    activated_at: datetime
    peak: Decimal
    peak_at: datetime
    # 마지막으로 확인한 평균 매입가. 앱이 체결을 받지 못한 사이 바뀌었는지 보려고 둔다.
    purchase_price: Decimal
    triggered_at: datetime | None = None
    triggered_price: Decimal | None = None


@dataclass(frozen=True)
class Loaded:
    memory: dict[str, Memory]
    # 버전 1 파일이었으면 버린 종목코드. 아니면 None이다.
    cleared_codes: list[str] | None = None


def load(path: Path) -> Loaded:
    """파일이 없으면 빈 기록이다. 읽지 못하면 OSError, 형식이 맞지 않으면 ValueError를 낸다.

    버전 1 파일은 기억을 모두 버린 빈 기록으로 읽는다. 다시 쓰는 것은 부르는 쪽이 한다.
    """
    if not path.exists():
        return Loaded({})
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version") if isinstance(data, dict) else None
    if version not in (FORMAT_VERSION, LEGACY_VERSION):
        raise ValueError(f"version이 {FORMAT_VERSION}이 아닙니다")
    stocks = data.get("stocks")
    if not isinstance(stocks, dict):
        raise ValueError("stocks가 객체가 아닙니다")
    if version == LEGACY_VERSION:
        return Loaded({}, sorted(stocks))
    return Loaded({code: _read(code, item) for code, item in stocks.items()})


def write(path: Path, memory: dict[str, Memory]) -> None:
    """기억 전체를 쓴다. 쓰지 못하면 OSError를 낸다."""
    stocks = {
        code: {
            "activated_at": format_moment(item.activated_at),
            "peak": format_decimal(item.peak),
            "peak_at": format_moment(item.peak_at),
            "triggered_at": format_moment(item.triggered_at),
            "triggered_price": format_decimal(item.triggered_price),
            "purchase_price": format_decimal(item.purchase_price),
        }
        for code, item in sorted(memory.items())
    }
    content = json.dumps({"version": FORMAT_VERSION, "stocks": stocks}, indent=2)
    write_atomic(path, content)


def _read(code: str, item: Any) -> Memory:
    if not code.strip():
        raise ValueError("종목코드가 비어 있습니다")
    if not isinstance(item, dict):
        raise ValueError(f"{code}: 객체가 아닙니다")
    triggered_at, triggered_price = item.get("triggered_at"), item.get("triggered_price")
    if (triggered_at is None) != (triggered_price is None):
        raise ValueError(f"{code}: triggered_at과 triggered_price는 함께 있어야 합니다")
    return Memory(
        activated_at=_read_moment(item.get("activated_at"), f"{code}.activated_at"),
        peak=_read_price(item.get("peak"), f"{code}.peak"),
        peak_at=_read_moment(item.get("peak_at"), f"{code}.peak_at"),
        purchase_price=_read_price(item.get("purchase_price"), f"{code}.purchase_price"),
        triggered_at=(
            None if triggered_at is None else _read_moment(triggered_at, f"{code}.triggered_at")
        ),
        triggered_price=(
            None
            if triggered_price is None
            else _read_price(triggered_price, f"{code}.triggered_price")
        ),
    )


def _read_moment(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name}이 문자열이 아닙니다")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"{name}에 시간대가 없습니다")
    return moment


def _read_price(value: Any, name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{name}이 문자열이 아닙니다")
    try:
        price = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name}이 숫자가 아닙니다") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError(f"{name}이 양수가 아닙니다")
    return price


def format_decimal(value: Decimal | None) -> str | None:
    """지수 표기 없이, 끝의 0을 뗀 문자열 소수. 200.0000 → 200."""
    return None if value is None else format(value.normalize(), "f")


def format_moment(value: datetime | None) -> str | None:
    """UTC ISO 8601, 초까지."""
    return None if value is None else value.astimezone(UTC).isoformat(timespec="seconds")
