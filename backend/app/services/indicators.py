"""
Technical Analysis Indicators Service
Calculates Trend, Momentum, Volatility, Volume Profile & Candlestick Patterns
Uses pandas/numpy when available, falls back to pure-Python implementations otherwise.
"""

from typing import List, Dict, Any, Tuple, Optional
from app.domain.models import Candle

# Optional numpy/pandas imports with graceful fallback
try:
    import numpy as np
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

import math
import statistics


class IndicatorService:
    """
    High-performance technical analysis engine.
    Fully self-contained with pandas/numpy when available,
    or pure Python fallback for environments without binary packages.
    """

    @classmethod
    def detect_candlestick_pattern(cls, candles: List[Candle]) -> Tuple[str, str]:
        """
        Detects latest candlestick pattern.
        Returns Tuple[pattern_name, pattern_bias] e.g. ("BULLISH_ENGULFING", "BULLISH")
        """
        if len(candles) < 3:
            return "NONE", "NEUTRAL"

        c1 = candles[-3]
        c2 = candles[-2]
        c3 = candles[-1]

        open3, high3, low3, close3 = c3.open, c3.high, c3.low, c3.close
        open2, high2, low2, close2 = c2.open, c2.high, c2.low, c2.close
        open1, high1, low1, close1 = c1.open, c1.high, c1.low, c1.close

        body3 = abs(close3 - open3)
        range3 = high3 - low3 or 0.00001
        upper_wick3 = high3 - max(open3, close3)
        lower_wick3 = min(open3, close3) - low3

        body2 = abs(close2 - open2)
        range2 = high2 - low2 or 0.00001

        # 1. Bullish Pin Bar / Hammer
        if lower_wick3 >= body3 * 2.0 and upper_wick3 <= body3 * 0.5:
            return "PIN_BAR_BULLISH", "BULLISH"

        # 2. Bearish Shooting Star / Inverted Hammer
        if upper_wick3 >= body3 * 2.0 and lower_wick3 <= body3 * 0.5:
            return "SHOOTING_STAR_BEARISH", "BEARISH"

        # 3. Bullish Engulfing
        if close2 < open2 and close3 > open3 and open3 <= close2 and close3 >= open2:
            return "BULLISH_ENGULFING", "BULLISH"

        # 4. Bearish Engulfing
        if close2 > open2 and close3 < open3 and open3 >= close2 and close3 <= open2:
            return "BEARISH_ENGULFING", "BEARISH"

        # 5. Morning Star
        if close1 < open1 and body2 < (abs(close1 - open1) * 0.3) and close3 > open3 and close3 > (open1 + close1) / 2:
            return "MORNING_STAR_BULLISH", "BULLISH"

        # 6. Evening Star
        if close1 > open1 and body2 < (abs(close1 - open1) * 0.3) and close3 < open3 and close3 < (open1 + close1) / 2:
            return "EVENING_STAR_BEARISH", "BEARISH"

        # 7. Bullish Harami
        if close2 < open2 and close3 > open3 and open3 > low2 and close3 < high2 and body3 < body2:
            return "BULLISH_HARAMI", "BULLISH"

        # 8. Bearish Harami
        if close2 > open2 and close3 < open3 and open3 < high2 and close3 > low2 and body3 < body2:
            return "BEARISH_HARAMI", "BEARISH"

        # 9. Strong Momentum Bar
        if close3 > open3 and body3 / range3 > 0.6:
            return "STRONG_BULLISH_BAR", "BULLISH"
        elif close3 < open3 and body3 / range3 > 0.6:
            return "STRONG_BEARISH_BAR", "BEARISH"

        return "NONE", "NEUTRAL"

    @staticmethod
    def candles_to_df(candles: List[Candle]):
        """Returns a pandas DataFrame or a plain dict-of-lists structure."""
        data = {
            "timestamp": [c.timestamp for c in candles],
            "open":      [c.open  for c in candles],
            "high":      [c.high  for c in candles],
            "low":       [c.low   for c in candles],
            "close":     [c.close for c in candles],
            "volume":    [c.volume for c in candles],
        }
        if _HAS_PANDAS:
            df = pd.DataFrame(data)
            df.sort_values("timestamp", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df
        # Pure-Python fallback: return a simple dict-wrapper
        return _DictFrame(data)

    @classmethod
    def calculate_all_indicators(cls, candles: List[Candle], bb_period: Optional[int] = None, bb_std: Optional[float] = None):
        df = cls.candles_to_df(candles)
        n  = len(candles)
        if n < 30:
            return df

        bb_period_val = int(bb_period) if bb_period and bb_period > 0 else 20
        bb_std_val = float(bb_std) if bb_std and bb_std > 0 else 2.0


        closes  = df["close"]
        highs   = df["high"]
        lows    = df["low"]
        volumes = df["volume"]
        opens   = df["open"]

        # ── EMA helper ──────────────────────────────────────────────
        def ema(src, span):
            k = 2.0 / (span + 1)
            result = [None] * n
            result[0] = src[0]
            for i in range(1, n):
                result[i] = src[i] * k + result[i - 1] * (1 - k)
            return result

        # ── Rolling mean helper ──────────────────────────────────────
        def rolling_mean(src, window):
            result = [None] * n
            for i in range(window - 1, n):
                result[i] = sum(src[i - window + 1: i + 1]) / window
            return result

        # ── Rolling std helper ───────────────────────────────────────
        def rolling_std(src, window):
            result = [None] * n
            for i in range(window - 1, n):
                vals = src[i - window + 1: i + 1]
                mean = sum(vals) / window
                result[i] = math.sqrt(sum((v - mean) ** 2 for v in vals) / window)
            return result

        # ── Rolling sum helper ───────────────────────────────────────
        def rolling_sum(src, window):
            result = [None] * n
            for i in range(window - 1, n):
                result[i] = sum(v for v in src[i - window + 1: i + 1] if v is not None)
            return result

        # ── Trend Indicators ────────────────────────────────────────
        ema20  = ema(closes, 20)
        ema50  = ema(closes, 50)
        ema200 = ema(closes, 200)
        sma20  = rolling_mean(closes, 20)
        sma50  = rolling_mean(closes, 50)

        # VWAP
        typical = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
        cum_tp_vol = 0.0
        cum_vol    = 0.0
        vwap = [None] * n
        for i in range(n):
            cum_tp_vol += typical[i] * volumes[i]
            cum_vol    += volumes[i]
            vwap[i]    = cum_tp_vol / (cum_vol or 1.0)

        # ATR
        high_low = [highs[i] - lows[i] for i in range(n)]
        high_prev_close = [abs(highs[i] - closes[i - 1]) if i > 0 else high_low[i] for i in range(n)]
        low_prev_close  = [abs(lows[i]  - closes[i - 1]) if i > 0 else 0.0 for i in range(n)]
        tr     = [max(high_low[i], high_prev_close[i], low_prev_close[i]) for i in range(n)]
        atr_14 = rolling_mean(tr, 14)

        # SuperTrend
        atr10 = rolling_mean(tr, 10)
        hl2   = [(highs[i] + lows[i]) / 2.0 for i in range(n)]
        basic_upper = [hl2[i] + (3.0 * (atr10[i] or 0)) for i in range(n)]
        basic_lower = [hl2[i] - (3.0 * (atr10[i] or 0)) for i in range(n)]

        supertrend     = [True]  * n
        supertrend_val = [0.0]   * n
        for i in range(1, n):
            if closes[i] > basic_upper[i - 1]:
                supertrend[i] = True
            elif closes[i] < basic_lower[i - 1]:
                supertrend[i] = False
            else:
                supertrend[i] = supertrend[i - 1]
            supertrend_val[i] = basic_lower[i] if supertrend[i] else basic_upper[i]

        # ADX
        up_move   = [highs[i] - highs[i - 1] if i > 0 else 0.0 for i in range(n)]
        down_move = [lows[i - 1] - lows[i]   if i > 0 else 0.0 for i in range(n)]
        pos_dm    = [up_move[i]   if up_move[i]   > down_move[i] and up_move[i]   > 0 else 0.0 for i in range(n)]
        neg_dm    = [down_move[i] if down_move[i]  > up_move[i]  and down_move[i] > 0 else 0.0 for i in range(n)]
        tr14      = rolling_sum(tr, 14)
        pdm14     = rolling_sum(pos_dm, 14)
        ndm14     = rolling_sum(neg_dm, 14)
        pos_di    = [100.0 * pdm14[i] / (tr14[i] or 1.0) if tr14[i] else None for i in range(n)]
        neg_di    = [100.0 * ndm14[i] / (tr14[i] or 1.0) if tr14[i] else None for i in range(n)]
        dx        = [100.0 * abs((pos_di[i] or 0) - (neg_di[i] or 0)) / ((pos_di[i] or 0) + (neg_di[i] or 0) + 1e-9)
                     if pos_di[i] is not None else None for i in range(n)]
        adx       = rolling_mean([v or 0 for v in dx], 14)

        # RSI
        delta = [closes[i] - closes[i - 1] if i > 0 else 0.0 for i in range(n)]
        gain  = rolling_mean([max(0.0, d) for d in delta], 14)
        loss  = rolling_mean([max(0.0, -d) for d in delta], 14)
        rsi_14 = [100.0 - (100.0 / (1.0 + (gain[i] or 0) / ((loss[i] or 0) + 1e-9)))
                  if gain[i] is not None else 50.0 for i in range(n)]

        # Stochastic RSI
        rsi_min = rolling_mean(rsi_14, 14)   # approximate with rolling mean for simplicity
        rsi_max = [max(rsi_14[max(0, i - 13): i + 1]) for i in range(n)]
        rsi_min_v = [min(rsi_14[max(0, i - 13): i + 1]) for i in range(n)]
        stoch_raw = [(rsi_14[i] - rsi_min_v[i]) / ((rsi_max[i] - rsi_min_v[i]) + 1e-9) for i in range(n)]
        stoch_rsi_k = [(v * 100.0 if v is not None else 50.0) for v in rolling_mean(stoch_raw, 3)]
        stoch_rsi_k = [v if v is not None else 50.0 for v in stoch_rsi_k]
        stoch_rsi_d = rolling_mean(stoch_rsi_k, 3)

        # MACD
        ema12      = ema(closes, 12)
        ema26      = ema(closes, 26)
        macd_line  = [ema12[i] - ema26[i] for i in range(n)]
        macd_sig   = ema(macd_line, 9)
        macd_hist  = [macd_line[i] - macd_sig[i] for i in range(n)]

        # ROC, CCI
        roc_12 = [(closes[i] - closes[i - 12]) / (closes[i - 12] or 1.0) * 100.0
                  if i >= 12 else 0.0 for i in range(n)]
        sma_tp20 = rolling_mean(typical, 20)
        mad       = [statistics.mean(abs(typical[j] - (sma_tp20[i] or typical[i]))
                     for j in range(max(0, i - 19), i + 1))
                     for i in range(n)]
        cci_20 = [(typical[i] - (sma_tp20[i] or typical[i])) / (0.015 * (mad[i] or 1e-9))
                  for i in range(n)]

        # Bollinger Bands
        sma_bb   = rolling_mean(closes, bb_period_val)
        std_bb   = rolling_std(closes, bb_period_val)
        bb_upper = [(sma_bb[i] or closes[i]) + bb_std_val * (std_bb[i] or 0) for i in range(n)]
        bb_lower = [(sma_bb[i] or closes[i]) - bb_std_val * (std_bb[i] or 0) for i in range(n)]
        bb_width = [(bb_upper[i] - bb_lower[i]) / ((sma_bb[i] or closes[i]) + 1e-9) for i in range(n)]
        percent_b = [(closes[i] - bb_lower[i]) / ((bb_upper[i] - bb_lower[i]) or 1e-9) for i in range(n)]
        bb_expansion = [bb_width[i] > bb_width[i-1] * 1.05 if i > 0 else False for i in range(n)]


        # Donchian
        don_high = [max(highs[max(0, i - 19): i + 1]) for i in range(n)]
        don_low  = [min(lows[max(0, i - 19): i + 1]) for i in range(n)]
        don_mid  = [(don_high[i] + don_low[i]) / 2.0 for i in range(n)]

        # OBV
        obv = [0.0] * n
        for i in range(1, n):
            if closes[i] > closes[i - 1]:
                obv[i] = obv[i - 1] + volumes[i]
            elif closes[i] < closes[i - 1]:
                obv[i] = obv[i - 1] - volumes[i]
            else:
                obv[i] = obv[i - 1]

        # RVOL
        vol_avg = rolling_mean(volumes, 20)
        rvol    = [volumes[i] / ((vol_avg[i] or volumes[i]) + 1e-9) for i in range(n)]

        # Candlestick patterns (simple)
        body_size    = [abs(closes[i] - opens[i]) for i in range(n)]
        lower_shadow = [opens[i] - lows[i] if closes[i] >= opens[i] else closes[i] - lows[i] for i in range(n)]
        upper_shadow = [highs[i] - closes[i] if closes[i] >= opens[i] else highs[i] - opens[i] for i in range(n)]

        pat_pinbar   = [lower_shadow[i] > 2.0 * body_size[i] and upper_shadow[i] < body_size[i] for i in range(n)]
        hl_size      = [highs[i] - lows[i] for i in range(n)]
        pat_doji     = [body_size[i] < 0.10 * (hl_size[i] or 1.0) for i in range(n)]

        # Set all computed columns
        df["ema_20"]       = ema20
        df["ema_50"]       = ema50
        df["ema_200"]      = ema200
        df["sma_20"]       = sma20
        df["sma_50"]       = sma50
        df["vwap"]         = vwap
        df["atr_14"]       = atr_14
        df["supertrend_dir"] = supertrend
        df["supertrend"]   = supertrend_val
        df["adx"]          = adx
        df["plus_di"]      = pos_di
        df["minus_di"]     = neg_di
        df["rsi_14"]       = rsi_14
        df["stoch_rsi_k"]  = stoch_rsi_k
        df["stoch_rsi_d"]  = stoch_rsi_d
        df["macd_line"]    = macd_line
        df["macd_signal"]  = macd_sig
        df["macd_hist"]    = macd_hist
        df["roc_12"]       = roc_12
        df["cci_20"]       = cci_20
        df["bb_middle"]    = sma_bb
        df["bb_upper"]     = bb_upper
        df["bb_lower"]     = bb_lower
        df["bb_width"]     = bb_width
        df["percent_b"]    = percent_b
        df["bb_expansion"] = bb_expansion
        df["bb_squeeze"]   = False
        df["donchian_high"] = don_high
        df["donchian_low"]  = don_low
        df["donchian_mid"]  = don_mid
        df["obv"]          = obv
        df["rvol"]         = rvol
        df["pattern_pinbar_hammer"]    = pat_pinbar
        df["pattern_doji"]             = pat_doji
        df["high_low"]      = high_low
        df["tr"]            = tr
        return df

    @classmethod
    def calculate_volume_profile(cls, candles: List[Candle], bins: int = 30) -> Dict[str, float]:
        if not candles:
            return {"poc": 0.0, "vah": 0.0, "val": 0.0}

        prices  = [(c.high + c.low + c.close) / 3.0 for c in candles]
        volumes = [c.volume for c in candles]

        min_p = min(prices)
        max_p = max(prices)

        if min_p == max_p:
            return {"poc": min_p, "vah": max_p, "val": min_p}

        bin_width = (max_p - min_p) / bins
        profile   = [0.0] * bins
        for p, v in zip(prices, volumes):
            b = min(int((p - min_p) / bin_width), bins - 1)
            profile[b] += v

        poc_bin   = profile.index(max(profile))
        poc_price = min_p + (poc_bin + 0.5) * bin_width

        total_vol  = sum(profile)
        target_vol = total_vol * 0.70
        accum_vol  = 0.0
        va_prices  = []
        sorted_bins = sorted(range(bins), key=lambda b: profile[b], reverse=True)
        for b in sorted_bins:
            accum_vol += profile[b]
            va_prices.append(min_p + (b + 0.5) * bin_width)
            if accum_vol >= target_vol:
                break

        vah = max(va_prices) if va_prices else poc_price
        val = min(va_prices) if va_prices else poc_price

        return {"poc": poc_price, "vah": vah, "val": val}


# ─── Pure-Python DataFrame substitute ──────────────────────────────────
class _DictFrame:
    """
    Minimal dict-backed DataFrame-like object used when pandas is not available.
    Supports column access, iloc[-1], len(), 'col' in df, and df["col"] = value.
    """
    def __init__(self, data: Dict[str, list]):
        self._d: Dict[str, list] = {k: list(v) for k, v in data.items()}
        self._n  = len(next(iter(data.values())) if data else [])

    # ── dict-like ────────────────────────────────────────────────────────
    def __getitem__(self, key: str):
        return self._d[key]

    def __setitem__(self, key: str, value):
        if isinstance(value, list):
            self._d[key] = value
        else:
            # scalar broadcast
            self._d[key] = [value] * self._n

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return self._n

    @property
    def iloc(self):
        return _ILocAccessor(self)

    def get(self, key: str, default=None):
        return self._d.get(key, [default] * self._n)

    @property
    def columns(self):
        return list(self._d.keys())

    @property
    def empty(self) -> bool:
        return self._n == 0


class _ILocAccessor:
    def __init__(self, frame):
        self._f = frame

    def __getitem__(self, idx):
        return _RowView(self._f, idx)


class _RowView:
    """Single-row view that behaves like a Series / dict for iloc[-1] access."""
    def __init__(self, frame, idx: int):
        self._f   = frame
        self._idx = idx

    def __getitem__(self, key: str):
        col = self._f[key]
        if col:
            return col[self._idx]
        return None

    def get(self, key: str, default=None):
        col = self._f._d.get(key)
        if col is None:
            return default
        v = col[self._idx]
        return default if v is None else v

    def __contains__(self, key: str) -> bool:
        return key in self._f
