import os
import sys

# Add backend directory to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.backtester import backtester

def test_backtest_metrics_calculation():
    print("[TEST] Running backtest metrics calculation verification...")
    
    # 1. Run standard backtest
    res = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M15",
        count=500,
        initial_balance=10000.0,
        risk_pct=1.0,
        lot_sizing_mode="FIXED",
        user_configured_lot=0.10
    )
    
    if "error" in res:
        print(f"FAILED: Backtest error: {res['error']}")
        sys.exit(1)
        
    total_trades = res["total_trades"]
    winning_trades = res["winning_trades"]
    losing_trades = res["losing_trades"]
    break_even_trades = res["break_even_trades"]
    win_rate_pct = res["win_rate_pct"]
    trades = res["trades"]
    
    print(f"Results Summary:")
    print(f"  Total Trades: {total_trades}")
    print(f"  Winning Trades: {winning_trades}")
    print(f"  Losing Trades: {losing_trades}")
    print(f"  Break-Even Trades: {break_even_trades}")
    print(f"  Win Rate: {win_rate_pct}%")
    
    # Validate equation 1: winning + losing + break_even = total_trades
    assert winning_trades + losing_trades + break_even_trades == total_trades, \
        f"Equation failed: {winning_trades} + {losing_trades} + {break_even_trades} != {total_trades}"
    print("[OK] VERIFIED: winning_trades + losing_trades + break_even_trades == total_trades")
    
    # Validate trade counts against raw trades list
    raw_wins = len([t for t in trades if t["pnl_usd"] > 0])
    raw_losses = len([t for t in trades if t["pnl_usd"] < 0])
    raw_be = len([t for t in trades if t["pnl_usd"] == 0])
    
    assert winning_trades == raw_wins, f"Winning trades mismatch: {winning_trades} vs {raw_wins}"
    assert losing_trades == raw_losses, f"Losing trades mismatch: {losing_trades} vs {raw_losses}"
    assert break_even_trades == raw_be, f"Break-even trades mismatch: {break_even_trades} vs {raw_be}"
    print("[OK] VERIFIED: Trade counts match raw executed trade definitions strictly (pnl > 0, pnl < 0, pnl == 0)")
    
    # Validate equation 2: win_rate = winning / (winning + losing) * 100
    decisive_trades = winning_trades + losing_trades
    if decisive_trades > 0:
        expected_win_rate = round((winning_trades / decisive_trades) * 100.0, 1)
    else:
        expected_win_rate = 0.0
        
    assert win_rate_pct == expected_win_rate, f"Win rate mismatch: {win_rate_pct}% vs expected {expected_win_rate}%"
    print(f"[OK] VERIFIED: win_rate ({win_rate_pct}%) == winning_trades / (winning_trades + losing_trades) * 100")
    
    # 2. Verify dynamic updates with different parameters
    print("\n[TEST] Testing dynamic calculation across parameter changes...")
    res2 = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M15",
        count=300,
        initial_balance=5000.0,
        risk_pct=2.0,
        lot_sizing_mode="AUTO_RISK",
        user_configured_lot=0.20,
        auto_trade_cooldown_seconds=600
    )
    
    total2 = res2["total_trades"]
    wins2 = res2["winning_trades"]
    losses2 = res2["losing_trades"]
    be2 = res2["break_even_trades"]
    wr2 = res2["win_rate_pct"]
    
    assert wins2 + losses2 + be2 == total2, "Equation 1 failed on parameter change!"
    dec2 = wins2 + losses2
    exp_wr2 = round((wins2 / dec2) * 100.0, 1) if dec2 > 0 else 0.0
    assert wr2 == exp_wr2, "Win rate formula failed on parameter change!"
    print("[OK] VERIFIED: Parameters changed dynamically and metrics remained perfectly accurate.")

    # 3. Verify zero trades edge case
    print("\n[TEST] Testing zero trades edge case...")
    empty_metrics = backtester._compute_metrics(
        {"balance": 10000.0, "trades": [], "equity_curve": [10000.0], "max_consecutive_losses": 0},
        10000.0, 0.1, 2.5, {"pip_value_per_lot": 10.0}
    )
    assert empty_metrics["total_trades"] == 0
    assert empty_metrics["winning_trades"] == 0
    assert empty_metrics["losing_trades"] == 0
    assert empty_metrics["break_even_trades"] == 0
    assert empty_metrics["win_rate"] == 0.0
    print("[OK] VERIFIED: Zero trades edge case handles win_rate=0%, winning_trades=0, losing_trades=0, break_even_trades=0 cleanly.")

    print("\n=======================================================")
    print("ALL BACKTEST METRICS VERIFICATION TESTS PASSED CLEANLY!")
    print("=======================================================")

if __name__ == "__main__":
    test_backtest_metrics_calculation()
