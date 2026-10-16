import urllib.request
import json
import time

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

print("=== TESTING LIVE ORDER PLACEMENT & LIFECYCLE ===")
# 1. Fetch current price
ev = api("/api/v1/trade/evaluate?symbol=XAUUSD&timeframe=M15")
current_price = ev.get("suggested_entry", 4190.0)
sl = round(current_price - 15.0, 2) # $15 SL
tp = round(current_price + 30.0, 2) # $30 TP
print(f"Current Gold Price: {current_price} | Test SL: {sl} | Test TP: {tp}")

# 2. Place test manual order
order_payload = {
    "symbol": "XAUUSD",
    "side": "BUY",
    "order_type": "MARKET",
    "lots": 0.01,
    "price": 0.0,
    "sl": sl,
    "tp": tp
}
print(f"Sending Manual Order: {order_payload}")
order_res = api("/api/v1/trade/manual", "POST", order_payload)
print(f"Order Placement Result: success={order_res.get('success')}, message={order_res.get('message')}")

trade_id = None
if order_res.get("success") and order_res.get("order"):
    trade_id = order_res["order"].get("trade_id")
    print(f"Opened Test Position Ticket: {trade_id}")

# 3. Check open positions
time.sleep(1)
positions = api("/api/v1/trade/positions")
print(f"Current Open Positions Count: {len(positions)}")
for p in positions:
    print(f"  Pos #{p.get('trade_id')}: {p.get('symbol')} {p.get('side')} {p.get('lots')} lots | Open: {p.get('entry_price')} | SL: {p.get('sl_price')} | TP: {p.get('tp_price')} | PnL: ${p.get('pnl_usd')}")

# 4. If we opened a test order, close it now to leave the account clean
if trade_id:
    print(f"\nClosing Test Position #{trade_id}...")
    close_res = api(f"/api/v1/trade/positions/close?trade_id={trade_id}", "POST")
    print("Close Result:", close_res.get("success"), close_res.get("message"))
    time.sleep(1)
    acc = api("/api/v1/account")
    print(f"Account Balance: {acc.get('balance')} | Equity: {acc.get('equity')}")
