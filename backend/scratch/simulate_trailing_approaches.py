import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sqlite3
from datetime import datetime, timedelta
import pandas as pd

# Import MT5 adapter to fetch exact candle price action during trade duration
from app.adapters.mt5_adapter import mt5_adapter
from app.services.indicators import IndicatorService

db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "trading_memory.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get all 39 trades executed today
cur.execute("""
    SELECT trade_id, symbol, side, lots, entry_price, exit_price, sl_price, tp_price, pnl_usd, pnl_pips, win_loss, exit_reason, open_timestamp, close_timestamp 
    FROM trade_history_memory 
    WHERE win_loss IN ('WIN', 'LOSS') AND symbol = 'XAUUSD' AND (close_timestamp LIKE '2026-08-18%' OR open_timestamp LIKE '2026-08-18%')
    ORDER BY close_timestamp ASC
""")
trades = [dict(r) for r in cur.fetchall()]

print(f"Loaded {len(trades)} trades for simulation.\n")

# Connect to MT5 if available to fetch M1 candles for exact trajectory analysis
mt5_connected = mt5_adapter.initialize()

def analyze_trade(trade):
    trade_id = trade['trade_id']
    symbol = trade['symbol']
    side = trade['side']
    lots = trade['lots'] or 0.10
    entry = trade['entry_price']
    initial_sl = float(trade['sl_price'] or 0.0)
    initial_tp = float(trade['tp_price'] or 0.0)
    actual_pnl = float(trade['pnl_usd'] or 0.0)
    actual_exit = float(trade['exit_price'] or 0.0)
    
    open_dt = datetime.fromisoformat(trade['open_timestamp'])
    close_dt = datetime.fromisoformat(trade['close_timestamp']) if trade['close_timestamp'] else open_dt + timedelta(minutes=15)
    
    if initial_sl <= 0 or initial_tp <= 0:
        sl_dist = 3.00 # $3.00 Gold default
        tp_dist = sl_dist * 1.5 # 1.5 R:R = $4.50
        initial_sl = entry - sl_dist if side == "BUY" else entry + sl_dist
        initial_tp = entry + tp_dist if side == "BUY" else entry - tp_dist
        
    sl_dist = abs(entry - initial_sl)
    tp_dist = abs(initial_tp - entry)
    planned_rr = tp_dist / sl_dist if sl_dist > 0 else 1.5

    # Fetch M1 candles during trade lifetime
    candles = mt5_adapter.fetch_candles(symbol, timeframe="M1", count=100, from_datetime=open_dt - timedelta(minutes=1), to_datetime=close_dt + timedelta(minutes=30))
    
    if not candles:
        # If candles unavailable, estimate from actual exit
        max_favorable = max(abs(actual_exit - entry), tp_dist * 0.8 if actual_pnl > 0 else 0)
        max_adverse = sl_dist if actual_pnl < 0 else abs(actual_exit - entry)
    else:
        # Calculate max favorable excursion (MFE) and max adverse excursion (MAE)
        if side == "BUY":
            max_high = max(c.high for c in candles if c.timestamp >= open_dt and c.timestamp <= close_dt + timedelta(minutes=5))
            min_low = min(c.low for c in candles if c.timestamp >= open_dt and c.timestamp <= close_dt + timedelta(minutes=5))
            max_favorable = max_high - entry
            max_adverse = entry - min_low
        else:
            min_low = min(c.low for c in candles if c.timestamp >= open_dt and c.timestamp <= close_dt + timedelta(minutes=5))
            max_high = max(c.high for c in candles if c.timestamp >= open_dt and c.timestamp <= close_dt + timedelta(minutes=5))
            max_favorable = entry - min_low
            max_adverse = max_high - entry

    # -------------------------------------------------------------
    # SIMULATION APPROACH A: No Trailing Stop (Pure Initial SL vs TP)
    # -------------------------------------------------------------
    # Did price reach TP before hitting initial SL?
    reached_tp = max_favorable >= tp_dist
    hit_sl = max_adverse >= sl_dist
    
    if reached_tp:
        pnl_approach_a = (tp_dist * lots * 100.0) if symbol == "XAUUSD" else (tp_dist * lots * 100000.0)
        outcome_a = "TP_HIT"
    elif hit_sl:
        pnl_approach_a = -(sl_dist * lots * 100.0) if symbol == "XAUUSD" else -(sl_dist * lots * 100000.0)
        outcome_a = "SL_HIT"
    else:
        # Closed at actual exit
        pnl_approach_a = actual_pnl
        outcome_a = "MANUAL_EXIT"

    # -------------------------------------------------------------
    # SIMULATION APPROACH B: Wide Trailing Stop (40 pips = $4.00 Gold)
    # -------------------------------------------------------------
    trail_dist_b = 4.00 # $4.00 Gold price
    if max_favorable >= trail_dist_b:
        trailed_exit_b = (entry + max_favorable - trail_dist_b) if side == "BUY" else (entry - max_favorable + trail_dist_b)
        pnl_approach_b = max(actual_pnl, ((trailed_exit_b - entry) * lots * 100.0) if side == "BUY" else ((entry - trailed_exit_b) * lots * 100.0))
        outcome_b = "TRAILED_WIDE"
    elif reached_tp:
        pnl_approach_b = (tp_dist * lots * 100.0)
        outcome_b = "TP_HIT"
    elif actual_pnl < 0:
        pnl_approach_b = actual_pnl
        outcome_b = "SL_HIT"
    else:
        pnl_approach_b = actual_pnl
        outcome_b = "ACTUAL"

    # -------------------------------------------------------------
    # SIMULATION APPROACH C: Delayed Trailing (Activate ONLY at >= 1.2R profit with 2.0x ATR trailing distance)
    # -------------------------------------------------------------
    activation_threshold_c = sl_dist * 1.2 # 1.2R profit required to activate
    trail_dist_c = 3.00 # 2.0x ATR (~$3.00 Gold price)
    
    if max_favorable >= tp_dist:
        pnl_approach_c = (tp_dist * lots * 100.0)
        outcome_c = "TP_HIT"
    elif max_favorable >= activation_threshold_c:
        trailed_exit_c = (entry + max_favorable - trail_dist_c) if side == "BUY" else (entry - max_favorable + trail_dist_c)
        pnl_approach_c = max(0.0, ((trailed_exit_c - entry) * lots * 100.0) if side == "BUY" else ((entry - trailed_exit_c) * lots * 100.0))
        outcome_c = "TRAILED_DELAYED"
    elif actual_pnl < 0:
        pnl_approach_c = actual_pnl
        outcome_c = "SL_HIT"
    else:
        pnl_approach_c = actual_pnl
        outcome_c = "ACTUAL"

    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "actual_pnl": actual_pnl,
        "max_favorable": max_favorable,
        "sl_dist": sl_dist,
        "tp_dist": tp_dist,
        "reached_tp": reached_tp,
        "pnl_a": pnl_approach_a,
        "outcome_a": outcome_a,
        "pnl_b": pnl_approach_b,
        "outcome_b": outcome_b,
        "pnl_c": pnl_approach_c,
        "outcome_c": outcome_c
    }

