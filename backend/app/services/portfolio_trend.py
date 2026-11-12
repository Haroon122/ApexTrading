"""
PORTFOLIO_TREND: three backtested XAUUSD trend strategies running side by side.
The TIMEFRAME of each strategy is a user setting (Risk Settings); every timeframe uses the parameters
that tested best on that timeframe (research/supertrend_bt.py, pullback_bt.py, README Round 5).

  DONCHIAN   (H1 or M15): channel breakout + daily-trend filter, stop 3xATR, chandelier trail 4xATR.
                          Implemented in donchian_trend.py; this module only wires it in.
  SUPERTREND (H1/H4/M15): Supertrend flip in the direction of the EMA200 of the same chart; NO trailing
                          stop - the trade is closed when Supertrend flips against it.
                            H4: ST(10, 2) stop 3xATR | H1: ST(14, 3) stop 3xATR | M15: ST(14, 3) stop 2xATR
  PULLBACK   (H1/H4/M15): in a daily trend (last completed daily close vs 200-day SMA) with EMA20 on the
                          trend side of EMA50, a candle that touches EMA20 and closes back on the trend
                          side -> entry.  H4: stop 2xATR, trail 3xATR | H1 and M15: stop 3xATR, trail 4xATR

Backtests, 2016-2026 (M15: Aug 2022 on), spread + slippage + swap included (max drawdown in R):
  Supertrend  H4 PF 1.39 DD 9R   | H1 PF 1.20 DD 32R  | M15 PF 1.16 DD 84R
  Pullback    H4 PF 1.45 DD 13R  | H1 PF 1.19 DD 34R  | M15 PF 1.28 DD 59R (PF 1.10 / DD 96R at 3x costs)
  Merged H1 Donchian + H4 Supertrend + H4 Pullback: PF 1.40, +185R, DD 41R, ~96 trades/yr.
H1 and especially M15 trade more but with larger drawdowns and thinner margins after costs.

All signals use CLOSED candles only (same as the backtest: signal on the closed bar, entry at the
next open = a market order right after the close). Each strategy has at most one open trade.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.domain.models import (
    Candle, ConfirmationBreakdown, TickerData, TradeDecisionReport, TradeSide,
)
from app.services import donchian_trend
from app.services.donchian_trend import ATR_PERIOD, atr_series, closed_only

logger = logging.getLogger("ApexTrader.PortfolioTrend")

PROFILE = "PORTFOLIO_TREND"
KEYS = ("DONCHIAN", "SUPERTREND", "PULLBACK")
FAR_TP_R = 10.0           # far take-profit so broker-side R:R checks pass; the real exit is the trail / flip

SUPPORTED_TFS: Dict[str, Tuple[str, ...]] = {
    "DONCHIAN": ("H1", "M15"),
    "SUPERTREND": ("H1", "H4", "M15"),
    "PULLBACK": ("H1", "H4", "M15"),
}
DEFAULT_TFS = {"DONCHIAN": "H1", "SUPERTREND": "H4", "PULLBACK": "H4"}
TF_MINUTES = {"M15": 15, "H1": 60, "H4": 240}
SETTING_FIELD = {"DONCHIAN": "portfolio_donchian_tf", "SUPERTREND": "portfolio_supertrend_tf",
                 "PULLBACK": "portfolio_pullback_tf"}

# Parameters that tested best on each timeframe
ST_EMA = 200
ST_PROFILES = {"H4": {"period": 10, "mult": 2.0, "stop_atr": 3.0},
               "H1": {"period": 14, "mult": 3.0, "stop_atr": 3.0},
               "M15": {"period": 14, "mult": 3.0, "stop_atr": 2.0}}
ST_MIN_BARS, ST_FETCH_BARS = 400, 800
PB_EMA_FAST, PB_EMA_SLOW, PB_TREND_SMA = 20, 50, 200
PB_PROFILES = {"H4": {"stop_atr": 2.0, "trail_atr": 3.0},
               "H1": {"stop_atr": 3.0, "trail_atr": 4.0},
               "M15": {"stop_atr": 3.0, "trail_atr": 4.0}}
PB_MIN_BARS, PB_FETCH_BARS = 150, 400

TAG_ABBR = {"DONCHIAN": "DON", "SUPERTREND": "ST", "PULLBACK": "PB"}
_ABBR_KEY = {v: k for k, v in TAG_ABBR.items()}
_TAG_RE = re.compile(r"^APEX:(DON|ST|PB)-(M15|H1|H4)$")
_TF_RE = re.compile(r"\b(M15|H1|H4)\b")

LABELS = {"DONCHIAN": "Donchian Trend", "SUPERTREND": "Supertrend + EMA200", "PULLBACK": "Trend Pullback (EMA20)"}
PREFIX = {"DONCHIAN": "DONCHIAN TREND", "SUPERTREND": "SUPERTREND", "PULLBACK": "PULLBACK"}


# ── timeframe handling ────────────────────────────────────────────────────

def tf_of(key: str, rs=None) -> str:
    """Timeframe the user chose for this strategy (falls back to the default when missing/unsupported)."""
    val = str(getattr(rs, SETTING_FIELD[key], "") or "").upper() if rs is not None else ""
    return val if val in SUPPORTED_TFS[key] else DEFAULT_TFS[key]


def validate_timeframes(rs) -> Optional[str]:
    """None if every enabled strategy has a supported timeframe, else the reason."""
    for k in enabled_keys(rs):
        val = str(getattr(rs, SETTING_FIELD[k], "") or "").upper()
        if val not in SUPPORTED_TFS[k]:
            return f"{LABELS[k]} cannot run on '{val or '?'}'. Allowed: {', '.join(SUPPORTED_TFS[k])}."
    return None


def params(key: str, tf: str) -> Dict[str, Any]:
    """stop / trail / exit description of a strategy on a timeframe."""
    if key == "DONCHIAN":
        return {"stop_atr": donchian_trend.STOP_ATR, "trail_atr": donchian_trend.TRAIL_ATR,
                "exit": f"Trailing stop {donchian_trend.TRAIL_ATR:g}xATR"}
    if key == "SUPERTREND":
        p = ST_PROFILES[tf]
        return {"stop_atr": p["stop_atr"], "trail_atr": None, "exit": "Closes when Supertrend flips",
                "period": p["period"], "mult": p["mult"]}
    p = PB_PROFILES[tf]
    return {"stop_atr": p["stop_atr"], "trail_atr": p["trail_atr"], "exit": f"Trailing stop {p['trail_atr']:g}xATR"}


def setup_st(tf: str) -> str:
    p = ST_PROFILES[tf]
    return f"SUPERTREND {tf}: ST({p['period']},{p['mult']:g}) flip + EMA{ST_EMA}"


def setup_pb(tf: str) -> str:
    return f"PULLBACK {tf}: EMA{PB_EMA_FAST} pullback in daily trend"


def setup_for(key: str, tf: str) -> str:
    if key == "DONCHIAN":
        return donchian_trend.setup_name(tf)
    return setup_st(tf) if key == "SUPERTREND" else setup_pb(tf)


# ── identification helpers ────────────────────────────────────────────────

def strategy_of(setup_name: Optional[str]) -> Optional[str]:
    """Strategy key for a trade's setup_name, or None (legacy / manual / unknown)."""
    s = (setup_name or "").upper()
    for key, prefix in PREFIX.items():
        if s.startswith(prefix):
            return key
    return None


