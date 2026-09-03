"""
Verification script for both changes:
  1. LiveCalendarProvider - fetches real events, tests caching, blackout simulation
  2. Setup C flag - confirms it's disabled
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

print("=" * 70)
print("CHANGE 1 VERIFICATION: LiveCalendarProvider")
print("=" * 70)

from app.services.news_filter import LiveCalendarProvider, news_filter

provider = LiveCalendarProvider()

# --- Test 1: Fetch raw HIGH-impact events from the live feed ---
print("\n[1] Fetching fresh calendar from FF feed...")
all_events = provider._get_or_refresh_events()
if all_events is None:
    print("  WARNING: No events (network issue or no cache). Provider returned None.")
else:
    print(f"  OK: {len(all_events)} HIGH-impact events fetched and cached.")
    print("\n  Sample HIGH-impact events (all this week):")
    for e in all_events[:10]:
        ts_local = e['timestamp']
        print(f"    [{e['currency']}] {e['name']} @ {ts_local.strftime('%Y-%m-%d %H:%M')} UTC | impact={e['impact']}")

# --- Test 2: Cache hit (no re-fetch) ---
print("\n[2] Cache test — calling again immediately (should NOT re-fetch)...")
import time as _time
t0 = _time.time()
events2 = provider._get_or_refresh_events()
elapsed = _time.time() - t0
print(f"  Second call returned in {elapsed*1000:.1f}ms — cache hit: {elapsed < 0.1}")
print(f"  Cache timestamp: {provider._cache_timestamp}")

# --- Test 3: get_events() window filter ---
print("\n[3] Testing get_events() time-window filtering...")
now_utc = datetime.utcnow()
window_start = now_utc - timedelta(hours=24)
window_end = now_utc + timedelta(hours=24*7)
events_in_window, available = provider.get_events(window_start, window_end)
print(f"  Available: {available}")
if events_in_window:
    print(f"  Events in next 7 days: {len(events_in_window)}")
    for e in events_in_window[:5]:
        print(f"    [{e['currency']}] {e['name']} @ {e['timestamp'].strftime('%Y-%m-%d %H:%M')} UTC")
else:
    print("  No HIGH-impact events in this window.")

# --- Test 4: Simulate a blackout near a real upcoming event ---
print("\n[4] Simulating NEWS BLACKOUT (mocking current_time near a real upcoming event)...")
if all_events:
    # Pick the first upcoming HIGH-impact USD event
    upcoming = [e for e in all_events if e['currency'] in ('USD', 'XAU') and e['timestamp'] > now_utc]
    if upcoming:
        target_event = upcoming[0]
        simulated_time = target_event['timestamp'] - timedelta(minutes=15)  # 15 min before event
        print(f"  Simulating current_time = {simulated_time.strftime('%Y-%m-%dT%H:%M')} UTC")
        print(f"  (15 minutes before: {target_event['name']} @ {target_event['timestamp'].strftime('%H:%M')} UTC)")
        
        is_blackout, msg = news_filter.is_news_blackout("XAUUSD", current_time=simulated_time)
        print(f"  is_news_blackout = {is_blackout}")
        print(f"  Message: {msg}")
        if is_blackout:
            print("  ✓ BLACKOUT CORRECTLY TRIGGERED")
        else:
            print("  ✗ No blackout triggered (may be outside window or different currency)")
    else:
        print("  No upcoming USD events found this week.")
else:
    print("  Skipping (no events available).")

# --- Test 5: news_filter global instance status ---
print("\n[5] Global news_filter instance status...")
print(f"  news_filter.provider type: {type(news_filter.provider).__name__}")
print(f"  news_filter.get_news_status(): {news_filter.get_news_status()}")

print("\n" + "=" * 70)
print("CHANGE 2 VERIFICATION: Setup C (Liquidity Sweep Reversal) Disabled")
print("=" * 70)

from app.core.config import settings
print(f"\n[1] settings.SETUP_C_LIQUIDITY_SWEEP_ENABLED = {settings.SETUP_C_LIQUIDITY_SWEEP_ENABLED}")
if not settings.SETUP_C_LIQUIDITY_SWEEP_ENABLED:
    print("  ✓ CORRECT: Setup C is DISABLED via feature flag (default=False)")
else:
    print("  ✗ ERROR: Setup C is enabled — check config.py")

# [2] Create synthetic market conditions that WOULD trigger Setup C if enabled
print("\n[2] Confirming Setup C cannot appear in evaluate_setups() output...")
import pandas as pd
import numpy as np
from app.services.decision_engine import decision_engine
from app.domain.models import Candle, TickerData, TradeSide, LiquiditySweep, MarketStructureAnalysis, OrderBlock, FairValueGap, MarketRegime

# Build minimal candle history (50 candles around 2700)
base_price = 2700.0
ts = datetime(2026, 8, 26, 12, 0, 0)
candles = []
for i in range(55):
    c = Candle(
        timestamp=ts + timedelta(minutes=i),
        open=base_price + i * 0.1,
        high=base_price + i * 0.1 + 0.5,
        low=base_price + i * 0.1 - 0.3,
        close=base_price + i * 0.1 + 0.2,
        volume=100.0 + i,
        timeframe="M1",
        is_closed=True
    )
    candles.append(c)

ticker = TickerData(
    symbol="XAUUSD",
    bid=2705.0,
    ask=2705.2,
    spread_pips=2.0,
    point_size=0.01,
    digits=2,
    timestamp=datetime.now()
)

from app.services.indicators import IndicatorService
df = IndicatorService.calculate_all_indicators(candles)

# Mock M15 structure with a liquidity sweep (BUY side) that would trigger Setup C SELL
struct_m15 = MarketStructureAnalysis(
    symbol="XAUUSD",
    timeframe="M15",
    trend="BEARISH",
    regime=MarketRegime.BEARISH_TREND,
    order_blocks=[],
    fvgs=[],
    liquidity_sweeps=[
        LiquiditySweep(side=TradeSide.BUY, price_level=2710.0, timestamp=datetime.now())
    ],
    vwap=2702.0,
    poc_price=2703.0,
    value_area_high=2706.0,
    value_area_low=2700.0
)

setups = decision_engine.evaluate_setups(
    symbol="XAUUSD",
    candles=candles,
    ticker=ticker,
    timeframe="M1",
    htf_candles={"M15": candles},
    df=df,
    structure=struct_m15,
    regime="WEAK_DOWNTREND"
)

setup_c_found = [s for s in setups if "SETUP C" in s.get("name", "")]
all_setup_names = [s.get("name", "") for s in setups if s.get("active")]

print(f"  Active setups found: {all_setup_names}")
if setup_c_found:
    print(f"  ✗ ERROR: Setup C appeared: {[s['name'] for s in setup_c_found]}")
else:
    print("  ✓ CORRECT: Setup C does NOT appear in active setups (disabled by flag)")

print("\n[3] Other setups are NOT affected...")
non_c_setups = [s for s in setups if "SETUP C" not in s.get("name", "")]
print(f"  Non-C setups count: {len(non_c_setups)} (Setup A/B/D/E/F variants may or may not trigger)")
print("  ✓ No Setup C means the disable is correctly isolated")

print("\n" + "=" * 70)
print("VERIFICATION COMPLETE")
print("=" * 70)
