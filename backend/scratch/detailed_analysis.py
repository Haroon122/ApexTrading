import sqlite3
import os
import sys

db_path = os.path.join(os.path.dirname(__file__), "..", "data", "trading_memory.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT * FROM trade_history_memory ORDER BY close_timestamp ASC")
all_rows = cur.fetchall()

rows = [r for r in all_rows if r['win_loss'] != 'PENDING']

print("=====================================================================")
print("                   APEX TRADER AI - TRADE ANALYSIS                   ")
print("=====================================================================")
print(f"Total Database Records: {len(all_rows)}")
print(f"Total Closed Trades: {len(rows)}")

wins = [r for r in rows if (r['pnl_usd'] or 0) > 0]
losses = [r for r in rows if (r['pnl_usd'] or 0) < 0]
breakevens = [r for r in rows if (r['pnl_usd'] or 0) == 0]

tot_win = sum(r['pnl_usd'] for r in wins)
tot_loss = sum(r['pnl_usd'] for r in losses)
net_pnl = tot_win + tot_loss

avg_win = tot_win / len(wins) if wins else 0
avg_loss = abs(tot_loss) / len(losses) if losses else 0
win_rate = (len(wins) / len(rows) * 100) if rows else 0
profit_factor = (tot_win / abs(tot_loss)) if tot_loss != 0 else float('inf')

print(f"\n--- OVERALL PERFORMANCE ---")
print(f"Wins: {len(wins)} ({win_rate:.1f}%)")
print(f"Losses: {len(losses)} ({len(losses)/len(rows)*100:.1f}%)")
print(f"Breakeven: {len(breakevens)}")
print(f"Total Gross Profit:  ${tot_win:.2f}")
print(f"Total Gross Loss:    ${tot_loss:.2f}")
print(f"Net Realized PnL:    ${net_pnl:.2f}")
print(f"Average Win:         ${avg_win:.2f}")
print(f"Average Loss:        ${avg_loss:.2f}")
print(f"Win/Loss Payoff:     {avg_win/avg_loss:.2f}" if avg_loss > 0 else "Win/Loss Payoff: N/A")
print(f"Profit Factor:       {profit_factor:.2f}")

# Analysis by Exit Reason
exit_reasons = {}
for r in rows:
    reason = r['exit_reason'] or 'UNKNOWN'
    if 'GIVEBACK_GUARD' in reason:
        cat = 'GIVEBACK_GUARD'
    elif 'sl' in reason.lower():
        cat = 'STOP_LOSS'
    elif 'tp' in reason.lower():
        cat = 'TAKE_PROFIT'
    else:
        cat = reason
    if cat not in exit_reasons:
        exit_reasons[cat] = {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
    exit_reasons[cat]['count'] += 1
    pnl = r['pnl_usd'] or 0.0
    exit_reasons[cat]['pnl'] += pnl
    if pnl > 0:
        exit_reasons[cat]['wins'] += 1
    elif pnl < 0:
        exit_reasons[cat]['losses'] += 1

print("\n--- PERFORMANCE BY EXIT REASON ---")
for cat, d in sorted(exit_reasons.items(), key=lambda x: x[1]['pnl'], reverse=True):
    wr = (d['wins'] / d['count'] * 100) if d['count'] else 0
    print(f"Exit Type: {cat:20} | Count: {d['count']:3} | Wins: {d['wins']:2} | Losses: {d['losses']:2} | WR: {wr:5.1f}% | Net PnL: ${d['pnl']:+8.2f}")

# Analysis by Setup Name
setup_stats = {}
for r in rows:
    setup = r['setup_name'] or 'UNKNOWN'
    if setup not in setup_stats:
        setup_stats[setup] = {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
    setup_stats[setup]['count'] += 1
    pnl = r['pnl_usd'] or 0.0
    setup_stats[setup]['pnl'] += pnl
    if pnl > 0:
        setup_stats[setup]['wins'] += 1
    elif pnl < 0:
        setup_stats[setup]['losses'] += 1

print("\n--- PERFORMANCE BY STRATEGY / SETUP ---")
for setup, d in sorted(setup_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
    wr = (d['wins'] / d['count'] * 100) if d['count'] else 0
    print(f"Setup: {setup:45} | Count: {d['count']:3} | WR: {wr:5.1f}% | Net PnL: ${d['pnl']:+8.2f}")

# Analysis by Timeframe
tf_stats = {}
for r in rows:
    tf = r['timeframe'] or 'UNKNOWN'
    if tf not in tf_stats:
        tf_stats[tf] = {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
    tf_stats[tf]['count'] += 1
    pnl = r['pnl_usd'] or 0.0
    tf_stats[tf]['pnl'] += pnl
    if pnl > 0:
        tf_stats[tf]['wins'] += 1
    elif pnl < 0:
        tf_stats[tf]['losses'] += 1

print("\n--- PERFORMANCE BY TIMEFRAME ---")
for tf, d in sorted(tf_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
    wr = (d['wins'] / d['count'] * 100) if d['count'] else 0
    print(f"Timeframe: {tf:10} | Count: {d['count']:3} | WR: {wr:5.1f}% | Net PnL: ${d['pnl']:+8.2f}")

# Side analysis
side_stats = {}
for r in rows:
    side = r['side'] or 'UNKNOWN'
    if side not in side_stats:
        side_stats[side] = {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
    side_stats[side]['count'] += 1
    pnl = r['pnl_usd'] or 0.0
    side_stats[side]['pnl'] += pnl
    if pnl > 0:
        side_stats[side]['wins'] += 1
    elif pnl < 0:
        side_stats[side]['losses'] += 1

print("\n--- PERFORMANCE BY DIRECTION (BUY vs SELL) ---")
for side, d in sorted(side_stats.items(), key=lambda x: x[1]['pnl'], reverse=True):
    wr = (d['wins'] / d['count'] * 100) if d['count'] else 0
    print(f"Side: {side:10} | Count: {d['count']:3} | WR: {wr:5.1f}% | Net PnL: ${d['pnl']:+8.2f}")
