import logging
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from stock_bot import app as app_module
from stock_bot import kiwoom
from stock_bot.app import create_app
from tests.fake_kiwoom import FAKE_ENV
from tests.log_capture import JsonLines

Handler = Callable[[httpx.Request], httpx.Response]


def _unexpected(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected Kiwoom call: {request.url}")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """설정 파일을 저장할 폴더. 테스트마다 비어 있는 임시 폴더다."""
    return tmp_path / "data"


@pytest.fixture
def make_client(data_dir: Path) -> Iterator[Callable[..., TestClient]]:
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
            data_dir=data_dir,
            today=today,
        )
        client = TestClient(app)
        password = (FAKE_ENV if environ is None else environ).get("PASSWORD", "")
        client.post("/api/auth/login", json={"password": password})
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """data_dir를 넘기지 않은 테스트도 프로젝트의 data/(실제 SL / TP 설정)를 읽지 않게 한다."""
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path / "default-data")


@pytest.fixture(autouse=True)
def no_call_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 TR 호출 사이의 대기 시간을 테스트에서는 없앤다. 간격 테스트는 따로 되돌린다."""
    monkeypatch.setattr(kiwoom, "CALL_INTERVAL_SECONDS", 0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def json_logs() -> Iterator[JsonLines]:
    handler = JsonLines()
    root = logging.getLogger("stock_bot")
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)
