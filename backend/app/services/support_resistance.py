"""Automatic support / resistance levels for the chart.

Display only: nothing here is used by the trading logic.

Method
  1. Confirmed swing highs / lows (pivot with `k` closed candles on each side).
  2. Pivots whose prices lie within `CLUSTER_ATR` x ATR of each other are merged into one
     level (price = mean of the pivots). Touches = number of pivots in the cluster.
  3. A level needs >= MIN_TOUCHES touches. Whether it is support or resistance depends only
     on where price is NOW, so a broken support automatically becomes resistance.
  4. Levels of the chart timeframe plus the bigger timeframes (H4, D1) are merged; a bigger
     timeframe's level wins when two levels coincide.
  5. Only the strongest levels near price are returned (a readable chart, not a ruler).
"""
import logging
from typing import Any, Dict, List, Optional

from app.domain.models import Candle
from app.services.donchian_trend import atr_series, closed_only

logger = logging.getLogger(__name__)

PIVOT_K = {"M1": 6, "M5": 6, "M15": 5, "M30": 5, "H1": 5, "H4": 4, "D1": 3}
CLUSTER_ATR = 0.35        # pivots closer than this (x ATR) form one level
MIN_TOUCHES = 2
RANGE_ATR = 12.0          # only levels within this many ATRs of price are shown
MAX_PER_SIDE = 4
LOOKBACK = {"M1": 600, "M5": 600, "M15": 600, "M30": 600, "H1": 600, "H4": 500, "D1": 400}
TF_RANK = {"M1": 0, "M5": 1, "M15": 2, "M30": 3, "H1": 4, "H4": 5, "D1": 6}
HIGHER = {"M1": ["H1", "H4", "D1"], "M5": ["H1", "H4", "D1"], "M15": ["H1", "H4", "D1"], "M30": ["H4", "D1"],
          "H1": ["H4", "D1"], "H4": ["D1"], "D1": []}


def pivots(candles: List[Candle], k: int) -> List[Dict[str, Any]]:
    """Confirmed swing highs/lows. The last k candles cannot be confirmed yet and are skipped."""
    out: List[Dict[str, Any]] = []
    n = len(candles)
    for i in range(k, n - k):
        hi, lo = candles[i].high, candles[i].low
        left, right = candles[i - k:i], candles[i + 1:i + k + 1]
        # equal extremes: the first candle wins (strictly higher than the left side, >= on the right)
        if hi > max(c.high for c in left) and hi >= max(c.high for c in right):
            out.append({"price": hi, "idx": i, "type": "H"})
        if lo < min(c.low for c in left) and lo <= min(c.low for c in right):
            out.append({"price": lo, "idx": i, "type": "L"})
    return out


def cluster(piv: List[Dict[str, Any]], tol: float, n_bars: int) -> List[Dict[str, Any]]:
    """Merge nearby pivot prices into levels."""
    if not piv or tol <= 0:
        return []
    ordered = sorted(piv, key=lambda p: p["price"])
    groups: List[List[Dict[str, Any]]] = [[ordered[0]]]
    for p in ordered[1:]:
        if p["price"] - groups[-1][-1]["price"] <= tol:
            groups[-1].append(p)
        else:
            groups.append([p])
    levels = []
    for g in groups:
        newest = max(p["idx"] for p in g)
        levels.append({"price": sum(p["price"] for p in g) / len(g), "touches": len(g),
                       "age": n_bars - 1 - newest})
    return levels


def levels_for(candles: List[Candle], tf: str) -> List[Dict[str, Any]]:
    """Raw levels of one timeframe (closed candles only)."""
    closed = closed_only(candles or [])
    if len(closed) < 40:
        return []
    atr = atr_series(closed)[-1]
    if atr <= 0:
        return []
    raw = cluster(pivots(closed, PIVOT_K.get(tf, 5)), CLUSTER_ATR * atr, len(closed))
    return [{**lv, "tf": tf, "atr": atr} for lv in raw if lv["touches"] >= MIN_TOUCHES]


