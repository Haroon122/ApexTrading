import os
import sys
from datetime import datetime, timedelta

# Add backend directory to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.domain.models import Candle
from app.services.backtester import backtester

def generate_synthetic_candles(count=100):
    start_time = datetime(2026, 1, 1, 0, 0)
    candles = []
    base_price = 2000.0
    for i in range(count):
        t = start_time + timedelta(minutes=15 * i)
        # Create fluctuating candle price data
        close_p = base_price + (i % 5) * 1.5 - (i % 3) * 2.0
        high_p = max(base_price, close_p) + 2.0
        low_p = min(base_price, close_p) - 2.0
        candles.append(Candle(
            timestamp=t,
            open=base_price,
            high=high_p,
            low=low_p,
            close=close_p,
            volume=100 + i
        ))
        base_price = close_p
    return candles

def test_fast_metrics():
    print("[TEST] Running fast synthetic backtest metrics calculation...")
    candles = generate_synthetic_candles(150)
    
    res = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M15",
        candles=candles,
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
    
    # 1. Check Equation: winning + losing + break_even == total_trades
    assert winning_trades + losing_trades + break_even_trades == total_trades, \
        f"Equation failed: {winning_trades} + {losing_trades} + {break_even_trades} != {total_trades}"
    print("✓ VERIFIED: winning_trades + losing_trades + break_even_trades == total_trades")
    
    # 2. Check individual counts against trade PnL definitions
    raw_wins = len([t for t in trades if t["pnl_usd"] > 0])
    raw_losses = len([t for t in trades if t["pnl_usd"] < 0])
    raw_be = len([t for t in trades if t["pnl_usd"] == 0])
    
    assert winning_trades == raw_wins, f"Winning trades mismatch: {winning_trades} vs {raw_wins}"
    assert losing_trades == raw_losses, f"Losing trades mismatch: {losing_trades} vs {raw_losses}"
    assert break_even_trades == raw_be, f"Break-even trades mismatch: {break_even_trades} vs {raw_be}"
    print("✓ VERIFIED: Trade counts match raw trade PnL definitions strictly (P&L > 0, P&L < 0, P&L == 0)")
    
    # 3. Check Win Rate formula: Winning Trades / (Winning Trades + Losing Trades) * 100
    decisive_trades = winning_trades + losing_trades
    if decisive_trades > 0:
        expected_win_rate = round((winning_trades / decisive_trades) * 100.0, 1)
    else:
        expected_win_rate = 0.0
        
    assert win_rate_pct == expected_win_rate, f"Win rate mismatch: {win_rate_pct}% vs expected {expected_win_rate}%"
    print(f"✓ VERIFIED: Win Rate ({win_rate_pct}%) == winning_trades / (winning_trades + losing_trades) * 100")
    
    # 4. Verify zero trades edge case
    empty_metrics = backtester._compute_metrics(
        {"balance": 10000.0, "trades": [], "equity_curve": [10000.0], "max_consecutive_losses": 0},
        10000.0, 0.1, 2.5, {"pip_value_per_lot": 10.0}
    )
    assert empty_metrics["total_trades"] == 0
    assert empty_metrics["winning_trades"] == 0
    assert empty_metrics["losing_trades"] == 0
    assert empty_metrics["break_even_trades"] == 0
    assert empty_metrics["win_rate"] == 0.0
    print("✓ VERIFIED: Zero trades edge case handles win_rate=0.0%, winning_trades=0, losing_trades=0, break_even_trades=0 cleanly.")

    print("\n=======================================================")
    print("ALL FAST SYNTHETIC BACKTEST METRICS VERIFICATION TESTS PASSED!")
    print("=======================================================")

if __name__ == "__main__":
    test_fast_metrics()
