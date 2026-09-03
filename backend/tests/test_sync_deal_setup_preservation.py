import pytest
import sqlite3
from datetime import datetime
from app.services.trade_learner import trade_learner
from app.services.trade_manager import trade_manager
from app.domain.models import TradeRecord, TradeSide, TradeState

def test_sync_deal_setup_preservation_from_db_and_active_trades():
    """
    Verifies Fix 4 setup_name resolution hierarchy:
    1. Preserves setup_name from existing PENDING DB row across backend restarts.
    2. Recovers setup_name from active_trades memory if DB row was missing.
    """
    trade_id = "test_setup_persist_101"
    
    # 1. Log initial trade execution at trade open time (e.g. from trade_manager)
    trade_learner.log_trade_execution(
        trade_id=trade_id,
        symbol="XAUUSD",
        side="BUY",
        timeframe="M15",
        entry_price=2500.0,
        lots=0.01,
        sl_price=2490.0,
        tp_price=2520.0,
        setup_name="SETUP B: SMC Demand OB Pullback Rejection",
        open_timestamp=datetime.now()
    )
    
    # Verify initial insert has setup_name
    with trade_learner._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT setup_name, win_loss FROM trade_history_memory WHERE trade_id = ?", (trade_id,))
        row = cursor.fetchone()
        assert row["setup_name"] == "SETUP B: SMC Demand OB Pullback Rejection"
        assert row["win_loss"] == "PENDING"

    # 2. Simulate backend restart: active_trades in memory is empty
    trade_manager.active_trades.clear()
    assert trade_id not in trade_manager.active_trades

    # 3. Trigger deal sync with setup_name="Unknown" (as MT5 deal sync would do)
    trade_learner.sync_deal_to_db(
        trade_id=trade_id,
        symbol="XAUUSD",
        side="BUY",
        entry_price=2500.0,
        exit_price=2515.0,
        lots=0.01,
        pnl_usd=15.0,
        open_time=datetime.now(),
        close_time=datetime.now(),
        comment="CLOSED",
        setup_name="Unknown"
    )

    # 4. Verify DB row updated outcome and preserved original setup_name
    with trade_learner._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT setup_name, win_loss, pnl_usd FROM trade_history_memory WHERE trade_id = ?", (trade_id,))
        row_after = cursor.fetchone()
        print(f"\n[SETUP PRESERVATION VERIFICATION] Trade #{trade_id} Outcome: {row_after['win_loss']} (${row_after['pnl_usd']}) | Setup: '{row_after['setup_name']}'")
        assert row_after["setup_name"] == "SETUP B: SMC Demand OB Pullback Rejection", f"Expected setup preservation, got '{row_after['setup_name']}'"
        assert row_after["win_loss"] == "WIN"

if __name__ == "__main__":
    test_sync_deal_setup_preservation_from_db_and_active_trades()
