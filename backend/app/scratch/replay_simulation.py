"""
1000+ M1 Candle Replay Simulation Script for XAUUSD Auto-Engine.
Runs historical/simulated market data through the upgraded DecisionEngine and produces a complete rejection histogram.
"""

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.domain.models import Candle, TickerData, TradeSide, RiskSettings
from app.services.decision_engine import decision_engine
from app.services.signal_history import signal_history
from app.services.trade_manager import trade_manager


def run_simulation(num_candles: int = 1000):
    print(f"=== STARTING {num_candles} M1 CANDLE REPLAY SIMULATION ===")
    
    np.random.seed(42)
    base_price = 2380.00
    candles = []
    current_time = datetime.now() - timedelta(minutes=num_candles)
    
    # Generate 1000 realistic XAUUSD M1 candles with regime transitions
    price = base_price
    for i in range(num_candles):
        # Create regimes: uptrend, downtrend, range, breakout
        phase = (i // 200) % 4
        if phase == 0:
            trend = 0.15 # Bullish
        elif phase == 1:
            trend = -0.15 # Bearish
        elif phase == 2:
            trend = 0.0 # Range
        else:
            trend = 0.35 if (i % 2 == 0) else -0.30 # High volatility / breakout
            
        price += trend + (np.random.randn() * 0.45)
        high = price + abs(np.random.randn() * 0.70)
        low = price - abs(np.random.randn() * 0.70)
        open_p = price - (np.random.randn() * 0.30)
        close = price
        
        candles.append(Candle(
            timestamp=current_time + timedelta(minutes=i),
            open=round(open_p, 2),
            high=round(high, 2),
            low=round(low, 2),
            close=round(close, 2),
            volume=100.0 + (i % 50),
            tick_volume=100 + (i % 50),
            is_closed=True
        ))

    signal_history.clear()
    
    trade_manager.risk_settings = RiskSettings(
        lot_sizing_mode="FIXED",
        user_configured_lot=0.10,
        max_allowed_lot=1.00,
        auto_trade_cooldown_seconds=0,
        max_spread_pips=3.0,
        strategy_profile="HYBRID_AUTO",
        signal_mode="CLOSED_CANDLE"
    )

    # Process sliding window of 40 candles through DecisionEngine
    window_size = 40
    for idx in range(window_size, len(candles)):
        sub_candles = candles[idx-window_size:idx]
        latest_c = sub_candles[-1]
        
        ticker = TickerData(
            symbol="XAUUSD",
            bid=latest_c.close,
            ask=round(latest_c.close + 0.20, 2),
            spread_pips=2.0,
            spread_points=20,
            raw_spread=0.20,
            point_size=0.01,
            digits=2,
            timestamp=latest_c.timestamp
        )
        
        # Evaluate trade opportunity
        report = decision_engine.evaluate_trade_opportunity("XAUUSD", sub_candles, ticker, "M1")

    stats = signal_history.get_summary_stats()
    
    print("\n=== REPLAY SIMULATION RESULTS ===")
    print(f"Total Evaluated Candles: {stats['total_evaluations']}")
    print(f"BUY Signals Generated : {stats['buy_signals']}")
    print(f"SELL Signals Generated: {stats['sell_signals']}")
    print(f"HOLD Decisions        : {stats['hold_evaluations']}")
    print("\n--- REJECTION HISTOGRAM BREAKDOWN ---")
    for reason, count in stats['rejection_histogram'].items():
        pct = (count / stats['total_evaluations']) * 100.0
        print(f"  {reason:<22}: {count:>4} ({pct:>5.1f}%)")

    return stats


if __name__ == "__main__":
    run_simulation(1000)
