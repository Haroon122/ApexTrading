import json
from types import SimpleNamespace as NS

import pytest

from app.domain.models import RiskSettings, TradeSide
from app.services import notifier as mod
from app.services.notifier import Notifier

TOPIC = "apex-test-topic-1234"


@pytest.fixture(autouse=True)
def _isolated_creds(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CREDS_PATH", str(tmp_path / "creds.json"))


class Rec(Notifier):
    """Notifier that records messages instead of calling ntfy."""
    def __init__(self, configured=True):
        super().__init__()
        self.cfg = configured
        self.msgs = []
    def configured(self):
        return self.cfg
    def _post(self, text, title="", priority=3, tags=""):
        self.msgs.append((title, priority, text)); return True
    def send(self, text, key=None, rs=None, **kw):
        if not self.configured() or (rs is not None and not self.enabled(rs)):
            return False
        if key:
            if key in self._sent: return False
            self._sent.add(key)
        self._post(text, **kw); return True


def _trade(side=TradeSide.SELL):
    return NS(trade_id="42", symbol="XAUUSD", side=side, lots=0.05, entry_price=3890.55, sl_price=3948.63,
              tp_price=3308.66, setup_name="DONCHIAN TREND: H1 85-bar Breakout")


def test_open_message_has_the_facts_and_is_sent_once():
    n, rs = Rec(), RiskSettings()
    assert n.trade_opened(_trade(), "Donchian", "H1", rs)
    assert not n.trade_opened(_trade(), "Donchian", "H1", rs)        # same ticket: no duplicate
    title, _, m = n.msgs[0]
    assert "SELL" in title and "3890.55" in m and "3948.63" in m and "Donchian" in m and "$290.40" in m


def test_close_message_shows_profit_and_loss():
    n, rs = Rec(), RiskSettings()
    n.trade_closed(_trade(), 3865.0, 89.9, "USER_CLOSE", rs)
    t2 = _trade(); t2.trade_id = "43"
    n.trade_closed(t2, 4027.04, -203.39, "[sl 4027.04]", rs)
    assert "PROFIT +$89.90" in n.msgs[0][0]
    assert "LOSS -$203.39" in n.msgs[1][0] and "[sl 4027.04]" in n.msgs[1][2] and n.msgs[1][1] == 4


def test_disabled_or_unconfigured_sends_nothing():
    n = Rec(); assert not n.trade_opened(_trade(), "x", "H1", RiskSettings(notifications_enabled=False)) and n.msgs == []
    n2 = Rec(configured=False); assert not n2.trade_opened(_trade(), "x", "H1", RiskSettings()) and n2.msgs == []


def test_spread_alert_needs_open_trade_threshold_and_cooldown():
    n, rs = Rec(), RiskSettings()
    wide = NS(symbol="XAUUSD", bid=3985.0, ask=4026.5)
    normal = NS(symbol="XAUUSD", bid=3985.0, ask=3985.13)
    assert not n.check_spread(wide, 0, rs)           # no open trade: nothing to protect
    assert not n.check_spread(normal, 1, rs)
    assert n.check_spread(wide, 1, rs) and "$41.50" in n.msgs[0][2] and n.msgs[0][1] == 5
    assert not n.check_spread(wide, 1, rs)           # cooldown: one warning, not one per second
    assert not Rec().check_spread(wide, 1, RiskSettings(notify_spread_alert_usd=0))


def test_topic_validation_and_saving():
    n = Notifier()
    assert n.validate(TOPIC) is None
    for bad in ("short", "has space in it", "", "emoji-\u2705-topic-name"):
        assert n.validate(bad)
    assert not n.configured()
    assert n.save_topic(TOPIC) is None and n.configured()
    assert n.save_topic("bad topic") and n._topic() == TOPIC          # a bad value changes nothing
    st = n.status()
    assert st["configured"] and st["topic_hint"] == "...1234" and TOPIC not in json.dumps(st)


def test_post_sends_to_the_topic_with_headers_and_never_raises(monkeypatch):
    n = Notifier(); n.save_topic(TOPIC)
    seen = {}
    class R:
        status = 200
        def __enter__(s): return s
        def __exit__(s, *a): return False
    def fake(req, timeout=0):
        seen.update(url=req.full_url, body=req.data.decode(), h={k.lower(): v for k, v in req.header_items()})
        return R()
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    assert n._post("hello", title="Trade opened: SELL \u2705", priority=4, tags="x")
    assert seen["url"] == f"https://ntfy.sh/{TOPIC}" and seen["body"] == "hello"
    assert seen["h"]["title"] == "Trade opened: SELL " and seen["h"]["priority"] == "4"   # header is ASCII only
    def boom(*a, **k): raise OSError("blocked")
    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert n._post("x") is False
    assert TOPIC not in (n.last_error or "") and "ntfy.sh" in n.last_error


def test_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.config import settings
    c = TestClient(app); h = {"X-API-Key": settings.API_KEY}
    r = c.get("/api/v1/notifications/status")
    assert r.status_code == 200 and r.json()["configured"] is False
    t = c.post("/api/v1/notifications/test", headers=h).json()
    assert t["ok"] is False and "Risk Settings" in (t["last_error"] or "")
    assert c.post("/api/v1/notifications/config", json={"topic": "no"}, headers=h).status_code == 400
    r = c.post("/api/v1/notifications/config", json={"topic": TOPIC}, headers=h)
    assert r.status_code == 200 and r.json()["configured"] and TOPIC not in r.text
    assert c.post("/api/v1/notifications/config", json={"topic": TOPIC}).status_code in (401, 403)