def timeframe_from_setup(setup_name: Optional[str]) -> Optional[str]:
    m = _TF_RE.search((setup_name or "").upper())
    return m.group(1) if m else None


def trade_timeframe(key: str, trade, rs=None) -> str:
    """Timeframe a trade was opened on (kept in the trade / its setup name), so changing the setting
    later never changes how an already-open trade is managed."""
    for cand in (str(getattr(trade, "timeframe", "") or "").upper(), timeframe_from_setup(getattr(trade, "setup_name", ""))):
        if cand in SUPPORTED_TFS[key]:
            return cand
    return tf_of(key, rs)


def tag_for_setup(setup_name: Optional[str]) -> Optional[str]:
    """Short MT5 order comment (<= 31 chars) so a position can be re-identified after a restart."""
    key = strategy_of(setup_name)
    tf = timeframe_from_setup(setup_name)
    return f"APEX:{TAG_ABBR[key]}-{tf}" if key and tf in TF_MINUTES else None


def setup_for_tag(comment: Optional[str]) -> Optional[str]:
    m = _TAG_RE.match((comment or "").strip())
    if not m:
        return None
    key, tf = _ABBR_KEY[m.group(1)], m.group(2)
    return setup_for(key, tf) if tf in SUPPORTED_TFS[key] else None


