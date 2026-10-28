"""로그인 API와 로그인 확인.

install_login이 login·logout·session API와 로그인 확인 middleware를 등록한다.
"""

import logging
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response

from stock_bot.auth import SESSION_COOKIE, AuthService, ClientInfo
from stock_bot.errors import AppError, error_response
from stock_bot.kiwoom import KST
from stock_bot.logging_setup import log
from stock_bot.notification import Embed, EmbedField, format_kst

logger = logging.getLogger("stock_bot.api")

# 로그인하지 않아도 부를 수 있는 API. 그 밖의 /api는 로그인 세션이 있어야 한다.
PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/session"}

_LOGIN_COLOR = 0x6C62A8
_LOCK_COLOR = 0xE42939


def install_login(app: FastAPI, auth: AuthService, notify: Callable[[Embed], None]) -> None:
    """요청 ID를 붙이는 middleware보다 먼저 불러야 거부 응답에도 요청 ID가 붙는다."""

    @app.middleware("http")
    async def require_login(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        # 조회가 아닌 요청(POST·PUT 등)은 다른 사이트에서 온 것이면 받지 않는다.
        if request.method not in ("GET", "HEAD") and not _same_origin(request):
            log(
                logger,
                logging.WARNING,
                "origin_rejected",
                path=path,
                origin=request.headers.get("origin"),
                **_client_info(request).log_fields(),
            )
            return error_response(
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
            return error_response(401, "unauthorized", "로그인이 필요합니다.", {})
        return await call_next(request)

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
                        EmbedField("풀리는 시각", format_kst(until), inline=True),
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
        notify(_access_embed("로그인", _LOGIN_COLOR, client, [EmbedField("시각", format_kst(now))]))
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
