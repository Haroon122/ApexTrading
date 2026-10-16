import sys
import os
import json
import time
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.services.backtester import BacktestingEngine
from app.adapters.mt5_adapter import mt5_adapter
from app.services.trade_manager import trade_manager
from app.services.indicators import IndicatorService

def run_deep_audit():
    print("==================================================================", flush=True)
    print("      DEEP HISTORICAL PROFITABILITY AUDIT (M15 & M5 SWEEP)        ", flush=True)
    print("==================================================================", flush=True)

    symbol = "XAUUSD"
    
    # We will test two key operational timeframes: M15 (Primary Swing) and M5 (Scalp)
    tests = [
        ("M15", 1000),
        ("M5", 1200)
    ]

    all_results = {}

    for tf, count in tests:
        print(f"\n" + "#" * 65, flush=True)
        print(f"### TIMEFRAME: {tf} | DATASET: {count} CANDLES", flush=True)
        print("#" * 65, flush=True)

        candles = mt5_adapter.fetch_candles(symbol, timeframe=tf, count=count)
        if not candles or len(candles) < 100:
            print(f"Error loading candles for {tf}", flush=True)
            continue

        print(f"Candles span: {candles[0].timestamp} to {candles[-1].timestamp}", flush=True)
        t_pre = time.time()
        precomputed_df = IndicatorService.calculate_all_indicators(candles)
        print(f"Indicators precomputed in {time.time() - t_pre:.2f}s", flush=True)

        # 1. OLD BOT (BASELINE)
        print("\n--- Running OLD STRATEGY (Baseline) ---", flush=True)
        t0 = time.time()
        res_old = BacktestingEngine.run_backtest(
            symbol=symbol,
            timeframe=tf,
            candles=candles,
            initial_balance=10000.0,
            risk_pct=1.0,
            lot_sizing_mode="FIXED",
            user_configured_lot=0.10,
            break_even_rr=999.0,
            trailing_activation_rr=999.0,
            giveback_guard_enabled=True,
            giveback_guard_min_rr=0.3,
            giveback_guard_retrace_pct=30.0,
            htf_alignment_filter_enabled=False,
            htf_alignment_mode="OFF",
            disabled_setups=[],
            precomputed_full_df=precomputed_df,
            skip_stress=True
        )
        t_old = time.time() - t0
        print(f"Completed in {t_old:.2f}s | Trades: {res_old.get('total_trades', 0)} | Net PnL: ${res_old.get('net_profit_usd', 0):+,.2f}", flush=True)

        # 2. UPGRADED BOT (AFTER CHANGES)
        print("\n--- Running UPGRADED STRATEGY (After Changes) ---", flush=True)
        t1 = time.time()
        res_new = BacktestingEngine.run_backtest(
            symbol=symbol,
            timeframe=tf,
            candles=candles,
            initial_balance=10000.0,
            risk_pct=1.0,
            lot_sizing_mode="FIXED",
            user_configured_lot=0.10,
            break_even_rr=1.0,
            trailing_activation_rr=1.2,
            giveback_guard_enabled=False,
            htf_alignment_filter_enabled=True,
            htf_alignment_mode="STRICT",
            disabled_setups=trade_manager.risk_settings.disabled_setups,
            precomputed_full_df=precomputed_df,
            skip_stress=True
        )
        t_new = time.time() - t1
        print(f"Completed in {t_new:.2f}s | Trades: {res_new.get('total_trades', 0)} | Net PnL: ${res_new.get('net_profit_usd', 0):+,.2f}", flush=True)

        # COMPARISON TABLE
        print("\n" + "=" * 68, flush=True)
        print(f"    RESULTS SUMMARY: {symbol} ({tf} - {count} Candles)", flush=True)
        print("=" * 68, flush=True)
        hdr = f"{'Metric':<26} | {'BEFORE (Old)':<17} | {'AFTER (New Upgraded)':<17}"
        print(hdr, flush=True)
        print("-" * 68, flush=True)

        keys = [
            ("Total Trades", "total_trades", "{:d}"),
            ("Winning Trades", "winning_trades", "{:d}"),
            ("Losing Trades", "losing_trades", "{:d}"),
            ("Break-Even Trades", "break_even_trades", "{:d}"),
            ("Win Rate (%)", "win_rate_pct", "{:.1f}%"),
            ("Net Profit (USD)", "net_profit_usd", "${:+,.2f}"),
            ("Net ROI (%)", "net_roi_pct", "{:+.2f}%"),
            ("Profit Factor", "profit_factor", "{:.2f}"),
            ("Max Drawdown (%)", "max_drawdown_pct", "{:.2f}%"),
            ("Max Consec Losses", "max_consecutive_losses", "{:d}"),
            ("Final Balance", "final_balance", "${:,.2f}")
        ]

        for label, k, fmt in keys:
            v_old = res_old.get(k, 0)
            v_new = res_new.get(k, 0)
            print(f"{label:<26} | {fmt.format(v_old):<17} | {fmt.format(v_new):<17}", flush=True)
        print("=" * 68, flush=True)

        all_results[tf] = {
            "before": {k: res_old.get(k) for k in ["total_trades", "winning_trades", "losing_trades", "win_rate_pct", "net_profit_usd", "net_roi_pct", "profit_factor", "max_drawdown_pct", "final_balance"]},
            "after": {k: res_new.get(k) for k in ["total_trades", "winning_trades", "losing_trades", "win_rate_pct", "net_profit_usd", "net_roi_pct", "profit_factor", "max_drawdown_pct", "final_balance"]}
        }

    with open(backend_dir / "scratch" / "deep_audit_results.json", "w") as f:
        json.dump(all_results, f, indent=2)

if __name__ == "__main__":
    run_deep_audit()
