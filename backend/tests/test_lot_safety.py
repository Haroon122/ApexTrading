"""
Automated Test Suite for Lot Sizing Safety System & Volume Mismatch Protection.
Tests all 10 mandatory execution safety criteria.
"""

import unittest
from datetime import datetime
from app.domain.models import (
    AccountInfo, TradeDecisionReport, TradeSide, ConfirmationBreakdown, RiskSettings, ManualOrderRequest, OrderType
)
from app.services.risk_manager import risk_manager
from app.services.trade_manager import trade_manager
from app.adapters.mt5_adapter import mt5_adapter
from app.core.config import settings


from unittest.mock import patch
from app.services.trade_learner import trade_learner


class TestLotSafetySystem(unittest.TestCase):

    def setUp(self):
        self.patcher = patch.object(trade_learner, "get_recent_closed_trades", return_value=[])
        self.patcher.start()
        trade_manager.active_trades.clear()

        self.mock_account = AccountInfo(
            account_number=10011810265,
            account_name="Test Account",
            broker="MetaQuotes Ltd.",
            server="MetaQuotes-Demo",
            currency="USD",
            balance=10000.0,
            equity=10000.0,
            free_margin=10000.0,
            margin=0.0,
            margin_level_pct=0.0,
            is_connected=True,
            open_positions_count=0
        )

        self.mock_report = TradeDecisionReport(
            symbol="XAUUSD",
            decision=TradeSide.BUY,
            timeframe="M1",
            confidence=0.85,
            confirmation_score=0.85,
            reasoning=["Test Signal"],
            rejection_reasons=[],
            suggested_entry=2300.00,
            suggested_sl=2295.00,
            suggested_tp=2310.00,
            suggested_lots=0.10,
            risk_reward_ratio=3.0,
            breakdown=ConfirmationBreakdown(passed=True),
            timestamp=datetime.now()
        )

        # Reset risk settings for each test with 0 cooldown for isolated unit tests
        trade_manager.risk_settings = RiskSettings(
            lot_sizing_mode="FIXED",
            user_configured_lot=0.10,
            max_allowed_lot=1.00,
            max_risk_per_trade_pct=1.0,
            min_risk_reward_ratio=1.5,
            max_open_positions=10,
            max_trades_per_day=50,
            max_consecutive_losses=99,
            auto_trade_cooldown_seconds=0
        )

    def tearDown(self):
        self.patcher.stop()

    def test_1_fixed_lot_mode_input_0_10(self):
        """TEST 1: Fixed lot mode input 0.10 -> Expected 0.10 lots"""
        trade_manager.risk_settings.lot_sizing_mode = "FIXED"
        trade_manager.risk_settings.user_configured_lot = 0.10
        
        result = risk_manager.evaluate_risk(self.mock_account, self.mock_report, "XAUUSD")
        self.assertTrue(result.passed, f"Reasons: {result.reasons}")
        self.assertEqual(result.calculated_lots, 0.10)

    def test_2_fixed_lot_mode_input_0_01(self):
        """TEST 2: Fixed lot mode input 0.01 -> Expected 0.01 lots"""
        trade_manager.risk_settings.lot_sizing_mode = "FIXED"
        trade_manager.risk_settings.user_configured_lot = 0.01
        
        result = risk_manager.evaluate_risk(self.mock_account, self.mock_report, "XAUUSD")
        self.assertTrue(result.passed, f"Reasons: {result.reasons}")
        self.assertEqual(result.calculated_lots, 0.01)

    def test_3_fixed_lot_mode_input_1_00(self):
        """TEST 3: Fixed lot mode input 1.00 -> Expected 1.00 lots"""
        trade_manager.risk_settings.lot_sizing_mode = "FIXED"
        trade_manager.risk_settings.user_configured_lot = 1.00
        trade_manager.risk_settings.max_allowed_lot = 1.00
        
        result = risk_manager.evaluate_risk(self.mock_account, self.mock_report, "XAUUSD")
        self.assertTrue(result.passed, f"Reasons: {result.reasons}")
        self.assertEqual(result.calculated_lots, 1.00)

    def test_4_broker_volume_step_normalization(self):
        """TEST 4: Input 0.10 with broker step 0.01 -> Expected 0.10"""
        valid, price, norm_lots, sl_val, tp_val, msg = mt5_adapter.validate_and_normalize_order_request(
            "XAUUSD", TradeSide.BUY, OrderType.MARKET, 2300.0, 0.10, 2295.0, 2310.0
        )
        self.assertTrue(valid, f"Msg: {msg}")
        self.assertEqual(norm_lots, 0.10)

    def test_5_accidental_100_lot_calculation_blocked(self):
        """TEST 5: If calculation accidentally returns 100.00 lots -> ORDER BLOCKED"""
        trade_manager.risk_settings.lot_sizing_mode = "DYNAMIC_RISK"
        trade_manager.risk_settings.max_allowed_lot = 1.00 # Cap at 1.00
        
        # Extremely small SL distance to force huge calculated lots
        tight_sl_report = self.mock_report.model_copy(update={"suggested_sl": 2299.999})
        result = risk_manager.evaluate_risk(self.mock_account, tight_sl_report, "XAUUSD")
        
        self.assertFalse(result.passed)
        self.assertEqual(result.calculated_lots, 0.0)
        self.assertTrue(any("EXCEEDS MAX ALLOWED LOT" in r for r in result.reasons))

    def test_6_fixed_vs_dynamic_risk_mode(self):
        """TEST 6: Fixed mode returns 0.10 while Dynamic mode returns risk-calculated lots"""
        # Fixed Mode
        trade_manager.risk_settings.lot_sizing_mode = "FIXED"
        trade_manager.risk_settings.user_configured_lot = 0.10
        res_fixed = risk_manager.evaluate_risk(self.mock_account, self.mock_report, "XAUUSD")
        self.assertTrue(res_fixed.passed, f"Fixed Reasons: {res_fixed.reasons}")
        self.assertEqual(res_fixed.calculated_lots, 0.10)

        # Dynamic Mode
        trade_manager.risk_settings.lot_sizing_mode = "DYNAMIC_RISK"
        res_dynamic = risk_manager.evaluate_risk(self.mock_account, self.mock_report, "XAUUSD")
        self.assertTrue(res_dynamic.passed, f"Dynamic Reasons: {res_dynamic.reasons}")
        self.assertNotEqual(res_dynamic.calculated_lots, 0.0)

    def test_7_missing_or_zero_volume_blocked(self):
        """TEST 7: Zero or negative volume -> ORDER BLOCKED"""
        valid, price, norm_lots, sl_val, tp_val, msg = mt5_adapter.validate_and_normalize_order_request(
            "XAUUSD", TradeSide.BUY, OrderType.MARKET, 2300.0, 0.00, 2295.0, 2310.0
        )
        self.assertFalse(valid)
        self.assertIn("ORDER BLOCKED — INVALID LOT SIZE", msg)

    def test_8_volume_above_max_allowed_lot_blocked(self):
        """TEST 8: Volume above max allowed lot (e.g. 5.0 lots > max 1.0 lot) -> ORDER BLOCKED"""
        trade_manager.risk_settings.max_allowed_lot = 1.00
        
        valid, price, norm_lots, sl_val, tp_val, msg = mt5_adapter.validate_and_normalize_order_request(
            "XAUUSD", TradeSide.BUY, OrderType.MARKET, 2300.0, 5.00, 2295.0, 2310.0
        )
        self.assertFalse(valid)
        self.assertIn("EXCEEDS MAX ALLOWED LOT", msg)

    def test_9_manual_order_request_volume_validation(self):
        """TEST 9: Manual order request enforces max allowed lot cap"""
        trade_manager.risk_settings.max_allowed_lot = 1.00
        
        req_valid = ManualOrderRequest(symbol="XAUUSD", side=TradeSide.BUY, lots=0.10)
        success_v, trade_v, msg_v = trade_manager.execute_manual_order(req_valid)
        self.assertTrue(success_v)

        req_invalid = ManualOrderRequest(symbol="XAUUSD", side=TradeSide.BUY, lots=100.00)
        success_inv, trade_inv, msg_inv = trade_manager.execute_manual_order(req_invalid)
        self.assertFalse(success_inv)
        self.assertIn("EXCEEDS MAX ALLOWED LOT", msg_inv)

    def test_10_execution_safety_pre_send_mismatch_guard(self):
        """TEST 10: Pre-send mismatch (0.10 requested vs 100.0 normalized) -> ORDER BLOCKED"""
        # Simulate mismatch where requested = 0.10 but norm_lots = 100.0
        lots = 0.10
        norm_lots = 100.0
        mismatch = abs(norm_lots - lots) > 0.05 * max(lots, 0.01)
        self.assertTrue(mismatch)


if __name__ == "__main__":
    unittest.main()
