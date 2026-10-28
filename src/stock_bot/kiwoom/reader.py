"""키움 응답에서 필드를 꺼낸다. 필요한 필드가 없거나 형식이 틀리면 응답 형식 오류로 처리한다."""

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_bot.core.errors import response_format_error
from stock_bot.core.logging_setup import log
from stock_bot.core.masking import secrets

logger = logging.getLogger("stock_bot.reader")

# 로그에 남길 본문 앞부분 길이. 키움 클라이언트의 형식 오류 로그와 같다.
_LOG_BODY_LIMIT = 200


class Reader:
    """키움 응답에서 필드를 꺼낸다. 필요한 필드가 없으면 응답 형식 오류로 처리한다."""

    def __init__(self, data: dict[str, Any], api_id: str) -> None:
        self._data = data
        self._api_id = api_id

    def text(self, key: str) -> str:
        return str(required(self._data, key, self._api_id)).strip()

    def optional(self, key: str) -> str | None:
        """없어도 되는 글자 필드. 없거나 빈 값이면 None."""
        value = self._data.get(key)
        text = "" if value is None else str(value).strip()
        return text or None

    def integer(self, key: str) -> int | None:
        value = self.number(key)
        return None if value is None else int(value)

    def decimal(self, key: str) -> float | None:
        value = self.number(key)
        return None if value is None else float(value)

    def rows(self, key: str, reader: type[Reader] | None = None) -> list[Reader]:
        """목록 필드의 각 항목을 읽는다. reader로 항목을 읽을 Reader 종류를 바꿀 수 있다."""
        value = required(self._data, key, self._api_id)
        if not isinstance(value, list):
            raise response_format_error(self._api_id, f"{key} 필드가 목록이 아닙니다.")
        make = reader or Reader
        return [make(row, self._api_id) for row in value if isinstance(row, dict)]

    def number(self, key: str) -> Decimal | None:
        """'-00000000196888', '156464.6701' 같은 문자열을 숫자로 바꾼다. 빈 값은 None."""
        raw = self.text(key)
        if raw == "":
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            # 원인을 찾을 수 있게 받은 값을 담는다. 너무 길면 자른다.
            raise response_format_error(
                self._api_id, f"{key} 값이 숫자가 아닙니다: {raw[:40]!r}"
            ) from None


def required(data: dict[str, Any], key: str, api_id: str) -> Any:
    if key not in data or data[key] is None:
        # 성공 응답에 필드가 빠진 경우다. HTTP 상태는 같은 request_id의 kiwoom_response 로그에 있다.
        # 자른 뒤에는 비밀값을 알아볼 수 없으므로 먼저 가린다.
        body = secrets.mask(json.dumps(data, ensure_ascii=False, default=str))
        log(
            logger,
            logging.ERROR,
            "kiwoom_field_missing",
            api_id=api_id,
            key=key,
            body_head=body[:_LOG_BODY_LIMIT],
        )
        raise response_format_error(api_id, f"{key} 필드가 없습니다.")
    return data[key]
