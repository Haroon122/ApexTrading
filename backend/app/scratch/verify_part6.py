import sys
import os
from datetime import datetime, timedelta

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.domain.models import Candle, TickerData, TradeSide, EngineState, BotStatus
from app.services.decision_engine import decision_engine
from app.services.trading_worker import trading_worker

def make_synthetic_candles(trend_type: str, count: int = 50) -> list:
    candles = []
    base_price = 1.08500
    curr_time = datetime.now() - timedelta(minutes=15 * count)
    
    for i in range(count):
        # Base wave
        phase = i % 10
        wave_change = 0.0005 if phase < 5 else -0.0005
            
        # Trend component
        if trend_type == "bullish":
            trend_change = 0.0004
        elif trend_type == "bearish":
            trend_change = -0.0004
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

def test_directional_scoring():
    print("\n==================================================")
    print("TESTING ISSUE 26 & 27: Directional Scoring & UI Consistency")
    print("==================================================")

    # Mock heuristic scorer to prevent random variation
    from app.services.decision_engine import heuristic_scorer
    original_calc = heuristic_scorer.calculate_score

    # Setup basic inputs
    ticker = TickerData(
        symbol="EURUSD",
        bid=1.0850,
        ask=1.0851,
        spread_pips=1.0,
        timestamp=datetime.now()
    )

    try:
        # Scenario 1: Strong Bullish Synthetic Data
        print("\n--- Scenario 1: Strong Bullish Setup ---")
        candles = make_synthetic_candles("bullish", 60)
        
        from app.domain.models import HeuristicScoringResult
        heuristic_scorer.calculate_score = lambda *args, **kwargs: HeuristicScoringResult(
            symbol="EURUSD",
            timeframe="M15",
            buy_score=0.90,
            sell_score=0.05,
            confidence_score=0.90,
            expected_movement_pips=15.0,
            expected_holding_bars=6,
            top_factors={"Bullish EMA ribbon": 0.5, "Strong buying momentum": 0.4},
            timestamp=datetime.now()
        )

        report = decision_engine.evaluate_trade_opportunity("EURUSD", candles, ticker, "M15")
        print(f"LONG_SCORE: {report.long_score:.4f} | SHORT_SCORE: {report.short_score:.4f}")
        print(f"Decision: {report.decision} | Explanation: {report.summary_explanation}")
        
        assert report.long_score > report.short_score, "LONG_SCORE should be higher than SHORT_SCORE"
        assert report.decision in (TradeSide.BUY, TradeSide.HOLD), "Decision must be BUY or HOLD"
        assert report.decision != TradeSide.SELL, "Decision must never be SELL in a strong bullish market"

        # Scenario 2: Strong Bearish Synthetic Data
        print("\n--- Scenario 2: Strong Bearish Setup ---")
        candles_bear = make_synthetic_candles("bearish", 60)
        
        heuristic_scorer.calculate_score = lambda *args, **kwargs: HeuristicScoringResult(
            symbol="EURUSD",
            timeframe="M15",
            buy_score=0.05,
            sell_score=0.90,
            confidence_score=0.90,
            expected_movement_pips=15.0,
            expected_holding_bars=6,
            top_factors={"Bearish EMA ribbon": 0.5, "Strong selling momentum": 0.4},
            timestamp=datetime.now()
        )

        report_bear = decision_engine.evaluate_trade_opportunity("EURUSD", candles_bear, ticker, "M15")
        print(f"LONG_SCORE: {report_bear.long_score:.4f} | SHORT_SCORE: {report_bear.short_score:.4f}")
        print(f"Decision: {report_bear.decision} | Explanation: {report_bear.summary_explanation}")
        
        assert report_bear.short_score > report_bear.long_score, "SHORT_SCORE should be higher than LONG_SCORE"
        assert report_bear.decision in (TradeSide.SELL, TradeSide.HOLD), "Decision must be SELL or HOLD"
        assert report_bear.decision != TradeSide.BUY, "Decision must never be BUY in a strong bearish market"

        # Scenario 3: Sideways/Choppy Synthetic Data
        print("\n--- Scenario 3: Sideways/Neutral Setup ---")
        candles_flat = make_synthetic_candles("sideways", 60)
        
        heuristic_scorer.calculate_score = lambda *args, **kwargs: HeuristicScoringResult(
            symbol="EURUSD",
            timeframe="M15",
            buy_score=0.30,
            sell_score=0.30,
            confidence_score=0.30,
            expected_movement_pips=0.0,
            expected_holding_bars=6,
            top_factors={"Sideways choppy structure": 1.0},
            timestamp=datetime.now()
        )

        report_flat = decision_engine.evaluate_trade_opportunity("EURUSD", candles_flat, ticker, "M15")
        print(f"LONG_SCORE: {report_flat.long_score:.4f} | SHORT_SCORE: {report_flat.short_score:.4f}")
        print(f"Decision: {report_flat.decision} | Explanation: {report_flat.summary_explanation}")
        
        assert report_flat.long_score < 0.70 and report_flat.short_score < 0.70, "Both scores should be below threshold"
        assert report_flat.decision == TradeSide.HOLD, "Decision must be HOLD"

        # Scenario 4: Mixed / Conflicting Pillars
        print("\n--- Scenario 4: Mixed/Conflicting Setup ---")
        heuristic_scorer.calculate_score = lambda *args, **kwargs: HeuristicScoringResult(
            symbol="EURUSD",
            timeframe="M15",
            buy_score=0.50,
            sell_score=0.45,
            confidence_score=0.50,
            expected_movement_pips=0.0,
            expected_holding_bars=6,
            top_factors={"Conflicting indicators": 1.0},
            timestamp=datetime.now()
        )

        report_mixed = decision_engine.evaluate_trade_opportunity("EURUSD", candles_flat, ticker, "M15")
        print(f"LONG_SCORE: {report_mixed.long_score:.4f} | SHORT_SCORE: {report_mixed.short_score:.4f}")
        print(f"Decision: {report_mixed.decision} | Explanation: {report_mixed.summary_explanation}")
        
        assert report_mixed.decision == TradeSide.HOLD, "Decision must be HOLD due to mixed signals"
        print(" -> Directional scoring scenarios passed successfully.")

    finally:
         heuristic_scorer.calculate_score = original_calc

