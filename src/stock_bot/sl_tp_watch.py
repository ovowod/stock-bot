"""SL / TP 실행: 모의투자 보유종목을 실시간 체결가로 지켜보다가 익절·손절 트리거에 자동 매도를 낸다.

켜진 SL/TP 세션 동안 감시 대상 종목의 체결가를 공유 실시간 연결로 구독하고,
수익률이 익절 기준 이상이거나 손절 기준 이하가 되면 매도 가능 수량 전량을 시장가로 판다.
실전투자 환경은 만들지 않는다. 수익률에 수수료·세금은 반영하지 않는다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from stock_bot.auto_sell import AutoSellGuard
from stock_bot.config import ENVIRONMENTS, Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import environment_var, log, request_id_var
from stock_bot.order import OrderService
from stock_bot.realtime import EventKind, RealtimeEvent, RealtimeHub, Subscription
from stock_bot.sl_tp import TIMEZONES, SlTpSettingsStore

logger = logging.getLogger("stock_bot.sl_tp_watch")

# 지켜볼 투자 환경. 실전은 넣지 않는다. 미국 모의는 05 티켓에서 더한다.
WATCHED = (ENVIRONMENTS[Environment.DOMESTIC_PAPER],)
# 시세 실시간 항목. 0B는 국내 주식체결이다.
QUOTE_TYPES = {Market.DOMESTIC: "0B"}
# 장구분(290)의 정규장 값. 국내 2는 장중이다. 장전·장후 시간외 체결은 판정하지 않는다.
REGULAR_MARKET = {Market.DOMESTIC: "2"}
# 공유 연결에서 SL / TP가 쓰는 그룹 번호. 체결 감시는 1을 쓴다.
GROUP_NO = "2"

PositionLookup = Callable[[EnvironmentSpec], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class _Session:
    start: time
    end: time
    take_profit: Decimal | None
    stop_loss: Decimal | None

    @property
    def span(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"


@dataclass
class _Run:
    """켜진 SL/TP 세션이 새로 시작될 때마다 하나. 트리거 기록은 실행마다 따로다."""

    number: int
    day: date
    session: _Session
    triggered: set[str] = field(default_factory=set)

    def covers(self, found: tuple[date, _Session] | None) -> bool:
        """그 날짜·세션이 이 실행의 것인가. 기준이 바뀌어도 같은 시간 범위면 같은 실행이다."""
        return found is not None and found[0] == self.day and found[1].span == self.session.span


class SlTpWatcher:
    def __init__(
        self,
        kiwoom: KiwoomClient,
        settings: SlTpSettingsStore,
        positions: PositionLookup,
        orders: OrderService,
        guard: AutoSellGuard,
        hub: RealtimeHub,
        now: Callable[[], datetime],
    ) -> None:
        self.kiwoom = kiwoom
        self.settings = settings
        self.positions = positions
        self.orders = orders
        self.guard = guard
        self.hub = hub
        self.now = now
        self._watches: list[_Watch] = []

    def start(self) -> None:
        """인증정보가 없는 모의 환경은 건너뛴다. Discord 설정과 상관없이 동작한다."""
        for spec in WATCHED:
            environment_var.set(spec.environment.value)
            try:
                self.kiwoom.credentials(spec)
            except AppError as exc:
                log(logger, logging.INFO, "sl_tp_watch_skipped", cause=exc.message)
                continue
            watch = _Watch(self, spec)
            self._watches.append(watch)
            watch.start()
        environment_var.set("-")

    async def stop(self) -> None:
        for watch in self._watches:
            await watch.stop()
        self._watches.clear()


class _Watch:
    """투자 환경 하나의 SL / TP 실행."""

    def __init__(self, owner: SlTpWatcher, spec: EnvironmentSpec) -> None:
        self._owner = owner
        self._spec = spec
        self._zone = ZoneInfo(TIMEZONES[spec.market])
        # 밀려남·설정 읽기 실패·서버 종료로 멈췄다. 멈추면 판정도 주문도 하지 않는다.
        self._stopped = False
        self._sessions: list[_Session] = []
        self._excluded: set[str] = set()
        self._run: _Run | None = None
        self._runs = 0
        # 종목코드 -> 매입가. 현금잔고이고 매입가를 읽을 수 있는 종목만 담는다. 제외종목도 담는다.
        self._holdings: dict[str, Decimal] = {}
        self._holdings_ready = False
        self._connection: int | None = None
        self._connected = False
        self._rejected = False
        # 같은 이유의 건너뜀·잘못된 체결가 로그를 반복해 남기지 않으려고
        # 종목마다 마지막 이유를 둔다.
        self._skips: dict[str, str] = {}
        self._invalid: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._channel = owner.hub.channel(spec, "sl_tp", "SL / TP", self._on_event)

    def start(self) -> None:
        try:
            settings = self._owner.settings.read(self._spec)
        except AppError as exc:
            # 깨진 설정을 빈 설정이나 옛 설정으로 착각해 동작하지 않는다.
            self._stopped = True
            log(logger, logging.ERROR, "sl_tp_watch_stopped", reason=exc.kind, cause=exc.message)
            return
        self._apply(settings)
        self._refresh_run()

    async def stop(self) -> None:
        # 서버가 꺼질 때 진행 중인 주문 호출은 기다리지 않는다.
        self._stopped = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def _apply(self, settings: dict[str, Any]) -> None:
        self._excluded = {item["code"] for item in settings["excluded"]}
        self._sessions = [
            _Session(
                time.fromisoformat(s["start"]),
                time.fromisoformat(s["end"]),
                None if s["take_profit"] is None else Decimal(s["take_profit"]),
                None if s["stop_loss"] is None else Decimal(s["stop_loss"]),
            )
            for s in settings["sessions"]
            if s["enabled"]
        ]

    def _session_at(self, moment: datetime) -> tuple[date, _Session] | None:
        """그 시각(그 시장 시간대)에 켜진 세션.

        시작은 포함하고 종료는 빼므로 맞닿은 시각은 뒤 세션이다.
        """
        local = moment.astimezone(self._zone)
        at = local.time()
        for session in self._sessions:
            if session.start <= at < session.end:
                return local.date(), session
        return None

    def _is_stopped(self) -> bool:
        # 밀려남은 채널 큐를 거치기 전에 공유 연결에서 바로 읽는다.
        return self._stopped or self._channel.stopped

    def _refresh_run(self) -> None:
        """지금 켜진 세션에 맞춰 SL/TP 세션 실행을 시작하거나 끝낸다."""
        found = self._session_at(self._owner.now())
        if found is None:
            if self._run is not None:
                log(logger, logging.INFO, "sl_tp_session_ended", **self._run_fields(self._run))
                self._run = None
                self._channel.subscribe(None)
            return
        day, session = found
        run = self._run
        if run is not None and run.covers(found):
            # 같은 세션 안에서 기준만 바뀌면 실행 번호와 트리거 기록을 그대로 둔다.
            run.session = session
            return
        self._runs += 1
        self._run = _Run(self._runs, day, session)
        log(logger, logging.INFO, "sl_tp_session_started", **self._run_fields(self._run))
        self._holdings_ready = False
        self._spawn(self._load_holdings(self._run))

    async def _load_holdings(self, run: _Run) -> None:
        try:
            rows = await self._owner.positions(self._spec)
        except AppError as exc:
            log(
                logger,
                logging.WARNING,
                "sl_tp_holdings_failed",
                run=run.number,
                kind=exc.kind,
                cause=exc.message,
            )
            return
        if run is not self._run or self._is_stopped():
            log(logger, logging.INFO, "sl_tp_holdings_discarded", run=run.number)
            return
        holdings: dict[str, Decimal] = {}
        counts = {"credit": 0, "no_purchase_price": 0, "duplicate": 0}
        duplicated: set[str] = set()
        for row in rows:
            code, price = row["code"], row["purchase_price"]
            if row["credit"]:
                # 현금 매도 주문으로 신용 보유분을 팔려다 실패하지 않게 다루지 않는다.
                counts["credit"] += 1
            elif price is None or price <= 0:
                counts["no_purchase_price"] += 1
            elif code in holdings or code in duplicated:
                # 같은 종목의 현금잔고가 여러 줄이면 어느 매입가인지 모른다.
                duplicated.add(code)
                holdings.pop(code, None)
                counts["duplicate"] += 1
            else:
                holdings[code] = price
        self._holdings = holdings
        self._holdings_ready = True
        log(
            logger,
            logging.INFO,
            "sl_tp_holdings_refreshed",
            run=run.number,
            count=len(holdings),
            excluded=len(holdings.keys() & self._excluded),
            **counts,
        )
        self._subscribe()

    def _watched(self) -> list[str]:
        return sorted(code for code in self._holdings if code not in self._excluded)

    def _subscribe(self) -> None:
        codes = self._watched()
        log(logger, logging.INFO, "sl_tp_subscription_changed", codes=codes)
        if not codes:
            self._channel.subscribe(None)
            return
        self._channel.subscribe(
            Subscription(GROUP_NO, codes, [QUOTE_TYPES[self._spec.market]], refresh="0")
        )

    async def _on_event(self, event: RealtimeEvent) -> None:
        environment_var.set(self._spec.environment.value)
        if event.kind is EventKind.CONNECTED:
            self._connection = event.connection
            self._connected = True
        elif event.kind is EventKind.DISCONNECTED:
            self._connected = False
        elif event.kind is EventKind.REGISTERED:
            self._rejected = False
        elif event.kind is EventKind.REJECTED:
            self._rejected = True
            log(logger, logging.WARNING, "sl_tp_subscription_rejected", **event.detail)
        elif event.kind is EventKind.STOPPED:
            self._stopped = True
            log(logger, logging.ERROR, "sl_tp_watch_stopped", reason="session_replaced")
        elif event.kind is EventKind.REAL:
            for item in event.items:
                self._judge(item, event)

    def _judge(self, item: dict[str, Any], event: RealtimeEvent) -> None:
        code = str(item.get("item") or "").strip()
        values = item.get("values")
        values = values if isinstance(values, dict) else {}
        log(logger, logging.DEBUG, "sl_tp_quote_received", code=code, price=values.get("10"))
        run = self._run
        if event.connection != self._connection:
            log(logger, logging.DEBUG, "sl_tp_quote_dropped", code=code, reason="old_connection")
            return
        if run is None:
            self._skip(code, "outside_session")
            return
        if not run.covers(self._session_at(event.received_at)):
            # 큐 처리가 세션 경계를 넘어 밀려도 앞 세션의 시세를 지금 세션 기준으로 판정하지 않는다.
            log(logger, logging.DEBUG, "sl_tp_quote_dropped", code=code, reason="other_run")
            return
        reason = self._skip_reason(run, code, values)
        if reason is not None:
            self._skip(code, reason)
            return
        price = self._price(code, values)
        if price is None:
            return
        self._skips.pop(code, None)
        purchase = self._holdings[code]
        rate = (price - purchase) / purchase * 100
        session = run.session
        if session.take_profit is not None and rate >= session.take_profit:
            trigger, threshold = "take_profit", session.take_profit
        elif session.stop_loss is not None and rate <= -session.stop_loss:
            trigger, threshold = "stop_loss", session.stop_loss
        else:
            return
        # 결과와 상관없이 이번 세션 실행에서는 다시 판정하지 않는다.
        run.triggered.add(code)
        request_id = uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        try:
            log(
                logger,
                logging.INFO,
                "sl_tp_triggered",
                code=code,
                **self._run_fields(run),
                trigger=trigger,
                price=str(price),
                purchase_price=str(purchase),
                rate=str(rate),
                threshold=str(threshold),
            )
            if not self._owner.guard.claim(self._spec.environment, code):
                log(logger, logging.INFO, "auto_sell_skipped", code=code, reason="in_progress")
                return
            # 시세 처리는 주문 결과를 기다리지 않는다.
            self._spawn(self._sell(run, code, request_id))
        finally:
            request_id_var.reset(token)

    def _skip_reason(self, run: _Run, code: str, values: dict[str, Any]) -> str | None:
        if self._is_stopped():
            return "stopped"
        if not self._connected:
            return "disconnected"
        if self._rejected:
            return "subscription_rejected"
        if not self._holdings_ready:
            return "holdings_pending"
        if str(values.get("290", "")).strip() != REGULAR_MARKET[self._spec.market]:
            return "not_regular_market"
        if code not in self._holdings or code in self._excluded:
            return "not_watched"
        if code in run.triggered:
            return "already_triggered"
        return None

    def _skip(self, code: str, reason: str) -> None:
        """같은 이유가 이어지면 처음 한 번만 INFO로 남긴다."""
        if self._skips.get(code) == reason:
            return
        self._skips[code] = reason
        log(logger, logging.INFO, "sl_tp_judge_skipped", code=code, reason=reason)

    def _price(self, code: str, values: dict[str, Any]) -> Decimal | None:
        """체결가(10)의 절댓값. 부호는 등락 방향이다. 읽을 수 없으면 None이다."""
        raw = values.get("10")
        reason = _unreadable_price(raw)
        if reason is None:
            self._invalid.pop(code, None)
            return abs(Decimal(str(raw).strip()))
        if self._invalid.get(code) != reason:
            self._invalid[code] = reason
            log(
                logger,
                logging.WARNING,
                "sl_tp_price_unreadable",
                code=code,
                reason=reason,
                raw=str(raw)[:40],
            )
        return None

    async def _sell(self, run: _Run, code: str, request_id: str) -> None:
        environment = self._spec.environment
        try:
            result = await self._owner.orders.sell_all_at_market(
                self._spec,
                code,
                exchange="",
                order_key=f"sl-tp-{request_id}",
                ready=lambda: self._may_send(run, code),
            )
        except asyncio.CancelledError:
            # 서버가 꺼지면 기다리지 않는다. 키움에 이미 보냈는지는 모른다.
            log(
                logger,
                logging.WARNING,
                "sl_tp_auto_sell_interrupted",
                code=code,
                run=run.number,
                cause="server_shutdown",
            )
            raise
        except Exception as exc:
            logger.error(
                "sl_tp_auto_sell_failed",
                exc_info=exc,
                extra={
                    "fields": {"code": code, "run": run.number, "error_type": type(exc).__name__}
                },
            )
            return
        finally:
            self._owner.guard.release(environment, code)
        if result.outcome == "accepted" and result.order_no:
            self._owner.guard.remember(environment, result.order_no, request_id)
        log(
            logger,
            logging.INFO if result.outcome in ("accepted", "skipped") else logging.WARNING,
            "sl_tp_auto_sell_finished",
            code=code,
            run=run.number,
            outcome=result.outcome,
            quantity=result.quantity,
            order_no=result.order_no,
            cause=result.cause,
        )

    def _may_send(self, run: _Run, code: str) -> bool:
        """키움에 보내기 직전 확인.

        연결 끊김만으로는 취소하지 않는다. 끊기기 전 체결가는 실제 체결이었다.
        """
        reason = None
        if self._spec.is_real:
            reason = "real_trading"
        elif self._is_stopped():
            reason = "stopped"
        elif run is not self._run or not run.covers(self._session_at(self._owner.now())):
            reason = "session_changed"
        elif code in self._excluded:
            reason = "excluded"
        if reason is not None:
            log(
                logger, logging.INFO, "sl_tp_send_refused", code=code, run=run.number, reason=reason
            )
        return reason is None

    def _spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _run_fields(run: _Run) -> dict[str, Any]:
        return {"run": run.number, "session": run.session.span, "day": run.day.isoformat()}


def _unreadable_price(raw: Any) -> str | None:
    """체결가를 판정에 쓸 수 없는 이유. 쓸 수 있으면 None이다."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return "empty"
    try:
        price = abs(Decimal(text))
    except InvalidOperation:
        return "not_a_number"
    return None if price.is_finite() and price > 0 else "not_positive"
