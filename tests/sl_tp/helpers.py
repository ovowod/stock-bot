"""SL / TP 테스트가 함께 쓰는 앱 실행, 시계, 키움 응답, 설정 도우미."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI

from stock_bot.app import create_app
from stock_bot.core.notification import DiscordNotifier
from tests.fake_discord import DISCORD_ENV, FakeDiscord
from tests.fake_kiwoom import (
    DOMESTIC_PAPER_ENV,
    FAKE_ENV,
    KT00018_HOLDING,
    KT00018_REPLY,
    UST21070_HOLDING,
    UST21070_REPLY,
    FakeKiwoom,
    usa10099_row,
)
from tests.fake_realtime import FakeConnection, FakeRealtime
from tests.support import wait_until

KST = ZoneInfo("Asia/Seoul")
# 월요일 한국 장중.
AT_10 = datetime(2026, 10, 26, 10, 0, tzinfo=KST)
SELL_REPLY = {"ord_no": "0000138", "dmst_stex_tp": "KRX"}


class Clock:
    def __init__(self, at: datetime = AT_10) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def session(
    start: str = "09:00",
    end: str = "15:30",
    enabled: bool = True,
    take_profit: str | None = None,
    stop_loss: str | None = "3",
) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "enabled": enabled,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
    }


def write_settings(
    data_dir: Path,
    *sessions: dict[str, Any],
    excluded: tuple[str, ...] = (),
    environment: str = "domestic_paper",
) -> None:
    path = data_dir / "sl-tp" / f"{environment}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "version": 1,
        "updated_at": None,
        "excluded": [{"code": c, "name": "제외", "exchange": None} for c in excluded],
        "sessions": list(sessions or (session(),)),
    }
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def position(code: str = "005930", **overrides: str) -> dict[str, str]:
    """kt00018 보유종목 한 줄. 기본은 매입가 60,000원, 매도 가능 3주의 현금잔고다."""
    return {
        **KT00018_HOLDING,
        "stk_cd": f"A{code}",
        "pur_pric": "000000000060000",
        "rmnd_qty": "000000000000003",
        "trde_able_qty": "000000000000003",
        **overrides,
    }


def balance(*rows: dict[str, str]) -> dict[str, Any]:
    return {**KT00018_REPLY, "acnt_evlt_remn_indv_tot": list(rows or (position(),))}


def sl_tp_kiwoom(*balances: dict[str, Any]) -> FakeKiwoom:
    return FakeKiwoom().reply("kt00018", *(balances or (balance(),))).reply("kt10001", SELL_REPLY)


def quote(code: str = "005930", price: str = "-58200", market: str = "2") -> dict[str, Any]:
    """국내 0B 주식체결 메시지. 체결가(10)의 부호는 등락 방향이다."""
    return {
        "trnm": "REAL",
        "data": [
            {
                "type": "0B",
                "name": "주식체결",
                "item": code,
                "values": {"20": "100000", "10": price, "290": market, "9081": "KRX"},
            }
        ],
    }


class Gate:
    """match에 맞는 요청을 open이 열릴 때까지 붙잡아 둔다."""

    def __init__(self, kiwoom: FakeKiwoom, match: Callable[[httpx.Request], bool]) -> None:
        self.kiwoom = kiwoom
        self.match = match
        self.open = asyncio.Event()
        self.hit = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.match(request) and not self.open.is_set():
            self.hit.set()
            await self.open.wait()
        return self.kiwoom(request)


def api(request: httpx.Request) -> str | None:
    return request.headers.get("api-id")


@asynccontextmanager
async def running(
    realtime: FakeRealtime,
    kiwoom: Callable[[httpx.Request], Any],
    data_dir: Path,
    clock: Clock | None = None,
    environ: dict[str, str] = DOMESTIC_PAPER_ENV,
    discord: FakeDiscord | None = None,
) -> AsyncIterator[FastAPI]:
    app = create_app(
        environ=environ,
        transport=httpx.MockTransport(kiwoom),
        static_dir=None,
        log_dir=None,
        data_dir=data_dir,
        notifier=DiscordNotifier(
            DISCORD_ENV if discord else {}, transport=httpx.MockTransport(discord or FakeDiscord())
        ),
        realtime_connect=realtime,
        now=clock or Clock(),
    )
    async with app.router.lifespan_context(app):
        yield app


def quote_regs(connection: FakeConnection) -> list[dict[str, Any]]:
    return [m for m in connection.requests("REG") if m["data"][0]["type"] == ["0B"]]


async def watching(realtime: FakeRealtime) -> FakeConnection:
    """SL / TP의 0B 구독이 등록된 연결."""
    await wait_until(lambda: any(quote_regs(c) for c in realtime.connections))
    return next(c for c in realtime.connections if quote_regs(c))


async def settle() -> None:
    await asyncio.sleep(0.05)


def messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


async def save(app: FastAPI, *sessions: dict[str, Any], excluded: tuple[str, ...] = ()) -> None:
    """SL / TP 화면처럼 저장 API로 설정을 저장한다."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        response = await client.put(
            "/api/environments/domestic_paper/sl-tp",
            json={
                "excluded": [{"code": c, "name": "제외", "exchange": None} for c in excluded],
                "sessions": list(sessions),
            },
        )
    assert response.status_code == 200, response.text


