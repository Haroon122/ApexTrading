"""
Automated Unit Tests for SL/TP Dollar Amounts and Open Position PnL Pips Calculation
Verifies Bug 1 (SL/TP Dollar Risk Accuracy) and Bug 2 (Unrealized PnL Pips for Open Positions).
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.symbol_specs import calculate_potential_pnl_usd, get_symbol_specs
from app.domain.models import TradeSide, TradeRecord, OrderType, TradeState
from datetime import datetime


def test_bug1_example1_sltp_dollar_risk():
    """
    BUG 1 TEST — Example 1:
    SELL position, entry = 4581.79, SL = 4584.76, lots = 0.10 on XAUUSD.
    Price distance = 2.97. Pip size = 0.10. Pips = 29.7.
    Pip value per lot = 10.0.
    Expected dollar loss = -(29.7 * 10.0 * 0.10) = -$29.70.
    """
    symbol = "XAUUSD"
    entry = 4581.79
    sl = 4584.76
    lots = 0.10

    sl_dollar_loss = calculate_potential_pnl_usd(symbol, TradeSide.SELL, entry, sl, lots)
    assert abs(sl_dollar_loss - (-29.70)) < 0.01, f"Expected -$29.70, got {sl_dollar_loss}"


def test_bug1_example2_sltp_dollar_loss_and_profit():
    """
    BUG 1 TEST — Example 2:
    SELL position, entry = 4583.52, SL = 4586.31, TP = 4577.23, lots = 0.10 on XAUUSD.
    SL distance = 2.79 -> 27.9 pips -> Expected SL loss = -$27.90.
    TP distance = 6.29 -> 62.9 pips -> Expected TP profit = +$62.90.
    """
    symbol = "XAUUSD"
    entry = 4583.52
    sl = 4586.31
    tp = 4577.23
    lots = 0.10

    sl_dollar_loss = calculate_potential_pnl_usd(symbol, TradeSide.SELL, entry, sl, lots)
    tp_dollar_profit = calculate_potential_pnl_usd(symbol, TradeSide.SELL, entry, tp, lots)

    assert abs(sl_dollar_loss - (-27.90)) < 0.01, f"Expected -$27.90, got {sl_dollar_loss}"
    assert abs(tp_dollar_profit - 62.90) < 0.01, f"Expected +$62.90, got {tp_dollar_profit}"


def test_bug2_unrealized_pips_calculation():
    """
    BUG 2 TEST:
    Verify that for a BUY position with entry=2380.00 and current bid=2382.50 on XAUUSD (pip_size=0.10),
    the unrealized PnL in pips evaluates to 25.0.
    """
    symbol = "XAUUSD"
    specs = get_symbol_specs(symbol)
    pip_size = specs["pip_size"]
    assert pip_size == 0.10

    entry_price = 2380.00
    current_bid = 2382.50
    side = TradeSide.BUY

    pips = (current_bid - entry_price) / pip_size
    assert round(pips, 1) == 25.0, f"Expected 25.0 pips, got {pips}"


def test_bug2_mt5_adapter_get_positions_pips(monkeypatch):
    """
    BUG 2 TEST — MT5 Adapter position pips parsing & fallback handling:
    Mocks MT5 positions & symbol_info_tick to verify pnl_pips calculation and fallback when tick is None.
    """
    from app.adapters.mt5_adapter import mt5_adapter
    from app.services.session_manager import session_manager

    class MockRawPos:
        ticket = 991122
        symbol = "XAUUSD"
        type = 0  # BUY
        price_open = 2380.00
        sl = 2375.00
        tp = 2390.00
        volume = 0.10
        time = 1700000000
        profit = 25.00

    class MockTick:
        bid = 2382.50
        ask = 2382.60

    # 1. Enable mock session
    monkeypatch.setattr(session_manager, "use_live_mt5", True)
    from app.adapters import mt5_adapter as mt5_module
    monkeypatch.setattr(mt5_module, "MT5_AVAILABLE", True)

    import MetaTrader5 as mock_mt5
    monkeypatch.setattr(mock_mt5, "positions_get", lambda: [MockRawPos()])
    monkeypatch.setattr(mock_mt5, "symbol_info_tick", lambda sym: MockTick())

    positions = mt5_adapter.get_positions()
    assert len(positions) == 1
    pos = positions[0]
    assert pos.pnl_pips == 25.0
    assert pos.potential_loss_usd == -50.00
    assert pos.potential_profit_usd == 100.00

    # 2. Test fallback when tick is None (e.g. market closed)
    monkeypatch.setattr(mock_mt5, "symbol_info_tick", lambda sym: None)
    positions_fallback = mt5_adapter.get_positions()
    assert len(positions_fallback) == 1
    assert positions_fallback[0].pnl_pips == 0.0
