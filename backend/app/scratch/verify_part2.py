import sys
import os
import random
from datetime import datetime, timedelta

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.domain import models
from app.domain.models import Candle, TickerData, TradeSide, MarketStructureAnalysis, OrderBlock, LiquiditySweep
from app.services.market_structure import MarketStructureEngine
from app.services.decision_engine import decision_engine
from app.services.symbol_specs import get_symbol_specs

def make_clean_candles(trend_type: str, count: int = 60) -> list:
    """Generates clean synthetic candles designed to trigger specific structure breaks."""
    random.seed(42)
    candles = []
    base_price = 1.08500
    curr_time = datetime.now() - timedelta(minutes=15 * count)
    
    # We want to create clean swing highs and lows.
    # To do this, we can generate a sine-wave-like structure with an optional trend.
    for i in range(count):
        # Base wave
        phase = i % 10
        if phase < 5:
            # Price going up
            wave_change = 0.0005
        else:
            # Price going down
            wave_change = -0.0005
            
        # Trend component
        if trend_type == "bullish":
            trend_change = 0.0003
        elif trend_type == "bearish":
            trend_change = -0.0003
        else:
            trend_change = 0.0
            
        change = wave_change + trend_change
        base_price += change
        
        candles.append(Candle(
            timestamp=curr_time,
            open=base_price - change,
            high=base_price + abs(change) * 0.2,
            low=base_price - abs(change) * 0.2,
            close=base_price,
            volume=1000.0,
            timeframe="M15",
            is_closed=True
        ))
        curr_time += timedelta(minutes=15)
        
    return candles

def test_trend_breaks():
    print("\n==================================================")
    print("TESTING ISSUE 7: Trend Break Classifications")
    print("==================================================")
    
    # 1. Bullish trend
    bull_candles = make_clean_candles("bullish", 60)
    bull_ms = MarketStructureEngine.analyze(bull_candles, "M15", "EURUSD")
    print(f"Bullish sequence trend: {bull_ms.trend}")
    assert bull_ms.trend == "BULLISH", f"Expected BULLISH but got {bull_ms.trend}"
    
    # 2. Bearish trend
    bear_candles = make_clean_candles("bearish", 60)
    bear_ms = MarketStructureEngine.analyze(bear_candles, "M15", "EURUSD")
    print(f"Bearish sequence trend: {bear_ms.trend}")
    assert bear_ms.trend == "BEARISH", f"Expected BEARISH but got {bear_ms.trend}"
    
    # 3. Sideways/neutral trend
    flat_candles = make_clean_candles("sideways", 60)
    flat_ms = MarketStructureEngine.analyze(flat_candles, "M15", "EURUSD")
    print(f"Sideways sequence trend: {flat_ms.trend}")
    # Sideways should not trigger any trend breaks, so it stays UNKNOWN (as we initialized it)
    assert flat_ms.trend == "UNKNOWN", f"Expected UNKNOWN but got {flat_ms.trend}"
    
    # 4. Insufficient candles
    short_candles = make_clean_candles("bullish", 15)
    short_ms = MarketStructureEngine.analyze(short_candles, "M15", "EURUSD")
    print(f"Short sequence trend: {short_ms.trend}")
    assert short_ms.trend == "UNKNOWN", f"Expected UNKNOWN but got {short_ms.trend}"
    
    print(" -> Issue 7 trend classification checks passed.")

def test_liquidity_sweep_opposition():
    print("\n==================================================")
    print("TESTING ISSUE 5: Opposing Liquidity Sweep Check")
    print("==================================================")
    
    candles = make_clean_candles("bullish", 50)
    ticker = TickerData(
        symbol="EURUSD",
        bid=candles[-1].close,
        ask=candles[-1].close + 0.00005,
        spread_pips=0.5,
        timestamp=datetime.now()
    )
    
    # Mock to force target_side to BUY
    original_trend = decision_engine._evaluate_trend
    original_struct_eval = decision_engine._evaluate_market_structure
    original_momentum = decision_engine._evaluate_momentum_volume
    
    decision_engine._evaluate_trend = lambda *args: (1.0, TradeSide.BUY, [])
    decision_engine._evaluate_market_structure = lambda *args: (1.0, TradeSide.BUY, [])
    decision_engine._evaluate_momentum_volume = lambda *args: (1.0, TradeSide.BUY, [])
    
    try:
        # Evaluate a normal buy setup first
        normal_report = decision_engine.evaluate_trade_opportunity("EURUSD", candles, ticker, "M15")
        normal_sweep = next(item for item in normal_report.condition_matrix if item.id == "liquidity_sweep")
        print(f"Without opposing sweep: passed={normal_sweep.passed} ({normal_sweep.details})")
        assert normal_sweep.passed == True
        
        # Inject a recent bearish sweep (TradeSide.SELL) within the lookback window (e.g. 5 bars ago)
        target_time = candles[-5].timestamp
        sweep = LiquiditySweep(
            side=TradeSide.SELL,
            price_level=1.09500,
            timestamp=target_time
        )
        
        # Mock MarketStructureEngine.analyze to return this sweep
        original_analyze = MarketStructureEngine.analyze
        def mock_analyze(c, tf, sym):
            ms = original_analyze(c, tf, sym)
            ms.liquidity_sweeps = [sweep]
            return ms
            
        MarketStructureEngine.analyze = mock_analyze
        
        try:
            opposed_report = decision_engine.evaluate_trade_opportunity("EURUSD", candles, ticker, "M15")
            opposed_sweep = next(item for item in opposed_report.condition_matrix if item.id == "liquidity_sweep")
            print(f"With opposing sweep: passed={opposed_sweep.passed} ({opposed_sweep.details})")
            
            assert opposed_sweep.passed == False
            assert "Recent opposing sweep" in opposed_sweep.details
            print(" -> Issue 5 opposing liquidity sweep check passed.")
        finally:
            MarketStructureEngine.analyze = original_analyze
    finally:
        decision_engine._evaluate_trend = original_trend
        decision_engine._evaluate_market_structure = original_struct_eval
        decision_engine._evaluate_momentum_volume = original_momentum

