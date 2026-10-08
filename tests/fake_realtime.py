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
        """REG를 보낸 연결이 count개가 될 때까지 기다린다. 연결된 순서대로 돌려준다."""
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


def open_order(**overrides: str) -> dict[str, str]:
    """ka10075 미체결 목록(oso)의 한 줄. kra-docs responseExample을 따른다."""
    row = {
        "acnt_no": "8100000111",
        "ord_no": "0000069",
        "mang_empno": "",
        "stk_cd": "005930",
        "tsk_tp": "",
        "ord_stt": "접수",
        "stk_nm": "삼성전자",
        "ord_qty": "10",
        "ord_pric": "60000",
        "oso_qty": "7",
        "cntr_tot_amt": "0",
        "orig_ord_no": "0000000",
        "io_tp_nm": "+매수",
        "trde_tp": "보통",
        "tm": "094022",
        "cntr_no": "",
        "cntr_pric": "0",
        "cntr_qty": "0",
        "cur_prc": "+60000",
        "sel_bid": "0",
        "buy_bid": "+60000",
        "unit_cntr_pric": "",
        "unit_cntr_qty": "",
        "tdy_trde_cmsn": "0",
        "tdy_trde_tax": "0",
        "ind_invsr": "",
        "stex_tp": "1",
        "stex_tp_txt": "KRX",
        "sor_yn": "N",
    }
    row.update(overrides)
    return row


def realized_row(**overrides: str) -> dict[str, str]:
    """ka10077 당일실현손익상세(tdy_rlzt_pl_dtl)의 한 줄. 종목코드에 A가 붙어 온다."""
    row = {
        "stk_nm": "삼성전자",
        "cntr_qty": "1",
        "buy_uv": "60000",
        "cntr_pric": "61000",
        "tdy_sel_pl": "1000",
        "pl_rt": "+1.67",
        "tdy_trde_cmsn": "0",
        "tdy_trde_tax": "0",
        "stk_cd": "A005930",
    }
    row.update(overrides)
    return row


def us_fill_event(**overrides: str) -> dict[str, Any]:
    """미국 F5 실시간 체결 메시지. 2026-10-08 모의 서버에서 받은 모양을 따른다.
    기본값은 포드 모터 1주 매수 체결완료다."""
    values = {
        "9201": "8200000222",
        "9203": "000002662",
        "9001": "F",
        "905": "21",
        "8046": "000030",
        "907": "02",
        "904": "000000000",
        "909": "000007164",
        "900": "1",
        "901": "0000012.3200",
        "911": "1",
        "910": "0000012.0750",
        "913": "체결완료",
        "902": "0",
        "908": "232308",
        "8018": "0.0000",
        "8019": "0.0000",
        "930": "1",
        "931": "0000012.0750",
        "8043": "USD",
        "50072": "매수",
        "302": "포드 모터",
        "50073": "지정가",
        "50724": "0000000.0000",
        "50725": "000000000000",
    }
    values.update(overrides)
    return {
        "trnm": "REAL",
        "data": [{"values": values, "type": "F5", "name": "해외주식체결", "item": values["9001"]}],
    }


def us_open_order(**overrides: str) -> dict[str, str]:
    """ust21050 미체결 목록(result_list)의 한 줄. 2026-10-08 모의 서버 응답을 따른다."""
    row = {
        "ord_cntr_tp": "10",
        "ord_no": "000002624",
        "orig_ord_no": "000000000",
        "stex_nm": "뉴욕",
        "crnc_code": "USD",
        "stk_cd": "F",
        "frgn_stk_nm": "포드 모터",
        "frgn_trde_tp": "00",
        "frgn_trde_nm": "지정가",
        "slby_tp": "2",
        "slby_tp_nm": "매수",
        "ord_qty": "000000000001",
        "ord_uv": "1.0000",
        "stop_pric": "0000000.0000",
        "cntr_qty": "000000000000",
        "cntr_uv": "0.0000",
        "mdfy_qty": "000000000000",
        "mdfy_uv": "0.0000",
        "cncl_qty": "000000000000",
        "ord_remnq": "000000000001",
        "ord_time": "23:22:11",
        "ord_resp_time": "23:22:11",
        "ord_stat": "접수",
        "rsrv_tp": "정상",
        "natn_nm": "미국",
    }
    row.update(overrides)
    return row
