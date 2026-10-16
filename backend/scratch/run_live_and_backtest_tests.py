import urllib.request
import json
import sys
import os

def call_api(endpoint, method="GET", payload=None):
    url = f"http://127.0.0.1:8000{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

print("=== 1. CHECK BOT STATUS & ACCOUNT ===")
health = call_api("/api/v1/health")
print("Health:", health)
acc = call_api("/api/v1/account")
print(f"Account: #{acc.get('account_number')} | Balance: {acc.get('balance')} {acc.get('currency')} | Equity: {acc.get('equity')} | Daily PnL: {acc.get('daily_pnl_usd')}")

print("\n=== 2. RUN BACKTEST ON M15 (LAST 1000 CANDLES) ===")
bt_m15 = call_api("/api/v1/backtest/run", method="POST", payload={
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "count": 1000,
    "initial_balance": 1000.0,
    "risk_pct": 1.0,
    "lot_sizing_mode": "FIXED",
    "user_configured_lot": 0.01
})
if "error" in bt_m15:
    print("M15 Backtest Error:", bt_m15["error"])
else:
    print(f"M15 Total Trades: {bt_m15.get('total_trades')}")
    print(f"M15 Win Rate:     {bt_m15.get('win_rate_pct')}% ({bt_m15.get('winning_trades')} wins / {bt_m15.get('losing_trades')} losses)")
    print(f"M15 Net Profit:   ${bt_m15.get('net_profit_usd')} (ROI: {bt_m15.get('net_roi_pct')}%)")
    print(f"M15 Profit Factor:{bt_m15.get('profit_factor')}")
    print(f"M15 Max Drawdown: {bt_m15.get('max_drawdown_pct')}%")

print("\n=== 3. RUN BACKTEST ON M5 (LAST 1000 CANDLES) ===")
bt_m5 = call_api("/api/v1/backtest/run", method="POST", payload={
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "count": 1000,
    "initial_balance": 1000.0,
    "risk_pct": 1.0,
    "lot_sizing_mode": "FIXED",
    "user_configured_lot": 0.01
})
if "error" in bt_m5:
    print("M5 Backtest Error:", bt_m5["error"])
else:
    print(f"M5 Total Trades:  {bt_m5.get('total_trades')}")
    print(f"M5 Win Rate:      {bt_m5.get('win_rate_pct')}% ({bt_m5.get('winning_trades')} wins / {bt_m5.get('losing_trades')} losses)")
    print(f"M5 Net Profit:    ${bt_m5.get('net_profit_usd')} (ROI: {bt_m5.get('net_roi_pct')}%)")
    print(f"M5 Profit Factor: {bt_m5.get('profit_factor')}")
    print(f"M5 Max Drawdown:  {bt_m5.get('max_drawdown_pct')}%")

print("\n=== 4. LIVE MARKET EVALUATION ACROSS TIMEFRAMES ===")
for tf in ["M1", "M5", "M15", "H1"]:
    ev = call_api(f"/api/v1/trade/evaluate?symbol=XAUUSD&timeframe={tf}")
    print(f"Timeframe: {tf:4} | Decision: {ev.get('decision', 'N/A'):5} | Confidence: {ev.get('confidence', 0)*100:.1f}% | Setup: {ev.get('setup_name', 'None')} | Rejections: {ev.get('rejection_reasons', [])}")
