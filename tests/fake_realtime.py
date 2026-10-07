"""키움 실시간(WebSocket) 서버를 흉내 낸다. 메시지 모양은 kra-docs의 00·F5 예제를 따른다."""

import asyncio
import json
from typing import Any

OK_LOGIN = {"trnm": "LOGIN", "return_code": 0, "return_msg": ""}
OK_REG = {"trnm": "REG", "return_code": 0, "return_msg": ""}


class FakeConnection:
    def __init__(self, url: str, login_reply: Any, reg_reply: Any) -> None:
        self.url = url
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_error: Exception | None = None
        self._inbox: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        # None이면 응답하지 않는다.
        self._replies = {"LOGIN": login_reply, "REG": reg_reply}

    async def send(self, message: str) -> None:
        data = json.loads(message)
        self.sent.append(data)
        reply = self._replies.get(data.get("trnm"))
        if reply is not None:
            self.push(reply)

    async def recv(self) -> str | bytes:
        message = await self._inbox.get()
        if message is None:
            raise ConnectionResetError("fake connection dropped")
        return message

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    def push(self, message: dict[str, Any]) -> None:
        self._inbox.put_nowait(json.dumps(message, ensure_ascii=False))

    def push_raw(self, text: str | bytes) -> None:
        self._inbox.put_nowait(text)

    def drop(self) -> None:
        """서버 쪽에서 연결이 끊긴 것처럼 recv가 오류를 낸다."""
        self._inbox.put_nowait(None)

    def sent_trnm(self) -> list[str]:
        return [m.get("trnm", "") for m in self.sent]


class FakeRealtime:
    """login_reply·reg_reply에 목록을 주면 연결 순서대로 하나씩 쓰고, 마지막 값을 계속 쓴다.
    refuse는 앞에서부터 거부할 연결 수다."""

    def __init__(
        self, login_reply: Any = OK_LOGIN, reg_reply: Any = OK_REG, refuse: int = 0
    ) -> None:
        self.login_reply = login_reply
        self.reg_reply = reg_reply
        self.refuse = refuse
        self.attempts = 0
        self.connections: list[FakeConnection] = []

    async def __call__(self, url: str) -> FakeConnection:
        self.attempts += 1
        if self.attempts <= self.refuse:
            raise ConnectionRefusedError("fake refused")
        index = len(self.connections)
        connection = FakeConnection(url, _nth(self.login_reply, index), _nth(self.reg_reply, index))
        self.connections.append(connection)
        return connection

    async def wait_registered(self, count: int = 1) -> list[FakeConnection]:
        """REG를 보낸 연결이 count개가 될 때까지 기다린다."""
        for _ in range(200):
            registered = [c for c in self.connections if "REG" in c.sent_trnm()]
            if len(registered) >= count:
                return registered
            await asyncio.sleep(0.01)
        raise AssertionError(f"REG를 보낸 연결 {count}개를 기다렸지만 {len(registered)}개")


def _nth(reply: Any, index: int) -> Any:
    if isinstance(reply, list):
        return reply[min(index, len(reply) - 1)]
    return reply


def fill_event(**overrides: str) -> dict[str, Any]:
    """국내 00 주문체결 실시간 메시지. 기본값은 삼성전자 10주 매수 중 3주 체결이다."""
    values = {
        "9201": "8100000111",
        "9203": "0000018",
        "9205": "",
        "9001": "005930",
        "912": "JJ",
        "913": "체결",
        "302": "삼성전자",
        "900": "10",
        "901": "60000",
        "902": "7",
        "903": "180000",
        "904": "0000000",
        "905": "+매수",
        "906": "보통",
        "907": "2",
        "908": "094105",
        "909": "1234567",
        "910": "+60000",
        "911": "3",
        "10": "+60000",
        "27": "+60100",
        "28": "-60000",
        "914": "+60000",
        "915": "3",
        "938": "0",
        "939": "0",
        "919": "0",
        "920": "",
        "921": "0701002",
        "922": "00",
        "923": "00000000",
        "2134": "1",
        "2135": "KRX",
        "2136": "N",
    }
    values.update(overrides)
    return {
        "trnm": "REAL",
        "data": [{"values": values, "type": "00", "name": "주문체결", "item": values["9001"]}],
    }
