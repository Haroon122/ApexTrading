import pytest
from datetime import datetime, timedelta
from app.domain.models import Candle, TickerData
from app.services.decision_engine import decision_engine

def test_decision_engine_preserves_requested_timeframe():
    """
    Verifies that decision_engine evaluates the exact requested timeframe (e.g. M15)
    and does not silently override it with hardcoded M1.
    """
    base_time = datetime(2026, 8, 26, 10, 0, 0)
    candles = [
        Candle(
            timestamp=base_time + timedelta(minutes=i*15),
            open=2500.0 + i,
            high=2505.0 + i,
            low=2495.0 + i,
            close=2502.0 + i,
            volume=500
        ) for i in range(40)
    ]
    ticker = TickerData(
        symbol="XAUUSD",
        ask=2541.0,
        bid=2540.0,
        spread_pips=1.0,
        digits=2,
        point_size=0.01,
        timestamp=base_time
    )
    
    # Request M15 evaluation
    report_m15 = decision_engine.evaluate_trade_opportunity(
        symbol="XAUUSD",
        candles=candles,
        ticker=ticker,
        timeframe="M15"
    )
    
    print(f"\n[TIMEFRAME VERIFICATION] Requested: 'M15' | Evaluated Report Timeframe: '{report_m15.timeframe}'")
    assert report_m15.timeframe == "M15", f"Expected timeframe 'M15', but got '{report_m15.timeframe}'"
    
    # Request H1 evaluation
    report_h1 = decision_engine.evaluate_trade_opportunity(
        symbol="XAUUSD",
        candles=candles,
        ticker=ticker,
        timeframe="H1"
    )
    print(f"[TIMEFRAME VERIFICATION] Requested: 'H1' | Evaluated Report Timeframe: '{report_h1.timeframe}'")
    assert report_h1.timeframe == "H1", f"Expected timeframe 'H1', but got '{report_h1.timeframe}'"

if __name__ == "__main__":
    test_decision_engine_preserves_requested_timeframe()
