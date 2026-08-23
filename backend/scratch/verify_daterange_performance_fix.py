import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.backtester import backtester, parse_date_str

def test_date_parsing_and_validation():
    print("[TEST 1] Testing Date Parsing & Range Validation...")
    
    # ISO format
    dt1 = parse_date_str("2026-08-01")
    assert dt1 is not None and dt1.year == 2026 and dt1.month == 8 and dt1.day == 1
    print("  [OK] ISO date parsing (%Y-%m-%d)")
    
    # Slash format (MM/DD/YYYY)
    dt2 = parse_date_str("08/01/2026")
    assert dt2 is not None and dt2.year == 2026 and dt2.month == 8 and dt2.day == 1
    print("  [OK] Slash date parsing (%m/%d/%Y)")
    
    # Invalid date range (from > to)
    res_inv = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M1",
        from_date="2026-08-14",
        to_date="2026-08-01"
    )
    assert "error" in res_inv and "must be earlier" in res_inv["error"]
    print("  [OK] Invalid date range rejection (from_date >= to_date)")
    
    # Exceeding safe candle limit (e.g. 60 days of M1 = ~86,400 candles)
    res_huge = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M1",
        from_date="2026-06-01",
        to_date="2026-08-14"
    )
    assert "error" in res_huge and "exceeding the maximum safe limit" in res_huge["error"]
    print("  [OK] Over-sized range rejection (>25,000 candles guard)")

def test_historical_date_range_performance():
    print("\n[TEST 2] Testing Historical Date Range Execution & Performance...")
    
    t0 = time.time()
    res1 = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M1",
        from_date="2026-08-01",
        to_date="2026-08-07", # 7 days M1 (~10,000 candles)
        initial_balance=100.0,
        risk_pct=1.5,
        lot_sizing_mode="FIXED",
        user_configured_lot=0.10
    )
    t1 = time.time() - t0
    
    if "error" in res1:
        print(f"  [FAIL] Backtest returned error: {res1['error']}")
        sys.exit(1)
        
    print(f"  [OK] 7 Days M1 Backtest completed in {t1:.2f}s!")
    print(f"  Summary: Symbol={res1['symbol']} | TF={res1['timeframe']} | Period={res1['from_date']} to {res1['to_date']}")
    print(f"  Trades: Total={res1['total_trades']} (Wins={res1['winning_trades']}, Losses={res1['losing_trades']}, BE={res1['break_even_trades']})")
    print(f"  Final Balance: ${res1['final_balance']} | ROI: {res1['net_roi_pct']}%")

    # Run consecutive test for determinism check
    res2 = backtester.run_backtest(
        symbol="XAUUSD",
        timeframe="M1",
        from_date="2026-08-01",
        to_date="2026-08-07",
        initial_balance=100.0,
        risk_pct=1.5,
        lot_sizing_mode="FIXED",
        user_configured_lot=0.10
    )
    
    assert res1["total_trades"] == res2["total_trades"], "Trade count non-deterministic!"
    assert res1["final_balance"] == res2["final_balance"], "Final balance non-deterministic!"
    print("  [OK] Deterministic results verified across consecutive runs!")

if __name__ == "__main__":
    test_date_parsing_and_validation()
    test_historical_date_range_performance()
    print("\n=========================================================")
    print("ALL DATE RANGE PERFORMANCE & VERIFICATION TESTS PASSED!")
    print("=========================================================")
