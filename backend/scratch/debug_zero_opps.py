import sys
from pathlib import Path
from datetime import datetime
from collections import Counter

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.adapters.mt5_adapter import mt5_adapter
from app.services.decision_engine import decision_engine
from app.services.trade_manager import trade_manager
from app.domain.models import TickerData
from app.services.indicators import IndicatorService
from app.services.news_filter import news_filter, MockCalendarProvider

trade_manager.backtest_active = True
news_filter.provider = MockCalendarProvider()

candles = mt5_adapter.fetch_candles("XAUUSD", "M15", count=500, from_datetime=datetime(2026, 9, 27, 0, 0), to_datetime=datetime(2026, 10, 2, 23, 59))
print(f"Total Candles: {len(candles)}", flush=True)
pre_df = IndicatorService.calculate_all_indicators(candles)

setups_counter = Counter()
reasons_counter = Counter()
decisions_counter = Counter()
raw_signals_counter = Counter()

disabled = trade_manager.risk_settings.disabled_setups
print("Active Disabled Setups:", disabled, flush=True)
print("Allowed Sessions:", trade_manager.risk_settings.allowed_sessions, flush=True)
print("HTF Mode:", trade_manager.risk_settings.htf_alignment_mode, flush=True)

for i in range(35, len(candles)):
    c = candles[i]
    slice_candles = candles[max(0, i - 100):i + 1]
    df_slice = pre_df.iloc[max(0, i - 100):i + 1]
    ticker = TickerData(
        symbol="XAUUSD",
        bid=c.close,
        ask=c.close + 0.30,
        spread_pips=3.0,
        point_size=0.01,
        digits=2,
        timestamp=c.timestamp
    )
    report = decision_engine.evaluate_trade_opportunity(
        symbol="XAUUSD",
        candles=slice_candles,
        ticker=ticker,
        timeframe="M15",
        precomputed_df=df_slice
    )
    decisions_counter[report.decision.value] += 1
    setups_counter[report.setup_name] += 1
    for r in report.rejection_reasons:
        reasons_counter[r] += 1

print("\n--- DECISIONS ---", flush=True)
print(dict(decisions_counter), flush=True)

print("\n--- SETUPS FOUND ---", flush=True)
print(dict(setups_counter), flush=True)

print("\n--- TOP REJECTION REASONS ---", flush=True)
for r, count in reasons_counter.most_common(10):
    print(f"  {count}x : {r}", flush=True)
