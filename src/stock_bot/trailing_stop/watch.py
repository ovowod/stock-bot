"""Trailing Stop 감시: 모의투자 보유종목의 시세 체결로 활성화, 고점, 트리거를 판정한다.

켜진 Trailing Stop 세션 동안 보유종목을 읽고 공유 실시간 연결로 시세 체결을 구독한다.
트리거가 일어나면 공용 자동 매도(AutoSell)로 매도 가능 수량 전량을 시장가로 판다.
기억(활성화·고점·트리거)은 투자 환경마다 메모리에 두고 고점 기록 파일(record.py)에 써서,
세션과 날짜를 넘기고 서버를 다시 시작해도 이어 간다. 파일은 서버가 시작할 때와
읽지 못한 상태에서 복구할 때만 읽는다. 그 밖에는 메모리가 기준이다.
실전투자 환경은 만들지 않는다. 수익률에 수수료·세금은 반영하지 않는다.

세션·보유종목 다시 읽기·연결 추적은 SL / TP 감시(sl_tp/watch.py)와 같은 규칙이다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from stock_bot.auto_trading.judging import (
    PRICE_FIELD,
    QUOTE_TYPES,
    Holding,
    HoldingsCheck,
    check_holdings,
    is_regular_market,
    quote_items,
    read_price,
    session_at,
)
from stock_bot.auto_trading.settings import TIMEZONES, FeatureSettingsStore
from stock_bot.core.config import ENVIRONMENTS, EnvironmentSpec
from stock_bot.core.errors import AppError
from stock_bot.core.logging_setup import environment_var, log, request_id_var
from stock_bot.core.notification import DiscordNotifier, Embed, EmbedField
from stock_bot.fill.feed import FeedState, FeedStatus, Fill, FillFeed
from stock_bot.kiwoom.kiwoom import KiwoomClient
from stock_bot.kiwoom.realtime import EventKind, RealtimeEvent, RealtimeHub, Subscription
from stock_bot.order.auto_sell import AutoSell
from stock_bot.order.order import AutoSellResult
from stock_bot.trailing_stop import record
from stock_bot.trailing_stop.alerts import RESULT_EMBEDS, SellTrigger
from stock_bot.trailing_stop.record import Memory, format_decimal, format_moment

logger = logging.getLogger("stock_bot.trailing_stop_watch")

# 공유 연결에서 Trailing Stop의 시세 구독이 쓰는 그룹 번호. 체결 feed는 1, SL / TP 시세는 2다.
# 같은 종목을 그룹 둘에 등록할 때 실시간 시세 200종목 한도를 어떻게 세는지는 문서로 확인하지 못했다.
GROUP_NO = "3"
# 지금 켜진 Trailing Stop 세션을 다시 계산하는 간격. 설정을 저장할 때도 다시 계산한다.
SESSION_CHECK_SECONDS = 1.0
# 보유종목 읽기가 실패하면 이 간격으로 다시 읽는다. 실패가 이어지면 두 배씩 늘린다.
HOLDINGS_RETRY_INITIAL_SECONDS = 1.0
HOLDINGS_RETRY_MAX_SECONDS = 60.0
# 보유종목 읽기가 첫 실패부터 이만큼 계속 실패하면 판정 중단 알림을 한 번 보낸다.
HOLDINGS_ALERT_AFTER = timedelta(minutes=5)
# 고점 갱신을 모아 고점 기록 파일에 쓰는 간격. 활성화·트리거·기억 삭제는 바로 쓴다.
RECORD_WRITE_SECONDS = 5.0
NEUTRAL_COLOR = 0x8B8F98
# 체결 알림과 로그에 남는 자동 매도 기능 이름.
AUTO_SELL_SOURCE = "Trailing Stop"

PositionLookup = Callable[[EnvironmentSpec], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class WatchState:
    """상태 조회가 읽는 감시의 지금 모습. 설정도 감시가 쓰고 있는 것을 그대로 준다."""

    watching: bool
    not_watching_reason: str | None
    stocks: list[dict[str, Any]]
    # 감시가 쓰고 있는 Trailing Stop 세션과 제외종목. 설정 파일을 읽지 못해 멈춰 있으면 None이다.
    sessions: list[dict[str, Any]] | None
    excluded: list[str]


@dataclass
class _Run:
    """켜진 Trailing Stop 세션이 새로 시작될 때마다 하나. 보유종목 읽기와 구독을 묶는다.

    기억은 실행에 묶이지 않는다. 실행이 바뀌어도 이어 간다.
    """

    number: int
    day: date
    span: str
    # 이 실행을 시작한 시각. 그보다 먼저 받은 시세는 앞 실행의 것이다.
    started_at: datetime
    # 지금 쓰는 보유종목을 읽을 때의 연결 번호. 다른 번호로 다시 연결되면 다시 읽는다.
    connection: int | None = None
    # 보유종목 다시 읽기 작업과, 읽는 동안 새로 온 계기.
    refresh: asyncio.Task[None] | None = None
    pending: list[str] = field(default_factory=list)
    # 보유종목 읽기가 처음 실패한 시각과 판정 중단을 알렸는지. 성공하면 지운다.
    failed_since: datetime | None = None
    failure_alerted: bool = False
    # 종목코드 -> 이 실행에서 낸 자동 매도와 그 결과(상태 조회 모양).
    # 결과는 매도를 시작한 실행에 남아, 늦게 나와도 다음 실행의 상태를 바꾸지 않는다.
    auto_sells: dict[str, dict[str, Any]] = field(default_factory=dict)

    def covers(self, found: tuple[date, dict[str, Any]] | None) -> bool:
        """그 날짜·세션이 이 실행의 것인가. 기준만 바뀌면 같은 실행이다."""
        return found is not None and found[0] == self.day and _span(found[1]) == self.span


class TrailingStopWatcher:
    def __init__(
        self,
        kiwoom: KiwoomClient,
        settings: FeatureSettingsStore,
        positions: PositionLookup,
        auto_sell: AutoSell,
        hub: RealtimeHub,
        feed: FillFeed,
        notifier: DiscordNotifier,
        now: Callable[[], datetime],
        record_dir: Path,
    ) -> None:
        self.kiwoom = kiwoom
        self.settings = settings
        self.positions = positions
        self.auto_sell = auto_sell
        self.hub = hub
        # 내 계좌 체결. 체결되면 매입가·보유수량이 바뀌므로 보유종목을 다시 읽는다.
        self.feed = feed
        self.notifier = notifier
        self.now = now
        # 투자 환경마다 고점 기록 파일 하나를 둔다.
        self.record_dir = record_dir
        self._watches: list[_Watch] = []

    def start(self) -> None:
        """모의투자 환경만 지켜본다. 인증정보가 없는 환경은 건너뛴다."""
        for spec in (s for s in ENVIRONMENTS.values() if not s.is_real):
            environment_var.set(spec.environment.value)
            try:
                self.kiwoom.credentials(spec)
            except AppError as exc:
                log(logger, logging.INFO, "trailing_stop_watch_skipped", cause=exc.message)
                continue
            watch = _Watch(self, spec)
            self._watches.append(watch)
            watch.start()
        environment_var.set("-")

    async def stop(self) -> None:
        for watch in self._watches:
            await watch.stop()
        self._watches.clear()

    def settings_saved(self, spec: EnvironmentSpec, settings: dict[str, Any]) -> None:
        for watch in self._watches:
            if watch.spec is spec:
                watch.settings_saved(settings)

    def state(self, spec: EnvironmentSpec) -> WatchState | None:
        """상태 조회가 읽는다. 지켜보지 않는 환경(실전, 인증정보 없음, 시작 전)은 None이다."""
        for watch in self._watches:
            if watch.spec is spec:
                return watch.state()
        return None


class _Watch:
    """투자 환경 하나의 Trailing Stop 감시."""

    def __init__(self, owner: TrailingStopWatcher, spec: EnvironmentSpec) -> None:
        self._owner = owner
        self.spec = spec
        # 서버 종료로 멈췄다. 밀려남은 채널이 바로 알려 준다.
        self._stopped = False
        # 시작 때 설정 파일을 읽지 못했다. 정상 설정이 저장될 때까지 멈춰 있다.
        self._settings_broken = False
        # 고점 기록 파일을 읽지 못했다. 고치거나 지운 뒤 설정을 저장해 다시 읽을 때까지 멈춰 있다.
        # 그동안 깨진 파일을 덮어쓰지 않는다.
        self._state_broken = False
        self._record = owner.record_dir / f"{spec.environment.value}.json"
        # 메모리가 파일보다 앞서 있는지, 파일에 아직 쓰지 않은 고점 갱신 종목, 쓰기 실패를 알렸는지.
        # 쓰기에 실패하면 그대로 두고 다음 쓰기 때 메모리 전체를 다시 쓴다.
        self._unwritten = False
        self._peaks: set[str] = set()
        self._write_alerted = False
        self._flush: asyncio.Task[None] | None = None
        # 세션 밖 보유종목 다시 읽기와, 읽는 동안 체결이 또 왔는지.
        self._outside: asyncio.Task[None] | None = None
        self._outside_pending = False
        self._sessions: list[dict[str, Any]] = []
        self._excluded: set[str] = set()
        self._run: _Run | None = None
        self._runs = 0
        # 종목코드 -> 매입가·거래소. 현금잔고이고 판정할 수 있는 종목만 담는다. 제외종목도 담는다.
        self._holdings: dict[str, Holding] = {}
        # 현금잔고가 있지만 판정할 수 없는 종목코드 -> 이유. 신용 보유분만 있는 종목은 담지 않는다.
        self._ineligible: dict[str, str] = {}
        # 종목코드 -> 마지막으로 읽은 현금 보유 수량. 매도 체결을 받으면 바로 뺀다.
        self._quantities: dict[str, Decimal] = {}
        self._holdings_ready = False
        # 종목코드 -> 기억. 고점 기록 파일보다 이것이 기준이다.
        self.memory: dict[str, Memory] = {}
        # 종목코드 -> 이 실행에서 판정한 마지막 시세 체결가와 받은 시각.
        self._last_prices: dict[str, tuple[Decimal, datetime]] = {}
        self._subscribed: list[str] | None = None
        # 연결이 LOGIN까지 되면 깨운다. 보유종목은 연결이 있을 때 읽어야 그 뒤 체결을 놓치지 않는다.
        self._wake = asyncio.Event()
        self._quotes_rejected = False
        self._fills_rejected = False
        self._unsubscribe_fills: Callable[[], None] | None = None
        # 평균 매입가가 앱이 받은 체결로 바뀐 것인지 보려고 둔다. 지난 비교 뒤 체결을 받은 종목,
        # 지난 비교 때의 연결 번호(None이면 서버 시작 뒤 비교 전), 그 뒤 체결 등록이 거부됐는지.
        # 연결이 바뀌었거나 거부됐으면 그사이 체결을 놓쳤을 수 있어 받은 체결로 인정하지 않는다.
        self._filled: set[str] = set()
        self._compared_connection: int | None = None
        self._fills_gap = False
        # 같은 이유의 건너뜀·잘못된 체결가 로그를 반복해 남기지 않으려고
        # 종목마다 마지막 이유를 둔다.
        self._skips: dict[str, str] = {}
        self._invalid: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._channel = owner.hub.channel(spec, "trailing_stop", "Trailing Stop", self._on_event)

    def start(self) -> None:
        self._spawn(self._tick())
        # 설정보다 먼저 읽는다. 설정 파일이 깨져 있어도 기억을 잃지 않고, 세션 밖 체결을 처리한다.
        if not self._read_record("start"):
            self._state_broken = True
            log(logger, logging.ERROR, "trailing_stop_watch_stopped", reason="state_unreadable")
            self._alert_state_unreadable()
        try:
            settings = self._owner.settings.read(self.spec)
        except AppError as exc:
            # 깨진 설정을 빈 설정으로 착각해 동작하지 않는다. 알림은 SL / TP가 같은 파일로 보낸다.
            self._settings_broken = True
            log(
                logger,
                logging.ERROR,
                "trailing_stop_watch_stopped",
                reason="settings_unreadable",
                cause=exc.message,
            )
            self._sync_fills()
            return
        self._apply(settings)
        self._refresh_run()
        self._sync_fills()

    async def stop(self) -> None:
        self._stopped = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        token = environment_var.set(self.spec.environment.value)
        try:
            # 모아 두고 아직 쓰지 않은 고점 갱신을 쓴다.
            self._write_unwritten("stopped")
        finally:
            environment_var.reset(token)

    def settings_saved(self, settings: dict[str, Any]) -> None:
        token = environment_var.set(self.spec.environment.value)
        try:
            # 고점 기록은 읽지 못한 상태에서 복구할 때만 다시 읽는다. 그 밖에는 메모리가 기준이다.
            if self._state_broken and self._read_record("settings_saved"):
                self._state_broken = False
                log(logger, logging.INFO, "trailing_stop_watch_resumed", reason="state_recovered")
            if self._settings_broken:
                log(logger, logging.INFO, "trailing_stop_watch_resumed", reason="settings_saved")
            self._settings_broken = False
            excluded = self._excluded
            run = self._run
            self._apply(settings)
            # 제외종목에 든 종목의 기억은 버린다. 세션 기준만 바꾼 저장으로는 버리지 않는다.
            for code in sorted(self.memory.keys() & self._excluded):
                self._forget(code, "excluded")
            self._refresh_run()
            self._sync_fills()
            if self._run is run and run is not None and excluded != self._excluded:
                # 같은 실행 안에서 제외종목만 바뀌었다. 구독만 맞춘다.
                if self._holdings_ready:
                    self._subscribe()
        finally:
            environment_var.reset(token)

    def state(self) -> WatchState:
        """감시 중이면 읽은 보유종목을, 아니면 기억이 있는 종목을 종목코드마다 하나씩 담는다."""
        reason = self._not_watching_reason()
        if reason is None:
            codes = sorted(self._holdings.keys() | self._ineligible.keys())
        else:
            codes = sorted(self.memory)
        return WatchState(
            reason is None,
            reason,
            [self._stock(code, reason is None) for code in codes],
            None if self._settings_broken else self._sessions,
            sorted(self._excluded),
        )

    def _not_watching_reason(self) -> str | None:
        if self._channel.stopped:
            return "connection_replaced"
        if self._settings_broken:
            return "settings_unreadable"
        if self._state_broken:
            return "state_unreadable"
        if self._run is None:
            return "no_session"
        if self._fills_rejected:
            return "fills_rejected"
        if self._quotes_rejected:
            return "quotes_rejected"
        if not self._holdings_ready:
            return "holdings_pending"
        return None

    def _stock(self, code: str, watching: bool) -> dict[str, Any]:
        """감시 중이 아니면 판정에 쓰는 매입가와 가격을 비운다. 화면은 계좌 값을 쓴다."""
        memory = self.memory.get(code)
        ineligible = self._ineligible.get(code) if watching else None
        holding = self._holdings.get(code) if watching else None
        last = self._last_prices.get(code) if holding is not None else None
        if ineligible is not None:
            status = "ineligible"
        elif memory is None:
            status = "waiting"
        else:
            status = "tracking" if memory.triggered_at is None else "triggered"
        return {
            "code": code,
            "status": status,
            "ineligible_reason": ineligible,
            "purchase_price": None if holding is None else format_decimal(holding.purchase_price),
            "last_price": None if last is None else format_decimal(last[0]),
            "last_price_at": None if last is None else format_moment(last[1]),
            "activated_at": None if memory is None else format_moment(memory.activated_at),
            "peak": None if memory is None else format_decimal(memory.peak),
            "peak_at": None if memory is None else format_moment(memory.peak_at),
            "triggered_at": None if memory is None else format_moment(memory.triggered_at),
            "triggered_price": None if memory is None else format_decimal(memory.triggered_price),
            "auto_sell": None if self._run is None else self._run.auto_sells.get(code),
        }

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(SESSION_CHECK_SECONDS)
            try:
                self._refresh_run()
            except Exception as exc:
                # 한 번의 계산 오류로 세션 전환이 멈추지 않게 한다.
                logger.error(
                    "trailing_stop_session_check_failed",
                    exc_info=exc,
                    extra={"fields": {"error_type": type(exc).__name__}},
                )

    def _apply(self, settings: dict[str, Any]) -> None:
        self._excluded = {item["code"] for item in settings["excluded"]}
        self._sessions = settings["sessions"]

    def _session_at(self, moment: datetime) -> tuple[date, dict[str, Any]] | None:
        return session_at(self._sessions, moment, self.spec.market)

    def _is_stopped(self) -> bool:
        return self._stopped or self._settings_broken or self._state_broken or self._channel.stopped

    def _refresh_run(self) -> None:
        """지금 켜진 세션에 맞춰 실행을 시작하거나 끝낸다. 기억은 그대로 둔다."""
        if self._is_stopped():
            return
        found = self._session_at(self._owner.now())
        run = self._run
        if run is not None and run.covers(found):
            return
        if run is not None:
            self._end_run(run)
        if found is None:
            if run is not None:
                self._subscribed = None
                self._channel.subscribe(None)
                self._sync_fills()
            return
        self._runs += 1
        new = _Run(self._runs, found[0], _span(found[1]), self._owner.now())
        new.connection = self._channel.connection
        self._run = new
        self._subscribed = None
        self._last_prices.clear()
        log(
            logger,
            logging.INFO,
            "trailing_stop_session_started",
            **_run_fields(new),
            previous_run=None if run is None else run.number,
            memory_count=len(self.memory),
        )
        self._sync_fills()
        self._request_refresh(new, "session_started")

    def _sync_fills(self) -> None:
        """세션 동안, 그리고 세션 밖에서도 기억이 있는 동안 내 계좌 체결을 받는다.

        세션 밖에서 전량 판 종목의 기억을 버리려는 것이다.
        """
        wanted = self._run is not None or bool(self.memory)
        if wanted and self._unsubscribe_fills is None:
            self._unsubscribe_fills = self._owner.feed.subscribe(
                self.spec, "trailing_stop", "Trailing Stop", self._on_feed
            )
        elif not wanted and self._unsubscribe_fills is not None:
            self._unsubscribe_fills()
            self._unsubscribe_fills = None

    def _end_run(self, run: _Run) -> None:
        log(logger, logging.INFO, "trailing_stop_session_ended", **_run_fields(run))
        self._write_unwritten("session_ended")
        self._run = None
        self._holdings_ready = False
        if run.refresh is not None and not run.refresh.done():
            run.refresh.cancel()
            if run.failed_since is not None:
                log(logger, logging.INFO, "trailing_stop_holdings_retry_stopped", run=run.number)

    def _request_refresh(self, run: _Run, reason: str) -> None:
        """보유종목을 다시 읽는다. 새 결과가 반영될 때까지 판정하지 않는다.

        읽는 중에 다시 오면 끝난 뒤 한 번 더 읽는다. 짧은 시간에 여러 번 와도 한 번으로 합친다.
        """
        if self._holdings_ready:
            log(logger, logging.INFO, "trailing_stop_judging_paused", run=run.number, reason=reason)
        self._holdings_ready = False
        run.pending.append(reason)
        if run.refresh is None or run.refresh.done():
            run.refresh = self._spawn(self._refresh(run))

    async def _refresh(self, run: _Run) -> None:
        # 다시 읽기는 여러 체결을 합치므로 어느 한 체결의 요청 ID로 남기지 않는다.
        request_id_var.set("-")
        delay = HOLDINGS_RETRY_INITIAL_SECONDS
        while True:
            # 연결이 없으면 읽지 않고 기다린다. 읽은 뒤 연결되면 그사이 체결을 놓친다.
            while self._channel.connection is None:
                if run is not self._run or self._is_stopped():
                    return
                self._wake.clear()
                await self._wake.wait()
            if run is not self._run or self._is_stopped():
                return
            reasons, run.pending = run.pending, []
            connection = self._channel.connection
            try:
                rows = await self._owner.positions(self.spec)
            except AppError as exc:
                if run.failed_since is None:
                    run.failed_since = self._owner.now()
                run.pending = reasons + run.pending
                log(
                    logger,
                    logging.WARNING,
                    "trailing_stop_holdings_failed",
                    run=run.number,
                    reasons=reasons,
                    kind=exc.kind,
                    cause=exc.message,
                    failed_since=run.failed_since.isoformat(),
                    wait_seconds=delay,
                )
                if (
                    not run.failure_alerted
                    and self._owner.now() - run.failed_since >= HOLDINGS_ALERT_AFTER
                ):
                    run.failure_alerted = True
                    self._alert_holdings_failing(run.failed_since, exc.message)
                await asyncio.sleep(delay)
                delay = min(delay * 2, HOLDINGS_RETRY_MAX_SECONDS)
                continue
            if run.failed_since is not None:
                log(
                    logger,
                    logging.INFO,
                    "trailing_stop_holdings_recovered",
                    run=run.number,
                    failed_since=run.failed_since.isoformat(),
                )
                run.failed_since = None
                run.failure_alerted = False
            if run is not self._run or self._is_stopped():
                log(
                    logger,
                    logging.INFO,
                    "trailing_stop_holdings_discarded",
                    run=run.number,
                    reason="run",
                )
                return
            if self._channel.connection != connection or run.pending:
                # 조회하는 동안 다시 연결됐거나 체결이 더 왔다. 이 결과는 쓰지 않고 한 번 더 읽는다.
                log(
                    logger,
                    logging.INFO,
                    "trailing_stop_holdings_discarded",
                    run=run.number,
                    reason="newer_trigger" if run.pending else "connection",
                )
                continue
            run.connection = connection
            self._use_holdings(run, rows, reasons)
            return

    def _use_holdings(self, run: _Run, rows: list[dict[str, Any]], reasons: list[str]) -> None:
        checked = self._use_quantities(rows, run.connection)
        self._holdings = checked.eligible
        self._ineligible = {c: r for c, r in checked.ineligible.items() if r != "credit"}
        self._holdings_ready = True
        log(
            logger,
            logging.INFO,
            "trailing_stop_holdings_refreshed",
            run=run.number,
            reasons=reasons,
            count=len(self._holdings),
            excluded=len(self._holdings.keys() & self._excluded),
            memory_count=len(self.memory),
            unknown_exchange_codes=checked.unknown_exchange_codes,
            **checked.counts,
        )
        log(logger, logging.INFO, "trailing_stop_judging_resumed", run=run.number)
        self._subscribe()

    def _use_quantities(self, rows: list[dict[str, Any]], connection: int | None) -> HoldingsCheck:
        """다시 읽은 보유종목으로 보유 수량을 바꾸고, 현금잔고가 없는 종목의 기억은 버린다.

        판정할 수 없게 됐을 뿐인 종목은 기억을 둔다. connection은 읽을 때의 연결 번호다.
        """
        checked = check_holdings(self.spec.market, rows)
        cash = {row["code"] for row in rows if not row["credit"]}
        for code in sorted(self.memory.keys() - cash):
            self._forget(code, "not_held")
        self._use_purchase_prices(checked.eligible, connection)
        self._quantities = {
            row["code"]: Decimal(row["quantity"])
            for row in rows
            if row["code"] in checked.eligible and not row["credit"] and row["quantity"] is not None
        }
        return checked

    def _use_purchase_prices(self, holdings: dict[str, Holding], connection: int | None) -> None:
        """기억의 평균 매입가를 다시 읽은 값과 비교한다.

        앱이 받은 체결로 바뀌었으면 새 값으로 바꾸고 바로 쓴다. 트리거된 종목은 고점을
        갱신하지 않아 다른 쓰기가 없다. 쓰지 않으면 재시작 뒤 앱이 봤던 변경이 못 본 변경으로
        보인다. 받은 체결로 설명되지 않으면 팔고 다시 산 것일 수 있어 기억을 버린다.
        """
        changed = False
        for code in sorted(self.memory):
            holding = holdings.get(code)
            if holding is None:
                continue
            if self._reconcile_purchase_price(code, holding.purchase_price, connection):
                changed = True
        self._filled.clear()
        self._compared_connection = connection
        self._fills_gap = self._fills_rejected
        if changed:
            self._write("purchase_price")

    def _reconcile_purchase_price(
        self, code: str, purchase: Decimal, connection: int | None
    ) -> bool:
        """다시 읽은 평균 매입가를 기억과 맞춰 본다.

        같으면 그대로 두고 False다. 다르지만 받은 체결로 설명되면 기억의 값을 바꾸고 True다.
        설명되지 않으면 기억을 버리고 False다. 바꾼 값을 파일에 쓰는 것은 부르는 쪽이 한다.
        """
        memory = self.memory[code]
        if purchase == memory.purchase_price:
            return False
        seen = (
            code in self._filled
            and connection is not None
            and connection == self._compared_connection
            and not self._fills_gap
        )
        if not seen:
            self._forget(
                code,
                "purchase_price_changed",
                previous_purchase_price=format_decimal(memory.purchase_price),
                purchase_price=format_decimal(purchase),
            )
            return False
        memory.purchase_price = purchase
        return True

    def _forget(self, code: str, reason: str, **detail: Any) -> None:
        memory = self.memory.pop(code)
        self._peaks.discard(code)
        log(
            logger,
            logging.INFO,
            "trailing_stop_memory_forgotten",
            code=code,
            reason=reason,
            peak=format_decimal(memory.peak),
            triggered=memory.triggered_at is not None,
            **detail,
        )
        self._write("forgotten")
        self._sync_fills()

    def _read_record(self, reason: str) -> bool:
        """고점 기록 파일을 읽어 기억으로 쓴다. 읽지 못하면 기억은 그대로 두고 False다."""
        try:
            loaded = record.load(self._record)
        except (OSError, ValueError) as exc:
            log(
                logger,
                logging.ERROR,
                "trailing_stop_record_unreadable",
                path=str(self._record),
                reason=reason,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            return False
        self.memory = loaded.memory
        self._unwritten = False
        self._peaks.clear()
        log(
            logger,
            logging.INFO,
            "trailing_stop_record_read",
            path=str(self._record),
            reason=reason,
            memory_count=len(loaded.memory),
        )
        if loaded.cleared_codes is not None:
            log(
                logger,
                logging.INFO,
                "trailing_stop_record_migrated",
                path=str(self._record),
                cleared_codes=loaded.cleared_codes,
            )
            # 다음 시작 때 다시 옮기지 않게 버전 2로 바로 쓴다.
            self._write("migrated")
        return True

    def _write(self, reason: str) -> None:
        """기억 전체를 고점 기록 파일에 바로 쓴다.

        쓰지 못하면 메모리의 판정은 그대로 이어 가고, 처음 한 번만 알린 뒤 다음 쓰기 때 다시 쓴다.
        """
        if self._state_broken:
            return
        self._unwritten = True
        try:
            record.write(self._record, self.memory)
        except OSError as exc:
            log(
                logger,
                logging.ERROR,
                "trailing_stop_record_write_failed",
                path=str(self._record),
                reason=reason,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            # 서버가 꺼지는 중이면 알림을 보낼 수 없다. 로그만 남긴다.
            if not self._write_alerted and not self._stopped:
                self._write_alerted = True
                self._alert_write_failed(str(exc))
            return
        if self._write_alerted:
            self._write_alerted = False
            log(logger, logging.INFO, "trailing_stop_record_write_recovered", reason=reason)
        # 고점 갱신은 시세 체결마다 남기지 않고, 쓸 때 갱신된 종목 수만 남긴다.
        log(
            logger,
            logging.DEBUG,
            "trailing_stop_record_written",
            reason=reason,
            memory_count=len(self.memory),
            peak_updated=len(self._peaks),
        )
        self._unwritten = False
        self._peaks.clear()

    def _write_unwritten(self, reason: str) -> None:
        """감시를 멈출 때(세션 끝, 서버 종료) 아직 쓰지 않은 것을 쓴다."""
        if self._unwritten:
            self._write(reason)

    async def _write_later(self) -> None:
        await asyncio.sleep(RECORD_WRITE_SECONDS)
        self._write_unwritten("peak")

    def _subscribe(self) -> None:
        """감시 대상이 바뀌었을 때만 구독을 다시 낸다. 실행이 새로 시작되면 늘 다시 낸다."""
        codes = sorted(code for code in self._holdings if code not in self._excluded)
        if codes == self._subscribed:
            return
        self._subscribed = codes
        log(logger, logging.INFO, "trailing_stop_subscription_changed", codes=codes)
        if not codes:
            self._channel.subscribe(None)
            return
        market = self.spec.market
        self._channel.subscribe(
            Subscription(
                GROUP_NO,
                quote_items(market, codes, self._holdings),
                [QUOTE_TYPES[market]],
                refresh="0",
            )
        )

    async def _on_event(self, event: RealtimeEvent) -> None:
        environment_var.set(self.spec.environment.value)
        run = self._run
        if event.kind is EventKind.CONNECTED:
            self._wake.set()
            if (
                run is not None
                and run.connection is not None
                and run.connection != event.connection
            ):
                # 지금 보유종목을 읽은 뒤 끊긴 적이 있다. 끊긴 동안 놓친 체결이 있을 수 있다.
                self._request_refresh(run, "reconnected")
        elif event.kind is EventKind.REGISTERED:
            if self._quotes_rejected:
                log(logger, logging.INFO, "trailing_stop_watch_resumed", reason="quotes_registered")
            self._quotes_rejected = False
        elif event.kind is EventKind.REJECTED:
            # 다음 세션 시작, 감시 종목 변경, 재연결 때 다시 등록한다(SL / TP와 같다).
            log(
                logger,
                logging.WARNING,
                "trailing_stop_subscription_rejected",
                subscription="quotes",
                group=event.group,
                **event.detail,
            )
            self._quotes_rejected = True
        elif event.kind is EventKind.STOPPED:
            self._wake.set()
            log(logger, logging.ERROR, "trailing_stop_watch_stopped", reason="connection_replaced")
        elif event.kind is EventKind.REAL:
            for item in event.items:
                self._judge(item, event)

    def _on_feed(self, value: Fill | FeedStatus) -> None:
        """체결 feed가 받는 자리에서 바로 부른다. 그래서 그 뒤의 시세보다 먼저 판정을 멈춘다."""
        environment_var.set(self.spec.environment.value)
        if isinstance(value, Fill):
            self._on_fill(value)
        elif value.state is FeedState.LIVE:
            was_rejected = self._fills_rejected
            self._fills_rejected = False
            if was_rejected and self._run is not None:
                # 거부된 동안의 체결은 받지 못했다. 바뀐 매입가를 반영한 뒤 판정한다.
                log(logger, logging.INFO, "trailing_stop_watch_resumed", reason="fills_registered")
                self._request_refresh(self._run, "fills_registered")
        else:
            log(
                logger,
                logging.WARNING,
                "trailing_stop_subscription_rejected",
                subscription="fills",
                **value.detail,
            )
            # 체결을 받지 못하면 바뀐 매입가·보유수량을 모른 채 판정하게 되므로 판정도 멈춘다.
            self._fills_rejected = True
            self._fills_gap = True

    def _on_fill(self, fill: Fill) -> None:
        """내 계좌 체결(앱 밖 주문 포함). 매도면 보유 수량을 바로 빼고, 보유종목을 다시 읽는다.

        세션 밖에서는 기억이 있는 종목의 체결만 다룬다.
        """
        remembered = fill.code in self.memory
        if fill.code is not None:
            self._filled.add(fill.code)
        self._subtract(fill)
        run = self._run
        if run is None:
            if remembered and self.memory:
                log(logger, logging.INFO, "trailing_stop_fill_seen", run=None, code=fill.code)
                self._request_outside_refresh()
                return
            reason = "outside_session"
        elif fill.connection != self._channel.connection:
            reason = "old_connection"
        else:
            log(logger, logging.INFO, "trailing_stop_fill_seen", run=run.number, code=fill.code)
            self._request_refresh(run, "fill")
            return
        log(logger, logging.DEBUG, "trailing_stop_fill_ignored", reason=reason)

    def _request_outside_refresh(self) -> None:
        """세션 밖에서 보유종목을 다시 읽는다. 짧은 시간에 여러 번 와도 한 번으로 합친다."""
        self._outside_pending = True
        if self._outside is None or self._outside.done():
            self._outside = self._spawn(self._refresh_outside())

    async def _refresh_outside(self) -> None:
        """없는 종목의 기억만 버린다. 시세를 구독하거나 판정하지 않는다.

        읽지 못하면 다시 시도하지 않는다. 다음 체결이나 다음 세션 시작 때 다시 읽는다.
        """
        request_id_var.set("-")
        while True:
            self._outside_pending = False
            connection = self._channel.connection
            try:
                rows = await self._owner.positions(self.spec)
            except AppError as exc:
                log(
                    logger,
                    logging.WARNING,
                    "trailing_stop_holdings_failed",
                    run=None,
                    reasons=["fill"],
                    kind=exc.kind,
                    cause=exc.message,
                    # 세션 밖에서는 다시 시도하지 않는다. 세션 안과 같은 칸을 비워 남긴다.
                    failed_since=None,
                    wait_seconds=None,
                )
                return
            if self._run is not None:
                # 그사이 세션이 시작됐다. 세션의 다시 읽기가 반영한다.
                log(
                    logger,
                    logging.INFO,
                    "trailing_stop_holdings_discarded",
                    run=None,
                    reason="run",
                )
                return
            if self._outside_pending:
                log(
                    logger,
                    logging.INFO,
                    "trailing_stop_holdings_discarded",
                    run=None,
                    reason="newer_trigger",
                )
                continue
            checked = self._use_quantities(rows, connection)
            log(
                logger,
                logging.INFO,
                "trailing_stop_holdings_refreshed",
                run=None,
                reasons=["fill"],
                count=len(checked.eligible),
                excluded=len(checked.eligible.keys() & self._excluded),
                memory_count=len(self.memory),
                unknown_exchange_codes=checked.unknown_exchange_codes,
                **checked.counts,
            )
            return

    def _subtract(self, fill: Fill) -> None:
        """전량 매도 직후 다시 사서 합쳐진 다시 읽기에 보유 중으로 나와도 예전 고점이 남지 않게,
        다시 읽기를 기다리지 않고 매도 수량을 빼서 0 이하면 바로 기억을 버린다."""
        code = fill.code
        if fill.side != "sell" or code is None or code not in self.memory:
            return
        held = self._quantities.get(code)
        if held is None or fill.quantity is None:
            return
        left = held - fill.quantity
        self._quantities[code] = left
        if left <= 0:
            self._forget(code, "sold_out")

    def _judge(self, item: dict[str, Any], event: RealtimeEvent) -> None:
        code = str(item.get("item") or "").strip()
        values = item.get("values")
        values = values if isinstance(values, dict) else {}
        log(
            logger,
            logging.DEBUG,
            "trailing_stop_quote_received",
            code=code,
            price=values.get(PRICE_FIELD),
        )
        if event.connection != self._channel.connection:
            log(
                logger,
                logging.DEBUG,
                "trailing_stop_quote_dropped",
                code=code,
                reason="old_connection",
            )
            return
        run = self._run
        if run is None:
            self._skip(code, "outside_session")
            return
        found = self._session_at(event.received_at)
        if event.received_at < run.started_at or found is None or not run.covers(found):
            # 큐 처리가 세션 경계를 넘어 밀려도 앞 세션의 시세를 지금 세션 기준으로 판정하지 않는다.
            log(logger, logging.DEBUG, "trailing_stop_quote_dropped", code=code, reason="other_run")
            return
        reason = self._skip_reason(code, values)
        if reason is not None:
            self._skip(code, reason)
            return
        price = self._price(code, values)
        if price is None:
            return
        self._skips.pop(code, None)
        at = event.received_at
        self._last_prices[code] = (price, at)
        # 기준 값은 그 시세 체결 시각에 켜진 세션의 값이다. 켜진 세션은 두 값이 모두 있다.
        session = found[1]
        activation = Decimal(session["activation_profit"])
        drop = Decimal(session["drop_from_peak"])
        purchase = self._holdings[code].purchase_price
        memory = self.memory.get(code)
        if memory is None:
            if price >= purchase * (1 + activation / 100):
                memory = self.memory[code] = Memory(
                    activated_at=at, peak=price, peak_at=at, purchase_price=purchase
                )
                self._log_judged(
                    "trailing_stop_activated", code, price, purchase, memory, run, session
                )
                self._write("activated")
            return
        if memory.triggered_at is not None:
            # 트리거 뒤에는 고점을 바꾸지 않는다. 이 실행에서 아직 팔지 않았으면 판다.
            if code not in run.auto_sells:
                self._start_sell(run, code, memory, session, uuid.uuid4().hex[:12], retry=True)
            return
        if price > memory.peak:
            # 고점 갱신은 시세 체결마다 로그로 남기거나 파일에 쓰지 않고 모아서 쓴다.
            memory.peak, memory.peak_at = price, at
            self._peaks.add(code)
            self._unwritten = True
            if self._flush is None or self._flush.done():
                self._flush = self._spawn(self._write_later())
        elif price <= memory.peak * (1 - drop / 100):
            memory.triggered_at, memory.triggered_price = at, price
            # 트리거 로그, 자동 매도, 그 체결 알림을 같은 요청 ID로 잇는다.
            request_id = uuid.uuid4().hex[:12]
            self._log_judged(
                "trailing_stop_triggered", code, price, purchase, memory, run, session, request_id
            )
            self._write("triggered")
            self._start_sell(run, code, memory, session, request_id, retry=False)

    def _log_judged(
        self,
        event: str,
        code: str,
        price: Decimal,
        purchase: Decimal,
        memory: Memory,
        run: _Run,
        session: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        """활성화·트리거 로그. 나중에 판정이 맞았는지 확인하도록 요청 ID와 적용한 기준을 담는다."""
        token = request_id_var.set(request_id or uuid.uuid4().hex[:12])
        try:
            log(
                logger,
                logging.INFO,
                event,
                code=code,
                **_run_fields(run),
                price=format_decimal(price),
                purchase_price=format_decimal(purchase),
                activation_profit=session["activation_profit"],
                drop_from_peak=session["drop_from_peak"],
                peak=format_decimal(memory.peak),
                peak_at=format_moment(memory.peak_at),
                activated_at=format_moment(memory.activated_at),
            )
        finally:
            request_id_var.reset(token)

    def _start_sell(
        self,
        run: _Run,
        code: str,
        memory: Memory,
        session: dict[str, Any],
        request_id: str,
        retry: bool,
    ) -> None:
        """자동 매도를 따로 돌린다. 시세 처리는 주문 결과를 기다리지 않는다.

        이 실행에서 그 종목은 결과와 상관없이 다시 내지 않는다. 다시 시도는 앞 실행에서
        트리거된 종목을 새 실행의 첫 시세 체결에서 파는 것이다. 알림의 트리거 칸은
        기억한 트리거 가격과 고점, 기준은 지금 켜진 세션의 값이다.
        """
        holding = self._holdings[code]
        assert memory.triggered_at is not None and memory.triggered_price is not None
        trigger = SellTrigger(
            code,
            holding.name,
            holding.exchange,
            memory.triggered_price,
            memory.triggered_at,
            memory.peak,
            holding.purchase_price,
            Decimal(session["activation_profit"]),
            Decimal(session["drop_from_peak"]),
            _span(session),
            retry,
        )
        run.auto_sells[code] = _attempt("sending", None, None, None, self._owner.now())
        token = request_id_var.set(request_id)
        try:
            log(
                logger,
                logging.INFO,
                "trailing_stop_auto_sell_started",
                code=code,
                run=run.number,
                attempt="retry" if retry else "trigger",
            )
            # 요청 ID는 만들어질 때의 문맥을 따라 매도 작업으로 넘어간다.
            self._spawn(self._sell(run, trigger, request_id))
        finally:
            request_id_var.reset(token)

    async def _sell(self, run: _Run, trigger: SellTrigger, request_id: str) -> None:
        """트리거 때 읽은 거래소로 판다. 국내는 거래소가 없어 빈 값으로 보낸다."""
        code = trigger.code
        result = await self._owner.auto_sell.sell(
            self.spec,
            code,
            trigger.exchange or "",
            source=AUTO_SELL_SOURCE,
            request_id=request_id,
            ready=lambda: self._may_send(run, code),
            confirm=lambda holding: self._confirm_holding(run, code, holding),
        )
        run.auto_sells[code] = _result_attempt(result, self._owner.now())
        log(
            logger,
            logging.INFO if result.outcome in ("accepted", "skipped") else logging.WARNING,
            "trailing_stop_auto_sell_finished",
            code=code,
            run=run.number,
            outcome=result.outcome,
            quantity=result.quantity,
            order_no=result.order_no,
            cause=result.cause,
        )
        embed = RESULT_EMBEDS.get(result.outcome)
        if embed is not None:
            sent = await self._owner.notifier.send(
                embed=embed(self.spec, trigger, result), spec=self.spec
            )
            log(
                logger,
                logging.INFO,
                "trailing_stop_notified",
                reason=f"auto_sell_{result.outcome}",
                code=code,
                sent=sent,
            )

    def _confirm_holding(self, run: _Run, code: str, holding: dict[str, Any]) -> bool:
        """주문 직전에 다시 읽은 평균 매입가를 보유종목을 다시 읽을 때와 같은 규칙으로 판단한다.

        트리거와 주문 사이에 연결이 끊긴 채 팔고 다시 산 새 보유분을 예전 트리거로 팔지 않는다.
        보유하지 않으면 판단하지 않는다(매도 가능 수량 0으로 보내지 않는다).
        """
        purchase = holding.get("purchase_price")
        if purchase is None or code not in self.memory:
            return True
        if self._reconcile_purchase_price(code, purchase, self._channel.connection):
            self._write("purchase_price")
        if code in self.memory:
            return True
        log(
            logger,
            logging.INFO,
            "trailing_stop_send_refused",
            code=code,
            run=run.number,
            reason="purchase_price_changed",
        )
        return False

    def _may_send(self, run: _Run, code: str) -> bool:
        """키움에 보내기 직전 확인. 거절하면 보내지 않고 다음 실행에서 다시 시도한다.

        연결 끊김만으로는 막지 않는다. 끊기기 전 체결가는 실제 체결이었다.
        """
        reason = None
        if self._is_stopped():
            reason = "stopped"
        elif run is not self._run or not run.covers(self._session_at(self._owner.now())):
            reason = "session_changed"
        elif code in self._excluded:
            reason = "excluded"
        elif code not in self.memory:
            # 활성화가 풀렸다(전량 매도, 보유에서 빠짐 등).
            reason = "forgotten"
        if reason is not None:
            log(
                logger,
                logging.INFO,
                "trailing_stop_send_refused",
                code=code,
                run=run.number,
                reason=reason,
            )
        return reason is None

    def _skip_reason(self, code: str, values: dict[str, Any]) -> str | None:
        if self._is_stopped():
            return "stopped"
        if self._quotes_rejected:
            return "subscription_rejected"
        if self._fills_rejected:
            return "fills_subscription_rejected"
        if not self._holdings_ready:
            return "holdings_pending"
        if not is_regular_market(self.spec.market, values):
            return "not_regular_market"
        if code not in self._holdings or code in self._excluded:
            return "not_watched"
        return None

    def _skip(self, code: str, reason: str) -> None:
        """같은 이유가 이어지면 처음 한 번만 INFO로 남긴다."""
        if self._skips.get(code) == reason:
            return
        self._skips[code] = reason
        log(logger, logging.INFO, "trailing_stop_judge_skipped", code=code, reason=reason)

    def _price(self, code: str, values: dict[str, Any]) -> Decimal | None:
        price, reason = read_price(values)
        if price is not None:
            self._invalid.pop(code, None)
            return price
        if reason is not None and self._invalid.get(code) != reason:
            self._invalid[code] = reason
            log(
                logger,
                logging.WARNING,
                "trailing_stop_price_unreadable",
                code=code,
                reason=reason,
                raw=str(values.get(PRICE_FIELD))[:40],
            )
        return None

    def _alert_holdings_failing(self, failed_since: datetime, cause: str) -> None:
        local = failed_since.astimezone(ZoneInfo(TIMEZONES[self.spec.market]))
        self._alert(
            "holdings_failing",
            "Trailing Stop 판정 중단",
            f"보유종목을 {int(HOLDINGS_ALERT_AFTER.total_seconds() // 60)}분 넘게 읽지 못해 "
            "판정을 멈췄습니다. 계속 다시 읽고, 성공하면 판정을 이어 갑니다.",
            EmbedField("첫 실패", f"{local:%Y-%m-%d %H:%M:%S} ({TIMEZONES[self.spec.market]})"),
            EmbedField("원인", cause),
        )

    def _alert_state_unreadable(self) -> None:
        self._alert(
            "state_unreadable",
            "Trailing Stop 판정 중단",
            "Trailing Stop 고점 기록을 읽지 못해 이 환경의 Trailing Stop 판정을 멈췄습니다. "
            "파일을 고치거나 지운 뒤 Trailing Stop 화면에서 다시 저장하면 그때부터 동작합니다. "
            "지우면 기억하던 고점이 사라집니다.",
            EmbedField("파일", str(self._record)),
        )

    def _alert_write_failed(self, cause: str) -> None:
        self._alert(
            "record_write_failed",
            "Trailing Stop 고점 기록 저장 실패",
            "고점 기록 파일에 쓰지 못했습니다. 판정은 메모리로 이어 가고, 다음에 쓸 때 다시 "
            "씁니다. 그 전에 서버를 다시 시작하면 마지막으로 쓴 고점으로 돌아갑니다.",
            EmbedField("파일", str(self._record)),
            EmbedField("원인", cause),
        )

    def _alert(self, reason: str, title: str, description: str, *fields: EmbedField) -> None:
        """판정·이벤트 처리가 기다리지 않게 따로 보낸다."""
        embed = Embed(
            title=title,
            description=description,
            color=NEUTRAL_COLOR,
            fields=(EmbedField("투자 환경", self.spec.label, inline=True), *fields),
        )

        async def send() -> None:
            sent = await self._owner.notifier.send(embed=embed, spec=self.spec)
            log(logger, logging.INFO, "trailing_stop_notified", reason=reason, sent=sent)

        self._spawn(send())

    def _spawn(self, work: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


def _span(session: dict[str, Any]) -> str:
    return f"{session['start']}-{session['end']}"


def _attempt(
    state: str, reason: str | None, quantity: int | None, order_no: str | None, at: datetime
) -> dict[str, Any]:
    """상태 조회의 auto_sell 모양."""
    return {
        "state": state,
        "reason": reason,
        "quantity": quantity,
        "order_no": order_no,
        "at": format_moment(at),
    }


def _result_attempt(result: AutoSellResult, at: datetime) -> dict[str, Any]:
    if result.outcome == "accepted":
        return _attempt("accepted", None, result.quantity, result.order_no, at)
    # 실패 이유는 결과 이름 그대로다. 매도 가능 수량 0(skipped)만 이름을 바꾼다.
    reason = "no_sellable" if result.outcome == "skipped" else result.outcome
    return _attempt("failed", reason, result.quantity, None, at)


def _run_fields(run: _Run) -> dict[str, Any]:
    return {"run": run.number, "session": run.span, "day": run.day.isoformat()}
