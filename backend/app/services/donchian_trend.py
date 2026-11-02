"""
Donchian Trend strategy for XAUUSD. Runs on ONE timeframe at a time, chosen by the
user (RiskSettings.donchian_timeframe): H1 (100-candle channel) or M15 (400-candle
channel, the same ~100-hour window on a finer chart).

Rules (same as the research backtest, Oct 2026, 2018-2026 H1 data):
  Entry : a CLOSED H1 candle closes above the highest high of the previous
          100 candles -> BUY, or below the lowest low -> SELL.
          Only the first close beyond the channel counts (a fresh breakout).
  Stop  : 3 x ATR(14) from entry.
  Exit  : chandelier trailing stop = last closed H1 close -/+ 4 x ATR(14).
          It only moves in the trade's favour. No fixed TP (a far TP is set
          only so broker-side R:R checks pass).

  Filter: trade only in the direction of the daily trend - BUY only if the last
          COMPLETED daily close is above its 200-day SMA, SELL only if below.

Backtest results, 2015-2026 H1 (spread + slippage + swap included):
  without filter: 2015-20 PF 1.12, 2021-26 PF 1.19, total +55R
  with filter   : 2015-20 PF 1.76, 2021-26 PF 1.24, total +82R, ~32 trades/year
  The filter was PF > 1 in both periods for all 15 tested settings (N 70-150, SMA 50-250).

M15 N400 + filter, 2015-2026: 2015-20 PF 1.51, 2021-26 PF 1.25, total +132R, ~62 trades/yr,
  max drawdown 39.5R (H1: 27R). More sensitive to spread: PF ~1.0 at 3x costs (H1: 1.46/1.17).
"""
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from app.domain.models import (
    Candle, ConfirmationBreakdown, TickerData, TradeDecisionReport, TradeSide,
)

logger = logging.getLogger("ApexTrader.DonchianTrend")

STRATEGY_PROFILE = "DONCHIAN_TREND"
SETUP_NAME = "DONCHIAN TREND: H1 100-bar Breakout"
TIMEFRAME = "H1"
CHANNEL = 100
ATR_PERIOD = 14
STOP_ATR = 3.0
TRAIL_ATR = 4.0
FAR_TP_R = 10.0
CANDLES_NEEDED = 300
# Per-timeframe settings. Both use the same stop/trail multiples and daily filter.
VARIANTS = {
    "H1": {"channel": 100, "candles": 300, "minutes": 60},
    "M15": {"channel": 400, "candles": 600, "minutes": 15},
}
TREND_SMA = 200
DAILY_NEEDED = TREND_SMA + 10


def variant(tf: Optional[str]) -> Tuple[str, dict]:
    """Normalised (timeframe, config); anything unknown falls back to H1."""
    tf = (tf or TIMEFRAME).upper()
    return (tf, VARIANTS[tf]) if tf in VARIANTS else (TIMEFRAME, VARIANTS[TIMEFRAME])


def active_timeframe(risk_settings) -> str:
    return variant(getattr(risk_settings, "donchian_timeframe", TIMEFRAME))[0]


def setup_name(tf: str) -> str:
    return f"DONCHIAN TREND: {tf} {VARIANTS[tf]['channel']}-bar Breakout"


def closed_only(candles: List[Candle]) -> List[Candle]:
    if candles and not getattr(candles[-1], "is_closed", True):
        return candles[:-1]
    return candles


