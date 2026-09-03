"""
Hard Higher-Timeframe (H1 + M15) Alignment Filter Backtest Sweep
===============================================================
Backtests M1 scalp entries under:
1. Baseline: HTF Filter OFF (Soft scoring only)
2. Strict HTF Filter: BUY requires H1=BULLISH & M15=BULLISH; SELL requires H1=BEARISH & M15=BEARISH
3. Moderate HTF Filter: Opposing HTF trend blocked (NEUTRAL allowed)

Evaluated over 24,500 M1 candles (~17 continuous trading days / 2.5 weeks of XAUUSD data).
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

        exits = {}
        for t in group:
            reason = t.get("exit_reason", "UNKNOWN")
            exits[reason] = exits.get(reason, 0) + 1

        breakdown[name] = {
            "total": total, "win_rate": win_rate, "net_pnl": net_pnl,
            "avg_win": avg_win, "avg_loss": avg_loss, "expectancy": expectancy,
            "exits": exits
        }
    return breakdown


def print_setup_table(title: str, breakdown: dict):
    print("\n" + "="*80)
    print(f" {title}")
    print("="*80)
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


def run_htf_scenario(label: str, filter_enabled: bool, mode: str, candles: list, precomputed_df: any = None) -> tuple:
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
        candles=candles,
        precomputed_full_df=precomputed_df,
        break_even_rr=0.5,
        trailing_activation_rr=0.6,
        giveback_guard_enabled=True,
        giveback_guard_min_rr=0.2,
        giveback_guard_retrace_pct=30.0,
        htf_alignment_filter_enabled=filter_enabled,
        htf_alignment_mode=mode
    )
    if "error" in result:
        print(f"    ERROR: {result['error']}")
        return {"label": label, "error": result["error"]}, None

    trades = result.get("trades", [])
    total = result.get("total_trades", 0)
    opps = result.get("total_opportunities", 0)
    blocked = result.get("blocked_count", 0)
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
        "label": label,
        "opps": opps,
        "total": total,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "max_dd": max_dd,
        "trades": trades,
    }, cached_df


def print_comparison_table(results: list, raw_baseline_opps: int):
    SEP = "-" * 125
    HDR = (
        f"{'Scenario / HTF Alignment Filter':<42} {'Opps':>6} {'Trades':>7} {'RejRate%':>9} "
        f"{'WinRate':>8} {'NetPnL':>10} {'AvgWin':>8} {'AvgLoss':>9} {'Expect':>9} {'MaxDD%':>7}"
    )
    print("\n" + SEP)
    print(HDR)
    print(SEP)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<42}   ERROR: {r['error'][:60]}")
            continue
        rej_rate = round((1.0 - (r['total'] / raw_baseline_opps)) * 100, 1) if raw_baseline_opps else 0.0
        print(
            f"{r['label']:<42} {r['opps']:>6} {r['total']:>7} {rej_rate:>8.1f}% "
            f"{r['win_rate']:>7.1f}% {r['net_pnl']:>10.2f} {r['avg_win']:>8.2f} "
            f"{r['avg_loss']:>9.2f} {r['expectancy']:>9.2f} {r['max_dd']:>7.2f}%"
        )
    print(SEP + "\n")


if __name__ == "__main__":
    print("\n" + "="*80)
    print(" HARD HTF (H1 + M15) ALIGNMENT FILTER BACKTEST SWEEP")
    print(f" Symbol: {SYMBOL} | TF: {TIMEFRAME} | Bars: {COUNT:,} (~17 Days) | Balance: ${BALANCE:,.0f}")
    print(" Validated Exit Config: BE=0.5R, Trail=0.6R, Guard=0.2R / 30%")
    print("="*80 + "\n")

    print("[0/1] Loading candles...")
    from app.adapters.mt5_adapter import mt5_adapter
    candles = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe=TIMEFRAME, count=COUNT)
    print(f"      Loaded {len(candles)} {TIMEFRAME} candles.")
    if candles:
        print(f"      Window: {candles[0].timestamp} -> {candles[-1].timestamp}\n")

    scenarios = [
        ("1. Baseline (HTF Filter OFF - Soft Scoring Only)", False, "MODERATE"),
        ("2. Strict HTF Filter (H1 & M15 Exact Match)",      True,  "STRICT"),
        ("3. Moderate HTF Filter (Opposing Trend Blocked)",   True,  "MODERATE"),
    ]

    results = []
    cached_df = None
    for label, enabled, mode in scenarios:
        r, cached_df = run_htf_scenario(label, enabled, mode, candles, precomputed_df=cached_df)
        results.append(r)

    raw_baseline_opps = results[0]["total"] if results and "total" in results[0] else 1

    # Print main comparison table
    print_comparison_table(results, raw_baseline_opps)

    # Print setup breakdowns for each scenario
    for r in results:
        if "trades" in r and r["trades"]:
            breakdown = compute_per_setup_breakdown(r["trades"])
            print_setup_table(f"PER-SETUP BREAKDOWN: {r['label']}", breakdown)

    # Print explicit conclusion & sample size warnings
    print("="*80)
    print(" VERDICT & SAMPLE SIZE ANALYSIS:")
    print("="*80)
    for r in results:
        label = r["label"]
        total = r.get("total", 0)
        net_pnl = r.get("net_pnl", 0.0)
        if total < 150:
            print(f"[SAMPLE SIZE WARNING] {label}: Only {total} trades executed across 17 days.")
            print(f"    Sample size is too small (<150 trades) to draw robust conclusions.")
        if net_pnl > 0:
            print(f"[POSITIVE PnL] {label}: Net PnL = +${net_pnl:,.2f} over 17 days across {total} trades.")
        else:
            print(f"[NET NEGATIVE] {label}: Net PnL = ${net_pnl:,.2f} across {total} trades.")
    print("="*80 + "\n")
