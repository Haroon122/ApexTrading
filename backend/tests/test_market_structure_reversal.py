import pytest
from datetime import datetime, timedelta
from app.domain.models import Candle
from app.services.market_structure import MarketStructureEngine

def test_market_structure_realtime_trend_reversal():
    """
    Verifies that MarketStructureEngine detects near real-time trend reversals
    when price breaks structure or exhibits sustained momentum against the last BOS.
    """
    base_time = datetime(2026, 8, 26, 10, 0, 0)
    candles = []
    
    # 1. Build initial uptrend with 40 candles to establish a BULLISH BOS (swings up to 2500)
    price = 2000.0
    for i in range(40):
        if i % 2 == 0:
            price += 10.0
        else:
            price -= 3.0
        candles.append(Candle(
            timestamp=base_time + timedelta(minutes=i*15),
            open=price - 2.0,
            high=price + 3.0,
            low=price - 3.0,
            close=price,
            volume=1000
        ))
        
    # Analyze initial structure (should be BULLISH)
    analysis_bullish = MarketStructureEngine.analyze(candles, timeframe="M15", symbol="XAUUSD")
    initial_trend = analysis_bullish.trend
    print(f"\n[SYNTHETIC TEST] Initial Trend after Uptrend: {initial_trend}")
    assert initial_trend == "BULLISH", f"Expected BULLISH, got {initial_trend}"
    
    # 2. Append 6 consecutive sharp bearish candles dropping 150 points (breaking previous swing lows)
    drop_price = price
    for j in range(1, 7):
        drop_price -= 25.0
        candles.append(Candle(
            timestamp=base_time + timedelta(minutes=(40 + j) * 15),
            open=drop_price + 5.0,
            high=drop_price + 6.0,
            low=drop_price - 1.0,
            close=drop_price,
            volume=2000
        ))
        
    # Analyze updated structure (should flip to BEARISH in real-time without waiting for full 3-pivot reversal)
    analysis_reversal = MarketStructureEngine.analyze(candles, timeframe="M15", symbol="XAUUSD")
    reversed_trend = analysis_reversal.trend
    print(f"[SYNTHETIC TEST] Reversed Trend after 6 Bearish Drop Candles: {reversed_trend}")
    assert reversed_trend == "BEARISH", f"Expected BEARISH real-time flip, got {reversed_trend}"

if __name__ == "__main__":
    test_market_structure_realtime_trend_reversal()
