import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.adapters.mt5_adapter import mt5_adapter
from app.services.indicators import IndicatorService
from app.services.decision_engine import decision_engine, wyckoff_engine, dark_venus_engine
from app.services.market_structure import MarketStructureEngine
from app.domain.models import TickerData

def profile_breakdown():
    candles = mt5_adapter.fetch_candles("XAUUSD", "M1", count=500)
    full_df = IndicatorService.calculate_all_indicators(candles)
    ticker = TickerData(symbol="XAUUSD", bid=candles[-1].close, ask=candles[-1].close + 0.5, spread_pips=5.0, spread_points=50, point_size=0.01, digits=2, timestamp=candles[-1].timestamp)

    w_slice = candles[-300:]
    df_slice = full_df.iloc[-300:]

    t0 = time.time()
    for _ in range(50):
        structure = MarketStructureEngine.analyze(w_slice, "M1", "XAUUSD", df=df_slice)
    t_struct = time.time() - t0

    t0 = time.time()
    for _ in range(50):
        wyckoff_res = wyckoff_engine.analyze(df_slice, timeframe="M15")
    t_wyckoff = time.time() - t0

    t0 = time.time()
    for _ in range(50):
        setups = dark_venus_engine.evaluate_bb_signals(df_slice, "RANGING", "NEUTRAL")
    t_dv = time.time() - t0

    t0 = time.time()
    for _ in range(50):
        setups_all = decision_engine.evaluate_setups("XAUUSD", w_slice, ticker, "M1", None, df_slice, structure, "RANGING")
    t_setups = time.time() - t0

    print(f"50 calls MarketStructureEngine.analyze: {t_struct:.4f}s ({t_struct/50*1000:.2f}ms/call)")
    print(f"50 calls wyckoff_engine.analyze:        {t_wyckoff:.4f}s ({t_wyckoff/50*1000:.2f}ms/call)")
    print(f"50 calls dark_venus_engine.evaluate_bb:  {t_dv:.4f}s ({t_dv/50*1000:.2f}ms/call)")
    print(f"50 calls decision_engine.evaluate_setups: {t_setups:.4f}s ({t_setups/50*1000:.2f}ms/call)")

if __name__ == "__main__":
    profile_breakdown()