def enabled_keys(rs) -> List[str]:
    flags = {"DONCHIAN": getattr(rs, "portfolio_donchian_enabled", True),
             "SUPERTREND": getattr(rs, "portfolio_supertrend_enabled", True),
             "PULLBACK": getattr(rs, "portfolio_pullback_enabled", True)}
    return [k for k in KEYS if flags[k]]


def is_active(rs) -> bool:
    return getattr(rs, "strategy_profile", "") == PROFILE


# ── indicators (pure python, same math as the research engine / pandas ewm adjust=False) ─────

def ema_series(values: List[float], span: int) -> List[float]:
    alpha = 2.0 / (span + 1.0)
    out: List[float] = []
    prev = None
    for v in values:
        prev = v if prev is None else prev + alpha * (v - prev)
        out.append(prev)
    return out


def supertrend(bars: List[Candle], period: int = 10, mult: float = 2.0) -> Tuple[List[int], List[float]]:
    """(direction per bar: +1 bullish / -1 bearish, active Supertrend line per bar)."""
    n = len(bars)
    if n == 0:
        return [], []
    atr = atr_series(bars, period)
    final_up = [0.0] * n
    final_lo = [0.0] * n
    dirs = [1] * n
    line = [0.0] * n
    for i, c in enumerate(bars):
        mid = (c.high + c.low) / 2.0
        ub = mid + mult * atr[i]
        lb = mid - mult * atr[i]
        if i == 0:
            final_up[i], final_lo[i] = ub, lb
            line[i] = lb
            continue
        pc = bars[i - 1].close
        final_up[i] = ub if (ub < final_up[i - 1] or pc > final_up[i - 1]) else final_up[i - 1]
        final_lo[i] = lb if (lb > final_lo[i - 1] or pc < final_lo[i - 1]) else final_lo[i - 1]
        if dirs[i - 1] == 1:
            dirs[i] = -1 if c.close < final_lo[i] else 1
        else:
            dirs[i] = 1 if c.close > final_up[i] else -1
        line[i] = final_lo[i] if dirs[i] == 1 else final_up[i]
    return dirs, line


# ── signals (each looks at the LAST bar of a list of CLOSED bars) ─────────────

def supertrend_signal(bars: List[Candle], tf: str = "H4") -> Tuple[int, str, dict]:
    info: dict = {}
    if len(bars) < ST_MIN_BARS:
        return 0, f"Need {ST_MIN_BARS} closed {tf} candles, have {len(bars)}", info
    p = ST_PROFILES[tf]
    dirs, line = supertrend(bars, p["period"], p["mult"])
    ema = ema_series([c.close for c in bars], ST_EMA)[-1]
    close = bars[-1].close
    info = {"st_dir": dirs[-1], "st_line": line[-1], "ema": ema, "close": close}
    flipped_up = dirs[-1] == 1 and dirs[-2] == -1
    flipped_dn = dirs[-1] == -1 and dirs[-2] == 1
    if flipped_up and close > ema:
        return 1, f"Supertrend flipped UP and close {close:.2f} > EMA{ST_EMA} {ema:.2f}", info
    if flipped_dn and close < ema:
        return -1, f"Supertrend flipped DOWN and close {close:.2f} < EMA{ST_EMA} {ema:.2f}", info
    if flipped_up or flipped_dn:
        side = "UP" if flipped_up else "DOWN"
        return 0, f"Supertrend flipped {side} but against EMA{ST_EMA} ({close:.2f} vs {ema:.2f}) - skipped", info
    return 0, (f"No Supertrend flip (trend {'UP' if dirs[-1] == 1 else 'DOWN'}, line {line[-1]:.2f}, "
               f"EMA{ST_EMA} {ema:.2f})"), info


