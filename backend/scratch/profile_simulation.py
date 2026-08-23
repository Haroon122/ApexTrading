import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.adapters.mt5_adapter import mt5_adapter
from app.services.indicators import IndicatorService
from app.services.decision_engine import decision_engine
from app.domain.models import TickerData
from datetime import datetime

def profile_components():
    print("Fetching 1000 M1 candles...")
    candles = mt5_adapter.fetch_candles("XAUUSD", "M1", count=1000)
    print(f"Loaded {len(candles)} candles.")

    # 1. Precalculate full_df
    t0 = time.time()
    full_df = IndicatorService.calculate_all_indicators(candles)
    t_full_df = time.time() - t0
    print(f"1. Precalculate full_df on 1000 candles: {t_full_df:.4f}s")

    # 2. Benchmark 500 loop iterations of evaluate_trade_opportunity
    t_eval_start = time.time()
    ticker = TickerData(symbol="XAUUSD", bid=candles[-1].close, ask=candles[-1].close + 0.5, spread_pips=5.0, spread_points=50, point_size=0.01, digits=2, timestamp=candles[-1].timestamp)
    for i in range(500):
        w_slice = candles[max(0, i + 1 - 300):i + 1]
        df_slice = full_df.iloc[max(0, i + 1 - 300):i + 1]
        rep = decision_engine.evaluate_trade_opportunity("XAUUSD", w_slice, ticker, "M1", precomputed_df=df_slice)
    t_eval = time.time() - t_eval_start
    print(f"2. 500 iterations of evaluate_trade_opportunity: {t_eval:.4f}s ({t_eval/500*1000:.3f}ms per bar)")

if __name__ == "__main__":
    profile_components()
