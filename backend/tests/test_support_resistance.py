from datetime import datetime, timedelta

from app.domain.models import Candle
from app.services import support_resistance as sr


def _c(i, o, h, l, c, tf="H1"):
    return Candle(timestamp=datetime(2026, 1, 1) + timedelta(hours=i), open=o, high=h, low=l, close=c,
                  volume=1, timeframe=tf, is_closed=True)


def _wave(cycles=12, lo=2000.0, hi=2050.0, step=5.0):
    """Price ping-pongs between a floor and a ceiling: both must become levels."""
    out, p, i, up = [], lo + 10, 0, True
    for _ in range(cycles):
        target = hi if up else lo
        while (p < target) if up else (p > target):
            n = p + step if up else p - step
            out.append(_c(i, p, max(p, n) + 1, min(p, n) - 1, n)); p = n; i += 1
        up = not up
    return out


def test_pivots_need_k_candles_on_both_sides():
    cs = _wave()
    piv = sr.pivots(cs, 3)
    assert piv, "a ping-pong market must have swing points"
    assert all(3 <= p["idx"] < len(cs) - 3 for p in piv)       # last candles are unconfirmed


def test_floor_and_ceiling_found_with_touches():
    cs = _wave()
    out = sr.build({"H1": cs}, "H1", price=2025.0)
    prices = [lv["price"] for lv in out]
    assert any(abs(p - 2051) < 3 for p in prices), prices       # ceiling
    assert any(abs(p - 1999) < 3 for p in prices), prices       # floor
    assert all(lv["touches"] >= 2 for lv in out)
    assert {lv["kind"] for lv in out} == {"support", "resistance"}


def test_kind_follows_price():
    cs = _wave()
    above = sr.build({"H1": cs}, "H1", price=2060.0)            # price above the old ceiling
    assert all(lv["kind"] == "support" for lv in above if lv["price"] < 2060)
    below = sr.build({"H1": cs}, "H1", price=1990.0)
    assert all(lv["kind"] == "resistance" for lv in below if lv["price"] > 1990)


def test_too_little_data_gives_nothing():
    assert sr.build({"H1": _wave(cycles=1)[:20]}, "H1", price=2000.0) == []


def test_levels_capped_per_side_and_strength_set():
    cs = _wave(cycles=30)
    out = sr.build({"H1": cs}, "H1", price=2025.0)
    assert sum(1 for l in out if l["kind"] == "support") <= sr.MAX_PER_SIDE
    assert sum(1 for l in out if l["kind"] == "resistance") <= sr.MAX_PER_SIDE
    assert all(l["strength"] in (1, 2, 3) for l in out)


def test_same_level_on_two_timeframes_is_merged():
    h1 = _wave()
    h4 = [Candle(**{**c.model_dump(), "timeframe": "H4"}) for c in _wave()]
    out = sr.build({"H1": h1, "H4": h4}, "H1", price=2025.0)
    prices = sorted(l["price"] for l in out)
    assert all(b - a > 0.3 for a, b in zip(prices, prices[1:]))   # no duplicate lines
    assert {l["tf"] for l in out} == {"H4"}                        # the bigger timeframe wins


def test_forming_candle_ignored():
    cs = _wave()
    forming = Candle(**{**cs[-1].model_dump(), "timestamp": cs[-1].timestamp + timedelta(hours=1),
                        "high": 9999.0, "is_closed": False})
    a = sr.build({"H1": cs}, "H1", price=2025.0)
    b = sr.build({"H1": cs + [forming]}, "H1", price=2025.0)
    assert a == b


class _FakeAdapter:
    def __init__(self): self.calls = 0
    def fetch_candles(self, symbol, tf, count):
        self.calls += 1
        return _wave()


def test_get_levels_cached_until_new_candle_and_relabels():
    sr._cache.clear()
    ad = _FakeAdapter()
    first = sr.get_levels(ad, "XAUUSD", "H1", 2025.0)
    assert first["ok"]
    moved = sr.get_levels(ad, "XAUUSD", "H1", 2070.0)
    assert all(l["kind"] == "support" for l in moved["levels"] if l["price"] < 2070)


def test_endpoint_and_bad_timeframe():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.config import settings
    c = TestClient(app)
    h = {"X-API-Key": settings.API_KEY} if hasattr(settings, "API_KEY") else {}
    r = c.get("/api/v1/market/levels?symbol=XAUUSD&timeframe=M15", headers=h)
    assert r.status_code == 200 and "levels" in r.json()
    assert c.get("/api/v1/market/levels?symbol=XAUUSD&timeframe=ZZ", headers=h).status_code == 400