def pullback_signal(bars: List[Candle], daily: Optional[List[Candle]], tf: str = "H4") -> Tuple[int, str, dict]:
    info: dict = {}
    if len(bars) < PB_MIN_BARS:
        return 0, f"Need {PB_MIN_BARS} closed {tf} candles, have {len(bars)}", info
    trend, trend_why = donchian_trend.daily_trend(daily, PB_TREND_SMA)
    closes = [c.close for c in bars]
    fast = ema_series(closes, PB_EMA_FAST)[-1]
    slow = ema_series(closes, PB_EMA_SLOW)[-1]
    last = bars[-1]
    info = {"daily_trend": trend, "daily_trend_text": trend_why, "ema_fast": fast, "ema_slow": slow, "close": last.close}
    if trend == 0:
        return 0, trend_why, info
    if trend == 1 and fast > slow and last.low <= fast and last.close > fast and last.close > last.open:
        return 1, (f"Pullback to EMA{PB_EMA_FAST} {fast:.2f} (low {last.low:.2f}) held; bullish close {last.close:.2f} "
                   f"in an UP daily trend"), info
    if trend == -1 and fast < slow and last.high >= fast and last.close < fast and last.close < last.open:
        return -1, (f"Pullback to EMA{PB_EMA_FAST} {fast:.2f} (high {last.high:.2f}) rejected; bearish close {last.close:.2f} "
                    f"in a DOWN daily trend"), info
    aligned = (fast > slow) if trend == 1 else (fast < slow)
    if not aligned:
        return 0, f"Daily trend {'UP' if trend == 1 else 'DOWN'} but EMA{PB_EMA_FAST}/EMA{PB_EMA_SLOW} not aligned with it", info
    return 0, (f"Daily trend {'UP' if trend == 1 else 'DOWN'}; waiting for a pullback to EMA{PB_EMA_FAST} {fast:.2f} "
               f"(last close {last.close:.2f})"), info


# ── report building ───────────────────────────────────────────────────────

def _hold_report(symbol: str, tf: str, reasoning: List[str], rejections: List[str], why: str) -> TradeDecisionReport:
    return TradeDecisionReport(
        symbol=symbol, decision=TradeSide.HOLD, timeframe=tf, confidence=0.0, confirmation_score=0.0,
        reasoning=reasoning, rejection_reasons=rejections, suggested_entry=0.0, suggested_sl=0.0,
        suggested_tp=0.0, suggested_lots=0.0, risk_reward_ratio=0.0, breakdown=ConfirmationBreakdown(passed=False),
        setup_name="None", summary_explanation=why, timestamp=datetime.now(),
    )


def _entry_report(symbol: str, tf: str, signal: int, bars: List[Candle], ticker: Optional[TickerData],
                  max_spread_price: Optional[float], stop_atr: float, setup: str,
                  reasoning: List[str], why: str) -> TradeDecisionReport:
    rejections: List[str] = []
    atr = atr_series(bars)[-1]
    spread = (ticker.ask - ticker.bid) if (ticker is not None and ticker.ask and ticker.bid) else 0.0
    if ticker is None:
        rejections += ["DATA_UNAVAILABLE", "No live price"]
    elif max_spread_price is not None and spread > max_spread_price:
        rejections += ["SPREAD_BLOCKED", f"Spread {spread:.2f} > max {max_spread_price:.2f}"]
    elif atr <= 0:
        rejections += ["DATA_UNAVAILABLE", "ATR is zero"]
    if rejections:
        rep = _hold_report(symbol, tf, reasoning, rejections, why)
        rep.signal_candle_ts = bars[-1].timestamp
        return rep
    entry = ticker.ask if signal == 1 else ticker.bid
    risk = stop_atr * atr
    reasoning = reasoning + [f"ATR(14) {atr:.2f} -> stop {risk:.2f} ({stop_atr:g}xATR)"]
    return TradeDecisionReport(
        symbol=symbol, decision=TradeSide.BUY if signal == 1 else TradeSide.SELL, timeframe=tf,
        confidence=1.0, confirmation_score=1.0, reasoning=reasoning, rejection_reasons=[],
        suggested_entry=round(entry, 2), suggested_sl=round(entry - signal * risk, 2),
        suggested_tp=round(entry + signal * FAR_TP_R * risk, 2), suggested_lots=0.0,
        risk_reward_ratio=FAR_TP_R, breakdown=ConfirmationBreakdown(passed=True),
        setup_name=setup, summary_explanation=why, timestamp=datetime.now(),
        signal_candle_ts=bars[-1].timestamp,
    )


