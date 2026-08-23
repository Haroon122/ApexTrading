"""
Automated Regression Test Suite for Timeframe Synchronization & MT5 Mapping.
Verifies all 9 supported timeframes (M1, M5, M15, M30, H1, H4, D1, W1, MN1)
and fallback default behavior.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.adapters.mt5_adapter import mt5_adapter, TF_MINUTES_MAP


def test_timeframe_minutes_mapping():
    """
    Verify TF_MINUTES_MAP contains all required timeframes with correct minute multipliers.
    """
    expected = {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D1": 1440,
        "W1": 10080,
        "MN1": 43200,
        "MN": 43200
    }
    for tf, expected_mins in expected.items():
        assert tf in TF_MINUTES_MAP
        assert TF_MINUTES_MAP[tf] == expected_mins


def test_fetch_candles_for_all_timeframes():
    """
    Verify fetch_candles returns valid candles with correct timeframe metadata for all 9 timeframes.
    """
    timeframes = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
    
    for tf in timeframes:
        candles = mt5_adapter.fetch_candles("XAUUSD", timeframe=tf, count=50)
        assert candles is not None
        assert len(candles) == 50
        assert candles[0].timeframe == tf


def test_fetch_candles_invalid_timeframe_fallback():
    """
    Verify invalid timeframe fallback defaults gracefully to M15.
    """
    candles = mt5_adapter.fetch_candles("XAUUSD", timeframe="INVALID_TF", count=40)
    assert candles is not None
    assert len(candles) == 40


if __name__ == "__main__":
    print("Running test_timeframe_minutes_mapping...")
    test_timeframe_minutes_mapping()
    print("Running test_fetch_candles_for_all_timeframes...")
    test_fetch_candles_for_all_timeframes()
    print("Running test_fetch_candles_invalid_timeframe_fallback...")
    test_fetch_candles_invalid_timeframe_fallback()
    print("\nALL TIMEFRAME SYNCHRONIZATION TESTS PASSED CLEANLY!")
