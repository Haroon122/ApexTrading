"""
Full Backtest Comparison: M5 / M15 Entry Timeframe & Confirmation Mode Sweep
=============================================================================
Evaluates M5 entry timeframe (with IMMEDIATE and CONFIRM_NEXT_CANDLE, with/without HTF filter)
against the best-known M1 baseline over the same ~17-day (24,500 M1-equivalent candle) window.
"""

import sys
import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")

from app.services.backtester import BacktestingEngine
from app.adapters.mt5_adapter import mt5_adapter

SYMBOL      = "XAUUSD"
BALANCE     = 10_000.0
RISK_PCT    = 1.0
LOT_SIZE    = 0.05
RR_RATIO    = 2.0
COOLDOWN    = 30
DAILY_LOSS  = 3.0
MAX_LOSSES  = 20
SPREAD_PIPS = 2.5
CONFIDENCE  = 0.70

# M1 equivalent count = 24,500 candles (~17 continuous trading days)
M1_COUNT  = 24_500
M5_COUNT  = 4_900    # 24,500 / 5
M15_COUNT = 1_634    # 24,500 / 15


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


def print_setup_table(title: str, breakdown: dict):
    print("\n" + "="*98)
    print(f" {title}")
    print("="*98)
    HDR = f"{'Setup Name':<44} {'Trades':>7} {'WinRate':>8} {'NetPnL':>10} {'AvgWin':>8} {'AvgLoss':>9} {'Expect':>9}"
    SEP = "-"*98
    print(HDR)
    print(SEP)
    sorted_setups = sorted(breakdown.items(), key=lambda x: x[1]["net_pnl"])
    for name, s in sorted_setups:
        print(
            f"{name:<44} {s['total']:>7} {s['win_rate']:>7.1f}% "
            f"{s['net_pnl']:>10.2f} {s['avg_win']:>8.2f} {s['avg_loss']:>9.2f} "
            f"{s['expectancy']:>9.2f}"
        )
    print(SEP + "\n")


def run_scenario(
    scenario_id: int,
    label: str,
    tf: str,
    count: int,
    confirm_mode: str,
    filter_enabled: bool,
    filter_mode: str,
    disabled_setups: list = None,
    candles: list = None
) -> tuple:
    print(f"  Running Scenario {scenario_id}: {label} ...", flush=True)
    
    if candles is None:
        candles = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe=tf, count=count)

    result = BacktestingEngine.run_backtest(
        symbol=SYMBOL,
        timeframe=tf,
        count=count,
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
        break_even_rr=0.5,
        trailing_activation_rr=0.6,
        giveback_guard_enabled=True,
        giveback_guard_min_rr=0.2,
        giveback_guard_retrace_pct=30.0,
        htf_alignment_filter_enabled=filter_enabled,
        htf_alignment_mode=filter_mode,
        disabled_setups=disabled_setups,
        entry_confirmation_mode=confirm_mode
    )

    if "error" in result:
        print(f"    ERROR: {result['error']}")
        return {"id": scenario_id, "label": label, "error": result["error"]}, candles

    trades = result.get("trades", [])
    total = result.get("total_trades", 0)

    wins = len([t for t in trades if t["pnl_usd"] > 0])
    losses = len([t for t in trades if t["pnl_usd"] < 0])
    decisive = wins + losses
    win_rate = round(wins / decisive * 100, 1) if decisive else 0.0

    win_pnls  = [t["pnl_usd"] for t in trades if t["pnl_usd"] > 0]
    loss_pnls = [t["pnl_usd"] for t in trades if t["pnl_usd"] < 0]
    avg_win  = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0.0
    avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0
    rr_ratio = round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else 0.0

    net_pnl    = round(result.get("net_profit_usd", 0.0), 2)
    max_dd     = result.get("max_drawdown_pct", 0.0)
    expectancy = round(result.get("expectancy_usd", 0.0), 2)

    return {
        "id":           scenario_id,
        "label":        label,
        "tf":           tf,
        "confirm":      confirm_mode,
        "htf_filter":   "STRICT" if filter_enabled and filter_mode=="STRICT" else ("MODERATE" if filter_enabled else "OFF"),
        "total":        total,
        "win_rate":     win_rate,
        "net_pnl":      net_pnl,
        "avg_win":      avg_win,
        "avg_loss":     avg_loss,
        "rr_ratio":     rr_ratio,
        "expectancy":   expectancy,
        "max_dd":       max_dd,
        "trades":       trades,
        "start_time":   candles[0].timestamp if candles else None,
        "end_time":     candles[-1].timestamp if candles else None,
    }, candles