def evaluate(key: str, symbol: str, bars: List[Candle], daily: Optional[List[Candle]],
             ticker: Optional[TickerData], max_spread_price: Optional[float] = None, rs=None,
             tf: Optional[str] = None) -> TradeDecisionReport:
    """Decision report for one strategy on its configured timeframe (HOLD when there is no valid signal)."""
    tf = tf or tf_of(key, rs)
    closed = closed_only(bars or [])
    last_ts = closed[-1].timestamp if closed else None
    if key == "DONCHIAN":
        rep = donchian_trend.evaluate(symbol, closed, ticker, max_spread_price, daily=daily, tf=tf, rs=rs)
        rep.signal_candle_ts = last_ts
        return rep
    if key == "SUPERTREND":
        sig, why, _ = supertrend_signal(closed, tf)
        setup, stop = setup_st(tf), ST_PROFILES[tf]["stop_atr"]
    elif key == "PULLBACK":
        sig, why, _ = pullback_signal(closed, daily, tf)
        setup, stop = setup_pb(tf), PB_PROFILES[tf]["stop_atr"]
    else:
        raise ValueError(f"Unknown strategy {key}")
    reasoning = [f"Strategy: {setup}", why]
    if sig == 0:
        rep = _hold_report(symbol, tf, reasoning, ["NO_SIGNAL", why], why)
        rep.signal_candle_ts = last_ts
        return rep
    return _entry_report(symbol, tf, sig, closed, ticker, max_spread_price, stop, setup, reasoning, why)


def signal_age_minutes(bars: List[Candle], tf: str, ticker: Optional[TickerData]) -> Optional[float]:
    """Minutes between the close of the signal candle and the latest broker tick (both broker clock)."""
    closed = closed_only(bars or [])
    if not closed or ticker is None or getattr(ticker, "timestamp", None) is None:
        return None
    try:
        close_time = closed[-1].timestamp + timedelta(minutes=TF_MINUTES[tf])
        return (ticker.timestamp - close_time).total_seconds() / 60.0
    except Exception:
        return None


def max_signal_age_minutes(tf: str) -> float:
    """A signal older than this is stale (bot was offline / market closed): skip it, price has moved."""
    return max(10.0, 0.15 * TF_MINUTES[tf])


# ── data fetching ─────────────────────────────────────────────────────────

def fetch_bars(adapter, symbol: str, key: str, rs=None, tf: Optional[str] = None) -> List[Candle]:
    tf = tf or tf_of(key, rs)
    if key == "DONCHIAN":
        return donchian_trend.fetch_bars(adapter, symbol, tf, rs=rs)
    return adapter.fetch_candles(symbol, tf, ST_FETCH_BARS if key == "SUPERTREND" else PB_FETCH_BARS) or []


def fetch_daily(adapter, symbol: str, key: str, rs=None, tf: Optional[str] = None) -> List[Candle]:
    tf = tf or tf_of(key, rs)
    if key == "DONCHIAN":
        return donchian_trend.fetch_daily(adapter, symbol, rs=rs, tf=tf)
    if key == "PULLBACK":
        return adapter.fetch_candles(symbol, "D1", PB_TREND_SMA + 20) or []
    return []


def manage_fetch_count(key: str) -> int:
    return ST_FETCH_BARS if key == "SUPERTREND" else 100


# ── open-position management ──────────────────────────────────────────────

def trail_candidate(candles: List[Candle], is_buy: bool, current_sl: float, trail_atr: float) -> Optional[float]:
    """Chandelier stop (last closed close -/+ trail_atr x ATR) if it improves on current_sl."""
    bars = closed_only(candles or [])
    if len(bars) < ATR_PERIOD + 2:
        return None
    atr = atr_series(bars)[-1]
    close = bars[-1].close
    cand = close - trail_atr * atr if is_buy else close + trail_atr * atr
    if is_buy and cand > current_sl:
        return cand
    if not is_buy and (current_sl <= 0 or cand < current_sl):
        return cand
    return None