def build(per_tf: Dict[str, List[Candle]], chart_tf: str, price: float) -> List[Dict[str, Any]]:
    """Merge the timeframes and pick what is worth drawing. `per_tf` maps timeframe -> candles."""
    base = closed_only(per_tf.get(chart_tf) or [])
    if len(base) < 40:
        return []
    ref_atr = atr_series(base)[-1]
    if ref_atr <= 0:
        return []

    cands: List[Dict[str, Any]] = []
    for tf, candles in per_tf.items():
        cands.extend(levels_for(candles, tf))

    # Same level on several timeframes -> keep the bigger timeframe, add the touches.
    cands.sort(key=lambda lv: (-TF_RANK.get(lv["tf"], 0), -lv["touches"]))
    merged: List[Dict[str, Any]] = []
    tol = CLUSTER_ATR * ref_atr
    for lv in cands:
        near = next((m for m in merged if abs(m["price"] - lv["price"]) <= tol), None)
        if near:
            near["touches"] += lv["touches"]
        else:
            merged.append(dict(lv))

    out = []
    for lv in merged:
        dist = lv["price"] - price
        if abs(dist) > RANGE_ATR * ref_atr:
            continue
        rank = TF_RANK.get(lv["tf"], 0)
        score = lv["touches"] + rank * 1.5 - min(lv["age"], 400) / 200.0
        out.append({"price": round(lv["price"], 2), "kind": "resistance" if dist > 0 else "support",
                    "tf": lv["tf"], "touches": lv["touches"], "score": round(score, 2),
                    "distance": round(dist, 2), "distance_atr": round(dist / ref_atr, 2)})

    # strongest few on each side of price
    result: List[Dict[str, Any]] = []
    for kind in ("resistance", "support"):
        side = [lv for lv in out if lv["kind"] == kind]
        side.sort(key=lambda lv: -lv["score"])
        result.extend(side[:MAX_PER_SIDE])
    # strength 3 = the three strongest levels drawn, 2 = the next three, 1 = the rest
    for rank, lv in enumerate(sorted(result, key=lambda x: -x["score"])):
        lv["strength"] = 3 if rank < 3 else 2 if rank < 6 else 1
    result.sort(key=lambda lv: -lv["price"])
    return result


_cache: Dict[str, Any] = {}


def get_levels(adapter, symbol: str, chart_tf: str, price: Optional[float] = None) -> Dict[str, Any]:
    """Levels for the chart. Recomputed only when a new candle has closed on any used timeframe."""
    chart_tf = (chart_tf or "M15").upper()
    tfs = [chart_tf] + [t for t in HIGHER.get(chart_tf, []) if t != chart_tf]
    per_tf: Dict[str, List[Candle]] = {}
    sig_parts = []
    for tf in tfs:
        try:
            c = adapter.fetch_candles(symbol, tf, LOOKBACK.get(tf, 500)) or []
        except Exception as e:
            logger.warning(f"[S/R] could not fetch {tf}: {e}")
            c = []
        per_tf[tf] = c
        cl = closed_only(c)
        sig_parts.append(f"{tf}:{cl[-1].timestamp.isoformat() if cl else '-'}")
    if price is None:
        base = closed_only(per_tf.get(chart_tf) or [])
        price = base[-1].close if base else 0.0
    # levels depend on which side of price they are: re-bucket whenever price moves > 1 ATR-ish step
    key = f"{symbol}|{chart_tf}"
    sig = "|".join(sig_parts)
    hit = _cache.get(key)
    if hit and hit["sig"] == sig:
        levels = hit["levels"]
        for lv in levels:                                    # re-label support/resistance on the live price
            lv["distance"] = round(lv["price"] - price, 2)
            lv["kind"] = "resistance" if lv["distance"] > 0 else "support"
    else:
        levels = build(per_tf, chart_tf, price)
        _cache[key] = {"sig": sig, "levels": levels}
    return {"symbol": symbol, "timeframe": chart_tf, "price": price, "levels": levels,
            "timeframes_used": tfs, "ok": bool(levels)}
