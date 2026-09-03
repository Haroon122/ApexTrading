import pytest
import sqlite3
from datetime import datetime
from app.services.trade_learner import trade_learner
from app.services.session_manager import session_manager

def test_mark_trade_discrepancy():
    """
    Verifies trade_learner.mark_trade_discrepancy updates a PENDING trade's win_loss
    to DISCREPANCY and sets exit_reason appropriately.
    """
    trade_id = "test_disc_101"
    
    # 1. Log initial pending trade
    trade_learner.log_trade_execution(
        trade_id=trade_id,
        symbol="EURUSD",
        side="BUY",
        timeframe="M15",
        entry_price=1.0850,
        lots=0.1,
        sl_price=1.0830,
        tp_price=1.0890,
        open_timestamp=datetime.now()
    )
    
    with trade_learner._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT win_loss FROM trade_history_memory WHERE trade_id = ?", (trade_id,))
        row = cursor.fetchone()
        assert row["win_loss"] == "PENDING"

    # 2. Mark discrepancy
    success = trade_learner.mark_trade_discrepancy(trade_id, reason="RECONCILIATION_DISCREPANCY")
    assert success is True

    # 3. Verify state updated in DB
    with trade_learner._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT win_loss, exit_reason FROM trade_history_memory WHERE trade_id = ?", (trade_id,))
        row_after = cursor.fetchone()
        assert row_after["win_loss"] == "DISCREPANCY"
        assert row_after["exit_reason"] == "RECONCILIATION_DISCREPANCY"

def test_session_manager_reconciliation_resolves_pending():
    """
    Verifies that session_manager.sync_history_to_sqlite resolves pending trades
    that are not active in MT5 without infinite logging loops.
    """
    trade_id = "test_disc_102"
    
    # Log pending trade
    trade_learner.log_trade_execution(
        trade_id=trade_id,
        symbol="EURUSD",
        side="SELL",
        timeframe="M15",
        entry_price=1.0850,
        lots=0.1,
        sl_price=1.0870,
        tp_price=1.0810,
        open_timestamp=datetime.now()
    )

    # Trigger session_manager sync with live flag set
    prev_state = session_manager.use_live_mt5
    session_manager.use_live_mt5 = True
    try:
        session_manager.sync_history_to_sqlite()
    finally:
        session_manager.use_live_mt5 = prev_state

    # Verify trade was resolved out of PENDING state
    with trade_learner._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT win_loss FROM trade_history_memory WHERE trade_id = ?", (trade_id,))
        row = cursor.fetchone()
        assert row["win_loss"] in ("DISCREPANCY", "WIN", "LOSS", "EVEN")
        assert row["win_loss"] != "PENDING"

if __name__ == "__main__":
    test_mark_trade_discrepancy()
    test_session_manager_reconciliation_resolves_pending()
    print("All reconciliation discrepancy tests passed!")
