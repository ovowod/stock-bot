"""JSON 한 줄 로그를 stdout과 logs/ 날짜별 파일에 남긴다."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from stock_bot.masking import secrets

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
environment_var: ContextVar[str] = ContextVar("environment", default="-")

RETENTION_DAYS = 14


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "module": record.name,
            "env": environment_var.get(),
            "request_id": request_id_var.get(),
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return secrets.mask(json.dumps(entry, ensure_ascii=False, default=str))


def setup_logging(log_dir: Path | None) -> None:
    root = logging.getLogger("stock_bot")
    if getattr(root, "_configured", False):
        return
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            TimedRotatingFileHandler(
                log_dir / "stock-bot.log",
                when="midnight",
                backupCount=RETENTION_DAYS,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    root._configured = True  # type: ignore[attr-defined]


def log(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """필드 값은 기록 전에 가린다. 포맷터도 최종 줄을 한 번 더 가린다."""
    masked = {key: _mask_value(value) for key, value in fields.items()}
    logger.log(level, event, extra={"fields": masked})


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return secrets.mask(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return secrets.mask(json.dumps(value, ensure_ascii=False, default=str))
