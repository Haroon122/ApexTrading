import sys
import os
import json
import asyncio
import sqlite3
from datetime import datetime, timedelta

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.domain.models import Candle, TickerData, TradeSide, AccountInfo, TradeDecisionReport, OrderType, ConfirmationBreakdown, SavedAccount, BotStatus, TradeState, TradeRecord
from app.services.session_manager import session_manager, encrypt_password, decrypt_password, ACCOUNTS_PATH
from app.services.trade_manager import trade_manager
from app.services.trading_worker import trading_worker
from app.adapters.mt5_adapter import mt5_adapter
from app.core.config import settings

# Path to db for cleaning
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "trading_memory.db")

def clean_db():
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS trade_history_memory (trade_id TEXT PRIMARY KEY)")
        cursor.execute("CREATE TABLE IF NOT EXISTS system_metadata (key TEXT PRIMARY KEY)")
        cursor.execute("DELETE FROM trade_history_memory")
        cursor.execute("DELETE FROM system_metadata")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error cleaning database: {e}")

async def test_single_worker():
    print("\n==================================================")
    print("TESTING ISSUE 14: Single Background Worker Loop")
    print("==================================================")
    
    # 1. Stop worker if running
    if trading_worker.is_running():
        await trading_worker.stop()
        
    print("Starting background worker...")
    started, msg = await trading_worker.start("EURUSD", "M15")
    print(f"Start attempt 1: success={started}, message='{msg}'")
    assert started == True
    
    # 2. Attempt to start again concurrently (should block / fail)
    started2, msg2 = await trading_worker.start("EURUSD", "M15")
    print(f"Start attempt 2 (concurrent): success={started2}, message='{msg2}'")
    assert started2 == False
    assert "already running" in msg2
    
    # 3. Verify it runs independently of WS clients
    print(f"Worker is_running check: {trading_worker.is_running()}")
    assert trading_worker.is_running() == True
    
    await trading_worker.stop()
    print("Stopped background worker.")
    print(" -> Issue 14 single-worker checks passed successfully.")

async def test_duplicate_signal_rejection():
    print("\n==================================================")
    print("TESTING ISSUE 15: Signal Idempotency Protection")
    print("==================================================")
    
    clean_db()
    trade_manager.executed_signals.clear()
    
    # Mock mt5_adapter.place_order to succeed immediately
    class MockPosition:
        ticket = 12345
        symbol = "EURUSD"
        price_open = 1.08500
        volume = 0.10
        sl = 1.08300
        tp = 1.08900
        
    original_place_order = mt5_adapter.place_order
    original_positions_get = mt5_adapter.session_manager.use_live_mt5
    
    # Mocking order placement success
    mt5_adapter.place_order = lambda **kwargs: (
        True, 
        TradeRecord(
            trade_id="12345", symbol="EURUSD", side=TradeSide.BUY,
            order_type=OrderType.MARKET, entry_price=1.08500,
            sl_price=1.08300, tp_price=1.08900, lots=0.10, state=TradeState.OPEN,
            open_time=datetime.now()
        ), 
        "Placed successfully"
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
            trend_score=1.0, market_structure_score=1.0, momentum_volume_score=1.0,
            heuristic_score=1.0, risk_news_score=1.0, total_score=1.0, passed=True
        ),
        timestamp=datetime.now()
    )
    
    original_bot_status = trade_manager.bot_status
    trade_manager.bot_status = BotStatus.RUNNING
    
    try:
        # A: Call execute_decision 1st time. Should execute successfully!
        ok1, trade1, msg1 = trade_manager.execute_decision(report, 0.10)
        print(f"1st execution attempt: success={ok1}, msg='{msg1}'")
        assert ok1 == True
        
        # B: Call execute_decision 2nd time immediately. Should reject as duplicate!
        ok2, trade2, msg2 = trade_manager.execute_decision(report, 0.10)
        print(f"2nd execution attempt: success={ok2}, msg='{msg2}'")
        assert ok2 == False
        assert "Duplicate signal detected" in msg2
        print(" -> Issue 15 signal idempotency checks passed successfully.")
    finally:
        mt5_adapter.place_order = original_place_order
        trade_manager.bot_status = original_bot_status

