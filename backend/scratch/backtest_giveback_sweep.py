"""
Scalper Profit-Protection Parameter Sweep & Per-Setup Performance Breakdown
=============================================================================
1. Runs parameter sweep across 30,000 M1 candles (~3 weeks of XAUUSD data).
2. Computes per-setup performance breakdown (Setup A/B/D/E/F) for baseline vs tuned exit configs.
"""

import sys
import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")

from app.services.backtester import BacktestingEngine

# ── Configuration ──────────────────────────────────────────────────────────────
SYMBOL      = "XAUUSD"
TIMEFRAME   = "M1"
COUNT       = 24_500        # ~17 days / 2.5 weeks of M1 candles (~24,500 max limit)
BALANCE     = 10_000.0
RISK_PCT    = 1.0
LOT_SIZE    = 0.05
RR_RATIO    = 2.0
COOLDOWN    = 30
DAILY_LOSS  = 3.0
MAX_LOSSES  = 20
SPREAD_PIPS = 2.5
CONFIDENCE  = 0.70

# ── Helper ─────────────────────────────────────────────────────────────────────
def run_scenario(label: str, **kwargs) -> tuple:
    print(f"  Running: {label} ...", flush=True)
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
        **kwargs
    )
    eval_reports = result.get("eval_reports", {})
    full_df = result.get("precomputed_full_df", None)

    if "error" in result:
        print(f"    ERROR: {result['error']}")
        return {"label": label, "error": result["error"], "trades_raw": []}, eval_reports, full_df

    trades = result.get("trades", [])
    total = result.get("total_trades", 0)
    wins = len([t for t in trades if t["pnl_usd"] > 0])
    decisive = wins + len([t for t in trades if t["pnl_usd"] < 0])
    win_rate = round(wins / decisive * 100, 1) if decisive else 0.0

    # Count exit types
    exit_types = {}
    avg_win = 0.0
    avg_loss = 0.0
    if trades:
        win_pnls  = [t["pnl_usd"] for t in trades if t["pnl_usd"] > 0]
        loss_pnls = [t["pnl_usd"] for t in trades if t["pnl_usd"] < 0]
        avg_win  = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0.0
        avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0
        for t in trades:
            r = t.get("exit_reason", "UNKNOWN")
            exit_types[r] = exit_types.get(r, 0) + 1

    net_pnl      = round(result.get("net_profit_usd", 0.0), 2)
    max_dd       = result.get("max_drawdown_pct", 0.0)
    expectancy   = round(result.get("expectancy_usd", 0.0), 2)
    period_from  = result.get("from_date", "?")
    period_to    = result.get("to_date", "?")

    return {
        "label":       label,
        "total":       total,
        "win_rate":    win_rate,
        "net_pnl":     net_pnl,
        "avg_win":     avg_win,
        "avg_loss":    avg_loss,
        "expectancy":  expectancy,
        "max_dd":      max_dd,
        "exit_types":  exit_types,
        "period":      f"{period_from} -> {period_to}",
        "trades_raw":  trades,
    }, eval_reports, full_df


def print_table(results: list):
    SEP = "-" * 120
    HDR = (
        f"{'Label':<42} {'Trades':>7} {'WinRate':>8} {'NetPnL':>10} "
        f"{'AvgWin':>8} {'AvgLoss':>9} {'Expect':>9} {'MaxDD%':>7}"
    )
    print(SEP)
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
    print(SEP)


def compute_per_setup_breakdown(trades: list) -> dict:
    """Group trades by setup_name and compute win rate, PnL, expectancy, exit breakdown."""
    setup_groups = {}
    for t in trades:
        name = t.get("setup_name", "Unknown")
        if name not in setup_groups:
            setup_groups[name] = []
        setup_groups[name].append(t)

    breakdown = {}
    for name, s_trades in setup_groups.items():
        total = len(s_trades)
        wins = len([t for t in s_trades if t["pnl_usd"] > 0])
        losses = len([t for t in s_trades if t["pnl_usd"] < 0])
        decisive = wins + losses
        win_rate = round(wins / decisive * 100, 1) if decisive else 0.0

        win_pnls = [t["pnl_usd"] for t in s_trades if t["pnl_usd"] > 0]
        loss_pnls = [t["pnl_usd"] for t in s_trades if t["pnl_usd"] < 0]

        avg_win = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0.0
        avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0
        net_pnl = round(sum(t["pnl_usd"] for t in s_trades), 2)
        expectancy = round(net_pnl / total, 2) if total > 0 else 0.0

        exits = {}
        for t in s_trades:
            r = t.get("exit_reason", "UNKNOWN")
            exits[r] = exits.get(r, 0) + 1

        breakdown[name] = {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "net_pnl": net_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "exit_reasons": exits,
        }
    return breakdown


def print_setup_table(breakdown: dict, title: str):
    print("\n" + "="*80)
    print(f" PER-SETUP PERFORMANCE BREAKDOWN: {title}")
    print("="*80)
    HDR = f"{'Setup Name':<28} {'Trades':>7} {'WinRate':>8} {'NetPnL':>10} {'AvgWin':>8} {'AvgLoss':>9} {'Expect':>9}"
    SEP = "-" * 80
    print(HDR)
    print(SEP)
    for name in sorted(breakdown.keys()):
        st = breakdown[name]
        print(
            f"{name:<28} {st['total']:>7} {st['win_rate']:>7.1f}% "
            f"{st['net_pnl']:>10.2f} {st['avg_win']:>8.2f} {st['avg_loss']:>9.2f} "
            f"{st['expectancy']:>9.2f}"
        )
    print(SEP)