results = [analyze_trade(t) for t in trades]

df_res = pd.DataFrame(results)

print("==================================================")
print("COMPARATIVE SIMULATION RESULTS (TODAY'S 39 TRADES)")
print("==================================================")
print(f"1. ACTUAL LIVE PERFORMANCE:")
print(f"   Total Realized PnL: ${df_res['actual_pnl'].sum():+.2f}")
print(f"   Win Rate: {(df_res['actual_pnl'] > 0).mean() * 100:.1f}%")
print(f"   Avg Win: ${df_res[df_res['actual_pnl'] > 0]['actual_pnl'].mean():.2f}")
print(f"   Avg Loss: ${abs(df_res[df_res['actual_pnl'] < 0]['actual_pnl'].mean()):.2f}")
print(f"   Realized Avg R:R: 1:{df_res[df_res['actual_pnl'] > 0]['actual_pnl'].mean() / abs(df_res[df_res['actual_pnl'] < 0]['actual_pnl'].mean()):.2f}\n")

print(f"2. APPROACH A (No Trailing Stop - Pure SL/TP Target Execution):")
print(f"   Total Simulated PnL: ${df_res['pnl_a'].sum():+.2f}")
print(f"   Trades Reaching Full TP: {df_res['reached_tp'].sum()} / {len(trades)} ({df_res['reached_tp'].mean()*100:.1f}%)")
print(f"   Win Rate: {(df_res['pnl_a'] > 0).mean() * 100:.1f}%")
print(f"   Avg Win: ${df_res[df_res['pnl_a'] > 0]['pnl_a'].mean():.2f}")
print(f"   Avg Loss: ${abs(df_res[df_res['pnl_a'] < 0]['pnl_a'].mean()):.2f}")
print(f"   Realized Avg R:R: 1:{df_res[df_res['pnl_a'] > 0]['pnl_a'].mean() / abs(df_res[df_res['pnl_a'] < 0]['pnl_a'].mean()):.2f}\n")

print(f"3. APPROACH B (Wide Trailing Stop = 40 pips / $4.00 Gold):")
print(f"   Total Simulated PnL: ${df_res['pnl_b'].sum():+.2f}")
print(f"   Win Rate: {(df_res['pnl_b'] > 0).mean() * 100:.1f}%")
print(f"   Avg Win: ${df_res[df_res['pnl_b'] > 0]['pnl_b'].mean():.2f}")
print(f"   Avg Loss: ${abs(df_res[df_res['pnl_b'] < 0]['pnl_b'].mean()):.2f}")
print(f"   Realized Avg R:R: 1:{df_res[df_res['pnl_b'] > 0]['pnl_b'].mean() / abs(df_res[df_res['pnl_b'] < 0]['pnl_b'].mean()):.2f}\n")

print(f"4. APPROACH C (Delayed Trailing: Activate at >= 1.2R with 2.0x ATR Trailing):")
print(f"   Total Simulated PnL: ${df_res['pnl_c'].sum():+.2f}")
print(f"   Win Rate: {(df_res['pnl_c'] > 0).mean() * 100:.1f}%")
print(f"   Avg Win: ${df_res[df_res['pnl_c'] > 0]['pnl_c'].mean():.2f}")
print(f"   Avg Loss: ${abs(df_res[df_res['pnl_c'] < 0]['pnl_c'].mean()):.2f}")
print(f"   Realized Avg R:R: 1:{df_res[df_res['pnl_c'] > 0]['pnl_c'].mean() / abs(df_res[df_res['pnl_c'] < 0]['pnl_c'].mean()):.2f}")
print("==================================================")
