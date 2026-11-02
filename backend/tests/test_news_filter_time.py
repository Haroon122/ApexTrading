"""News blackout must compare live (UTC) calendar times against real UTC now."""
from datetime import datetime, timedelta

import app.services.news_filter as nf


class _FakeLive(nf.LiveCalendarProvider):
    def __init__(self, events):
        self._events = events

    def get_events(self, start, end):
        return [e for e in self._events if start <= e["timestamp"] <= end], True


def _with_events(monkeypatch, minutes_from_now):
    ev = [{"name": "CPI", "currency": "USD", "impact": "HIGH",
           "timestamp": datetime.utcnow() + timedelta(minutes=minutes_from_now)}]
    monkeypatch.setattr(nf.news_filter, "provider", _FakeLive(ev))


def test_blocks_before_and_after_event(monkeypatch):
    _with_events(monkeypatch, 10)
    assert nf.news_filter.is_news_blackout("XAUUSD")[0]
    _with_events(monkeypatch, -20)
    assert nf.news_filter.is_news_blackout("XAUUSD")[0]


def test_not_blocked_far_from_event(monkeypatch):
    _with_events(monkeypatch, 120)
    assert not nf.news_filter.is_news_blackout("XAUUSD")[0]


def test_ignores_caller_local_time(monkeypatch):
    # A local (UTC+5) or broker timestamp passed by a caller must not shift the window.
    _with_events(monkeypatch, 10)
    local = datetime.utcnow() + timedelta(hours=5)
    assert nf.news_filter.is_news_blackout("XAUUSD", current_time=local)[0]


def test_next_high_impact(monkeypatch):
    _with_events(monkeypatch, 90)
    nxt = nf.next_high_impact("XAUUSD")
    assert nxt and nxt["name"] == "CPI" and 88 <= nxt["minutes_from_now"] <= 90
