"""Tests for manually changing SL/TP of an open position (dashboard "SL/TP" button)."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.adapters.mt5_adapter import mt5_adapter
from app.domain.models import OrderType, TradeRecord, TradeSide, TradeState
from app.services.trade_manager import trade_manager


def _pos(side=TradeSide.SELL, entry=3953.97, sl=3993.39, tp=3458.86):
    return TradeRecord(trade_id="111", symbol="XAUUSD", side=side, order_type=OrderType.MARKET,
                       entry_price=entry, sl_price=sl, tp_price=tp, lots=0.05,
                       state=TradeState.OPEN, open_time=datetime.now())


@pytest.fixture
def broker(monkeypatch):
    """Fake MT5: one open position, a live price and a record of SL/TP updates."""
    state = SimpleNamespace(positions=[_pos()], bid=3905.0, ask=3905.4, gap=0.0, calls=[], accept=True, ok=True)

    def get_positions():
        mt5_adapter.positions_ok = state.ok
        return list(state.positions)

    def update(trade_id, sl, tp):
        state.calls.append((trade_id, sl, tp))
        mt5_adapter.last_sltp_error = "" if state.accept else "MT5 rejected (code 10016: Invalid stops)"
        return state.accept

    monkeypatch.setattr(mt5_adapter, "get_positions", get_positions)
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda s: SimpleNamespace(bid=state.bid, ask=state.ask))
    monkeypatch.setattr(mt5_adapter, "update_sl_tp", update)
    monkeypatch.setattr(mt5_adapter, "min_stop_distance", lambda s: state.gap)
    monkeypatch.setattr(trade_manager, "active_trades", {})
    return state


def test_sell_sl_to_entry_is_accepted(broker):
    ok, msg = trade_manager.modify_position("111", 3953.97, 3458.86)
    assert ok, msg
    assert broker.calls == [("111", 3953.97, 3458.86)]


def test_sell_sl_below_price_is_rejected(broker):
    ok, msg = trade_manager.modify_position("111", 3900.0, 3458.86)
    assert not ok and "above the current price" in msg
    assert broker.calls == []


def test_sell_tp_above_price_is_rejected(broker):
    ok, msg = trade_manager.modify_position("111", 3993.39, 3920.0)
    assert not ok and "take profit must be below" in msg


def test_buy_rules_are_mirrored(broker):
    broker.positions = [_pos(side=TradeSide.BUY, entry=3880.0, sl=3850.0, tp=3990.0)]
    assert not trade_manager.modify_position("111", 3910.0, 3990.0)[0]   # SL above bid
    assert not trade_manager.modify_position("111", 3850.0, 3900.0)[0]   # TP below ask
    assert trade_manager.modify_position("111", 3880.0, 3990.0)[0]       # SL to entry


def test_tp_zero_removes_target_but_sl_is_required(broker):
    assert trade_manager.modify_position("111", 3993.39, 0)[0]
    ok, msg = trade_manager.modify_position("111", 0, 3458.86)
    assert not ok and "Stop Loss is required" in msg


def test_broker_minimum_distance(broker):
    broker.gap = 5.0
    ok, msg = trade_manager.modify_position("111", 3908.0, 3458.86)  # only 2.6 above ask
    assert not ok and "at least" in msg
    assert trade_manager.modify_position("111", 3911.0, 3458.86)[0]


def test_unknown_or_unchanged_position(broker):
    assert "not found" in trade_manager.modify_position("999", 3990.0, 0)[1]
    assert "Nothing changed" in trade_manager.modify_position("111", 3993.39, 3458.86)[1]


def test_failed_position_read_blocks_change(broker):
    broker.ok = False
    ok, msg = trade_manager.modify_position("111", 3990.0, 0)
    assert not ok and "Could not read" in msg and broker.calls == []


def test_mt5_rejection_reason_is_returned(broker):
    broker.accept = False
    ok, msg = trade_manager.modify_position("111", 3990.0, 0)
    assert not ok and "Invalid stops" in msg


def test_no_live_price(broker, monkeypatch):
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda s: None)
    ok, msg = trade_manager.modify_position("111", 3990.0, 0)
    assert not ok and "No live price" in msg


def test_cached_trade_updated_and_widening_warns(broker):
    trade_manager.active_trades["111"] = _pos()
    ok, msg = trade_manager.modify_position("111", 4010.0, 3458.86)  # stop moved further away
    assert ok and "further away" in msg
    assert trade_manager.active_trades["111"].sl_price == 4010.0


def test_trailing_never_loosens_a_manual_stop():
    """After the user moves the SL to entry, the chandelier trail must not move it back."""
    from app.domain.models import Candle
    from app.services import donchian_trend as dt
    t0 = datetime(2026, 1, 1)
    candles = [Candle(timestamp=t0, open=c, high=c + 10, low=c - 10, close=c, volume=1,
                      timeframe="H1", is_closed=True) for c in [3910.0] * 30]
    # SELL with stop at entry 3953.97: trail = close + 4*ATR(~20) = ~3990, worse than 3953.97
    assert dt.trailing_stop(candles, False, current_sl=3953.97) is None
