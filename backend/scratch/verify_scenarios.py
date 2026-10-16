import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.abspath("backend"))

from app.domain.models import Candle, TickerData, TradeSide, RiskSettings
from app.services.decision_engine import decision_engine
from app.services.dark_venus_bb import dark_venus_engine
from app.services.trade_manager import trade_manager

print("=" * 80)
print("ApexTrading Multi-Setup Verification: Trend, Breakout, Momentum & Pullbacks")
print("=" * 80)

ticker = TickerData(
    symbol="XAUUSD",
    bid=2350.00,
    ask=2350.20,
    spread_pips=2.0,
    spread_points=20,
    raw_spread=0.20,
    point_size=0.01,
    digits=2,
    timestamp=datetime.now()
)

class MockStructure:
    def __init__(self, trend="BULLISH", bos_price=2340.0):
        self.trend = trend
        self.order_blocks = []
        self.liquidity_sweeps = []
        self.latest_structure = type("S", (), {"type": f"{trend}_BOS", "price": bos_price})()
        self.value_area_high = 2360.0
        self.poc_price = 2352.0

scenarios = [
    {
        "name": "1. Bullish Breakout & Momentum Expansion (Upper BB + Volume Spike)",
        "regime": "STRONG_UPTREND",
        "h1_trend": "BULLISH",
        "struct_trend": "BULLISH",
        "row": {
            "close": 2355.0, "open": 2348.0, "high": 2356.0, "low": 2347.0,
            "bb_upper": 2352.0, "bb_middle": 2345.0, "bb_lower": 2338.0,
            "bb_width": 0.006, "percent_b": 1.15, "bb_expansion": True, "bb_squeeze": False,
            "rsi_14": 68.0, "macd_hist": 1.2, "rvol": 1.8, "atr_14": 2.5,
            "ema_9": 2350.0, "ema_20": 2346.0, "ema_50": 2340.0
        }
    },
    {
        "name": "2. Bullish BOS Breakout & Retest (Broken Resistance acting as Support)",
        "regime": "STRONG_UPTREND",
        "h1_trend": "BULLISH",
        "struct_trend": "BULLISH",
        "row": {
            "close": 2343.0, "open": 2341.0, "high": 2344.0, "low": 2340.5, # Retest at BOS 2340.0
            "bb_upper": 2350.0, "bb_middle": 2340.0, "bb_lower": 2330.0,
            "bb_width": 0.005, "percent_b": 0.65, "bb_expansion": False, "bb_squeeze": False,
            "rsi_14": 58.0, "macd_hist": 0.6, "rvol": 1.4, "atr_14": 2.0,
            "ema_9": 2343.0, "ema_20": 2340.0, "ema_50": 2335.0
        }
    },
    {
        "name": "3. Bullish EMA 9/20 Dynamic Trend Pullback",
        "regime": "WEAK_UPTREND",
        "h1_trend": "BULLISH",
        "struct_trend": "BULLISH",
        "row": {
            "close": 2342.0, "open": 2339.0, "high": 2343.0, "low": 2338.5, # Touched EMA20 at 2339.0
            "bb_upper": 2348.0, "bb_middle": 2339.0, "bb_lower": 2330.0,
            "bb_width": 0.004, "percent_b": 0.60, "bb_expansion": False, "bb_squeeze": False,
            "rsi_14": 54.0, "macd_hist": 0.3, "rvol": 1.2, "atr_14": 1.8,
            "ema_9": 2341.0, "ema_20": 2339.0, "ema_50": 2332.0
        }
    },
    {
        "name": "4. Bearish Breakdown & Momentum Expansion (Lower BB + Volume Spike)",
        "regime": "STRONG_DOWNTREND",
        "h1_trend": "BEARISH",
        "struct_trend": "BEARISH",
        "row": {
            "close": 2330.0, "open": 2338.0, "high": 2339.0, "low": 2329.0,
            "bb_upper": 2350.0, "bb_middle": 2340.0, "bb_lower": 2332.0,
            "bb_width": 0.006, "percent_b": -0.10, "bb_expansion": True, "bb_squeeze": False,
            "rsi_14": 32.0, "macd_hist": -1.4, "rvol": 1.9, "atr_14": 2.6,
            "ema_9": 2335.0, "ema_20": 2339.0, "ema_50": 2345.0
        }
    },
    {
        "name": "5. Counter-Trend Mean Reversion in Range (Lower Band Oversold Rejection)",
        "regime": "RANGE",
        "h1_trend": "NEUTRAL",
        "struct_trend": "NEUTRAL",
        "row": {
            "close": 2332.0, "open": 2330.0, "high": 2333.0, "low": 2328.0, # Pierced lower BB at 2330.0 + rejection
            "bb_upper": 2350.0, "bb_middle": 2340.0, "bb_lower": 2330.0,
            "bb_width": 0.004, "percent_b": 0.10, "bb_expansion": False, "bb_squeeze": False,
            "rsi_14": 28.0, "macd_hist": -0.2, "rvol": 1.1, "atr_14": 1.5,
            "ema_9": 2335.0, "ema_20": 2338.0, "ema_50": 2340.0
        }
    }
]

print(f"{'Scenario':<42} | {'Active Setups (Disabled)':<20} | {'Active Setups (Enabled)':<20}")
print("-" * 88)

disabled_list = [
    "BB Midline Pullback",
    "SETUP D: Bullish BOS Breakout & Retest",
    "SETUP D: Bearish BOS Breakdown & Retest",
    "SETUP F: Bullish Volume & Momentum Expansion",
    "SETUP F: Bearish Volume & Momentum Expansion",
    "BB Band Breakout / Momentum"
]

for sc in scenarios:
    df = pd.DataFrame([sc["row"]] * 25)
    struct = MockStructure(trend=sc["struct_trend"])
    
    # 1. Evaluate with setups disabled
    trade_manager.risk_settings.disabled_setups = disabled_list
    setups_disabled = decision_engine.evaluate_setups(
        symbol="XAUUSD", candles=[], ticker=ticker, timeframe="M15",
        htf_candles={"H1": [], "M15": []}, df=df, structure=struct, regime=sc["regime"]
    )
    active_dis = [s["name"] for s in setups_disabled if s.get("active")]
    
    # 2. Evaluate with setups enabled
    trade_manager.risk_settings.disabled_setups = []
    setups_enabled = decision_engine.evaluate_setups(
        symbol="XAUUSD", candles=[], ticker=ticker, timeframe="M15",
        htf_candles={"H1": [], "M15": []}, df=df, structure=struct, regime=sc["regime"]
    )
    active_en = [s["name"] for s in setups_enabled if s.get("active")]
    
    print(f"{sc['name'][:40]:<42} | {len(active_dis)} active: {str(active_dis[:1])[:15]:<10} | {len(active_en)} active: {str(active_en[:1])[:15]:<10}")

print("=" * 88)
