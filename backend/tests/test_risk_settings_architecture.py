"""
Automated Test Suite: Single Source of Truth Risk Settings Architecture Verification
Verifies end-to-end integration across DecisionEngine, RiskManager, MT5Adapter, IndicatorService, and DarkVenusBB.
"""

import os
import pytest
import pandas as pd
from datetime import datetime
from app.domain.models import AccountInfo, RiskSettings, TradeDecisionReport, TradeSide, ConfirmationBreakdown
from app.services.trade_manager import trade_manager
from app.services.risk_manager import RiskManager
from app.adapters.mt5_adapter import mt5_adapter
from app.services.indicators import IndicatorService
from app.services.dark_venus_bb import dark_venus_engine
from app.services.decision_engine import DecisionEngine
from app.domain.models import Candle, TickerData

@pytest.fixture(autouse=True)
def restore_risk_settings():
    """Ensure risk settings are restored after each test."""
    original_cfg = trade_manager.risk_settings.model_copy()
    trade_manager.risk_settings.max_trades_per_day = 1000
    trade_manager.risk_settings.max_consecutive_losses = 999
    trade_manager.risk_settings.auto_trade_cooldown_seconds = 0
    yield
    trade_manager.risk_settings = original_cfg


def test_min_risk_reward_ratio_architecture():
    """
    Test 1: Min R:R Architecture
    Verify setting min_risk_reward_ratio to 1.5, 2.0, 3.0 governs RiskManager and MT5Adapter validation.
    """
    rm = RiskManager()
    account = AccountInfo(account_number=123456, balance=10000.0, equity=10000.0, daily_pnl_usd=0.0, weekly_pnl_usd=0.0)
    
    # 1. Test R:R = 1.5
    trade_manager.risk_settings.min_risk_reward_ratio = 1.5
    trade_manager.risk_settings.lot_sizing_mode = "FIXED"
    trade_manager.risk_settings.user_configured_lot = 0.10
    
    report_1_5 = TradeDecisionReport(
        symbol="XAUUSD",
        decision=TradeSide.BUY,
        timeframe="M1",
        confidence=0.85,
        confirmation_score=0.85,
        reasoning=[],
        rejection_reasons=[],
        suggested_entry=2300.00,
        suggested_sl=2290.00,
        suggested_tp=2315.00,
        suggested_lots=0.10,
        risk_reward_ratio=1.5, # 1:1.5 ratio
        breakdown=ConfirmationBreakdown(),
        timestamp=datetime.now()
    )
    
    res = rm.evaluate_risk(account, report_1_5, "XAUUSD")
    assert res.passed is True, f"R:R 1.5 should be accepted when min_risk_reward_ratio=1.5. Reasons: {res.reasons if hasattr(res, 'reasons') else ''}"
    
    valid, price, norm_lots, sl_val, tp_val, msg = mt5_adapter.validate_and_normalize_order_request(
        "XAUUSD", TradeSide.BUY, None, 2300.00, 0.10, 2290.00, 2315.00
    )
    assert valid is True, f"MT5Adapter should accept R:R 1.5 when min_risk_reward_ratio=1.5. Msg: {msg}"
    
    # 2. Test R:R = 2.0 (with 1.5 report) -> Should be rejected
    trade_manager.risk_settings.min_risk_reward_ratio = 2.0
    res_rejected = rm.evaluate_risk(account, report_1_5, "XAUUSD")
    assert res_rejected.passed is False, "R:R 1.5 should be rejected when min_risk_reward_ratio=2.0"
    
    valid_rej, _, _, _, _, msg_rej = mt5_adapter.validate_and_normalize_order_request(
        "XAUUSD", TradeSide.BUY, None, 2300.00, 0.10, 2290.00, 2315.00
    )
    assert valid_rej is False, "MT5Adapter should reject R:R 1.5 when min_risk_reward_ratio=2.0"


def test_max_risk_per_trade_pct_architecture():
    """
    Test 2: Max Risk Per Trade % Architecture
    Verify dynamic lot sizing risks exact user % of equity (0.5%, 1.0%, 2.0%).
    """
    rm = RiskManager()
    account = AccountInfo(account_number=123456, balance=10000.0, equity=10000.0, daily_pnl_usd=0.0, weekly_pnl_usd=0.0)
    
    report = TradeDecisionReport(
        symbol="XAUUSD",
        decision=TradeSide.BUY,
        timeframe="M1",
        confidence=0.85,
        confirmation_score=0.85,
        reasoning=[],
        rejection_reasons=[],
        suggested_entry=2300.00,
        suggested_sl=2290.00, # 100 points ($10.00) SL distance
        suggested_tp=2320.00,
        suggested_lots=0.10,
        risk_reward_ratio=2.0,
        breakdown=ConfirmationBreakdown(),
        timestamp=datetime.now()
    )
    
    trade_manager.risk_settings.lot_sizing_mode = "DYNAMIC_RISK"
    trade_manager.risk_settings.min_risk_reward_ratio = 2.0
    
    # 0.5% risk on $10,000 equity = $50 USD. $10 SL distance = 0.05 lots (100 points * $1 = $100 per 1.0 lot)
    trade_manager.risk_settings.max_risk_per_trade_pct = 0.5
    res_0_5 = rm.evaluate_risk(account, report, "XAUUSD")
    assert res_0_5.passed is True
    assert res_0_5.calculated_lots == 0.05, f"Expected 0.05 lots for 0.5% risk, got {res_0_5.calculated_lots}"
    
    # 1.0% risk on $10,000 equity = $100 USD -> 0.10 lots
    trade_manager.risk_settings.max_risk_per_trade_pct = 1.0
    res_1_0 = rm.evaluate_risk(account, report, "XAUUSD")
    assert res_1_0.passed is True
    assert res_1_0.calculated_lots == 0.10, f"Expected 0.10 lots for 1.0% risk, got {res_1_0.calculated_lots}"
    
    # 2.0% risk on $10,000 equity = $200 USD -> 0.20 lots
    trade_manager.risk_settings.max_risk_per_trade_pct = 2.0
    res_2_0 = rm.evaluate_risk(account, report, "XAUUSD")
    assert res_2_0.passed is True
    assert res_2_0.calculated_lots == 0.20, f"Expected 0.20 lots for 2.0% risk, got {res_2_0.calculated_lots}"


