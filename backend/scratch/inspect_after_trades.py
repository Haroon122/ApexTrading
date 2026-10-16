import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.services.backtester import BacktestingEngine
from app.adapters.mt5_adapter import mt5_adapter
from app.services.trade_manager import trade_manager
from app.services.indicators import IndicatorService

candles = mt5_adapter.fetch_candles("XAUUSD", "M15", 1000)
pre_df = IndicatorService.calculate_all_indicators(candles)

res = BacktestingEngine.run_backtest(
    symbol="XAUUSD",
    timeframe="M15",
    candles=candles,
    initial_balance=10000.0,
    risk_pct=1.0,
    lot_sizing_mode="FIXED",
    user_configured_lot=0.10,
    break_even_rr=1.0,
    trailing_activation_rr=1.2,
    giveback_guard_enabled=False,
    htf_alignment_filter_enabled=True,
    htf_alignment_mode="STRICT",
    disabled_setups=trade_manager.risk_settings.disabled_setups,
    precomputed_full_df=pre_df,
    skip_stress=True
)

print(f"M15 Trades Count: {len(res.get('trades', []))}")
for i, t in enumerate(res.get("trades", [])):
    print(f"[{i+1}] Setup: {t.get('setup_name')} | Side: {t.get('side')} | PnL: ${t.get('pnl_usd', 0):+.2f} | Entry: {t.get('entry_price')} | Exit: {t.get('exit_price')} | Reason: {t.get('exit_reason')}")
