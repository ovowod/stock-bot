"""FastAPI 앱. 브라우저는 이 서버의 /api만 호출하고, 키움 호출은 서버 안에서만 일어난다."""

import asyncio
import logging
import math
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from stock_bot.account import AccountService
from stock_bot.auth import SESSION_COOKIE, AuthService, ClientInfo, load_password
from stock_bot.config import ENVIRONMENTS, LOG_DIR, PROJECT_ROOT, load_env_file, parse_environment
from stock_bot.errors import AppError
from stock_bot.fill_watch import FillWatcher, RealtimeConnect, connect_websocket
from stock_bot.kiwoom import KST, KiwoomClient
from stock_bot.logging_setup import environment_var, log, request_id_var, setup_logging
from stock_bot.masking import secrets
from stock_bot.notification import DiscordNotifier, Embed, EmbedField
from stock_bot.order import OrderService
from stock_bot.quote import QuoteService
from stock_bot.ranking import RankingService
from stock_bot.stock_search import StockSearchService, today_kst

logger = logging.getLogger("stock_bot.api")

WEB_DIST = PROJECT_ROOT / "web" / "dist"
# 로그인하지 않아도 부를 수 있는 API. 그 밖의 /api는 로그인 세션이 있어야 한다.
PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/session"}


def create_app(
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    static_dir: Path | None = WEB_DIST,
    log_dir: Path | None = LOG_DIR,
    today: Callable[[], date] | None = None,
    clock: Callable[[], float] | None = None,
    notifier: DiscordNotifier | None = None,
    realtime_connect: RealtimeConnect | None = None,
) -> FastAPI:
    setup_logging(log_dir)
    if environ is None:
        load_env_file()
        environ = os.environ

    auth = AuthService(load_password(environ), clock or time.monotonic)
    notifier = notifier or DiscordNotifier(environ)
    # 보내는 중인 알림. 참조를 잡아 두지 않으면 작업이 끝나기 전에 정리될 수 있다.
    # 서버가 꺼질 때 남은 알림은 버린다.
    notifications: set[asyncio.Task[bool]] = set()

    def notify(embed: Embed) -> None:
        """로그인 응답이 알림을 기다리지 않게 따로 보낸다. 실패는 notifier가 로그로 남긴다."""
        if not notifier.enabled:
            return
        task = asyncio.create_task(notifier.send(embed=embed))
        notifications.add(task)
        task.add_done_callback(notifications.discard)

    kiwoom = KiwoomClient(environ, transport=transport)
    rankings = RankingService(kiwoom)
    stocks = StockSearchService(kiwoom, today or today_kst)
    accounts = AccountService(kiwoom, stocks.listings)
    quotes = QuoteService(kiwoom)
    orders = OrderService(kiwoom, accounts.holding)
    fills = FillWatcher(kiwoom, notifier, realtime_connect or connect_websocket)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        fills.start()
        try:
            yield
        finally:
            await fills.stop()

    app = FastAPI(
        title="Stock Bot", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    # 아래 request_context보다 먼저 등록해야 그 안쪽에서 돈다.
    # 그래야 거부 응답에도 요청 ID가 붙는다.
    @app.middleware("http")
    async def require_login(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if request.method == "POST" and not _same_origin(request):
            log(
                logger,
                logging.WARNING,
                "origin_rejected",
                path=path,
                origin=request.headers.get("origin"),
                **_client_info(request).log_fields(),
            )
            return _error_response(
                403, "forbidden_origin", "다른 사이트에서 온 요청은 받지 않습니다.", {}
            )
        if (
            path.startswith("/api")
            and path not in PUBLIC_API_PATHS
            and not auth.authenticate(request.cookies.get(SESSION_COOKIE))
        ):
            log(
                logger,
                logging.WARNING,
                "unauthorized_request",
                path=path,
                **_client_info(request).log_fields(),
            )
            return _error_response(401, "unauthorized", "로그인이 필요합니다.", {})
        return await call_next(request)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid.uuid4().hex[:12]
        request_id_var.set(request_id)
        environment_var.set("-")
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/api"):
            log(
                logger,
                logging.INFO,
                "api_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        log(
            logger,
            logging.WARNING,
            "api_error",
            path=request.url.path,
            kind=exc.kind,
            status=exc.status,
            cause=exc.message,
            detail=exc.detail,
        )
        return _error_response(exc.status, exc.kind, exc.message, exc.detail)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unexpected_error",
            exc_info=exc,
            extra={"fields": {"path": request.url.path, "error_type": type(exc).__name__}},
        )
        return _error_response(500, "internal_error", "서버 내부 오류가 발생했습니다.", {})

    @app.post("/api/auth/login")
    async def login(request: Request) -> Response:
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        password = payload.get("password") if isinstance(payload, dict) else None
        if not isinstance(password, str):
            # 보낸 값은 응답과 로그에 담지 않는다. 비밀번호가 들어 있을 수 있다.
            raise AppError("invalid_request", "요청 형식이 올바르지 않습니다.", 400)
        client = _client_info(request)
        result = auth.login(password, client)
        now = datetime.now(KST)
        if result.locked_now:
            until = now + timedelta(seconds=result.retry_after_seconds)
            notify(
                _access_embed(
                    "로그인 시도 제한",
                    _LOCK_COLOR,
                    client,
                    [
                        EmbedField("연속 실패", f"{result.failures}회", inline=True),
                        EmbedField("풀리는 시각", _kst(until), inline=True),
                    ],
                )
            )
        if result.retry_after_seconds:
            minutes = math.ceil(result.retry_after_seconds / 60)
            raise AppError(
                "login_locked",
                f"로그인 시도가 너무 많습니다. {minutes}분 후 다시 시도하세요.",
                429,
                {"retry_after_seconds": result.retry_after_seconds},
            )
        if result.session_id is None:
            raise AppError("invalid_password", "비밀번호가 올바르지 않습니다.", 401)
        notify(_access_embed("로그인", _LOGIN_COLOR, client, [EmbedField("시각", _kst(now))]))
        response = Response(status_code=204)
        # Max-Age·Expires를 두지 않아 브라우저를 닫으면 사라진다.
        response.set_cookie(SESSION_COOKIE, result.session_id, **_cookie_options(request))
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> Response:
        auth.logout(request.cookies.get(SESSION_COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, **_cookie_options(request))
        return response

    @app.get("/api/auth/session")
    async def get_session(request: Request) -> dict[str, Any]:
        if not auth.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise AppError("unauthorized", "로그인이 필요합니다.", 401)
        return {"authenticated": True}

    @app.get("/api/environments")
    async def list_environments() -> list[dict[str, Any]]:
        return [
            {
                "value": spec.environment.value,
                "label": spec.label,
                "market": spec.market.value,
                "is_real": spec.is_real,
            }
            for spec in ENVIRONMENTS.values()
        ]

    @app.get("/api/environments/{environment}/account")
    async def get_account(environment: str) -> dict[str, Any]:
        spec = parse_environment(environment)
        environment_var.set(spec.environment.value)
        return await accounts.fetch(spec)

    @app.get("/api/environments/{environment}/holdings/{code}")
    async def get_holding(environment: str, code: str) -> dict[str, Any]:
        spec = parse_environment(environment)
        environment_var.set(spec.environment.value)
        holding = await accounts.holding(spec, code)
        return {**holding, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds")}

    @app.get("/api/environments/{environment}/rankings/{kind}")
    async def get_ranking(
        environment: str, kind: str, exchange: str | None = None, period: str | None = None
    ) -> dict[str, Any]:
        spec = parse_environment(environment)
        environment_var.set(spec.environment.value)
        return await rankings.fetch(spec, kind, {"exchange": exchange, "period": period})

    @app.get("/api/environments/{environment}/stocks")
    async def search_stocks(environment: str, q: str | None = None) -> dict[str, Any]:
        spec = parse_environment(environment)
        environment_var.set(spec.environment.value)
        return await stocks.search(spec, q)

    @app.get("/api/environments/{environment}/quote")
    async def get_quote(
        environment: str, code: str | None = None, exchange: str | None = None
    ) -> dict[str, Any]:
        spec = parse_environment(environment)
        environment_var.set(spec.environment.value)
        return await quotes.fetch(spec, code, exchange)

    @app.post("/api/environments/{environment}/orders")
    async def place_order(environment: str, request: Request) -> dict[str, Any]:
        spec = parse_environment(environment)
        environment_var.set(spec.environment.value)
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        return await orders.place(spec, payload)

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")

    return app


_LOGIN_COLOR = 0x6C62A8
_LOCK_COLOR = 0xE42939


def _access_embed(title: str, color: int, client: ClientInfo, extra: list[EmbedField]) -> Embed:
    """접속 정보를 담은 Discord 알림. 입력한 비밀번호는 넣지 않는다."""
    return Embed(
        title=title,
        color=color,
        fields=(
            *extra,
            EmbedField("IP", client.ip, inline=True),
            EmbedField("브라우저·OS", client.device, inline=True),
        ),
    )


def _kst(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S KST")


def _client_info(request: Request) -> ClientInfo:
    return ClientInfo(
        request.client.host if request.client else "-",
        request.headers.get("user-agent", ""),
    )


def _cookie_options(request: Request) -> dict[str, Any]:
    """HTTP에서 Secure를 붙이면 쿠키가 저장되지 않으므로 HTTPS 요청일 때만 붙인다."""
    return {
        "path": "/",
        "httponly": True,
        "samesite": "strict",
        "secure": request.url.scheme == "https",
    }


def _same_origin(request: Request) -> bool:
    """Origin 헤더가 없으면(같은 출처의 일부 요청, 테스트 도구) 통과시킨다."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == request.headers.get("host")


def _error_response(status: int, kind: str, message: str, detail: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "kind": kind,
                "message": secrets.mask(message),
                "request_id": request_id_var.get(),
                **detail,
            }
        },
    )
