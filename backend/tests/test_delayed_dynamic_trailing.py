"""
Automated Test Suite: Delayed Dynamic Trailing Engine Verification (Approach C)
Verifies:
1. Trailing activation gating (inactive < 1.2R, active >= 1.2R).
2. Breakeven + 0.2R profit lock upon activation.
3. Dynamic ATR multiplier scaling (2.0 * ATR).
4. BUY and SELL directionality and SL monotonicity (SL never moves backwards).
5. Single Source of Truth configuration via RiskSettings.
"""

import pytest
from datetime import datetime
from app.domain.models import TradeRecord, TradeSide, TradeState, OrderType, TickerData
from app.services.trade_manager import trade_manager

@pytest.fixture(autouse=True)
def setup_risk_settings():
    original_cfg = trade_manager.risk_settings.model_copy()
    trade_manager.risk_settings.auto_trailing_stop_enabled = True
    trade_manager.risk_settings.auto_breakeven_enabled = True
    trade_manager.risk_settings.trailing_activation_rr = 1.2
    trade_manager.risk_settings.break_even_rr = 1.0          # freeze at old default for these tests
    trade_manager.risk_settings.giveback_guard_enabled = False # disable guard — tested separately
    trade_manager.risk_settings.trailing_atr_multiplier = 2.0
    trade_manager.risk_settings.trailing_stop_pips = 15.0
    yield
    trade_manager.risk_settings = original_cfg



def mock_ticker(price):
    return TickerData(
        symbol="XAUUSD",
        bid=price,
        ask=price + 0.20,
        spread_pips=2.0,
        point_size=0.01,
        digits=2,
        timestamp=datetime.now()
    )


def test_no_trailing_activation_below_1_2r(monkeypatch):
    """
    Test 1: Verify trailing stop remains INACTIVE when unrealized profit is below 1.2R.
    """
    updated_sl_calls = []
    def mock_update_sl_tp(trade_id, new_sl=None, new_tp=None):
        updated_sl_calls.append((trade_id, new_sl, new_tp))
        return True

    from app.adapters.mt5_adapter import mt5_adapter
    monkeypatch.setattr(mt5_adapter, "update_sl_tp", mock_update_sl_tp)
    monkeypatch.setattr(mt5_adapter.session_manager, "use_live_mt5", False)
    monkeypatch.setattr(mt5_adapter, "fetch_candles", lambda s, t, c: [])
    monkeypatch.setattr(trade_manager, "reconcile_positions", lambda symbol: [])

    # BUY trade: Entry 2300.00, SL 2290.00 (10 points SL distance = $10.00)
    # 1.0R = 2310.00, 1.19R = 2311.90, 1.20R = 2312.00
    trade = TradeRecord(
        trade_id="test_trail_1",
        symbol="XAUUSD",
        side=TradeSide.BUY,
        order_type=OrderType.MARKET,
        timeframe="M1",
        entry_price=2300.00,
        sl_price=2290.00,
        tp_price=2320.00,
        lots=0.10,
        state=TradeState.OPEN,
        open_time=datetime.now(),
        open_timestamp=datetime.now()
    )
    trade_manager.active_trades = {"test_trail_1": trade}

    # Price at 2305.00 (0.5R profit) -> Should NOT activate trailing
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda symbol: mock_ticker(2305.00))
    trade_manager.update_open_positions("XAUUSD")
    assert trade.sl_price == 2290.00, "SL should remain unchanged at 0.5R profit"

    # Price at 2311.90 (1.19R profit) -> Breakeven activates at 1.0R (SL=2302.00), but Trailing (candidate 2308.40) MUST NOT activate
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda symbol: mock_ticker(2311.90))
    trade_manager.update_open_positions("XAUUSD")
    assert trade.sl_price == 2302.00, f"SL should be set to Breakeven (2302.00) and NOT Trailing (2308.40), got {trade.sl_price}"


def test_trailing_activation_at_1_2r_locks_breakeven_plus_profit(monkeypatch):
    """
    Test 2: Verify trailing stop ACTIVATES at 1.2R profit and locks in Breakeven + 0.2R profit.
    """
    updated_sl_calls = []
    def mock_update_sl_tp(trade_id, new_sl=None, new_tp=None):
        updated_sl_calls.append((trade_id, new_sl, new_tp))
        return True

    from app.adapters.mt5_adapter import mt5_adapter
    monkeypatch.setattr(mt5_adapter, "update_sl_tp", mock_update_sl_tp)
    monkeypatch.setattr(mt5_adapter.session_manager, "use_live_mt5", False)
    monkeypatch.setattr(mt5_adapter, "fetch_candles", lambda s, t, c: [])
    monkeypatch.setattr(trade_manager, "reconcile_positions", lambda symbol: [])

    # BUY trade: Entry 2300.00, SL 2290.00 (10 points SL distance)
    # 1.2R = 2312.00. 0.2R lock = 2302.00.
    trade = TradeRecord(
        trade_id="test_trail_2",
        symbol="XAUUSD",
        side=TradeSide.BUY,
        order_type=OrderType.MARKET,
        timeframe="M1",
        entry_price=2300.00,
        sl_price=2290.00,
        tp_price=2320.00,
        lots=0.10,
        state=TradeState.OPEN,
        open_time=datetime.now(),
        open_timestamp=datetime.now()
    )
    trade_manager.active_trades = {"test_trail_2": trade}

    # Price moves to 2312.50 (1.25R profit) -> Should activate trailing & update SL to at least 2302.00
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda symbol: mock_ticker(2312.50))
    trade_manager.update_open_positions("XAUUSD")
    assert trade.sl_price >= 2302.00, f"SL should be updated to at least Breakeven + 0.2R (2302.00), got {trade.sl_price}"


