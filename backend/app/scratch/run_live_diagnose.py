import sys
import os
import asyncio
import logging

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout
)

from app.services.trading_worker import trading_worker
from app.services.trade_manager import trade_manager
from app.domain.models import BotStatus
from app.adapters.mt5_adapter import mt5_adapter

async def main():
    print("==================================================")
    print("RUNNING LIVE DIAGNOSTICS FOR AUTO-TRADING ENGINE")
    print("==================================================")
    
    # Enable autotrading status
    trade_manager.bot_status = BotStatus.RUNNING
    print(f"Set trade_manager.bot_status to: {trade_manager.bot_status.value}")
    print(f"Risk Settings auto_trade_enabled: {trade_manager.risk_settings.auto_trade_enabled}")
    
    # Check MT5 connection
    acc = mt5_adapter.get_account_info()
    if acc:
        print(f"MT5 Account Info: login={acc.account_number}, balance={acc.balance}, equity={acc.equity}, is_connected={getattr(acc, 'is_connected', False)}")
    else:
        print("MT5 Account Info: Not Available")
        
    print("\nStarting background trading worker for EURUSD on M15 timeframe...")
    success, msg = await trading_worker.start("EURUSD", "M15")
    print(f"Worker start success: {success} | Message: {msg}")
    
    if not success:
        print("Worker failed to start. Exiting.")
        return
        
    print("\nLetting worker loop execute for 15 seconds...")
    await asyncio.sleep(15.0)
    
    print("\nStopping worker...")
    stop_success, stop_msg = await trading_worker.stop()
    print(f"Worker stop success: {stop_success} | Message: {stop_msg}")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
