"""Dead Cross 상태 조회: 계좌 확인의 Dead Cross 보기가 3초마다 부른다.

키움을 부르지 않는다. 감시 여부·이유·종목과 설정은 감시의 메모리에서 읽는다.
감시하지 않는 환경(실전, 인증정보 없음)만 설정을 자동매매 설정 파일에서 읽는다.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from stock_bot.auto_trading.settings import TIMEZONES, FeatureSettingsStore
from stock_bot.core.config import EnvironmentSpec
from stock_bot.core.errors import AppError
from stock_bot.core.logging_setup import log
from stock_bot.dead_cross.settings import dead_cross_session_at
from stock_bot.dead_cross.watch import DeadCrossWatcher
from stock_bot.kiwoom.kiwoom import KiwoomClient

logger = logging.getLogger("stock_bot.dead_cross")


class DeadCrossStatus:
    def __init__(
        self,
        kiwoom: KiwoomClient,
        settings: FeatureSettingsStore,
        watcher: DeadCrossWatcher,
        now: Callable[[], datetime],
    ) -> None:
        # 인증정보가 있는지만 본다. 키움에 요청하지 않는다.
        self._kiwoom = kiwoom
        self._settings = settings
        self._watcher = watcher
        self._now = now

    def read(self, spec: EnvironmentSpec) -> dict[str, Any]:
        """성공한 조회는 DEBUG로만 남긴다. 3초마다 불려 INFO 로그를 채우지 않게 한다.

        감시하는 환경은 감시가 쓰고 있는 설정으로 답한다. 실행 중에 설정 파일이 깨져도 감시는
        시작·저장 때 읽은 설정으로 계속 판정하므로, 파일을 따로 읽으면 화면이 감시와 어긋난다.
        """
        state = self._watcher.state(spec)
        recent_sells: list[dict[str, Any]] = []
        if state is None:
            watching, reason, stocks, sessions, excluded = self._unwatched(spec)
        else:
            watching, reason, stocks = state.watching, state.not_watching_reason, state.stocks
            sessions, excluded = state.sessions or [], state.excluded
            recent_sells = state.recent_sells
        found = dead_cross_session_at(sessions, self._now(), spec.market, include_disabled=True)
        session = None if found is None else found[1]
        log(
            logger,
            logging.DEBUG,
            "dead_cross_status_read",
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
            "recent_sells": recent_sells,
        }

    def _unwatched(
        self, spec: EnvironmentSpec
    ) -> tuple[bool, str | None, list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        """감시하지 않는 환경(실전, 인증정보 없음, 서버 시작 전). 설정은 파일에서 읽는다."""
        try:
            settings: dict[str, Any] | None = self._settings.read(spec, quiet=True)
        except AppError:
            # 읽지 못한 원인은 설정 저장소가 ERROR로 남겼다. 조회는 이유를 담아 답한다.
            settings = None
        if spec.is_real:
            reason = "real"
        else:
            try:
                self._kiwoom.credentials(spec)
                reason = "no_session" if settings is not None else "settings_unreadable"
            except AppError:
                reason = "no_credentials"
        if settings is None:
            return False, reason, [], [], []
        excluded = [item["code"] for item in settings["excluded"]]
        return False, reason, [], settings["sessions"], excluded
