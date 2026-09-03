"""
Progressive Setup Pruning Sweep Under Strict HTF Alignment
==========================================================
Backtests progressive removal of worst-performing setups under Strict HTF (H1 + M15) Alignment
on the 24,500 M1 candle XAUUSD dataset (~17 days of data) with validated exit defaults.
"""

import sys
import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")

from app.services.backtester import BacktestingEngine

SYMBOL      = "XAUUSD"
TIMEFRAME   = "M1"
COUNT       = 24_500        # ~17 days / 2.5 weeks of M1 candles
BALANCE     = 10_000.0
RISK_PCT    = 1.0
LOT_SIZE    = 0.05
RR_RATIO    = 2.0
COOLDOWN    = 30
DAILY_LOSS  = 3.0
MAX_LOSSES  = 20
SPREAD_PIPS = 2.5
CONFIDENCE  = 0.70

def compute_per_setup_breakdown(trades: list) -> dict:
    setup_groups = {}
    for t in trades:
        name = t.get("setup_name", "Unknown")
        if name not in setup_groups:
            setup_groups[name] = []
        setup_groups[name].append(t)

    breakdown = {}
    for name, group in setup_groups.items():
        total = len(group)
        wins = len([t for t in group if t["pnl_usd"] > 0])
        losses = len([t for t in group if t["pnl_usd"] < 0])
        decisive = wins + losses
        win_rate = round(wins / decisive * 100, 1) if decisive else 0.0

        win_pnls  = [t["pnl_usd"] for t in group if t["pnl_usd"] > 0]
        loss_pnls = [t["pnl_usd"] for t in group if t["pnl_usd"] < 0]
        avg_win  = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0.0
        avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0
        net_pnl  = round(sum(t["pnl_usd"] for t in group), 2)
        expectancy = round(net_pnl / total, 2) if total else 0.0

        breakdown[name] = {
            "total": total, "win_rate": win_rate, "net_pnl": net_pnl,
            "avg_win": avg_win, "avg_loss": avg_loss, "expectancy": expectancy
        }
    return breakdown


def run_pruning_scenario(step_num: int, label: str, disabled_setups: list, candles: list, precomputed_df: any = None, cached_reports: any = None) -> tuple:
    print(f"  Running Step {step_num}: {label} (Disabled: {len(disabled_setups)}) ...", flush=True)
    result = BacktestingEngine.run_backtest(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        count=COUNT,
        initial_balance=BALANCE,
        risk_pct=RISK_PCT,
        lot_sizing_mode="FIXED",
        user_configured_lot=LOT_SIZE,
        min_risk_reward_ratio=RR_RATIO,
        auto_trade_cooldown_seconds=COOLDOWN,
        daily_loss_limit_pct=DAILY_LOSS,
        max_consecutive_losses=MAX_LOSSES,
        max_spread_pips=SPREAD_PIPS,
        min_ai_confidence=CONFIDENCE,
        skip_stress=True,
        candles=candles,
        cached_reports=cached_reports,
        precomputed_full_df=precomputed_df,
        break_even_rr=0.5,
        trailing_activation_rr=0.6,
        giveback_guard_enabled=True,
        giveback_guard_min_rr=0.2,
        giveback_guard_retrace_pct=30.0,
        htf_alignment_filter_enabled=True,
        htf_alignment_mode="STRICT",
        disabled_setups=disabled_setups
    )
    if "error" in result:
        print(f"    ERROR: {result['error']}")
        return {"step": step_num, "label": label, "error": result["error"]}, None, None

    trades = result.get("trades", [])
    total = result.get("total_trades", 0)
    eval_reports = result.get("eval_reports", {})
    cached_df = result.get("precomputed_full_df", None)

    wins = len([t for t in trades if t["pnl_usd"] > 0])
    decisive = wins + len([t for t in trades if t["pnl_usd"] < 0])
    win_rate = round(wins / decisive * 100, 1) if decisive else 0.0

    win_pnls  = [t["pnl_usd"] for t in trades if t["pnl_usd"] > 0]
    loss_pnls = [t["pnl_usd"] for t in trades if t["pnl_usd"] < 0]
    avg_win  = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0.0
    avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0

    net_pnl    = round(result.get("net_profit_usd", 0.0), 2)
    max_dd     = result.get("max_drawdown_pct", 0.0)
    expectancy = round(result.get("expectancy_usd", 0.0), 2)

    return {
        "step":            step_num,
        "label":           label,
        "disabled":        list(disabled_setups),
        "total":           total,
        "win_rate":        win_rate,
        "net_pnl":         net_pnl,
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "expectancy":      expectancy,
        "max_dd":          max_dd,
        "trades":          trades,
    }, eval_reports, cached_df


