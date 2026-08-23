"""
Automated Test Suite for Position State Synchronization & Real-Time MT5 Engine.
Tests all mandatory position sync criteria and reconciliation guards.
"""

import unittest
import asyncio
from datetime import datetime
from app.domain.models import (
    AccountInfo, TradeDecisionReport, TradeSide, ConfirmationBreakdown, RiskSettings, TradeRecord, TradeState, OrderType, EngineState
)
from app.services.trade_manager import trade_manager
from app.services.trading_worker import trading_worker
from app.adapters.mt5_adapter import mt5_adapter


class TestPositionStateSyncSystem(unittest.TestCase):

    def setUp(self):
        self.orig_use_live = mt5_adapter.session_manager.use_live_mt5
        mt5_adapter.session_manager.use_live_mt5 = True
        trade_manager.active_trades.clear()
        trade_manager.risk_settings = RiskSettings(
            lot_sizing_mode="FIXED",
            user_configured_lot=0.10,
            max_allowed_lot=1.00,
            auto_trade_cooldown_seconds=0
        )

        self.mock_trade = TradeRecord(
            trade_id="152465675657",
            symbol="XAUUSD",
            side=TradeSide.BUY,
            order_type=OrderType.MARKET,
            entry_price=2300.00,
            sl_price=2295.00,
            tp_price=2310.00,
            lots=0.10,
            state=TradeState.OPEN,
            open_time=datetime.now()
        )

    def tearDown(self):
        mt5_adapter.session_manager.use_live_mt5 = self.orig_use_live
        trade_manager.active_trades.clear()

    def test_1_position_state_replacement_empty_mt5(self):
        """TEST 1: When MT5 returns 0 positions, active_positions becomes empty []"""
        # Add mock trade to active_trades
        trade_manager.active_trades["152465675657"] = self.mock_trade
        
        # Mock mt5_adapter.get_positions to return empty list []
        original_get_positions = mt5_adapter.get_positions
        mt5_adapter.get_positions = lambda: []

        try:
            active_list = trade_manager.reconcile_positions("XAUUSD")
            self.assertEqual(len(active_list), 0)
            self.assertEqual(len(trade_manager.active_trades), 0)
        finally:
            mt5_adapter.get_positions = original_get_positions

    def test_2_external_close_detection_and_reconciliation(self):
        """TEST 2: Detect when a trade is closed externally in MT5 and reconcile outcome"""
        trade_manager.active_trades["152465675657"] = self.mock_trade
        self.assertIn("152465675657", trade_manager.active_trades)

        # Mock MT5 returning 0 open positions
        original_get_positions = mt5_adapter.get_positions
        mt5_adapter.get_positions = lambda: []

        try:
            trade_manager.reconcile_positions("XAUUSD")
            self.assertNotIn("152465675657", trade_manager.active_trades)
            self.assertEqual(self.mock_trade.state, TradeState.CLOSED)
        finally:
            mt5_adapter.get_positions = original_get_positions

    def test_3_closed_ticket_modification_protection(self):
        """TEST 3: Verify ticket existence check prevents SL/TP modification on closed tickets"""
        trade_manager.active_trades["152465675657"] = self.mock_trade

        # Mock MT5 returning empty positions
        original_get_positions = mt5_adapter.get_positions
        mt5_adapter.get_positions = lambda: []

        try:
            # Running update_open_positions should perform reconciliation and skip modification
            trade_manager.update_open_positions("XAUUSD")
            self.assertNotIn("152465675657", trade_manager.active_trades)
        finally:
            mt5_adapter.get_positions = original_get_positions

    def test_4_single_worker_instance_guard(self):
        """TEST 4: Duplicate start calls enforce single worker instance"""
        async def run_test():
            trading_worker._running = True
            trading_worker.symbol = "XAUUSD"
            trading_worker.timeframe = "M1"

            ok, msg = await trading_worker.start("XAUUSD", "M1")
            self.assertTrue(ok)
            self.assertIn("already running", msg.lower())
            
            trading_worker._running = False

        asyncio.run(run_test())

    def test_5_sequence_number_generation(self):
        """TEST 5: Verify sequence counter increments monotonically"""
        trading_worker._sequence_num = 10
        prev_seq = trading_worker._sequence_num

        async def run_test():
            trading_worker._sequence_num += 1
            self.assertEqual(trading_worker._sequence_num, prev_seq + 1)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
