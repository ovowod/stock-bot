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


# 앞의 표시가 우선한다. User-Agent에 Edge는 Chrome을, Chrome은 Safari를,
# iOS는 Mac OS X를 함께 적는다.
_BROWSERS = (
    ("Edg", "Edge"),
    ("Firefox/", "Firefox"),
    ("FxiOS", "Firefox"),
    ("Chrome/", "Chrome"),
    ("CriOS", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("Windows", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)
_UNKNOWN = "알 수 없음"


def describe_device(user_agent: str) -> str:
    """User-Agent에서 브라우저와 OS를 "Chrome / Windows"처럼 간단히 고른다."""
    browser = next((name for mark, name in _BROWSERS if mark in user_agent), _UNKNOWN)
    system = next((name for mark, name in _SYSTEMS if mark in user_agent), _UNKNOWN)
    return f"{browser} / {system}"


@dataclass(frozen=True)
class ClientInfo:
    """접속 정보. 로그인 요청이 온 IP 주소와 User-Agent."""

    ip: str
    user_agent: str

    @property
    def device(self) -> str:
        return describe_device(self.user_agent)

    def log_fields(self) -> dict[str, str]:
        return {
            "ip": self.ip,
            "device": self.device,
            "user_agent": self.user_agent[:_USER_AGENT_LIMIT],
        }


@dataclass(frozen=True)
class LoginResult:
    """session_id가 있으면 성공이다.

    retry_after_seconds가 0보다 크면 IP가 잠겨 있다. locked_now는 이번 실패로 막 잠겼다는 뜻이다.
    """

    session_id: str | None
    failures: int = 0
    retry_after_seconds: int = 0
    locked_now: bool = False


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

    def login(self, password: str, client: ClientInfo) -> LoginResult:
        """맞으면 새 세션 ID를 돌려주고 이전 세션은 버린다.

        잠긴 IP는 비밀번호를 확인하지 않고, 잠긴 동안의 시도는 실패 수에 넣지 않는다.
        """
        record = self._failures.get(client.ip)
        if record is not None and record.locked_until is not None:
            remaining = math.ceil(record.locked_until - self._clock())
            if remaining > 0:
                log(
                    logger,
                    logging.WARNING,
                    "login_rejected_locked",
                    retry_after_seconds=remaining,
                    **client.log_fields(),
                )
                return LoginResult(None, record.count, retry_after_seconds=remaining)
            # 풀린 잠금은 기록째 지워 실패 수를 0부터 다시 센다.
            del self._failures[client.ip]

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
            if record.count < MAX_FAILURES:
                return LoginResult(None, record.count)
            record.locked_until = self._clock() + LOCK_SECONDS
            log(
                logger,
                logging.WARNING,
                "login_locked",
                failures=record.count,
                lock_seconds=LOCK_SECONDS,
                **client.log_fields(),
            )
            return LoginResult(None, record.count, LOCK_SECONDS, locked_now=True)

        self._failures.pop(client.ip, None)
        if self._session is not None:
            # 교체되는 이전 세션의 접속 정보를 남긴다.
            log(logger, logging.INFO, "session_replaced", **self._session.client.log_fields())
        session_id = token_urlsafe(32)
        now = self._clock()
        self._session = _Session(session_id, client, now, now)
        log(logger, logging.INFO, "login_succeeded", **client.log_fields())
        return LoginResult(session_id)

    def authenticate(self, session_id: str | None) -> bool:
        """현재 세션이고 만료되지 않았으면 마지막 요청 시각을 갱신하고 True다. 만료됐으면 지운다."""
        session = self._current(session_id)
        if session is None:
            return False
        now = self._clock()
        if now - session.logged_in_at >= ABSOLUTE_TIMEOUT_SECONDS:
            reason = "absolute"
        elif now - session.last_seen >= IDLE_TIMEOUT_SECONDS:
            reason = "idle"
        else:
            session.last_seen = now
            return True
        log(logger, logging.INFO, "session_expired", reason=reason, **session.client.log_fields())
        self._session = None
        return False

    def logout(self, session_id: str | None) -> None:
        """요청한 쪽의 세션일 때만 끝낸다. 쿠키가 없거나 예전 쿠키면 현재 세션은 그대로 둔다."""
        session = self._current(session_id)
        if session is None:
            return
        log(logger, logging.INFO, "logout", **session.client.log_fields())
        self._session = None

    def _current(self, session_id: str | None) -> _Session | None:
        """session_id가 현재 세션의 것이면 그 세션, 아니면 None이다."""
        if session_id is None or self._session is None:
            return None
        if not hmac.compare_digest(session_id.encode(), self._session.id.encode()):
            return None
        return self._session
