import sys
from pathlib import Path
from collections import Counter

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.adapters.mt5_adapter import mt5_adapter
from app.services.decision_engine import decision_engine
from app.services.trade_manager import trade_manager
from app.domain.models import TickerData, TradeSide
from app.services.indicators import IndicatorService

mt5_adapter.initialize()
gold_sym = mt5_adapter.discover_gold_symbol()
candles = mt5_adapter.fetch_candles(gold_sym, timeframe="M15", count=200)

print(f"Loaded {len(candles)} candles for {gold_sym}")

cfg = trade_manager.risk_settings
print("disabled_setups:", cfg.disabled_setups)
print("allowed_sessions:", cfg.allowed_sessions)
print("htf_alignment_mode:", cfg.htf_alignment_mode)

reasons_counter = Counter()
setup_counter = Counter()
decisions_counter = Counter()

# Let's inspect last 50 candles
for i in range(35, len(candles)):
    c = candles[i]
    slice_candles = candles[max(0, i - 100):i + 1]
    ticker = TickerData(
        symbol=gold_sym,
        bid=c.close,
        ask=c.close + 0.30,
        spread_pips=3.0,
        point_size=0.01,
        digits=2,
        timestamp=c.timestamp
    )
    report = decision_engine.evaluate_trade_opportunity(
        symbol=gold_sym,
        candles=slice_candles,
        ticker=ticker,
        timeframe="M15"
    )
    decisions_counter[report.decision.value] += 1
    setup_counter[report.setup_name] += 1
    for r in report.rejection_reasons:
        reasons_counter[r] += 1

print("\n--- DECISIONS ---")
print(dict(decisions_counter))

print("\n--- SETUPS FOUND ---")
print(dict(setup_counter))

print("\n--- TOP REJECTION REASONS ---")
for r, count in reasons_counter.most_common(10):
    print(f"  {count}x : {r}")
