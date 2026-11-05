"""Dead Cross 판정 기록 파일: 투자 환경마다 JSON 파일 하나에 종목마다의 기억을 둔다.

자동매매 설정 파일, Trailing Stop 고점 기록과 따로 둔다. 판정은 봉이 끝날 때마다 바뀐다.
최근 자동 매도도 함께 둔다. 전량 체결로 종목이 사라져도 결과를 보고, 재시작 뒤에도 남기려는 것이다.
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
# 상태만 계산하던 때의 형식. 그때의 데드크로스 발생으로 팔지 않게, 읽으면 기억을 모두 버린다.
LEGACY_VERSION = 1


@dataclass(frozen=True)
class Basis:
    """이동평균을 계산한 기준."""

    bar_period: str
    short_period: int
    long_period: int


@dataclass
class Memory:
    """종목 하나의 기억. 보유하는 동안 세션과 날짜를 넘겨 이어 간다."""

    # 마지막으로 판정한 봉의 기준과 시작 시각.
    basis: Basis
    judged_bar: datetime
    # 하향 돌파를 판정한 시각과 그 봉의 시작 시각. 풀리면 None이다.
    crossed_at: datetime | None = None
    crossed_bar: datetime | None = None
    # 마지막으로 확인한 평균 매입가. 없으면 None이다. 팔고 다시 샀는지 보려고 둔다.
    purchase_price: Decimal | None = None


@dataclass(frozen=True)
class Loaded:
    memory: dict[str, Memory]
    # 최근 자동 매도. 최신이 앞이다.
    recent_sells: list[dict[str, Any]]
    # 버전 1 파일이었으면 버린 종목코드. 아니면 None이다.
    cleared_codes: list[str] | None = None


def load(path: Path) -> Loaded:
    """파일이 없으면 빈 기록이다. 읽지 못하면 OSError, 형식이 맞지 않으면 ValueError를 낸다.

    버전 1 파일은 기억을 모두 버린 빈 기록으로 읽는다. 다시 쓰는 것은 부르는 쪽이 한다.
    """
    if not path.exists():
        return Loaded({}, [])
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version") if isinstance(data, dict) else None
    if version not in (FORMAT_VERSION, LEGACY_VERSION):
        raise ValueError(f"version이 {FORMAT_VERSION}이 아닙니다")
    stocks = data.get("stocks")
    if not isinstance(stocks, dict):
        raise ValueError("stocks가 객체가 아닙니다")
    if version == LEGACY_VERSION:
        return Loaded({}, [], sorted(stocks))
    recent_sells = data.get("recent_sells")
    if not isinstance(recent_sells, list) or not all(isinstance(s, dict) for s in recent_sells):
        raise ValueError("recent_sells가 객체 목록이 아닙니다")
    return Loaded(
        {code: _read(code, item) for code, item in stocks.items()},
        [_settled(sell) for sell in recent_sells],
    )


def write(path: Path, memory: dict[str, Memory], recent_sells: list[dict[str, Any]]) -> None:
    """기억과 최근 자동 매도 전체를 쓴다. 쓰지 못하면 OSError를 낸다."""
    stocks = {
        code: {
            "judged_bar": {
                "bar_period": item.basis.bar_period,
                "short_period": item.basis.short_period,
                "long_period": item.basis.long_period,
                "start": format_moment(item.judged_bar),
            },
            "crossed_at": format_moment(item.crossed_at),
            "crossed_bar": format_moment(item.crossed_bar),
            "purchase_price": None if item.purchase_price is None else str(item.purchase_price),
        }
        for code, item in sorted(memory.items())
    }
    content = json.dumps(
        {"version": FORMAT_VERSION, "stocks": stocks, "recent_sells": recent_sells},
        ensure_ascii=False,
        indent=2,
    )
    write_atomic(path, content)


def format_moment(value: datetime | None) -> str | None:
    """UTC ISO 8601, 초까지."""
    return None if value is None else value.astimezone(UTC).isoformat(timespec="seconds")


def _settled(sell: dict[str, Any]) -> dict[str, Any]:
    """주문 도중 서버가 꺼져 결과를 받지 못한 항목은 결과 확인 불가로 읽는다."""
    if sell.get("state") == "sending":
        return {**sell, "state": "failed", "reason": "unknown"}
    return sell


def _read(code: str, item: Any) -> Memory:
    if not code.strip():
        raise ValueError("종목코드가 비어 있습니다")
    if not isinstance(item, dict):
        raise ValueError(f"{code}: 객체가 아닙니다")
    judged = item.get("judged_bar")
    if not isinstance(judged, dict):
        raise ValueError(f"{code}.judged_bar가 객체가 아닙니다")
    bar_period = judged.get("bar_period")
    if not isinstance(bar_period, str) or not bar_period:
        raise ValueError(f"{code}.judged_bar.bar_period가 문자열이 아닙니다")
    crossed_at, crossed_bar = item.get("crossed_at"), item.get("crossed_bar")
    if (crossed_at is None) != (crossed_bar is None):
        raise ValueError(f"{code}: crossed_at과 crossed_bar는 함께 있어야 합니다")
    return Memory(
        basis=Basis(
            bar_period,
            _read_count(judged.get("short_period"), f"{code}.judged_bar.short_period"),
            _read_count(judged.get("long_period"), f"{code}.judged_bar.long_period"),
        ),
        judged_bar=_read_moment(judged.get("start"), f"{code}.judged_bar.start"),
        crossed_at=None if crossed_at is None else _read_moment(crossed_at, f"{code}.crossed_at"),
        crossed_bar=(
            None if crossed_bar is None else _read_moment(crossed_bar, f"{code}.crossed_bar")
        ),
        purchase_price=_read_price(item.get("purchase_price"), f"{code}.purchase_price"),
    )


def _read_price(value: Any, name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        price = Decimal(value) if isinstance(value, str) else None
    except InvalidOperation:
        price = None
    if price is None or not price.is_finite() or price <= 0:
        raise ValueError(f"{name}이 양수 문자열이 아닙니다")
    return price


def _read_count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name}이 양의 정수가 아닙니다")
    return value


def _read_moment(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name}이 문자열이 아닙니다")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"{name}에 시간대가 없습니다")
    return moment
