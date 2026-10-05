"""키움 응답에서 필드를 꺼낸다. 필요한 필드가 없거나 형식이 틀리면 응답 형식 오류로 처리한다."""

from decimal import Decimal, InvalidOperation
from typing import Any

from stock_bot.errors import response_format_error


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
        raise response_format_error(api_id, f"{key} 필드가 없습니다.")
    return data[key]
