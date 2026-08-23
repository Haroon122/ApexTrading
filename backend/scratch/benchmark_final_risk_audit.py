import os
import sys
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.backtester import backtester

def run_benchmark():
    print("======================================================")
    print("RUNNING FINAL BACKTEST BENCHMARK WITH RISK FIXES")
    print("======================================================")
    
    t0 = time.time()
    res = backtester.run_backtest(
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
    t_elapsed = time.time() - t0
    
    if "error" in res:
        print(f"ERROR: {res['error']}")
        return
        
    rejection_breakdown = res.get("rejection_breakdown", {})
    
    print(f"\n[BENCHMARK RESULT SUMMARY - {t_elapsed:.2f}s]")
    print(f"Total Signals (Opportunities): {res.get('total_opportunities', 0)}")
    print(f"Executed Trades:               {res.get('total_trades', 0)}")
    print(f"Winning Trades:                {res.get('winning_trades', 0)}")
    print(f"Losing Trades:                 {res.get('losing_trades', 0)}")
    print(f"Break-Even Trades:             {res.get('break_even_trades', 0)}")
    print(f"Cooldown Blocks:               {rejection_breakdown.get('COOLDOWN', 0)}")
    print(f"Daily Loss Blocks:             {rejection_breakdown.get('DAILY_LOSS_LIMIT', 0)}")
    print(f"Consecutive Loss Blocks:       {rejection_breakdown.get('CONSECUTIVE_LOSS_LIMIT', 0)}")
    print(f"Fixed Lot Risk Safety Blocks:  {rejection_breakdown.get('FIXED_LOT_RISK_EXCEEDS_LIMIT', 0)}")
    print(f"Insufficient Equity Blocks:    {rejection_breakdown.get('INSUFFICIENT_EQUITY', 0)}")
    print(f"Account Bankrupt Blocks:       {rejection_breakdown.get('ACCOUNT_BANKRUPT', 0)}")
    print(f"Other Blocks:                  {res.get('blocked_count', 0)}")
    print(f"Win Rate:                      {res.get('win_rate_pct', 0)}%")
    print(f"Profit Factor:                 {res.get('profit_factor', 0)}")
    print(f"Net P&L (USD):                 ${res.get('net_profit_usd', 0)}")
    print(f"Final Balance:                 ${res.get('final_balance', 0)}")
    print(f"ROI:                           {res.get('net_roi_pct', 0)}%")
    print(f"Max Drawdown:                  {res.get('max_drawdown_pct', 0)}%")
    print(f"Max Consecutive Loss:          {res.get('max_consecutive_losses', 0)}")

if __name__ == "__main__":
    run_benchmark()
