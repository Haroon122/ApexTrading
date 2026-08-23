import sqlite3

conn = sqlite3.connect('data/trading_memory.db')
c = conn.cursor()
c.execute("SELECT trade_id, symbol, side, entry_price, exit_price, pnl_usd, pnl_pips FROM trade_history_memory WHERE win_loss != 'PENDING' ORDER BY close_timestamp DESC LIMIT 10")
rows = c.fetchall()
for r in rows:
    print(r)
