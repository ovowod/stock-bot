"""Trailing Stop 상태 조회: 계좌 확인의 Trailing Stop 보기가 3초마다 부른다.

키움을 부르지 않는다. 설정은 자동매매 설정 파일에서 읽는다.
Trailing Stop 감시는 아직 없어 늘 감시 안 함이고 종목도 없다. 감시가 생기면 _watch_state가
감시의 메모리에서 감시 여부, 이유, 종목을 받는다.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from stock_bot.auto_trading.judging import session_at
from stock_bot.auto_trading.settings import TIMEZONES, FeatureSettingsStore
from stock_bot.core.config import EnvironmentSpec
from stock_bot.core.errors import AppError
from stock_bot.core.logging_setup import log
from stock_bot.kiwoom.kiwoom import KiwoomClient

logger = logging.getLogger("stock_bot.trailing_stop")


class TrailingStopStatus:
    def __init__(
        self,
        kiwoom: KiwoomClient,
        settings: FeatureSettingsStore,
        now: Callable[[], datetime],
    ) -> None:
        # 인증정보가 있는지만 본다. 키움에 요청하지 않는다.
        self._kiwoom = kiwoom
        self._settings = settings
        self._now = now

    def read(self, spec: EnvironmentSpec) -> dict[str, Any]:
        """성공한 조회는 DEBUG로만 남긴다. 3초마다 불려 INFO 로그를 채우지 않게 한다."""
        try:
            settings: dict[str, Any] | None = self._settings.read(spec, quiet=True)
        except AppError:
            # 읽지 못한 원인은 설정 저장소가 ERROR로 남겼다. 조회는 이유를 담아 답한다.
            settings = None
        sessions = [] if settings is None else settings["sessions"]
        found = session_at(sessions, self._now(), spec.market, include_disabled=True)
        session = None if found is None else found[1]
        watching, reason, stocks = self._watch_state(spec, settings is not None)
        excluded = [] if settings is None else [item["code"] for item in settings["excluded"]]
        log(
            logger,
            logging.DEBUG,
            "trailing_stop_status_read",
            watching=watching,
            not_watching_reason=reason,
            session=None if session is None else f"{session['start']}-{session['end']}",
            excluded_count=len(excluded),
            stock_count=len(stocks),
        )
        return {
            "environment": spec.environment.value,
            "market": spec.market.value,
            "timezone": TIMEZONES[spec.market],
            "watching": watching,
            "not_watching_reason": reason,
            "session": session,
            "excluded": excluded,
            "stocks": stocks,
        }

    def _watch_state(
        self, spec: EnvironmentSpec, settings_ok: bool
    ) -> tuple[bool, str | None, list[dict[str, Any]]]:
        """(감시 중인지, 감시 안 함의 이유, 종목들). 실전투자는 감시하지 않는다."""
        if spec.is_real:
            return False, "real", []
        try:
            self._kiwoom.credentials(spec)
        except AppError:
            return False, "no_credentials", []
        if not settings_ok:
            return False, "settings_unreadable", []
        return False, "no_session", []
