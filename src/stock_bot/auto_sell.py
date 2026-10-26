"""자동 매도 공통 장치: 투자 환경과 종목마다 진행 중인 자동 매도를 하나로 막는다.

자동 매도를 내려는 기능은 먼저 종목을 차지하고, 주문 결과가 나오면 놓는다.
접수된 자동 매도의 주문번호는 체결 알림이 알아보도록 잠시 기억한다. 서버를 재시작하면 사라진다.
사용자가 주문 패널에서 직접 내는 매도는 이 장치를 거치지 않는다.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from stock_bot.config import Environment

# 접수된 자동 매도 주문번호를 이만큼 지나면 잊는다. 체결 알림의 매도 체결 기억과 같은 기준이다.
ORDER_MEMORY_SECONDS = 24 * 3600


@dataclass(frozen=True)
class AcceptedAutoSell:
    order_no: str
    trigger_request_id: str
    accepted: float


class AutoSellGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # 진행 중인 자동 매도. 결과가 나오면 Event를 켜, 결과를 기다리던 체결 알림을 깨운다.
        self._claimed: dict[tuple[Environment, str], asyncio.Event] = {}
        self._accepted: dict[tuple[Environment, str], AcceptedAutoSell] = {}

    def claim(self, environment: Environment, code: str) -> bool:
        """비어 있으면 차지하고 True다. 이미 진행 중이면 False다.

        확인과 차지 사이에 await가 없으므로 같은 순간의 두 요청 가운데 하나만 통과한다.
        """
        key = (environment, code)
        if key in self._claimed:
            return False
        self._claimed[key] = asyncio.Event()
        return True

    def release(self, environment: Environment, code: str) -> None:
        """주문 결과(접수·거부·결과 불명 등)가 나왔다. 접수면 remember를 먼저 부른다."""
        done = self._claimed.pop((environment, code), None)
        if done is not None:
            done.set()

    async def settled(self, environment: Environment, code: str, timeout: float) -> bool:
        """그 종목의 자동 매도가 진행 중이면 결과가 나올 때까지 최대 timeout초 기다린다.

        진행 중이 아니거나 결과가 나왔으면 True, 기다리다 시간이 다 됐으면 False다.
        """
        done = self._claimed.get((environment, code))
        if done is None:
            return True
        try:
            await asyncio.wait_for(done.wait(), max(timeout, 0))
        except TimeoutError:
            return False
        return True

    def remember(self, environment: Environment, order_no: str, trigger_request_id: str) -> None:
        now = self._clock()
        for key, entry in list(self._accepted.items()):
            if now - entry.accepted > ORDER_MEMORY_SECONDS:
                del self._accepted[key]
        self._accepted[(environment, order_no)] = AcceptedAutoSell(
            order_no, trigger_request_id, now
        )

    def accepted(self, environment: Environment, order_no: str) -> AcceptedAutoSell | None:
        """주문번호가 그대로 같을 때만 찾는다. 주문 응답과 체결 이벤트의 주문번호 형식이
        같은지는 아직 모의 서버에서 확인하지 못했다(07). 다르면 표시만 빠진다."""
        return self._accepted.get((environment, order_no))
