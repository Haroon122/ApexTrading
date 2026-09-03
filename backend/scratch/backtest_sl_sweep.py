"""
SL Distance Recalibration Sweep Script
======================================
Backtests SL distance multipliers (100%, 80%, 65%, 50%, 35%, 25%, 20%)
on the 24,500 M1 candle XAUUSD dataset combined with the validated exit config.
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

def run_sl_scenario(label: str, sl_mult: float, candles: list, cached_evals: dict, cached_df: any) -> dict:
    print(f"  Running: {label} (sl_multiplier={sl_mult}) ...", flush=True)
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
        cached_reports=cached_evals,
        precomputed_full_df=cached_df,
        break_even_rr=0.5,
        trailing_activation_rr=0.6,
        giveback_guard_enabled=True,
        giveback_guard_min_rr=0.2,
        giveback_guard_retrace_pct=30.0,
        sl_multiplier=sl_mult
    )
    if "error" in result:
        print(f"    ERROR: {result['error']}")
        return {"label": label, "error": result["error"], "sl_mult": sl_mult}

    trades = result.get("trades", [])
    total = result.get("total_trades", 0)
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
        "label":      label,
        "sl_mult":    sl_mult,
        "total":      total,
        "win_rate":   win_rate,
        "net_pnl":    net_pnl,
        "avg_win":    avg_win,
        "avg_loss":   avg_loss,
        "expectancy": expectancy,
        "max_dd":     max_dd,
        "trades":     trades,
    }


def print_sl_table(results: list):
    SEP = "-" * 115
    HDR = (
        f"{'SL Distance Configuration':<42} {'Trades':>7} {'WinRate':>8} {'NetPnL':>10} "
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
    print("\n" + "="*75)
    print(" STOP LOSS DISTANCE RECALIBRATION SWEEP")
    print(f" Symbol: {SYMBOL} | TF: {TIMEFRAME} | Bars: {COUNT:,} (~17 Days) | Balance: ${BALANCE:,.0f}")
    print(" Validated Exit Config: BE=0.5R, Trail=0.6R, Guard=0.2R / 30%")
    print("="*75 + "\n")

    print("[0/2] Loading candles & caching decision engine setup evaluations...")
    from app.adapters.mt5_adapter import mt5_adapter
    candles = mt5_adapter.fetch_candles(symbol=SYMBOL, timeframe=TIMEFRAME, count=COUNT)
    print(f"      Loaded {len(candles)} {TIMEFRAME} candles.")
    if candles:
        print(f"      Window: {candles[0].timestamp} -> {candles[-1].timestamp}\n")

    # Run baseline once to generate cached_reports and precomputed_full_df
    res_base = BacktestingEngine.run_backtest(
        symbol=SYMBOL, timeframe=TIMEFRAME, count=COUNT,
        initial_balance=BALANCE, risk_pct=RISK_PCT, lot_sizing_mode="FIXED",
        user_configured_lot=LOT_SIZE, min_risk_reward_ratio=RR_RATIO,
        auto_trade_cooldown_seconds=COOLDOWN, daily_loss_limit_pct=DAILY_LOSS,
        max_consecutive_losses=MAX_LOSSES, max_spread_pips=SPREAD_PIPS,
        min_ai_confidence=CONFIDENCE, skip_stress=True, candles=candles,
        break_even_rr=0.5, trailing_activation_rr=0.6,
        giveback_guard_enabled=True, giveback_guard_min_rr=0.2, giveback_guard_retrace_pct=30.0,
        sl_multiplier=1.0
    )
    cached_evals = res_base.get("eval_reports", {})
    cached_df    = res_base.get("precomputed_full_df", None)
    print(f"      Setup evaluation cached ({len(cached_evals)} candles evaluated).\n")

    SL_MULTIPLIERS = [
        ("100% SL Distance (Current Baseline)", 1.00),
        ("80% SL Distance (20% Tighter)",        0.80),
        ("65% SL Distance (35% Tighter)",        0.65),
        ("50% SL Distance (50% Tighter)",        0.50),
        ("35% SL Distance (65% Tighter)",        0.35),
        ("25% SL Distance (75% Tighter)",        0.25),
        ("20% SL Distance (80% Tighter)",        0.20),
    ]

    print("[1/2] Sweeping SL Distance Multipliers...")
    results = []
    for label, mult in SL_MULTIPLIERS:
        r = run_sl_scenario(label, mult, candles, cached_evals, cached_df)
        results.append(r)

    # ── Print Sweep Table ──────────────────────────────────────────────────────
    print_sl_table(results)

    # Check for positive PnL
    positive_results = [r for r in results if "error" not in r and r.get("net_pnl", -1) > 0]
    print("="*75)
    print(" KEY SWEEP CONCLUSIONS & POSITIVE PnL CHECK:")
    print("="*75)
    if positive_results:
        print(f"✅  FOUND {len(positive_results)} POSITIVE PnL COMBINATION(S):")
        for r in positive_results:
            print(f"   - {r['label']}: Net PnL = +${r['net_pnl']:,.2f} | WR = {r['win_rate']}% | Expectancy = +${r['expectancy']:.2f}")
    else:
        print("❌  NO TESTED SL DISTANCE PRODUCED A NET POSITIVE PnL over the 17-day window.")
        print("   All tested SL multipliers remained net negative.")
    print("="*75 + "\n")
