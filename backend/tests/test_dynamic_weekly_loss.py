"""
Automated Test Suite for Dynamic Weekly Loss Limit Risk Management
Verifies default values, Pydantic validation, dynamic RiskManager enforcement, and JSON persistence.
"""

import os
import sys
import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.domain.models import RiskSettings, AccountInfo, TradeDecisionReport, TradeSide, ConfirmationBreakdown
from app.services.trade_manager import trade_manager
from app.services.risk_manager import risk_manager
from app.core.config import settings


def test_1_default_weekly_loss_limit_setting():
    """TEST 1: Verify default weekly_loss_limit_pct is 7.1%."""
    default_cfg = RiskSettings()
    assert default_cfg.weekly_loss_limit_pct == 7.1


def test_2_validation_invalid_weekly_loss_limit():
    """TEST 2: Verify validation rejects zero or negative weekly_loss_limit_pct."""
    with pytest.raises(ValidationError):
        RiskSettings(weekly_loss_limit_pct=0.0)

    with pytest.raises(ValidationError):
        RiskSettings(weekly_loss_limit_pct=-5.0)

    with pytest.raises(ValidationError):
        RiskSettings(weekly_loss_limit_pct=150.0)


def test_3_dynamic_risk_manager_enforcement():
    """
    TEST 3: Verify RiskManager dynamically respects trade_manager.risk_settings.weekly_loss_limit_pct.
    Given account balance = $10,000 and weekly_pnl_usd = -$600 (-6.0%):
    - At 5.0% limit -> BREACHED (Trading locked)
    - At 10.0% limit -> PASSED (Trading unlocked)
    """
    account = AccountInfo(
        account_number=12345,
        balance=10000.0,
        equity=10000.0,
        weekly_pnl_usd=-600.0, # -6% loss
        daily_pnl_usd=0.0,
        drawdown_pct=0.0,
        open_positions_count=0,
        is_connected=True
    )

    report = TradeDecisionReport(
        symbol="XAUUSD",
        decision=TradeSide.BUY,
        timeframe="M15",
        confidence=0.85,
        confirmation_score=0.80,
        reasoning=["Test trade"],
        rejection_reasons=[],
        suggested_entry=2400.0,
        suggested_sl=2395.0,
        suggested_tp=2410.0,
        suggested_lots=0.10,
        risk_reward_ratio=2.0,
        breakdown=ConfirmationBreakdown(passed=True),
        timestamp=account.account_number
    )

    # 1. Set limit to 5.0% ($500 max loss)
    current_settings = trade_manager.risk_settings
    orig_weekly = current_settings.weekly_loss_limit_pct
    orig_max_trades = current_settings.max_trades_per_day
    orig_cooldown = current_settings.auto_trade_cooldown_seconds
    orig_consec = current_settings.max_consecutive_losses
    current_settings.weekly_loss_limit_pct = 5.0
    current_settings.max_trades_per_day = 1000
    current_settings.auto_trade_cooldown_seconds = 0
    current_settings.max_consecutive_losses = 999
    current_settings.max_positions_per_direction = 0
    current_settings.min_minutes_between_entries = 0
    current_settings.blocked_hours = []
    trade_manager.update_risk_settings(current_settings)

    try:
        result_5pct = risk_manager.evaluate_risk(account, report, "XAUUSD")
        assert result_5pct.passed is False
        assert any("Weekly Loss Limit breached" in r for r in result_5pct.reasons)
        assert "-5.0% of balance" in result_5pct.reasons[0]

        # 2. Immediately update limit to 10.0% ($1000 max loss)
        current_settings.weekly_loss_limit_pct = 10.0
        trade_manager.update_risk_settings(current_settings)

        result_10pct = risk_manager.evaluate_risk(account, report, "XAUUSD")
        assert result_10pct.passed is True
    finally:
        # Restore default setting
        current_settings.weekly_loss_limit_pct = orig_weekly
        current_settings.max_trades_per_day = orig_max_trades
        current_settings.auto_trade_cooldown_seconds = orig_cooldown
        current_settings.max_consecutive_losses = orig_consec
        trade_manager.update_risk_settings(current_settings)


def test_4_persistence_to_disk():
    """TEST 4: Verify trade_manager.update_risk_settings persists weekly_loss_limit_pct to risk_settings.json."""
    from app.services.trade_manager import CONFIG_PATH

    current_settings = trade_manager.risk_settings
    original_val = current_settings.weekly_loss_limit_pct

    try:
        current_settings.weekly_loss_limit_pct = 8.5
        trade_manager.update_risk_settings(current_settings)

        # Reload fresh from disk
        reloaded = trade_manager._load_persisted_risk_settings()
        assert reloaded.weekly_loss_limit_pct == 8.5
    finally:
        current_settings.weekly_loss_limit_pct = original_val
        trade_manager.update_risk_settings(current_settings)