def print_pruning_table(results: list):
    SEP = "-" * 115
    HDR = (
        f"{'Step / Removed Setup':<42} {'Trades':>7} {'WinRate':>8} {'NetPnL':>10} "
        f"{'AvgWin':>8} {'AvgLoss':>9} {'Expect':>9} {'MaxDD%':>7}"
    )
    print("\n" + SEP)
    print(HDR)
    print(SEP)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<42}   ERROR: {r['error'][:60]}")
            continue
        print(
            f"{r['label']:<42} {r['total']:>7} {r['win_rate']:>7.1f}% "
            f"{r['net_pnl']:>10.2f} {r['avg_win']:>8.2f} {r['avg_loss']:>9.2f} "
            f"{r['expectancy']:>9.2f} {r['max_dd']:>7.2f}%"
        )
    print(SEP + "\n")


if __name__ == "__main__":
    print("\n" + "="*80)
    print(" PROGRESSIVE SETUP PRUNING SWEEP (STRICT HTF ALIGNMENT ENABLED)")
    print(f" Symbol: {SYMBOL} | TF: {TIMEFRAME} | Bars: {COUNT:,} (~17 Days) | Balance: ${BALANCE:,.0f}")
    print(" Validated Exit Config: BE=0.5R, Trail=0.6R, Guard=0.2R / 30%")
    print("="*80 + "\n")

    print("[0/1] Loading candles...")
    from app.adapters.mt5_adapter import mt5_adapter
    candles = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe=TIMEFRAME, count=COUNT)
    print(f"      Loaded {len(candles)} {TIMEFRAME} candles.")
    if candles:
        print(f"      Window: {candles[0].timestamp} -> {candles[-1].timestamp}\n")

    # Progressive removal list ordered by worst net PnL contribution
    PRUNING_STEPS = [
        (0, "Step 0: All Setups Active (Strict HTF Baseline)", []),
        (1, "Step 1: Remove BB Midline Pullback", [
            "BB Midline Pullback"
        ]),
        (2, "Step 2: + Remove SETUP D Bullish Breakout", [
            "BB Midline Pullback",
            "SETUP D: Bullish BOS Breakout & Retest"
        ]),
        (3, "Step 3: + Remove SETUP D Bearish Breakdown", [
            "BB Midline Pullback",
            "SETUP D: Bullish BOS Breakout & Retest",
            "SETUP D: Bearish BOS Breakdown & Retest"
        ]),
        (4, "Step 4: + Remove BB Counter-Trend Reversion", [
            "BB Midline Pullback",
            "SETUP D: Bullish BOS Breakout & Retest",
            "SETUP D: Bearish BOS Breakdown & Retest",
            "BB Counter-Trend Reversion"
        ]),
        (5, "Step 5: + Remove BB Trend Interaction", [
            "BB Midline Pullback",
            "SETUP D: Bullish BOS Breakout & Retest",
            "SETUP D: Bearish BOS Breakdown & Retest",
            "BB Counter-Trend Reversion",
            "BB Trend-Following Interaction"
        ]),
        (6, "Step 6: + Remove BB Band Breakout", [
            "BB Midline Pullback",
            "SETUP D: Bullish BOS Breakout & Retest",
            "SETUP D: Bearish BOS Breakdown & Retest",
            "BB Counter-Trend Reversion",
            "BB Trend-Following Interaction",
            "BB Band Breakout / Momentum"
        ]),
        (7, "Step 7: + Remove SETUP E EMA Pullback", [
            "BB Midline Pullback",
            "SETUP D: Bullish BOS Breakout & Retest",
            "SETUP D: Bearish BOS Breakdown & Retest",
            "BB Counter-Trend Reversion",
            "BB Trend-Following Interaction",
            "BB Band Breakout / Momentum",
            "SETUP E: EMA 9/20 Dynamic Trend Pullback"
        ]),
    ]

    results = []
    cached_df = None
    cached_evals = None
    for step_num, label, disabled in PRUNING_STEPS:
        r, cached_evals, cached_df = run_pruning_scenario(
            step_num, label, disabled, candles, precomputed_df=cached_df, cached_reports=cached_evals
        )
        results.append(r)

    # Print Pruning Sweep Table
    print_pruning_table(results)

    # Positive PnL & Crossover Check
    positive_steps = [r for r in results if "net_pnl" in r and r["net_pnl"] > 0]
    
    print("="*80)
    print(" PRUNING SWEEP VERDICT & POSITIVE PnL CROSSOVER CHECK:")
    print("="*80)
    if positive_steps:
        print(f"[SUCCESS] NET POSITIVE PnL ACHIEVED AT STEP {positive_steps[0]['step']}!")
        for p in positive_steps:
            print(f"   - {p['label']}: Net PnL = +${p['net_pnl']:,.2f} | WR = {p['win_rate']}% | Trades = {p['total']}")
    else:
        print("[NET NEGATIVE] NO PRUNING STEP ACHIEVED NET POSITIVE PnL.")
        print("   Even after progressively removing up to 7 underperforming setups, net PnL remained negative.")

    print("\n[SAMPLE SIZE & RELIABILITY CHECK]")
    for r in results:
        total = r.get("total", 0)
        label = r.get("label", "")
        if total < 150:
            print(f"   [SAMPLE SIZE WARNING] {label}: Only {total} trades executed (<150 trade reliability threshold).")
        else:
            print(f"   [ROBUST SAMPLE] {label}: {total} trades executed.")
    print("="*80 + "\n")
