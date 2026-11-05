import pytest

from stock_bot.dead_cross import watch as dc_watch
from stock_bot.fill import feed as fill_feed
from stock_bot.kiwoom import realtime as realtime_module


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    monkeypatch.setattr(dc_watch, "SESSION_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(dc_watch, "RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(dc_watch, "RETRY_MAX_SECONDS", 0.04)
    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_MAX_SECONDS", 0.04)
