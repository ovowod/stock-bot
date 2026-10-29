"""자동 매도 규칙: 종목마다 하나만 내고, 접수된 자동 매도의 체결을 알아본다."""

import asyncio
import time
from collections.abc import Callable

import pytest

from stock_bot.core.config import ENVIRONMENTS, Environment
from stock_bot.order import auto_sell as auto_sell_module
from stock_bot.order.auto_sell import AutoSell, AutoSellMatch
from stock_bot.order.order import AutoSellResult

pytestmark = pytest.mark.anyio

PAPER = ENVIRONMENTS[Environment.DOMESTIC_PAPER]


class FakeOrders:
    """sell_all_at_market만 흉내 낸다. gate를 열 때까지 주문 응답을 붙잡아 둔다."""

    def __init__(self, result: AutoSellResult | Exception) -> None:
        self.result = result
        self.gate = asyncio.Event()
        self.gate.set()
        self.calls: list[tuple[str, str]] = []

    async def sell_all_at_market(
        self, spec, code: str, exchange: str, order_key: str, ready: Callable[[], bool]
    ) -> AutoSellResult:
        self.calls.append((code, order_key))
        await self.gate.wait()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def accepted(order_no: str = "0000138") -> AutoSellResult:
    return AutoSellResult("accepted", quantity=10, order_no=order_no)


async def sell(auto_sell: AutoSell, code: str = "005930", request_id: str = "req-1"):
    return await auto_sell.sell(
        PAPER, code, "", source="SL / TP", request_id=request_id, ready=lambda: True
    )


async def test_second_sell_of_the_same_stock_is_refused_while_the_first_is_in_flight():
    orders = FakeOrders(accepted())
    orders.gate.clear()
    auto_sell = AutoSell(orders)

    first = asyncio.create_task(sell(auto_sell, request_id="req-1"))
    await asyncio.sleep(0)
    second = await sell(auto_sell, request_id="req-2")
    other_stock = asyncio.create_task(sell(auto_sell, code="000660", request_id="req-3"))
    orders.gate.set()

    assert second.outcome == "in_progress"
    assert (await first).outcome == "accepted"
    assert (await other_stock).outcome == "accepted"
    assert orders.calls == [("005930", "auto-sell-req-1"), ("000660", "auto-sell-req-3")]


async def test_fill_that_arrives_before_the_order_reply_is_matched_after_acceptance():
    # 시장가는 주문 응답보다 체결 통보가 먼저 올 수 있다.
    orders = FakeOrders(accepted("0000138"))
    orders.gate.clear()
    auto_sell = AutoSell(orders)

    selling = asyncio.create_task(sell(auto_sell, request_id="req-1"))
    await asyncio.sleep(0)
    matching = asyncio.create_task(
        auto_sell.match(PAPER.environment, "005930", "0000138", time.monotonic())
    )
    await asyncio.sleep(0)
    orders.gate.set()

    assert (await selling).outcome == "accepted"
    assert await matching == AutoSellMatch(source="SL / TP", trigger_request_id="req-1")


async def test_fill_of_another_order_is_not_an_auto_sell():
    auto_sell = AutoSell(FakeOrders(accepted("0000138")))
    await sell(auto_sell)

    assert await auto_sell.match(PAPER.environment, "005930", "0000999", time.monotonic()) is None


async def test_match_stops_waiting_for_a_slow_order_reply(monkeypatch, caplog):
    monkeypatch.setattr(auto_sell_module, "RESULT_WAIT_SECONDS", 0.05)
    orders = FakeOrders(accepted("0000138"))
    orders.gate.clear()
    auto_sell = AutoSell(orders)

    selling = asyncio.create_task(sell(auto_sell))
    await asyncio.sleep(0)
    found = await auto_sell.match(PAPER.environment, "005930", "0000138", time.monotonic())
    orders.gate.set()
    await selling

    assert found is None
    assert "fill_auto_sell_wait_timeout" in [r.getMessage() for r in caplog.records]


async def test_unexpected_error_is_reported_as_unknown_and_frees_the_stock():
    orders = FakeOrders(RuntimeError("boom"))
    auto_sell = AutoSell(orders)

    failed = await sell(auto_sell, request_id="req-1")
    orders.result = accepted()
    retried = await sell(auto_sell, request_id="req-2")

    assert failed.outcome == "unknown"
    assert failed.cause == "RuntimeError"
    assert retried.outcome == "accepted"
