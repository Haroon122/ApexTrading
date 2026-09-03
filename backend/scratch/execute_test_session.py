"""
Live DEMO Test Execution Script
Executes 25-30 live/demo trades via FastAPI backend endpoints,
logs risk evaluation checks, tracks open positions, monitors trailing stop/breakeven,
and verifies database persistence.
"""

import urllib.request
import json
import time
import sqlite3
from datetime import datetime

BASE_URL = "http://localhost:8000/api/v1"
API_KEY = "apex_secret_api_key_2026"
HEADERS = {
    "Content-Type": "application/json",
    "X-API-Key": API_KEY
}

def make_request(url, method="GET", data=None):
    req = urllib.request.Request(url, headers=HEADERS, method=method)
    if data:
        json_bytes = json.dumps(data).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    else:
        json_bytes = None
    
    try:
        with urllib.request.urlopen(req, data=json_bytes) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return json.loads(err_body)
        except Exception:
            return {"error": str(e), "body": err_body}
    except Exception as e:
        return {"error": str(e)}

def run_test():
    print("=== STARTING 25-30 LIVE/DEMO TRADES EXECUTION TEST ===")
    
    # 1. First, check risk settings and health
    health = make_request(f"{BASE_URL}/health")
    print(f"Backend Health: {health}")
    
    # Update risk settings to allow testing up to 30 trades (raise max_trades_per_day, lower cooldown if needed)
    print("\nSetting test risk parameters for execution test...")
    risk_settings_update = {
        "max_trades_per_day": 50,
        "auto_trade_cooldown_seconds": 0, # zero cooldown for batch testing
        "max_open_positions": 5,
        "max_allowed_lot": 1.00,
        "user_configured_lot": 0.01, # 0.01 lot size for safety on demo
        "lot_sizing_mode": "FIXED",
        "min_risk_reward_ratio": 2.0
    }
    update_res = make_request(f"{BASE_URL}/settings/risk", method="POST", data=risk_settings_update)
    print(f"Risk Settings Updated: {update_res.get('success')}")

    trade_logs = []
    risk_evaluation_logs = []
    executed_trades_count = 0
    
    # Perform 30 trade execution attempts
    for i in range(1, 31):
        print(f"\n--- TRADE ATTEMPT #{i} ---")
        
        # Call /trade/execute endpoint
        res = make_request(f"{BASE_URL}/trade/execute?symbol=XAUUSD&timeframe=M15", method="POST")
        
        success = res.get("success", False)
        msg = res.get("message", "")
        report = res.get("report") or {}
        trade = res.get("trade") or {}
        risk_reasons = res.get("risk_reasons") or []
        
        setup_name = report.get("setup_name") or trade.get("setup_name") or "Unknown"
        symbol = report.get("symbol") or "XAUUSD"
        side = report.get("decision") or trade.get("side") or "HOLD"
        confidence = report.get("confidence") or 0.0
        entry = report.get("suggested_entry") or trade.get("entry_price") or 0.0
        sl = report.get("suggested_sl") or trade.get("sl_price") or 0.0
        tp = report.get("suggested_tp") or trade.get("tp_price") or 0.0
        lots = trade.get("lots") or 0.01
        
        log_entry = {
            "attempt": i,
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "message": msg,
            "symbol": symbol,
            "side": side,
            "lots": lots,
            "entry_price": entry,
            "sl": sl,
            "tp": tp,
            "confidence_score": confidence,
            "setup_name": setup_name,
            "risk_reasons": risk_reasons,
            "trade_id": trade.get("trade_id")
        }
        trade_logs.append(log_entry)
        
        if success:
            executed_trades_count += 1
            print(f"SUCCESS: Trade #{trade.get('trade_id')} Executed! Side: {side}, Entry: {entry}, SL: {sl}, TP: {tp}, Setup: {setup_name}")
        else:
            print(f"NO TRADE: {msg}")
            if risk_reasons:
                print(f"Risk Reasons: {risk_reasons}")
                
        # If auto trade decision was HOLD, let's also test manual execution with AI decision report parameters to verify risk manager on forced trades
        if not success and "HOLD" in msg:
            print("Attempting manual AI order placement to test risk_manager enforcement under active market conditions...")
            manual_payload = {
                "symbol": "XAUUSD",
                "side": "BUY" if i % 2 == 1 else "SELL",
                "order_type": "MARKET",
                "lots": 0.01,
                "price": 0.0,
                "sl": (entry - 3.0) if (i % 2 == 1 and entry > 0) else (2500.0 if i % 2 == 1 else 2510.0),
                "tp": (entry + 6.0) if (i % 2 == 1 and entry > 0) else (2515.0 if i % 2 == 1 else 2490.0)
            }
            man_res = make_request(f"{BASE_URL}/trade/manual", method="POST", data=manual_payload)
            man_success = man_res.get("success", False)
            man_msg = man_res.get("message", "")
            man_order = man_res.get("order") or {}
            
            log_entry["manual_attempt"] = {
                "success": man_success,
                "message": man_msg,
                "trade_id": man_order.get("trade_id")
            }
            if man_success:
                executed_trades_count += 1
                print(f"MANUAL EXECUTION SUCCESS: Trade #{man_order.get('trade_id')} placed! Side: {manual_payload['side']}")
            else:
                print(f"MANUAL EXECUTION REJECTED: {man_msg}")
        
        time.sleep(0.5) # small pause between attempts
        
    print(f"\nCompleted 30 execution attempts. Total Executed Trades: {executed_trades_count}")

    # 2. Track open positions and lifecycle management
    print("\n--- TRACKING OPEN POSITIONS & LIFECYCLE MANAGEMENT ---")
    positions = make_request(f"{BASE_URL}/trade/positions")
    print(f"Active Open Positions Count: {len(positions)}")
    for pos in positions:
        print(f"Position #{pos.get('trade_id')}: {pos.get('symbol')} {pos.get('side')} {pos.get('lots')} lots @ {pos.get('entry_price')}, SL: {pos.get('sl_price')}, TP: {pos.get('tp_price')}, PnL: ${pos.get('pnl_usd')}")

    # Close open positions to test position sync and history recording
    if positions:
        print("\nClosing open positions to test lifecycle closure and position sync...")
        close_all_res = make_request(f"{BASE_URL}/trade/positions/close-all", method="POST")
        print(f"Close All Result: {close_all_res}")

    # Trigger account refresh to sync history deals into SQLite database
    time.sleep(1.5)
    refresh_res = make_request(f"{BASE_URL}/account/refresh", method="POST")
    print(f"Account Refresh & SQLite History Sync Triggered: {refresh_res.get('success')}")

    # 3. Database Verification
    print("\n--- VERIFYING DATABASE (backend/data/trading_memory.db) ---")
    conn = sqlite3.connect("data/trading_memory.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT trade_id, symbol, side, timeframe, entry_price, exit_price, lots, sl_price, tp_price, pnl_usd, win_loss, exit_reason, setup_name, open_timestamp, close_timestamp
        FROM trade_history_memory
        ORDER BY open_timestamp DESC
        LIMIT 35
    """)
    db_rows = cursor.fetchall()
    print(f"Fetched {len(db_rows)} recent trade records from database:")
    
    db_summary = []
    for r in db_rows:
        row_dict = dict(r)
        db_summary.append(row_dict)
        print(f"Trade #{row_dict['trade_id']}: Side={row_dict['side']}, Lots={row_dict['lots']}, PnL=${row_dict['pnl_usd']}, Win/Loss={row_dict['win_loss']}, ExitReason='{row_dict['exit_reason']}', Setup='{row_dict['setup_name']}'")
        
    # Save test logs to JSON file for report synthesis
    out_file = "scratch/test_session_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "executed_count": executed_trades_count,
            "trade_logs": trade_logs,
            "db_records": db_summary
        }, f, indent=2)
    print(f"\nSaved test execution results to {out_file}")

if __name__ == "__main__":
    run_test()
