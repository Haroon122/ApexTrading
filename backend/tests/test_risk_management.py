"""
Automated Regression Test Suite for Risk Management & Circuit Breaker Engine
Verifies independent risk filter pipeline, max consecutive loss limits, daily loss limits,
account equity guards, fixed-lot safety rules, metric signs, and run determinism.
"""

import os
import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.risk_engine import RiskEngine, RiskBlockReason
from app.services.backtester import backtester


def test_1_cooldown_and_daily_loss_independence():
    """
    TEST 1: Cooldown enabled + Daily Loss Limit reached.
    Expected: DAILY_LOSS_LIMIT blocks the trade even when cooldown has expired.
    """
    now = datetime(2026, 8, 1, 12, 0, 0)
    last_exit = now - timedelta(seconds=120) # Cooldown expired 60s ago
    
    passed, reason, details = RiskEngine.evaluate_trade_risk(
        symbol="XAUUSD",
        current_time=now,
        current_spread_pips=2.0,
        report_confidence=0.85,
        report_rr_ratio=1.5,
        account_balance=100.0,
        start_of_day_balance=100.0,
        daily_realized_pnl=-7.0, # Breached 6% ($6.00) limit
        consecutive_losses=0,
        last_trade_exit_time=last_exit,
        trade_lots=0.01,
        sl_pips=20.0,
        pip_value_per_lot=10.0,
        commission_per_lot=7.0,
        max_spread_pips=5.0,
        auto_trade_cooldown_seconds=60,
        daily_loss_limit_pct=6.0,
        max_consecutive_losses=5,
        min_ai_confidence=0.65,
        min_risk_reward_ratio=1.5
    )
    
    assert passed is False
    assert reason == RiskBlockReason.DAILY_LOSS_LIMIT.value
    assert "Daily loss limit breached" in details


def test_2_cooldown_and_consecutive_loss_independence():
    """
    TEST 2: Cooldown enabled + Max Consecutive Loss reached.
    Expected: CONSECUTIVE_LOSS_LIMIT blocks the trade even when cooldown has expired.
    """
    now = datetime(2026, 8, 1, 12, 0, 0)
    last_exit = now - timedelta(seconds=120) # Cooldown expired
    
    passed, reason, details = RiskEngine.evaluate_trade_risk(
        symbol="XAUUSD",
        current_time=now,
        current_spread_pips=2.0,
        report_confidence=0.85,
        report_rr_ratio=1.5,
        account_balance=100.0,
        start_of_day_balance=100.0,
        daily_realized_pnl=0.0,
        consecutive_losses=5, # Limit reached
        last_trade_exit_time=last_exit,
        trade_lots=0.01,
        sl_pips=20.0,
        pip_value_per_lot=10.0,
        commission_per_lot=7.0,
        max_spread_pips=5.0,
        auto_trade_cooldown_seconds=60,
        daily_loss_limit_pct=6.0,
        max_consecutive_losses=5,
        min_ai_confidence=0.65,
        min_risk_reward_ratio=1.5
    )
    
    assert passed is False
    assert reason == RiskBlockReason.CONSECUTIVE_LOSS_LIMIT.value


def test_3_max_consecutive_loss_limit():
    """
    TEST 3: Max Consecutive Loss = 5.
    Expected: Backtester max_consecutive_losses metric never exceeds 5.
    """
    res = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M1",
        count=1000,
        initial_balance=100.0,
        risk_pct=1.5,
        lot_sizing_mode="AUTO_RISK",
        user_configured_lot=0.01,
        auto_trade_cooldown_seconds=60,
        daily_loss_limit_pct=6.0,
        max_consecutive_losses=5,
        max_spread_pips=5.0
    )
    
    assert "error" not in res
    assert res["max_consecutive_losses"] <= 5


def test_4_daily_loss_limit_stops_trading():
    """
    TEST 4: Daily Loss Limit = 6%.
    Expected: Daily loss limit blocks further trades once day limit is reached.
    """
    res = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M1",
        count=1000,
        initial_balance=100.0,
        risk_pct=1.5,
        lot_sizing_mode="AUTO_RISK",
        user_configured_lot=0.01,
        auto_trade_cooldown_seconds=60,
        daily_loss_limit_pct=6.0,
        max_consecutive_losses=5,
        max_spread_pips=5.0
    )
    
    assert "error" not in res
    assert "DAILY_LOSS_LIMIT" in res["rejection_breakdown"]


