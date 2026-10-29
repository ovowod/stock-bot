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

from stock_bot.core.config import Credentials, EnvironmentSpec, load_credentials
from stock_bot.core.errors import AppError, response_format_error
from stock_bot.core.logging_setup import log
from stock_bot.core.masking import secrets

logger = logging.getLogger("stock_bot.kiwoom")

KST = ZoneInfo("Asia/Seoul")
TOKEN_EXPIRY_MARGIN = timedelta(minutes=5)
CONTENT_TYPE = "application/json;charset=UTF-8"
MAX_PAGES = 10
_LOG_BODY_LIMIT = 200
# 키움 공식 기준(openapi.kiwoom.com/intro "API 호출 횟수 제한")의 한도는 "1초당"이지만
# 모의 서버가 1.002초 간격을 1700으로 거부한 적이 있어 0.2초 여유를 둔다.
CALL_INTERVAL_SECONDS = 1.2
# 이보다 오래 기다려야 하면 보내지 않고 호출 한도 오류로 돌려준다. 화면이 오래 멈춰 있지 않게 한다.
# 화면을 붙잡지 않는 서버 작업(자동 매도)은 patient로 불러 이 한도 없이 차례를 기다린다.
MAX_PACING_WAIT_SECONDS = 5.0

INVALID_TOKEN = 8005
CREDENTIAL_MISMATCH = {8030, 8031}
RATE_LIMITED = {1700, 1701, 1702}
# 실제 모의 서버는 return_code=5와 함께 메시지 안에 "[1700:...]"처럼 문서의 오류 코드를 넣어 보냈다.
_EMBEDDED_CODE = re.compile(r"\[(\d{4}):")

# 키움 문서 "주문" 메뉴의 TR. 나머지는 실전 한도에서 조회로 센다.
ORDER_TR_IDS = frozenset({"kt10000", "kt10001", "kt10003", "ust20000", "ust20001", "ust20003"})
# 주문 허용이 꺼진 투자 환경에서 보내지 않는 TR. 키움 문서에서 돈이나 주문 상태를 바꾸는 TR 전부다.
# 같은 주소에 조회 TR이 섞여 있어(ust21204·ust31300 등) 주소가 아니라 TR로 막는다.
MONEY_MOVING_TR_IDS = frozenset(
    {
        # 국내 주문(/api/dostk/ordr): 매수·매도·정정·취소, 금현물 매수·매도·정정·취소
        "kt10000",
        "kt10001",
        "kt10002",
        "kt10003",
        "kt50000",
        "kt50001",
        "kt50002",
        "kt50003",
        # 국내 신용주문(/api/dostk/crdordr): 신용 매수·매도·정정·취소
        "kt10006",
        "kt10007",
        "kt10008",
        "kt10009",
        # 미국 주문(/api/us/ordr): 매수·매도·정정·취소, 예약 매수·매도·취소
        "ust20000",
        "ust20001",
        "ust20002",
        "ust20003",
        "ust21200",
        "ust21201",
        "ust21203",
        # 미국 환전(/api/us/exchange): 환전 신청
        "ust31302",
    }
)
# 실전 묶음별 1초당 한도: (일반, 피크타임). 피크타임 한도는 미국만 있다.
REAL_CALL_LIMITS: dict[str, tuple[int, int | None]] = {
    "domestic_order": (5, None),
    "domestic_inquiry": (5, None),
    "us_order": (10, 3),
    "us_inquiry": (5, 3),
}
# 미국 실전 피크타임: 매일 한국시간 09:00:00~09:59:59.
PEAK_HOUR = 9
# 묶음 한도와 별도로 TR 하나에 걸린 1초당 한도. 묶음 한도도 함께 지킨다.
# kt00018: 2026-10-29 18시부터 1초 5회에서 3회로 줄었다(키움 공지 2026-10-27).
TR_CALL_LIMITS: dict[str, int] = {"kt00018": 3}


@dataclass(frozen=True)
class _Token:
    value: str
    expires_at: datetime


@dataclass(frozen=True)
class _Unit:
    """호출 한도를 함께 세는 단위. name은 로그의 pacing_unit이다."""

    key: tuple[str, str]
    name: str
    limit: int
    peak_limit: int | None = None

    def limit_at(self, at: datetime) -> int:
        return self.peak_limit if self.is_peak(at) and self.peak_limit else self.limit

    def is_peak(self, at: datetime) -> bool:
        return self.peak_limit is not None and at.hour == PEAK_HOUR


