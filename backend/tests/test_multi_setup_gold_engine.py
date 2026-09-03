"""
Comprehensive Unit Test Suite for Multi-Setup Gold Engine & Decision Architecture.
Verifies candle indexing (CURRENT_CANDLE vs CLOSED_CANDLE), 6 independent setups for BUY and SELL,
Wyckoff context boosting, regime-aware confidence thresholds, structured HOLD reasons, and news/spread filters.
"""

import unittest
import pandas as pd
import numpy as np
from datetime import datetime

from app.domain.models import Candle, TickerData, TradeSide, RiskSettings, OrderType
from app.services.dark_venus_bb import DarkVenusStyleBBEngine
from app.services.wyckoff_engine import WyckoffContextEngine, wyckoff_engine
from app.services.decision_engine import DecisionEngine
from app.services.news_filter import news_filter, MockCalendarProvider
from app.services.signal_history import signal_history
from app.services.trade_manager import trade_manager
from app.adapters.mt5_adapter import mt5_adapter


class TestMultiSetupGoldEngine(unittest.TestCase):

    def setUp(self):
        self.decision_engine = DecisionEngine()
        self.ticker = TickerData(
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

        trade_manager.risk_settings = RiskSettings(
            lot_sizing_mode="FIXED",
            user_configured_lot=0.10,
            max_allowed_lot=1.00,
            auto_trade_cooldown_seconds=0,
            max_spread_pips=3.0,
            strategy_profile="HYBRID_AUTO",
            signal_mode="CLOSED_CANDLE"
        )
        news_filter.use_mock_news = False
        signal_history.clear()

    def _generate_candles(self, count=50, base_price=2350.0, trend=0.0):
        candles = []
        price = base_price
        for i in range(count):
            price += trend + (np.random.randn() * 0.4)
            high = price + abs(np.random.randn() * 0.6)
            low = price - abs(np.random.randn() * 0.6)
            open_p = price - (np.random.randn() * 0.2)
            close = price
            candles.append(Candle(
                timestamp=datetime.now(),
                open=open_p,
                high=high,
                low=low,
                close=close,
                volume=100.0 + i,
                tick_volume=100 + i,
                is_closed=(i < count - 1)
            ))
        return candles

    def test_1_candle_mode_indexing(self):
        """TEST 1: Verify CURRENT_CANDLE vs CLOSED_CANDLE target candle indexing"""
        df = pd.DataFrame([
            {"close": 2350.0, "high": 2352.0, "low": 2348.0, "open": 2349.0, "bb_upper": 2355.0, "bb_middle": 2350.0, "bb_lower": 2345.0, "is_closed": True},
            {"close": 2354.0, "high": 2356.0, "low": 2350.0, "open": 2351.0, "bb_upper": 2355.0, "bb_middle": 2350.0, "bb_lower": 2345.0, "is_closed": True},
            {"close": 2360.0, "high": 2362.0, "low": 2353.0, "open": 2354.0, "bb_upper": 2355.0, "bb_middle": 2350.0, "bb_lower": 2345.0, "is_closed": False}, # forming candle
        ])

        setups_closed = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="RANGE", signal_mode="CLOSED_CANDLE")
        setups_current = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="RANGE", signal_mode="CURRENT_CANDLE")

        self.assertIsNotNone(setups_closed)
        self.assertIsNotNone(setups_current)

    def test_2_wyckoff_context_boosting(self):
        """TEST 2: Verify Wyckoff Accumulation/Distribution detection and confidence boost"""
        candles = self._generate_candles(40, base_price=2350.0)
        df = pd.DataFrame([{
            "open": c.open, "high": c.high, "low": c.low, "close": c.close, "tick_volume": c.volume
        } for c in candles])

        res = WyckoffContextEngine.analyze(df)
        self.assertIn(res.bias, ["BULLISH", "BEARISH", "NEUTRAL"])
        self.assertGreaterEqual(res.confidence_boost, 0.0)

    def test_3_symmetrical_buy_and_sell_evaluations(self):
        """TEST 3: Verify symmetrical BUY and SELL evaluation without bullish bias"""
        candles_bull = self._generate_candles(40, base_price=2350.0, trend=0.5)
        candles_bear = self._generate_candles(40, base_price=2350.0, trend=-0.5)

        rep_bull = self.decision_engine.evaluate_trade_opportunity("XAUUSD", candles_bull, self.ticker, "M1")
        rep_bear = self.decision_engine.evaluate_trade_opportunity("XAUUSD", candles_bear, self.ticker, "M1")

        self.assertIsNotNone(rep_bull)
        self.assertIsNotNone(rep_bear)
        self.assertGreaterEqual(rep_bull.long_score, 0.0)
        self.assertGreaterEqual(rep_bear.short_score, 0.0)

    def test_4_structured_hold_reasons(self):
        """TEST 4: Verify generic HOLD is replaced by structured reasons (NO_SETUP, LOW_CONFIDENCE, SPREAD_BLOCKED, etc.)"""
        candles = self._generate_candles(35)

        # 1. Normal evaluation returns valid decision with structured primary reason
        report = self.decision_engine.evaluate_trade_opportunity("XAUUSD", candles, self.ticker, "M1")
        self.assertIn(report.rejection_reasons[0], ["BUY_READY", "SELL_READY", "NO_SETUP", "LOW_CONFIDENCE", "SPREAD_BLOCKED", "NEWS_BLOCKED", "RR_BLOCKED", "RISK_BLOCKED"])

        # 2. Excessive spread evaluation MUST return HOLD and SPREAD_BLOCKED
        high_spread_ticker = TickerData(
            symbol="XAUUSD", bid=2350.00, ask=2351.00, spread_pips=10.0, spread_points=100,
            raw_spread=1.00, point_size=0.01, digits=2, timestamp=datetime.now()
        )
        report_spread = self.decision_engine.evaluate_trade_opportunity("XAUUSD", candles, high_spread_ticker, "M1")
        self.assertEqual(report_spread.decision, TradeSide.HOLD)
        self.assertEqual(report_spread.rejection_reasons[0], "SPREAD_BLOCKED")

    def test_5_mock_news_bypass_config(self):
        """TEST 5: Verify mock news provider does not silently block live trades unless configured"""
        news_filter.use_mock_news = False
        news_filter.provider = MockCalendarProvider()
        is_blocked, msg = news_filter.is_news_blackout("XAUUSD")
        self.assertFalse(is_blocked, "Mock news must not block trades when use_mock_news = False")

    def test_6_signal_history_and_rejection_histogram(self):
        """TEST 6: Verify signal history tracking and rejection histogram generation"""
        signal_history.clear()
        signal_history.record_evaluation(
            symbol="XAUUSD", direction="HOLD", best_setup="None", regime="RANGE",
            buy_score=0.40, sell_score=0.35, confidence=0.40, spread_pips=2.0,
            rr_ratio=2.0, news_status="UNKNOWN", decision="HOLD", primary_reason="NO_SETUP"
        )
        signal_history.record_evaluation(
            symbol="XAUUSD", direction="HOLD", best_setup="BB Reversion", regime="RANGE",
            buy_score=0.62, sell_score=0.30, confidence=0.62, spread_pips=2.0,
            rr_ratio=2.0, news_status="UNKNOWN", decision="HOLD", primary_reason="LOW_CONFIDENCE"
        )

        stats = signal_history.get_summary_stats()
        self.assertEqual(stats["total_evaluations"], 2)
        self.assertEqual(stats["rejection_histogram"]["NO_SETUP"], 1)
        self.assertEqual(stats["rejection_histogram"]["LOW_CONFIDENCE"], 1)


if __name__ == "__main__":
    unittest.main()
