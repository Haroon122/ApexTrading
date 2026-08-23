"""
Automated Test Suite for ENABLE_LIVE_TRADING in Risk Management
Verifies default value, validation, safety check enforcement, and JSON persistence.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.domain.models import RiskSettings
from app.services.trade_manager import trade_manager


def test_enable_live_trading_default():
    """Verify default enable_live_trading is False."""
    cfg = RiskSettings()
    assert cfg.enable_live_trading is False


def test_enable_live_trading_update_and_persistence():
    """Verify enable_live_trading can be updated and persisted to risk_settings.json."""
    curr = trade_manager.risk_settings
    orig = curr.enable_live_trading
    try:
        curr.enable_live_trading = True
        trade_manager.update_risk_settings(curr)
        
        reloaded = trade_manager._load_persisted_risk_settings()
        assert reloaded.enable_live_trading is True
    finally:
        curr.enable_live_trading = orig
        trade_manager.update_risk_settings(curr)


def test_execution_safety_real_vs_demo_account():
    """
    Verify _check_execution_safety logic:
    - DEMO account (trade_mode != 2) is allowed even when enable_live_trading is False.
    - REAL account (trade_mode == 2) is blocked when enable_live_trading is False.
    - REAL account (trade_mode == 2) is allowed when enable_live_trading is True.
    """
    curr = trade_manager.risk_settings
    orig = curr.enable_live_trading

    try:
        # Mock MT5 session to pretend live MT5 is active
        with patch("app.adapters.mt5_adapter.mt5_adapter.session_manager.use_live_mt5", True), \
             patch("app.services.session_manager.MT5_AVAILABLE", True), \
             patch("MetaTrader5.account_info") as mock_acc_info:

            # 1. Demo account (trade_mode == 0) with enable_live_trading=False
            curr.enable_live_trading = False
            mock_acc = MagicMock()
            mock_acc.trade_mode = 0  # Demo
            mock_acc_info.return_value = mock_acc

            allowed, mode = trade_manager._check_execution_safety("XAUUSD")
            assert allowed is True
            assert mode == "DEMO"

            # 2. Real account (trade_mode == 2) with enable_live_trading=False
            mock_acc.trade_mode = 2  # Real
            allowed, mode = trade_manager._check_execution_safety("XAUUSD")
            assert allowed is False
            assert "CRITICAL SAFETY LOCK" in mode

            # 3. Real account (trade_mode == 2) with enable_live_trading=True
            curr.enable_live_trading = True
            allowed, mode = trade_manager._check_execution_safety("XAUUSD")
            assert allowed is True
            assert mode == "LIVE"

    finally:
        curr.enable_live_trading = orig
        trade_manager.update_risk_settings(curr)
