"""자동매매 설정: 투자 환경마다 JSON 파일 하나에 자동매매 기능별 칸을 둔다(ADR 0004).

파일 읽기·쓰기, 예전 SL / TP 파일 이전, 기능들이 함께 쓰는 제외종목·세션 시각 검사를 갖는다.
기능마다 다른 것은 세션의 기준 칸뿐이라 Feature.criteria로 받는다.
저장은 기능마다 따로 하며, 성공하면 on_saved에 그 기능의 새 설정을 알린다.
"""

import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from stock_bot.core.config import ENVIRONMENTS, EnvironmentSpec, Market
from stock_bot.core.errors import AppError
from stock_bot.core.logging_setup import environment_var, log
from stock_bot.kiwoom.us_exchange import US_EXCHANGES

logger = logging.getLogger("stock_bot.auto_trading")

FORMAT_VERSION = 2
LEGACY_SL_TP_VERSION = 1
TIMEZONES = {Market.DOMESTIC: "Asia/Seoul", Market.US: "America/New_York"}
# 정규장. 휴장일·단축 거래일은 다루지 않는다(spec의 Out of Scope).
REGULAR_HOURS = {Market.DOMESTIC: ("09:00", "15:30"), Market.US: ("09:30", "16:00")}
# fullmatch로 검사한다. "$"는 끝의 줄바꿈 앞에서도 맞고, \d는 아라비아 숫자 등도 받는다.
_TIME = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_PERCENT = re.compile(r"[0-9]+(\.[0-9]{1,2})?")


class Invalid(ValueError):
    """설정이 검사 규칙에 맞지 않는다. 메시지는 사용자에게 그대로 보여준다."""


# (세션 항목, 켜짐 여부, 위치 문구) → 기준 칸만 남긴 dict. 맞지 않으면 Invalid.
Criteria = Callable[[dict[str, Any], bool, str], dict[str, Any]]


@dataclass(frozen=True)
class Feature:
    """자동매매 기능 하나. key는 파일의 칸 이름이자 로그 이벤트·오류 종류의 앞부분이다."""

    key: str
    name: str
    session_name: str
    criteria: Criteria


# 저장이 성공하면 (투자 환경, 읽기 API와 같은 모양의 설정)으로 부른다.
SettingsListener = Callable[[EnvironmentSpec, dict[str, Any]], None]


class SettingsMigrationError(RuntimeError):
    """예전 설정 파일을 옮기지 못했다. 서버를 시작하지 않는다."""


