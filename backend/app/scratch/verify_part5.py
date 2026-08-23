import sys
import os
import json
import asyncio
import sqlite3
from datetime import datetime, timedelta

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.domain.models import Candle, TickerData, TradeSide, AccountInfo, TradeDecisionReport, OrderType, ConfirmationBreakdown, SavedAccount, BotStatus, TradeState, TradeRecord
from app.services.session_manager import session_manager
from app.services.trade_manager import trade_manager
from app.services.trade_learner import trade_learner
from app.services.news_filter import news_filter, MockCalendarProvider
from app.services.backtester import backtester
from app.adapters.mt5_adapter import mt5_adapter
from app.core.config import settings

# Path to db for cleaning
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "trading_memory.db")

def clean_db():
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS trade_history_memory")
        cursor.execute("DROP TABLE IF EXISTS system_metadata")
        conn.commit()
        conn.close()
        # Re-initialize
        trade_learner._init_db()
    except Exception as e:
        print(f"Error cleaning database: {e}")

async def test_outcome_learning_loop():
    print("\n==================================================")
    print("TESTING ISSUE 21: Outcome Learning Loop & Safeguard Gate")
    print("==================================================")
    
    clean_db()
    
    # 1. Log a pending trade
    trade_id = "test_learn_999"
    trade_learner.log_trade_execution(
        trade_id=trade_id,
        symbol="EURUSD",
        side="BUY",
        timeframe="M15",
        entry_price=1.08500,
        lots=0.10,
        sl_price=1.08300,
        tp_price=1.08900,
        confidence_score=0.85
    )
    
    # Check it is in PENDING state
    with trade_learner._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT win_loss, exit_price, pnl_usd FROM trade_history_memory WHERE trade_id = ?", (trade_id,))
        row = cursor.fetchone()
        print(f"Initial Trade: win_loss={row['win_loss']}, exit_price={row['exit_price']}, pnl_usd={row['pnl_usd']}")
        assert row["win_loss"] == "PENDING"
        
    # 2. Simulate outcomes to trigger learning updates
    # We will log 10 wins and 10 losses to verify threshold optimization calculations
    print("Simulating 15 wins and 2 losses to test relaxation threshold shift...")
    for idx in range(15):
        tid = f"win_{idx}"
        trade_learner.log_trade_execution(tid, "EURUSD", "BUY", "M15", 1.08500, 0.1, 1.0830, 1.0890, confidence_score=0.85)
        trade_learner.update_trade_outcome(tid, 1.08900, 60.0, 40.0) # Win
        
    for idx in range(2):
        tid = f"loss_{idx}"
        trade_learner.log_trade_execution(tid, "EURUSD", "BUY", "M15", 1.08500, 0.1, 1.0830, 1.0890, confidence_score=0.85)
        trade_learner.update_trade_outcome(tid, 1.08300, -20.0, -20.0) # Loss

    # 3. Check optimized thresholds
    opt = trade_learner.get_optimized_thresholds()
    print(f"Optimized Thresholds output: {opt}")
    # High performance mode (win rate > 70%) should relax threshold to 0.65
    assert opt["min_confidence"] == 0.65
    
    # 4. Check Safeguard: Propose thresholds and verify they are NOT applied automatically
    proposed = trade_learner.propose_new_thresholds()
    effective = trade_learner.get_effective_thresholds()
    print(f"Proposed: {proposed}")
    print(f"Effective: {effective}")
    
    # Effective should still be default because proposed is not yet approved
    assert effective["min_confidence"] == 0.70
    print("Verification: Proposed thresholds were NOT applied automatically (Safeguard OK).")
    
    # 5. Approve proposed thresholds via endpoints simulation
    from app.api.v1.endpoints import approve_proposed_learning_thresholds, get_proposed_learning_thresholds
    
    # Simulate API approval
    res = approve_proposed_learning_thresholds()
    print(f"Approval response: {res}")
    
    effective_after = trade_learner.get_effective_thresholds()
    print(f"Effective after approval: {effective_after}")
    assert effective_after["min_confidence"] == 0.65
    print("Verification: Proposed thresholds successfully applied AFTER explicit approval.")
    print(" -> Issue 21 checks passed successfully.")

