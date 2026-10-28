"""FastAPI 앱. 브라우저는 이 서버의 /api만 호출하고, 키움 호출은 서버 안에서만 일어난다."""

import asyncio
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from stock_bot.account.account import AccountService
from stock_bot.account.holdings import Holdings
from stock_bot.auth.auth import AuthService, load_password
from stock_bot.auth.login import install_login
from stock_bot.core.config import (
    DATA_DIR,
    ENVIRONMENTS,
    LOG_DIR,
    PROJECT_ROOT,
    EnvironmentSpec,
    load_env_file,
    parse_environment,
)
from stock_bot.core.errors import AppError, error_response
from stock_bot.core.logging_setup import environment_var, log, request_id_var, setup_logging
from stock_bot.core.notification import DiscordNotifier, Embed
from stock_bot.fill.feed import FillFeed
from stock_bot.fill.watch import FillWatcher
from stock_bot.kiwoom.kiwoom import KiwoomClient
from stock_bot.kiwoom.realtime import RealtimeConnect, RealtimeHub, connect_websocket
from stock_bot.market.quote import QuoteService
from stock_bot.market.ranking import RankingService
from stock_bot.market.stock_search import StockSearchService, today_kst
from stock_bot.order.alerts import cancel_embed
from stock_bot.order.auto_sell import AutoSell
from stock_bot.order.open_order import OpenOrderService
from stock_bot.order.order import OrderService
from stock_bot.sl_tp.sl_tp import SlTpSettingsStore
from stock_bot.sl_tp.watch import SlTpWatcher

logger = logging.getLogger("stock_bot.api")

WEB_DIST = PROJECT_ROOT / "web" / "dist"


async def selected_environment(environment: str) -> EnvironmentSpec:
    """경로의 투자 환경을 읽고 이 요청의 로그에 환경을 붙인다.

    async여야 한다. 일반 함수면 FastAPI가 다른 thread에서 돌려 environment_var가 route로
    넘어오지 않는다.
    """
    spec = parse_environment(environment)
    environment_var.set(spec.environment.value)
    return spec


Spec = Annotated[EnvironmentSpec, Depends(selected_environment)]