def test_5_reset_weekly_loss_lock():
    """
    TEST 5: Verify reset_weekly_loss_lock clears lock, resets counter timestamp,
    logs exact message, preserves setting, and keeps normal risk checks active.
    """
    from app.services.trade_learner import trade_learner

    # 1. Trigger manual reset
    success, msg = trade_learner.reset_weekly_loss_lock()
    assert success is True
    assert "RESET" in msg

    # 2. Verify metadata persistence
    reset_ts = trade_learner.get_metadata("weekly_loss_reset_timestamp")
    assert reset_ts is not None

    # 3. Verify weekly loss limit percentage is preserved
    assert trade_manager.risk_settings.weekly_loss_limit_pct == 7.1

    current_settings = trade_manager.risk_settings
    orig_consec = current_settings.max_consecutive_losses
    orig_max_trades = current_settings.max_trades_per_day
    orig_cooldown = current_settings.auto_trade_cooldown_seconds

    current_settings.max_consecutive_losses = 999
    current_settings.max_positions_per_direction = 0
    current_settings.min_minutes_between_entries = 0
    current_settings.blocked_hours = []
    current_settings.max_trades_per_day = 1000
    current_settings.auto_trade_cooldown_seconds = 0
    trade_manager.update_risk_settings(current_settings)

    try:
        # 4. Verify risk checks are STILL active for new trades after reset
        account = AccountInfo(
            account_number=12345,
            balance=10000.0,
            equity=10000.0,
            weekly_pnl_usd=0.0,  # Reset weekly PnL
            daily_pnl_usd=0.0,
            drawdown_pct=0.0,
            open_positions_count=0,
            is_connected=True
        )
        report = TradeDecisionReport(
            symbol="XAUUSD",
            decision=TradeSide.BUY,
            timeframe="M15",
            confidence=0.85,
            confirmation_score=0.80,
            reasoning=["Post-reset test trade"],
            rejection_reasons=[],
            suggested_entry=2400.0,
            suggested_sl=2395.0,
            suggested_tp=2410.0,
            suggested_lots=0.10,
            risk_reward_ratio=2.0,
            breakdown=ConfirmationBreakdown(passed=True),
            timestamp=account.account_number
        )

        # Risk evaluation passes when weekly PnL is within limit
        res_passed = risk_manager.evaluate_risk(account, report, "XAUUSD")
        assert res_passed.passed is True

        # If subsequent trades breach weekly loss limit again (-7.5%), risk engine locks again
        account_breached = AccountInfo(
            account_number=12345,
            balance=10000.0,
            equity=8000.0,
            weekly_pnl_usd=-2000.0,  # > weekly loss limit on $10,000 balance
            daily_pnl_usd=0.0,
            drawdown_pct=0.0,
            open_positions_count=0,
            is_connected=True
        )
        res_relocked = risk_manager.evaluate_risk(account_breached, report, "XAUUSD")
        assert res_relocked.passed is False
        assert any("Weekly Loss Limit breached" in r for r in res_relocked.reasons)
    finally:
        current_settings.max_consecutive_losses = orig_consec
        current_settings.max_trades_per_day = orig_max_trades
        current_settings.auto_trade_cooldown_seconds = orig_cooldown
        trade_manager.update_risk_settings(current_settings)


def test_6_auto_broker_week_reset():
    """
    TEST 6: Verify automatic weekly reset when a new broker trading week arrives.
    """
    from datetime import datetime, timedelta
    from app.services.trade_learner import trade_learner

    server_now = trade_learner.get_broker_current_time()
    week_start = trade_learner.get_broker_week_start(server_now)
    assert week_start <= server_now

    # Simulate old week stored in metadata
    old_week = (week_start - timedelta(days=7)).isoformat()
    trade_learner.set_metadata("last_known_broker_week_start", old_week)

    # Calling get_realized_pnl triggers check_and_apply_auto_weekly_reset
    daily_pnl, weekly_pnl = trade_learner.get_realized_pnl()

    # Verify last_known_broker_week_start was automatically updated to current week
    updated_week = trade_learner.get_metadata("last_known_broker_week_start")
    assert updated_week == week_start.isoformat()


