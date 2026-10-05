from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from stock_bot.app import create_app
from tests.fake_kiwoom import FAKE_ENV

Handler = Callable[[httpx.Request], httpx.Response]


def _unexpected(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected Kiwoom call: {request.url}")


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(
        handler: Handler = _unexpected, environ: dict[str, str] | None = None
    ) -> TestClient:
        app = create_app(
            environ=FAKE_ENV if environ is None else environ,
            transport=httpx.MockTransport(handler),
            static_dir=None,
            log_dir=None,
        )
        client = TestClient(app)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