async def test_reconciliation():
    print("\n==================================================")
    print("TESTING ISSUE 16: Broker Position Reconciliation")
    print("==================================================")
    
    # Setup mock terminal response
    import MetaTrader5 as mt5_mock
    original_order_send = mt5_mock.order_send
    original_positions_get = mt5_mock.positions_get
    original_symbol_info = mt5_mock.symbol_info
    original_symbol_select = mt5_mock.symbol_select
    original_account_info = mt5_mock.account_info
    original_use_live = mt5_adapter.session_manager.use_live_mt5
    
    # Mocking order placement success from MetaTrader5
    class MockResult:
        retcode = 10009 # mt5.TRADE_RETCODE_DONE
        order = 777888
        deal = 777888
        price = 1.08550 # Slipped price!
        comment = "Done"
        
    class MockPosition:
        ticket = 777888
        identifier = 777888
        symbol = "EURUSD"
        price_open = 1.08550  # Confirmed slipped price!
        volume = 0.15         # Confirmed adjusted volume!
        sl = 1.08350
        tp = 1.09150          # Confirmed broker TP
        type = 0
        time = int(datetime.now().timestamp())
        profit = 0.0
        
    class MockSymbolInfo:
        visible = True
        trade_mode = 4
        digits = 5
        point = 0.00001
        trade_stops_level = 0
        volume_min = 0.01
        volume_max = 100.0
        volume_step = 0.01
        filling_mode = 2
        ask = 1.08550
        bid = 1.08550
        
    class MockMT5AccountInfo:
        login = 12345
        name = "Test Account"
        company = "Mock Broker"
        server = "MockServer"
        trade_mode = 0
        currency = "USD"
        leverage = 100
        balance = 100000.0
        equity = 100000.0
        margin = 0.0
        margin_free = 100000.0
        margin_level = 0.0
        profit = 0.0
        
    mt5_mock.order_send = lambda request: MockResult()
    mt5_mock.positions_get = lambda **kwargs: [MockPosition()]
    mt5_mock.symbol_info = lambda sym: MockSymbolInfo()
    mt5_mock.symbol_select = lambda sym, select: True
    mt5_mock.account_info = lambda: MockMT5AccountInfo()
    mt5_adapter.session_manager.use_live_mt5 = True
    
    try:
        # Request order placement
        # We request SL = 1.08300 and TP = 1.09100.
        # But broker sets SL = 1.08350 and TP = 1.09150, and slips price to 1.08550.
        ok, trade, msg = mt5_adapter.place_order(
            symbol="EURUSD", side=TradeSide.BUY, lots=0.10, sl=1.08300, tp=1.09100
        )
        print(f"Placed trade: success={ok}, msg='{msg}'")
        if trade:
            print(f"trade details: entry_price={trade.entry_price}, lots={trade.lots}, SL={trade.sl_price}, TP={trade.tp_price}")
        
        # Verify that broker-confirmed values are returned, NOT the requested ones!
        assert trade.entry_price == 1.08550
        assert trade.lots == 0.15
        assert trade.sl_price == 1.08350
        assert trade.tp_price == 1.09150
        print(" -> Issue 16 position reconciliation checks passed successfully.")
    finally:
        mt5_mock.order_send = original_order_send
        mt5_mock.positions_get = original_positions_get
        mt5_mock.symbol_info = original_symbol_info
        mt5_mock.symbol_select = original_symbol_select
        mt5_mock.account_info = original_account_info
        mt5_adapter.session_manager.use_live_mt5 = original_use_live

async def test_security_and_encryption():
    print("\n==================================================")
    print("TESTING ISSUE 19: API Security & Encryption at Rest")
    print("==================================================")
    
    # 1. Test Symmetric Encryption
    test_pass = "secure_mt5_pass_123"
    enc = encrypt_password(test_pass)
    dec = decrypt_password(enc)
    print(f"Plaintext Password:   {test_pass}")
    print(f"Encrypted Password:   {enc}")
    print(f"Decrypted Password:   {dec}")
    assert enc != test_pass
    assert dec == test_pass
    print("Symmetric password encryption verified.")
    
    # 2. Test Saved Accounts Disk Encryption
    # Clear and save an account
    session_manager.saved_accounts.clear()
    sa_id = "test_acc@MockServer"
    session_manager.saved_accounts[sa_id] = SavedAccount(
        id=sa_id,
        name="Test Encrypted Account",
        login=12345,
        password="my_secret_password",
        server="MockServer",
        broker="Mock Broker"
    )
    session_manager._save_saved_accounts()
    
    # Check physical file contents
    assert os.path.exists(ACCOUNTS_PATH)
    with open(ACCOUNTS_PATH, "r") as f:
        file_json = json.load(f)
        saved_pass = file_json[sa_id]["password"]
        print(f"Password stored in file: {saved_pass}")
        # Verify it is encrypted in the file
        assert saved_pass != "my_secret_password"
        assert decrypt_password(saved_pass) == "my_secret_password"
        
    print("Persisted credentials file is verified encrypted at rest.")
    
    # 3. Verify public saved accounts response model
    # Simulate endpoints GET /account/saved return
    from app.api.v1.endpoints import get_saved_accounts
    public_accounts = get_saved_accounts()
    print(f"Public saved accounts: {public_accounts}")
    # Verify no password field exists in the returned items
    for pa in public_accounts:
        assert not hasattr(pa, 'password'), "Password field exposed in public model!"
        assert "password" not in pa.model_dump(), "Password field exposed in public model dictionary!"
        
    print("Public saved accounts model verified (no credentials exposed).")
    
    # 4. Test API Key Auth Dependency
    # We call verify_api_key with and without the header
    from app.api.v1.endpoints import verify_api_key
    from fastapi import HTTPException
    
    print("Testing API Key verification with valid key...")
    try:
        verify_api_key(settings.API_KEY)
        print("Valid key: OK")
    except HTTPException:
        assert False, "Valid API key failed validation!"
        
    print("Testing API Key verification with invalid key...")
    try:
        verify_api_key("wrong_key")
        assert False, "Invalid API key did not trigger 401/403 rejection!"
    except HTTPException as e:
        print(f"Rejection success: status_code={e.status_code}, detail='{e.detail}'")
        assert e.status_code == 401
        
    print("Testing API Key verification with missing key...")
    try:
        verify_api_key(None)
        assert False, "Missing API key did not trigger 401/403 rejection!"
    except HTTPException as e:
        print(f"Rejection success: status_code={e.status_code}, detail='{e.detail}'")
        assert e.status_code == 401
        
    print(" -> Issue 19 security checks passed successfully.")

if __name__ == "__main__":
    from app.domain.models import TradeState
    asyncio.run(test_single_worker())
    asyncio.run(test_duplicate_signal_rejection())
    asyncio.run(test_reconciliation())
    asyncio.run(test_security_and_encryption())
    print("\n==================================================")
    print("ALL PART 4 FIXES VERIFIED AND WORKING SUCCESSFULLY!")
    print("==================================================")
