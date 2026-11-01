"""공통 판정 부품 가운데 SL / TP 테스트가 거치지 않는 부분.

꺼진 세션도 찾는 경우와 종목코드마다의 대상 아님 이유는 아직 앱에서 쓰는 곳이 없어 직접 확인한다.
"""

from datetime import UTC, datetime
from decimal import Decimal

from stock_bot.auto_trading.judging import check_holdings, session_at
from stock_bot.core.config import Market

# 한국시간 09:00–10:00(꺼짐), 10:00–11:00(켜짐). 10:00에서 맞닿는다.
SESSIONS = [
    {"start": "09:00", "end": "10:00", "enabled": False, "activation_profit": None},
    {"start": "10:00", "end": "11:00", "enabled": True, "activation_profit": "5"},
]


def kst(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 2, hour - 9, minute, tzinfo=UTC)


def row(code: str, price: str | None = "1000", credit: bool = False, exchange=None):
    return {
        "code": code,
        "name": code,
        "purchase_price": None if price is None else Decimal(price),
        "exchange": exchange,
        "credit": credit,
    }


def test_disabled_session_is_found_only_when_asked():
    assert session_at(SESSIONS, kst(9, 30), Market.DOMESTIC) is None
    found = session_at(SESSIONS, kst(9, 30), Market.DOMESTIC, include_disabled=True)
    assert found is not None and found[1] is SESSIONS[0]


def test_touching_sessions_belong_to_the_later_one_including_disabled():
    for include_disabled in (False, True):
        found = session_at(SESSIONS, kst(10, 0), Market.DOMESTIC, include_disabled=include_disabled)
        assert found is not None and found[1] is SESSIONS[1]
    assert session_at(SESSIONS, kst(11, 0), Market.DOMESTIC, include_disabled=True) is None


def test_ineligible_reason_per_code():
    checked = check_holdings(
        Market.US,
        [
            row("CASH", exchange="NASDAQ"),
            row("CASH", credit=True, exchange="NASDAQ"),
            row("CREDIT", credit=True, exchange="NASDAQ"),
            row("NOPRICE", price=None, exchange="NASDAQ"),
            row("NOPRICE", credit=True, exchange="NASDAQ"),
            row("TWICE", exchange="NYSE"),
            row("TWICE", exchange="NYSE"),
            row("TWICE", price="0", exchange="NYSE"),
            row("NOEXCH", exchange=None),
        ],
    )

    assert set(checked.eligible) == {"CASH"}
    assert checked.ineligible == {
        "CREDIT": "credit",
        "NOPRICE": "no_purchase_price",
        "TWICE": "duplicate",
        "NOEXCH": "unknown_exchange",
    }
    assert checked.counts == {
        "credit": 3,
        "no_purchase_price": 2,
        "duplicate": 1,
        "unknown_exchange": 1,
    }
    assert checked.unknown_exchange_codes == ["NOEXCH"]
