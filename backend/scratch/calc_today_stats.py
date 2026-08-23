import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "trading_memory.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get trades from today (2026-08-18)
cur.execute("""
    SELECT trade_id, symbol, side, lots, entry_price, exit_price, sl_price, tp_price, pnl_usd, pnl_pips, win_loss, exit_reason, open_timestamp, close_timestamp 
    FROM trade_history_memory 
    WHERE win_loss IN ('WIN', 'LOSS') AND (close_timestamp LIKE '2026-08-18%' OR open_timestamp LIKE '2026-08-18%')
    ORDER BY close_timestamp ASC
""")
rows = cur.fetchall()

if not rows:
    # Fallback to all completed trades if timestamp filter format varies
    cur.execute("""
        SELECT trade_id, symbol, side, lots, entry_price, exit_price, sl_price, tp_price, pnl_usd, pnl_pips, win_loss, exit_reason, open_timestamp, close_timestamp 
        FROM trade_history_memory 
        WHERE win_loss IN ('WIN', 'LOSS')
        ORDER BY close_timestamp ASC
    """)
    rows = cur.fetchall()

wins = [r for r in rows if r['pnl_usd'] > 0]
losses = [r for r in rows if r['pnl_usd'] < 0]

tot_win_usd = sum(r['pnl_usd'] for r in wins)
tot_loss_usd = sum(r['pnl_usd'] for r in losses)

avg_win = tot_win_usd / len(wins) if wins else 0
avg_loss = abs(tot_loss_usd / len(losses)) if losses else 0
win_rate = (len(wins) / len(rows) * 100) if rows else 0
profit_factor = tot_win_usd / abs(tot_loss_usd) if tot_loss_usd != 0 else 0

# Calculate actual average R:R achieved (average winning PnL / average losing PnL)
actual_avg_rr = avg_win / avg_loss if avg_loss > 0 else 0

print("==================================================")
print("TODAY'S TRADE AUDIT METRICS (2026-08-18)")
print("==================================================")
print(f"Total Executed Trades    : {len(rows)}")
print(f"Winning Trades Count     : {len(wins)}")
print(f"Losing Trades Count      : {len(losses)}")
print(f"Win Rate (%)             : {win_rate:.1f}%")
print(f"Total Profit ($)         : ${tot_win_usd:.2f}")
print(f"Total Loss ($)           : ${tot_loss_usd:.2f}")
print(f"Net Realized PnL ($)     : ${tot_win_usd + tot_loss_usd:.2f}")
print(f"Average Winning Trade ($): ${avg_win:.2f}")
print(f"Average Losing Trade ($) : ${avg_loss:.2f}")
print(f"Profit Factor            : {profit_factor:.2f}")
print(f"Actual Realized Avg R:R  : 1:{actual_avg_rr:.2f}")
print("==================================================")
