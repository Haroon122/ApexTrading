"""
Automated Unit Tests for ApexTrader Profitability & Algorithmic Strategy Upgrades
Verifies:
1. SETUP C HTF Trend & RSI Divergence Gate
2. BB Midline Pullback Volatility Squeeze Filter
3. Proximity Stacking Guard in Risk Manager
"""

import os
import sys
import pytest
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.domain.models import TradeSide, TradeDecisionReport, TradeRecord, OrderType, TradeState, AccountInfo
from app.services.decision_engine import decision_engine
from app.services.dark_venus_bb import dark_venus_engine
from app.services.risk_manager import risk_manager
from app.services.trade_manager import trade_manager


def test_setup_c_htf_trend_filter():
    """
    SETUP C GUARD TEST:
    Verify that Buy-Side Liquidity Sweep SELL signals are blocked when RSI > 65 or HTF trend opposes,
    and accepted when parameters match.
    """
    class MockSweep:
        side = TradeSide.BUY
        price_level = 4590.00

    class MockStruct:
        liquidity_sweeps = [MockSweep()]
        order_blocks = []
        latest_structure = None

    df = pd.DataFrame([{
        "close": 4585.0, "open": 4588.0, "high": 4591.0, "low": 4584.0,
        "rsi_14": 70.0, "macd_hist": -0.5, "rvol": 1.5, "atr_14": 1.5,
        "bb_upper": 4590.0, "bb_middle": 4585.0, "bb_lower": 4580.0, "bb_width": 0.002
    }])

    ticker = type("T", (), {"digits": 2, "point_size": 0.01, "ask": 4585.0, "bid": 4585.0})()

    setups = decision_engine.evaluate_setups(
        symbol="XAUUSD",
        candles=[],
        ticker=ticker,
        timeframe="M1",
        htf_candles={"H1": []},
        df=df,
        structure=MockStruct(),
        regime="RANGE"
    )

    sell_sweeps = [s for s in setups if s.get("id") == "setup_liq_sweep_sell"]
    assert len(sell_sweeps) == 0, "Buy-side sweep SELL should be rejected when RSI > 65"


def test_bb_midline_squeeze_filter():
    """
    BB MIDLINE SQUEEZE TEST:
    Verify Mode 5 (BB Midline Pullback) is suppressed when bb_width < 0.0015 (low-volatility squeeze).
    """
    row_squeeze = {
        "close": 2382.0, "open": 2380.0, "high": 2383.0, "low": 2379.0,
        "bb_upper": 2385.0, "bb_middle": 2380.0, "bb_lower": 2375.0,
        "bb_width": 0.0010,  # Below 0.0015 squeeze threshold
        "percent_b": 0.5, "bb_squeeze": True, "bb_expansion": False,
        "rsi_14": 55.0, "macd_hist": 0.5, "rvol": 1.0, "atr_14": 1.0
    }
    df_squeeze = pd.DataFrame([row_squeeze] * 25)

    setups_squeeze = dark_venus_engine.evaluate_bb_signals(df_squeeze, regime="UPTREND", h1_trend="BULLISH")
    mode5_list = [s for s in setups_squeeze if s.get("id") == "dv_mode_5"]
    assert len(mode5_list) == 1
    assert mode5_list[0]["active"] is False, "BB Midline Pullback should be inactive during low-volatility squeeze"

    row_normal = {
        "close": 2382.0, "open": 2380.0, "high": 2383.0, "low": 2379.0,
        "bb_upper": 2385.0, "bb_middle": 2380.0, "bb_lower": 2375.0,
        "bb_width": 0.0025,  # Above 0.0015
        "percent_b": 0.5, "bb_squeeze": False, "bb_expansion": False,
        "rsi_14": 55.0, "macd_hist": 0.5, "rvol": 1.0, "atr_14": 1.0
    }
    df_normal = pd.DataFrame([row_normal] * 25)

    setups_normal = dark_venus_engine.evaluate_bb_signals(df_normal, regime="UPTREND", h1_trend="BULLISH")
    mode5_normal = [s for s in setups_normal if s.get("id") == "dv_mode_5"][0]
    assert mode5_normal["active"] is True, "BB Midline Pullback should be active when bandwidth >= 0.0015"


def test_proximity_stacking_guard():
    """
    PROXIMITY GUARD TEST:
    Verify risk_manager rejects a new trade if an open position already exists within 20 pips ($2.00).
    """
    mock_trade = TradeRecord(
        trade_id="1001",
        symbol="XAUUSD",
        side=TradeSide.SELL,
        order_type=OrderType.MARKET,
        entry_price=4580.00,
        sl_price=4583.00,
        tp_price=4575.00,
        lots=0.10,
        state=TradeState.OPEN,
        open_time=datetime.now()
    )
    trade_manager.active_trades["1001"] = mock_trade

    account = AccountInfo(
        account_number=12345678,
        broker="MetaQuotes-Demo",
        currency="USD",
        balance=2500.0,
        equity=2500.0,
        margin_free=2000.0
    )

    # Candidate trade at 4581.00 (10 pips away < 20 pips buffer) -> SHOULD BE BLOCKED
    report_close = TradeDecisionReport(
        symbol="XAUUSD",
        timeframe="M1",
        decision=TradeSide.SELL,
        confidence=0.85,
        confirmation_score=85.0,
        reasoning=["Test Proximity Close"],
        rejection_reasons=[],
        suggested_entry=4581.00,
        suggested_sl=4584.00,
        suggested_tp=4575.00,
        suggested_lots=0.10,
        risk_reward_ratio=2.0,
        breakdown={},
        timestamp=datetime.now()
    )

    res_close = risk_manager.evaluate_risk(account, report_close, "XAUUSD")
    assert res_close.passed is False
    assert any("PROXIMITY GUARD" in r for r in res_close.reasons)

    # Candidate trade at 4585.00 (50 pips away > 20 pips buffer) -> SHOULD PASS PROXIMITY GUARD
    report_far = TradeDecisionReport(
        symbol="XAUUSD",
        timeframe="M1",
        decision=TradeSide.SELL,
        confidence=0.85,
        confirmation_score=85.0,
        reasoning=["Test Proximity Far"],
        rejection_reasons=[],
        suggested_entry=4585.00,
        suggested_sl=4588.00,
        suggested_tp=4579.00,
        suggested_lots=0.10,
        risk_reward_ratio=2.0,
        breakdown={},
        timestamp=datetime.now()
    )

    res_far = risk_manager.evaluate_risk(account, report_far, "XAUUSD")
    assert not any("PROXIMITY GUARD" in r for r in res_far.reasons)

    trade_manager.active_trades.pop("1001", None)
