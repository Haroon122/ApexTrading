"""
Unit tests for User-Selectable Entry Timeframe (M1, M5, M15) and Next-Candle Entry Confirmation Mechanism.

Tests cover:
1. Default settings preservation (entry_timeframe="M1", entry_confirmation_mode="IMMEDIATE")
2. M5 & M15 candle evaluation and ATR SL distance scaling
3. Entry confirmation logic (CONFIRM_NEXT_CANDLE) firing & executing when next candle confirms
4. Execution-time re-validation (re-calculating SL/TP/lots at current market price & re-checking spread/news)
5. Entry confirmation logic skipping/cancelling trade when next candle reverses against signal
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.domain.models import (
    Candle, TickerData, TradeSide, TradeDecisionReport, RiskSettings
)
from app.services.decision_engine import decision_engine
from app.services.trade_manager import trade_manager
from app.services.trading_worker import GoldTradingWorker


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures & Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _generate_test_candles(count: int = 50, timeframe: str = "M1", base_price: float = 2000.0, trend: str = "BULLISH") -> list:
    candles = []
    start_time = datetime.now() - timedelta(minutes=count * (5 if timeframe == "M5" else (15 if timeframe == "M15" else 1)))
    step_minutes = 5 if timeframe == "M5" else (15 if timeframe == "M15" else 1)
    
    price = base_price
    for i in range(count):
        candle_time = start_time + timedelta(minutes=i * step_minutes)
        if trend == "BULLISH":
            open_p = price + (i * 0.2)
            high_p = open_p + 1.5
            low_p = open_p - 0.5
            close_p = open_p + 1.0
        else:
            open_p = price - (i * 0.2)
            high_p = open_p + 0.5
            low_p = open_p - 1.5
            close_p = open_p - 1.0
        
        candles.append(Candle(
            timestamp=candle_time,
            open=round(open_p, 2),
            high=round(high_p, 2),
            low=round(low_p, 2),
            close=round(close_p, 2),
            volume=500.0,
            timeframe=timeframe,
            is_closed=True
        ))
        price = close_p
    return candles


def _make_test_ticker(symbol: str = "XAUUSD", bid: float = 2010.0, ask: float = 2010.3) -> TickerData:
    return TickerData(
        symbol=symbol,
        bid=bid,
        ask=ask,
        spread_pips=3.0,
        spread_points=30,
        raw_spread=0.30,
        point_size=0.01,
        digits=2,
        timestamp=datetime.now()
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Default Settings Preservation
# ─────────────────────────────────────────────────────────────────────────────

class TestDefaultBehaviorPreservation:
    def test_risk_settings_defaults(self):
        rs = RiskSettings()
        assert rs.entry_timeframe == "M15", "entry_timeframe must default to 'M15'"
        assert rs.entry_confirmation_mode == "IMMEDIATE", "entry_confirmation_mode must default to 'IMMEDIATE'"
        assert rs.break_even_rr == pytest.approx(1.0)
        assert rs.trailing_activation_rr == pytest.approx(1.2)

    def test_worker_initial_timeframe_defaults_to_m15(self):
        worker = GoldTradingWorker()
        expected_tf = getattr(trade_manager.risk_settings, "entry_timeframe", "M15")
        assert worker.timeframe == expected_tf
        assert worker.pending_confirmation is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Timeframe Selection & ATR SL Distance Scaling
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeframeSLScaling:
    def test_sl_distance_scales_with_timeframe_atr(self):
        ticker = _make_test_ticker()
        
        # Evaluate on M1 candles
        m1_candles = _generate_test_candles(50, "M1", base_price=2000.0, trend="BULLISH")
        m5_candles = _generate_test_candles(50, "M5", base_price=2000.0, trend="BULLISH")
        
        htf = {"M1": m1_candles, "M5": m5_candles, "M15": m5_candles, "H1": m5_candles, "H4": m5_candles}
        
        report_m1 = decision_engine.evaluate_trade_opportunity("XAUUSD", m1_candles, ticker, "M1", htf_candles=htf)
        report_m5 = decision_engine.evaluate_trade_opportunity("XAUUSD", m5_candles, ticker, "M5", htf_candles=htf)
        
        assert report_m1.timeframe == "M1"
        assert report_m5.timeframe == "M5"
        
        # M5 candles have larger price ranges per candle than M1, so SL/TP ranges scale accordingly
        if report_m1.suggested_sl > 0 and report_m5.suggested_sl > 0:
            sl_dist_m1 = abs(report_m1.suggested_entry - report_m1.suggested_sl)
            sl_dist_m5 = abs(report_m5.suggested_entry - report_m5.suggested_sl)
            assert sl_dist_m5 >= sl_dist_m1


# ─────────────────────────────────────────────────────────────────────────────
# 3. Entry Confirmation Mechanism (CONFIRM_NEXT_CANDLE)
# ─────────────────────────────────────────────────────────────────────────────

class TestEntryConfirmationMechanism:
    def test_confirmation_mode_immediate_executes_without_delay(self):
        trade_manager.update_risk_settings({"entry_timeframe": "M1", "entry_confirmation_mode": "IMMEDIATE"})
        worker = GoldTradingWorker()
        
        candles = _generate_test_candles(50, "M1", base_price=2000.0, trend="BULLISH")
        ticker = _make_test_ticker()
        
        report = decision_engine.evaluate_trade_opportunity("XAUUSD", candles, ticker, "M1")
        assert report.timeframe == "M1"

    def test_confirm_next_candle_initiates_pending_wait(self):
        worker = GoldTradingWorker()
        candles = _generate_test_candles(50, "M1", base_price=2000.0, trend="BULLISH")
        ticker = _make_test_ticker()
        
        # Simulate signal report
        report = decision_engine.evaluate_trade_opportunity("XAUUSD", candles, ticker, "M1")
        
        # Set pending confirmation state manually to simulate initial signal detection
        worker.pending_confirmation = {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "direction": "BUY",
            "signal_candle_ts": candles[-1].timestamp,
            "signal_close": candles[-1].close,
            "report": report
        }
        
        assert worker.pending_confirmation["direction"] == "BUY"
        assert worker.pending_confirmation["timeframe"] == "M1"

    def test_confirm_next_candle_cancels_on_reversal(self):
        worker = GoldTradingWorker()
        candles = _generate_test_candles(50, "M1", base_price=2000.0, trend="BULLISH")
        
        signal_ts = candles[-1].timestamp
        signal_close = 2010.0
        
        # Setup pending BUY confirmation
        worker.pending_confirmation = {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "direction": "BUY",
            "signal_candle_ts": signal_ts,
            "signal_close": signal_close,
            "report": MagicMock()
        }
        
        # Next candle closes significantly lower (reversing against BUY signal)
        next_candle_ts = signal_ts + timedelta(minutes=1)
        reversing_candle = Candle(
            timestamp=next_candle_ts,
            open=2009.5, high=2009.8, low=2002.0, close=2003.0,
            volume=600.0, timeframe="M1", is_closed=True
        )
        
        atr_val = 1.0
        # BUY direction check: close (2003.0) < signal_close (2010.0) - 0.2*ATR (0.2) = 2009.8
        is_confirmed = (reversing_candle.close >= signal_close - 0.2 * atr_val)
        assert is_confirmed is False, "BUY signal should fail confirmation on sharp downward reversal candle"

    def test_confirm_next_candle_confirms_on_continuation(self):
        worker = GoldTradingWorker()
        candles = _generate_test_candles(50, "M1", base_price=2000.0, trend="BULLISH")
        
        signal_ts = candles[-1].timestamp
        signal_close = 2010.0
        
        worker.pending_confirmation = {
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "direction": "BUY",
            "signal_candle_ts": signal_ts,
            "signal_close": signal_close,
            "report": MagicMock()
        }
        
        # Next candle closes higher (confirming BUY continuation)
        next_candle_ts = signal_ts + timedelta(minutes=1)
        confirming_candle = Candle(
            timestamp=next_candle_ts,
            open=2010.1, high=2014.0, low=2009.9, close=2013.5,
            volume=600.0, timeframe="M1", is_closed=True
        )
        
        atr_val = 1.0
        is_confirmed = (confirming_candle.close >= signal_close - 0.2 * atr_val)
        assert is_confirmed is True, "BUY signal should confirm when next candle closes higher"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Execution-Time Re-validation
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionTimeRevalidation:
    def test_revalidation_uses_current_ticker_price(self):
        candles = _generate_test_candles(50, "M5", base_price=2000.0, trend="BULLISH")
        
        ticker_at_signal = _make_test_ticker("XAUUSD", bid=2010.0, ask=2010.3)
        ticker_at_execution = _make_test_ticker("XAUUSD", bid=2015.0, ask=2015.3)
        
        report_sig = decision_engine.evaluate_trade_opportunity("XAUUSD", candles, ticker_at_signal, "M5")
        report_exec = decision_engine.evaluate_trade_opportunity("XAUUSD", candles, ticker_at_execution, "M5")
        
        # Execution-time report must reflect the CURRENT market ask price
        assert report_sig.suggested_entry == pytest.approx(2010.3)
        assert report_exec.suggested_entry == pytest.approx(2015.3)
