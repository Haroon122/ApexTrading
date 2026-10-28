"""Tests for stacking guard, entry spacing and blocked hours in RiskManager."""
from datetime import datetime, timedelta
from unittest.mock import patch

from app.domain.models import (
    AccountInfo, ConfirmationBreakdown, OrderType, TradeDecisionReport,
    TradeRecord, TradeSide, TradeState,
)
from app.services.risk_manager import risk_manager
from app.services.trade_learner import trade_learner
from app.services.trade_manager import trade_manager


def _account():
    return AccountInfo(account_number=1, balance=10000.0, equity=10000.0, margin=0.0,
                       free_margin=10000.0, margin_level=0.0, daily_pnl_usd=0.0,
                       weekly_pnl_usd=0.0, drawdown_pct=0.0, open_positions_count=0)


def _report(side=TradeSide.BUY, entry=2400.0):
    sl = entry - 5 if side == TradeSide.BUY else entry + 5
    tp = entry + 10 if side == TradeSide.BUY else entry - 10
    return TradeDecisionReport(
        symbol="XAUUSD", decision=side, timeframe="M15", confidence=0.85,
        confirmation_score=0.8, reasoning=[], rejection_reasons=[],
        suggested_entry=entry, suggested_sl=sl, suggested_tp=tp, suggested_lots=0.01,
        risk_reward_ratio=2.0, breakdown=ConfirmationBreakdown(passed=True),
        timestamp=datetime.now(),
    )


def _open_trade(side, entry):
    return TradeRecord(trade_id="1", symbol="XAUUSD", side=side, order_type=OrderType.MARKET,
                       entry_price=entry, sl_price=entry - 5, tp_price=entry + 10, lots=0.01,
                       state=TradeState.OPEN, open_time=datetime.now())


def _evaluate(report, active=None):
    rs = trade_manager.risk_settings
    rs.lot_sizing_mode, rs.user_configured_lot, rs.max_allowed_lot = "FIXED", 0.01, 0.05
    rs.auto_trade_cooldown_seconds, rs.max_consecutive_losses, rs.max_trades_per_day = 0, 999, 999
    with patch("app.adapters.mt5_adapter.mt5_adapter") as mt5, \
         patch.dict(trade_manager.active_trades, {t.trade_id: t for t in (active or [])}, clear=True):
        mt5.is_connected = False
        mt5.get_positions.return_value = []
        return risk_manager.evaluate_risk(_account(), report, "XAUUSD")


def _has(res, text):
    return any(text in r for r in res.reasons)


def test_stacking_guard_blocks_same_direction_far_away():
    trade_manager.risk_settings.max_positions_per_direction = 1
    res = _evaluate(_report(TradeSide.BUY, 2410.0), [_open_trade(TradeSide.BUY, 2400.0)])
    assert _has(res, "STACKING GUARD")


def test_stacking_guard_allows_opposite_direction():
    trade_manager.risk_settings.max_positions_per_direction = 1
    res = _evaluate(_report(TradeSide.SELL, 2410.0), [_open_trade(TradeSide.BUY, 2400.0)])
    assert not _has(res, "STACKING GUARD")


def test_entry_spacing_uses_db_last_open():
    trade_manager.risk_settings.min_minutes_between_entries = 30
    trade_learner.log_trade_execution("100", "XAUUSD", "BUY", "M15", 2400, 0.01, 2395, 2410,
                                      open_timestamp=datetime.now() - timedelta(minutes=10))
    assert _has(_evaluate(_report()), "ENTRY SPACING")


def test_entry_spacing_passes_after_gap():
    trade_manager.risk_settings.min_minutes_between_entries = 30
    trade_learner.log_trade_execution("101", "XAUUSD", "BUY", "M15", 2400, 0.01, 2395, 2410,
                                      open_timestamp=datetime.now() - timedelta(minutes=45))
    assert not _has(_evaluate(_report()), "ENTRY SPACING")


def test_blocked_hours():
    trade_manager.risk_settings.blocked_hours = [datetime.now().hour]
    assert _has(_evaluate(_report()), "BLOCKED HOUR")
    trade_manager.risk_settings.blocked_hours = []
    assert not _has(_evaluate(_report()), "BLOCKED HOUR")
