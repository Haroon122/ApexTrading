import os
import sys

# Add backend directory to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.backtester import backtester

def test_compute_metrics_unit():
    print("[TEST] Running isolated unit test for _compute_metrics...")
    
    # Mock trade dataset with 5 winning, 3 losing, and 2 break-even trades (Total 10 trades)
    sim_res = {
        "balance": 10500.0,
        "trades": [
            {"pnl_usd": 150.0, "r_multiple": 2.0, "lots": 0.1},
            {"pnl_usd": 200.0, "r_multiple": 2.5, "lots": 0.1},
            {"pnl_usd": -50.0, "r_multiple": -1.0, "lots": 0.1},
            {"pnl_usd": 0.0, "r_multiple": 0.0, "lots": 0.1},
            {"pnl_usd": 100.0, "r_multiple": 1.5, "lots": 0.1},
            {"pnl_usd": -80.0, "r_multiple": -1.2, "lots": 0.1},
            {"pnl_usd": 300.0, "r_multiple": 3.0, "lots": 0.1},
            {"pnl_usd": -70.0, "r_multiple": -1.0, "lots": 0.1},
            {"pnl_usd": 0.0, "r_multiple": 0.0, "lots": 0.1},
            {"pnl_usd": 250.0, "r_multiple": 2.5, "lots": 0.1},
        ],
        "equity_curve": [10000.0, 10150.0, 10350.0, 10300.0, 10300.0, 10400.0, 10320.0, 10620.0, 10550.0, 10550.0, 10800.0],
        "max_consecutive_losses": 1
    }
    
    specs = {"pip_value_per_lot": 10.0}
    metrics = backtester._compute_metrics(sim_res, 10000.0, 0.1, 2.5, specs)
    
    print("Metrics Output:", metrics)
    
    assert metrics["total_trades"] == 10, f"Expected 10 total trades, got {metrics['total_trades']}"
    assert metrics["winning_trades"] == 5, f"Expected 5 winning trades, got {metrics['winning_trades']}"
    assert metrics["losing_trades"] == 3, f"Expected 3 losing trades, got {metrics['losing_trades']}"
    assert metrics["break_even_trades"] == 2, f"Expected 2 break-even trades, got {metrics['break_even_trades']}"
    
    # Check equation: winning + losing + break_even == total
    assert metrics["winning_trades"] + metrics["losing_trades"] + metrics["break_even_trades"] == metrics["total_trades"]
    print("[OK] VERIFIED: winning_trades (5) + losing_trades (3) + break_even_trades (2) == total_trades (10)")
    
    # Check win rate calculation: 5 / (5 + 3) * 100 = 5 / 8 * 100 = 62.5%
    expected_win_rate = round((5 / 8) * 100.0, 1) # 62.5%
    assert metrics["win_rate"] == expected_win_rate, f"Expected win_rate {expected_win_rate}%, got {metrics['win_rate']}%"
    print(f"[OK] VERIFIED: win_rate ({metrics['win_rate']}%) == 5 / (5 + 3) * 100 (62.5%)")
    
    # Check zero trades case
    empty_res = {"balance": 10000.0, "trades": [], "equity_curve": [10000.0], "max_consecutive_losses": 0}
    empty_metrics = backtester._compute_metrics(empty_res, 10000.0, 0.1, 2.5, specs)
    assert empty_metrics["total_trades"] == 0
    assert empty_metrics["winning_trades"] == 0
    assert empty_metrics["losing_trades"] == 0
    assert empty_metrics["break_even_trades"] == 0
    assert empty_metrics["win_rate"] == 0.0
    print("[OK] VERIFIED: Zero trades edge case returns 0% win rate and zero for all trade counters.")
    
    print("\n=======================================================")
    print("ALL UNIT TESTS COMPLETED SUCCESSFULLY!")
    print("=======================================================")

if __name__ == "__main__":
    test_compute_metrics_unit()
