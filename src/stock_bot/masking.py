"""비밀값 가림. 브라우저 응답과 로그가 같은 함수를 쓴다."""

import threading

MASK = "***"
_MIN_SECRET_LENGTH = 4


class SecretRegistry:
    """앱 키, 시크릿, 접근 토큰, 계좌번호처럼 어디에도 나가면 안 되는 값을 모아 둔다."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, *values: str) -> None:
        with self._lock:
            self._values.update(v for v in values if len(v) >= _MIN_SECRET_LENGTH)

    def mask(self, text: str) -> str:
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            text = text.replace(value, MASK)
        return text


secrets = SecretRegistry()


def mask_account_no(account_no: str) -> str:
    """앞 4자리와 끝 2자리만 남긴다."""
    digits = "".join(ch for ch in account_no if ch.isdigit())
    if len(digits) <= 6:
        return "*" * len(digits)
    return f"{digits[:4]}{'*' * (len(digits) - 6)}{digits[-2:]}"
