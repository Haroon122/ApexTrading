import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "trading_memory.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT trade_id, symbol, side, lots, entry_price, exit_price, sl_price, tp_price, pnl_usd, pnl_pips, win_loss, exit_reason, open_timestamp, close_timestamp 
    FROM trade_history_memory 
    WHERE win_loss != 'PENDING' 
    ORDER BY close_timestamp ASC
""")
rows = cur.fetchall()

print(f"TOTAL TRADES RECORDED: {len(rows)}\n")

wins = [r for r in rows if r['pnl_usd'] > 0]
losses = [r for r in rows if r['pnl_usd'] < 0]

tot_win_usd = sum(r['pnl_usd'] for r in wins)
tot_loss_usd = sum(r['pnl_usd'] for r in losses)

avg_win = tot_win_usd / len(wins) if wins else 0
avg_loss = abs(tot_loss_usd / len(losses)) if losses else 0
win_rate = (len(wins) / len(rows) * 100) if rows else 0
profit_factor = tot_win_usd / abs(tot_loss_usd) if tot_loss_usd != 0 else 0

print("=== OVERALL TRADE STATS ===")
print(f"Total Trades: {len(rows)}")
print(f"Winning Trades Count: {len(wins)}")
print(f"Losing Trades Count: {len(losses)}")
print(f"Win Rate: {win_rate:.1f}%")
print(f"Total Profit ($): ${tot_win_usd:.2f}")
print(f"Total Loss ($): ${tot_loss_usd:.2f}")
print(f"Net PnL ($): ${tot_win_usd + tot_loss_usd:.2f}")
print(f"Average Winning Trade ($): ${avg_win:.2f}")
print(f"Average Losing Trade ($): ${avg_loss:.2f}")
print(f"Profit Factor: {profit_factor:.2f}\n")

print("=== DETAILED TRADE LIST ===")
for r in rows:
    entry = r['entry_price'] or 0
    exit_p = r['exit_price'] or 0
    sl = r['sl_price'] or 0
    tp = r['tp_price'] or 0
    lots = r['lots'] or 0
    pnl = r['pnl_usd'] or 0
    side = r['side']
    reason = r['exit_reason'] or 'N/A'
    
    sl_dist = abs(entry - sl) if sl > 0 else 0
    tp_dist = abs(tp - entry) if tp > 0 else 0
    planned_rr = tp_dist / sl_dist if sl_dist > 0 else 0
    
    actual_dist = abs(exit_p - entry) if exit_p > 0 else 0
    actual_rr = actual_dist / sl_dist if sl_dist > 0 else 0
    
    print(f"Trade #{r['trade_id']} | {r['symbol']} {side} {lots}L | Entry: {entry:.2f} | Exit: {exit_p:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | PnL: ${pnl:+.2f} ({r['win_loss']}) | Exit Reason: {reason} | Planned R:R: 1:{planned_rr:.2f}")
