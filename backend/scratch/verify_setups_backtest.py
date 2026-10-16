import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Add backend to path
sys.path.insert(0, os.path.abspath("backend"))

from app.domain.models import Candle, TickerData, TradeSide, RiskSettings
from app.services.decision_engine import DecisionEngine
from app.services.trade_manager import trade_manager
from app.services.news_filter import news_filter

# Bypass live network calls for news in backtest benchmark
news_filter.is_news_blackout = lambda symbol, current_time=None: (False, "No news blackout")

print("=" * 70)
print("ApexTrading End-to-End Multi-Setup Performance & Expectancy Benchmark")
print("=" * 70)

# Create 500 M15 test candles simulating real Gold cycles (ranging, trend breakout, pullbacks)
np.random.seed(42)
candles_m15 = []
base_price = 2300.0
curr_price = base_price
start_time = datetime(2026, 10, 1, 8, 0, 0) # London session start

for i in range(500):
    timestamp = start_time + timedelta(minutes=15 * i)
    # Regimes:
    # 0-100: Range (2300 +/- 4)
    # 100-250: Strong Bullish Breakout & Trend (2300 -> 2360)
    # 250-350: Consolidation & Retest
    # 350-450: Strong Bearish Breakdown (2360 -> 2315)
    # 450-500: Range
    if 100 <= i < 250:
        drift = 0.40  # Bull trend
        vol = 0.5
    elif 350 <= i < 450:
        drift = -0.45 # Bear trend
        vol = 0.6
    else:
        drift = 0.0   # Consolidation
        vol = 0.35
        
    delta = drift + np.random.randn() * vol
    open_p = curr_price
    close_p = open_p + delta
    high_p = max(open_p, close_p) + abs(np.random.randn() * 0.4)
    low_p = min(open_p, close_p) - abs(np.random.randn() * 0.4)
    curr_price = close_p
    
    candles_m15.append(Candle(
        timestamp=timestamp,
        open=round(open_p, 2),
        high=round(high_p, 2),
        low=round(low_p, 2),
        close=round(close_p, 2),
        volume=float(np.random.randint(500, 3000)),
        is_closed=True
    ))

# Build H1 candles by aggregating 4 M15 candles
candles_h1 = []
for idx in range(0, len(candles_m15), 4):
    chunk = candles_m15[idx:idx+4]
    if len(chunk) == 4:
        candles_h1.append(Candle(
            timestamp=chunk[0].timestamp,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
            volume=sum(c.volume for c in chunk),
            is_closed=True
        ))

htf_candles = {"H1": candles_h1, "M15": candles_m15}

