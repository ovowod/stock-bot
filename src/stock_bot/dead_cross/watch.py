"""Dead Cross 감시: 모의투자 보유종목의 차트로 이동평균을 내고 하향 돌파를 판정한다.

켜진 Dead Cross 세션 동안 보유종목을 읽고 종목마다 차트(REST)를 조회한다. 판정 뒤 데드크로스
발생이 남아 있으면 공용 자동 매도(AutoSell)로 매도 가능 수량 전량을 시장가로 판다. 기억(마지막
판정 봉·데드크로스 발생)은 투자 환경마다 메모리에 두고 판정 기록 파일(record.py)에 써서,
세션과 날짜를 넘기고 서버를 다시 시작해도 이어 간다.

조회 시점: 세션 시작, 설정 저장, 내 계좌 체결, 분봉이 끝난 5초 뒤. 투자 환경마다 조회 차례는
하나만 돌고, 도는 동안 들어온 요청은 끝난 뒤 한 차례로 합친다. 차례마다 먼저 보유종목을 다시 읽는다.
시세는 구독하지 않는다. 공유 실시간 연결은 내 계좌 체결을 받고, 연결이 밀려났는지 알려고 쓴다.
실전투자 환경은 만들지 않는다. 시세 대신 시계로 움직이므로 토·일에는 세션이 없다.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from stock_bot.auto_trading.settings import TIMEZONES, FeatureSettingsStore
from stock_bot.core.config import ENVIRONMENTS, EnvironmentSpec, Market
from stock_bot.core.errors import AppError
from stock_bot.core.logging_setup import environment_var, log, request_id_var
from stock_bot.core.notification import DiscordNotifier
from stock_bot.dead_cross import record
from stock_bot.dead_cross.alerts import RESULT_EMBEDS, SellTrigger
from stock_bot.dead_cross.bars import (
    Bar,
    chart_request,
    completed,
    moving_average,
    next_bar_end,
    read_bars,
)
from stock_bot.dead_cross.record import Basis, Memory, format_moment
from stock_bot.dead_cross.settings import dead_cross_session_at
from stock_bot.fill.feed import FeedState, FeedStatus, Fill, FillFeed
from stock_bot.kiwoom.kiwoom import KiwoomClient
from stock_bot.kiwoom.realtime import EventKind, RealtimeEvent, RealtimeHub
from stock_bot.kiwoom.us_exchange import US_EXCHANGE_CODES
from stock_bot.order.auto_sell import AutoSell
from stock_bot.order.order import AutoSellResult

logger = logging.getLogger("stock_bot.dead_cross_watch")

# 지금 켜진 세션과 봉이 끝났는지를 다시 보는 간격.
SESSION_CHECK_SECONDS = 1.0
# 분봉이 끝난 뒤 조회하기까지 기다리는 시간. 키움에 그 봉이 반영될 틈을 둔다.
BAR_CLOSE_DELAY = timedelta(seconds=5)
# 보유종목·차트 조회가 실패하면 이 간격으로 다시 시도한다. 실패가 이어지면 두 배씩 늘린다.
RETRY_INITIAL_SECONDS = 1.0
RETRY_MAX_SECONDS = 60.0
# 화면에 보이는 이동평균 자리수. 국내는 원 단위 가격의 소수 둘째, 미국은 달러 가격의 소수 넷째다.
MA_PLACES = {Market.DOMESTIC: Decimal("0.01"), Market.US: Decimal("0.0001")}
# 공용 자동 매도에 알리는 이 기능의 이름. 체결 알림에 그대로 붙는다.
AUTO_SELL_SOURCE = "Dead Cross"
# 상태 조회와 판정 기록 파일에 남기는 최근 자동 매도 수.
RECENT_SELLS_LIMIT = 10

PositionLookup = Callable[[EnvironmentSpec], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class WatchState:
    """상태 조회가 읽는 감시의 지금 모습. 설정도 감시가 쓰고 있는 것을 그대로 준다."""

    watching: bool
    not_watching_reason: str | None
    stocks: list[dict[str, Any]]
    # 감시가 쓰고 있는 Dead Cross 세션과 제외종목. 설정 파일을 읽지 못해 멈춰 있으면 None이다.
    sessions: list[dict[str, Any]] | None
    excluded: list[str]
    # 최근 자동 매도. 최신이 앞이다.
    recent_sells: list[dict[str, Any]]


@dataclass(frozen=True)
class _Held:
    """판정할 수 있는 보유종목 한 줄."""

    name: str
    # 미국 종목의 거래소. 국내는 None이다.
    exchange: str | None
    # 평균 매입가. 없으면 None이다. 매입가가 없어도 판정하고 판다.
    purchase_price: Decimal | None


@dataclass(frozen=True)
class _Run:
    """켜진 Dead Cross 세션이 새로 시작될 때마다 하나. 기억은 실행에 묶이지 않는다."""

    number: int
    day: date
    span: str
    # 종목코드 -> 이 실행에서 낸 가장 최근 자동 매도. 상태 조회의 auto_sell 모양이다.
    auto_sells: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 이 실행에서 자동 매도를 시도한 (종목코드, 발생 봉). 같은 발생 봉으로 다시 내지 않는다.
    attempted: set[tuple[str, datetime]] = field(default_factory=set)

    def covers(self, found: tuple[date, dict[str, Any]] | None) -> bool:
        """그 날짜·세션이 이 실행의 것인가. 기준만 바뀌면 같은 실행이다."""
        return found is not None and found[0] == self.day and _span(found[1]) == self.span


@dataclass(frozen=True)
class _Values:
    """화면에 보일 가장 최근 완료 봉의 이동평균과 그 기준.

    판정과 따로, 조회할 때마다 다시 계산한다.
    """

    short: Decimal
    long: Decimal
    basis: Basis


class DeadCrossWatcher:
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
        # 내 계좌 체결. 체결되면 보유종목이 바뀌므로 다시 읽는다.
        self.feed = feed
        self.notifier = notifier
        self.now = now
        # 투자 환경마다 판정 기록 파일 하나를 둔다.
        self.record_dir = record_dir
        self._watches: list[_Watch] = []

    def start(self) -> None:
        """모의투자 환경만 지켜본다. 인증정보가 없는 환경은 건너뛴다."""
        for spec in (s for s in ENVIRONMENTS.values() if not s.is_real):
            environment_var.set(spec.environment.value)
            try:
                self.kiwoom.credentials(spec)
            except AppError as exc:
                log(logger, logging.INFO, "dead_cross_watch_skipped", cause=exc.message)
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
    """투자 환경 하나의 Dead Cross 감시."""

    def __init__(self, owner: DeadCrossWatcher, spec: EnvironmentSpec) -> None:
        self._owner = owner
        self.spec = spec
        self._zone = ZoneInfo(TIMEZONES[spec.market])
        # 서버 종료로 멈췄다. 연결 밀려남은 채널이 바로 알려 준다.
        self._stopped = False
        # 시작 때 설정 파일을 읽지 못했다. 정상 설정이 저장될 때까지 멈춰 있다.
        self._settings_broken = False
        # 판정 기록 파일을 읽지 못했다. 고치거나 지운 뒤 설정을 저장해 다시 읽을 때까지 멈춰 있다.
        # 그동안 깨진 파일을 덮어쓰지 않는다.
        self._state_broken = False
        self._record = owner.record_dir / f"{spec.environment.value}.json"
        self._sessions: list[dict[str, Any]] = []
        self._excluded: set[str] = set()
        # 설정을 저장할 때마다 는다. 조회를 시작한 뒤 바뀌었으면 그 응답을 버린다.
        self._settings_version = 0
        self._run: _Run | None = None
        self._runs = 0
        # 다음에 봉이 끝났다고 보고 조회할 시각. 일봉 세션과 세션 밖은 None이다.
        self._due: datetime | None = None
        # 현금잔고이고 판정할 수 있는 종목만 담는다. 제외종목도 담는다.
        self._holdings: dict[str, _Held] = {}
        # 현금잔고가 있지만 판정할 수 없는 종목코드 -> 이유. 신용 보유분만 있는 종목은 담지 않는다.
        self._ineligible: dict[str, str] = {}
        self._holdings_ready = False
        # 종목코드 -> 기억. 판정 기록 파일보다 이것이 기준이다.
        self.memory: dict[str, Memory] = {}
        # 최근 자동 매도. 최신이 앞이다. 기억과 함께 판정 기록 파일에 쓴다.
        self._recent_sells: list[dict[str, Any]] = []
        # 종목코드 -> 화면에 보일 이동평균. 기억과 함께 버린다.
        self._values: dict[str, _Values] = {}
        # 종목코드 -> 기억을 버린 횟수. 조회하는 동안 버렸으면 그 응답을 버린다.
        self._epochs: dict[str, int] = {}
        # 마지막 조회가 실패한 종목, 봉이 모자란 종목.
        self._failed: set[str] = set()
        self._short_of_bars: set[str] = set()
        # 조회 차례와, 도는 동안 들어온 요청. 새 요청이 오면 다시 시도 대기를 깨운다.
        self._round: asyncio.Task[None] | None = None
        self._pending: list[str] = []
        self._requested = asyncio.Event()
        # 세션 밖 보유종목 다시 읽기와, 읽는 동안 체결이 또 왔는지.
        self._outside: asyncio.Task[None] | None = None
        self._outside_pending = False
        self._fills_rejected = False
        self._unsubscribe_fills: Callable[[], None] | None = None
        # 연결이 LOGIN까지 되면 깨운다. 보유종목은 연결이 있을 때 읽어야 그 뒤 체결을 놓치지 않는다.
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        # 시세는 구독하지 않는다. 연결 번호와 밀려남을 알려고 채널만 연다.
        self._channel = owner.hub.channel(spec, "dead_cross", "Dead Cross", self._on_event)

    def start(self) -> None:
        self._spawn(self._tick())
        # 설정보다 먼저 읽는다. 설정 파일이 깨져 있어도 기억을 잃지 않고, 세션 밖 체결을 처리한다.
        if not self._read_record("start"):
            self._state_broken = True
            log(logger, logging.ERROR, "dead_cross_watch_stopped", reason="state_unreadable")
        try:
            settings = self._owner.settings.read(self.spec)
        except AppError as exc:
            # 깨진 설정을 빈 설정으로 착각해 동작하지 않는다. 알림은 SL / TP가 같은 파일로 보낸다.
            self._settings_broken = True
            log(
                logger,
                logging.ERROR,
                "dead_cross_watch_stopped",
                reason="settings_unreadable",
                cause=exc.message,
            )
            self._sync_fills()
            return
        self._apply(settings)
        # 서버가 꺼진 사이 제외종목에 넣었으면 그 기억은 버린다.
        self._forget_excluded()
        self._refresh_run()
        self._sync_fills()

    async def stop(self) -> None:
        self._stopped = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def settings_saved(self, settings: dict[str, Any]) -> None:
        token = environment_var.set(self.spec.environment.value)
        try:
            if self._state_broken and self._read_record("settings_saved"):
                self._state_broken = False
                log(logger, logging.INFO, "dead_cross_watch_resumed", reason="state_recovered")
            if self._settings_broken:
                log(logger, logging.INFO, "dead_cross_watch_resumed", reason="settings_saved")
            self._settings_broken = False
            run = self._run
            self._apply(settings)
            self._settings_version += 1
            self._forget_excluded()
            self._refresh_run()
            self._sync_fills()
            if self._run is run and run is not None:
                # 같은 실행 안에서 기준이나 제외종목이 바뀌었다. 새 기준으로 다시 조회한다.
                self._schedule()
                self._request_round("settings_saved")
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
            [self._stock(code, reason) for code in codes],
            None if self._settings_broken else self._sessions,
            sorted(self._excluded),
            self._recent_sells,
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
        if not self._holdings_ready:
            return "holdings_pending"
        return None

    def _stock(self, code: str, stopped: str | None) -> dict[str, Any]:
        """status는 waiting·tracking·crossed·ineligible이다. reason은 계산 대기의 이유나,
        추적 중·데드크로스 발생인데 지금 갱신이 멈춘 이유다."""
        memory = self.memory.get(code)
        values = self._values.get(code)
        ineligible = None if stopped is not None else self._ineligible.get(code)
        if ineligible is not None:
            status, reason = "ineligible", None
        elif memory is not None:
            status = "tracking" if memory.crossed_at is None else "crossed"
            reason = stopped or ("chart_failed" if code in self._failed else None)
        else:
            status = "waiting"
            if stopped is not None:
                reason = stopped
            elif code in self._excluded:
                reason = "excluded"
            elif code in self._failed:
                reason = "chart_failed"
            elif code in self._short_of_bars:
                reason = "not_enough_bars"
            else:
                # 보유종목을 읽었고 아직 차트를 받기 전이다.
                reason = None
        places = MA_PLACES[self.spec.market]
        return {
            "code": code,
            "status": status,
            "reason": reason,
            "ineligible_reason": ineligible,
            "short_ma": None if values is None else _format_ma(values.short, places),
            "long_ma": None if values is None else _format_ma(values.long, places),
            "basis": None if values is None else _basis(values.basis),
            "judged_bar": None if memory is None else format_moment(memory.judged_bar),
            "crossed_at": None if memory is None else format_moment(memory.crossed_at),
            "crossed_bar": None if memory is None else format_moment(memory.crossed_bar),
            "auto_sell": None if self._run is None else self._run.auto_sells.get(code),
        }

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(SESSION_CHECK_SECONDS)
            try:
                self._refresh_run()
                if (
                    self._run is not None
                    and self._due is not None
                    and self._owner.now() >= self._due
                ):
                    self._schedule()
                    self._request_round("bar_closed")
            except Exception as exc:
                # 한 번의 계산 오류로 세션 전환이 멈추지 않게 한다.
                logger.error(
                    "dead_cross_session_check_failed",
                    exc_info=exc,
                    extra={"fields": {"error_type": type(exc).__name__}},
                )

    def _apply(self, settings: dict[str, Any]) -> None:
        self._excluded = {item["code"] for item in settings["excluded"]}
        self._sessions = settings["sessions"]

    def _session_at(self, moment: datetime) -> tuple[date, dict[str, Any]] | None:
        return dead_cross_session_at(self._sessions, moment, self.spec.market)

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
                self._sync_fills()
            return
        self._runs += 1
        new = _Run(self._runs, found[0], _span(found[1]))
        self._run = new
        log(
            logger,
            logging.INFO,
            "dead_cross_session_started",
            **_run_fields(new),
            bar_period=found[1]["bar_period"],
            short_period=found[1]["short_period"],
            long_period=found[1]["long_period"],
            previous_run=None if run is None else run.number,
            memory_count=len(self.memory),
        )
        self._sync_fills()
        self._schedule()
        self._request_round("session_started")

    def _schedule(self) -> None:
        """다음에 봉이 끝났다고 볼 시각을 정한다. 시계로 정하므로 종목과 상관없다."""
        found = self._session_at(self._owner.now())
        if self._run is None or found is None:
            self._due = None
            return
        now = self._owner.now()
        end = next_bar_end(found[1]["bar_period"], self.spec.market, now - BAR_CLOSE_DELAY)
        self._due = None if end is None else end + BAR_CLOSE_DELAY

    def _sync_fills(self) -> None:
        """세션 동안, 그리고 세션 밖에서도 기억이 있는 동안 내 계좌 체결을 받는다.

        세션 밖에서 전량 판 종목의 기억을 버리려는 것이다.
        """
        wanted = self._run is not None or bool(self.memory)
        if wanted and self._unsubscribe_fills is None:
            self._unsubscribe_fills = self._owner.feed.subscribe(
                self.spec, "dead_cross", "Dead Cross", self._on_feed
            )
        elif not wanted and self._unsubscribe_fills is not None:
            self._unsubscribe_fills()
            self._unsubscribe_fills = None

    def _end_run(self, run: _Run) -> None:
        log(logger, logging.INFO, "dead_cross_session_ended", **_run_fields(run))
        self._run = None
        self._due = None
        self._holdings_ready = False
        self._pending.clear()
        self._failed.clear()
        self._short_of_bars.clear()
        if self._round is not None and not self._round.done():
            self._round.cancel()
            log(logger, logging.INFO, "dead_cross_round_cancelled", run=run.number)
        # 취소한 차례는 아직 끝나지 않았다. 새 실행의 요청이 그것을 기다리지 않게 놓는다.
        self._round = None

    def _request_round(self, reason: str) -> None:
        """조회 차례를 요청한다. 돌고 있으면 끝난 뒤 한 차례로 합쳐 다시 돈다."""
        run = self._run
        if run is None or self._is_stopped():
            return
        self._pending.append(reason)
        self._requested.set()
        if self._round is None or self._round.done():
            self._round = self._spawn(self._rounds(run))

    async def _rounds(self, run: _Run) -> None:
        delay = RETRY_INITIAL_SECONDS
        # 보유종목을 읽지 못해 차트를 받지 못한 차례가 있다. 다시 시도는 모든 종목을 받아야 한다.
        missed_round = False
        while self._pending:
            # 연결이 없으면 읽지 않고 기다린다. 읽은 뒤 연결되면 그사이 체결을 놓친다.
            while self._channel.connection is None:
                if run is not self._run or self._is_stopped():
                    return
                self._wake.clear()
                await self._wake.wait()
            if run is not self._run or self._is_stopped() or self._fills_rejected:
                # 체결 등록이 거부된 동안은 돌지 않는다. 다시 등록되면 새로 요청한다.
                return
            reasons, self._pending = self._pending, []
            self._requested.clear()
            request_id_var.set(uuid.uuid4().hex[:12])
            log(logger, logging.INFO, "dead_cross_round_started", run=run.number, reasons=reasons)
            ok = await self._read_holdings(run, reasons)
            if not ok:
                missed_round = True
            elif run is self._run:
                # 실패 뒤 다시 시도만 남았으면 실패한 종목만 다시 받는다.
                retry_only = reasons == ["retry"] and not missed_round
                missed_round = False
                ok = await self._fetch_all(run, set(self._failed) if retry_only else None)
            if run is not self._run:
                return
            if ok:
                delay = RETRY_INITIAL_SECONDS
                continue
            if not self._pending:
                # 새 요청이 오면 기다리지 않고 바로 다시 돈다.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._requested.wait(), delay)
                delay = min(delay * 2, RETRY_MAX_SECONDS)
                self._pending.append("retry")

    async def _read_holdings(self, run: _Run, reasons: list[str]) -> bool:
        try:
            rows = await self._owner.positions(self.spec)
        except AppError as exc:
            self._holdings_ready = False
            log(
                logger,
                logging.WARNING,
                "dead_cross_holdings_failed",
                run=run.number,
                reasons=reasons,
                kind=exc.kind,
                cause=exc.message,
            )
            return False
        if run is not self._run or self._is_stopped():
            return False
        self._use_holdings(rows)
        log(
            logger,
            logging.INFO,
            "dead_cross_holdings_refreshed",
            run=run.number,
            reasons=reasons,
            count=len(self._holdings),
            excluded=len(self._holdings.keys() & self._excluded),
            ineligible=self._ineligible,
            memory_count=len(self.memory),
        )
        self._holdings_ready = True
        return True

    def _use_holdings(self, rows: list[dict[str, Any]]) -> None:
        """보유종목을 바꾸고, 현금잔고가 없는 종목의 기억은 버린다.

        평균 매입가가 바뀐 종목은 발생을 푼다. 기억의 값을 두어 _fetch가 판정을 다시 시작한다.
        """
        eligible, ineligible = _check_holdings(self.spec.market, rows)
        cash = {row["code"] for row in rows if not row["credit"]}
        for code in sorted(self.memory.keys() - cash):
            self._forget(code, "not_held")
        for code in sorted(self._values.keys() - cash):
            self._values.pop(code)
        self._check_purchase_prices(eligible)
        self._holdings = eligible
        self._ineligible = {c: r for c, r in ineligible.items() if r != "credit"}

    async def _fetch_all(self, run: _Run, only: set[str] | None = None) -> bool:
        """판정 대상 종목마다(only를 주면 그 종목만) 차트를 조회해 판정한다. 실패가 있으면 False."""
        ok = True
        for code in sorted(self._holdings if only is None else self._holdings.keys() & only):
            if run is not self._run or self._is_stopped():
                return ok
            if code in self._excluded or code not in self._holdings:
                continue
            if not await self._fetch(run, code, self._holdings[code].exchange):
                ok = False
        return ok

    async def _fetch(self, run: _Run, code: str, exchange: str | None) -> bool:
        found = self._session_at(self._owner.now())
        if found is None:
            return True
        session = found[1]
        basis = _session_basis(session)
        version, epoch = self._settings_version, self._epochs.get(code, 0)
        memory = self.memory.get(code)
        judged = memory.judged_bar if memory is not None and memory.basis == basis else None
        purchase_changed = memory is not None and _purchase_changed(
            memory, self._holdings[code].purchase_price
        )
        if purchase_changed:
            judged = None
        now = self._owner.now()
        request = chart_request(
            self.spec.market, code, exchange, basis.bar_period, now.astimezone(self._zone).date()
        )
        market = self.spec.market

        def enough(rows: list[Any]) -> bool:
            bars = completed(read_bars(market, basis.bar_period, rows), basis.bar_period, now)
            return _covered(bars, judged, basis.long_period)

        token = request_id_var.set(uuid.uuid4().hex[:12])
        try:
            try:
                rows, ended, pages = await self._owner.kiwoom.call_until(
                    self.spec, request.api_id, request.path, request.body, request.list_key, enough
                )
                bars = completed(read_bars(market, basis.bar_period, rows), basis.bar_period, now)
            except (AppError, ValueError) as exc:
                self._failed.add(code)
                log(
                    logger,
                    logging.WARNING,
                    "dead_cross_chart_failed",
                    run=run.number,
                    code=code,
                    api_id=request.api_id,
                    bar_period=basis.bar_period,
                    error_type=type(exc).__name__,
                    cause=exc.message if isinstance(exc, AppError) else str(exc),
                )
                return False
            log(
                logger,
                logging.INFO,
                "dead_cross_chart_fetched",
                run=run.number,
                code=code,
                api_id=request.api_id,
                bar_period=basis.bar_period,
                pages=pages,
                rows=len(rows),
                completed=len(bars),
                ended=ended,
            )
            stale = (
                run is not self._run
                or version != self._settings_version
                or epoch != self._epochs.get(code, 0)
                or code not in self._holdings
                or code in self._excluded
            )
            if stale:
                # 조회하는 동안 세션·설정·보유가 바뀌었다. 지운 기억을 되살리지 않게 버린다.
                log(logger, logging.INFO, "dead_cross_chart_discarded", run=run.number, code=code)
                if run is self._run:
                    self._pending.append("stale")
                return True
            self._failed.discard(code)
            if purchase_changed:
                self._restart_judging(run, code, basis, bars)
                return True
            crossed = self._judge(run, code, basis, bars, judged, now)
            self._sell_if_crossed(run, code, session, basis, bars, retry=not crossed)
            return True
        finally:
            request_id_var.reset(token)

    def _judge(
        self,
        run: _Run,
        code: str,
        basis: Basis,
        bars: list[Bar],
        judged: datetime | None,
        now: datetime,
    ) -> bool:
        """마지막 판정 봉 뒤의 완료 봉을 오래된 것부터 차례로 판정한다.

        기억이 없거나 기준이 바뀌었으면 가장 최근 완료 봉 하나만 본다. 처음 보는 종목의 그 봉은
        기준점으로만 둔다. 사기 전에 끝났을 수 있어, 하향 돌파여도 발생으로 치지 않는다.
        이번 판정에서 하향 돌파가 일어나 풀리지 않았으면 True다.
        """
        short, long = basis.short_period, basis.long_period
        self._update_values(code, basis, bars)
        if judged is not None and not any(bar.start <= judged for bar in bars):
            # 키움이 주는 과거 범위가 끝나 마지막 판정 봉까지 받지 못했다.
            log(
                logger,
                logging.WARNING,
                "dead_cross_catch_up_truncated",
                run=run.number,
                code=code,
                judged_bar=format_moment(judged),
                oldest_bar=format_moment(bars[-1].start) if bars else None,
            )
            judged = None
        if judged is None:
            indexes = [0]
        else:
            newer = sum(1 for bar in bars if bar.start > judged)
            indexes = list(range(newer - 1, -1, -1))
        indexes = [k for k in indexes if k + long < len(bars)]
        if not indexes:
            if code not in self.memory:
                self._short_of_bars.add(code)
                log(
                    logger,
                    logging.INFO,
                    "dead_cross_not_enough_bars",
                    run=run.number,
                    code=code,
                    completed=len(bars),
                    needed=long + 1,
                )
            return False
        self._short_of_bars.discard(code)
        memory = self.memory.get(code)
        crossed = False
        for k in indexes:
            bar = bars[k]
            values = (
                moving_average(bars, k, short),
                moving_average(bars, k, long),
                moving_average(bars, k + 1, short),
                moving_average(bars, k + 1, long),
            )
            if memory is None:
                purchase = self._holdings[code].purchase_price
                memory = self.memory[code] = Memory(basis, bar.start, purchase_price=purchase)
                log(
                    logger,
                    logging.INFO,
                    "dead_cross_baseline_set",
                    run=run.number,
                    code=code,
                    bar=format_moment(bar.start),
                )
                continue
            event = _cross(memory, *values)
            if event == "crossed":
                memory.crossed_at, memory.crossed_bar = now, bar.start
                crossed = True
            elif event == "released":
                memory.crossed_at = memory.crossed_bar = None
                crossed = False
            memory.basis, memory.judged_bar = basis, bar.start
            log(
                logger,
                logging.INFO if event else logging.DEBUG,
                f"dead_cross_{event}" if event else "dead_cross_judged",
                run=run.number,
                code=code,
                bar=format_moment(bar.start),
                bar_period=basis.bar_period,
                short_period=short,
                long_period=long,
                short_ma=str(values[0]),
                long_ma=str(values[1]),
                previous_short_ma=str(values[2]),
                previous_long_ma=str(values[3]),
            )
        self._write("judged")
        self._sync_fills()
        return crossed

    def _update_values(self, code: str, basis: Basis, bars: list[Bar]) -> None:
        short, long = basis.short_period, basis.long_period
        if len(bars) >= long:
            self._values[code] = _Values(
                moving_average(bars, 0, short), moving_average(bars, 0, long), basis
            )

    def _restart_judging(self, run: _Run, code: str, basis: Basis, bars: list[Bar]) -> None:
        """평균 매입가가 바뀐 종목의 판정을 가장 최근 완료 봉부터 다시 시작한다.

        팔고 다시 샀을 수 있어, 그때까지의 봉은 판정하지 않고 이 조회에서는 팔지 않는다.
        재매수 이전의 하향 돌파가 차례 판정으로 되살아나지 않게 한다. 받은 봉이 없으면
        기억의 값을 그대로 두어 다음 조회에서 다시 한다.
        """
        self._update_values(code, basis, bars)
        if not bars:
            return
        memory = self.memory[code]
        previous = memory.purchase_price
        memory.basis, memory.judged_bar = basis, bars[0].start
        memory.purchase_price = self._holdings[code].purchase_price
        log(
            logger,
            logging.INFO,
            "dead_cross_judging_restarted",
            run=run.number,
            code=code,
            judged_bar=format_moment(bars[0].start),
            previous_purchase_price=str(previous),
            purchase_price=str(memory.purchase_price),
        )
        self._write("judging_restarted")

    def _sell_if_crossed(
        self,
        run: _Run,
        code: str,
        session: dict[str, Any],
        basis: Basis,
        bars: list[Bar],
        retry: bool,
    ) -> None:
        """판정을 끝낸 종목이 데드크로스 발생이면 자동 매도를 따로 돌린다.

        조회 차례는 주문 결과를 기다리지 않는다. 같은 발생 봉으로는 이 실행에서 한 번만 내므로,
        첫 매도와 다음 실행의 다시 시도를 이 한 곳이 다룬다. 이 응답의 가장 최근 완료 봉까지
        지금 기준으로 판정하지 못했으면(기준 변경 뒤 봉 부족, 빈 응답) 내지 않는다.
        """
        memory = self.memory.get(code)
        if memory is None or memory.crossed_bar is None or memory.basis != basis:
            return
        if len(bars) < basis.long_period or bars[0].start != memory.judged_bar:
            return
        if (code, memory.crossed_bar) in run.attempted:
            return
        run.attempted.add((code, memory.crossed_bar))
        holding = self._holdings[code]
        values = self._values[code]
        places = MA_PLACES[self.spec.market]
        trigger = SellTrigger(
            code=code,
            name=holding.name,
            exchange=holding.exchange,
            crossed_bar=memory.crossed_bar,
            basis=basis,
            short_ma=values.short.quantize(places, rounding=ROUND_HALF_UP),
            long_ma=values.long.quantize(places, rounding=ROUND_HALF_UP),
            session=_span(session),
            retry=retry,
        )
        attempt = run.auto_sells[code] = _attempt("sending", None, None, None, self._owner.now())
        # 주문을 보내기 전에 저장한다. 응답 전에 서버가 꺼져도 시도가 목록에 남는다.
        recent = {
            "code": code,
            "name": holding.name,
            "crossed_bar": format_moment(memory.crossed_bar),
            "retry": retry,
            **attempt,
        }
        self._recent_sells.insert(0, recent)
        del self._recent_sells[RECENT_SELLS_LIMIT:]
        self._write("auto_sell_started")
        log(
            logger,
            logging.INFO,
            "dead_cross_auto_sell_started",
            code=code,
            run=run.number,
            attempt="retry" if retry else "trigger",
            crossed_bar=format_moment(memory.crossed_bar),
        )
        # 차트 조회의 요청 ID가 만들어질 때의 문맥을 따라 매도 작업으로 넘어간다.
        self._spawn(self._sell(run, trigger, recent, request_id_var.get()))

    async def _sell(
        self, run: _Run, trigger: SellTrigger, recent: dict[str, Any], request_id: str
    ) -> None:
        """국내는 거래소가 없어 빈 값으로 보낸다."""
        code = trigger.code
        result = await self._owner.auto_sell.sell(
            self.spec,
            code,
            trigger.exchange or "",
            source=AUTO_SELL_SOURCE,
            request_id=request_id,
            ready=lambda: self._may_send(run, trigger),
            confirm=lambda holding: self._confirm_holding(run, code, holding),
        )
        attempt = run.auto_sells[code] = _result_attempt(result, self._owner.now())
        recent.update(attempt)
        self._write("auto_sell_finished")
        log(
            logger,
            logging.INFO if result.outcome in ("accepted", "skipped") else logging.WARNING,
            "dead_cross_auto_sell_finished",
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
                "dead_cross_notified",
                reason=f"auto_sell_{result.outcome}",
                code=code,
                sent=sent,
            )

    def _confirm_holding(self, run: _Run, code: str, holding: dict[str, Any]) -> bool:
        """주문 직전에 다시 읽은 평균 매입가가 바뀌었으면 발생을 풀고 보내지 않는다.

        기억이 없으면 판단하지 않는다(보내기 직전 확인이 막는다).
        """
        memory = self.memory.get(code)
        purchase = _known_price(holding.get("purchase_price"))
        if memory is None or not _purchase_changed(memory, purchase):
            return True
        self._release_changed(code, memory, purchase)
        log(
            logger,
            logging.INFO,
            "dead_cross_send_refused",
            code=code,
            run=run.number,
            reason="purchase_price_changed",
        )
        return False

    def _may_send(self, run: _Run, trigger: SellTrigger) -> bool:
        """키움에 보내기 직전 확인. 거절하면 보내지 않고, 발생이 남아 있으면 다음 실행에서 낸다."""
        code = trigger.code
        memory = self.memory.get(code)
        found = self._session_at(self._owner.now())
        reason = None
        if self._is_stopped():
            reason = "stopped"
        elif found is None or run is not self._run or not run.covers(found):
            reason = "session_changed"
        elif _session_basis(found[1]) != trigger.basis:
            reason = "basis_changed"
        elif code in self._excluded:
            reason = "excluded"
        elif memory is None:
            reason = "forgotten"
        elif memory.crossed_bar != trigger.crossed_bar:
            reason = "released"
        if reason is not None:
            log(
                logger,
                logging.INFO,
                "dead_cross_send_refused",
                code=code,
                run=run.number,
                reason=reason,
            )
        return reason is None

    def _check_purchase_prices(self, held: dict[str, _Held]) -> None:
        """다시 읽은 평균 매입가를 기억과 맞춘다. 기억에 값이 없으면 채우고, 다르면 발생을 푼다."""
        for code, holding in held.items():
            memory = self.memory.get(code)
            if memory is None:
                continue
            if memory.purchase_price is None and holding.purchase_price is not None:
                memory.purchase_price = holding.purchase_price
                log(
                    logger,
                    logging.INFO,
                    "dead_cross_purchase_price_filled",
                    code=code,
                    purchase_price=str(holding.purchase_price),
                )
                self._write("purchase_price")
            elif memory.crossed_at is not None and _purchase_changed(
                memory, holding.purchase_price
            ):
                self._release_changed(code, memory, holding.purchase_price)

    def _release_changed(self, code: str, memory: Memory, purchase: Decimal | None) -> None:
        """평균 매입가가 바뀐 종목의 발생을 푼다. 팔고 다시 샀을 수 있어 팔지 않는 쪽으로 간다."""
        log(
            logger,
            logging.INFO,
            "dead_cross_released",
            code=code,
            reason="purchase_price_changed",
            previous_purchase_price=str(memory.purchase_price),
            purchase_price=str(purchase),
        )
        memory.crossed_at = memory.crossed_bar = None
        self._write("released")

    def _forget_excluded(self) -> None:
        """제외종목에 든 종목의 기억과 화면 값을 버린다. 기준만 바꾼 저장으로는 버리지 않는다."""
        for code in sorted(self.memory.keys() & self._excluded):
            self._forget(code, "excluded")
        for code in sorted(self._values.keys() & self._excluded):
            self._values.pop(code)

    def _forget(self, code: str, reason: str) -> None:
        memory = self.memory.pop(code)
        self._values.pop(code, None)
        self._epochs[code] = self._epochs.get(code, 0) + 1
        log(
            logger,
            logging.INFO,
            "dead_cross_memory_forgotten",
            code=code,
            reason=reason,
            crossed=memory.crossed_at is not None,
        )
        self._write("forgotten")
        self._sync_fills()

    def _read_record(self, reason: str) -> bool:
        """판정 기록 파일을 읽어 기억으로 쓴다. 읽지 못하면 기억은 그대로 두고 False다."""
        try:
            loaded = record.load(self._record)
        except (OSError, ValueError) as exc:
            log(
                logger,
                logging.ERROR,
                "dead_cross_record_unreadable",
                path=str(self._record),
                reason=reason,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            return False
        self.memory = loaded.memory
        self._recent_sells = loaded.recent_sells
        self._values.clear()
        log(
            logger,
            logging.INFO,
            "dead_cross_record_read",
            path=str(self._record),
            reason=reason,
            memory_count=len(loaded.memory),
        )
        if loaded.cleared_codes is not None:
            log(
                logger,
                logging.WARNING,
                "dead_cross_record_migrated",
                path=str(self._record),
                cleared_codes=loaded.cleared_codes,
            )
            self._write("migrated")
        return True

    def _write(self, reason: str) -> None:
        """기억 전체를 판정 기록 파일에 쓴다. 쓰지 못하면 메모리의 판정은 그대로 이어 가고,
        다음 쓰기 때 전체를 다시 쓴다."""
        if self._state_broken:
            return
        try:
            record.write(self._record, self.memory, self._recent_sells)
        except OSError as exc:
            log(
                logger,
                logging.ERROR,
                "dead_cross_record_write_failed",
                path=str(self._record),
                reason=reason,
                error_type=type(exc).__name__,
                cause=str(exc),
            )
            return
        log(
            logger,
            logging.DEBUG,
            "dead_cross_record_written",
            reason=reason,
            memory_count=len(self.memory),
        )

    async def _on_event(self, event: RealtimeEvent) -> None:
        environment_var.set(self.spec.environment.value)
        if event.kind is EventKind.CONNECTED:
            self._wake.set()
        elif event.kind is EventKind.STOPPED:
            self._wake.set()
            log(logger, logging.ERROR, "dead_cross_watch_stopped", reason="connection_replaced")

    def _on_feed(self, value: Fill | FeedStatus) -> None:
        environment_var.set(self.spec.environment.value)
        if isinstance(value, Fill):
            self._on_fill(value)
        elif value.state is FeedState.LIVE:
            was_rejected = self._fills_rejected
            self._fills_rejected = False
            if was_rejected:
                # 거부된 동안의 체결은 받지 못했다. 보유종목을 다시 읽은 뒤 판정한다.
                log(logger, logging.INFO, "dead_cross_watch_resumed", reason="fills_registered")
                self._request_round("fills_registered")
        else:
            log(
                logger,
                logging.WARNING,
                "dead_cross_subscription_rejected",
                subscription="fills",
                **value.detail,
            )
            self._fills_rejected = True

    def _on_fill(self, fill: Fill) -> None:
        """내 계좌 체결(앱 밖 주문 포함). 세션 안이면 다시 조회하고, 세션 밖이면 기억이 있는
        종목의 체결만 보유종목을 다시 읽어 없는 종목의 기억을 버린다."""
        if self._run is not None:
            log(logger, logging.INFO, "dead_cross_fill_seen", run=self._run.number, code=fill.code)
            self._request_round("fill")
        elif fill.code in self.memory:
            log(logger, logging.INFO, "dead_cross_fill_seen", run=None, code=fill.code)
            self._request_outside_refresh()
        else:
            log(logger, logging.DEBUG, "dead_cross_fill_ignored", reason="outside_session")

    def _request_outside_refresh(self) -> None:
        """세션 밖에서 보유종목을 다시 읽는다. 짧은 시간에 여러 번 와도 한 번으로 합친다."""
        self._outside_pending = True
        if self._outside is None or self._outside.done():
            self._outside = self._spawn(self._refresh_outside())

    async def _refresh_outside(self) -> None:
        """없는 종목의 기억을 버리고 평균 매입가를 맞춘다. 차트를 조회하거나 판정하지 않는다.

        읽지 못하면 다시 시도하지 않는다. 다음 체결이나 다음 세션 시작 때 다시 읽는다.
        """
        request_id_var.set("-")
        while True:
            self._outside_pending = False
            try:
                rows = await self._owner.positions(self.spec)
            except AppError as exc:
                log(
                    logger,
                    logging.WARNING,
                    "dead_cross_holdings_failed",
                    run=None,
                    reasons=["fill"],
                    kind=exc.kind,
                    cause=exc.message,
                )
                return
            if self._run is not None:
                # 그사이 세션이 시작됐다. 세션의 조회 차례가 반영한다.
                return
            if self._outside_pending:
                continue
            cash = {row["code"] for row in rows if not row["credit"]}
            for code in sorted(self.memory.keys() - cash):
                self._forget(code, "not_held")
            self._check_purchase_prices(_check_holdings(self.spec.market, rows)[0])
            log(
                logger,
                logging.INFO,
                "dead_cross_holdings_refreshed",
                run=None,
                reasons=["fill"],
                memory_count=len(self.memory),
            )
            return

    def _spawn(self, work: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


def _check_holdings(
    market: Market, rows: Sequence[dict[str, Any]]
) -> tuple[dict[str, _Held], dict[str, str]]:
    """보유종목 줄을 판정할 종목과 대상 아님(코드 -> 이유)으로 나눈다.

    SL / TP·Trailing Stop과 같은 규칙이되, 매입가는 쓰지 않아 매입가 없음은 대상 아님이 아니다.
    현금잔고의 이유가 신용보다 앞서고, 같은 종목 여러 줄이 다른 이유보다 앞선다.
    """
    eligible: dict[str, _Held] = {}
    ineligible: dict[str, str] = {}
    duplicated: set[str] = set()
    for row in rows:
        code, exchange = row["code"], row["exchange"]
        if row["credit"]:
            reason = "credit"
        elif market is Market.US and exchange not in US_EXCHANGE_CODES:
            reason = "unknown_exchange"
        elif code in eligible or code in duplicated:
            duplicated.add(code)
            eligible.pop(code, None)
            reason = "duplicate"
        else:
            eligible[code] = _Held(
                row["name"],
                exchange if market is Market.US else None,
                _known_price(row["purchase_price"]),
            )
            continue
        if reason == "duplicate" or ineligible.get(code, "credit") == "credit":
            ineligible[code] = reason
    return eligible, {c: r for c, r in ineligible.items() if c not in eligible}


def _covered(bars: Sequence[Bar], judged: datetime | None, long: int) -> bool:
    """판정에 필요한 완료 봉을 다 받았나.

    기억이 없으면 가장 최근 봉 하나를 판정할 만큼(장기 + 1)이다. 있으면 마지막 판정 봉 다음 봉부터
    최신 봉까지와, 그 첫 봉의 직전 봉 이동평균에 쓰는 과거 봉이다.
    """
    if judged is None:
        return len(bars) >= long + 1
    if not any(bar.start <= judged for bar in bars):
        return False
    newer = sum(1 for bar in bars if bar.start > judged)
    return len(bars) >= newer + long


def _cross(
    memory: Memory, short: Decimal, long: Decimal, prev_short: Decimal, prev_long: Decimal
) -> str | None:
    """이 봉의 판정. 하향 돌파면 "crossed", 데드크로스 발생이 풀리면 "released"다."""
    if memory.crossed_at is None:
        return "crossed" if prev_short >= prev_long and short < long else None
    return "released" if short >= long else None


def _known_price(value: Decimal | None) -> Decimal | None:
    return value if value is not None and value > 0 else None


def _purchase_changed(memory: Memory, purchase: Decimal | None) -> bool:
    """기억과 평균 매입가가 다른가. 어느 쪽이든 매입가가 없으면 비교하지 않는다."""
    return (
        memory.purchase_price is not None
        and purchase is not None
        and purchase != memory.purchase_price
    )


def _format_ma(value: Decimal, places: Decimal) -> str:
    return format(value.quantize(places, rounding=ROUND_HALF_UP), "f")


def _basis(basis: Basis) -> dict[str, Any]:
    return {
        "bar_period": basis.bar_period,
        "short_period": basis.short_period,
        "long_period": basis.long_period,
    }


def _session_basis(session: dict[str, Any]) -> Basis:
    return Basis(session["bar_period"], session["short_period"], session["long_period"])


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
