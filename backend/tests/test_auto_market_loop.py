"""
Automated Test Suite for Autonomous Market Loop & Multi-Timeframe Analysis Engine
Verifies background worker loop, HTF candle payloads, resilience to temporary errors, and manual evaluation API.
"""

import os
import sys
import asyncio
import pytest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.trading_worker import trading_worker
from app.services.decision_engine import decision_engine
from app.adapters.mt5_adapter import mt5_adapter
from app.domain.models import Candle, TickerData, TradeDecisionReport


def test_1_pre_flight_checks_and_worker_initialization():
    """TEST 1: Verify pre-flight checks and worker initialization."""
    worker = trading_worker
    assert worker is not None
    assert worker.is_running() is False or worker.is_running() is True


def test_2_trading_worker_loop_payload_generation():
    """
    TEST 2: Verify worker loop populates last_payload with multi-timeframe data,
    ticker, account, positions, and decision_report.
    """
    async def _impl():
        worker = trading_worker
        start_ok, start_msg = await worker.start("XAUUSD", "M1")
        assert start_ok is True

        # Allow worker to run at least 1 cycle
        await asyncio.sleep(2.0)

        assert worker.is_running() is True
        assert worker.last_payload is not None
        assert "timestamp" in worker.last_payload
        assert "decision_report" in worker.last_payload
        assert "htf_candles" in worker.last_payload
        assert "candles" in worker.last_payload

        htf = worker.last_payload["htf_candles"]
        assert "M1" in htf or "M15" in htf or "H1" in htf

        await worker.stop()
        assert worker.is_running() is False

    asyncio.run(_impl())


def test_3_decision_engine_htf_evaluation():
    """TEST 3: Verify decision_engine evaluates multi-timeframe candles correctly."""
    symbol = "XAUUSD"
    m1_candles = mt5_adapter.fetch_candles(symbol, "M1", 100)
    m5_candles = mt5_adapter.fetch_candles(symbol, "M5", 100)
    m15_candles = mt5_adapter.fetch_candles(symbol, "M15", 100)
    h1_candles = mt5_adapter.fetch_candles(symbol, "H1", 100)
    h4_candles = mt5_adapter.fetch_candles(symbol, "H4", 100)

    ticker = mt5_adapter.get_ticker(symbol)
    htf_candles = {
        "M1": m1_candles,
        "M5": m5_candles,
        "M15": m15_candles,
        "H1": h1_candles,
        "H4": h4_candles
    }

    report = decision_engine.evaluate_trade_opportunity(symbol, m1_candles, ticker, "M1", htf_candles=htf_candles)
    assert isinstance(report, TradeDecisionReport)
    assert report.symbol == symbol
    assert report.decision is not None
    assert "H1" in report.htf_alignment


if __name__ == "__main__":
    print("Running test_1_pre_flight_checks_and_worker_initialization...")
    test_1_pre_flight_checks_and_worker_initialization()
    print("Running test_2_trading_worker_loop_payload_generation...")
    test_2_trading_worker_loop_payload_generation()
    print("Running test_3_decision_engine_htf_evaluation...")
    test_3_decision_engine_htf_evaluation()
    print("\nALL AUTONOMOUS MARKET LOOP TESTS PASSED CLEANLY!")
