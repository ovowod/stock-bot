"""비밀번호 로그인과 로그인 세션.

로그인 세션은 서버 메모리에 하나만 둔다. 세션 ID는 추측할 수 없는 무작위 값이라
서명할 키가 필요 없고, 서버를 다시 시작하면 사라진다. 비밀번호와 세션 ID는 로그에 남기지 않는다.
"""

import hmac
import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from secrets import token_urlsafe

from stock_bot.logging_setup import log
from stock_bot.masking import secrets

logger = logging.getLogger("stock_bot.auth")

SESSION_COOKIE = "stock_bot_session"
IDLE_TIMEOUT_SECONDS = 30 * 60
ABSOLUTE_TIMEOUT_SECONDS = 12 * 60 * 60
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
_USER_AGENT_LIMIT = 200


@dataclass(frozen=True)
class ClientInfo:
    """접속 정보. 로그인 요청이 온 IP 주소와 User-Agent."""

    ip: str
    user_agent: str

    def log_fields(self) -> dict[str, str]:
        return {"ip": self.ip, "user_agent": self.user_agent[:_USER_AGENT_LIMIT]}


@dataclass
class _Session:
    id: str
    client: ClientInfo
    logged_in_at: float
    last_seen: float


@dataclass
class _Failures:
    """IP 하나의 연속 실패 수와 잠금이 풀리는 시각."""

    count: int = 0
    locked_until: float | None = None


def load_password(environ: Mapping[str, str]) -> str:
    """앞뒤 공백도 비밀번호의 일부로 그대로 쓴다. 비었거나 공백뿐이면 시작하지 않는다."""
    password = environ.get("PASSWORD", "")
    if not password.strip():
        log(logger, logging.ERROR, "config_error", missing=["PASSWORD"])
        raise RuntimeError("PASSWORD 환경변수가 설정되지 않아 서버를 시작하지 않습니다.")
    secrets.add(password)
    return password


class AuthService:
    def __init__(self, password: str, clock: Callable[[], float] = time.monotonic) -> None:
        self._password = password.encode()
        self._clock = clock
        self._session: _Session | None = None
        # 프록시 뒤에 두지 않으므로 X-Forwarded-For가 아니라 연결한 쪽의 IP로 센다.
        self._failures: dict[str, _Failures] = {}

    def locked_seconds(self, client: ClientInfo) -> int:
        """이 IP가 잠겨 있으면 풀릴 때까지 남은 초(올림), 아니면 0이다.

        풀린 잠금은 기록째 지워 실패 수를 0부터 다시 센다.
        """
        record = self._failures.get(client.ip)
        if record is None or record.locked_until is None:
            return 0
        remaining = record.locked_until - self._clock()
        if remaining <= 0:
            del self._failures[client.ip]
            return 0
        log(
            logger,
            logging.WARNING,
            "login_rejected_locked",
            retry_after_seconds=math.ceil(remaining),
            **client.log_fields(),
        )
        return math.ceil(remaining)

    def login(self, password: str, client: ClientInfo) -> str | None:
        """맞으면 새 세션 ID를 돌려주고 이전 세션은 버린다. 틀리면 None이다.

        잠긴 IP는 locked_seconds로 먼저 걸러야 한다. 잠긴 동안의 시도는 실패 수에 넣지 않는다.
        """
        # 문자열을 그대로 compare_digest에 넣으면 ASCII 밖의 글자에서 TypeError가 난다.
        if not hmac.compare_digest(password.encode(), self._password):
            record = self._failures.setdefault(client.ip, _Failures())
            record.count += 1
            log(
                logger,
                logging.WARNING,
                "login_failed",
                failures=record.count,
                **client.log_fields(),
            )
            if record.count >= MAX_FAILURES:
                record.locked_until = self._clock() + LOCK_SECONDS
                log(
                    logger,
                    logging.WARNING,
                    "login_locked",
                    failures=record.count,
                    lock_seconds=LOCK_SECONDS,
                    **client.log_fields(),
                )
            return None
        self._failures.pop(client.ip, None)
        if self._session is not None:
            log(logger, logging.INFO, "session_replaced", previous_ip=self._session.client.ip)
        session_id = token_urlsafe(32)
        now = self._clock()
        self._session = _Session(session_id, client, now, now)
        log(logger, logging.INFO, "login_succeeded", **client.log_fields())
        return session_id

    def authenticate(self, session_id: str | None) -> bool:
        """현재 세션이고 만료되지 않았으면 마지막 요청 시각을 갱신하고 True다. 만료됐으면 지운다."""
        if self._session is None or not self.is_valid(session_id):
            return False
        now = self._clock()
        if now - self._session.logged_in_at >= ABSOLUTE_TIMEOUT_SECONDS:
            reason = "absolute"
        elif now - self._session.last_seen >= IDLE_TIMEOUT_SECONDS:
            reason = "idle"
        else:
            self._session.last_seen = now
            return True
        log(logger, logging.INFO, "session_expired", reason=reason, ip=self._session.client.ip)
        self._session = None
        return False

    def logout(self, session_id: str | None) -> None:
        """요청한 쪽의 세션일 때만 끝낸다. 쿠키가 없거나 예전 쿠키면 현재 세션은 그대로 둔다."""
        if self._session is None or not self.is_valid(session_id):
            return
        log(logger, logging.INFO, "logout", ip=self._session.client.ip)
        self._session = None

    def is_valid(self, session_id: str | None) -> bool:
        if session_id is None or self._session is None:
            return False
        return hmac.compare_digest(session_id.encode(), self._session.id.encode())