def atr_series(candles: List[Candle], period: int = ATR_PERIOD) -> List[float]:
    """Wilder-style ATR (EMA with alpha = 1/period) over true range."""
    out, prev_atr, prev_close = [], None, None
    alpha = 1.0 / period
    for c in candles:
        tr = c.high - c.low if prev_close is None else max(
            c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        prev_atr = tr if prev_atr is None else prev_atr + alpha * (tr - prev_atr)
        out.append(prev_atr)
        prev_close = c.close
    return out


def breakout_signal(candles: List[Candle], channel: int = CHANNEL) -> Tuple[int, str]:
    """+1 / -1 / 0 for the LAST candle in the list (must be closed candles)."""
    if len(candles) < channel + 2:
        return 0, f"Need {channel + 2} closed candles, have {len(candles)}"
    last, prev = candles[-1], candles[-2]
    hi = max(c.high for c in candles[-channel - 1:-1])
    lo = min(c.low for c in candles[-channel - 1:-1])
    prev_hi = max(c.high for c in candles[-channel - 2:-2])
    prev_lo = min(c.low for c in candles[-channel - 2:-2])
    if last.close > hi and prev.close <= prev_hi:
        return 1, f"Close {last.close:.2f} broke {channel}-bar high {hi:.2f}"
    if last.close < lo and prev.close >= prev_lo:
        return -1, f"Close {last.close:.2f} broke {channel}-bar low {lo:.2f}"
    return 0, f"No breakout (channel {lo:.2f} - {hi:.2f}, last close {last.close:.2f})"


def daily_trend(daily: Optional[List[Candle]], sma: int = TREND_SMA) -> Tuple[int, str]:
    """+1 if the last completed daily close is above its SMA, -1 if below, 0 if unknown."""
    days = closed_only(daily or [])
    if len(days) < sma:
        return 0, f"Daily trend unknown ({len(days)}/{sma} daily candles)"
    avg = sum(c.close for c in days[-sma:]) / sma
    last = days[-1].close
    if last > avg:
        return 1, f"Daily trend UP (close {last:.2f} > {sma}-day avg {avg:.2f})"
    if last < avg:
        return -1, f"Daily trend DOWN (close {last:.2f} < {sma}-day avg {avg:.2f})"
    return 0, "Daily trend flat"


def evaluate(symbol: str, candles: List[Candle], ticker: Optional[TickerData],
             max_spread_price: Optional[float] = None,
             daily: Optional[List[Candle]] = None, tf: str = TIMEFRAME) -> TradeDecisionReport:
    """Build a TradeDecisionReport the existing pipeline can execute."""
    tf, cfg = variant(tf)
    name = setup_name(tf)
    bars = closed_only(candles or [])
    reasoning: List[str] = [f"Strategy: {name}"]
    rejections: List[str] = []
    signal, why = breakout_signal(bars, cfg["channel"])
    reasoning.append(why)
    trend, trend_why = daily_trend(daily)
    reasoning.append(trend_why)
    if signal != 0 and trend != signal:
        side = "BUY" if signal == 1 else "SELL"
        why = f"{side} breakout skipped: against the daily trend. {trend_why}"
        reasoning.append(why)
        rejections += ["TREND_FILTER", why]
        signal = 0

    decision = TradeSide.HOLD
    entry = sl = tp = 0.0
    rr = 0.0
    if signal != 0 and ticker is not None:
        atr = atr_series(bars)[-1]
        spread = (ticker.ask - ticker.bid) if ticker.ask and ticker.bid else 0.0
        if max_spread_price is not None and spread > max_spread_price:
            rejections += ["SPREAD_BLOCKED", f"Spread {spread:.2f} > max {max_spread_price:.2f}"]
        elif atr <= 0:
            rejections += ["DATA_UNAVAILABLE", "ATR is zero"]
        else:
            decision = TradeSide.BUY if signal == 1 else TradeSide.SELL
            entry = ticker.ask if signal == 1 else ticker.bid
            risk = STOP_ATR * atr
            sl = entry - signal * risk
            tp = entry + signal * FAR_TP_R * risk
            rr = FAR_TP_R
            reasoning.append(f"ATR(14) {atr:.2f} -> stop {risk:.2f} ({STOP_ATR}xATR), trailing {TRAIL_ATR}xATR")
    elif signal == 0 and not rejections:
        rejections += ["NO_SIGNAL", why]

    return TradeDecisionReport(
        symbol=symbol, decision=decision, timeframe=tf,
        confidence=1.0 if decision != TradeSide.HOLD else 0.0,
        confirmation_score=1.0 if decision != TradeSide.HOLD else 0.0,
        reasoning=reasoning, rejection_reasons=rejections,
        suggested_entry=round(entry, 2), suggested_sl=round(sl, 2), suggested_tp=round(tp, 2),
        suggested_lots=0.0, risk_reward_ratio=rr,
        breakdown=ConfirmationBreakdown(passed=decision != TradeSide.HOLD),
        setup_name=name if decision != TradeSide.HOLD else "None",
        summary_explanation=why, timestamp=datetime.now(),
    )


def trailing_stop(candles: List[Candle], is_buy: bool, current_sl: float) -> Optional[float]:
    """New chandelier stop if it improves on current_sl, else None."""
    bars = closed_only(candles or [])
    if len(bars) < ATR_PERIOD + 2:
        return None
    atr = atr_series(bars)[-1]
    close = bars[-1].close
    cand = close - TRAIL_ATR * atr if is_buy else close + TRAIL_ATR * atr
    if is_buy and cand > current_sl:
        return cand
    if not is_buy and (current_sl <= 0 or cand < current_sl):
        return cand
    return None


def status(symbol: str, candles: List[Candle], ticker: Optional[TickerData],
           equity: float, risk_pct: float, max_lot: float, positions: list,
           daily: Optional[List[Candle]] = None, tf: str = TIMEFRAME) -> dict:
    """Snapshot for the dashboard: where price is vs the channel, next-trade plan,
    and the trailing stop of open positions."""
    tf, cfg = variant(tf)
    channel = cfg["channel"]
    bars = closed_only(candles or [])
    out = {"ok": False, "symbol": symbol, "timeframe": tf, "channel_bars": channel}
    if len(bars) < channel + 2:
        out["message"] = f"Waiting for data ({len(bars)}/{channel + 2} closed {tf} candles)"
        return out
    hi = max(c.high for c in bars[-channel:])
    lo = min(c.low for c in bars[-channel:])
    atr = atr_series(bars)[-1]
    last_close = bars[-1].close
    bid = ticker.bid if ticker else last_close
    ask = ticker.ask if ticker else last_close
    price = (bid + ask) / 2
    stop = STOP_ATR * atr
    # $ per 1.00 lot for a 1-dollar move on gold (100 oz contract)
    loss_per_lot = stop * 100.0
    raw_lot = (equity * risk_pct / 100.0) / loss_per_lot if loss_per_lot > 0 else 0.0
    lot = max(0.01, min(max_lot, int(raw_lot * 100) / 100.0)) if equity > 0 else 0.01
    risk_usd = lot * loss_per_lot
    sig, why = breakout_signal(bars, channel)
    trend, trend_why = daily_trend(daily)
    if sig != 0 and trend != sig:
        why = f"Breakout skipped (against daily trend). {why}"
        sig = 0
    trails = []
    for p in positions or []:
        if getattr(p, "symbol", "") != symbol:
            continue
        is_buy = getattr(p.side, "value", str(p.side)) == "BUY"
        cand = last_close - TRAIL_ATR * atr if is_buy else last_close + TRAIL_ATR * atr
        locked = ((p.sl_price - p.entry_price) if is_buy else (p.entry_price - p.sl_price)) * p.lots * 100.0 if p.sl_price else None
        trails.append({"ticket": p.trade_id, "side": "BUY" if is_buy else "SELL", "lots": p.lots,
                       "entry": p.entry_price, "sl": p.sl_price, "next_trail": round(cand, 2),
                       "pnl_usd": getattr(p, "pnl_usd", 0.0),
                       "locked_usd": round(locked, 2) if locked is not None else None})
    out.update({
        "ok": True, "price": round(price, 2), "bid": bid, "ask": ask,
        "channel_high": round(hi, 2), "channel_low": round(lo, 2), "last_close": round(last_close, 2),
        "to_buy": round(hi - last_close, 2), "to_sell": round(last_close - lo, 2),
        "position_pct": round((last_close - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0,
        "atr": round(atr, 2), "stop_distance": round(stop, 2), "trail_distance": round(TRAIL_ATR * atr, 2),
        "next_lot": lot, "next_risk_usd": round(risk_usd, 2),
        "next_risk_pct": round(risk_usd / equity * 100, 2) if equity > 0 else None,
        "lot_capped": raw_lot > max_lot, "signal": {1: "BUY", -1: "SELL"}.get(sig, "NONE"), "signal_text": why,
        "last_candle_time": bars[-1].timestamp.isoformat(), "positions": trails,
        "daily_trend": {1: "UP", -1: "DOWN"}.get(trend, "UNKNOWN"), "daily_trend_text": trend_why,
    })
    # Candle timestamps are MT5 server time read as UTC epoch; convert back so the
    # dashboard can show the broker clock instead of a shifted local time.
    from datetime import timezone, timedelta
    srv_open = datetime.fromtimestamp(bars[-1].timestamp.timestamp(), tz=timezone.utc).replace(tzinfo=None)
    step = timedelta(minutes=cfg["minutes"])
    out["last_candle_server"] = f"{srv_open:%H:%M}-{(srv_open + step):%H:%M}"
    out["next_check_server"] = f"{(srv_open + 2 * step):%H:%M}"
    # Tick timestamps are in broker time, so measure staleness on our own clock:
    # how long since the last tick time CHANGED.
    tick_age = None
    if ticker is not None and getattr(ticker, "timestamp", None):
        seen = _last_tick.get(symbol)
        if seen is None or seen[0] != ticker.timestamp:
            _last_tick[symbol] = (ticker.timestamp, datetime.now())
            tick_age = 0.0
        else:
            tick_age = (datetime.now() - seen[1]).total_seconds()
    out["tick_age_sec"] = round(tick_age) if tick_age is not None else None
    out["market_open"] = tick_age is not None and tick_age < 180
    return out


_last_tick: dict = {}


def fetch_daily(adapter, symbol: str) -> List[Candle]:
    return adapter.fetch_candles(symbol, "D1", DAILY_NEEDED) or []


def fetch_bars(adapter, symbol: str, tf: str) -> List[Candle]:
    tf, cfg = variant(tf)
    return adapter.fetch_candles(symbol, tf, cfg["candles"]) or []
