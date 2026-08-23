import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import traceback
from datetime import datetime
from app.domain.models import TradeRecord, TradeSide, TradeState, OrderType
from app.services.trade_manager import trade_manager

try:
    trade_manager.risk_settings.auto_trailing_stop_enabled = True
    trade_manager.risk_settings.auto_breakeven_enabled = True
    trade_manager.risk_settings.trailing_activation_rr = 1.2
    trade_manager.risk_settings.trailing_atr_multiplier = 2.0
    trade_manager.risk_settings.trailing_stop_pips = 15.0

    trade = TradeRecord(
        trade_id="test_trail_1",
        symbol="XAUUSD",
        side=TradeSide.BUY,
        order_type=OrderType.BUY,
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
    trade_manager.monitor_and_manage_positions("XAUUSD", curr_price=2305.00)
    print("Success!")
except Exception as e:
    traceback.print_exc()