def evaluate_simulation(disabled_setups, label=""):
    engine = DecisionEngine()
    trade_manager.risk_settings = RiskSettings(
        lot_sizing_mode="DYNAMIC",
        user_configured_lot=0.10,
        max_allowed_lot=1.00,
        auto_trade_cooldown_seconds=180,
        max_spread_pips=3.0,
        strategy_profile="HYBRID_AUTO",
        signal_mode="CLOSED_CANDLE",
        htf_alignment_filter_enabled=True,
        htf_alignment_mode="MODERATE",
        disabled_setups=disabled_setups,
        allowed_sessions=["LONDON", "NEWYORK", "LONDON_NY_OVERLAP", "ASIAN"]
    )
    
    trades = []
    in_trade = False
    entry_price = 0.0
    sl = 0.0
    tp = 0.0
    side = None
    setup_name = ""
    
    # Run through historical slices
    for step in range(35, len(candles_m15)):
        current_candle = candles_m15[step]
        ticker = TickerData(
            symbol="XAUUSD",
            bid=current_candle.close,
            ask=current_candle.close + 0.20,
            spread_pips=2.0,
            spread_points=20,
            raw_spread=0.20,
            point_size=0.01,
            digits=2,
            timestamp=current_candle.timestamp
        )
        
        # Check active position exit
        if in_trade:
            if side == TradeSide.BUY:
                if current_candle.low <= sl:
                    pnl = (sl - entry_price) * 100 # $ per standard lot scale
                    trades.append({"pnl": pnl, "win": False, "setup": setup_name, "side": "BUY"})
                    in_trade = False
                elif current_candle.high >= tp:
                    pnl = (tp - entry_price) * 100
                    trades.append({"pnl": pnl, "win": True, "setup": setup_name, "side": "BUY"})
                    in_trade = False
            elif side == TradeSide.SELL:
                if current_candle.high >= sl:
                    pnl = (entry_price - sl) * 100
                    trades.append({"pnl": pnl, "win": False, "setup": setup_name, "side": "SELL"})
                    in_trade = False
                elif current_candle.low <= tp:
                    pnl = (entry_price - tp) * 100
                    trades.append({"pnl": pnl, "win": True, "setup": setup_name, "side": "SELL"})
                    in_trade = False
                    
        # Check new entry if not in trade
        if not in_trade:
            slice_candles = candles_m15[:step+1]
            report = engine.evaluate_trade_opportunity(
                symbol="XAUUSD",
                candles=slice_candles,
                ticker=ticker,
                timeframe="M15",
                htf_candles=htf_candles
            )
            
            if report.decision in (TradeSide.BUY, TradeSide.SELL):
                in_trade = True
                side = report.decision
                entry_price = report.suggested_entry
                sl = report.suggested_sl
                tp = report.suggested_tp
                setup_name = report.setup_name or "Unknown Setup"

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    total_trades = len(trades)
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    net_profit = gross_profit - gross_loss
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    
    # Setups breakdown
    setup_counts = {}
    for t in trades:
        s = t["setup"]
        setup_counts[s] = setup_counts.get(s, {"count": 0, "wins": 0, "pnl": 0.0})
        setup_counts[s]["count"] += 1
        if t["win"]:
            setup_counts[s]["wins"] += 1
        setup_counts[s]["pnl"] += t["pnl"]
        
    return {
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "net_profit": net_profit,
        "profit_factor": profit_factor,
        "setup_breakdown": setup_counts
    }

# Run Benchmark 1: CURRENT STATE (With setups disabled)
disabled_current = [
    "BB Midline Pullback",
    "SETUP D: Bullish BOS Breakout & Retest",
    "SETUP D: Bearish BOS Breakdown & Retest",
    "SETUP F: Bullish Volume & Momentum Expansion",
    "SETUP F: Bearish Volume & Momentum Expansion",
    "BB Band Breakout / Momentum"
]
res_current = evaluate_simulation(disabled_current, label="Current Settings")

# Run Benchmark 2: OPTIMIZED STATE (All institutional trend & breakout setups enabled)
res_optimized = evaluate_simulation([], label="All Setups Enabled")

print("\n" + "=" * 70)
print("BENCHMARK RESULTS COMPARISON")
print("=" * 70)
print(f"{'Performance Metric':<32} | {'Current (Trend Blocked)':<18} | {'Optimized (Trend Enabled)':<18}")
print("-" * 72)
print(f"{'Total Trades Executed':<32} | {res_current['total_trades']:<18} | {res_optimized['total_trades']:<18}")
print(f"{'Win Rate (%)':<32} | {res_current['win_rate']:<18.1f}% | {res_optimized['win_rate']:<18.1f}%")
print(f"{'Net Profit ($)':<32} | ${res_current['net_profit']:<17.2f} | ${res_optimized['net_profit']:<17.2f}")
print(f"{'Profit Factor':<32} | {res_current['profit_factor']:<18.2f} | {res_optimized['profit_factor']:<18.2f}")
print("=" * 70)

print("\nPER-SETUP INDEPENDENT PERFORMANCE BREAKDOWN:")
print("-" * 70)
for s_name, data in res_optimized["setup_breakdown"].items():
    wr = (data["wins"] / data["count"] * 100) if data["count"] > 0 else 0
    print(f"- {s_name}:")
    print(f"    Trades: {data['count']} | Wins: {data['wins']} | Win Rate: {wr:.1f}% | Net PnL: ${data['pnl']:.2f}")
print("=" * 70)
