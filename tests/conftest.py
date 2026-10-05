from collections.abc import Callable, Iterator
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from stock_bot import stock_search
from stock_bot.app import create_app
from tests.fake_kiwoom import FAKE_ENV

Handler = Callable[[httpx.Request], httpx.Response]


def _unexpected(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected Kiwoom call: {request.url}")


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(
        handler: Handler = _unexpected,
        environ: dict[str, str] | None = None,
        today: Callable[[], date] | None = None,
    ) -> TestClient:
        app = create_app(
            environ=FAKE_ENV if environ is None else environ,
            transport=httpx.MockTransport(handler),
            static_dir=None,
            log_dir=None,
            today=today,
        )
        client = TestClient(app)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


@pytest.fixture(autouse=True)
def no_list_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """국내 종목 목록 호출 사이의 대기 시간을 테스트에서는 없앤다."""
    monkeypatch.setattr(stock_search, "DOMESTIC_LIST_INTERVAL_SECONDS", 0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
