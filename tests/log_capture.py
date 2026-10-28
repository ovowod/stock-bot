"""로그를 JSON 한 줄로 모은다.

요청 ID처럼 로그를 남기는 그 자리의 context에서 읽는 값을 테스트에서 본다.
"""

import json
import logging
from typing import Any

from stock_bot.core.logging_setup import JsonFormatter


class JsonLines(logging.Handler):
    """로그를 남기는 그 자리에서 JSON 한 줄로 만든다. 요청 ID는 그때의 context에서 읽힌다."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))