def manage_action(key: str, candles: List[Candle], is_buy: bool, current_sl: float,
                  tf: Optional[str] = None) -> Optional[Tuple[str, Any]]:
    """What to do with an open trade of this strategy given the latest CLOSED candles of the timeframe it
    was opened on: ("TRAIL", new_sl), ("CLOSE", reason) or None."""
    tf = tf or DEFAULT_TFS[key]
    if key == "SUPERTREND":
        bars = closed_only(candles or [])
        if len(bars) < ST_MIN_BARS:
            return None            # not enough data to judge: keep the broker-side stop only
        p = ST_PROFILES[tf]
        dirs, _ = supertrend(bars, p["period"], p["mult"])
        if (is_buy and dirs[-1] == -1) or (not is_buy and dirs[-1] == 1):
            return "CLOSE", f"SUPERTREND_FLIP ({'bearish' if is_buy else 'bullish'} on {tf})"
        return None
    new_sl = trail_candidate(candles, is_buy, current_sl, params(key, tf)["trail_atr"])
    return ("TRAIL", new_sl) if new_sl is not None else None


# ── dashboard status ──────────────────────────────────────────────────────

def _plan(bars: List[Candle], equity: float, risk_pct: float, max_lot: float, stop_atr: float,
          trail_atr: Optional[float]) -> dict:
    atr = atr_series(bars)[-1]
    stop = stop_atr * atr
    loss_per_lot = stop * 100.0           # gold: 100 oz per lot
    raw = (equity * risk_pct / 100.0) / loss_per_lot if loss_per_lot > 0 else 0.0
    lot = max(0.01, min(max_lot, int(raw * 100) / 100.0)) if equity > 0 else 0.01
    risk_usd = lot * loss_per_lot
    return {"atr": round(atr, 2), "stop_distance": round(stop, 2),
            "trail_distance": round(trail_atr * atr, 2) if trail_atr else None,
            "next_lot": lot, "next_risk_usd": round(risk_usd, 2),
            "next_risk_pct": round(risk_usd / equity * 100, 2) if equity > 0 else None,
            "lot_capped": raw > max_lot}


def _positions_view(key: str, symbol: str, positions: list, bars: List[Candle], tf: str, rs=None) -> List[dict]:
    out = []
    atr = atr_series(bars)[-1] if bars else 0.0
    last_close = bars[-1].close if bars else 0.0
    for p in positions or []:
        if getattr(p, "symbol", "") != symbol or strategy_of(getattr(p, "setup_name", "")) != key:
            continue
        ptf = trade_timeframe(key, p, rs)
        prm = params(key, ptf)
        is_buy = getattr(p.side, "value", str(p.side)) == "BUY"
        nxt = None
        if prm["trail_atr"] and ptf == tf:       # the trail distance shown is only valid on the chart it trails on
            nxt = round(last_close - prm["trail_atr"] * atr if is_buy else last_close + prm["trail_atr"] * atr, 2)
        locked = ((p.sl_price - p.entry_price) if is_buy else (p.entry_price - p.sl_price)) * p.lots * 100.0 if p.sl_price else None
        out.append({"ticket": p.trade_id, "side": "BUY" if is_buy else "SELL", "lots": p.lots, "entry": p.entry_price,
                    "sl": p.sl_price, "next_trail": nxt, "pnl_usd": getattr(p, "pnl_usd", 0.0),
                    "locked_usd": round(locked, 2) if locked is not None else None,
                    "exit_rule": prm["exit"], "timeframe": ptf})
    return out