def create_app(
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    static_dir: Path | None = WEB_DIST,
    log_dir: Path | None = LOG_DIR,
    data_dir: Path | None = None,
    today: Callable[[], date] | None = None,
    clock: Callable[[], float] | None = None,
    notifier: DiscordNotifier | None = None,
    realtime_connect: RealtimeConnect | None = None,
    now: Callable[[], datetime] | None = None,
) -> FastAPI:
    """clock은 로그인 만료용 경과 시간이고, now는 시간대가 붙은 현재 시각이다."""
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
        """로그인·주문 취소 응답이 알림을 기다리지 않게 따로 보낸다.

        실패는 notifier가 로그로 남긴다.
        """
        if not notifier.enabled:
            return
        task = asyncio.create_task(notifier.send(embed=embed))
        notifications.add(task)
        task.add_done_callback(notifications.discard)

    kiwoom = KiwoomClient(environ, transport=transport)
    rankings = RankingService(kiwoom)
    stocks = StockSearchService(kiwoom, today or today_kst)
    holdings = Holdings(kiwoom, stocks.listings)
    accounts = AccountService(kiwoom, holdings)
    quotes = QuoteService(kiwoom)
    open_orders = OpenOrderService(kiwoom, stocks.listings)
    orders = OrderService(
        kiwoom,
        holdings.holding,
        # 취소 재확인은 미국 종목 목록이 꼭 있어야 거래소를 정할 수 있다.
        lambda spec: open_orders.orders(spec, listing_required=True),
        on_cancelled=lambda spec, order, result: notify(cancel_embed(spec, order, result)),
        fresh_holding=holdings.fresh_holding,
    )
    now = now or (lambda: datetime.now(UTC))
    realtime = RealtimeHub(kiwoom, notifier, realtime_connect or connect_websocket, now)
    # 자동 매도는 SL / TP가 내고, 체결 알림이 그 체결을 알아본다.
    auto_sell = AutoSell(orders)
    feed = FillFeed(realtime)
    fills = FillWatcher(kiwoom, open_orders, notifier, feed, auto_sell)
    sl_tp = SlTpSettingsStore((data_dir or DATA_DIR) / "sl-tp")
    sl_tp_watch = SlTpWatcher(
        kiwoom, sl_tp, holdings.positions, auto_sell, realtime, feed, notifier, now
    )
    sl_tp.on_saved = sl_tp_watch.settings_saved

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        fills.start()
        sl_tp_watch.start()
        try:
            yield
        finally:
            await sl_tp_watch.stop()
            await fills.stop()
            await feed.stop()
            await realtime.stop()

    app = FastAPI(
        title="Stock Bot", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    # 아래 request_context보다 먼저 등록해야 로그인 확인이 그 안쪽에서 돈다.
    # 그래야 거부 응답에도 요청 ID가 붙는다.
    install_login(app, auth, notify)

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
        return error_response(exc.status, exc.kind, exc.message, exc.detail)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unexpected_error",
            exc_info=exc,
            extra={"fields": {"path": request.url.path, "error_type": type(exc).__name__}},
        )
        return error_response(500, "internal_error", "서버 내부 오류가 발생했습니다.", {})

    @app.get("/api/environments")
    async def list_environments() -> list[dict[str, Any]]:
        return [
            {
                "value": spec.environment.value,
                "label": spec.label,
                "market": spec.market.value,
                "is_real": spec.is_real,
                "orders_allowed": spec.orders_allowed,
            }
            for spec in ENVIRONMENTS.values()
        ]

    @app.get("/api/environments/{environment}/account")
    async def get_account(spec: Spec) -> dict[str, Any]:
        return await accounts.fetch(spec)

    @app.get("/api/environments/{environment}/holdings/{code}")
    async def get_holding(spec: Spec, code: str) -> dict[str, Any]:
        holding = await holdings.holding(spec, code)
        return {**holding, "fetched_at": datetime.now(UTC).isoformat(timespec="seconds")}

    @app.get("/api/environments/{environment}/open-orders")
    async def get_open_orders(spec: Spec) -> dict[str, Any]:
        return await open_orders.fetch(spec)

    @app.get("/api/environments/{environment}/rankings/{kind}")
    async def get_ranking(
        spec: Spec, kind: str, exchange: str | None = None, period: str | None = None
    ) -> dict[str, Any]:
        return await rankings.fetch(spec, kind, {"exchange": exchange, "period": period})

    @app.get("/api/environments/{environment}/stocks")
    async def search_stocks(spec: Spec, q: str | None = None) -> dict[str, Any]:
        return await stocks.search(spec, q)

    @app.get("/api/environments/{environment}/quote")
    async def get_quote(
        spec: Spec, code: str | None = None, exchange: str | None = None
    ) -> dict[str, Any]:
        return await quotes.fetch(spec, code, exchange)

    @app.post("/api/environments/{environment}/orders")
    async def place_order(spec: Spec, request: Request) -> dict[str, Any]:
        return await orders.place(spec, await _json_body(request))

    @app.post("/api/environments/{environment}/cancellations")
    async def cancel_order(spec: Spec, request: Request) -> dict[str, Any]:
        return await orders.cancel(spec, await _json_body(request))

    @app.get("/api/environments/{environment}/sl-tp")
    async def get_sl_tp(spec: Spec) -> dict[str, Any]:
        return sl_tp.read(spec)

    @app.put("/api/environments/{environment}/sl-tp")
    async def put_sl_tp(spec: Spec, request: Request) -> dict[str, Any]:
        return await sl_tp.save(spec, await _json_body(request))

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")

    return app


async def _json_body(request: Request) -> Any:
    """요청 본문의 JSON. 해석하지 못하면 None이고, 받는 쪽이 형식 오류로 처리한다."""
    try:
        return await request.json()
    except ValueError:
        return None