# ── Run Scenarios ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*70)
    print(" EXTENDED SCALPER PROFIT-PROTECTION BACKTEST SWEEP")
    print(f" Symbol: {SYMBOL} | TF: {TIMEFRAME} | Bars: {COUNT:,} (~3 Weeks) | Balance: ${BALANCE:,.0f}")
    print("="*70 + "\n")

    print("[0/3] Pre-fetching candles once for fast sweep execution...")
    from app.adapters.mt5_adapter import mt5_adapter
    candles = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe=TIMEFRAME, count=COUNT)
    print(f"      Loaded {len(candles)} {TIMEFRAME} candles.")
    if candles:
        print(f"      Window: {candles[0].timestamp} -> {candles[-1].timestamp}\n")

    results = []

    # 1. BASELINE: old settings, no giveback guard
    print("[1/3] Running baseline (OLD settings)...")
    res1, cached_evals, cached_df = run_scenario(
        "BASELINE (BE=1.0, Trail=1.2, NoGuard)",
        candles=candles,
        break_even_rr=1.0,
        trailing_activation_rr=1.2,
        giveback_guard_enabled=False,
        giveback_guard_min_rr=0.3,
        giveback_guard_retrace_pct=50.0,
    )
    results.append(res1)
    print(f"      Setup evaluation cached ({len(cached_evals)} candles evaluated).\n")

    # 2. NEW DEFAULTS: lowered thresholds + guard
    print("[2/3] Running new defaults (BE=0.5, Trail=0.6, Guard ON: 0.2R / 30%)...")
    res2, _, _ = run_scenario(
        "NEW DEFAULTS (BE=0.5, Trail=0.6, Guard 0.2/30%)",
        candles=candles,
        cached_reports=cached_evals,
        precomputed_full_df=cached_df,
        break_even_rr=0.5,
        trailing_activation_rr=0.6,
        giveback_guard_enabled=True,
        giveback_guard_min_rr=0.2,
        giveback_guard_retrace_pct=30.0,
    )
    results.append(res2)

    # 3. PARAMETER SWEEP KEY SCENARIOS
    print("\n[3/3] Running representative parameter sweep...")
    SWEEP_CONFIGS = [
        (0.2, 50.0),
        (0.3, 30.0),
        (0.3, 50.0),
        (0.5, 50.0),
    ]

    sweep_results = []
    for min_rr, retrace_pct in SWEEP_CONFIGS:
        label = f"BE=0.5 Trail=0.6 Guard(min={min_rr} ret={int(retrace_pct)}%)"
        r, _, _ = run_scenario(
            label,
            candles=candles,
            cached_reports=cached_evals,
            precomputed_full_df=cached_df,
            break_even_rr=0.5,
            trailing_activation_rr=0.6,
            giveback_guard_enabled=True,
            giveback_guard_min_rr=min_rr,
            giveback_guard_retrace_pct=retrace_pct,
        )
        r["min_rr"] = min_rr
        r["retrace_pct"] = retrace_pct
        sweep_results.append(r)
        results.append(r)

    # ── Print Comparison Table ─────────────────────────────────────────────────
    print("\n\n" + "="*70)
    print(" EXTENDED WINDOW COMPARISON TABLE (~3 WEEKS DATA)")
    print("="*70)
    print_table(results)

    # ── Best Sweep Pick ────────────────────────────────────────────────────────
    valid_sweep = [r for r in sweep_results if "error" not in r and r.get("total", 0) > 0]
    if valid_sweep:
        def score(r):
            # Composite score: prioritize net PnL, then low drawdown, then expectancy
            dd_penalty = r["max_dd"] * 200
            return r["net_pnl"] - dd_penalty + r["expectancy"] * 10

        best = max(valid_sweep, key=score)
        print(f"\n[BEST] BEST SWEEP COMBINATION:")
        print(f"   Label:               {best['label']}")
        print(f"   giveback_min_rr:     {best.get('min_rr', '?')}")
        print(f"   giveback_retrace_pct:{best.get('retrace_pct', '?')}%")
        print(f"   Total Trades:        {best['total']}")
        print(f"   Win Rate:            {best['win_rate']}%")
        print(f"   Net PnL:             ${best['net_pnl']:,.2f}")
        print(f"   Avg Win:             ${best['avg_win']:,.2f}")
        print(f"   Avg Loss:            ${best['avg_loss']:,.2f}")
        print(f"   Expectancy/Trade:    ${best['expectancy']:,.2f}")
        print(f"   Max Drawdown:        {best['max_dd']:.2f}%")
        print(f"\n   Exit Type Breakdown: {best.get('exit_types', {})}")

    # ── PER-SETUP BREAKDOWN ────────────────────────────────────────────────────
    # 1. Baseline per-setup
    b_breakdown = compute_per_setup_breakdown(res1.get("trades_raw", []))
    print_setup_table(b_breakdown, "BASELINE (BE=1.0, Trail=1.2, No Guard)")

    # 2. Chosen Best Config per-setup (res2: Guard 0.2R / 30%)
    best_config_trades = res2.get("trades_raw", [])
    best_breakdown = compute_per_setup_breakdown(best_config_trades)
    print_setup_table(best_breakdown, "NEW TUNED CONFIG (BE=0.5, Trail=0.6, Guard min=0.2R ret=30%)")

    # Details of exit reasons per setup for best config
    print("\n" + "="*80)
    print(" EXIT REASON BREAKDOWN PER SETUP (Tuned Config)")
    print("="*80)
    for s_name in sorted(best_breakdown.keys()):
        exits = best_breakdown[s_name]["exit_reasons"]
        print(f"  {s_name:<28} -> Exits: {exits}")
    print("="*80 + "\n")
