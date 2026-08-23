import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.backtester import backtester

def run_benchmark():
    print("======================================================")
    print("[BENCHMARK] Testing Date Range Backtest Performance")
    print("======================================================")

    # TEST A: No dates specified (default 500 candles M15)
    t0 = time.time()
    res_a = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M15",
        count=500
    )
    t_a = time.time() - t0
    if "error" in res_a:
        print(f"TEST A ERROR: {res_a['error']}")
    else:
        print(f"TEST A (No dates, M15 500 candles): {t_a:.2f}s | Trades: {res_a['total_trades']} | Balance: ${res_a['final_balance']}")

    # TEST B: Date range specified (M1 2026-08-01 to 2026-08-14)
    t1 = time.time()
    res_b = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M1",
        from_date="2026-08-01",
        to_date="2026-08-14",
        initial_balance=100.0,
        risk_pct=1.5,
        lot_sizing_mode="FIXED",
        user_configured_lot=0.10,
        auto_trade_cooldown_seconds=60,
        daily_loss_limit_pct=6.0,
        max_consecutive_losses=5,
        max_spread_pips=5.0
    )
    t_b = time.time() - t1
    if "error" in res_b:
        print(f"TEST B ERROR: {res_b['error']}")
    else:
        print(f"TEST B (Date range M1 2026-08-01 -> 2026-08-14): {t_b:.2f}s | Trades: {res_b['total_trades']} | Balance: ${res_b['final_balance']}")

if __name__ == "__main__":
    run_benchmark()
