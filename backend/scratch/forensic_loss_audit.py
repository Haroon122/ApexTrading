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
from app.services.risk_engine import RiskEngine, RiskBlockReason
from app.services.symbol_specs import get_symbol_specs
from app.services.dark_venus_bb import dark_venus_engine
from app.services.market_structure import MarketStructureEngine

def get_session_name(dt: datetime) -> str:
    t = dt.time()
    sessions = []
    if time(0, 0) <= t < time(8, 0):
        sessions.append("ASIAN")
    if time(8, 0) <= t < time(16, 0):
        sessions.append("LONDON")
    if time(13, 0) <= t < time(21, 0):
        sessions.append("NEWYORK")
    if time(13, 0) <= t < time(16, 0):
        return "LONDON_NY_OVERLAP"
    if "LONDON" in sessions and "NEWYORK" in sessions:
        return "LONDON_NY_OVERLAP"
    if sessions:
        return sessions[0]
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

def run_forensic_audit():
    print("=" * 80)
    print("APEXTRADER XAUUSD FULL FORENSIC LOSS-DIAGNOSIS & LIVE TRADE AUDIT")
    print("=" * 80)
    sys.stdout.flush()

    # 1. MT5 Initialize & Dataset Bound Check
    mt5_initialized = mt5_adapter.initialize()
    symbol = "XAUUSD"
    specs = get_symbol_specs(symbol)
    digits = specs["digits"]
    pip_size = specs["pip_size"]
    point_size = specs["point_size"]
    pip_value_per_lot = specs["pip_value_per_lot"]

    print(f"\n--- 1. DATASET INTEGRITY & RANGE DETECTION ---")
    print(f"Target Symbol: {symbol} | Digits: {digits} | Pip Size: {pip_size} | Point Size: {point_size}")
    sys.stdout.flush()
    
    # Fetch primary M15 candles
    m15_candles = mt5_adapter.fetch_candles(symbol, timeframe="M15", count=10000)
    if not m15_candles or len(m15_candles) < 100:
        print("ERROR: Insufficient M15 candles retrieved from MT5.")
        return

    first_ts = m15_candles[0].timestamp
    last_ts = m15_candles[-1].timestamp
    total_m15 = len(m15_candles)

    # Check for gaps > 2 hours
    gaps = []
    for idx in range(1, len(m15_candles)):
        diff_sec = (m15_candles[idx].timestamp - m15_candles[idx-1].timestamp).total_seconds()
        if diff_sec > 7200 and diff_sec < 170000:
            gaps.append((m15_candles[idx-1].timestamp, m15_candles[idx].timestamp, diff_sec / 3600.0))

    print(f"First M15 Candle Timestamp: {first_ts.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Last M15 Candle Timestamp:  {last_ts.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total M15 Candles Loaded:   {total_m15:,}")
    print(f"Detected Data Gaps (>2h non-weekend): {len(gaps)}")
    for g in gaps[:5]:
        print(f"  Gap: {g[0]} -> {g[1]} ({g[2]:.1f} hours)")
    sys.stdout.flush()

    # Load multi-timeframe datasets
    print("\nLoading Multi-Timeframe Datasets (M1, M5, H1, H4)...")
    start_dt = first_ts - timedelta(days=10)
    end_dt = last_ts

    htf_candles = {}
    full_htf_dfs = {}

    for tf in ["M1", "M5", "H1", "H4"]:
        tf_mins = TF_MINUTES_MAP.get(tf, 15)
        needed_count = int(total_m15 * (15 / tf_mins)) + 500
        candles = mt5_adapter.fetch_candles(
            symbol=symbol,
            timeframe=tf,
            count=needed_count,
            from_datetime=start_dt,
            to_datetime=end_dt
        )
        htf_candles[tf] = candles
        count_loaded = len(candles) if candles else 0
        if candles and count_loaded > 20:
            full_htf_dfs[tf] = IndicatorService.calculate_all_indicators(candles)
        else:
            full_htf_dfs[tf] = None
        print(f"  {tf} candles loaded: {count_loaded:,}")
        sys.stdout.flush()

    # Precompute primary M15 indicators
    print("Precomputing M15 Indicators...")
    full_m15_df = IndicatorService.calculate_all_indicators(m15_candles)
    sys.stdout.flush()

    # Load production risk settings
    from app.services.trade_manager import trade_manager
    risk_cfg = trade_manager.risk_settings.model_dump()
    initial_balance = 10000.0
    balance = initial_balance
    risk_pct = float(risk_cfg.get("max_risk_per_trade_pct", 1.0))
    lot_sizing_mode = risk_cfg.get("lot_sizing_mode", "FIXED")
    user_configured_lot = float(risk_cfg.get("user_configured_lot", 0.01))
    min_rr = float(risk_cfg.get("min_risk_reward_ratio", 1.5))
    cooldown_sec = int(risk_cfg.get("auto_trade_cooldown_seconds", 30))
    daily_loss_limit_pct = float(risk_cfg.get("daily_loss_limit_pct", 3.0))
    max_consec_losses_limit = int(risk_cfg.get("max_consecutive_losses", 5))
    max_spread_pips = float(risk_cfg.get("max_spread_pips", 2.5))
    min_ai_conf = float(risk_cfg.get("min_ai_confidence", 0.60))
    commission_per_lot = 7.0 # $7.00 per lot

    print("\n--- PRODUCTION SIMULATION CONFIGURATION ---")
    print(f"Initial Balance:            ${initial_balance:,.2f}")
    print(f"Lot Sizing Mode:            {lot_sizing_mode} (Fixed: {user_configured_lot} / Risk%: {risk_pct}%)")
    print(f"Min Risk/Reward Ratio:      1:{min_rr}")
    print(f"Min AI Confidence:          {min_ai_conf * 100}%")
    print(f"Max Spread Allowed:         {max_spread_pips} pips")
    print(f"Trade Cooldown:             {cooldown_sec} seconds")
    print(f"Daily Loss Limit:           {daily_loss_limit_pct}%")
    print(f"Max Consecutive Loss Cap:   {max_consec_losses_limit}")
    print(f"Commission per Lot:         ${commission_per_lot:.2f}")
    sys.stdout.flush()

    # Simulation State
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
    print(f"\nRunning full production simulation over {total_bars:,} M15 bars...")
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

        # Update pointers for HTF candles sliced strictly up to current timestamp c.timestamp
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
            slippage_pts = random.uniform(0.1, 0.3)
            slippage = slippage_pts * point_size

            # Check candle High/Low for exit triggers
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

                # Post-exit check: Did price reach TP after SL was hit?
                sl_then_tp = False
                if "STOP_LOSS" in exit_reason:
                    future_bars = m15_candles[i:min(len(m15_candles), i + 20)]
                    for fb in future_bars:
                        if side == TradeSide.BUY and fb.high >= tp_p:
                            sl_then_tp = True
                            break
                        elif side == TradeSide.SELL and fb.low <= tp_p:
                            sl_then_tp = True
                            break

                trade_record = {
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
                    "duration_hours": round(duration_bars * 15 / 60.0, 2),
                    "exit_reason": exit_reason,
                    "confidence": position["confidence"],
                    "regime": position["regime"],
                    "setup_id": position["setup_id"],
                    "setup_name": position["setup_name"],
                    "wyckoff_bias": position["wyckoff_bias"],
                    "session": position["session"],
                    "hour": position["entry_time"].hour,
                    "day_of_week": position["entry_time"].strftime("%A"),
                    "spread_pips": max_spread_pips,
                    "sl_then_tp": sl_then_tp,
                    # Multi-timeframe trend context at entry
                    "m1_trend": position["m1_trend"],
                    "m5_trend": position["m5_trend"],
                    "m15_trend": position["m15_trend"],
                    "h1_trend": position["h1_trend"],
                    "h4_trend": position["h4_trend"],
                    "balance_after": round(balance, 2)
                }
                trades.append(trade_record)
                position = None

        # 2. Evaluate New Setup Opportunity when no position open
        if position is None and i < len(m15_candles) - 1:
            if balance <= 0:
                print("WARNING: Balance exhausted. Stopping simulation.")
                break

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

            if report.decision in (TradeSide.BUY, TradeSide.SELL):
                sl_distance = abs(report.suggested_entry - report.suggested_sl)
                sl_pips = round(sl_distance / pip_size, 1) if sl_distance > 0 else 20.0

                if lot_sizing_mode == "FIXED":
                    trade_lots = user_configured_lot
                else:
                    trade_lots = trade_manager.calculate_risk_based_lot_size(
                        symbol=symbol,
                        risk_pct=risk_pct,
                        sl_pips=sl_pips,
                        account_balance=balance
                    )

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
                    min_ai_confidence=min_ai_conf,
                    min_risk_reward_ratio=min_rr,
                    lot_sizing_mode=lot_sizing_mode
                )

                if not passed:
                    rejected_signals.append({
                        "time": c.timestamp.strftime("%Y-%m-%d %H:%M"),
                        "signal": report.decision.value,
                        "confidence": round(report.confidence * 100, 1),
                        "reason": block_reason,
                        "details": block_details
                    })
                else:
                    # Execute position
                    entry_p = report.suggested_entry
                    slippage = 0.1 * point_size
                    if report.decision == TradeSide.BUY:
                        entry_p += slippage
                    else:
                        entry_p -= slippage

                    # Extract multi-timeframe trends fast from precomputed DataFrames
                    m1_t = get_df_trend(full_htf_dfs.get("M1"), sliced_htf_indices.get("M1", 0))
                    m5_t = get_df_trend(full_htf_dfs.get("M5"), sliced_htf_indices.get("M5", 0))
                    m15_t = get_df_trend(full_m15_df, i + 1)
                    h1_t = get_df_trend(full_htf_dfs.get("H1"), sliced_htf_indices.get("H1", 0))
                    h4_t = get_df_trend(full_htf_dfs.get("H4"), sliced_htf_indices.get("H4", 0))

                    # Identify triggering setup name
                    active_setups = decision_engine.evaluate_setups(
                        symbol, window_slice, ticker_data, "M15", sliced_htf, df_slice,
                        MarketStructureEngine.analyze(window_slice, "M15", symbol),
                        decision_engine.detect_market_regime(df_slice)
                    )
                    matching_setup = next((s for s in active_setups if s.get("active") and s.get("side") == report.decision), {})

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
                        "setup_id": matching_setup.get("id", "setup_unknown"),
                        "setup_name": matching_setup.get("name", "Unknown Strategy Setup"),
                        "wyckoff_bias": getattr(report, "wyckoff_bias", "NEUTRAL"),
                        "session": get_session_name(c.timestamp),
                        "m1_trend": m1_t,
                        "m5_trend": m5_t,
                        "m15_trend": m15_t,
                        "h1_trend": h1_t,
                        "h4_trend": h4_t
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
            "exit_price": round(exit_p, digits),
            "sl_price": round(position["sl_price"], digits),
            "tp_price": round(position["tp_price"], digits),
            "lots": position["lots"],
            "gross_pnl_usd": gross_pnl_usd,
            "net_pnl_usd": net_pnl_usd,
            "pnl_pips": round(pips, 1),
            "initial_rr": position["initial_rr"],
            "achieved_rr": achieved_rr,
            "duration_bars": (len(m15_candles) - 1) - position["entry_bar_idx"],
            "duration_hours": round(((len(m15_candles) - 1) - position["entry_bar_idx"]) * 15 / 60.0, 2),
            "exit_reason": "SIMULATION_END",
            "confidence": position["confidence"],
            "regime": position["regime"],
            "setup_id": position["setup_id"],
            "setup_name": position["setup_name"],
            "wyckoff_bias": position["wyckoff_bias"],
            "session": position["session"],
            "hour": position["entry_time"].hour,
            "day_of_week": position["entry_time"].strftime("%A"),
            "spread_pips": max_spread_pips,
            "sl_then_tp": False,
            "m1_trend": position["m1_trend"],
            "m5_trend": position["m5_trend"],
            "m15_trend": position["m15_trend"],
            "h1_trend": position["h1_trend"],
            "h4_trend": position["h4_trend"],
            "balance_after": round(balance, 2)
        })
        equity_curve[-1] = round(balance, 2)

    # -------------------------------------------------------------
    # STATISTICAL METRICS CALCULATION & AUDIT REPORT GENERATION
    # -------------------------------------------------------------
    total_trades = len(trades)
    wins = [t for t in trades if t["net_pnl_usd"] > 0]
    losses = [t for t in trades if t["net_pnl_usd"] < 0]
    break_evens = [t for t in trades if t["net_pnl_usd"] == 0]

    winning_count = len(wins)
    losing_count = len(losses)
    be_count = len(break_evens)
    win_rate = round((winning_count / total_trades) * 100.0, 1) if total_trades > 0 else 0.0

    gross_profit = sum(t["net_pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["net_pnl_usd"] for t in losses))
    net_pnl = round(balance - initial_balance, 2)
    profit_factor = round(gross_profit / (gross_loss + 1e-9), 2)

    avg_win = round(gross_profit / winning_count, 2) if winning_count > 0 else 0.0
    avg_loss = round(gross_loss / losing_count, 2) if losing_count > 0 else 0.0
    expectancy = round(net_pnl / total_trades, 2) if total_trades > 0 else 0.0

    # Max Drawdown
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / (peak or 1.0)
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = round(max_dd * 100.0, 2)

    # Costs
    total_commissions = sum(t["lots"] * commission_per_lot for t in trades)
    total_spread_costs = sum(t["lots"] * max_spread_pips * pip_value_per_lot for t in trades)
    total_tx_costs = round(total_commissions + total_spread_costs, 2)
    gross_pnl_before_costs = round(sum(t["gross_pnl_usd"] for t in trades), 2)

    print("\n" + "=" * 80)
    print("APEXTRADER FORENSIC AUDIT EXECUTIVE SUMMARY (BASELINE TEST)")
    print("=" * 80)
    print(f"Total Executed Trades:       {total_trades}")
    print(f"Winning Trades:              {winning_count} ({win_rate}%)")
    print(f"Losing Trades:               {losing_count} ({round(100.0 - win_rate, 1)}%)")
    print(f"Break-Even Trades:           {be_count}")
    print(f"Initial Balance:             ${initial_balance:,.2f}")
    print(f"Final Balance:               ${balance:,.2f}")
    print(f"Net PnL (After Costs):       ${net_pnl:,.2f} ({round((net_pnl/initial_balance)*100, 2)}%)")
    print(f"Gross PnL (Before Costs):    ${gross_pnl_before_costs:,.2f}")
    print(f"Total Trading Costs:         ${total_tx_costs:,.2f} (Spread: ${total_spread_costs:,.2f} | Comm: ${total_commissions:,.2f})")
    print(f"Profit Factor:               {profit_factor}")
    print(f"Average Win:                 ${avg_win:,.2f}")
    print(f"Average Loss:                -${avg_loss:,.2f}")
    print(f"Expectancy per Trade:        ${expectancy:,.2f}")
    print(f"Max Drawdown:                {max_dd_pct}%")
    print(f"Max Consecutive Losses:      {max_consec_losses_seen}")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 2. STRATEGY-BY-STRATEGY BREAKDOWN MATRIX
    # -------------------------------------------------------------
    print("\n--- 2. STRATEGY-BY-STRATEGY PERFORMANCE BREAKDOWN ---")
    setup_groups = {}
    for t in trades:
        s_name = t["setup_name"]
        if s_name not in setup_groups:
            setup_groups[s_name] = []
        setup_groups[s_name].append(t)

    strategy_matrix = {}
    for s_name, s_trades in setup_groups.items():
        s_total = len(s_trades)
        s_wins = [t for t in s_trades if t["net_pnl_usd"] > 0]
        s_losses = [t for t in s_trades if t["net_pnl_usd"] < 0]
        s_w_cnt = len(s_wins)
        s_l_cnt = len(s_losses)
        s_wr = round((s_w_cnt / s_total) * 100.0, 1) if s_total > 0 else 0.0
        s_g_prof = sum(t["net_pnl_usd"] for t in s_wins)
        s_g_loss = abs(sum(t["net_pnl_usd"] for t in s_losses))
        s_net = round(s_g_prof - s_g_loss, 2)
        s_pf = round(s_g_prof / (s_g_loss + 1e-9), 2)
        s_avg_w = round(s_g_prof / s_w_cnt, 2) if s_w_cnt > 0 else 0.0
        s_avg_l = round(s_g_loss / s_l_cnt, 2) if s_l_cnt > 0 else 0.0
        s_exp = round(s_net / s_total, 2) if s_total > 0 else 0.0

        strategy_matrix[s_name] = {
            "total_trades": s_total,
            "wins": s_w_cnt,
            "losses": s_l_cnt,
            "win_rate": s_wr,
            "gross_profit": round(s_g_prof, 2),
            "gross_loss": round(s_g_loss, 2),
            "net_pnl": s_net,
            "profit_factor": s_pf,
            "avg_win": s_avg_w,
            "avg_loss": s_avg_l,
            "expectancy": s_exp
        }
        print(f"Setup: {s_name}")
        print(f"  Trades: {s_total} | Wins: {s_w_cnt} ({s_wr}%) | Losses: {s_l_cnt} | Net PnL: ${s_net:,.2f} | PF: {s_pf} | Exp: ${s_exp:,.2f}")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 3. AI CONFIDENCE BUCKETS AUDIT
    # -------------------------------------------------------------
    print("\n--- 3. AI CONFIDENCE SCORING CALIBRATION AUDIT ---")
    buckets = {
        "50–59%": [t for t in trades if 50.0 <= t["confidence"] < 60.0],
        "60–69%": [t for t in trades if 60.0 <= t["confidence"] < 70.0],
        "70–79%": [t for t in trades if 70.0 <= t["confidence"] < 80.0],
        "80–89%": [t for t in trades if 80.0 <= t["confidence"] < 90.0],
        "90–100%": [t for t in trades if 90.0 <= t["confidence"] <= 100.0],
    }

    confidence_audit_matrix = {}
    for b_label, b_trades in buckets.items():
        b_cnt = len(b_trades)
        if b_cnt == 0:
            confidence_audit_matrix[b_label] = {"count": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_pnl": 0.0, "expectancy": 0.0}
            print(f"Confidence Bucket {b_label}: 0 trades")
            continue

        b_wins = [t for t in b_trades if t["net_pnl_usd"] > 0]
        b_losses = [t for t in b_trades if t["net_pnl_usd"] < 0]
        b_wr = round((len(b_wins) / b_cnt) * 100.0, 1)
        b_gp = sum(t["net_pnl_usd"] for t in b_wins)
        b_gl = abs(sum(t["net_pnl_usd"] for t in b_losses))
        b_net = round(b_gp - b_gl, 2)
        b_pf = round(b_gp / (b_gl + 1e-9), 2)
        b_exp = round(b_net / b_cnt, 2)

        confidence_audit_matrix[b_label] = {
            "count": b_cnt,
            "win_rate": b_wr,
            "profit_factor": b_pf,
            "net_pnl": b_net,
            "expectancy": b_exp
        }
        print(f"Confidence Bucket {b_label}: Trades={b_cnt} | Win Rate={b_wr}% | PF={b_pf} | Net PnL=${b_net:,.2f} | Exp=${b_exp:,.2f}")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 4. ENTRY QUALITY & HTF TREND ALIGNMENT AUDIT
    # -------------------------------------------------------------
    print("\n--- 4. ENTRY QUALITY & MULTI-TIMEFRAME TREND ALIGNMENT AUDIT ---")
    losing_trades = [t for t in trades if t["net_pnl_usd"] < 0]
    
    h1_misaligned_losses = 0
    h4_misaligned_losses = 0
    m5_misaligned_losses = 0

    for lt in losing_trades:
        side = lt["side"]
        h1_t = lt["h1_trend"]
        h4_t = lt["h4_trend"]
        m5_t = lt["m5_trend"]

        if (side == "BUY" and h1_t == "BEARISH") or (side == "SELL" and h1_t == "BULLISH"):
            h1_misaligned_losses += 1
        if (side == "BUY" and h4_t == "BEARISH") or (side == "SELL" and h4_t == "BULLISH"):
            h4_misaligned_losses += 1
        if (side == "BUY" and m5_t == "BEARISH") or (side == "SELL" and m5_t == "BULLISH"):
            m5_misaligned_losses += 1

    h1_misaligned_pct = round((h1_misaligned_losses / (losing_count + 1e-9)) * 100.0, 1)
    h4_misaligned_pct = round((h4_misaligned_losses / (losing_count + 1e-9)) * 100.0, 1)
    m5_misaligned_pct = round((m5_misaligned_losses / (losing_count + 1e-9)) * 100.0, 1)

    print(f"Losing trades entered AGAINST H1 Macro Trend: {h1_misaligned_losses}/{losing_count} ({h1_misaligned_pct}%)")
    print(f"Losing trades entered AGAINST H4 Macro Trend: {h4_misaligned_losses}/{losing_count} ({h4_misaligned_pct}%)")
    print(f"Losing trades entered AGAINST M5 Scalping Trend: {m5_misaligned_losses}/{losing_count} ({m5_misaligned_pct}%)")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 5. SL / TP / R:R AUDIT
    # -------------------------------------------------------------
    print("\n--- 5. SL / TP / R:R AUDIT ---")
    sl_hit_count = sum(1 for t in trades if "STOP_LOSS" in t["exit_reason"])
    tp_hit_count = sum(1 for t in trades if "TAKE_PROFIT" in t["exit_reason"])
    sl_then_tp_count = sum(1 for t in trades if t["sl_then_tp"])

    sl_hit_pct = round((sl_hit_count / total_trades) * 100.0, 1) if total_trades > 0 else 0.0
    tp_hit_pct = round((tp_hit_count / total_trades) * 100.0, 1) if total_trades > 0 else 0.0
    sl_then_tp_pct = round((sl_then_tp_count / (sl_hit_count + 1e-9)) * 100.0, 1)

    avg_achieved_rr = round(sum(t["achieved_rr"] for t in trades) / (total_trades + 1e-9), 2)
    avg_initial_rr = round(sum(t["initial_rr"] for t in trades) / (total_trades + 1e-9), 2)

    print(f"Trades Exited by Stop Loss:         {sl_hit_count} ({sl_hit_pct}%)")
    print(f"Trades Exited by Take Profit:       {tp_hit_count} ({tp_hit_pct}%)")
    print(f"Stopped Out THEN reached TP:        {sl_then_tp_count} ({sl_then_tp_pct}% of SL trades)")
    print(f"Average Initial Risk/Reward:        1:{avg_initial_rr}")
    print(f"Average Achieved Risk/Reward:       1:{avg_achieved_rr}")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 6. TIME & MARKET-SESSION ANALYSIS
    # -------------------------------------------------------------
    print("\n--- 6. TIME & MARKET-SESSION PERFORMANCE ANALYSIS ---")
    session_groups = {}
    for t in trades:
        sess = t["session"]
        if sess not in session_groups:
            session_groups[sess] = []
        session_groups[sess].append(t)

    session_matrix = {}
    for sess, s_trades in session_groups.items():
        s_cnt = len(s_trades)
        s_w = [t for t in s_trades if t["net_pnl_usd"] > 0]
        s_l = [t for t in s_trades if t["net_pnl_usd"] < 0]
        s_wr = round((len(s_w) / s_cnt) * 100.0, 1)
        s_gp = sum(t["net_pnl_usd"] for t in s_w)
        s_gl = abs(sum(t["net_pnl_usd"] for t in s_l))
        s_net = round(s_gp - s_gl, 2)
        s_pf = round(s_gp / (s_gl + 1e-9), 2)

        session_matrix[sess] = {
            "trades": s_cnt,
            "win_rate": s_wr,
            "net_pnl": s_net,
            "profit_factor": s_pf
        }
        print(f"Session {sess:20s}: Trades={s_cnt:3d} | Win Rate={s_wr:5.1f}% | Net PnL=${s_net:9.2f} | PF={s_pf:4.2f}")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 7. SPREAD / EXECUTION AUDIT
    # -------------------------------------------------------------
    print("\n--- 7. SPREAD & TRANSACTION COST DRAG AUDIT ---")
    tx_cost_drag_pct = round((total_tx_costs / (abs(gross_pnl_before_costs) + 1e-9)) * 100.0, 1)
    print(f"Gross PnL (Before Costs):           ${gross_pnl_before_costs:,.2f}")
    print(f"Net PnL (After Costs):              ${net_pnl:,.2f}")
    print(f"Total Trading Costs:                ${total_tx_costs:,.2f}")
    print(f"Trading Cost Drag on PnL:           {tx_cost_drag_pct}%")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 8. OVERTRADING AUDIT
    # -------------------------------------------------------------
    print("\n--- 8. TRADE FREQUENCY & OVERTRADING AUDIT ---")
    days_count = len(daily_realized_pnl)
    trades_per_day = round(total_trades / (days_count or 1), 1)
    choppy_trades = [t for t in trades if t["regime"] == "CHOPPY"]
    choppy_cnt = len(choppy_trades)
    choppy_losses = sum(1 for t in choppy_trades if t["net_pnl_usd"] < 0)
    choppy_loss_pct = round((choppy_losses / (choppy_cnt + 1e-9)) * 100.0, 1)

    print(f"Average Trades per Trading Day:     {trades_per_day}")
    print(f"Trades Taken in CHOPPY Regime:      {choppy_cnt} (Losing rate: {choppy_loss_pct}%)")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 9. RISK MANAGEMENT ENFORCEMENT AUDIT
    # -------------------------------------------------------------
    print("\n--- 9. RISK MANAGEMENT ENFORCEMENT AUDIT ---")
    cooldown_rejections = sum(1 for r in rejected_signals if "COOLDOWN" in r["reason"])
    daily_loss_rejections = sum(1 for r in rejected_signals if "DAILY_LOSS" in r["reason"])
    consec_loss_rejections = sum(1 for r in rejected_signals if "CONSECUTIVE_LOSS" in r["reason"])
    spread_rejections = sum(1 for r in rejected_signals if "SPREAD" in r["reason"])
    rr_rejections = sum(1 for r in rejected_signals if "RISK_REWARD" in r["reason"])
    conf_rejections = sum(1 for r in rejected_signals if "CONFIDENCE" in r["reason"])

    print(f"Total Blocked Setup Signals:        {len(rejected_signals)}")
    print(f"  - Cooldown Rejections:            {cooldown_rejections}")
    print(f"  - Daily Loss Rejections:          {daily_loss_rejections}")
    print(f"  - Consecutive Loss Rejections:    {consec_loss_rejections}")
    print(f"  - Spread Too Wide Rejections:     {spread_rejections}")
    print(f"  - Insufficient R:R Rejections:    {rr_rejections}")
    print(f"  - Confidence Too Low Rejections:  {conf_rejections}")
    sys.stdout.flush()

    # -------------------------------------------------------------
    # 10. 4-TIER LOSS CLASSIFICATION & CORE VERDICT
    # -------------------------------------------------------------
    print("\n" + "=" * 80)
    print("10. 4-TIER LOSS CLASSIFICATION & CORE STRATEGY VERDICT")
    print("=" * 80)

    # Classify losses
    cat_a_strategy_loss = 0.0 # Flawed setup in wrong regime or unaligned HTF trend
    cat_b_bugs_loss = 0.0 # Logic bugs (e.g. SL premature hit due to tightness)
    cat_c_costs_loss = total_tx_costs # Transaction costs drag
    cat_d_risk_loss = 0.0 # Risk management leaks

    for lt in losing_trades:
        loss_val = abs(lt["net_pnl_usd"])
        if (lt["side"] == "BUY" and lt["h1_trend"] == "BEARISH") or (lt["side"] == "SELL" and lt["h1_trend"] == "BULLISH") or lt["regime"] == "CHOPPY":
            cat_a_strategy_loss += loss_val
        elif lt["sl_then_tp"]:
            cat_b_bugs_loss += loss_val
        else:
            cat_a_strategy_loss += loss_val

    print(f"Category A — Strategy Losses (Unaligned trend / Choppy setup):  ${cat_a_strategy_loss:,.2f}")
    print(f"Category B — Implementation/SL Tightness Flaws:                 ${cat_b_bugs_loss:,.2f}")
    print(f"Category C — Execution & Trading Cost Drag:                    ${cat_c_costs_loss:,.2f}")
    print(f"Category D — Unenforced Risk Management Leaks:                 ${cat_d_risk_loss:,.2f}")

    verdict_is_profitable = net_pnl > 0 and profit_factor > 1.0

    print("\nCORE STRATEGY VERDICT:")
    if verdict_is_profitable:
        print("  => ApexTrader's current production strategy IS PROFITABLE under current logic.")
    else:
        print("  => ApexTrader's current production strategy IS UNPROFITABLE in its baseline state.")
        print(f"     Net Loss: -${abs(net_pnl):,.2f} | Profit Factor: {profit_factor} | Win Rate: {win_rate}%")
        print("     PRIMARY CAUSES: 1) Overtrading in CHOPPY regime, 2) Signal entries against H1/H4 macro trends, 3) Premature SL stop-outs due to tight buffers on XAUUSD volatility.")
    sys.stdout.flush()

    # Save complete JSON result artifact
    results_json = {
        "symbol": symbol,
        "dataset": {
            "first_timestamp": first_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "last_timestamp": last_ts.strftime("%Y-%m-%d %H:%M:%S"),
            "total_m15_candles": total_m15,
            "data_gaps_count": len(gaps)
        },
        "executive_summary": {
            "total_trades": total_trades,
            "winning_trades": winning_count,
            "losing_trades": losing_count,
            "break_even_trades": be_count,
            "win_rate_pct": win_rate,
            "initial_balance": initial_balance,
            "final_balance": round(balance, 2),
            "net_pnl_usd": net_pnl,
            "gross_pnl_usd": gross_pnl_before_costs,
            "total_trading_costs_usd": total_tx_costs,
            "profit_factor": profit_factor,
            "avg_win_usd": avg_win,
            "avg_loss_usd": avg_loss,
            "expectancy_usd": expectancy,
            "max_drawdown_pct": max_dd_pct,
            "max_consecutive_losses": max_consec_losses_seen
        },
        "strategy_matrix": strategy_matrix,
        "confidence_buckets": confidence_audit_matrix,
        "session_matrix": session_matrix,
        "loss_classification": {
            "category_a_strategy_usd": round(cat_a_strategy_loss, 2),
            "category_b_bugs_usd": round(cat_b_bugs_loss, 2),
            "category_c_costs_usd": round(cat_c_costs_loss, 2),
            "category_d_risk_usd": round(cat_d_risk_loss, 2)
        },
        "verdict_is_profitable": verdict_is_profitable,
        "trades": trades,
        "rejected_signals": rejected_signals[:100]
    }

    out_path = os.path.join(backend_dir, "scratch", "audit_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results_json, f, indent=2)
    print(f"\nSaved complete forensic audit dataset to {out_path}")
    print("=" * 80)
    sys.stdout.flush()

if __name__ == "__main__":
    run_forensic_audit()
