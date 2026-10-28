"""여러 테스트가 함께 쓰는 기다리기 도우미."""

import asyncio
from collections.abc import Callable


async def wait_until(condition: Callable[[], bool]) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("조건을 기다렸지만 만족하지 않았습니다.")