def print_comparison_table(results: list):
    SEP = "-" * 130
    HDR = (
        f"{'Scenario ID & Description':<48} {'TF':>4} {'Confirm Mode':>20} "
        f"{'Trades':>7} {'WinRate':>8} {'NetPnL':>10} {'AvgWin':>8} {'AvgLoss':>9} {'Expect':>9} {'MaxDD%':>7}"
    )
    print("\n" + SEP)
    print(HDR)
    print(SEP)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<48}   ERROR: {r['error'][:60]}")
            continue
        print(
            f"{r['label']:<48} {r['tf']:>4} {r['confirm']:>20} "
            f"{r['total']:>7} {r['win_rate']:>7.1f}% {r['net_pnl']:>10.2f} "
            f"{r['avg_win']:>8.2f} {r['avg_loss']:>9.2f} {r['expectancy']:>9.2f} {r['max_dd']:>7.2f}%"
        )
    print(SEP + "\n")


if __name__ == "__main__":
    print("\n" + "="*100)
    print(" FULL BACKTEST COMPARISON: M5 / M15 TIMEFRAME & CONFIRMATION MODE SWEEP")
    print(f" Symbol: {SYMBOL} | Initial Balance: ${BALANCE:,.0f} | Fixed Lot: {LOT_SIZE}")
    print(" Validated Exit Config: BE=0.5R, Trail=0.6R, Guard=0.2R / 30%")
    print("="*100 + "\n")

    # Fetch dataset candles for each timeframe covering exact same continuous window
    print("[0/3] Pre-loading historical candle datasets for M1, M5, M15...")
    m1_candles  = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe="M1", count=M1_COUNT)
    m5_candles  = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe="M5", count=M5_COUNT)
    m15_candles = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe="M15", count=M15_COUNT)

    print(f"      M1 Candles : {len(m1_candles)} bars ({m1_candles[0].timestamp} -> {m1_candles[-1].timestamp})")
    print(f"      M5 Candles : {len(m5_candles)} bars ({m5_candles[0].timestamp} -> {m5_candles[-1].timestamp})")
    print(f"      M15 Candles: {len(m15_candles)} bars ({m15_candles[0].timestamp} -> {m15_candles[-1].timestamp})\n")

    # Scenario Definitions
    scenarios_def = [
        # 1. REFERENCE — M1 Baseline
        (1, "1. REFERENCE — M1 Baseline (Strict HTF, No BB Midline)", "M1", M1_COUNT, "IMMEDIATE", True, "STRICT", ["BB Midline Pullback"], m1_candles),
        # 2. M5, IMMEDIATE, no HTF filter
        (2, "2. M5, IMMEDIATE, HTF Filter OFF", "M5", M5_COUNT, "IMMEDIATE", False, "MODERATE", None, m5_candles),
        # 3. M5, IMMEDIATE, Strict HTF filter
        (3, "3. M5, IMMEDIATE, Strict HTF Filter ON", "M5", M5_COUNT, "IMMEDIATE", True, "STRICT", None, m5_candles),
        # 4. M5, CONFIRM_NEXT_CANDLE, no HTF filter
        (4, "4. M5, CONFIRM_NEXT_CANDLE, HTF Filter OFF", "M5", M5_COUNT, "CONFIRM_NEXT_CANDLE", False, "MODERATE", None, m5_candles),
        # 5. M5, CONFIRM_NEXT_CANDLE, Strict HTF filter
        (5, "5. M5, CONFIRM_NEXT_CANDLE, Strict HTF Filter ON", "M5", M5_COUNT, "CONFIRM_NEXT_CANDLE", True, "STRICT", None, m5_candles),
    ]

    results = []
    for sc in scenarios_def:
        r, _ = run_scenario(*sc)
        results.append(r)

    # Determine best M5 scenario (highest net PnL among scenarios 2-5)
    m5_results = [r for r in results if r.get("id") in (2, 3, 4, 5) and "error" not in r]
    best_m5 = max(m5_results, key=lambda x: x["net_pnl"]) if m5_results else results[1]

    print(f"\n  [INFO] Best M5 Scenario identified: Scenario {best_m5['id']} ({best_m5['label']}) with Net PnL = ${best_m5['net_pnl']:,.2f}")
    print(f"         Running Scenario 6 (M15 with best configuration: Confirm={best_m5['confirm']}, HTF Filter={best_m5['htf_filter']})...\n")

    m15_filter_enabled = (best_m5["htf_filter"] == "STRICT")
    m15_sc = (6, f"6. M15, {best_m5['confirm']}, HTF Filter {best_m5['htf_filter']}", "M15", M15_COUNT, best_m5["confirm"], m15_filter_enabled, "STRICT", None, m15_candles)
    r6, _ = run_scenario(*m15_sc)
    results.append(r6)

    # Print Main Comparison Table
    print_comparison_table(results)

    # Print Per-Setup Breakdown Tables
    for r in results:
        if "trades" in r and r["trades"]:
            breakdown = compute_per_setup_breakdown(r["trades"])
            print_setup_table(f"PER-SETUP BREAKDOWN: Scenario {r['id']} — {r['label']}", breakdown)

    # Print Important Checks & Verdict
    print("="*100)
    print(" AUDIT & VERDICT SUMMARY:")
    print("="*100)

    # Check 1: Candle Data Verification
    print("\n[CHECK 1: CANDLE DATA VERIFICATION]")
    print(f"   - M1  Dataset: {len(m1_candles)} discrete M1 candles")
    print(f"   - M5  Dataset: {len(m5_candles)} discrete M5 candles (True M5 timeframe loaded from MT5 adapter)")
    print(f"   - M15 Dataset: {len(m15_candles)} discrete M15 candles (True M15 timeframe loaded from MT5 adapter)")

    # Check 2: Sample Size Audit
    print("\n[CHECK 2: SAMPLE SIZE AUDIT (< 100 Trades Warning)]")
    for r in results:
        total = r.get("total", 0)
        label = r.get("label", "")
        if total < 100:
            print(f"   [LOW SAMPLE SIZE WARNING] Scenario {r['id']} ({label}): Only {total} trades executed over ~17 days. Statistical conclusions may be unreliable.")
        else:
            print(f"   [ROBUST SAMPLE] Scenario {r['id']} ({label}): {total} trades executed.")

    # Check 3: Net Positive PnL Crossover
    print("\n[CHECK 3: NET POSITIVE PnL CROSSOVER]")
    positive_scenarios = [r for r in results if r.get("net_pnl", 0) > 0]
    if positive_scenarios:
        for r in positive_scenarios:
            print(f"   [NET POSITIVE PnL] Scenario {r['id']} ({r['label']}): Net PnL = +${r['net_pnl']:,.2f} over {r['total']} trades!")
            if "trades" in r and r["trades"]:
                bd = compute_per_setup_breakdown(r["trades"])
                pos_setups = [name for name, s in bd.items() if s["net_pnl"] > 0]
                print(f"      Driving Setups with Net Positive PnL: {pos_setups}")
    else:
        print("   [NET NEGATIVE] No M5 or M15 scenario crossed into net positive PnL overall.")

    # Check 4: Risk:Reward Profile Verdict vs M1 Baseline
    m1_ref = results[0]
    m1_rr = m1_ref.get("rr_ratio", 0.0)
    m1_win_rate = m1_ref.get("win_rate", 0.0)
    m1_expectancy = m1_ref.get("expectancy", 0.0)

    print("\n[CHECK 4: RISK:REWARD PROFILE & HONEST VERDICT VS M1 BASELINE]")
    print(f"   - M1 Baseline Reference  : Win Rate = {m1_win_rate}% | Avg Win/Avg Loss Ratio = {m1_rr:.2f} | Expectancy = ${m1_expectancy:.2f}/trade")

    for r in results[1:]:
        sc_rr = r.get("rr_ratio", 0.0)
        sc_wr = r.get("win_rate", 0.0)
        sc_exp = r.get("expectancy", 0.0)
        improved_rr = sc_rr > m1_rr
        print(
            f"   - Scenario {r['id']} ({r['tf']}, {r['confirm']}): Win Rate = {sc_wr}% | "
            f"Avg Win/Avg Loss Ratio = {sc_rr:.2f} ({'IMPROVED vs M1' if improved_rr else 'Worse vs M1'}) | Expectancy = ${sc_exp:.2f}/trade"
        )

    print("\n" + "="*100 + "\n")