async def test_news_calendar_failsafe():
    print("\n==================================================")
    print("TESTING ISSUE 22: Economic Calendar Fail-Safe")
    print("==================================================")
    
    # 1. Setup normal calendar and verify no news blackout on EURUSD on a Sunday
    sunday = datetime(2026, 8, 9, 12, 0, 0) # Sunday
    is_blackout, msg = news_filter.is_news_blackout("EURUSD", current_time=sunday)
    print(f"Normal Calendar check on Sunday: is_blackout={is_blackout}, message='{msg}'")
    assert is_blackout == False
    
    # 2. Test blackout zone on Wednesday NFP/CPI
    wednesday_cpi = datetime(2026, 8, 12, 13, 35, 0) # Wednesday 13:35 CPI
    is_blackout2, msg2 = news_filter.is_news_blackout("EURUSD", current_time=wednesday_cpi)
    print(f"Normal Calendar check during news window: is_blackout={is_blackout2}, message='{msg2}'")
    assert is_blackout2 == True
    
    # 3. Simulate news calendar server failure
    news_filter.provider = MockCalendarProvider(simulate_failure=True)
    is_blackout_fail, msg_fail = news_filter.is_news_blackout("EURUSD", current_time=sunday)
    print(f"Calendar Failure check: is_blackout={is_blackout_fail}, message='{msg_fail}'")
    
    # Since block_on_unavailable = True, it should block trades with NEWS_DATA_UNAVAILABLE!
    assert is_blackout_fail == True
    assert msg_fail == "NEWS_DATA_UNAVAILABLE"
    
    # Reset
    news_filter.provider = MockCalendarProvider(simulate_failure=False)
    print(" -> Issue 22 checks passed successfully.")

async def test_segmented_backtest_and_stress():
    print("\n==================================================")
    print("TESTING ISSUES 23 & 24: Segmented Backtest & Robustness Checks")
    print("==================================================")
    
    # Mock candles (100 candles)
    base_time = datetime.now() - timedelta(days=10)
    mock_candles = []
    for idx in range(100):
        mock_candles.append(Candle(
            timestamp=base_time + timedelta(minutes=15 * idx),
            open=1.08500 + idx * 0.00002,
            high=1.08550 + idx * 0.00002,
            low=1.08480 + idx * 0.00002,
            close=1.08520 + idx * 0.00002,
            volume=100 + idx,
            is_closed=True
        ))
        
    res = backtester.run_backtest(
        symbol="EURUSD",
        timeframe="M15",
        candles=mock_candles,
        initial_balance=10000.0,
        risk_pct=1.0,
        lots=0.10,
        spread_pips=0.5
    )
    
    # 1. Verify split segments exist in output
    assert "segmented_reports" in res
    print("In-Sample Report:      ", res["segmented_reports"]["in_sample_60"])
    print("Validation Report:     ", res["segmented_reports"]["validation_20"])
    print("Out-of-Sample Report:  ", res["segmented_reports"]["out_of_sample_20"])
    
    # 2. Verify Sharpe/Sortino is hidden due to low trades count
    assert res["segmented_reports"]["in_sample_60"]["sharpe_ratio"] == "insufficient trades for Sharpe/Sortino"
    assert res["segmented_reports"]["in_sample_60"]["sortino_ratio"] == "insufficient trades for Sharpe/Sortino"
    print("Verification: Sharpe/Sortino ratios are hidden when trade count is low.")
    
    # 3. Verify Stress Test Robustness Metrics
    assert "robustness_stress_test" in res
    stress = res["robustness_stress_test"]
    print("Robustness Stress Test Report: ", stress)
    assert "avg_profit_factor" in stress
    assert "avg_drawdown" in stress
    print(" -> Issues 23 & 24 checks passed successfully.")

async def test_regression_guardrails():
    print("\n==================================================")
    print("TESTING ISSUE 25: Regression Guardrails")
    print("==================================================")
    
    # 1. Call _fetch_htf_trends with is_backtest=True and no htf_candles. It should trigger AssertionError!
    from app.services.decision_engine import decision_engine
    try:
        decision_engine._fetch_htf_trends("EURUSD", htf_candles=None, is_backtest=True)
        assert False, "Regression guardrail failed: Allowed live MT5 queries during backtests!"
    except AssertionError as e:
        print(f"Guardrail triggered successfully: {e}")
        assert "REGRESSION GUARDRAIL" in str(e)
        
    print(" -> Issue 25 checks passed successfully.")

if __name__ == "__main__":
    asyncio.run(test_outcome_learning_loop())
    asyncio.run(test_news_calendar_failsafe())
    asyncio.run(test_segmented_backtest_and_stress())
    asyncio.run(test_regression_guardrails())
    print("\n==================================================")
    print("ALL PART 5 FIXES VERIFIED AND WORKING SUCCESSFULLY!")
    print("==================================================")
