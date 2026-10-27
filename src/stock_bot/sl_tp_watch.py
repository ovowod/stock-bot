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
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from stock_bot.auto_sell import AutoSell
from stock_bot.config import ENVIRONMENTS, Environment, EnvironmentSpec, Market
from stock_bot.errors import AppError
from stock_bot.fill_watch import FILL_STATUSES
from stock_bot.kiwoom import KiwoomClient
from stock_bot.logging_setup import environment_var, log, request_id_var
from stock_bot.notification import DiscordNotifier, Embed, EmbedField
from stock_bot.quote import US_EXCHANGE_CODES
from stock_bot.realtime import (
    SHARED_GROUP_NO,
    EventKind,
    RealtimeEvent,
    RealtimeHub,
    Subscription,
)
from stock_bot.sl_tp import TIMEZONES, SlTpSettingsStore
from stock_bot.sl_tp_alerts import (
    AutoSellTrigger,
    accepted_embed,
    failed_embed,
    local_time,
    stopped_embed,
    unknown_embed,
)

logger = logging.getLogger("stock_bot.sl_tp_watch")

# 지켜볼 투자 환경. 실전은 넣지 않는다. 국내 모의와 미국 모의는 인증정보·연결·설정이 따로다.
WATCHED = (ENVIRONMENTS[Environment.DOMESTIC_PAPER], ENVIRONMENTS[Environment.US_PAPER])
# 체결 알림에 "SL / TP 자동 매도"로 표시된다.
AUTO_SELL_SOURCE = "SL / TP"
# 시세 실시간 항목. 0B는 국내 주식체결, FE는 미국주식 실시간 체결가다.
QUOTE_TYPES = {Market.DOMESTIC: "0B", Market.US: "FE"}
# 내 계좌 체결 실시간 항목(00 국내 주문체결, F5 미국 실시간 체결).
# 체결을 뜻하는 주문상태는 체결 알림과 같다.
# 체결되면 매입가·보유수량이 바뀌므로 보유종목을 다시 읽는다.
FILL_TYPES = {Market.DOMESTIC: "00", Market.US: "F5"}
# 장구분(290)의 정규장 값. 국내 2는 장중, 미국 0은 정규장이다.
# 국내 장전·장후 시간외, 미국 프리마켓·애프터마켓·주간거래 체결은 판정하지 않는다.
REGULAR_MARKET = {Market.DOMESTIC: "2", Market.US: "0"}
# 공유 연결에서 SL / TP의 시세 구독이 쓰는 그룹 번호.
# 주문체결은 공유 연결이 SHARED_GROUP_NO(1)로 한 번만 등록해 체결 감시와 함께 받는다.
GROUP_NO = "2"
# 지금 켜진 SL/TP 세션을 다시 계산하는 간격. 설정을 저장할 때도 다시 계산한다.
SESSION_CHECK_SECONDS = 1.0
# 보유종목 읽기가 실패하면 이 간격으로 다시 읽는다. 실패가 이어지면 두 배씩 늘린다.
HOLDINGS_RETRY_INITIAL_SECONDS = 1.0
HOLDINGS_RETRY_MAX_SECONDS = 60.0
# 보유종목 읽기가 첫 실패부터 이만큼 계속 실패하면 판정 중단 알림을 한 번 보낸다.
HOLDINGS_ALERT_AFTER = timedelta(minutes=5)
# 자동 매도 결과 알림. 매도를 보내지 않은 결과(건너뜀·보내기 전 취소)는 알리지 않는다.
RESULT_EMBEDS = {"accepted": accepted_embed, "rejected": failed_embed, "unknown": unknown_embed}