def test_7_reset_testing_risk_baseline(monkeypatch):
    """
    TEST 7: Verify reset_testing_risk_baseline clears all circuit breaker baselines,
    persists testing_risk_reset_timestamp in SQLite, logs exact message, and preserves limits.
    """
    from app.services.trade_learner import trade_learner
    from datetime import datetime, timedelta

    # Ensure reset timestamp is ahead of any pre-existing DB trade timestamps
    future_now = datetime.now() + timedelta(hours=24)
    monkeypatch.setattr(trade_learner, "get_broker_current_time", lambda: future_now)

    # 1. Execute testing risk baseline reset
    success, msg = trade_learner.reset_testing_risk_baseline()
    assert success is True
    assert msg == "TESTING RISK BASELINE RESET — ALL RISK PROTECTIONS ACTIVE"

    # 2. Verify metadata persistence in SQLite
    testing_ts = trade_learner.get_metadata("testing_risk_reset_timestamp")
    weekly_ts = trade_learner.get_metadata("weekly_loss_reset_timestamp")
    assert testing_ts is not None
    assert weekly_ts is not None

    # 3. Verify realized PnL baseline resets
    daily_pnl, weekly_pnl = trade_learner.get_realized_pnl()
    assert daily_pnl == 0.0

    # 4. Verify pre-trade risk engine remains active and locks if new daily loss threshold is breached
    current_settings = trade_manager.risk_settings
    orig_consec = current_settings.max_consecutive_losses
    orig_max_trades = current_settings.max_trades_per_day
    orig_cooldown = current_settings.auto_trade_cooldown_seconds

    current_settings.max_consecutive_losses = 999
    current_settings.max_positions_per_direction = 0
    current_settings.min_minutes_between_entries = 0
    current_settings.blocked_hours = []
    current_settings.max_trades_per_day = 1000
    current_settings.auto_trade_cooldown_seconds = 0
    trade_manager.update_risk_settings(current_settings)

    try:
        report = TradeDecisionReport(
            symbol="XAUUSD",
            decision=TradeSide.BUY,
            timeframe="M15",
            confidence=0.85,
            confirmation_score=0.80,
            reasoning=["Post-reset test trade"],
            rejection_reasons=[],
            suggested_entry=2400.0,
            suggested_sl=2395.0,
            suggested_tp=2410.0,
            suggested_lots=0.10,
            risk_reward_ratio=2.0,
            breakdown=ConfirmationBreakdown(passed=True),
            timestamp=12345
        )

        account_clean = AccountInfo(
            account_number=12345,
            balance=10000.0,
            equity=10000.0,
            daily_pnl_usd=0.0,
            weekly_pnl_usd=0.0,
            drawdown_pct=0.0,
            open_positions_count=0,
            is_connected=True
        )
        res_clean = risk_manager.evaluate_risk(account_clean, report, "XAUUSD")
        assert res_clean.passed is True

        account_daily_breach = AccountInfo(
            account_number=12345,
            balance=10000.0,
            equity=8500.0,
            daily_pnl_usd=-1500.0, # > daily loss limit ($1000)
            weekly_pnl_usd=-1500.0,
            drawdown_pct=4.0,
            open_positions_count=0,
            is_connected=True
        )
        res_daily_breach = risk_manager.evaluate_risk(account_daily_breach, report, "XAUUSD")
        assert res_daily_breach.passed is False
        assert any("Daily Loss Limit breached" in r for r in res_daily_breach.reasons)
    finally:
        current_settings.max_consecutive_losses = orig_consec
        current_settings.max_trades_per_day = orig_max_trades
        current_settings.auto_trade_cooldown_seconds = orig_cooldown
        trade_manager.update_risk_settings(current_settings)


def test_8_real_account_reset_blocked(monkeypatch):
    """
    TEST 8: Verify testing-session reset is strictly blocked when connected to a REAL account.
    """
    from app.services.trade_learner import trade_learner

    monkeypatch.setattr(trade_learner, "is_real_account", lambda: True)
    success, msg = trade_learner.reset_testing_risk_baseline()
    assert success is False
    assert "RESET BLOCKED" in msg
    assert "REAL live trading accounts" in msg


if __name__ == "__main__":
    print("Running test_1_default_weekly_loss_limit_setting...")
    test_1_default_weekly_loss_limit_setting()
    print("Running test_2_validation_invalid_weekly_loss_limit...")
    test_2_validation_invalid_weekly_loss_limit()
    print("Running test_3_dynamic_risk_manager_enforcement...")
    test_3_dynamic_risk_manager_enforcement()
    print("Running test_4_persistence_to_disk...")
    test_4_persistence_to_disk()
    print("Running test_5_reset_weekly_loss_lock...")
    test_5_reset_weekly_loss_lock()
    print("Running test_6_auto_broker_week_reset...")
    test_6_auto_broker_week_reset()
    print("Running test_7_reset_testing_risk_baseline...")
    test_7_reset_testing_risk_baseline(pytest.MonkeyPatch())
    print("Running test_8_real_account_reset_blocked...")
    test_8_real_account_reset_blocked(pytest.MonkeyPatch())
    print("\nALL DYNAMIC WEEKLY LOSS & TESTING BASELINE RESET TESTS PASSED CLEANLY!")