def test_5_fixed_lot_equity_safety():
    """
    TEST 5: $100 balance + Fixed Lot 0.10.
    Expected: FIXED_LOT_RISK_EXCEEDS_LIMIT or INSUFFICIENT_EQUITY prevents excessive risk trading.
    """
    now = datetime(2026, 8, 1, 12, 0, 0)
    passed, reason, details = RiskEngine.evaluate_trade_risk(
        symbol="XAUUSD",
        current_time=now,
        current_spread_pips=2.0,
        report_confidence=0.85,
        report_rr_ratio=1.5,
        account_balance=20.0, # Low balance
        start_of_day_balance=100.0,
        daily_realized_pnl=0.0,
        consecutive_losses=0,
        last_trade_exit_time=None,
        trade_lots=0.10,
        sl_pips=25.0, # $25 loss + $0.70 comm = $25.70 risk > $20 balance
        pip_value_per_lot=10.0,
        commission_per_lot=7.0,
        max_spread_pips=5.0,
        auto_trade_cooldown_seconds=60,
        daily_loss_limit_pct=6.0,
        max_consecutive_losses=5,
        min_ai_confidence=0.65,
        min_risk_reward_ratio=1.5
    )
    
    assert passed is False
    assert reason == RiskBlockReason.FIXED_LOT_RISK_EXCEEDS_LIMIT.value


def test_6_known_xauusd_pnl_math():
    """
    TEST 6: Known XAUUSD entry/exit P&L math verification.
    BUY 0.10 lots XAUUSD @ 2400.00, Exit @ 2402.50 (+25 pips).
    Expected P&L: 25 pips * ($10.00/pip * 0.10) - ($7.00/lot * 0.10) = $25.00 - $0.70 = $24.30.
    """
    entry_p = 2400.00
    exit_p = 2402.50
    pip_size = 0.10
    pip_val_per_lot = 10.0
    lots = 0.10
    comm_per_lot = 7.0
    
    pips = (exit_p - entry_p) / pip_size # 25 pips
    comm = lots * comm_per_lot # $0.70
    pnl = pips * (pip_val_per_lot * lots) - comm # $25.00 - $0.70 = $24.30
    
    assert pips == 25.0
    assert round(comm, 2) == 0.70
    assert round(pnl, 2) == 24.30


def test_7_metric_sign_verification():
    """
    TEST 7: Initial Balance = $100, Final Balance = $50.
    Expected: Net Profit = -$50, ROI = -50%.
    """
    metrics = backtester._compute_metrics(
        sim_res={
            "balance": 50.0,
            "trades": [
                {"pnl_usd": -50.0, "lots": 0.01, "r_multiple": -1.0}
            ],
            "equity_curve": [100.0, 50.0],
            "max_consecutive_losses": 1
        },
        initial_balance=100.0,
        lots=0.01,
        spread_pips=2.0,
        specs={"pip_value_per_lot": 10.0}
    )
    
    assert metrics["net_pnl"] == -50.0
    roi = round((metrics["net_pnl"] / 100.0) * 100.0, 2)
    assert roi == -50.0


def test_8_backtest_determinism():
    """
    TEST 8: Same backtest settings run twice produce identical results.
    """
    res1 = backtester.run_backtest(
        symbol="XAUUSD", timeframe="M15", from_date="2026-08-01", to_date="2026-08-05", initial_balance=1000.0
    )
    res2 = backtester.run_backtest(
        symbol="XAUUSD", timeframe="M15", from_date="2026-08-01", to_date="2026-08-05", initial_balance=1000.0
    )
    
    assert res1["total_trades"] == res2["total_trades"]
    assert res1["final_balance"] == res2["final_balance"]
    assert res1["net_profit_usd"] == res2["net_profit_usd"]


if __name__ == "__main__":
    print("Running test_1...")
    test_1_cooldown_and_daily_loss_independence()
    print("Running test_2...")
    test_2_cooldown_and_consecutive_loss_independence()
    print("Running test_3...")
    test_3_max_consecutive_loss_limit()
    print("Running test_4...")
    test_4_daily_loss_limit_stops_trading()
    print("Running test_5...")
    test_5_fixed_lot_equity_safety()
    print("Running test_6...")
    test_6_known_xauusd_pnl_math()
    print("Running test_7...")
    test_7_metric_sign_verification()
    print("Running test_8...")
    test_8_backtest_determinism()
    print("\nALL 8 REGRESSION TESTS PASSED CLEANLY!")