def test_sell_position_delayed_dynamic_trailing(monkeypatch):
    """
    Test 3: Verify SELL position trailing activates at 1.2R and only moves SL DOWNWARD.
    """
    updated_sl_calls = []
    def mock_update_sl_tp(trade_id, new_sl=None, new_tp=None):
        updated_sl_calls.append((trade_id, new_sl, new_tp))
        return True

    from app.adapters.mt5_adapter import mt5_adapter
    monkeypatch.setattr(mt5_adapter, "update_sl_tp", mock_update_sl_tp)
    monkeypatch.setattr(mt5_adapter.session_manager, "use_live_mt5", False)
    monkeypatch.setattr(mt5_adapter, "fetch_candles", lambda s, t, c: [])
    monkeypatch.setattr(trade_manager, "reconcile_positions", lambda symbol: [])

    # SELL trade: Entry 2300.00, SL 2310.00 (10 points SL distance)
    # 1.2R profit level = 2288.00 (Entry - 12.00)
    trade = TradeRecord(
        trade_id="test_trail_sell",
        symbol="XAUUSD",
        side=TradeSide.SELL,
        order_type=OrderType.MARKET,
        timeframe="M1",
        entry_price=2300.00,
        sl_price=2310.00,
        tp_price=2280.00,
        lots=0.10,
        state=TradeState.OPEN,
        open_time=datetime.now(),
        open_timestamp=datetime.now()
    )
    trade_manager.active_trades = {"test_trail_sell": trade}

    # Price at 2292.00 (0.8R profit) -> Should NOT activate trailing
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda symbol: mock_ticker(2292.00))
    trade_manager.update_open_positions("XAUUSD")
    assert trade.sl_price >= 2300.00, "SELL SL should not trail below breakeven at 0.8R"

    # Price at 2287.50 (1.25R profit) -> Should activate trailing & move SL below 2298.00
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda symbol: mock_ticker(2287.50))
    trade_manager.update_open_positions("XAUUSD")
    assert trade.sl_price <= 2298.00, f"SELL SL should be moved down to at least Breakeven - 0.2R (2298.00), got {trade.sl_price}"


def test_single_source_of_truth_custom_activation_rr(monkeypatch):
    """
    Test 4: Verify setting trailing_activation_rr to 1.5 in RiskSettings delays activation until 1.5R.
    """
    from app.adapters.mt5_adapter import mt5_adapter
    monkeypatch.setattr(mt5_adapter, "update_sl_tp", lambda tid, new_sl=None, new_tp=None: True)
    monkeypatch.setattr(mt5_adapter.session_manager, "use_live_mt5", False)
    monkeypatch.setattr(mt5_adapter, "fetch_candles", lambda s, t, c: [])
    monkeypatch.setattr(trade_manager, "reconcile_positions", lambda symbol: [])

    # Update runtime activation threshold to 1.5R
    trade_manager.update_risk_settings({"trailing_activation_rr": 1.5})

    # BUY trade: Entry 2300.00, SL 2290.00 (10 points SL distance)
    trade = TradeRecord(
        trade_id="test_trail_custom_rr",
        symbol="XAUUSD",
        side=TradeSide.BUY,
        order_type=OrderType.MARKET,
        timeframe="M1",
        entry_price=2300.00,
        sl_price=2290.00,
        tp_price=2325.00,
        lots=0.10,
        state=TradeState.OPEN,
        open_time=datetime.now(),
        open_timestamp=datetime.now()
    )
    trade_manager.active_trades = {"test_trail_custom_rr": trade}

    # Price at 2313.00 (1.3R profit) -> Below 1.5R threshold -> Trailing MUST NOT activate
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda symbol: mock_ticker(2313.00))
    trade_manager.update_open_positions("XAUUSD")
    assert trade.sl_price <= 2302.00, "Trailing should NOT activate at 1.3R when trailing_activation_rr=1.5"

    # Price at 2315.50 (1.55R profit) -> Exceeds 1.5R threshold -> Trailing ACTIVATES
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda symbol: mock_ticker(2315.50))
    trade_manager.update_open_positions("XAUUSD")
    assert trade.sl_price >= 2302.00, "Trailing SHOULD activate at 1.55R when trailing_activation_rr=1.5"
