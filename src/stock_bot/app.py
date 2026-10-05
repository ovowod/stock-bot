"""FastAPI 앱. 브라우저는 이 서버의 /api만 호출하고, 키움 호출은 서버 안에서만 일어난다."""

import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from stock_bot.account import AccountService
from stock_bot.config import ENVIRONMENTS, LOG_DIR, PROJECT_ROOT, load_env_file, parse_environment
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import environment_var, log, request_id_var, setup_logging
from stock_bot.masking import secrets
from stock_bot.order import OrderService
from stock_bot.quote import QuoteService
from stock_bot.ranking import RankingService
from stock_bot.stock_search import StockSearchService, today_kst

logger = logging.getLogger("stock_bot.api")

WEB_DIST = PROJECT_ROOT / "web" / "dist"


def create_app(
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    static_dir: Path | None = WEB_DIST,
    log_dir: Path | None = LOG_DIR,
    today: Callable[[], date] | None = None,
) -> FastAPI:
    setup_logging(log_dir)
    if environ is None:
        load_env_file()
        environ = os.environ

    kiwoom = KiwoomClient(environ, transport=transport)
    accounts = AccountService(kiwoom)
    rankings = RankingService(kiwoom)
    stocks = StockSearchService(kiwoom, today or today_kst)
    quotes = QuoteService(kiwoom)
    orders = OrderService(kiwoom)
    app = FastAPI(title="Stock Bot", docs_url=None, redoc_url=None, openapi_url=None)

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
