import urllib.request
import json
import sys

def api(url, method="GET", body=None):
    full_url = f"http://127.0.0.1:8000{url}"
    data = json.dumps(body).encode("utf-8") if body else None
    headers = {"Content-Type": "application/json", "X-API-Key": "apex_secret_api_key_2026"}
    req = urllib.request.Request(full_url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

print("=== 1. API HEALTH & ACCOUNT ===")
h = api("/api/v1/health")
print("Health:", h)
acc = api("/api/v1/account")
print(f"Account #{acc.get('account_number')} | Bal: {acc.get('balance')} {acc.get('currency')} | Eq: {acc.get('equity')} | Mode: {acc.get('connection_mode')}")

print("\n=== 2. FAST BACKTEST (150 CANDLES M15) ===")
bt = api("/api/v1/backtest/run", "POST", {
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "count": 150,
    "initial_balance": 1000.0,
    "risk_pct": 1.0,
    "lot_sizing_mode": "FIXED",
    "user_configured_lot": 0.01
})
if "error" in bt:
    print("Backtest Error:", bt["error"])
else:
    print(f"Trades: {bt.get('total_trades')} | Win Rate: {bt.get('win_rate_pct')}% | Wins: {bt.get('winning_trades')} | Losses: {bt.get('losing_trades')}")
    print(f"Net PnL: ${bt.get('net_profit_usd')} | ROI: {bt.get('net_roi_pct')}% | Profit Factor: {bt.get('profit_factor')}")
    print(f"Max Drawdown: {bt.get('max_drawdown_pct')}%")

print("\n=== 3. LIVE EVALUATION & DECISION ENGINE ===")
ev = api("/api/v1/trade/evaluate?symbol=XAUUSD&timeframe=M15")
print(f"Decision: {ev.get('decision')} | Confidence: {ev.get('confidence', 0)*100:.1f}%")
print(f"Suggested Entry: {ev.get('suggested_entry')} | SL: {ev.get('suggested_sl')} | TP: {ev.get('suggested_tp')} | RR: 1:{ev.get('risk_reward_ratio')}")
print(f"Active Setup: {ev.get('setup_name')}")
print(f"Rejections: {ev.get('rejection_reasons')}")

print("\n=== 4. TESTING PIPELINE EXECUTION CALL ===")
exec_res = api("/api/v1/trade/execute?symbol=XAUUSD&timeframe=M15", "POST")
print("Execute AI Signal Result:")
print("  Success:", exec_res.get("success"))
print("  Message:", exec_res.get("message"))
print("  Risk Reasons:", exec_res.get("risk_reasons"))
if exec_res.get("report"):
    print("  Report Decision:", exec_res["report"].get("decision"))
    print("  Report Rejections:", exec_res["report"].get("rejection_reasons"))
