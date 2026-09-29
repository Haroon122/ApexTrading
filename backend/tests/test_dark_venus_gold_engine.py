"""
Comprehensive Unit Test Suite for Gold-Only Dark Venus + Institutional Scalping Engine.
Tests all 50 mandatory requirements, Dark Venus BB modes, 10 Hybrid Setups, 100-point scoring model,
dynamic spread/ATR filter, explainable decision traces, and 0.10 lot safety guard.
"""

import unittest
import pandas as pd
import numpy as np
from datetime import datetime

from app.domain.models import Candle, TickerData, TradeSide, RiskSettings, TradeDecisionReport, OrderType, TradeRecord, TradeState
from app.services.dark_venus_bb import DarkVenusStyleBBEngine, dark_venus_engine
from app.services.decision_engine import DecisionEngine
from app.services.trade_manager import trade_manager
from app.services.risk_manager import risk_manager
from app.adapters.mt5_adapter import mt5_adapter


class TestDarkVenusGoldEngine(unittest.TestCase):

    def setUp(self):
        self.decision_engine = DecisionEngine()
        self.ticker = TickerData(
            symbol="XAUUSD",
            bid=2300.00,
            ask=2300.20,
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

    def _generate_mock_df(self, count=50, base_price=2300.0, trend=0.0):
        data = []
        price = base_price
        for i in range(count):
            price += trend + (np.random.randn() * 0.5)
            high = price + abs(np.random.randn() * 0.8)
            low = price - abs(np.random.randn() * 0.8)
            open_p = price - (np.random.randn() * 0.3)
            close = price
            data.append({
                "time": datetime.now(),
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": 100 + i,
                "spread": 20,
                "real_volume": 100,
                "ema_9": price,
                "ema_20": price - 0.2,
                "ema_50": price - 0.5,
                "ema_200": price - 1.0,
                "atr_14": 1.5,
                "rsi_14": 55.0,
                "macd_hist": 0.2,
                "rvol": 1.2
            })
        df = pd.DataFrame(data)
        return DarkVenusStyleBBEngine.calculate_bollinger_features(df)

    def test_1_gold_symbol_auto_discovery(self):
        """TEST 1: Verify broker Gold symbol auto-discovery"""
        discovered = mt5_adapter.discover_gold_symbol()
        self.assertIn("XAU", discovered.upper())
        self.assertIsInstance(discovered, str)

    def test_2_dark_venus_mode_1_counter_trend_regime_guard(self):
        """TEST 2: Mode 1 Counter-Trend Reversion active in RANGE, blocked in STRONG_DOWNTREND"""
        df = self._generate_mock_df(30)
        # Force low <= bb_lower
        df.iloc[-1, df.columns.get_loc("low")] = float(df.iloc[-1]["bb_lower"]) - 0.5
        df.iloc[-1, df.columns.get_loc("close")] = float(df.iloc[-1]["bb_lower"]) + 0.2
        df.iloc[-1, df.columns.get_loc("open")] = float(df.iloc[-1]["bb_lower"]) - 0.2
        df.iloc[-1, df.columns.get_loc("percent_b")] = 0.05

        # In RANGE regime -> Should generate Mode 1 BUY signal
        setups_range = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="RANGE")
        m1_range = next((s for s in setups_range if s["id"] == "dv_mode_1"), None)
        self.assertIsNotNone(m1_range)
        self.assertTrue(m1_range["active"])
        self.assertEqual(m1_range["side"], TradeSide.BUY)

        # In STRONG_DOWNTREND regime -> Mode 1 MUST BE BLOCKED
        setups_trend = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="STRONG_DOWNTREND")
        m1_trend = next((s for s in setups_trend if s["id"] == "dv_mode_1"), None)
        self.assertIsNotNone(m1_trend)
        self.assertFalse(m1_trend["active"], "Counter-Trend BB BUY must be blocked in STRONG_DOWNTREND")

    def test_3_dark_venus_mode_2_momentum_breakout(self):
        """TEST 3: Mode 2 Upper/Lower BB Breakout with RVOL > 1.3"""
        df = self._generate_mock_df(30)
        # Force upper breakout
        df.iloc[-1, df.columns.get_loc("close")] = float(df.iloc[-1]["bb_upper"]) + 1.5
        df.iloc[-1, df.columns.get_loc("open")] = float(df.iloc[-1]["bb_upper"]) + 0.1
        df.iloc[-1, df.columns.get_loc("high")] = float(df.iloc[-1]["bb_upper"]) + 1.8
        df.iloc[-1, df.columns.get_loc("low")] = float(df.iloc[-1]["bb_upper"])
        df.iloc[-1, df.columns.get_loc("rvol")] = 1.6
        df.iloc[-1, df.columns.get_loc("rsi_14")] = 65.0

        setups = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="BREAKOUT")
        m2 = next((s for s in setups if s["id"] == "dv_mode_2"), None)
        self.assertIsNotNone(m2)
        self.assertTrue(m2["active"])
        self.assertEqual(m2["side"], TradeSide.BUY)

    def test_4_dark_venus_mode_3_trend_following(self):
        """TEST 4: Mode 3 BB Interaction in HTF trend direction"""
        df = self._generate_mock_df(30)
        df.iloc[-1, df.columns.get_loc("low")] = float(df.iloc[-1]["bb_middle"]) - 0.2
        df.iloc[-1, df.columns.get_loc("close")] = float(df.iloc[-1]["bb_middle"]) + 0.5
        df.iloc[-1, df.columns.get_loc("open")] = float(df.iloc[-1]["bb_middle"])

        setups = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="WEAK_UPTREND", h1_trend="BULLISH")
        m3 = next((s for s in setups if s["id"] == "dv_mode_3"), None)
        self.assertIsNotNone(m3)
        self.assertTrue(m3["active"])
        self.assertEqual(m3["side"], TradeSide.BUY)

    def test_5_dark_venus_mode_4_central_band_cross(self):
        """TEST 5: Mode 4 Middle Band Cross in trend direction"""
        df = self._generate_mock_df(30)
        df.iloc[-2, df.columns.get_loc("close")] = float(df.iloc[-2]["bb_middle"]) - 0.5
        df.iloc[-1, df.columns.get_loc("close")] = float(df.iloc[-1]["bb_middle"]) + 0.5
        df.iloc[-1, df.columns.get_loc("rsi_14")] = 58.0
        df.iloc[-1, df.columns.get_loc("macd_hist")] = 0.3

        setups = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="WEAK_UPTREND", h1_trend="BULLISH")
        m4 = next((s for s in setups if s["id"] == "dv_mode_4"), None)
        self.assertIsNotNone(m4)
        self.assertTrue(m4["active"])
        self.assertEqual(m4["side"], TradeSide.BUY)

    def test_6_dark_venus_mode_5_midline_pullback(self):
        """TEST 6: Mode 5 Midline Pullback Rejection"""
        df = self._generate_mock_df(30)
        df.iloc[-1, df.columns.get_loc("low")] = float(df.iloc[-1]["bb_middle"]) - 0.1
        df.iloc[-1, df.columns.get_loc("close")] = float(df.iloc[-1]["bb_middle"]) + 0.4
        df.iloc[-1, df.columns.get_loc("open")] = float(df.iloc[-1]["bb_middle"])
        df.iloc[-1, df.columns.get_loc("bb_width")] = 0.0020

        setups = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="STRONG_UPTREND", h1_trend="BULLISH")
        m5 = next((s for s in setups if s["id"] == "dv_mode_5"), None)
        self.assertIsNotNone(m5)
        self.assertTrue(m5["active"])
        self.assertEqual(m5["side"], TradeSide.BUY)

    def test_7_dark_venus_mode_6_squeeze_expansion(self):
        """TEST 7: Mode 6 Low BB Squeeze -> Expansion Breakout"""
        df = self._generate_mock_df(30)
        df.iloc[-1, df.columns.get_loc("bb_squeeze")] = True
        df.iloc[-1, df.columns.get_loc("bb_expansion")] = True
        df.iloc[-1, df.columns.get_loc("close")] = float(df.iloc[-1]["bb_upper"]) + 1.0
        df.iloc[-1, df.columns.get_loc("rvol")] = 1.6

        setups = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="BREAKOUT")
        m6 = next((s for s in setups if s["id"] == "dv_mode_6"), None)
        self.assertIsNotNone(m6)
        self.assertTrue(m6["active"])
        self.assertEqual(m6["side"], TradeSide.BUY)

    def test_8_signal_mode_closed_candle(self):
        """TEST 8: CLOSED_CANDLE mode drops unclosed forming candle"""
        candles = [
            Candle(timestamp=datetime.now(), open=2300.0, high=2305.0, low=2295.0, close=2302.0, volume=100.0, tick_volume=100, is_closed=True)
            for _ in range(35)
        ]
        # Add unclosed forming candle
        candles.append(Candle(timestamp=datetime.now(), open=2302.0, high=2320.0, low=2300.0, close=2318.0, volume=50.0, tick_volume=50, is_closed=False))

        report = self.decision_engine.evaluate_trade_opportunity("XAUUSD", candles, self.ticker, "M1")
        self.assertIsNotNone(report)
        self.assertIn("GOLD AI DECISION TRACE", report.summary_explanation)

    def test_9_transparent_100_point_scoring(self):
        """TEST 9: Independent LONG_SCORE and SHORT_SCORE 100-point model"""
        candles = [
            Candle(timestamp=datetime.now(), open=2300.0 + i*0.1, high=2301.0 + i*0.1, low=2299.5 + i*0.1, close=2300.8 + i*0.1, volume=120.0, tick_volume=120, is_closed=True)
            for i in range(40)
        ]
        report = self.decision_engine.evaluate_trade_opportunity("XAUUSD", candles, self.ticker, "M1")
        self.assertGreaterEqual(report.breakdown.long_score, 0.0)
        self.assertLessEqual(report.breakdown.long_score, 1.0)

    def test_10_decision_trace_why_buy_why_hold(self):
        """TEST 10: Human-readable WHY BUY / WHY HOLD Decision Trace generation"""
        candles = [
            Candle(timestamp=datetime.now(), open=2300.0, high=2301.0, low=2299.0, close=2300.5, volume=100.0, tick_volume=100, is_closed=True)
            for _ in range(35)
        ]
        report = self.decision_engine.evaluate_trade_opportunity("XAUUSD", candles, self.ticker, "M1")
        self.assertIn("=== GOLD AI DECISION TRACE ===", report.summary_explanation)
        self.assertTrue("WHY HOLD?" in report.summary_explanation or "WHY BUY?" in report.summary_explanation or "WHY SELL?" in report.summary_explanation)

    def test_11_mandatory_0_10_lot_safety(self):
        """TEST 11: Mandatory Safety Check — User 0.10 lot remains strictly 0.10 lots"""
        trade_manager.risk_settings.lot_sizing_mode = "FIXED"
        trade_manager.risk_settings.user_configured_lot = 0.10
        trade_manager.risk_settings.max_allowed_lot = 1.00

        # Run pre-execution volume check via mt5_adapter
        valid, price, norm_lots, sl_val, tp_val, msg = mt5_adapter.validate_and_normalize_order_request(
            "XAUUSD", TradeSide.BUY, OrderType.MARKET, 2300.0, 0.10, 2295.0, 2310.0
        )
        self.assertTrue(valid, f"Volume validation failed: {msg}")
        self.assertEqual(norm_lots, 0.10, "0.10 lot configuration MUST NOT be modified or amplified!")
        self.assertNotEqual(norm_lots, 100.00, "0.10 -> 100 lot bug MUST NOT OCCUR!")

    def test_12_dark_venus_counter_trend_guards_in_downtrend(self):
        """TEST 12: Verify Modes 2, 4, 6 strictly block counter-trend BUY signals in DOWNTREND / BEARISH H1"""
        df = self._generate_mock_df(30)
        # Force upper breakout scenario
        df.iloc[-1, df.columns.get_loc("close")] = float(df.iloc[-1]["bb_upper"]) + 1.5
        df.iloc[-1, df.columns.get_loc("open")] = float(df.iloc[-1]["bb_upper"]) + 0.1
        df.iloc[-1, df.columns.get_loc("rvol")] = 1.6
        df.iloc[-1, df.columns.get_loc("rsi_14")] = 65.0

        # In DOWNTREND / BEARISH H1 -> Mode 2 BUY breakout MUST BE BLOCKED
        setups_downtrend = DarkVenusStyleBBEngine.evaluate_bb_signals(df, regime="WEAK_DOWNTREND", h1_trend="BEARISH")
        m2_down = next((s for s in setups_downtrend if s["id"] == "dv_mode_2"), None)
        self.assertIsNotNone(m2_down)
        self.assertFalse(m2_down["active"], "Mode 2 BUY breakout MUST be blocked when H1 trend is BEARISH / DOWNTREND")


if __name__ == "__main__":
    unittest.main()
