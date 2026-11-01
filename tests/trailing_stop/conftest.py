import pytest

from stock_bot.fill import feed as fill_feed
from stock_bot.kiwoom import realtime as realtime_module
from stock_bot.trailing_stop import watch as ts_watch


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    monkeypatch.setattr(ts_watch, "SESSION_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(ts_watch, "HOLDINGS_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(ts_watch, "HOLDINGS_RETRY_MAX_SECONDS", 0.04)
    monkeypatch.setattr(ts_watch, "RECORD_WRITE_SECONDS", 0.01)
    monkeypatch.setattr(realtime_module, "RECONNECT_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(fill_feed, "REGISTER_RETRY_MAX_SECONDS", 0.04)