def _next_close_min(closed: List[Candle], tf: str, ticker: Optional[TickerData]) -> Optional[int]:
    """Minutes until the forming candle closes (the next moment a signal can fire)."""
    try:
        now = ticker.timestamp if ticker is not None else datetime.now()
        end = closed[-1].timestamp + timedelta(minutes=2 * TF_MINUTES[tf])
        return max(0, int((end - now).total_seconds() // 60))
    except Exception:
        return None


def status(key: str, symbol: str, bars: List[Candle], daily: Optional[List[Candle]], ticker: Optional[TickerData],
           equity: float, risk_pct: float, max_lot: float, positions: list, rs=None,
           tf: Optional[str] = None) -> dict:
    """Dashboard snapshot for one strategy on its configured timeframe."""
    tf = tf or tf_of(key, rs)
    prm = params(key, tf)
    closed = closed_only(bars or [])
    out: Dict[str, Any] = {"key": key, "label": LABELS[key], "timeframe": tf, "exit_rule": prm["exit"],
                           "supported_timeframes": list(SUPPORTED_TFS[key]),
                           "enabled": key in enabled_keys(rs) if rs is not None else True, "ok": False}
    if key == "DONCHIAN":
        d = donchian_trend.status(symbol, closed, ticker, equity, risk_pct, max_lot, [], daily=daily, tf=tf, rs=rs)
        out.update({k: v for k, v in d.items() if k not in ("symbol", "positions")})
        out.update({"key": key, "label": LABELS[key], "timeframe": tf, "exit_rule": prm["exit"],
                    "enabled": key in enabled_keys(rs) if rs is not None else True})
        if d.get("ok"):
            out["headline"] = {"UP": "Daily trend UP", "DOWN": "Daily trend DOWN"}.get(d.get("daily_trend"), "Daily trend unknown")
            out["detail"] = (f"{d['channel_bars']}-bar channel {d['channel_low']:.2f} - {d['channel_high']:.2f}; "
                             f"BUY needs +${d['to_buy']:.2f}, SELL needs -${d['to_sell']:.2f}")
            out["plan"] = _plan(closed, equity, risk_pct, max_lot, prm["stop_atr"], prm["trail_atr"])
            out["signal_text"] = d.get("signal_text", "")
            out["positions"] = _positions_view(key, symbol, positions, closed, tf, rs)
            px = float(ticker.bid) if ticker is not None else closed[-1].close
            if d.get("daily_trend") == "DOWN":
                out["waiting"] = (f"Daily trend DOWN: only SELL. Needs a candle CLOSE below {d['channel_low']:.2f} "
                                  f"(price must fall ${abs(d['to_sell']):.2f} from now). Longs are blocked.")
            elif d.get("daily_trend") == "UP":
                out["waiting"] = (f"Daily trend UP: only BUY. Needs a candle CLOSE above {d['channel_high']:.2f} "
                                  f"(price must rise ${abs(d['to_buy']):.2f} from now). Shorts are blocked.")
            else:
                out["waiting"] = "Daily trend unclear: no breakout trades until it is."
            out["next_close_min"] = _next_close_min(closed, tf, ticker)
        return out

    if key == "SUPERTREND":
        sig, why, info = supertrend_signal(closed, tf)
        if not info:
            out["message"] = why
            return out
        side_ok = "BUY" if info["close"] > info["ema"] else "SELL"
        out.update({"ok": True, "signal": {1: "BUY", -1: "SELL"}.get(sig, "NONE"), "signal_text": why,
                    "supertrend": "UP" if info["st_dir"] == 1 else "DOWN", "supertrend_line": round(info["st_line"], 2),
                    "ema": round(info["ema"], 2), "last_close": round(info["close"], 2),
                    "headline": f"Supertrend {'UP' if info['st_dir'] == 1 else 'DOWN'}",
                    "detail": f"ST({prm['period']},{prm['mult']:g}) line {info['st_line']:.2f}; EMA{ST_EMA} {info['ema']:.2f}; "
                              f"close is {'above' if info['close'] > info['ema'] else 'below'} the EMA (only {side_ok} flips are taken)"})
    else:
        sig, why, info = pullback_signal(closed, daily, tf)
        if not info:
            out["message"] = why
            return out
        dt = info["daily_trend"]
        out.update({"ok": True, "signal": {1: "BUY", -1: "SELL"}.get(sig, "NONE"), "signal_text": why,
                    "daily_trend": {1: "UP", -1: "DOWN"}.get(dt, "UNKNOWN"), "ema_fast": round(info["ema_fast"], 2),
                    "ema_slow": round(info["ema_slow"], 2), "last_close": round(info["close"], 2),
                    "headline": f"Daily trend {({1: 'UP', -1: 'DOWN'}).get(dt, 'unknown')}",
                    "detail": f"EMA{PB_EMA_FAST} {info['ema_fast']:.2f}, EMA{PB_EMA_SLOW} {info['ema_slow']:.2f}; "
                              f"close {info['close']:.2f} is ${abs(info['close'] - info['ema_fast']):.2f} "
                              f"{'above' if info['close'] > info['ema_fast'] else 'below'} EMA{PB_EMA_FAST}"})
    out["plan"] = _plan(closed, equity, risk_pct, max_lot, prm["stop_atr"], prm["trail_atr"])
    out["positions"] = _positions_view(key, symbol, positions, closed, tf, rs)
    px = float(ticker.bid) if ticker is not None else (closed[-1].close if closed else 0.0)
    if key == "SUPERTREND":
        line, ema_v = info["st_line"], info["ema"]
        if info["st_dir"] == 1:
            out["waiting"] = (f"Supertrend is UP. A SELL needs a candle CLOSE below the line {line:.2f} "
                              f"(price must fall ${abs(px - line):.2f}) while price is under EMA200 {ema_v:.2f}; BUY flips are only taken above the EMA.")
        else:
            out["waiting"] = (f"Supertrend is DOWN, so no fresh flip. A new BUY needs a CLOSE above {line:.2f} "
                              f"(price must rise ${abs(line - px):.2f}) with price also above EMA200 {ema_v:.2f}.")
    elif key == "PULLBACK":
        ef = info["ema_fast"]
        if info["daily_trend"] == 1:
            out["waiting"] = (f"Daily trend UP: BUY when price dips to EMA20 {ef:.2f} (${abs(px - ef):.2f} away) "
                              f"and a candle closes back above it.")
        elif info["daily_trend"] == -1:
            out["waiting"] = (f"Daily trend DOWN: SELL when price rallies up to EMA20 {ef:.2f} (${abs(ef - px):.2f} away) "
                              f"and a candle closes back below it.")
        else:
            out["waiting"] = "Daily trend unclear: no pullback trades until it is."
    if closed:
        out["next_close_min"] = _next_close_min(closed, tf, ticker)
        step = timedelta(minutes=TF_MINUTES[tf])
        out["last_candle_time"] = closed[-1].timestamp.isoformat()
        out["last_candle_server"] = f"{closed[-1].timestamp:%H:%M}-{(closed[-1].timestamp + step):%H:%M}"
    return out


# ── convenience wrappers used by the API / websocket / worker ────────────────

def evaluate_all(adapter, symbol: str, rs, ticker: Optional[TickerData] = None,
                 max_spread_price: Optional[float] = None) -> Dict[str, TradeDecisionReport]:
    """Fresh report for every enabled strategy (no execution)."""
    if ticker is None:
        ticker = adapter.get_ticker(symbol)
    return {k: evaluate(k, symbol, fetch_bars(adapter, symbol, k, rs=rs), fetch_daily(adapter, symbol, k, rs=rs),
                        ticker, max_spread_price, rs=rs) for k in enabled_keys(rs)}


def primary_report(reports: Dict[str, TradeDecisionReport]) -> Optional[TradeDecisionReport]:
    """The report to show as 'the' decision: the first one with a trade signal, else the first enabled."""
    for r in reports.values():
        if r.decision != TradeSide.HOLD:
            return r
    return next(iter(reports.values()), None)


def status_all(adapter, symbol: str, rs, equity: float, positions: list, ticker: Optional[TickerData] = None) -> dict:
    """Everything the dashboard needs for the three strategy cards."""
    if ticker is None:
        ticker = adapter.get_ticker(symbol)
    risk_pct = float(getattr(rs, "portfolio_risk_per_strategy_pct", 0.5) or 0.5)
    max_lot = float(getattr(rs, "max_allowed_lot", 0.05) or 0.05)
    cards = [status(k, symbol, fetch_bars(adapter, symbol, k, rs=rs), fetch_daily(adapter, symbol, k, rs=rs),
                    ticker, equity, risk_pct, max_lot, positions, rs=rs) for k in KEYS]
    return {"active": is_active(rs), "risk_per_strategy_pct": risk_pct, "strategies": cards,
            "open_total": sum(len(c.get("positions", [])) for c in cards),
            "max_open_positions": int(getattr(rs, "max_open_positions", 0) or 0)}