PositionLookup = Callable[[EnvironmentSpec], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class _Holding:
    name: str
    purchase_price: Decimal
    # 미국 종목의 거래소(NYSE·NASDAQ·AMEX). 국내는 None이다.
    exchange: str | None


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
    # 이 실행을 시작한 시각. 그보다 먼저 받은 시세는 앞 실행의 것이다(껐다 켠 경우 포함).
    started_at: datetime
    triggered: set[str] = field(default_factory=set)
    # 지금 쓰는 보유종목을 읽을 때의 연결 번호. 다른 번호로 다시 연결되면
    # 끊긴 동안의 체결을 반영하려고 다시 읽는다.
    connection: int | None = None
    # 보유종목 다시 읽기 작업과, 읽는 동안 새로 온 계기(아직 반영하지 못한 이유들).
    refresh: asyncio.Task[None] | None = None
    pending: list[str] = field(default_factory=list)
    # 보유종목 읽기가 처음 실패한 시각. 한 번이라도 성공하면 지운다.
    # 판정 중단 알림(5분)이 이 시각을 쓴다. 알렸으면 성공하거나 실행이 끝날 때까지
    # 다시 알리지 않는다.
    failed_since: datetime | None = None
    failure_alerted: bool = False

    def covers(self, found: tuple[date, _Session] | None) -> bool:
        """그 날짜·세션이 이 실행의 것인가. 기준이 바뀌어도 같은 시간 범위면 같은 실행이다.

        시간 범위를 바꾸면 다른 세션으로 본다. 기준이나 제외종목만 바꿔야 실행이 이어진다.
        """
        return found is not None and found[0] == self.day and found[1].span == self.session.span


class SlTpWatcher:
    def __init__(
        self,
        kiwoom: KiwoomClient,
        settings: SlTpSettingsStore,
        positions: PositionLookup,
        auto_sell: AutoSell,
        hub: RealtimeHub,
        notifier: DiscordNotifier,
        now: Callable[[], datetime],
    ) -> None:
        self.kiwoom = kiwoom
        # Discord가 꺼져 있어도 자동 매도는 동작한다. 알림만 보내지 않는다.
        self.notifier = notifier
        self.settings = settings
        self.positions = positions
        self.auto_sell = auto_sell
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

    def settings_saved(self, spec: EnvironmentSpec, settings: dict[str, Any]) -> None:
        """설정 저장이 성공했다. 지켜보지 않는 환경(실전, 인증정보 없음)은 무시한다."""
        for watch in self._watches:
            if watch.spec is spec:
                watch.settings_saved(settings)


class _Watch:
    """투자 환경 하나의 SL / TP 실행."""

    def __init__(self, owner: SlTpWatcher, spec: EnvironmentSpec) -> None:
        self._owner = owner
        self.spec = spec
        self._zone = ZoneInfo(TIMEZONES[spec.market])
        # 밀려남·서버 종료로 멈췄다. 멈추면 판정도 주문도 하지 않는다.
        self._stopped = False
        # 시작 때 설정 파일을 읽지 못했다. 정상 설정이 저장될 때까지 멈춰 있다.
        self._settings_broken = False
        self._sessions: list[_Session] = []
        self._excluded: set[str] = set()
        self._run: _Run | None = None
        self._runs = 0
        # 종목코드 -> 매입가·거래소. 현금잔고이고 매입가를 읽을 수 있으며, 미국은 거래소를 아는
        # 종목만 담는다. 제외종목도 담는다.
        self._holdings: dict[str, _Holding] = {}
        self._holdings_ready = False
        # 지금 구독한 시세 종목. 실행이 바뀌면 None으로 되돌려 다시 등록한다.
        self._subscribed: list[str] | None = None
        # 연결이 LOGIN까지 되면 깨운다. 보유종목은 연결이 있을 때 읽어야 그 뒤 체결을 놓치지 않는다.
        self._wake = asyncio.Event()
        self._rejected = False
        self._fills_rejected = False
        # 판정 중단을 알린 구독 거부의 그룹.
        # 등록에 성공하거나 새 세션 실행이 시작되면 다시 알릴 수 있다.
        self._rejection_alerted: set[str | None] = set()
        # 같은 이유의 건너뜀·잘못된 체결가 로그를 반복해 남기지 않으려고
        # 종목마다 마지막 이유를 둔다.
        self._skips: dict[str, str] = {}
        self._invalid: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._channel = owner.hub.channel(spec, "sl_tp", "SL / TP", self._on_event)

    def start(self) -> None:
        self._spawn(self._tick())
        try:
            settings = self._owner.settings.read(self.spec)
        except AppError as exc:
            # 깨진 설정을 빈 설정이나 옛 설정으로 착각해 동작하지 않는다.
            self._settings_broken = True
            log(logger, logging.ERROR, "sl_tp_watch_stopped", reason=exc.kind, cause=exc.message)
            self._alert(
                "settings_unreadable",
                stopped_embed(
                    self.spec,
                    "SL / TP 설정 파일을 읽지 못해 이 환경의 SL / TP를 멈췄습니다. "
                    "SL / TP 화면에서 설정을 다시 저장하면 그때부터 동작합니다.",
                ),
            )
            return
        self._apply(settings)
        self._refresh_run()

    def settings_saved(self, settings: dict[str, Any]) -> None:
        token = environment_var.set(self.spec.environment.value)
        try:
            self._settings_saved(settings)
        finally:
            environment_var.reset(token)

    def _settings_saved(self, settings: dict[str, Any]) -> None:
        if self._settings_broken:
            log(logger, logging.INFO, "sl_tp_watch_resumed", reason="settings_saved")
        self._settings_broken = False
        excluded = self._excluded
        self._apply(settings)
        run = self._run
        self._refresh_run()
        if self._run is run and run is not None and excluded != self._excluded:
            # 같은 실행 안에서 제외종목만 바뀌었다. 트리거 기록은 두고 구독만 맞춘다.
            if self._holdings_ready:
                self._subscribe()

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(SESSION_CHECK_SECONDS)
            try:
                self._refresh_run()
            except Exception as exc:
                # 한 번의 계산 오류로 세션 전환이 멈추지 않게 한다.
                logger.error(
                    "sl_tp_session_check_failed",
                    exc_info=exc,
                    extra={"fields": {"error_type": type(exc).__name__}},
                )

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
        return self._stopped or self._settings_broken or self._channel.stopped

    def _refresh_run(self) -> None:
        """지금 켜진 세션에 맞춰 SL/TP 세션 실행을 시작하거나 끝낸다.

        시각이 되어 시작, 다른 세션으로 넘어감, OFF→ON, 다음 날 같은 세션은 모두 새 실행이다.
        """
        if self._is_stopped():
            return
        found = self._session_at(self._owner.now())
        run = self._run
        if run is not None and found is not None and run.covers(found):
            # 같은 세션 안에서 기준만 바뀌면 실행 번호와 트리거 기록을 그대로 둔다.
            run.session = found[1]
            return
        if run is not None:
            self._end_run(run)
        if found is None:
            if run is not None:
                self._subscribed = None
                self._channel.subscribe(None)
                self._channel.share(None)
            return
        day, session = found
        self._runs += 1
        new = _Run(self._runs, day, session, self._owner.now())
        new.connection = self._channel.connection
        self._run = new
        self._subscribed = None
        # 새 세션에서 같은 거부가 다시 나면 다시 알린다.
        self._rejection_alerted.clear()
        log(
            logger,
            logging.INFO,
            "sl_tp_session_started",
            **self._run_fields(new),
            previous_run=None if run is None else run.number,
        )
        if self._channel.shared != self._fill_types() or self._fills_rejected:
            # 새로 함께 받으면 지금 등록 결과를 받는다. 거부됐던 등록은 여기서 다시 시도한다.
            self._channel.share(self._fill_types())
        self._request_refresh(new, "session_started")

    def _fill_types(self) -> list[str]:
        return [FILL_TYPES[self.spec.market]]

    def _end_run(self, run: _Run) -> None:
        log(logger, logging.INFO, "sl_tp_session_ended", **self._run_fields(run))
        self._run = None
        self._holdings_ready = False
        if run.refresh is not None and not run.refresh.done():
            # 켜진 세션이 끝나면 다시 읽기와 재시도를 멈춘다. 실패 기록은 실행과 함께 사라진다.
            run.refresh.cancel()
            if run.failed_since is not None:
                log(logger, logging.INFO, "sl_tp_holdings_retry_stopped", run=run.number)

    def _request_refresh(self, run: _Run, reason: str) -> None:
        """보유종목을 다시 읽는다.

        새 결과가 반영될 때까지 판정하지 않는다. 옛 매입가로 판정하지 않기 위해서다.

        읽는 중에 다시 오면 끝난 뒤 한 번 더 읽는다. 짧은 시간에 여러 번 와도 한 번으로 합친다.
        """
        if self._holdings_ready:
            log(logger, logging.INFO, "sl_tp_judging_paused", run=run.number, reason=reason)
        self._holdings_ready = False
        run.pending.append(reason)
        if run.refresh is None or run.refresh.done():
            run.refresh = self._spawn(self._refresh(run))

    async def _refresh(self, run: _Run) -> None:
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
            # 여기까지 온 계기는 이번 조회가 반영한다.
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
                    "sl_tp_holdings_failed",
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
                    self._alert(
                        "holdings_failing",
                        stopped_embed(
                            self.spec,
                            f"보유종목을 {_minutes(HOLDINGS_ALERT_AFTER)}분 넘게 읽지 못해 "
                            "판정을 멈췄습니다. "
                            "계속 다시 읽고, 성공하면 판정을 이어 갑니다.",
                            EmbedField("첫 실패", local_time(self.spec, run.failed_since)),
                            EmbedField("원인", exc.message),
                        ),
                    )
                await asyncio.sleep(delay)
                delay = min(delay * 2, HOLDINGS_RETRY_MAX_SECONDS)
                continue
            if run.failed_since is not None:
                log(
                    logger,
                    logging.INFO,
                    "sl_tp_holdings_recovered",
                    run=run.number,
                    failed_since=run.failed_since.isoformat(),
                )
                run.failed_since = None
                run.failure_alerted = False
            if run is not self._run or self._is_stopped():
                log(logger, logging.INFO, "sl_tp_holdings_discarded", run=run.number, reason="run")
                return
            if self._channel.connection != connection or run.pending:
                # 조회하는 동안 다시 연결됐거나 체결이 더 왔다. 이 결과는 쓰지 않고 한 번 더 읽는다.
                log(
                    logger,
                    logging.INFO,
                    "sl_tp_holdings_discarded",
                    run=run.number,
                    reason="newer_trigger" if run.pending else "connection",
                )
                continue
            run.connection = connection
            self._use_holdings(run, rows, reasons)
            return

    def _use_holdings(self, run: _Run, rows: list[dict[str, Any]], reasons: list[str]) -> None:
        holdings: dict[str, _Holding] = {}
        counts = {"credit": 0, "no_purchase_price": 0, "duplicate": 0, "unknown_exchange": 0}
        duplicated: set[str] = set()
        unknown_exchange: list[str] = []
        for row in rows:
            code, price, exchange = row["code"], row["purchase_price"], row["exchange"]
            if row["credit"]:
                # 현금 매도 주문으로 신용 보유분을 팔려다 실패하지 않게 다루지 않는다.
                counts["credit"] += 1
            elif price is None or price <= 0:
                counts["no_purchase_price"] += 1
            elif self.spec.market is Market.US and exchange not in US_EXCHANGE_CODES:
                # 종목 목록에서 거래소를 찾지 못한 종목은 다루지 않는다.
                # 거래소 없이 주문을 보내다 실패하지 않기 위해서다.
                counts["unknown_exchange"] += 1
                unknown_exchange.append(code)
            elif code in holdings or code in duplicated:
                # 같은 종목의 현금잔고가 여러 줄이면 어느 매입가인지 모른다.
                duplicated.add(code)
                holdings.pop(code, None)
                counts["duplicate"] += 1
            else:
                holdings[code] = _Holding(row["name"], price, exchange)
        removed = sorted(self._holdings.keys() - holdings.keys())
        self._holdings = holdings
        self._holdings_ready = True
        log(
            logger,
            logging.INFO,
            "sl_tp_holdings_refreshed",
            run=run.number,
            reasons=reasons,
            count=len(holdings),
            excluded=len(holdings.keys() & self._excluded),
            removed=removed,
            unknown_exchange_codes=unknown_exchange,
            **counts,
        )
        log(logger, logging.INFO, "sl_tp_judging_resumed", run=run.number)
        self._subscribe()

    def _watched(self) -> list[str]:
        return sorted(code for code in self._holdings if code not in self._excluded)

    def _subscribe(self) -> None:
        """감시 대상이 바뀌었을 때만 구독을 다시 낸다. 실행이 새로 시작되면 늘 다시 낸다."""
        codes = self._watched()
        if codes == self._subscribed:
            return
        self._subscribed = codes
        if self._fills_rejected:
            # 거부됐던 주문체결 등록은 보유종목 변경으로 구독을 다시 맞출 때도 다시 시도한다.
            self._channel.share(self._fill_types())
        log(logger, logging.INFO, "sl_tp_subscription_changed", codes=codes)
        if not codes:
            self._channel.subscribe(None)
            return
        self._channel.subscribe(
            Subscription(
                GROUP_NO, self._quote_items(codes), [QUOTE_TYPES[self.spec.market]], refresh="0"
            )
        )

    def _quote_items(self, codes: list[str]) -> list[Any]:
        """국내는 접두어 없는 종목코드, 미국은 {티커, 거래소 코드} 객체로 등록한다."""
        if self.spec.market is Market.DOMESTIC:
            return list(codes)
        return [
            {"jmcode": code, "stex_tp": US_EXCHANGE_CODES[self._holdings[code].exchange or ""]}
            for code in codes
        ]

    async def _on_event(self, event: RealtimeEvent) -> None:
        environment_var.set(self.spec.environment.value)
        run = self._run
        if event.kind is EventKind.CONNECTED:
            self._wake.set()
            if run is not None and run.connection is not None:
                if run.connection != event.connection:
                    # 지금 보유종목을 읽은 뒤 끊긴 적이 있다. 끊긴 동안 놓친 체결이 있을 수 있다.
                    self._request_refresh(run, "reconnected")
        elif event.kind is EventKind.DISCONNECTED:
            if run is not None:
                log(
                    logger,
                    logging.INFO,
                    "sl_tp_judging_paused",
                    run=run.number,
                    reason="disconnected",
                )
        elif event.kind is EventKind.REGISTERED:
            self._rejection_alerted.discard(event.group)
            if event.group == SHARED_GROUP_NO:
                self._fills_rejected = False
            else:
                self._rejected = False
        elif event.kind is EventKind.REJECTED:
            log(
                logger,
                logging.WARNING,
                "sl_tp_subscription_rejected",
                group=event.group,
                **event.detail,
            )
            if event.group == SHARED_GROUP_NO:
                # 체결을 받지 못하면 바뀐 매입가를 모른 채 판정하게 되므로 판정도 멈춘다.
                self._fills_rejected = True
                what = (
                    "주문체결 실시간 등록이 거부되어 판정을 멈췄습니다. "
                    "체결을 받지 못하면 바뀐 매입가를 알 수 없습니다."
                )
            else:
                self._rejected = True
                what = "실시간 시세 구독이 거부되어 판정을 멈췄습니다."
            if event.group not in self._rejection_alerted:
                # 거부가 이어지는 동안은 한 번만 알린다.
                self._rejection_alerted.add(event.group)
                self._alert(
                    "subscription_rejected",
                    stopped_embed(
                        self.spec,
                        f"{what} 다음 세션 시작이나 감시 종목이 바뀔 때 다시 시도합니다.",
                        EmbedField("원인", str(event.detail.get("return_msg") or "확인 불가")),
                    ),
                )
        elif event.kind is EventKind.STOPPED:
            self._stopped = True
            self._wake.set()
            log(logger, logging.ERROR, "sl_tp_watch_stopped", reason="session_replaced")
        elif event.kind is EventKind.REAL:
            for item in event.items:
                if item.get("type") == FILL_TYPES[self.spec.market]:
                    self._on_fill(item, event)
                else:
                    self._judge(item, event)

    def _on_fill(self, item: dict[str, Any], event: RealtimeEvent) -> None:
        """내 계좌 체결(앱 밖 MTS·HTS 주문 포함). 매입가·보유수량이 바뀌었으니 다시 읽는다."""
        run = self._run
        values = item.get("values")
        values = values if isinstance(values, dict) else {}
        status = values.get("913")
        if run is None:
            reason = "outside_session"
        elif event.connection != self._channel.connection:
            reason = "old_connection"
        elif status not in FILL_STATUSES[FILL_TYPES[self.spec.market]]:
            reason = "not_a_fill"
        else:
            log(logger, logging.INFO, "sl_tp_fill_seen", run=run.number, code=values.get("9001"))
            self._request_refresh(run, "fill")
            return
        log(logger, logging.DEBUG, "sl_tp_fill_ignored", reason=reason, status=status)

    def _judge(self, item: dict[str, Any], event: RealtimeEvent) -> None:
        code = str(item.get("item") or "").strip()
        values = item.get("values")
        values = values if isinstance(values, dict) else {}
        log(logger, logging.DEBUG, "sl_tp_quote_received", code=code, price=values.get("10"))
        run = self._run
        if event.connection != self._channel.connection:
            # 끊긴 뒤 큐에 남아 있던 시세나 재연결 전 연결의 시세는 판정하지 않는다.
            log(logger, logging.DEBUG, "sl_tp_quote_dropped", code=code, reason="old_connection")
            return
        if run is None:
            self._skip(code, "outside_session")
            return
        if event.received_at < run.started_at or not run.covers(
            self._session_at(event.received_at)
        ):
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
        holding = self._holdings[code]
        purchase = holding.purchase_price
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
        fired = AutoSellTrigger(
            code,
            holding.name,
            holding.exchange,
            trigger,
            price,
            purchase,
            rate,
            threshold,
            run.session.span,
        )
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
            # 시세 처리는 주문 결과를 기다리지 않는다.
            self._spawn(self._sell(run, fired, request_id))
        finally:
            request_id_var.reset(token)

    def _skip_reason(self, run: _Run, code: str, values: dict[str, Any]) -> str | None:
        if self._is_stopped():
            return "stopped"
        if self._rejected:
            return "subscription_rejected"
        if self._fills_rejected:
            return "fills_subscription_rejected"
        if not self._holdings_ready:
            return "holdings_pending"
        if str(values.get("290", "")).strip() != REGULAR_MARKET[self.spec.market]:
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

    async def _sell(self, run: _Run, trigger: AutoSellTrigger, request_id: str) -> None:
        """트리거 때 읽은 거래소로 판다. 국내는 거래소가 없어 빈 값으로 보낸다."""
        code = trigger.code
        result = await self._owner.auto_sell.sell(
            self.spec,
            code,
            trigger.exchange or "",
            source=AUTO_SELL_SOURCE,
            request_id=request_id,
            ready=lambda: self._may_send(run, code),
        )
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
        embed = RESULT_EMBEDS.get(result.outcome)
        if embed is not None:
            sent = await self._owner.notifier.send(
                embed=embed(self.spec, trigger, result), spec=self.spec
            )
            log(logger, logging.INFO, "sl_tp_notified", kind=result.outcome, code=code, sent=sent)

    def _alert(self, reason: str, embed: Embed) -> None:
        """판정 중단 알림. 판정·이벤트 처리가 기다리지 않게 따로 보낸다."""

        async def send() -> None:
            sent = await self._owner.notifier.send(embed=embed, spec=self.spec)
            log(logger, logging.INFO, "sl_tp_stop_notified", reason=reason, sent=sent)

        self._spawn(send())

    def _may_send(self, run: _Run, code: str) -> bool:
        """키움에 보내기 직전 확인.

        연결 끊김만으로는 취소하지 않는다. 끊기기 전 체결가는 실제 체결이었다.
        """
        reason = None
        if self.spec.is_real:
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

    def _spawn(self, work: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

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


def _minutes(span: timedelta) -> int:
    return int(span.total_seconds() // 60)