def test_bollinger_band_dynamic_parameters():
    """
    Test 3: Dynamic BB Parameters (bb_period & bb_std)
    Verify IndicatorService and DarkVenusBB engine respond to user-configured BB parameters.
    """
    candles = [
        Candle(timestamp=datetime.now(), open=2300.0+i*0.1, high=2301.0+i*0.1, low=2299.0+i*0.1, close=2300.5+i*0.1, volume=100.0, timeframe="M1")
        for i in range(50)
    ]
    
    # Run with bb_period=14, bb_std=2.5
    df_14 = IndicatorService.calculate_all_indicators(candles, bb_period=14, bb_std=2.5)
    assert "bb_upper" in df_14.columns
    assert df_14["bb_middle"].iloc[-1] is not None
    
    # Verify dark_venus_engine calculate_bollinger_features uses parameters
    raw_df = IndicatorService.candles_to_df(candles)
    df_bb_custom = dark_venus_engine.calculate_bollinger_features(raw_df, period=14, std_dev=2.5)
    assert df_bb_custom._bb_period == 14
    assert df_bb_custom._bb_std == 2.5


def test_max_daily_loss_amount_circuit_breaker():
    """
    Test 4: Max Daily Loss Amount ($) Circuit Breaker
    Verify auto trading is blocked when daily USD loss reaches or exceeds max_daily_loss_amount.
    """
    rm = RiskManager()
    
    trade_manager.risk_settings.daily_loss_limit_pct = 10.0 # High % limit so dollar limit triggers first
    trade_manager.risk_settings.max_daily_loss_amount = 50.0 # $50 max daily loss
    
    # Account with $60 loss (exceeds $50 limit)
    account_breached = AccountInfo(account_number=123456, balance=10000.0, equity=9940.0, daily_pnl_usd=-60.0, weekly_pnl_usd=0.0)
    
    report = TradeDecisionReport(
        symbol="XAUUSD",
        decision=TradeSide.BUY,
        timeframe="M1",
        confidence=0.85,
        confirmation_score=0.85,
        reasoning=[],
        rejection_reasons=[],
        suggested_entry=2300.00,
        suggested_sl=2290.00,
        suggested_tp=2320.00,
        suggested_lots=0.10,
        risk_reward_ratio=2.0,
        breakdown=ConfirmationBreakdown(),
        timestamp=datetime.now()
    )
    
    res = rm.evaluate_risk(account_breached, report, "XAUUSD")
    assert res.passed is False, "Auto trading should be blocked when daily loss ($60) exceeds max_daily_loss_amount ($50)"


def test_live_runtime_reload_without_restart():
    """
    Test 5: Live Runtime Reload
    Verify updating trade_manager.update_risk_settings() immediately changes runtime execution behavior without restart.
    """
    rm = RiskManager()
    account = AccountInfo(account_number=123456, balance=10000.0, equity=10000.0, daily_pnl_usd=0.0, weekly_pnl_usd=0.0)
    
    report = TradeDecisionReport(
        symbol="XAUUSD",
        decision=TradeSide.BUY,
        timeframe="M1",
        confidence=0.85,
        confirmation_score=0.85,
        reasoning=[],
        rejection_reasons=[],
        suggested_entry=2300.00,
        suggested_sl=2290.00,
        suggested_tp=2315.00,
        suggested_lots=0.10,
        risk_reward_ratio=1.5,
        breakdown=ConfirmationBreakdown(),
        timestamp=datetime.now()
    )
    
    # Runtime state set to 1.5
    trade_manager.update_risk_settings({"min_risk_reward_ratio": 1.5})
    assert rm.evaluate_risk(account, report, "XAUUSD").passed is True
    
    # Runtime updated live to 2.0 without restart
    trade_manager.update_risk_settings({"min_risk_reward_ratio": 2.0})
    assert rm.evaluate_risk(account, report, "XAUUSD").passed is False
