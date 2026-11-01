"""Trailing Stop 테스트 도우미: 설정·잔고·고점 기록 파일을 만들고 상태 조회 API를 부른다."""

import json
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from tests.fake_discord import FakeDiscord
from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom
from tests.fake_realtime import FakeConnection, FakeRealtime, fill_event
from tests.sl_tp.helpers import (
    AT_10,
    LISTING,
    SELL_REPLY,
    US_SELL_REPLY,
    balance,
    position,
    settle,
    us_balance,
)
from tests.support import wait_until


def ts_session(
    start: str = "09:00",
    end: str = "15:30",
    enabled: bool = True,
    activation_profit: str | None = "5",
    drop_from_peak: str | None = "3",
) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "enabled": enabled,
        "activation_profit": activation_profit,
        "drop_from_peak": drop_from_peak,
    }


def write_ts(
    data_dir: Path,
    *sessions: dict[str, Any],
    excluded: tuple[str, ...] = (),
    environment: str = "domestic_paper",
    sl_tp: dict[str, Any] | None = None,
) -> None:
    """Trailing Stop 칸만 둔 자동매매 설정 파일. SL / TP는 sl_tp를 줄 때만 켠다."""
    exchange = "NASDAQ" if environment.startswith("us") else None
    path = data_dir / "auto-trading" / f"{environment}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content: dict[str, Any] = {
        "version": 2,
        "trailing_stop": {
            "updated_at": None,
            "excluded": [{"code": c, "name": "제외", "exchange": exchange} for c in excluded],
            "sessions": list(sessions or (ts_session(),)),
        },
    }
    if sl_tp is not None:
        content["sl_tp"] = sl_tp
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def ts_kiwoom(*balances: Any) -> FakeKiwoom:
    """잔고 응답과 접수되는 매도 주문 응답."""
    return FakeKiwoom().reply("kt00018", *(balances or (balance(),))).reply("kt10001", SELL_REPLY)


def ts_us_kiwoom(*balances: Any) -> FakeKiwoom:
    kiwoom = (
        FakeKiwoom()
        .reply("ust21070", *(balances or (us_balance(),)))
        .reply("usa10099", {"list": LISTING})
        .reply("ust20001", US_SELL_REPLY)
    )
    return kiwoom


def held(code: str = "005930", price: str = "60000", quantity: str = "3", **extra: str):
    """매입가 price원, quantity주인 현금잔고."""
    return position(
        code,
        pur_pric=f"{int(price):015d}",
        rmnd_qty=f"{int(quantity):015d}",
        trde_able_qty=f"{int(quantity):015d}",
        **extra,
    )


def sell_fill(code: str = "005930", quantity: str = "3") -> dict[str, Any]:
    return fill_event(
        **{"9001": code, "907": "1", "905": "-매도", "915": quantity, "911": quantity}
    )


def ts_regs(connection: FakeConnection) -> list[dict[str, Any]]:
    return [m for m in connection.requests("REG") if m["grp_no"] == "3"]


async def watching(realtime: FakeRealtime) -> FakeConnection:
    """Trailing Stop의 시세 구독(그룹 3)이 등록된 연결."""
    await wait_until(lambda: any(ts_regs(c) for c in realtime.connections))
    return next(c for c in realtime.connections if ts_regs(c))


async def status(app: FastAPI, environment: str = "domestic_paper") -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        response = await client.get(f"/api/environments/{environment}/trailing-stop/status")
    assert response.status_code == 200, response.text
    return response.json()


async def stock(app: FastAPI, code: str = "005930", environment: str = "domestic_paper"):
    body = await status(app, environment)
    return next((s for s in body["stocks"] if s["code"] == code), None)


async def until_status(app: FastAPI, check, environment: str = "domestic_paper") -> dict:
    found: dict[str, Any] = {}

    async def poll() -> bool:
        found["body"] = await status(app, environment)
        return check(found["body"])

    for _ in range(200):
        if await poll():
            return found["body"]
        await settle()
    raise AssertionError(f"상태를 기다렸지만 만족하지 않았습니다: {found.get('body')}")


def utc(moment) -> str:
    return moment.astimezone(UTC).isoformat()


def record_path(data_dir: Path, environment: str = "domestic_paper") -> Path:
    return data_dir / "trailing-stop" / f"{environment}.json"


def read_record(data_dir: Path) -> dict[str, Any]:
    return json.loads(record_path(data_dir).read_text(encoding="utf-8"))


def remembered(
    activated_at,
    peak: str,
    peak_at,
    triggered_at=None,
    triggered_price: str | None = None,
    purchase_price: str = "60000",
) -> dict[str, Any]:
    """고점 기록 파일의 종목 하나(버전 2)."""
    return {
        "activated_at": utc(activated_at),
        "peak": peak,
        "peak_at": utc(peak_at),
        "triggered_at": None if triggered_at is None else utc(triggered_at),
        "triggered_price": triggered_price,
        "purchase_price": purchase_price,
    }


def write_record(data_dir: Path, stocks: dict[str, Any], version: int = 2) -> None:
    path = record_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version, "stocks": stocks}), encoding="utf-8")


async def put_ts(
    app: FastAPI, *sessions: dict[str, Any], excluded: tuple[str, ...] = ()
) -> httpx.Response:
    """Trailing Stop 화면처럼 저장 API로 설정을 저장한다."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        response = await client.put(
            "/api/environments/domestic_paper/trailing-stop",
            json={
                "excluded": [{"code": c, "name": "제외", "exchange": None} for c in excluded],
                "sessions": list(sessions or (ts_session(),)),
            },
        )
    assert response.status_code == 200, response.text
    return response


def fill_regs(connection: FakeConnection) -> list[dict[str, Any]]:
    return [m for m in connection.requests("REG") if m["grp_no"] == "1"]


def ts_alerts(discord: FakeDiscord) -> list[dict[str, Any]]:
    """Trailing Stop 판정 중단·저장 실패 알림만. 로그인 알림과 자동 매도 결과 알림은 뺀다."""
    return [
        p
        for p in discord.payloads
        if p["embeds"][0]["title"].startswith("Trailing Stop")
        and not p["embeds"][0]["title"].startswith("Trailing Stop 자동 매도")
    ]


async def alerted(discord: FakeDiscord) -> None:
    await wait_until(lambda: bool(ts_alerts(discord)))


AT_16 = AT_10.replace(hour=16)
NEXT_DAY_10 = AT_10 + timedelta(days=1)


REJECTED_REG = {"trnm": "REG", "return_code": 1, "return_msg": "등록 실패"}


def rejecting(group: str, flag: dict[str, bool]) -> FakeRealtime:
    """flag["on"]인 동안 그 그룹의 REG를 거부한다."""

    def respond(message: dict[str, Any]) -> dict[str, Any]:
        if message["trnm"] == "REG" and message["grp_no"] == group and flag["on"]:
            return REJECTED_REG
        return {"trnm": message["trnm"], "return_code": 0, "return_msg": ""}

    return FakeRealtime(responder=respond)
