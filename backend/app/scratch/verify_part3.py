import sys
import os
import sqlite3
from datetime import datetime, timedelta

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.domain.models import Candle, TickerData, TradeSide, AccountInfo, TradeDecisionReport, OrderType, ConfirmationBreakdown
from app.services.session_manager import session_manager
from app.services.trade_learner import trade_learner
from app.services.risk_manager import risk_manager
from app.services.trade_manager import trade_manager
from app.adapters.mt5_adapter import mt5_adapter

# Path to db for cleaning
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "trading_memory.db")

def clean_db():
    """Cleans database tables instead of deleting the file to avoid Windows lock issues."""
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Ensure tables exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_history_memory (
                trade_id TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                timeframe TEXT,
                entry_price REAL,
                exit_price REAL,
                lots REAL,
                sl_price REAL,
                tp_price REAL,
                pnl_usd REAL,
                win_loss TEXT,
                open_timestamp TEXT,
                close_timestamp TEXT,
                exit_reason TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cursor.execute("DELETE FROM trade_history_memory")
        cursor.execute("DELETE FROM system_metadata")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error cleaning database: {e}")

def test_realized_daily_pnl():
    print("\n==================================================")
    print("TESTING ISSUE 10: Realized Daily P&L vs Floating")
    print("==================================================")
    
    clean_db()
    
    # 1. Log a realized loss deal to SQLite for today
    trade_learner.sync_deal_to_db(
        trade_id="test_deal_1",
        symbol="EURUSD",
        side="BUY",
        entry_price=1.08500,
        exit_price=1.08300,
        lots=1.0,
        pnl_usd=-200.0,
        open_time=datetime.now() - timedelta(hours=2),
        close_time=datetime.now() - timedelta(hours=1),
        comment="SL HIT"
    )
    
    # 2. Mock mt5.account_info() response (simulating live mode)
    class MockMT5AccountInfo:
        login = 12345
        name = "Test Account"
        company = "Mock Broker"
        server = "MockServer"
        trade_mode = 0
        currency = "USD"
        leverage = 100
        balance = 10000.0
        equity = 10000.0
        margin = 0.0
        margin_free = 10000.0
        margin_level = 0.0
        profit = 50.0  # Floating profit!
        
    original_use_live = session_manager.use_live_mt5
    original_mt5_available = sys.modules.get('MetaTrader5')
    
    # Force use_live_mt5 to True for the test
    session_manager.use_live_mt5 = True
    
    import MetaTrader5 as mt5_mock
    original_account_info = mt5_mock.account_info
    mt5_mock.account_info = lambda: MockMT5AccountInfo()
    
    try:
        acc_info = session_manager.get_account_info()
        print(f"MT5 Floating Profit: ${acc_info.floating_profit:.2f}")
        print(f"Realized Daily P&L (DB): ${acc_info.daily_pnl_usd:.2f}")
        
        # Verify realized P&L is -200.0, not +50.0
        assert acc_info.daily_pnl_usd == -200.0, f"Expected -200.0 but got {acc_info.daily_pnl_usd}"
        assert acc_info.floating_profit == 50.0, f"Expected 50.0 but got {acc_info.floating_profit}"
        print(" -> Issue 10 realized daily P&L test passed successfully.")
    finally:
        mt5_mock.account_info = original_account_info
        session_manager.use_live_mt5 = original_use_live

def test_drawdown_protection_and_restart():
    print("\n==================================================")
    print("TESTING ISSUE 11: Max Drawdown Protection & Restart Persistence")
    print("==================================================")
    
    clean_db()
    login_id = 55555
    
    # 1. Simulate account equity peak in DB
    trade_learner.set_metadata(f"peak_equity_{login_id}", "10000.0")
    
    # Verify it was saved
    saved_peak = trade_learner.get_metadata(f"peak_equity_{login_id}")
    print(f"Persisted Peak Equity in DB: ${saved_peak}")
    assert float(saved_peak) == 10000.0
    
    # 2. Simulate account drop to $8700 (drawdown = 13%, exceeding settings.MAX_DRAWDOWN_LIMIT_PCT = 12%)
    acc_info = AccountInfo(
        account_number=login_id,
        balance=10000.0,
        equity=8700.0,  # Breached!
        drawdown_pct=13.0,
        peak_equity=10000.0,
        is_connected=True
    )
    
    report = TradeDecisionReport(
        symbol="EURUSD",
        decision=TradeSide.BUY,
        timeframe="M15",
        confidence=0.85,
        confirmation_score=0.85,
        reasoning=["Trend is bullish"],
        rejection_reasons=[],
        suggested_entry=1.08500,
        suggested_sl=1.08300,
        suggested_tp=1.08900,
        suggested_lots=0.10,
        risk_reward_ratio=2.0,
        breakdown=ConfirmationBreakdown(
            trend_score=1.0,
            market_structure_score=1.0,
            momentum_volume_score=1.0,
            heuristic_score=1.0,
            risk_news_score=1.0,
            total_score=1.0,
            passed=True
        ),
        timestamp=datetime.now()
    )
    
    # 3. Evaluate risk
    # Ensure settings.MAX_DRAWDOWN_LIMIT_PCT is 12.0
    from app.core.config import settings as app_settings
    original_limit = app_settings.MAX_DRAWDOWN_LIMIT_PCT
    app_settings.MAX_DRAWDOWN_LIMIT_PCT = 12.0
    
    try:
        check = risk_manager.evaluate_risk(acc_info, report, "EURUSD")
        print(f"Risk Check Passed: {check.passed}")
        print(f"Rejection reasons: {check.reasons}")
        
        # Verify trade blocked due to Drawdown Breach
        assert check.passed == False
        assert any("Drawdown" in r for r in check.reasons), "Drawdown breach message not found!"
        
        # 4. Simulate application restart
        # We instantiate a new MT5SessionManager to verify it restores peak equity from SQLite
        # without resetting it to the current equity.
        new_session_manager = session_manager.__class__()
        new_session_manager.use_live_mt5 = True
        new_session_manager.active_login = login_id
        
        # Mock mt5.account_info to return equity = 8700.0 (so it doesn't set a new higher peak)
        class MockMT5AccountInfo:
            login = login_id
            name = "Test Account"
            company = "Mock Broker"
            server = "MockServer"
            trade_mode = 0
            currency = "USD"
            leverage = 100
            balance = 10000.0
            equity = 8700.0
            margin = 0.0
            margin_free = 8700.0
            margin_level = 0.0
            profit = 0.0
            
        import MetaTrader5 as mt5_mock
        original_account_info = mt5_mock.account_info
        mt5_mock.account_info = lambda: MockMT5AccountInfo()
        
        try:
            restored_acc = new_session_manager.get_account_info()
            print(f"Restored Peak Equity after restart: ${restored_acc.peak_equity:.2f}")
            print(f"Restored Drawdown % after restart: {restored_acc.drawdown_pct:.1f}%")
            
            assert restored_acc.peak_equity == 10000.0
            assert restored_acc.drawdown_pct == 13.0
            print(" -> Issue 11 drawdown protection restart persistence passed.")
        finally:
            mt5_mock.account_info = original_account_info
            
        app_settings.MAX_DRAWDOWN_LIMIT_PCT = original_limit
    except Exception as e:
        app_settings.MAX_DRAWDOWN_LIMIT_PCT = original_limit
        raise e

def test_order_revalidation():
    print("\n==================================================")
    print("TESTING ISSUE 12: Broker Order Normalization & Re-validation")
    print("==================================================")
    
    # We want to test validate_and_normalize_order_request
    # 1. Setup a mock symbol specifications where Stops Level is large (e.g. 50 points = 5.0 pips)
    
    class MockMT5SymbolInfo:
        visible = True
        trade_mode = 4 # Full trade access
        digits = 5
        point = 0.00001
        trade_stops_level = 50  # 50 points Stops Level!
        volume_min = 0.01
        volume_max = 100.0
        volume_step = 0.01
        filling_mode = 2
        ask = 1.08500
        bid = 1.08500
        
    import MetaTrader5 as mt5_mock
    original_symbol_info = mt5_mock.symbol_info
    original_symbol_select = mt5_mock.symbol_select
    
    mt5_mock.symbol_info = lambda sym: MockMT5SymbolInfo()
    mt5_mock.symbol_select = lambda sym, select: True
    
    # Mock account equity to $1000 (so maximum trade risk at 1.0% is $10.0)
    class MockMT5AccountInfo:
        login = 12345
        name = "Test Account"
        company = "Mock Broker"
        server = "MockServer"
        trade_mode = 0
        currency = "USD"
        leverage = 100
        balance = 1000.0
        equity = 1000.0
        margin = 0.0
        margin_free = 1000.0
        margin_level = 0.0
        profit = 0.0
        
    original_account_info = mt5_mock.account_info
    mt5_mock.account_info = lambda: MockMT5AccountInfo()
    
    original_use_live = mt5_adapter.session_manager.use_live_mt5
    mt5_adapter.session_manager.use_live_mt5 = True
    
    try:
        # A: Check normal valid request (0.10 lots, SL = 1.08450 i.e. 5.0 pips away)
        # Recalculated risk: 0.10 * 5 * $10 = $5.0 (<= $10 limit). Should pass!
        valid, entry, lots, sl, tp, msg = mt5_adapter.validate_and_normalize_order_request(
            "EURUSD", TradeSide.BUY, OrderType.MARKET, 0.0, 0.10, 1.08450, 1.08600
        )
        print(f"Normal Request: valid={valid}, SL={sl}, TP={tp}, msg={msg}")
        assert valid == True
        
        # B: Check stops level adjustment trigger that exceeds risk limits!
        # Requested SL = 1.08490 (1.0 pip away).
        # Normalization forces SL to 1.08450 (5.0 pips away due to Stops Level = 50).
        # We request 0.30 lots.
        # Normalized recalculated risk: 0.30 * 5 * $10 = $15.0 (exceeds $10 limit).
        # Should be rejected!
        valid2, entry2, lots2, sl2, tp2, msg2 = mt5_adapter.validate_and_normalize_order_request(
            "EURUSD", TradeSide.BUY, OrderType.MARKET, 0.0, 0.30, 1.08490, 1.08600
        )
        print(f"Adjusted stops level trigger: valid={valid2}, msg={msg2}")
        
        assert valid2 == False
        assert "exceeds maximum allowed risk" in msg2
        
        # C: Check order bounds validation: BUY SL > Entry
        # Requested SL = 1.08550 (above entry price 1.08500!). Should be rejected immediately!
        valid3, entry3, lots3, sl3, tp3, msg3 = mt5_adapter.validate_and_normalize_order_request(
            "EURUSD", TradeSide.BUY, OrderType.MARKET, 0.0, 0.10, 1.08550, 1.08600
        )
        print(f"Bounds check trigger: valid={valid3}, msg={msg3}")
        assert valid3 == False
        assert "Invalid stop loss" in msg3
        
        print(" -> Issue 12 broker normalization and risk re-validation passed.")
        
    finally:
        mt5_mock.symbol_info = original_symbol_info
        mt5_mock.symbol_select = original_symbol_select
        mt5_mock.account_info = original_account_info
        mt5_adapter.session_manager.use_live_mt5 = original_use_live

def test_max_open_positions():
    print("\n==================================================")
    print("TESTING ISSUE 13: Live Position Queries & Enforcements")
    print("==================================================")
    
    # 1. Setup risk settings max_open_positions = 2
    original_max = trade_manager.risk_settings.max_open_positions
    trade_manager.risk_settings.max_open_positions = 2
    
    # 2. Mock mt5_adapter.get_positions to return 2 active positions
    class MockTradeRecord:
        def __init__(self, trade_id, symbol):
            self.trade_id = trade_id
            self.symbol = symbol
            self.state = TradeSide.BUY
            
    original_get_positions = mt5_adapter.get_positions
    original_is_connected = mt5_adapter.is_connected
    
    mt5_adapter.get_positions = lambda: [MockTradeRecord("t1", "EURUSD"), MockTradeRecord("t2", "EURUSD")]
    mt5_adapter.session_manager.use_live_mt5 = True
    
    try:
        acc_info = AccountInfo(
            account_number=12345,
            balance=10000.0,
            equity=10000.0,
            open_positions_count=0,  # Cached count is 0 (drifted!), but live count is 2!
            is_connected=True
        )
        
        report = TradeDecisionReport(
            symbol="EURUSD",
            decision=TradeSide.BUY,
            timeframe="M15",
            confidence=0.85,
            confirmation_score=0.85,
            reasoning=["Trend is bullish"],
            rejection_reasons=[],
            suggested_entry=1.08500,
            suggested_sl=1.08300,
            suggested_tp=1.08900,
            suggested_lots=0.10,
            risk_reward_ratio=2.0,
            breakdown=ConfirmationBreakdown(
                trend_score=1.0,
                market_structure_score=1.0,
                momentum_volume_score=1.0,
                heuristic_score=1.0,
                risk_news_score=1.0,
                total_score=1.0,
                passed=True
            ),
            timestamp=datetime.now()
        )
        
        # Evaluate risk
        check = risk_manager.evaluate_risk(acc_info, report, "EURUSD")
        print(f"Risk Check: passed={check.passed}, reasons={check.reasons}")
        
        # Verify it was rejected because we queried live positions (2) >= max limit (2)
        assert check.passed == False
        assert any("Maximum open positions limit reached" in r for r in check.reasons), "Expected max open positions error message"
        print(" -> Issue 13 live position count query verification passed.")
    finally:
        trade_manager.risk_settings.max_open_positions = original_max
        mt5_adapter.get_positions = original_get_positions
        mt5_adapter.session_manager.use_live_mt5 = original_is_connected

if __name__ == "__main__":
    test_realized_daily_pnl()
    test_drawdown_protection_and_restart()
    test_order_revalidation()
    test_max_open_positions()
    print("\n==================================================")
    print("ALL PART 3 FIXES VERIFIED AND WORKING SUCCESSFULLY!")
    print("==================================================")
