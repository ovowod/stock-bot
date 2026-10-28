"""자동 매도: 투자 환경과 종목마다 진행 중인 자동 매도를 하나로 막고, 그 체결을 알아본다.

자동 매도를 내려는 기능은 sell을 부르고, 체결 알림은 매도 체결마다 match로 묻는다.
접수된 자동 매도의 주문번호는 체결 알림이 알아보도록 잠시 기억한다. 서버를 재시작하면 사라진다.
사용자가 주문 패널에서 직접 내는 매도는 이 module을 거치지 않는다.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from stock_bot.core.config import Environment, EnvironmentSpec
from stock_bot.core.logging_setup import log
from stock_bot.order.order import AutoSellResult, OrderService

logger = logging.getLogger("stock_bot.auto_sell")

# 접수된 자동 매도 주문번호를 이만큼 지나면 잊는다. 체결 알림의 매도 체결 기억과 같은 기준이다.
ORDER_MEMORY_SECONDS = 24 * 3600

# 매도 체결이 온 종목에 진행 중인 자동 매도가 있으면, 체결 이벤트를 받은 때부터
# 이만큼 그 주문 결과를 기다린 뒤 주문번호를 맞춰 본다.
# 시장가는 주문 응답보다 체결 통보가 먼저 올 수 있다.
RESULT_WAIT_SECONDS = 5.0


@dataclass(frozen=True)
class AutoSellMatch:
    """체결이 자동 매도의 것일 때 그 자동 매도."""

    source: str  # 자동 매도를 낸 기능의 이름. 예: "SL / TP"
    trigger_request_id: str


@dataclass(frozen=True)
class _Accepted:
    match: AutoSellMatch
    accepted: float


class AutoSell:
    def __init__(self, orders: OrderService, clock: Callable[[], float] = time.monotonic) -> None:
        self._orders = orders
        self._clock = clock
        # 진행 중인 자동 매도. 결과가 나오면 Event를 켜, 결과를 기다리던 체결 알림을 깨운다.
        self._claimed: dict[tuple[Environment, str], asyncio.Event] = {}
        self._accepted: dict[tuple[Environment, str], _Accepted] = {}

    async def sell(
        self,
        spec: EnvironmentSpec,
        code: str,
        exchange: str,
        *,
        source: str,
        request_id: str,
        ready: Callable[[], bool],
    ) -> AutoSellResult:
        """매도 가능 수량 전량을 시장가로 판다. 그 종목의 자동 매도가 진행 중이면 보내지 않는다.

        ready는 키움에 보내기 직전에 부른다. False면 보내지 않는다.
        예외가 나도 결과를 돌려준다. 어디서 멈췄는지 모르면 unknown이다.
        """
        key = (spec.environment, code)
        # 확인과 차지 사이에 await가 없으므로 같은 순간의 두 요청 가운데 하나만 통과한다.
        if key in self._claimed:
            log(logger, logging.INFO, "auto_sell_skipped", code=code, reason="in_progress")
            return AutoSellResult("in_progress")
        done = self._claimed[key] = asyncio.Event()
        try:
            result = await self._orders.sell_all_at_market(
                spec, code, exchange, order_key=f"auto-sell-{request_id}", ready=ready
            )
            if result.outcome == "accepted" and result.order_no:
                # 차지를 놓기 전에 기억해야, 결과를 기다리던 체결 알림이 이 주문번호를 찾는다.
                self._remember(spec.environment, result.order_no, source, request_id)
        except asyncio.CancelledError:
            # 서버가 꺼지면 기다리지 않는다. 키움에 이미 보냈는지는 모른다.
            log(
                logger,
                logging.WARNING,
                "auto_sell_interrupted",
                code=code,
                source=source,
                cause="server_shutdown",
            )
            raise
        except Exception as exc:
            logger.error(
                "auto_sell_failed",
                exc_info=exc,
                extra={
                    "fields": {"code": code, "source": source, "error_type": type(exc).__name__}
                },
            )
            # 어디서 멈췄는지 모르므로 키움에 이미 보냈을 수 있다. 결과 확인 불가로 돌려준다.
            result = AutoSellResult("unknown", cause=type(exc).__name__)
        finally:
            del self._claimed[key]
            done.set()
        return result

    async def match(
        self, environment: Environment, code: str, order_no: str, received_clock: float
    ) -> AutoSellMatch | None:
        """매도 체결이 자동 매도의 것이면 그 자동 매도다.

        그 종목의 자동 매도가 진행 중이면 체결 이벤트를 받은 때(received_clock)부터
        RESULT_WAIT_SECONDS까지 결과를 기다린다.
        주문번호가 그대로 같을 때만 찾는다. 주문 응답과 체결 이벤트의 주문번호 형식이
        같은지는 아직 모의 서버에서 확인하지 못했다(07). 다르면 표시만 빠진다.
        """
        settled = await self._settled(environment, code, received_clock)
        accepted = self._accepted.get((environment, order_no))
        if accepted is None:
            if not settled:
                log(
                    logger,
                    logging.WARNING,
                    "fill_auto_sell_wait_timeout",
                    order_no=order_no,
                    # 큐에서 기다린 시간을 포함해 체결 이벤트를 받은 뒤 지난 시간이다.
                    waited_seconds=round(self._clock() - received_clock, 3),
                    limit_seconds=RESULT_WAIT_SECONDS,
                )
            return None
        log(
            logger,
            logging.INFO,
            "fill_auto_sell_matched",
            order_no=order_no,
            source=accepted.match.source,
            trigger_request_id=accepted.match.trigger_request_id,
        )
        return accepted.match

    async def _settled(self, environment: Environment, code: str, received_clock: float) -> bool:
        """진행 중이 아니거나 결과가 나왔으면 True, 기다리다 시간이 다 됐으면 False다."""
        done = self._claimed.get((environment, code))
        if done is None:
            return True
        timeout = RESULT_WAIT_SECONDS - (self._clock() - received_clock)
        try:
            await asyncio.wait_for(done.wait(), max(timeout, 0))
        except TimeoutError:
            return False
        return True

    def _remember(
        self, environment: Environment, order_no: str, source: str, trigger_request_id: str
    ) -> None:
        now = self._clock()
        for key, entry in list(self._accepted.items()):
            if now - entry.accepted > ORDER_MEMORY_SECONDS:
                del self._accepted[key]
        self._accepted[(environment, order_no)] = _Accepted(
            AutoSellMatch(source, trigger_request_id), now
        )
