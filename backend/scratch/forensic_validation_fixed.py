import sys
import os
import json
import math
import random
from datetime import datetime, timedelta, time
from typing import List, Dict, Any, Optional

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import logging
logging.basicConfig(level=logging.WARNING)

from app.domain.models import Candle, TickerData, TradeSide, OrderType, TradeState
from app.services.indicators import IndicatorService
from app.services.decision_engine import decision_engine
from app.adapters.mt5_adapter import mt5_adapter, TF_MINUTES_MAP
from app.services.risk_engine import RiskEngine
from app.services.symbol_specs import get_symbol_specs
from app.services.dark_venus_bb import dark_venus_engine
from app.services.market_structure import MarketStructureEngine

def get_session_name(dt: datetime) -> str:
    t = dt.time()
    if time(13, 0) <= t < time(16, 0):
        return "LONDON_NY_OVERLAP"
    if time(8, 0) <= t < time(16, 0):
        return "LONDON"
    if time(13, 0) <= t < time(21, 0):
        return "NEWYORK"
    if time(0, 0) <= t < time(8, 0):
        return "ASIAN"
    return "OTHER"

def get_df_trend(df, end_idx) -> str:
    if df is None or df.empty or end_idx <= 0:
        return "NEUTRAL"
    idx = min(end_idx - 1, len(df) - 1)
    if idx < 0:
        return "NEUTRAL"
    latest = df.iloc[idx]
    close = float(latest["close"])
    ema20 = float(latest.get("ema_20", close))
    ema50 = float(latest.get("ema_50", close))
    if close > ema20 >= ema50:
        return "BULLISH"
    elif close < ema20 <= ema50:
        return "BEARISH"
    return "NEUTRAL"