class AutoTradingSettingsFile:
    """투자 환경마다 파일 하나. 한 칸이라도 깨지면 파일 전체를 읽지 못한 것으로 본다."""

    def __init__(self, directory: Path, features: Sequence[Feature]) -> None:
        self._directory = directory
        self._features = features

    def path(self, spec: EnvironmentSpec) -> Path:
        return self._directory / f"{spec.environment.value}.json"

    def load(self, spec: EnvironmentSpec) -> dict[str, Any]:
        """파일의 칸들을 돌려준다. 읽지 못하면 OSError나 ValueError를 낸다.

        아는 기능의 칸은 검사한 값으로, 모르는 칸은 검사 없이 그대로 돌려준다. 모르는 칸도
        저장할 때 다시 써서, 이전 서버로 되돌려 저장해도 나중에 더한 기능의 칸이 지워지지 않는다.
        """
        path = self.path(spec)
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
            raise ValueError(f"version이 {FORMAT_VERSION}가 아닙니다")
        sections = {key: value for key, value in data.items() if key != "version"}
        for feature in self._features:
            if feature.key in data:
                sections[feature.key] = _read_section(data[feature.key], feature, spec)
        return sections

    def write(self, spec: EnvironmentSpec, sections: dict[str, Any]) -> None:
        """임시 파일에 다 쓴 뒤 바꿔치기해, 쓰다 끊겨도 반쯤 쓴 파일이 남지 않게 한다."""
        path = self.path(spec)
        content = json.dumps({"version": FORMAT_VERSION, **sections}, ensure_ascii=False, indent=2)
        temp: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
            ) as file:
                temp = Path(file.name)
                file.write(content)
            os.replace(temp, path)
        except OSError:
            if temp is not None:
                temp.unlink(missing_ok=True)
            raise

    def migrate(self, legacy_dir: Path, feature: Feature) -> None:
        """예전 SL / TP 파일(version 1)을 새 파일의 feature 칸으로 옮긴다. 원본은 그대로 둔다.

        새 파일이 이미 있으면 예전 파일은 보지 않는다. 옮기지 못하면 SL / TP가 빈 설정으로
        조용히 바뀌지 않게 SettingsMigrationError를 내 서버를 시작하지 않는다.
        """
        for spec in ENVIRONMENTS.values():
            legacy = legacy_dir / f"{spec.environment.value}.json"
            path = self.path(spec)
            if path.exists() or not legacy.exists():
                continue
            token = environment_var.set(spec.environment.value)
            try:
                try:
                    data = json.loads(legacy.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or data.get("version") != LEGACY_SL_TP_VERSION:
                        raise ValueError(f"version이 {LEGACY_SL_TP_VERSION}이 아닙니다")
                    self.write(spec, {feature.key: _read_section(data, feature, spec)})
                except (OSError, ValueError) as exc:
                    log(
                        logger,
                        logging.ERROR,
                        "auto_trading_settings_migration_failed",
                        legacy_path=str(legacy),
                        path=str(path),
                        error_type=type(exc).__name__,
                        cause=str(exc),
                    )
                    raise SettingsMigrationError(
                        f"예전 설정 파일을 옮기지 못했습니다: {legacy}"
                    ) from exc
                log(
                    logger,
                    logging.INFO,
                    "auto_trading_settings_migrated",
                    legacy_path=str(legacy),
                    path=str(path),
                )
            finally:
                environment_var.reset(token)


class FeatureSettingsStore:
    """자동매매 설정 파일에서 기능 하나의 칸을 읽고, 검사하고, 저장한다."""

    def __init__(self, file: AutoTradingSettingsFile, feature: Feature) -> None:
        self._file = file
        self._feature = feature
        self._logger = logging.getLogger(f"stock_bot.{feature.key}")
        self.on_saved: SettingsListener | None = None

    def read(self, spec: EnvironmentSpec) -> dict[str, Any]:
        settings = self._load(spec).get(self._feature.key, _empty())
        self._log(logging.INFO, "settings_read", **_counts(settings))
        return _response(spec, settings)

    async def save(self, spec: EnvironmentSpec, payload: Any) -> dict[str, Any]:
        feature = self._feature
        try:
            settings = _validate(payload, feature, spec.market)
        except Invalid as exc:
            self._log(
                logging.WARNING, "settings_rejected", cause=str(exc), **_payload_counts(payload)
            )
            raise AppError(f"invalid_{feature.key}_settings", str(exc), 400) from exc
        counts = _counts(settings)
        # 자동 매도를 보낼 수 없는 환경(주문 허용이 꺼진 실전)에서는 켜 둘 수 없다.
        if not spec.orders_allowed and counts["enabled_count"]:
            self._log(logging.WARNING, "enable_refused", **counts)
            raise AppError(
                f"{feature.key}_enable_not_allowed",
                f"실전투자에서는 {feature.session_name}을 켤 수 없습니다.",
                403,
            )
        # 다른 기능의 칸을 그대로 다시 쓰려고 먼저 읽는다. 깨진 파일 위에는 덮어쓰지 않는다.
        # 읽기부터 쓰기까지 await가 없어 같은 환경의 저장이 서로 섞이지 않는다.
        sections = self._load(spec)
        settings["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        sections[feature.key] = settings
        try:
            self._file.write(spec, sections)
        except OSError as exc:
            self._log(
                logging.ERROR,
                "settings_write_failed",
                path=str(self._file.path(spec)),
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            raise AppError(
                f"{feature.key}_settings_write_failed",
                f"{feature.name} 설정을 저장하지 못했습니다.",
                500,
            ) from exc
        self._log(logging.INFO, "settings_saved", **counts)
        response = _response(spec, settings)
        if self.on_saved is not None:
            # 이미 저장했다. 반영 쪽 오류로 저장 결과를 실패로 돌려주지 않는다.
            try:
                self.on_saved(spec, response)
            except Exception as exc:
                self._logger.error(
                    f"{feature.key}_settings_listener_failed",
                    exc_info=exc,
                    extra={"fields": {"error_type": type(exc).__name__}},
                )
        return response

    def _load(self, spec: EnvironmentSpec) -> dict[str, Any]:
        try:
            return self._file.load(spec)
        except (OSError, ValueError) as exc:
            self._log(
                logging.ERROR,
                "settings_unreadable",
                path=str(self._file.path(spec)),
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            raise AppError(
                f"{self._feature.key}_settings_unreadable",
                f"{self._feature.name} 설정 파일을 읽지 못했습니다.",
                500,
            ) from exc

    def _log(self, level: int, event: str, **fields: Any) -> None:
        log(self._logger, level, f"{self._feature.key}_{event}", **fields)


def _empty() -> dict[str, Any]:
    return {"updated_at": None, "excluded": [], "sessions": []}


def _read_section(data: Any, feature: Feature, spec: EnvironmentSpec) -> dict[str, Any]:
    """파일의 한 칸을 검사한다. 실전 환경에 켜진 세션이 있는 것도 규칙에 맞지 않는 것으로 본다."""
    if not isinstance(data, dict):
        raise ValueError(f"{feature.key} 칸이 객체가 아닙니다")
    updated_at = data.get("updated_at")
    if updated_at is not None and not isinstance(updated_at, str):
        raise ValueError(f"{feature.key}.updated_at이 문자열이 아닙니다")
    try:
        settings = _validate(data, feature, spec.market)
    except Invalid as exc:
        raise ValueError(f"{feature.key}: {exc}") from exc
    if not spec.orders_allowed and _counts(settings)["enabled_count"]:
        raise ValueError(f"{feature.key}: 실전투자인데 켜진 {feature.session_name}이 있습니다")
    return {"updated_at": updated_at, **settings}


def _response(spec: EnvironmentSpec, settings: dict[str, Any]) -> dict[str, Any]:
    start, end = REGULAR_HOURS[spec.market]
    return {
        "environment": spec.environment.value,
        "market": spec.market.value,
        "timezone": TIMEZONES[spec.market],
        "regular_hours": {"start": start, "end": end},
        "can_enable": spec.orders_allowed,
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


def _validate(payload: Any, feature: Feature, market: Market) -> dict[str, Any]:
    """요청이나 파일의 내용을 검사해 알려진 칸만 남긴 설정을 돌려준다."""
    if not isinstance(payload, dict):
        raise Invalid("요청 형식이 올바르지 않습니다.")
    excluded = payload.get("excluded")
    sessions = payload.get("sessions")
    if not isinstance(excluded, list):
        raise Invalid("제외종목 형식이 올바르지 않습니다.")
    if not isinstance(sessions, list):
        raise Invalid(f"{feature.session_name} 형식이 올바르지 않습니다.")
    return {
        "excluded": _validate_excluded(excluded, market),
        "sessions": _validate_sessions(sessions, feature, market),
    }


def _validate_excluded(items: list[Any], market: Market) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        where = f"{index}번째 제외종목"
        if not isinstance(item, dict):
            raise Invalid(f"{where}: 형식이 올바르지 않습니다.")
        code, name, exchange = item.get("code"), item.get("name"), item.get("exchange")
        if not isinstance(code, str) or not code.strip():
            raise Invalid(f"{where}: 종목코드가 비어 있습니다.")
        if not isinstance(name, str) or not name.strip():
            raise Invalid(f"{where}: 종목명이 비어 있습니다.")
        if market is Market.DOMESTIC and exchange is not None:
            raise Invalid(f"{where}: 국내 종목에는 거래소를 넣지 않습니다.")
        if market is Market.US and exchange not in US_EXCHANGES:
            raise Invalid(f"{where}: 거래소는 {'·'.join(US_EXCHANGES)} 중 하나여야 합니다.")
        if code in seen:
            raise Invalid(f"{where}: 이미 추가된 종목입니다({code}).")
        seen.add(code)
        result.append({"code": code, "name": name, "exchange": exchange})
    return result


def _validate_sessions(items: list[Any], feature: Feature, market: Market) -> list[dict[str, Any]]:
    """HH:MM은 문자열 비교가 시각 비교와 같다. 시작 시각은 포함하고 종료 시각은 빼므로
    앞 세션의 종료와 다음 세션의 시작이 같아도 겹치지 않는다."""
    opens, closes = REGULAR_HOURS[market]
    result: list[dict[str, Any]] = []
    previous_end: str | None = None
    for index, item in enumerate(items, start=1):
        where = f"{index}번째 {feature.session_name}"
        if not isinstance(item, dict):
            raise Invalid(f"{where}: 형식이 올바르지 않습니다.")
        start, end = item.get("start"), item.get("end")
        if not (isinstance(start, str) and _TIME.fullmatch(start)) or not (
            isinstance(end, str) and _TIME.fullmatch(end)
        ):
            raise Invalid(f"{where}: 시각은 HH:MM 형식이어야 합니다.")
        if start >= end:
            raise Invalid(f"{where}: 시작 시각이 종료 시각보다 빨라야 합니다.")
        if start < opens or end > closes:
            raise Invalid(f"{where}: 정규장({opens}–{closes}) 안에서만 정할 수 있습니다.")
        if previous_end is not None and start < previous_end:
            raise Invalid(f"{where}: 시작 시각이 앞 세션의 종료 시각보다 이릅니다.")
        previous_end = end
        enabled = item.get("enabled")
        if not isinstance(enabled, bool):
            raise Invalid(f"{where}: 켜짐 여부가 올바르지 않습니다.")
        result.append(
            {
                "start": start,
                "end": end,
                "enabled": enabled,
                **feature.criteria(item, enabled, where),
            }
        )
    return result


def percent(value: Any, bounds: tuple[Decimal, Decimal], name: str) -> str | None:
    """정하지 않았으면 None, 맞으면 받은 문자열을 그대로 돌려준다."""
    if value is None:
        return None
    low, high = bounds
    if (
        not isinstance(value, str)
        or not _PERCENT.fullmatch(value)
        or not low <= Decimal(value) <= high
    ):
        raise Invalid(f"{name}은 {low}~{high} 사이, 소수점 둘째 자리까지의 숫자여야 합니다.")
    return value
