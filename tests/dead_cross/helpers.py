"""Dead Cross 테스트 도우미: 설정·차트 응답·판정 기록 파일을 만들고 상태 조회 API를 부른다."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI

from tests.fake_kiwoom import FAKE_ENV, FakeKiwoom, body_of, kiwoom_error, page_response
from tests.sl_tp.helpers import LISTING, balance, settle, us_balance

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


def dc_session(
    start: str = "09:00",
    end: str = "15:30",
    enabled: bool = True,
    bar_period: str | None = "5m",
    short_period: int | None = 2,
    long_period: int | None = 3,
) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "enabled": enabled,
        "bar_period": bar_period,
        "short_period": short_period,
        "long_period": long_period,
    }


def write_dc(
    data_dir: Path,
    *sessions: dict[str, Any],
    excluded: tuple[str, ...] = (),
    environment: str = "domestic_paper",
) -> None:
    """Dead Cross 칸만 둔 자동매매 설정 파일."""
    exchange = "NASDAQ" if environment.startswith("us") else None
    path = data_dir / "auto-trading" / f"{environment}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "version": 2,
        "dead_cross": {
            "updated_at": None,
            "excluded": [{"code": c, "name": "제외", "exchange": exchange} for c in excluded],
            "sessions": list(sessions or (dc_session(),)),
        },
    }
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def minute_rows(
    newest: datetime, closes: list[str], minutes: int = 5, sign: str = "-"
) -> list[dict[str, str]]:
    """newest 봉부터 과거로 minutes 간격의 분봉 행. closes도 최신부터다.

    국내 분봉처럼 가격 앞에 전일 대비 부호를 붙인다. 미국은 sign=""로 준다.
    """
    rows = []
    for index, close in enumerate(closes):
        start = newest - timedelta(minutes=minutes * index)
        rows.append({"cntr_tm": start.strftime("%Y%m%d%H%M%S"), "cur_prc": f"{sign}{close}"})
    return rows


def daily_rows(newest: datetime, closes: list[str]) -> list[dict[str, str]]:
    return [
        {"dt": (newest - timedelta(days=index)).strftime("%Y%m%d"), "cur_prc": close}
        for index, close in enumerate(closes)
    ]


class Chart:
    """차트 TR 응답. 종목코드마다 최신부터의 행을 두고 page_size씩 연속조회로 나눠 준다."""

    def __init__(self, list_key: str = "stk_min_pole_chart_qry", page_size: int = 900) -> None:
        self.list_key = list_key
        self.page_size = page_size
        self.rows: dict[str, list[dict[str, str]]] = {}
        self.failing: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        code = body_of(request)["stk_cd"]
        if code in self.failing:
            return kiwoom_error(1, "차트 조회 실패")
        start = int(request.headers.get("next-key") or 0)
        rows = self.rows.get(code, [])
        end = start + self.page_size
        more = end < len(rows)
        return page_response(
            {self.list_key: rows[start:end]}, "Y" if more else "N", str(end) if more else ""
        )


def dc_kiwoom(chart: Chart, *balances: Any, api_id: str = "ka10080") -> FakeKiwoom:
    return FakeKiwoom().reply("kt00018", *(balances or (balance(),))).respond(api_id, chart)


def dc_us_kiwoom(chart: Chart, *balances: Any, api_id: str = "usa06011") -> FakeKiwoom:
    return (
        FakeKiwoom()
        .reply("ust21070", *(balances or (us_balance(),)))
        .reply("usa10099", {"list": LISTING})
        .respond(api_id, chart)
    )


async def status(app: FastAPI, environment: str = "domestic_paper") -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        response = await client.get(f"/api/environments/{environment}/dead-cross/status")
    assert response.status_code == 200, response.text
    return response.json()


async def until_status(app: FastAPI, check, environment: str = "domestic_paper") -> dict:
    found: dict[str, Any] = {}
    for _ in range(200):
        found["body"] = await status(app, environment)
        if check(found["body"]):
            return found["body"]
        await settle()
    raise AssertionError(f"상태를 기다렸지만 만족하지 않았습니다: {found.get('body')}")


def first(body: dict[str, Any]) -> dict[str, Any] | None:
    return body["stocks"][0] if body["stocks"] else None


def judged(body: dict[str, Any]) -> bool:
    item = first(body)
    return item is not None and item["judged_bar"] is not None


async def put_dc(
    app: FastAPI, *sessions: dict[str, Any], excluded: tuple[str, ...] = ()
) -> httpx.Response:
    """Dead Cross 화면처럼 저장 API로 설정을 저장한다."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/auth/login", json={"password": FAKE_ENV["PASSWORD"]})
        response = await client.put(
            "/api/environments/domestic_paper/dead-cross",
            json={
                "excluded": [{"code": c, "name": "제외", "exchange": None} for c in excluded],
                "sessions": list(sessions or (dc_session(),)),
            },
        )
    assert response.status_code == 200, response.text
    return response


def utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def record_path(data_dir: Path, environment: str = "domestic_paper") -> Path:
    return data_dir / "dead-cross" / f"{environment}.json"


def read_record(data_dir: Path, environment: str = "domestic_paper") -> dict[str, Any]:
    return json.loads(record_path(data_dir, environment).read_text(encoding="utf-8"))


def remembered(
    start: datetime,
    crossed_at: datetime | None = None,
    crossed_bar: datetime | None = None,
    bar_period: str = "5m",
    short_period: int = 2,
    long_period: int = 3,
    purchase_price: str | None = "60000",
) -> dict[str, Any]:
    """판정 기록 파일의 종목 하나."""
    return {
        "judged_bar": {
            "bar_period": bar_period,
            "short_period": short_period,
            "long_period": long_period,
            "start": utc(start),
        },
        "crossed_at": None if crossed_at is None else utc(crossed_at),
        "crossed_bar": None if crossed_bar is None else utc(crossed_bar),
        "purchase_price": purchase_price,
    }


def write_record(
    data_dir: Path,
    stocks: dict[str, Any],
    environment: str = "domestic_paper",
    recent_sells: list[dict[str, Any]] | None = None,
) -> None:
    path = record_path(data_dir, environment)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {"version": 2, "stocks": stocks, "recent_sells": recent_sells or []}
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def write_legacy_record(data_dir: Path, stocks: dict[str, Any]) -> None:
    """상태만 계산하던 때의 버전 1 기록."""
    path = record_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "stocks": stocks}), encoding="utf-8")