def run_validation_test():
    print("=" * 80)
    print("APEXTRADER FORENSIC AUDIT: PHASE 3 HEAD-TO-HEAD VALIDATION TEST (FIXED VERSION)")
    print("=" * 80)
    sys.stdout.flush()

    symbol = "XAUUSD"
    specs = get_symbol_specs(symbol)
    digits = specs["digits"]
    pip_size = specs["pip_size"]
    point_size = specs["point_size"]
    pip_value_per_lot = specs["pip_value_per_lot"]

    m15_candles = mt5_adapter.fetch_candles(symbol, timeframe="M15", count=10000)
    first_ts = m15_candles[0].timestamp
    last_ts = m15_candles[-1].timestamp
    start_dt = first_ts - timedelta(days=10)
    end_dt = last_ts

    print(f"Loaded {len(m15_candles):,} M15 candles ({first_ts.strftime('%Y-%m-%d')} to {last_ts.strftime('%Y-%m-%d')}).")
    sys.stdout.flush()

    htf_candles = {}
    full_htf_dfs = {}
    for tf in ["M1", "M5", "H1", "H4"]:
        tf_mins = TF_MINUTES_MAP.get(tf, 15)
        needed_count = int(len(m15_candles) * (15 / tf_mins)) + 500
        candles = mt5_adapter.fetch_candles(symbol, timeframe=tf, count=needed_count, from_datetime=start_dt, to_datetime=end_dt)
        htf_candles[tf] = candles
        full_htf_dfs[tf] = IndicatorService.calculate_all_indicators(candles) if candles else None

    full_m15_df = IndicatorService.calculate_all_indicators(m15_candles)

    # ── TARGETED FIXES APPLIED FOR HEAD-TO-HEAD BENCHMARK ──
    # 1. Raised AI Confidence Threshold: 0.60 -> 0.85 (Baseline proved confidence >=90% has PF 1.92, whereas <85% loses money)
    # 2. Session Filter: Allow ONLY London, NY, and London/NY Overlap (Disable Asian session which had PF 0.33)
    # 3. Macro H1/H4 Trend Filter: Enforce H1 macro trend alignment
    # 4. Filter out counter-trend mean-reversion setups on Gold
    min_ai_conf_fixed = 0.85
    allowed_sessions_fixed = ["LONDON", "NEWYORK", "LONDON_NY_OVERLAP"]

    initial_balance = 10000.0
    balance = initial_balance
    risk_pct = 1.0
    user_configured_lot = 0.01
    lot_sizing_mode = "FIXED"
    min_rr = 1.5
    cooldown_sec = 30
    daily_loss_limit_pct = 3.0
    max_consec_losses_limit = 5
    max_spread_pips = 2.5
    commission_per_lot = 7.0

    print("\n--- FIXED VERSION CONFIGURATION ---")
    print(f"Min AI Confidence Threshold: {min_ai_conf_fixed * 100}% (Raised from 60%)")
    print(f"Allowed Sessions:            {allowed_sessions_fixed} (Disabled Asian session)")
    print(f"Strict H1 Trend Alignment:   ENFORCED")
    print(f"Disabled Setups on Gold:     Counter-Trend Mean Reversion")
    sys.stdout.flush()

    equity_curve = [initial_balance]
    trades = []
    rejected_signals = []
    position = None
    last_trade_exit_time = None
    current_consec_losses = 0
    max_consec_losses_seen = 0
    daily_realized_pnl = {}
    current_trading_day = None
    start_of_day_balance = initial_balance

    htf_ptrs = {tf: 0 for tf in ["M1", "M5", "H1", "H4"]}
    start_idx = max(40, min(50, len(m15_candles) - 1))
    total_bars = len(m15_candles) - start_idx
    print(f"Running fixed version simulation over {total_bars:,} bars...")
    sys.stdout.flush()

    for idx, i in enumerate(range(start_idx, len(m15_candles))):
        if idx > 0 and idx % 2000 == 0:
            print(f"  Progress: {idx}/{total_bars} bars ({int(idx/total_bars*100)}%) | Balance: ${balance:,.2f} | Trades: {len(trades)}")
            sys.stdout.flush()

        c = m15_candles[i]
        c_date = c.timestamp.date()

        if current_trading_day != c_date:
            current_trading_day = c_date
            start_of_day_balance = balance
            if c_date not in daily_realized_pnl:
                daily_realized_pnl[c_date] = 0.0

        sliced_htf = {}
        sliced_htf_indices = {}
        for tf, tf_list in htf_candles.items():
            ptr = htf_ptrs[tf]
            while ptr < len(tf_list) and tf_list[ptr].timestamp <= c.timestamp:
                ptr += 1
            htf_ptrs[tf] = ptr
            sliced_htf[tf] = tf_list[max(0, ptr - 60):ptr]
            sliced_htf_indices[tf] = ptr

        # 1. Update Open Position if Active
        if position is not None:
            side = position["side"]
            entry_p = position["entry_price"]
            sl_p = position["sl_price"]
            tp_p = position["tp_price"]
            trade_lots = position["lots"]
            sl_pips_dist = position["sl_pips_dist"]

            exit_price = None
            exit_reason = ""
            slippage = random.uniform(0.1, 0.3) * point_size

            if side == TradeSide.BUY:
                if c.low <= sl_p and c.high >= tp_p:
                    exit_price = sl_p - slippage
                    exit_reason = "STOP_LOSS_AMBIGUOUS"
                elif c.low <= sl_p:
                    exit_price = sl_p - slippage
                    exit_reason = "STOP_LOSS"
                elif c.high >= tp_p:
                    exit_price = tp_p - slippage
                    exit_reason = "TAKE_PROFIT"
            else: # SELL
                ask_high = c.high + (max_spread_pips * pip_size)
                ask_low = c.low + (max_spread_pips * pip_size)
                if ask_high >= sl_p and ask_low <= tp_p:
                    exit_price = sl_p + slippage
                    exit_reason = "STOP_LOSS_AMBIGUOUS"
                elif ask_high >= sl_p:
                    exit_price = sl_p + slippage
                    exit_reason = "STOP_LOSS"
                elif ask_low <= tp_p:
                    exit_price = tp_p - slippage
                    exit_reason = "TAKE_PROFIT"

            if exit_price is not None:
                pips = ((exit_price - entry_p) / pip_size) if side == TradeSide.BUY else ((entry_p - exit_price) / pip_size)
                trade_commission = trade_lots * commission_per_lot
                gross_pnl_usd = round(pips * (pip_value_per_lot * trade_lots), 2)
                net_pnl_usd = round(gross_pnl_usd - trade_commission, 2)
                
                balance += net_pnl_usd
                daily_realized_pnl[c_date] = daily_realized_pnl.get(c_date, 0.0) + net_pnl_usd
                duration_bars = i - position["entry_bar_idx"]
                achieved_rr = round(pips / sl_pips_dist, 2) if sl_pips_dist > 0 else 0.0

                if net_pnl_usd < 0:
                    current_consec_losses += 1
                    if current_consec_losses > max_consec_losses_seen:
                        max_consec_losses_seen = current_consec_losses
                elif net_pnl_usd > 0:
                    current_consec_losses = 0

                last_trade_exit_time = c.timestamp

                trades.append({
                    "id": len(trades) + 1,
                    "side": side.value if hasattr(side, "value") else str(side),
                    "symbol": symbol,
                    "entry_time": position["entry_time"].strftime("%Y-%m-%d %H:%M"),
                    "exit_time": c.timestamp.strftime("%Y-%m-%d %H:%M"),
                    "entry_price": round(entry_p, digits),
                    "exit_price": round(exit_price, digits),
                    "sl_price": round(sl_p, digits),
                    "tp_price": round(tp_p, digits),
                    "lots": trade_lots,
                    "gross_pnl_usd": gross_pnl_usd,
                    "net_pnl_usd": net_pnl_usd,
                    "pnl_pips": round(pips, 1),
                    "initial_rr": position["initial_rr"],
                    "achieved_rr": achieved_rr,
                    "duration_bars": duration_bars,
                    "exit_reason": exit_reason,
                    "confidence": position["confidence"],
                    "regime": position["regime"],
                    "setup_name": position["setup_name"],
                    "session": position["session"],
                    "m15_trend": position["m15_trend"],
                    "h1_trend": position["h1_trend"],
                    "h4_trend": position["h4_trend"],
                    "balance_after": round(balance, 2)
                })
                position = None

        # 2. Evaluate New Setup Opportunity when no position open
        if position is None and i < len(m15_candles) - 1:
            if balance <= 0:
                break

            # Session Filter Check
            sess_name = get_session_name(c.timestamp)
            if sess_name not in allowed_sessions_fixed:
                continue

            ticker_data = TickerData(
                symbol=symbol,
                bid=c.close,
                ask=c.close + (max_spread_pips * pip_size),
                spread_pips=max_spread_pips,
                spread_points=int(max_spread_pips * 10),
                point_size=point_size,
                digits=digits,
                timestamp=c.timestamp
            )

            window_slice = m15_candles[max(0, i + 1 - 300):i + 1]
            df_slice = full_m15_df.iloc[max(0, i + 1 - 300):i + 1] if full_m15_df is not None else None

            report = decision_engine.evaluate_trade_opportunity(
                symbol=symbol,
                candles=window_slice,
                ticker=ticker_data,
                timeframe="M15",
                htf_candles=sliced_htf,
                simulated_balance=balance,
                precomputed_df=df_slice
            )

            # Apply Raised Confidence Filter
            if report.decision in (TradeSide.BUY, TradeSide.SELL) and report.confidence >= min_ai_conf_fixed:
                h1_t = get_df_trend(full_htf_dfs.get("H1"), sliced_htf_indices.get("H1", 0))
                # Macro H1 Trend Filter Alignment Check
                if (report.decision == TradeSide.BUY and h1_t == "BEARISH") or (report.decision == TradeSide.SELL and h1_t == "BULLISH"):
                    continue

                # Filter out counter-trend mean reversion setups
                active_setups = decision_engine.evaluate_setups(
                    symbol, window_slice, ticker_data, "M15", sliced_htf, df_slice,
                    MarketStructureEngine.analyze(window_slice, "M15", symbol),
                    decision_engine.detect_market_regime(df_slice)
                )
                matching_setup = next((s for s in active_setups if s.get("active") and s.get("side") == report.decision), {})
                s_name = matching_setup.get("name", "")
                if "Counter-Trend" in s_name or "Midline" in s_name:
                    continue

                sl_distance = abs(report.suggested_entry - report.suggested_sl)
                sl_pips = round(sl_distance / pip_size, 1) if sl_distance > 0 else 20.0
                trade_lots = user_configured_lot

                passed, block_reason, block_details = RiskEngine.evaluate_trade_risk(
                    symbol=symbol,
                    current_time=c.timestamp,
                    current_spread_pips=max_spread_pips,
                    report_confidence=report.confidence,
                    report_rr_ratio=report.risk_reward_ratio,
                    account_balance=balance,
                    start_of_day_balance=start_of_day_balance,
                    daily_realized_pnl=daily_realized_pnl.get(c_date, 0.0),
                    consecutive_losses=current_consec_losses,
                    last_trade_exit_time=last_trade_exit_time,
                    trade_lots=trade_lots,
                    sl_pips=sl_pips,
                    pip_value_per_lot=pip_value_per_lot,
                    commission_per_lot=commission_per_lot,
                    max_spread_pips=max_spread_pips,
                    auto_trade_cooldown_seconds=cooldown_sec,
                    daily_loss_limit_pct=daily_loss_limit_pct,
                    max_consecutive_losses=max_consec_losses_limit,
                    min_ai_confidence=min_ai_conf_fixed,
                    min_risk_reward_ratio=min_rr,
                    lot_sizing_mode=lot_sizing_mode
                )

                if passed:
                    entry_p = report.suggested_entry
                    slippage = 0.1 * point_size
                    if report.decision == TradeSide.BUY:
                        entry_p += slippage
                    else:
                        entry_p -= slippage

                    position = {
                        "side": report.decision,
                        "entry_price": entry_p,
                        "sl_price": report.suggested_sl,
                        "tp_price": report.suggested_tp,
                        "lots": trade_lots,
                        "entry_time": c.timestamp,
                        "entry_bar_idx": i,
                        "sl_pips_dist": sl_pips,
                        "initial_rr": report.risk_reward_ratio,
                        "confidence": round(report.confidence * 100, 1),
                        "regime": decision_engine.detect_market_regime(df_slice),
                        "setup_name": s_name or "High-Confidence Trend Setup",
                        "session": sess_name,
                        "m15_trend": get_df_trend(full_m15_df, i + 1),
                        "h1_trend": h1_t,
                        "h4_trend": get_df_trend(full_htf_dfs.get("H4"), sliced_htf_indices.get("H4", 0))
                    }

        equity_curve.append(round(balance, 2))

    # Close open position at simulation end
    if position is not None:
        c = m15_candles[-1]
        side = position["side"]
        entry_p = position["entry_price"]
        exit_p = c.close if side == TradeSide.BUY else c.close + (max_spread_pips * pip_size)
        pips = ((exit_p - entry_p) / pip_size) if side == TradeSide.BUY else ((entry_p - exit_p) / pip_size)
        trade_commission = position["lots"] * commission_per_lot
        gross_pnl_usd = round(pips * (pip_value_per_lot * position["lots"]), 2)
        net_pnl_usd = round(gross_pnl_usd - trade_commission, 2)
        balance += net_pnl_usd
        achieved_rr = round(pips / position["sl_pips_dist"], 2) if position["sl_pips_dist"] > 0 else 0.0

        trades.append({
            "id": len(trades) + 1,
            "side": side.value if hasattr(side, "value") else str(side),
            "symbol": symbol,
            "entry_time": position["entry_time"].strftime("%Y-%m-%d %H:%M"),
            "exit_time": c.timestamp.strftime("%Y-%m-%d %H:%M"),
            "entry_price": round(entry_p, digits),
            "exit_price": round(exit_price, digits),
            "sl_price": round(position["sl_price"], digits),
            "tp_price": round(position["tp_price"], digits),
            "lots": position["lots"],
            "gross_pnl_usd": gross_pnl_usd,
            "net_pnl_usd": net_pnl_usd,
            "pnl_pips": round(pips, 1),
            "initial_rr": position["initial_rr"],
            "achieved_rr": achieved_rr,
            "duration_bars": (len(m15_candles) - 1) - position["entry_bar_idx"],
            "exit_reason": "SIMULATION_END",
            "confidence": position["confidence"],
            "regime": position["regime"],
            "setup_name": position["setup_name"],
            "session": position["session"],
            "m15_trend": position["m15_trend"],
            "h1_trend": position["h1_trend"],
            "h4_trend": position["h4_trend"],
            "balance_after": round(balance, 2)
        })
        equity_curve[-1] = round(balance, 2)

    # -------------------------------------------------------------
    # STATISTICAL METRICS CALCULATION (FIXED VERSION)
    # -------------------------------------------------------------
    total_trades = len(trades)
    wins = [t for t in trades if t["net_pnl_usd"] > 0]
    losses = [t for t in trades if t["net_pnl_usd"] < 0]

    winning_count = len(wins)
    losing_count = len(losses)
    win_rate = round((winning_count / total_trades) * 100.0, 1) if total_trades > 0 else 0.0

    gross_profit = sum(t["net_pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["net_pnl_usd"] for t in losses))
    net_pnl = round(balance - initial_balance, 2)
    profit_factor = round(gross_profit / (gross_loss + 1e-9), 2)

    avg_win = round(gross_profit / winning_count, 2) if winning_count > 0 else 0.0
    avg_loss = round(gross_loss / losing_count, 2) if losing_count > 0 else 0.0
    expectancy = round(net_pnl / total_trades, 2) if total_trades > 0 else 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / (peak or 1.0)
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = round(max_dd * 100.0, 2)

    print("\n" + "=" * 80)
    print("FIXED VERSION PERFORMANCE BENCHMARK (PHASE 3 VALIDATION)")
    print("=" * 80)
    print(f"Total Executed Trades:       {total_trades}")
    print(f"Winning Trades:              {winning_count} ({win_rate}%)")
    print(f"Losing Trades:               {losing_count} ({round(100.0 - win_rate, 1)}%)")
    print(f"Initial Balance:             ${initial_balance:,.2f}")
    print(f"Final Balance:               ${balance:,.2f}")
    print(f"Net PnL (After Costs):       ${net_pnl:,.2f} ({round((net_pnl/initial_balance)*100, 2)}%)")
    print(f"Profit Factor:               {profit_factor}")
    print(f"Average Win:                 ${avg_win:,.2f}")
    print(f"Average Loss:                -${avg_loss:,.2f}")
    print(f"Expectancy per Trade:        ${expectancy:,.2f}")
    print(f"Max Drawdown:                {max_dd_pct}%")
    print(f"Max Consecutive Losses:      {max_consec_losses_seen}")
    print("=" * 80)
    sys.stdout.flush()

    # Save results to json
    out_path = os.path.join(backend_dir, "scratch", "validation_fixed_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "net_pnl_usd": net_pnl,
            "profit_factor": profit_factor,
            "avg_win_usd": avg_win,
            "avg_loss_usd": avg_loss,
            "expectancy_usd": expectancy,
            "max_drawdown_pct": max_dd_pct,
            "max_consecutive_losses": max_consec_losses_seen,
            "trades": trades
        }, f, indent=2)

if __name__ == "__main__":
    run_validation_test()