def _pacing_unit(spec: EnvironmentSpec, api_id: str) -> _Unit:
    """모의투자와 토큰 발급은 TR별 1회, 실전은 (TR의 시장, 요청 구분) 묶음으로 센다."""
    if not spec.is_real or api_id == "au10001":
        return _Unit((spec.credential_prefix, api_id), "tr", 1)
    # 미국 계좌 확인이 부르는 ka00001처럼 투자 환경이 아니라 TR로 시장을 정한다.
    market = "us" if api_id.startswith(("ust", "usa")) else "domestic"
    name = f"{market}_{'order' if api_id in ORDER_TR_IDS else 'inquiry'}"
    return _Unit((spec.credential_prefix, name), name, *REAL_CALL_LIMITS[name])


@dataclass(eq=False)
class _Slot:
    """한 요청이 보내기로 예약한 시각. 보낸 뒤에는 실제로 보낸 시각이 된다.

    시각이 같은 자리도 서로 다른 요청이므로 목록에서 객체 자체로 찾는다(eq=False).
    """

    at: float
    api_id: str
    sent: bool = False


class _Pacer:
    """단위마다 최근 CALL_INTERVAL_SECONDS 동안 보낸 요청이 한도를 넘지 않게 내보낸다.

    요청은 줄에 들어올 때 보낼 시각을 예약하므로 먼저 온 요청이 먼저 나간다.
    대기 한도는 이때 한 번만 판단한다. 연속조회 다음 페이지도 한도에 세어지므로 같은 줄에 선다.
    한도는 보낼 시각의 한국시간으로 정하므로 줄 서 있던 요청도 피크타임이 되면 낮은 한도를 따른다.
    """

    def __init__(
        self,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        kst_now: Callable[[], datetime],
    ):
        self._clock = clock
        self._sleep = sleep
        self._kst_now = kst_now
        self._slots: dict[tuple[str, str], list[_Slot]] = {}

    async def wait(self, unit: _Unit, api_id: str, patient: bool = False) -> None:
        start = self._clock()
        slots = self._slots.setdefault(unit.key, [])
        # 기준 시간이 지난 기록은 더 이상 한도에 세어지지 않는다.
        while slots and slots[0].sent and slots[0].at + CALL_INTERVAL_SECONDS <= start:
            slots.pop(0)
        at = self._earliest(unit, slots, start, api_id)
        if not patient and at - start > MAX_PACING_WAIT_SECONDS:
            log(
                logger,
                logging.WARNING,
                "kiwoom_pacing_rejected",
                api_id=api_id,
                wait_ms=round((at - start) * 1000),
                **self._unit_fields(unit, start, at),
            )
            raise AppError(
                "rate_limited",
                "키움 API 호출 한도를 넘었습니다. 잠시 후 다시 시도하세요.",
                429,
                {"api_id": api_id},
            )
        slot = _Slot(at, api_id)
        slots.append(slot)
        waited = False
        try:
            # 앞 요청이 예약보다 늦게 나갔거나 그사이 한도가 낮아졌으면 다시 간격을 채운다.
            while True:
                slot.at = self._earliest_for(unit, slot)
                if (delay := slot.at - self._clock()) <= 0:
                    break
                waited = True
                await self._sleep(delay)
        except BaseException:
            # 기다리다 취소된 요청은 보내지 않으므로 자리를 비운다.
            slots.remove(slot)
            raise
        slot.at, slot.sent = self._clock(), True
        if waited:
            log(
                logger,
                logging.INFO,
                "kiwoom_pacing_wait",
                api_id=api_id,
                wait_ms=round((slot.at - start) * 1000),
                **self._unit_fields(unit, start, slot.at),
            )

    def _earliest_for(self, unit: _Unit, slot: _Slot) -> float:
        slots = self._slots[unit.key]
        before = slots[: slots.index(slot)]
        return self._earliest(unit, before, max(slot.at, self._clock()), slot.api_id)

    def _earliest(self, unit: _Unit, before: list[_Slot], start: float, api_id: str) -> float:
        """앞 요청(before) 뒤에, start 이후 한도에 자리가 나는 가장 이른 시각."""
        at = max(start, before[-1].at) if before else start
        tr_limit = TR_CALL_LIMITS.get(api_id)
        same_tr = [s for s in before if s.api_id == api_id]
        while True:
            kst = self._kst(at)
            limit = unit.limit_at(kst)
            if len(before) < limit or (ready := before[-limit].at + CALL_INTERVAL_SECONDS) <= at:
                if (
                    tr_limit is None
                    or len(same_tr) < tr_limit
                    or (ready := same_tr[-tr_limit].at + CALL_INTERVAL_SECONDS) <= at
                ):
                    return at
            elif unit.is_peak(kst):
                # 피크타임이 끝나면 한도가 올라가므로 그 시각에 자리가 날 수 있다.
                peak_end = kst.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                ready = min(ready, at + (peak_end - kst).total_seconds())
            at = ready

    def _kst(self, at: float) -> datetime:
        return self._kst_now() + timedelta(seconds=at - self._clock())

    def _unit_fields(self, unit: _Unit, start: float, at: float) -> dict[str, Any]:
        """줄에 들어온 시각이나 보낼 시각이 피크타임이면 피크타임 한도를 쓴 것으로 본다."""
        peak = unit.is_peak(self._kst(start)) or unit.is_peak(self._kst(at))
        return {"pacing_unit": unit.name} | ({"peak": True} if peak else {})


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
        kst_now: Callable[[], datetime] = lambda: datetime.now(KST),
    ) -> None:
        self._environ = environ
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)
        self._pacer = _Pacer(clock, sleep, kst_now)
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
        self,
        spec: EnvironmentSpec,
        api_id: str,
        path: str,
        body: dict[str, Any],
        patient: bool = False,
    ) -> dict[str, Any]:
        """TR을 호출한다. 연속조회가 있으면 최대 MAX_PAGES까지 목록 필드를 이어 붙인다.

        patient면 호출 한도 대기에 MAX_PACING_WAIT_SECONDS 제한을 두지 않는다.
        """
        merged: dict[str, Any] = {}
        cont_yn, next_key = "N", ""
        for page_no in range(1, MAX_PAGES + 1):
            page = await self._call_page(
                spec, api_id, path, body, cont_yn, next_key, page_no, patient
            )
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
        self,
        spec: EnvironmentSpec,
        api_id: str,
        path: str,
        body: dict[str, Any],
        ready: Callable[[], bool] | None = None,
        patient: bool = False,
    ) -> dict[str, Any]:
        """주문처럼 다시 보내면 안 되는 TR을 한 번만 호출한다.

        토큰 무효(8005)도 재시도하지 않는다. 다음 호출이 새 토큰을 받도록 그 토큰만 버린다.
        ready를 주면 토큰 발급과 호출 한도 대기가 모두 끝난 뒤 보내기 바로 앞에서 부른다.
        False면 보내지 않고 cancelled_before_send 오류를 낸다.
        """
        token = await self._token(spec)
        headers = {
            "api-id": api_id,
            "authorization": f"Bearer {token.value}",
            "Content-Type": CONTENT_TYPE,
        }
        try:
            page = await self._post(spec, api_id, path, body, headers, 1, ready, patient)
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
        patient: bool = False,
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
            try:
                return await self._post(spec, api_id, path, body, headers, page_no, patient=patient)
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
        ready: Callable[[], bool] | None = None,
        patient: bool = False,
    ) -> _Page:
        url = f"{spec.domain}{path}"
        # 주문 허용이 꺼진 환경을 막는 유일한 관문. 모든 호출이 여기를 지나 보내지기 전에 막힌다.
        if api_id in MONEY_MOVING_TR_IDS and not spec.orders_allowed:
            log(logger, logging.WARNING, "kiwoom_order_blocked", api_id=api_id, target=url)
            raise AppError(
                "order_not_allowed", "실전투자에서는 주문할 수 없습니다.", 403, {"api_id": api_id}
            )
        await self._pacer.wait(_pacing_unit(spec, api_id), api_id, patient)
        if ready is not None and not ready():
            # 호출 한도 자리는 이미 썼지만 보내지 않았다. 한도를 넘길 일은 없으므로 되돌리지 않는다.
            log(logger, logging.WARNING, "kiwoom_request_cancelled", api_id=api_id, target=url)
            raise AppError(
                "cancelled_before_send",
                "보내기 직전 확인에서 요청을 보내지 않았습니다.",
                409,
                {"api_id": api_id},
            )
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
                # 자른 뒤에는 비밀값을 알아볼 수 없으므로 먼저 가린다.
                body_head=secrets.mask(response.text)[:_LOG_BODY_LIMIT],
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
