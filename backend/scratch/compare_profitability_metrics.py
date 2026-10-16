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

def run_comparison():
    print("==================================================================", flush=True)
    print(" APEX TRADING BOT - PROFITABILITY BACKTEST: BEFORE VS AFTER FIXES", flush=True)
    print("==================================================================", flush=True)

    symbol = "XAUUSD"
    timeframe = "M15"
    candle_count = 350

    print(f"[1] Fetching {candle_count} candles for {symbol} ({timeframe})...", flush=True)
    candles = mt5_adapter.fetch_candles(symbol, timeframe=timeframe, count=candle_count)
    print(f"    Loaded {len(candles)} candles. ({candles[0].timestamp} to {candles[-1].timestamp})", flush=True)

    print("[2] Precomputing technical indicators for rapid evaluation...", flush=True)
    t_ind = time.time()
    precomputed_df = IndicatorService.calculate_all_indicators(candles)
    print(f"    Indicators precalculated in {time.time() - t_ind:.2f}s", flush=True)

    # -------------------------------------------------------------------------
    # TEST 1: BEFORE CHANGES (OLD STRATEGY)
    # Toxic setups enabled: BB Midline, BOS Breakout, Momentum Expansion
    # HTF Filter: OFF
    # Giveback Guard: ON (Early profit cutoff)
    # Breakeven: OFF
    # -------------------------------------------------------------------------
    print("\n" + "=" * 65, flush=True)
    print(">>> 1. RUNNING BACKTEST: BEFORE CHANGES (OLD BOT)", flush=True)
    print("    - All Setups Active (BB Midline Pullback, BOS, Momentum)", flush=True)
    print("    - HTF Alignment Filter: OFF", flush=True)
    print("    - Giveback Guard: ENABLED (chokes winners early)", flush=True)
    print("    - Auto-Breakeven: DISABLED", flush=True)
    print("=" * 65, flush=True)

    t0 = time.time()
    res_before = BacktestingEngine.run_backtest(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        initial_balance=10000.0,
        risk_pct=1.0,
        lot_sizing_mode="FIXED",
        user_configured_lot=0.10,
        break_even_rr=999.0, # disabled
        trailing_activation_rr=999.0, # disabled
        giveback_guard_enabled=True,
        giveback_guard_min_rr=0.3,
        giveback_guard_retrace_pct=30.0,
        htf_alignment_filter_enabled=False,
        htf_alignment_mode="OFF",
        disabled_setups=[], # All setups allowed
        precomputed_full_df=precomputed_df,
        skip_stress=True
    )
    t_before = time.time() - t0
    print(f"    Backtest 1 completed in {t_before:.2f}s", flush=True)

    # -------------------------------------------------------------------------
    # TEST 2: AFTER CHANGES (CURRENT UPGRADED BOT)
    # Toxic setups eliminated
    # HTF Filter: STRICT
    # Giveback Guard: DISABLED
    # Auto-Breakeven: ENABLED @ 1.0R
    # Trailing Stop: ENABLED @ 1.2R
    # -------------------------------------------------------------------------
    print("\n" + "=" * 65, flush=True)
    print(">>> 2. RUNNING BACKTEST: AFTER CHANGES (CURRENT UPGRADED BOT)", flush=True)
    print("    - Toxic Setups Eliminated (BB Midline, BOS, Momentum)", flush=True)
    print("    - Strict HTF Trend Alignment: ENABLED", flush=True)
    print("    - Giveback Guard: DISABLED", flush=True)
    print("    - Auto-Breakeven: ENABLED @ 1.0R (Risk-Free once +1R hit)", flush=True)
    print("    - Auto-Trailing Stop: ENABLED @ 1.2R (Let winners run)", flush=True)
    print("=" * 65, flush=True)

    t1 = time.time()
    disabled = trade_manager.risk_settings.disabled_setups
    res_after = BacktestingEngine.run_backtest(
        symbol=symbol,
        timeframe=timeframe,
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
        disabled_setups=disabled,
        precomputed_full_df=precomputed_df,
        skip_stress=True
    )
    t_after = time.time() - t1
    print(f"    Backtest 2 completed in {t_after:.2f}s", flush=True)

    # -------------------------------------------------------------------------
    # HEAD-TO-HEAD COMPARISON TABLE
    # -------------------------------------------------------------------------
    print("\n" + "=" * 72, flush=True)
    print("               HEAD-TO-HEAD PROFITABILITY RESULTS               ", flush=True)
    print("=" * 72, flush=True)

    metrics = [
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
        ("Initial Balance", "initial_balance", "${:,.2f}"),
        ("Final Balance", "final_balance", "${:,.2f}")
    ]

    print(f"{'Performance Metric':<28} | {'BEFORE (Old Bot)':<18} | {'AFTER (New Bot)':<18}", flush=True)
    print("-" * 72, flush=True)

    for label, key, fmt in metrics:
        b_val = res_before.get(key, 0)
        a_val = res_after.get(key, 0)
        b_str = fmt.format(b_val)
        a_str = fmt.format(a_val)
        print(f"{label:<28} | {b_str:<18} | {a_str:<18}", flush=True)

    print("=" * 72, flush=True)

    # Detailed Per-Setup Performance Comparison
    def setup_breakdown(trades):
        stats = {}
        for t in trades:
            s = t.get("setup_name") or "Unknown"
            if s not in stats:
                stats[s] = {"count": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            stats[s]["count"] += 1
            pnl = t.get("pnl_usd", 0.0)
            stats[s]["pnl"] += pnl
            if pnl > 0:
                stats[s]["wins"] += 1
            elif pnl < 0:
                stats[s]["losses"] += 1
        return stats

    sb_before = setup_breakdown(res_before.get("trades", []))
    sb_after = setup_breakdown(res_after.get("trades", []))

    print("\n--- PER-SETUP PERFORMANCE (BEFORE vs AFTER) ---", flush=True)
    all_setups = set(list(sb_before.keys()) + list(sb_after.keys()))
    for s in sorted(all_setups):
        b = sb_before.get(s, {"count": 0, "wins": 0, "losses": 0, "pnl": 0.0})
        a = sb_after.get(s, {"count": 0, "wins": 0, "losses": 0, "pnl": 0.0})
        b_wr = (b["wins"] / b["count"] * 100) if b["count"] else 0.0
        a_wr = (a["wins"] / a["count"] * 100) if a["count"] else 0.0
        print(f"Setup: {s[:42]:<42}")
        print(f"  BEFORE: {b['count']:2d} trades | WinRate: {b_wr:5.1f}% | Net PnL: ${b['pnl']:+8.2f}")
        print(f"  AFTER:  {a['count']:2d} trades | WinRate: {a_wr:5.1f}% | Net PnL: ${a['pnl']:+8.2f}")
        print("-" * 65)

    # Save to JSON artifact
    result_data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_count": len(candles),
        "period_start": candles[0].timestamp.isoformat(),
        "period_end": candles[-1].timestamp.isoformat(),
        "before": {k: res_before.get(k) for k in ["total_trades", "winning_trades", "losing_trades", "break_even_trades", "win_rate_pct", "net_profit_usd", "net_roi_pct", "profit_factor", "max_drawdown_pct", "max_consecutive_losses", "final_balance"]},
        "after": {k: res_after.get(k) for k in ["total_trades", "winning_trades", "losing_trades", "break_even_trades", "win_rate_pct", "net_profit_usd", "net_roi_pct", "profit_factor", "max_drawdown_pct", "max_consecutive_losses", "final_balance"]}
    }

    out_file = backend_dir / "scratch" / "profitability_comparison_summary.json"
    with open(out_file, "w") as f:
        json.dump(result_data, f, indent=2)
    print(f"\n[OK] Summary successfully written to {out_file.name}", flush=True)

if __name__ == "__main__":
    run_comparison()
