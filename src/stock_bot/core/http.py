"""브라우저 요청을 읽는 도우미."""

from typing import Any

from fastapi import Request


async def json_body(request: Request) -> Any:
    """요청 본문의 JSON. 해석하지 못하면 None이고, 받는 쪽이 형식 오류로 처리한다."""
    try:
        return await request.json()
    except ValueError:
        return None
