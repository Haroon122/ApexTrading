import urllib.request
import json
import sys
import os

sys.path.insert(0, os.path.abspath("c:/Users/localadmin/Desktop/Trading_Bot/backend"))

from app.services.backtester import backtester

def test_user_exact_config():
    print("==========================================================")
    print("RUNNING USER'S EXACT TEST CONFIGURATION")
    print("==========================================================")

    payload = {
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "from_date": "2026-08-13",
        "to_date": "2026-08-14",
        "initial_balance": 500.0,
        "risk_pct": 1.0,
        "lot_sizing_mode": "FIXED",
        "user_configured_lot": 0.10,
        "auto_trade_cooldown_seconds": 180,
        "daily_loss_limit_pct": 6.0,
        "max_consecutive_losses": 5,
        "max_spread_pips": 5.0
    }

    # 1. Test Direct Backend Execution
    print("\n--- 1. DIRECT BACKEND ENGINE TEST ---")
    res = backtester.run_backtest(
        symbol=payload["symbol"],
        timeframe=payload["timeframe"],
        from_date=payload["from_date"],
        to_date=payload["to_date"],
        initial_balance=payload["initial_balance"],
        risk_pct=payload["risk_pct"],
        lot_sizing_mode=payload["lot_sizing_mode"],
        user_configured_lot=payload["user_configured_lot"],
        auto_trade_cooldown_seconds=payload["auto_trade_cooldown_seconds"],
        daily_loss_limit_pct=payload["daily_loss_limit_pct"],
        max_consecutive_losses=payload["max_consecutive_losses"],
        max_spread_pips=payload["max_spread_pips"]
    )

    print("Result Keys Received:", list(res.keys()))
    if "error" in res:
        print("API ERROR RETURNED:", res["error"])
    else:
        print(f"Symbol: {res['symbol']} | Timeframe: {res['timeframe']}")
        print(f"Period: {res['from_date']} to {res['to_date']}")
        print(f"Initial Balance: ${res['initial_balance']} -> Final Balance: ${res['final_balance']}")
        print(f"Net Profit: ${res['net_profit_usd']} ({res['net_roi_pct']}%)")
        print(f"Total Trades: {res['total_trades']} | Total Opps: {res['total_opportunities']}")
        print(f"Executed: {res['executed_count']} | Blocked: {res['blocked_count']}")
        print(f"Rejection Breakdown: {res['rejection_breakdown']}")

    # 2. Test Live HTTP Endpoint
    print("\n--- 2. HTTP POST TO LIVE API ENDPOINT ---")
    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/v1/backtest/run",
            data=req_data,
            headers={"Content-Type": "application/json"}
        )
        http_res = urllib.request.urlopen(req)
        http_body = json.loads(http_res.read().decode("utf-8"))
        print("HTTP Status Code:", http_res.getcode())
        print(f"HTTP Response Symbol: {http_body['symbol']} | Initial Balance: ${http_body['initial_balance']}")
        print(f"HTTP Response Final Balance: ${http_body['final_balance']} | Net Profit: ${http_body['net_profit_usd']}")
        print(f"HTTP Total Trades: {http_body['total_trades']} | Blocked Count: {http_body['blocked_count']}")
    except Exception as e:
        print("HTTP POST Error:", e)

if __name__ == "__main__":
    test_user_exact_config()
