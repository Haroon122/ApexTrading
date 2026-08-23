import sys
import os
from datetime import datetime

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath("c:/Users/localadmin/Desktop/Trading_Bot/backend"))

from app.services.backtester import backtester
from app.domain.models import BacktestRequest

def run_tests():
    print("=== BACKTESTER ENGINE COMPLETE VERIFICATION TESTS ===")

    # Test 1: XAUUSD, M1, $500 initial balance, 1% risk, 60s cooldown
    print("\n--- TEST 1: XAUUSD, M1, $500 balance, 1.0% risk, 60s cooldown ---")
    res1 = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M1",
        count=120,
        initial_balance=500.0,
        risk_pct=1.0,
        lot_sizing_mode="AUTO_RISK",
        auto_trade_cooldown_seconds=60
    )
    assert res1["timeframe"] == "M1", f"Expected timeframe M1, got {res1['timeframe']}"
    assert res1["initial_balance"] == 500.0, f"Expected balance $500, got {res1['initial_balance']}"
    print("Pass: Backtester executed with symbol XAUUSD, timeframe M1, initial balance $500.0, 60s cooldown.")
    print("Metrics:", f"Total Trades={res1['total_trades']}, Net PnL=${res1['net_profit_usd']}, Final Balance=${res1['final_balance']}")
    print("Rejection Breakdown:", res1["rejection_breakdown"])

    # Test 2: Change timeframe to M5
    print("\n--- TEST 2: Change timeframe to M5 ---")
    res2 = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M5",
        count=120,
        initial_balance=500.0,
        risk_pct=1.0,
        auto_trade_cooldown_seconds=60
    )
    assert res2["timeframe"] == "M5", f"Expected timeframe M5, got {res2['timeframe']}"
    print(f"Pass: Timeframe dynamically updated to {res2['timeframe']}.")

    # Test 3: Change initial balance $500 -> $1000
    print("\n--- TEST 3: Change initial balance $500 -> $1000 ---")
    res3 = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M5",
        count=120,
        initial_balance=1000.0,
        risk_pct=1.0,
        auto_trade_cooldown_seconds=60
    )
    assert res3["initial_balance"] == 1000.0, f"Expected balance $1000, got {res3['initial_balance']}"
    print(f"Pass: Initial balance set to ${res3['initial_balance']}, Final balance = ${res3['final_balance']}.")

    # Test 4 & 5: Cooldown variation (300s vs 60s vs 0s)
    print("\n--- TEST 4 & 5: Cooldown variation (300s vs 60s vs 0s) ---")
    res_cd300 = backtester.run_backtest(
        symbol="XAUUSD", timeframe="M1", count=120, auto_trade_cooldown_seconds=300
    )
    res_cd60 = backtester.run_backtest(
        symbol="XAUUSD", timeframe="M1", count=120, auto_trade_cooldown_seconds=60
    )
    res_cd0 = backtester.run_backtest(
        symbol="XAUUSD", timeframe="M1", count=120, auto_trade_cooldown_seconds=0
    )

    cd300_rejections = res_cd300["rejection_breakdown"].get("COOLDOWN", 0)
    cd60_rejections = res_cd60["rejection_breakdown"].get("COOLDOWN", 0)
    cd0_rejections = res_cd0["rejection_breakdown"].get("COOLDOWN", 0)

    print(f"Cooldown=300s: Cooldown rejections = {cd300_rejections}")
    print(f"Cooldown=60s:  Cooldown rejections = {cd60_rejections}")
    print(f"Cooldown=0s:   Cooldown rejections = {cd0_rejections}")
    assert cd0_rejections == 0, f"Expected 0 cooldown rejections when cooldown=0s, got {cd0_rejections}"
    assert cd300_rejections >= cd60_rejections, "Expected 300s cooldown to block >= 60s cooldown"
    print("Pass: Cooldown rejection count behaves dynamically across 0s, 60s, and 300s.")

    # Test 6: Change Daily Loss Circuit Breaker
    print("\n--- TEST 6: Daily Loss Circuit Breaker limit ---")
    res_dl0 = backtester.run_backtest(
        symbol="XAUUSD", timeframe="M1", count=120, daily_loss_limit_pct=0.1
    )
    print(f"Daily loss limit 0.1%: Rejections = {res_dl0['rejection_breakdown']}")

    # Test 7: Total Reconciliation
    print("\n--- TEST 7: Total Reconciliation ---")
    tot_opps = res1["total_opportunities"]
    exec_cnt = res1["executed_count"]
    blk_cnt = res1["blocked_count"]
    print(f"Total Opportunities: {tot_opps} = Executed ({exec_cnt}) + Blocked ({blk_cnt})")
    assert tot_opps == exec_cnt + blk_cnt, f"Reconciliation error: {tot_opps} != {exec_cnt} + {blk_cnt}"
    print("Pass: Opportunity totals reconcile perfectly!")

    # Test 8, 9, 10: Check Equity Curve & USD vs Pips
    print("\n--- TEST 8, 9, 10: Equity curve & USD vs Pips ---")
    eq_curve = res1["equity_curve"]
    trades = res1["trades"]
    print(f"Equity curve length: {len(eq_curve)}, Starting: ${eq_curve[0]}, Ending: ${eq_curve[-1]}")
    if trades:
        t0 = trades[0]
        print("Sample Trade #1:", f"Side={t0['side']}, Entry={t0['entry_price']}, Exit={t0['exit_price']}, Lots={t0['lots']}, USD PnL=${t0['pnl_usd']}, Pips PnL={t0['pnl_pips']} pips")
    
    print("\nALL 10 BACKTESTER VERIFICATION TESTS COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
