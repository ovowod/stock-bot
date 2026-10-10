"""키움 REST API 클라이언트.

투자 환경에 맞는 도메인·인증정보 선택, 접근 토큰 발급과 캐시, 공통 헤더, 연속조회,
결과 코드 해석, 호출 간격 맞추기, 요청 로그를 이 모듈 안에서 처리한다.
호출하는 쪽은 TR과 요청 본문만 넘긴다.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from stock_bot.config import Credentials, EnvironmentSpec, load_credentials
from stock_bot.errors import AppError, response_format_error
from stock_bot.logging_setup import log
from stock_bot.masking import secrets

logger = logging.getLogger("stock_bot.kiwoom")

KST = ZoneInfo("Asia/Seoul")
TOKEN_EXPIRY_MARGIN = timedelta(minutes=5)
CONTENT_TYPE = "application/json;charset=UTF-8"
MAX_PAGES = 10
_LOG_BODY_LIMIT = 200
# 같은 인증정보로 같은 TR을 1초 안에 다시 부르면 호출 한도(1700)에 걸렸다.
# 문서에는 한도 숫자가 없다.
CALL_INTERVAL_SECONDS = 1.0
# 이보다 오래 기다려야 하면 보내지 않고 호출 한도 오류로 돌려준다. 화면이 오래 멈춰 있지 않게 한다.
MAX_PACING_WAIT_SECONDS = 5.0

INVALID_TOKEN = 8005
CREDENTIAL_MISMATCH = {8030, 8031}
RATE_LIMITED = {1700, 1701, 1702}
# 실제 모의 서버는 return_code=5와 함께 메시지 안에 "[1700:...]"처럼 문서의 오류 코드를 넣어 보냈다.
_EMBEDDED_CODE = re.compile(r"\[(\d{4}):")


@dataclass(frozen=True)
class _Token:
    value: str
    expires_at: datetime


class _Pacer:
    """같은 (인증정보 묶음, TR)의 요청을 CALL_INTERVAL_SECONDS 간격으로 내보낸다.

    요청은 줄에 들어올 때 보낼 시각을 예약하므로 먼저 온 요청이 먼저 나간다. 5초 한도는 이때 한 번만
    판단한다. 차례가 와도 그사이 연속조회 페이지가 나갔으면 그 시각에서 다시 간격을 채운다.
    """

    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]]):
        self._clock = clock
        self._sleep = sleep
        self._last_sent: dict[tuple[str, str], float] = {}
        self._next_slot: dict[tuple[str, str], float] = {}

    async def wait(self, key: tuple[str, str]) -> None:
        api_id = key[1]
        start = self._clock()
        slot = max(start, self._next_slot.get(key, start), self._earliest(key))
        if slot - start > MAX_PACING_WAIT_SECONDS:
            log(
                logger,
                logging.WARNING,
                "kiwoom_pacing_rejected",
                api_id=api_id,
                wait_ms=round((slot - start) * 1000),
            )
            raise AppError(
                "rate_limited",
                "키움 API 호출 한도를 넘었습니다. 잠시 후 다시 시도하세요.",
                429,
                {"api_id": api_id},
            )
        self._next_slot[key] = slot + CALL_INTERVAL_SECONDS
        waited = False
        while (delay := max(slot, self._earliest(key)) - self._clock()) > 0:
            waited = True
            await self._sleep(delay)
        if waited:
            log(
                logger,
                logging.INFO,
                "kiwoom_pacing_wait",
                api_id=api_id,
                wait_ms=round((self._clock() - start) * 1000),
            )

    def sent(self, key: tuple[str, str]) -> None:
        self._last_sent[key] = self._clock()

    def _earliest(self, key: tuple[str, str]) -> float:
        return self._last_sent.get(key, float("-inf")) + CALL_INTERVAL_SECONDS


@dataclass(frozen=True)
class _Page:
    data: dict[str, Any]
    cont_yn: str
    next_key: str


class _KiwoomResult(Exception):
    def __init__(self, api_id: str, return_code: Any, message: str) -> None:
        super().__init__(message)
        self.api_id = api_id
        self.return_code = return_code
        self.message = message
        embedded = _EMBEDDED_CODE.search(message)
        # 분류에는 메시지에 든 문서상 오류 코드를 우선 쓰고, 없으면 return_code를 쓴다.
        self.code = int(embedded.group(1)) if embedded else return_code


class KiwoomClient:
    def __init__(
        self,
        environ: Mapping[str, str],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._environ = environ
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)
        self._pacer = _Pacer(clock, sleep)
        # 토큰은 인증정보 묶음별로 보관한다. 국내 실전과 미국 실전은 같은 앱 키를 쓰므로
        # 토큰도 공유해야 서로의 발급이 상대 토큰을 무효로 만들 여지가 없다.
        self._tokens: dict[str, _Token] = {}
        self._token_locks: dict[str, asyncio.Lock] = {}

    def credentials(self, spec: EnvironmentSpec) -> Credentials:
        credentials = load_credentials(spec, self._environ)
        secrets.add(*credentials.secrets())
        return credentials

    async def access_token(self, spec: EnvironmentSpec) -> str:
        """REST 호출과 같은 캐시의 접근 토큰. 실시간 연결의 LOGIN에 쓴다."""
        return (await self._token(spec)).value

    async def discard_access_token(self, spec: EnvironmentSpec, value: str) -> None:
        """실시간 LOGIN이 거부한 토큰을 버린다. 그사이 새로 받은 토큰은 지우지 않는다."""
        cached = self._tokens.get(spec.credential_prefix)
        if cached is not None and cached.value == value:
            await self._discard_token(spec, cached)

    async def call(
        self, spec: EnvironmentSpec, api_id: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """TR을 호출한다. 연속조회가 있으면 최대 MAX_PAGES까지 목록 필드를 이어 붙인다."""
        merged: dict[str, Any] = {}
        cont_yn, next_key = "N", ""
        for page_no in range(1, MAX_PAGES + 1):
            page = await self._call_page(spec, api_id, path, body, cont_yn, next_key, page_no)
            # 앞 페이지에서 목록이던 필드가 빠지거나 목록이 아니면,
            # 일부만 받은 결과를 돌려주지 않고 응답 형식 오류로 처리한다.
            for key, value in merged.items():
                if isinstance(value, list) and not isinstance(page.data.get(key), list):
                    raise response_format_error(
                        api_id, f"{page_no}페이지의 {key} 필드가 목록이 아닙니다."
                    )
            for key, value in page.data.items():
                if isinstance(value, list) and isinstance(merged.get(key), list):
                    merged[key].extend(value)
                elif key not in merged:
                    merged[key] = value
            if page.cont_yn != "Y":
                return merged
            cont_yn, next_key = "Y", page.next_key
        log(logger, logging.WARNING, "kiwoom_incomplete", api_id=api_id, pages=MAX_PAGES)
        raise AppError(
            "incomplete_result",
            f"조회 결과가 {MAX_PAGES}페이지를 넘어 전체를 가져오지 못했습니다.",
            502,
            {"api_id": api_id},
        )

    async def call_first_page(
        self, spec: EnvironmentSpec, api_id: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """TR의 첫 페이지만 받는다. 다음 데이터가 있어도(cont-yn=Y) 이어 받지 않는다."""
        page = await self._call_page(spec, api_id, path, body, "N", "", 1)
        return page.data

    async def call_once(
        self, spec: EnvironmentSpec, api_id: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """주문처럼 다시 보내면 안 되는 TR을 한 번만 호출한다.

        토큰 무효(8005)도 재시도하지 않는다. 다음 호출이 새 토큰을 받도록 그 토큰만 버린다.
        """
        token = await self._token(spec)
        headers = {
            "api-id": api_id,
            "authorization": f"Bearer {token.value}",
            "Content-Type": CONTENT_TYPE,
        }
        try:
            page = await self._post(spec, api_id, path, body, headers, 1)
        except _KiwoomResult as result:
            if result.code == INVALID_TOKEN:
                await self._discard_token(spec, token)
                log(logger, logging.WARNING, "kiwoom_token_discarded", api_id=api_id)
                raise AppError(
                    "token_expired",
                    "토큰이 만료되어 주문하지 못했습니다. 다시 주문하세요.",
                    502,
                    {"api_id": api_id, "return_code": result.code},
                ) from None
            raise _classify(result) from None
        return page.data

    async def _discard_token(self, spec: EnvironmentSpec, token: _Token) -> None:
        """그 토큰이 아직 캐시에 있을 때만 지운다. 그사이 새로 받은 토큰은 지우지 않는다."""
        key = spec.credential_prefix
        async with self._token_locks.setdefault(key, asyncio.Lock()):
            if self._tokens.get(key) == token:
                del self._tokens[key]

    async def _call_page(
        self,
        spec: EnvironmentSpec,
        api_id: str,
        path: str,
        body: dict[str, Any],
        cont_yn: str,
        next_key: str,
        page_no: int,
    ) -> _Page:
        token = await self._token(spec)
        for attempt in (1, 2):
            headers = {
                "api-id": api_id,
                "authorization": f"Bearer {token.value}",
                "cont-yn": cont_yn,
                "next-key": next_key,
                "Content-Type": CONTENT_TYPE,
            }
            # 8005 뒤 다시 보내는 요청은 연속조회 페이지라도 새 요청으로 보고 간격을 지킨다.
            paced = cont_yn != "Y" or attempt == 2
            try:
                return await self._post(spec, api_id, path, body, headers, page_no, paced)
            except _KiwoomResult as result:
                if result.code != INVALID_TOKEN or attempt == 2:
                    raise _classify(result) from None
                log(logger, logging.WARNING, "kiwoom_retry_invalid_token", api_id=api_id)
                token = await self._token(spec, invalid=token)
        raise AssertionError("unreachable")

    async def _token(self, spec: EnvironmentSpec, invalid: _Token | None = None) -> _Token:
        """캐시된 토큰을 돌려주거나 새로 발급한다. 같은 인증정보의 발급은 lock으로 하나만 진행한다.

        invalid가 주어지면 그 토큰이 아직 캐시에 있을 때만 지운다. 늦게 도착한 8005가
        그사이 다른 요청이 새로 받은 토큰을 지우지 않게 하기 위해서다.
        """
        key = spec.credential_prefix
        lock = self._token_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._tokens.get(key)
            if invalid is not None and cached == invalid:
                del self._tokens[key]
                cached = None
            if cached and datetime.now(KST) < cached.expires_at - TOKEN_EXPIRY_MARGIN:
                return cached
            token = await self._issue_token(spec)
            self._tokens[key] = token
            return token

    async def _issue_token(self, spec: EnvironmentSpec) -> _Token:
        credentials = self.credentials(spec)
        body = {
            "grant_type": "client_credentials",
            "appkey": credentials.app_key,
            "secretkey": credentials.app_secret,
        }
        try:
            page = await self._post(
                spec, "au10001", "/oauth2/token", body, {"Content-Type": CONTENT_TYPE}, 1
            )
        except _KiwoomResult as result:
            raise _classify(result) from None
        value = page.data.get("token")
        if not isinstance(value, str) or not value:
            raise response_format_error("au10001", "token 필드가 없습니다.")
        secrets.add(value)
        token = _Token(value, _parse_expires_dt(page.data.get("expires_dt")))
        log(
            logger,
            logging.INFO,
            "token_issued",
            api_id="au10001",
            expires_at=token.expires_at.isoformat(),
        )
        return token

    async def _post(
        self,
        spec: EnvironmentSpec,
        api_id: str,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str],
        page_no: int,
        paced: bool = True,
    ) -> _Page:
        """paced가 False면(연속조회 다음 페이지) 기다리지 않고 보내되, 보낸 시각은 기록한다."""
        url = f"{spec.domain}{path}"
        key = (spec.credential_prefix, api_id)
        if paced:
            await self._pacer.wait(key)
        self._pacer.sent(key)
        log(logger, logging.INFO, "kiwoom_request", api_id=api_id, target=url, page=page_no)
        started = time.perf_counter()
        try:
            response = await self._http.post(url, content=json.dumps(body), headers=headers)
        except httpx.HTTPError as exc:
            log(
                logger,
                logging.ERROR,
                "kiwoom_connection_failed",
                api_id=api_id,
                target=url,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            raise AppError(
                "connection_error", "키움 서버에 연결하지 못했습니다.", 502, {"api_id": api_id}
            ) from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or "return_code" not in data:
            log(
                logger,
                logging.ERROR,
                "kiwoom_response_malformed",
                api_id=api_id,
                target=url,
                http_status=response.status_code,
                body_head=response.text[:_LOG_BODY_LIMIT],
                elapsed_ms=elapsed_ms,
            )
            raise response_format_error(api_id, f"HTTP {response.status_code}, return_code 없음")

        return_code = data["return_code"]
        return_msg = str(data.get("return_msg", ""))
        cont_yn = response.headers.get("cont-yn", "N")
        log(
            logger,
            logging.INFO if return_code == 0 else logging.WARNING,
            "kiwoom_response",
            api_id=api_id,
            target=url,
            http_status=response.status_code,
            return_code=return_code,
            return_msg=return_msg,
            cont_yn=cont_yn,
            elapsed_ms=elapsed_ms,
        )
        if return_code != 0:
            raise _KiwoomResult(api_id, return_code, return_msg)
        return _Page(data, cont_yn, response.headers.get("next-key", ""))


def _classify(result: _KiwoomResult) -> AppError:
    detail = {"api_id": result.api_id, "return_code": result.code}
    if result.code in CREDENTIAL_MISMATCH:
        return AppError(
            "config_error",
            f"실전/모의 인증정보가 맞지 않습니다. [{result.code}] {result.message}",
            503,
            detail,
        )
    if result.code in RATE_LIMITED:
        return AppError(
            "rate_limited",
            f"키움 API 호출 한도를 넘었습니다. 잠시 후 다시 시도하세요. [{result.code}]",
            429,
            detail,
        )
    return AppError("kiwoom_error", f"키움 오류 [{result.code}] {result.message}", 502, detail)


def _parse_expires_dt(value: Any) -> datetime:
    """expires_dt(YYYYMMDDHHMMSS)를 한국시간으로 해석한다.

    키움 문서에 시간대가 없다. 실제가 UTC라면 한국시간 해석은 9시간 일찍 재발급할 뿐이지만,
    반대로 UTC로 해석했는데 실제가 한국시간이면 만료된 토큰을 쓰게 된다. 틀려도 안전한 쪽을 택했다.
    """
    try:
        return datetime.strptime(str(value), "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        raise response_format_error("au10001", "expires_dt 형식이 올바르지 않습니다.") from None
