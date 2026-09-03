"""
Backtest Comparison: Partial Close / Scale-Out Exit Strategy
============================================================
Evaluates Partial Scale-Out exit mode (30%, 50%, 70%) against the Scenario 3 Baseline
(M5 entry, IMMEDIATE mode, Strict HTF Filter ON, -$398.81 net PnL baseline to beat)
over the 24,500 M1-equivalent candle window (~17 continuous trading days).
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
TIMEFRAME   = "M5"
COUNT       = 4_900    # ~17 days (24,500 M1 equivalent / 5)
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
    partial_enabled: bool,
    partial_pct: float,
    candles: list
) -> dict:
    print(f"  Running Scenario {scenario_id}: {label} ...", flush=True)

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
        break_even_rr=0.5,
        trailing_activation_rr=0.6,
        giveback_guard_enabled=True,
        giveback_guard_min_rr=0.2,
        giveback_guard_retrace_pct=30.0,
        htf_alignment_filter_enabled=True,
        htf_alignment_mode="STRICT",
        disabled_setups=None,
        entry_confirmation_mode="IMMEDIATE",
        partial_close_enabled=partial_enabled,
        partial_close_pct=partial_pct
    )

    if "error" in result:
        print(f"    ERROR: {result['error']}")
        return {"id": scenario_id, "label": label, "error": result["error"]}

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
        "partial_pct":  f"{partial_pct:.0f}%" if partial_enabled else "OFF",
        "total":        total,
        "win_rate":     win_rate,
        "net_pnl":      net_pnl,
        "avg_win":      avg_win,
        "avg_loss":     avg_loss,
        "rr_ratio":     rr_ratio,
        "expectancy":   expectancy,
        "max_dd":       max_dd,
        "trades":       trades,
    }


def print_comparison_table(results: list):
    SEP = "-" * 125
    HDR = (
        f"{'Scenario / Scale-Out Mode':<48} {'Partial%':>10} "
        f"{'Trades':>7} {'WinRate':>8} {'NetPnL':>10} {'AvgWin':>8} {'AvgLoss':>9} {'AvgWin/Loss':>11} {'Expect':>9} {'MaxDD%':>7}"
    )
    print("\n" + SEP)
    print(HDR)
    print(SEP)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<48}   ERROR: {r['error'][:60]}")
            continue
        print(
            f"{r['label']:<48} {r['partial_pct']:>10} "
            f"{r['total']:>7} {r['win_rate']:>7.1f}% {r['net_pnl']:>10.2f} "
            f"{r['avg_win']:>8.2f} {r['avg_loss']:>9.2f} {r['rr_ratio']:>11.2f} {r['expectancy']:>9.2f} {r['max_dd']:>7.2f}%"
        )
    print(SEP + "\n")


if __name__ == "__main__":
    print("\n" + "="*100)
    print(" PARTIAL CLOSE / SCALE-OUT EXIT STRATEGY BACKTEST COMPARISON")
    print(f" Symbol: {SYMBOL} | TF: {TIMEFRAME} | Bars: {COUNT:,} (~17 Days) | Balance: ${BALANCE:,.0f}")
    print(" Config: Scenario 3 Baseline (M5, IMMEDIATE, Strict HTF Filter ON)")
    print(" Exit Base: BE=0.5R, Trail=0.6R, Guard=0.2R / 30%")
    print("="*100 + "\n")

    print("[0/1] Loading M5 candles...")
    candles = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe=TIMEFRAME, count=COUNT)
    print(f"      Loaded {len(candles)} {TIMEFRAME} candles ({candles[0].timestamp} -> {candles[-1].timestamp})\n")

    scenarios_def = [
        (1, "1. BASELINE (Scenario 3 - No Partial Close, 100% Guard)", False, 0.0),
        (2, "2. PARTIAL CLOSE 30% (30% Guard, 70% Trailing Runner)", True, 30.0),
        (3, "3. PARTIAL CLOSE 50% (50% Guard, 50% Trailing Runner)", True, 50.0),
        (4, "4. PARTIAL CLOSE 70% (70% Guard, 30% Trailing Runner)", True, 70.0),
    ]

    results = []
    for sc_id, label, enabled, pct in scenarios_def:
        r = run_scenario(sc_id, label, enabled, pct, candles)
        results.append(r)

    # Print Main Comparison Table
    print_comparison_table(results)

    # Print Per-Setup Breakdown Tables
    for r in results:
        if "trades" in r and r["trades"]:
            breakdown = compute_per_setup_breakdown(r["trades"])
            print_setup_table(f"PER-SETUP BREAKDOWN: {r['label']}", breakdown)

    # Print Important Audits & Verdict
    print("="*100)
    print(" AUDIT & VERDICT SUMMARY:")
    print("="*100)

    # Check 1: Sample Size Audit
    print("\n[CHECK 1: SAMPLE SIZE AUDIT (< 100 Trades Warning)]")
    for r in results:
        total = r.get("total", 0)
        label = r.get("label", "")
        if total < 100:
            print(f"   [LOW SAMPLE SIZE WARNING] {label}: Only {total} trades executed over ~17 days. Statistical conclusions may be unreliable.")
        else:
            print(f"   [ROBUST SAMPLE] {label}: {total} trades executed.")

    # Check 2: Net Positive PnL Crossover
    print("\n[CHECK 2: NET POSITIVE PnL CROSSOVER]")
    positive_scenarios = [r for r in results if r.get("net_pnl", 0) > 0]
    if positive_scenarios:
        for r in positive_scenarios:
            print(f"   [NET POSITIVE PnL] {r['label']}: Net PnL = +${r['net_pnl']:,.2f} over {r['total']} trades!")
            if "trades" in r and r["trades"]:
                bd = compute_per_setup_breakdown(r["trades"])
                pos_setups = [name for name, s in bd.items() if s["net_pnl"] > 0]
                print(f"      Driving Setups with Net Positive PnL: {pos_setups}")
    else:
        print("   [NET NEGATIVE] No partial close variant crossed into net positive PnL overall.")

    # Check 3: Win/Loss Ratio Ceiling Breakdown Verdict
    base_rr = results[0].get("rr_ratio", 0.0)
    base_pnl = results[0].get("net_pnl", 0.0)
    print("\n[CHECK 3: WIN/LOSS RATIO CEILING VERDICT vs BASELINE]")
    print(f"   - Baseline (No Partial Close): Avg Win / Avg Loss Ratio = {base_rr:.2f} | Net PnL = ${base_pnl:,.2f}")

    for r in results[1:]:
        sc_rr = r.get("rr_ratio", 0.0)
        sc_pnl = r.get("net_pnl", 0.0)
        improved_rr = sc_rr > base_rr
        improved_pnl = sc_pnl > base_pnl
        print(
            f"   - {r['label']}: Avg Win / Avg Loss Ratio = {sc_rr:.2f} "
            f"({'BROKE CEILING' if improved_rr else 'No Ceiling Break'}) | "
            f"Net PnL = ${sc_pnl:,.2f} ({'IMPROVED vs Baseline' if improved_pnl else 'Worse vs Baseline'})"
        )

    print("\n" + "="*100 + "\n")
