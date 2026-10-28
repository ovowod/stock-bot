"""브라우저에 돌려줄 오류. 모든 오류는 같은 응답 형식을 쓴다."""

from typing import Any

from fastapi.responses import JSONResponse

from stock_bot.logging_setup import request_id_var
from stock_bot.masking import secrets


class AppError(Exception):
    def __init__(
        self, kind: str, message: str, status: int, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.detail = detail or {}


def response_format_error(api_id: str, cause: str) -> AppError:
    return AppError(
        "response_format_error",
        f"키움 응답 형식이 올바르지 않습니다. ({cause})",
        502,
        {"api_id": api_id},
    )


def error_response(status: int, kind: str, message: str, detail: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "kind": kind,
                "message": secrets.mask(message),
                "request_id": request_id_var.get(),
                **detail,
            }
        },
    )
