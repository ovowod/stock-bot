"""SL / TP 설정: 투자 환경마다 제외종목과 SL/TP 세션을 JSON 파일 하나로 보관한다.

매도 동작은 아직 없다. 설정을 읽고, 검사하고, 저장하기만 한다.
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stock_bot.config import Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.logging_setup import log

logger = logging.getLogger("stock_bot.sl_tp")

FORMAT_VERSION = 1
TIMEZONES = {Market.DOMESTIC: "Asia/Seoul", Market.US: "America/New_York"}
# 정규장. 휴장일·단축 거래일은 다루지 않는다(spec의 Out of Scope).
REGULAR_HOURS = {Market.DOMESTIC: ("09:00", "15:30"), Market.US: ("09:30", "16:00")}
US_EXCHANGES = ("NYSE", "NASDAQ", "AMEX")


class SlTpSettingsStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._locks: dict[Environment, asyncio.Lock] = {}

    def _path(self, spec: EnvironmentSpec) -> Path:
        return self._directory / f"{spec.environment.value}.json"

    def read(self, spec: EnvironmentSpec) -> dict[str, Any]:
        settings = self._load(spec)
        log(logger, logging.INFO, "sl_tp_settings_read", **_counts(settings))
        return _response(spec, settings)

    async def save(self, spec: EnvironmentSpec, payload: Any) -> dict[str, Any]:
        try:
            settings = _validate(payload, spec.market)
        except AppError as exc:
            log(logger, logging.WARNING, "sl_tp_settings_rejected", cause=exc.message)
            raise
        settings["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        async with self._locks.setdefault(spec.environment, asyncio.Lock()):
            self._write(spec, settings)
        log(logger, logging.INFO, "sl_tp_settings_saved", **_counts(settings))
        return _response(spec, settings)

    def _load(self, spec: EnvironmentSpec) -> dict[str, Any]:
        path = self._path(spec)
        if not path.exists():
            return {"updated_at": None, "excluded": [], "sessions": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
                raise ValueError(f"version이 {FORMAT_VERSION}이 아닙니다")
            updated_at = data.get("updated_at")
            if updated_at is not None and not isinstance(updated_at, str):
                raise ValueError("updated_at이 문자열이 아닙니다")
            settings = _validate(data, spec.market)
        except (OSError, ValueError) as exc:
            cause = str(exc)
            raise _unreadable(path, cause) from exc
        except AppError as exc:
            raise _unreadable(path, exc.message) from exc
        settings["updated_at"] = updated_at
        return settings

    def _write(self, spec: EnvironmentSpec, settings: dict[str, Any]) -> None:
        """임시 파일에 다 쓴 뒤 바꿔치기해, 쓰다 끊겨도 반쯤 쓴 파일이 남지 않게 한다."""
        path = self._path(spec)
        content = json.dumps({"version": FORMAT_VERSION, **settings}, ensure_ascii=False, indent=2)
        temp: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
            ) as file:
                temp = Path(file.name)
                file.write(content)
            os.replace(temp, path)
        except OSError as exc:
            if temp is not None:
                temp.unlink(missing_ok=True)
            log(
                logger,
                logging.ERROR,
                "sl_tp_settings_write_failed",
                path=str(path),
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            raise AppError(
                "sl_tp_settings_write_failed", "SL / TP 설정을 저장하지 못했습니다.", 500
            ) from exc


def _unreadable(path: Path, cause: str) -> AppError:
    log(logger, logging.ERROR, "sl_tp_settings_unreadable", path=str(path), cause=cause)
    return AppError("sl_tp_settings_unreadable", "SL / TP 설정 파일을 읽지 못했습니다.", 500)


def _response(spec: EnvironmentSpec, settings: dict[str, Any]) -> dict[str, Any]:
    start, end = REGULAR_HOURS[spec.market]
    return {
        "environment": spec.environment.value,
        "market": spec.market.value,
        "timezone": TIMEZONES[spec.market],
        "regular_hours": {"start": start, "end": end},
        "can_enable": not spec.is_real,
        "updated_at": settings["updated_at"],
        "excluded": settings["excluded"],
        "sessions": settings["sessions"],
    }


def _counts(settings: dict[str, Any]) -> dict[str, int]:
    return {
        "excluded_count": len(settings["excluded"]),
        "session_count": len(settings["sessions"]),
        "enabled_count": sum(1 for session in settings["sessions"] if session["enabled"]),
    }


def _invalid(message: str) -> AppError:
    return AppError("invalid_sl_tp_settings", message, 400)


def _validate(payload: Any, market: Market) -> dict[str, Any]:
    """요청이나 파일의 내용을 검사해 알려진 칸만 남긴 설정을 돌려준다."""
    if not isinstance(payload, dict):
        raise _invalid("요청 형식이 올바르지 않습니다.")
    excluded = payload.get("excluded")
    sessions = payload.get("sessions")
    if not isinstance(excluded, list):
        raise _invalid("제외종목 형식이 올바르지 않습니다.")
    if not isinstance(sessions, list):
        raise _invalid("SL/TP 세션 형식이 올바르지 않습니다.")
    if sessions:
        raise _invalid("SL/TP 세션은 아직 저장할 수 없습니다.")
    return {"excluded": _validate_excluded(excluded, market), "sessions": []}


def _validate_excluded(items: list[Any], market: Market) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        where = f"{index}번째 제외종목"
        if not isinstance(item, dict):
            raise _invalid(f"{where}: 형식이 올바르지 않습니다.")
        code, name, exchange = item.get("code"), item.get("name"), item.get("exchange")
        if not isinstance(code, str) or not code.strip():
            raise _invalid(f"{where}: 종목코드가 비어 있습니다.")
        if not isinstance(name, str) or not name.strip():
            raise _invalid(f"{where}: 종목명이 비어 있습니다.")
        if market is Market.DOMESTIC and exchange is not None:
            raise _invalid(f"{where}: 국내 종목에는 거래소를 넣지 않습니다.")
        if market is Market.US and exchange not in US_EXCHANGES:
            raise _invalid(f"{where}: 거래소는 {'·'.join(US_EXCHANGES)} 중 하나여야 합니다.")
        if code in seen:
            raise _invalid(f"{where}: 이미 추가된 종목입니다({code}).")
        seen.add(code)
        result.append({"code": code, "name": name, "exchange": exchange})
    return result
