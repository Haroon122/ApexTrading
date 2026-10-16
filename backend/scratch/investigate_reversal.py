import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "..", "data", "trading_memory.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("PRAGMA table_info(trade_history_memory)")
cols = [c[1] for c in cur.fetchall()]
print("Columns in DB:", cols)

cur.execute("""
    SELECT trade_id, symbol, side, timeframe, entry_price, exit_price, sl_price, tp_price, lots, pnl_usd, pnl_pips, win_loss, exit_reason, setup_name, open_timestamp, close_timestamp
    FROM trade_history_memory
    WHERE win_loss != 'PENDING'
    ORDER BY close_timestamp DESC
    LIMIT 25
""")
rows = cur.fetchall()

print("\n=== DETAILED ANALYSIS OF RECENT TRADES ===")
for r in rows:
    entry = r['entry_price'] or 0
    exit_p = r['exit_price'] or 0
    sl = r['sl_price'] or 0
    tp = r['tp_price'] or 0
    side = r['side']
    pnl = r['pnl_usd'] or 0
    reason = r['exit_reason'] or 'N/A'
    setup = r['setup_name'] or 'N/A'
    tf = r['timeframe'] or 'N/A'
    
    sl_dist = abs(entry - sl) if sl > 0 else 0
    tp_dist = abs(tp - entry) if tp > 0 else 0
    
    print(f"Trade #{r['trade_id']} | {tf} {side} {setup[:30]}")
    print(f"   Entry: {entry:.2f} | SL: {sl:.2f} (${sl_dist:.2f} risk) | TP: {tp:.2f} (${tp_dist:.2f} target)")
    print(f"   Exit: {exit_p:.2f} | PnL: ${pnl:+.2f} | Reason: {reason}")
    print(f"   Target Multiplier: TP is {tp_dist/sl_dist:.1f}x SL distance" if sl_dist > 0 else "   Target Multiplier: N/A")
    print("-" * 65)
