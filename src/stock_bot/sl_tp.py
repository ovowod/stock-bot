"""SL / TP 설정: 투자 환경마다 제외종목과 SL/TP 세션을 JSON 파일 하나로 보관한다.

매도 동작은 아직 없다. 설정을 읽고, 검사하고, 저장하기만 한다.
"""

import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from stock_bot.config import EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.logging_setup import log

logger = logging.getLogger("stock_bot.sl_tp")

FORMAT_VERSION = 1
TIMEZONES = {Market.DOMESTIC: "Asia/Seoul", Market.US: "America/New_York"}
# 정규장. 휴장일·단축 거래일은 다루지 않는다(spec의 Out of Scope).
REGULAR_HOURS = {Market.DOMESTIC: ("09:00", "15:30"), Market.US: ("09:30", "16:00")}
US_EXCHANGES = ("NYSE", "NASDAQ", "AMEX")
# fullmatch로 검사한다. "$"는 끝의 줄바꿈 앞에서도 맞고, \d는 아라비아 숫자 등도 받는다.
_TIME = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_PERCENT = re.compile(r"[0-9]+(\.[0-9]{1,2})?")
# 익절·손절 기준(%)의 범위. 손절 기준은 양수로 두고 손실률을 뜻한다.
TAKE_PROFIT_RANGE = (Decimal("0.01"), Decimal("1000"))
STOP_LOSS_RANGE = (Decimal("0.01"), Decimal("99.99"))


class SlTpSettingsStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

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
            log(
                logger,
                logging.WARNING,
                "sl_tp_settings_rejected",
                cause=exc.message,
                **_payload_counts(payload),
            )
            raise
        counts = _counts(settings)
        # 매도 동작이 붙어도 실전 계좌가 갑자기 매도되지 않게, 실전에서는 켜 둘 수 없다.
        if spec.is_real and counts["enabled_count"]:
            log(logger, logging.WARNING, "sl_tp_enable_refused", **counts)
            raise _enable_not_allowed()
        settings["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        # 쓰기는 await 없이 끝나므로 같은 환경의 저장이 서로 섞이지 않는다.
        self._write(spec, settings)
        log(logger, logging.INFO, "sl_tp_settings_saved", **counts)
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
            if spec.is_real and _counts(settings)["enabled_count"]:
                raise _enable_not_allowed()
        except (OSError, ValueError, AppError) as exc:
            cause = exc.message if isinstance(exc, AppError) else str(exc)
            log(
                logger,
                logging.ERROR,
                "sl_tp_settings_unreadable",
                path=str(path),
                error_type=type(exc).__name__,
                cause=cause,
            )
            raise AppError(
                "sl_tp_settings_unreadable", "SL / TP 설정 파일을 읽지 못했습니다.", 500
            ) from exc
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


def _enable_not_allowed() -> AppError:
    return AppError("sl_tp_enable_not_allowed", "실전투자에서는 SL/TP 세션을 켤 수 없습니다.", 403)


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


def _payload_counts(payload: Any) -> dict[str, int]:
    """검사를 통과하지 못한 요청도 로그에서 크기를 가늠할 수 있게 목록 길이만 센다."""
    counts: dict[str, int] = {}
    if isinstance(payload, dict):
        if isinstance(payload.get("excluded"), list):
            counts["excluded_count"] = len(payload["excluded"])
        if isinstance(payload.get("sessions"), list):
            counts["session_count"] = len(payload["sessions"])
    return counts


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
    return {
        "excluded": _validate_excluded(excluded, market),
        "sessions": _validate_sessions(sessions, market),
    }


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


def _validate_sessions(items: list[Any], market: Market) -> list[dict[str, Any]]:
    """HH:MM은 문자열 비교가 시각 비교와 같다. 시작 시각은 포함하고 종료 시각은 빼므로
    앞 세션의 종료와 다음 세션의 시작이 같아도 겹치지 않는다."""
    opens, closes = REGULAR_HOURS[market]
    result: list[dict[str, Any]] = []
    previous_end: str | None = None
    for index, item in enumerate(items, start=1):
        where = f"{index}번째 SL/TP 세션"
        if not isinstance(item, dict):
            raise _invalid(f"{where}: 형식이 올바르지 않습니다.")
        start, end = item.get("start"), item.get("end")
        if not (isinstance(start, str) and _TIME.fullmatch(start)) or not (
            isinstance(end, str) and _TIME.fullmatch(end)
        ):
            raise _invalid(f"{where}: 시각은 HH:MM 형식이어야 합니다.")
        if start >= end:
            raise _invalid(f"{where}: 시작 시각이 종료 시각보다 빨라야 합니다.")
        if start < opens or end > closes:
            raise _invalid(f"{where}: 정규장({opens}–{closes}) 안에서만 정할 수 있습니다.")
        if previous_end is not None and start < previous_end:
            raise _invalid(f"{where}: 시작 시각이 앞 세션의 종료 시각보다 이릅니다.")
        previous_end = end
        enabled = item.get("enabled")
        if not isinstance(enabled, bool):
            raise _invalid(f"{where}: 켜짐 여부가 올바르지 않습니다.")
        take_profit = _percent(item.get("take_profit"), TAKE_PROFIT_RANGE, f"{where}: 익절 기준")
        stop_loss = _percent(item.get("stop_loss"), STOP_LOSS_RANGE, f"{where}: 손절 기준")
        if enabled and take_profit is None and stop_loss is None:
            raise _invalid(
                f"{where}: 켜진 세션에는 익절 기준이나 손절 기준이 하나 이상 있어야 합니다."
            )
        result.append(
            {
                "start": start,
                "end": end,
                "enabled": enabled,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
            }
        )
    return result


def _percent(value: Any, bounds: tuple[Decimal, Decimal], name: str) -> str | None:
    """정하지 않았으면 None, 맞으면 받은 문자열을 그대로 돌려준다."""
    if value is None:
        return None
    low, high = bounds
    if (
        not isinstance(value, str)
        or not _PERCENT.fullmatch(value)
        or not low <= Decimal(value) <= high
    ):
        raise _invalid(f"{name}은 {low}~{high} 사이, 소수점 둘째 자리까지의 숫자여야 합니다.")
    return value