def nth_call_gate(kiwoom: FakeKiwoom, api_id: str, n: int) -> Gate:
    """api_id의 n번째 호출만 붙잡는다."""
    seen = {"count": 0}

    def match(request: httpx.Request) -> bool:
        if api(request) != api_id:
            return False
        seen["count"] += 1
        return seen["count"] == n

    return Gate(kiwoom, match)


# 2026-10-26 14:00 UTC는 뉴욕 10:00(서머타임), 한국 23:00이다.
NY_10 = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)
US_SELL_REPLY = {"ord_no": "000000300"}


LISTING = [
    usa10099_row("AAPL", "애플", "APPLE INC", "ND"),
    usa10099_row("SPY", "SPDR S&P 500", "SPDR S&P 500 ETF", "NY"),
    usa10099_row("MSFT", "마이크로소프트", "MICROSOFT CORP", "ND"),
]


def us_session(start: str = "09:30", end: str = "16:00", **overrides: Any) -> dict[str, Any]:
    return session(start=start, end=end, **overrides)


def write_us_settings(
    data_dir: Path, *sessions: dict[str, Any], excluded: tuple[str, ...] = ()
) -> None:
    path = data_dir / "sl-tp" / "us_paper.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "version": 1,
        "updated_at": None,
        "excluded": [{"code": c, "name": "제외", "exchange": "NASDAQ"} for c in excluded],
        "sessions": list(sessions or (us_session(),)),
    }
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def us_position(code: str = "AAPL", **overrides: str) -> dict[str, str]:
    """ust21070 보유종목 한 줄. 기본은 매입단가 $200, 매도 가능 3주다."""
    return {
        **UST21070_HOLDING,
        "stk_cd": code,
        "frgn_stk_book_uv": "200.0000",
        "poss_qty": "000000000003",
        "sell_alowq": "000000000003",
        **overrides,
    }


def us_balance(*rows: dict[str, str]) -> dict[str, Any]:
    return {**UST21070_REPLY, "result_list": list(rows or (us_position(),))}


def us_kiwoom(*balances: dict[str, Any] | httpx.Response) -> FakeKiwoom:
    return (
        FakeKiwoom()
        .reply("ust21070", *(balances or (us_balance(),)))
        .reply("usa10099", {"list": LISTING})
        .reply("ust20001", US_SELL_REPLY)
    )


def us_quote(code: str = "AAPL", price: str = "-194.0000", market: str = "0") -> dict[str, Any]:
    """FE 미국주식 실시간 체결가. 체결가(10)의 부호는 등락 방향이다."""
    return {
        "trnm": "REAL",
        "data": [
            {
                "type": "FE",
                "name": "미국주식 실시간 체결가",
                "stexTp": "ND",
                "item": code,
                "values": {"10": price, "290": market, "20": "100000", "22": "20261026"},
            }
        ],
    }


def us_quote_regs(connection: FakeConnection) -> list[dict[str, Any]]:
    return [m for m in connection.requests("REG") if m["data"][0]["type"] == ["FE"]]


async def watching_us(realtime: FakeRealtime) -> FakeConnection:
    await wait_until(lambda: any(us_quote_regs(c) for c in realtime.connections))
    return next(c for c in realtime.connections if us_quote_regs(c))