def test_state_machine():
    print("\n==================================================")
    print("TESTING ISSUE 28: Engine State Machine & Pre-flight Checks")
    print("==================================================")

    print("Initial State:", trading_worker.state.value)
    assert trading_worker.state == EngineState.STOPPED, "Should start in STOPPED state"

    # Test pre-flight checks offline bypass
    from app.services.trade_manager import trade_manager
    original_risk = trade_manager.risk_settings
    original_bot_status = trade_manager.bot_status
    
    try:
        # Mock empty risk settings to trigger a pre-flight failure
        trade_manager.risk_settings = None
        
        print("\nStarting worker with missing risk settings (Should fail pre-flight)...")
        success, msg = asyncio.run(trading_worker.start("EURUSD", "M15"))
        print(f"Start success: {success} | Message: {msg}")
        print(f"Current State: {trading_worker.state.value} | Reason: {trading_worker.status_reason}")
        
        assert not success, "Worker start should fail without risk settings"
        assert trading_worker.state == EngineState.ERROR, "State should transition to ERROR"
        assert "Pre-flight failed" in trading_worker.status_reason, "Reason should indicate pre-flight failure"
        
    finally:
        trade_manager.risk_settings = original_risk

    try:
        # Set bot status to RUNNING to verify successful RUNNING state transition in loop
        trade_manager.bot_status = BotStatus.RUNNING
        
        print("\nStarting worker with valid settings (Should pass pre-flight and run)...")
        success, msg = asyncio.run(trading_worker.start("EURUSD", "M15"))
        print(f"Start success: {success} | Message: {msg}")
        print(f"Current State: {trading_worker.state.value} | Reason: {trading_worker.status_reason}")
        
        assert success, "Worker should start successfully"
        assert trading_worker.state == EngineState.RUNNING, "State should transition to RUNNING"
        
        # Stop bot
        print("\nStopping worker...")
        stopped, stop_msg = asyncio.run(trading_worker.stop())
        print(f"Stop success: {stopped} | Message: {stop_msg}")
        print(f"Current State: {trading_worker.state.value} | Reason: {trading_worker.status_reason}")
        
        assert stopped, "Worker should stop successfully"
        assert trading_worker.state == EngineState.STOPPED, "State should transition back to STOPPED"
        print(" -> State machine transitions passed successfully.")
        
    finally:
        trade_manager.bot_status = original_bot_status

if __name__ == "__main__":
    import asyncio
    test_directional_scoring()
    test_state_machine()
    print("\n==================================================")
    print("ALL PART 6 FIXES VERIFIED AND WORKING SUCCESSFULLY!")
    print("==================================================")