def test_risk_reward_failure():
    print("\n==================================================")
    print("TESTING ISSUE 6: Real (Non-Tautological) Risk:Reward Gate")
    print("==================================================")
    
    candles = make_clean_candles("bullish", 50)
    ticker = TickerData(
        symbol="EURUSD",
        bid=1.08500,
        ask=1.08500,
        spread_pips=0.0,
        timestamp=datetime.now()
    )
    
    # Mock to force target_side to BUY
    original_trend = decision_engine._evaluate_trend
    original_struct_eval = decision_engine._evaluate_market_structure
    original_momentum = decision_engine._evaluate_momentum_volume
    
    decision_engine._evaluate_trend = lambda *args: (1.0, TradeSide.BUY, [])
    decision_engine._evaluate_market_structure = lambda *args: (1.0, TradeSide.BUY, [])
    decision_engine._evaluate_momentum_volume = lambda *args: (1.0, TradeSide.BUY, [])
    
    # Mock MarketStructureEngine.analyze to inject a bearish order block very close above the entry price
    original_analyze = MarketStructureEngine.analyze
    def mock_analyze(c, tf, sym):
        ms = original_analyze(c, tf, sym)
        # Entry price is 1.08500. Bearish OB is at 1.08505 (5 points above entry!)
        ms.order_blocks = [
            OrderBlock(
                side=TradeSide.SELL,
                top=1.08510,
                bottom=1.08505,
                timestamp=datetime.now(),
                mitigated=False
            )
        ]
        return ms
        
    MarketStructureEngine.analyze = mock_analyze
    
    try:
        # We also need to mock risk settings to ensure required_rr is 2.0
        from app.services.trade_manager import trade_manager
        original_rr = trade_manager.risk_settings.min_risk_reward_ratio
        trade_manager.risk_settings.min_risk_reward_ratio = 2.0
        
        report = decision_engine.evaluate_trade_opportunity("EURUSD", candles, ticker, "M15")
        rr_item = next(item for item in report.condition_matrix if item.id == "risk_reward")
        print(f"R:R Gate: passed={rr_item.passed} ({rr_item.details})")
        
        # Verify it genuinely failed because the target is too close (Calculated R:R will be tiny)
        assert rr_item.passed == False, "Expected R:R gate to fail for tight structural target"
        print(" -> Issue 6 real structural R:R check passed.")
        
        # Restore risk settings
        trade_manager.risk_settings.min_risk_reward_ratio = original_rr
    finally:
        MarketStructureEngine.analyze = original_analyze
        decision_engine._evaluate_trend = original_trend
        decision_engine._evaluate_market_structure = original_struct_eval
        decision_engine._evaluate_momentum_volume = original_momentum

def test_htf_alignment():
    print("\n==================================================")
    print("TESTING ISSUE 8: HTF Trend Voting Checks")
    print("==================================================")
    
    # Scenario: 3 NEUTRAL, 1 BULLISH. For a BUY proposed trade, this should NOT align!
    # Mock to force target_side to BUY
    original_trend = decision_engine._evaluate_trend
    original_struct_eval = decision_engine._evaluate_market_structure
    original_momentum = decision_engine._evaluate_momentum_volume
    
    decision_engine._evaluate_trend = lambda *args: (1.0, TradeSide.BUY, [])
    decision_engine._evaluate_market_structure = lambda *args: (1.0, TradeSide.BUY, [])
    decision_engine._evaluate_momentum_volume = lambda *args: (1.0, TradeSide.BUY, [])
    
    # Mock _fetch_htf_trends to return this scenario
    original_fetch = decision_engine._fetch_htf_trends
    decision_engine._fetch_htf_trends = lambda sym, htf_candles=None, *args, **kwargs: {
        "M5": "BULLISH",
        "M15": "NEUTRAL",
        "M30": "NEUTRAL",
        "H1": "NEUTRAL"
    }
    
    try:
        candles = make_clean_candles("bullish", 50)
        ticker = TickerData(
            symbol="EURUSD",
            bid=candles[-1].close,
            ask=candles[-1].close + 0.00005,
            spread_pips=0.5,
            timestamp=datetime.now()
        )
        
        report = decision_engine.evaluate_trade_opportunity("EURUSD", candles, ticker, "M15")
        htf_item = next(item for item in report.condition_matrix if item.id == "htf_alignment")
        print(f"HTF Alignment (3 Neutral, 1 Bullish): passed={htf_item.passed} ({htf_item.details})")
        
        # Should fail since Bullish votes (1) < required minimum (3)
        assert htf_item.passed == False, "Expected HTF check to fail when majority is NEUTRAL"
        print(" -> Issue 8 HTF voting check passed.")
    finally:
        decision_engine._fetch_htf_trends = original_fetch
        decision_engine._evaluate_trend = original_trend
        decision_engine._evaluate_market_structure = original_struct_eval
        decision_engine._evaluate_momentum_volume = original_momentum

if __name__ == "__main__":
    test_trend_breaks()
    test_liquidity_sweep_opposition()
    test_risk_reward_failure()
    test_htf_alignment()
    print("\n==================================================")
    print("ALL PART 2 FIXES VERIFIED AND WORKING SUCCESSFULLY!")
    print("==================================================")
