"""
Test isolation: every test uses a temporary trade database and settings file.

Without this, tests wrote fake trades (win_0, test_learn_999, ...) into the real
data/trading_memory.db and overwrote data/risk_settings.json.
"""
import pytest

from app.domain.models import RiskSettings
from app.services import trade_manager as tm_module
from app.services.trade_learner import trade_learner


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_learner, "db_path", str(tmp_path / "trading_memory.db"))
    trade_learner._init_db()
    monkeypatch.setattr(tm_module, "CONFIG_PATH", tmp_path / "risk_settings.json")

    saved = tm_module.trade_manager.risk_settings
    # Start from model defaults, not whatever is in the live settings file.
    rs = RiskSettings()
    tm_module.trade_manager.risk_settings = rs
    # New entry guards are tested on their own; keep them off for older tests
    # so results don't depend on the clock or leftover open positions.
    rs.max_positions_per_direction = 0
    rs.min_minutes_between_entries = 0
    rs.blocked_hours = []
    # In-memory counters leak between tests (cooldown / daily trade count).
    monkeypatch.setattr(tm_module.trade_manager, "last_auto_trade_time", None)
    monkeypatch.setattr(tm_module.trade_manager, "daily_trade_count", 0)
    yield
    tm_module.trade_manager.risk_settings = saved
