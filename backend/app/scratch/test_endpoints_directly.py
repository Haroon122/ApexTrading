import sys
import os
import asyncio

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.api.v1.endpoints import start_bot

async def run_test():
    print("Calling start_bot() endpoint directly...")
    try:
        res = await start_bot(symbol="EURUSD", timeframe="M15")
        print("Result:", res)
    except Exception as e:
        print("Exception occurred:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_test())
