"""
Deep Market Structure Analysis Engine (Smart Money Concepts / Institutional Footprints)
Detects BOS, CHoCH, Order Blocks, FVGs, Liquidity Sweeps, VWAP & Volume Profile
Works without numpy/pandas via pure-Python fallback.
"""

from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

from app.domain.models import (
    Candle, MarketStructureAnalysis, MarketStructurePoint,
    OrderBlock, FairValueGap, LiquiditySweep, TradeSide, MarketRegime
)
from app.services.indicators import IndicatorService


class MarketStructureEngine:
    """
    Algorithmic Market Structure Analysis Module.
    Identifies institutional liquidity traps, structural breaks, imbalance, and order blocks.
    """

    @classmethod
    def analyze(cls, candles: List[Candle], timeframe: str = "M15", symbol: str = "EURUSD", df: Optional[Any] = None) -> MarketStructureAnalysis:
        from app.services.symbol_specs import get_symbol_specs
        specs = get_symbol_specs(symbol)
        pip_size = specs["pip_size"]

        if len(candles) < 40:
            vp = IndicatorService.calculate_volume_profile(candles)
            return MarketStructureAnalysis(
                symbol=symbol,
                timeframe=timeframe,
                trend="UNKNOWN",
                regime=MarketRegime.RANGING,
                order_blocks=[],
                fvgs=[],
                liquidity_sweeps=[],
                vwap=candles[-1].close if candles else 1.0,
                poc_price=vp["poc"],
                value_area_high=vp["vah"],
                value_area_low=vp["val"]
            )

        if df is None:
            df = IndicatorService.calculate_all_indicators(candles)

        # Extract raw numpy arrays for fast positional analysis
        closes     = df["close"].values if hasattr(df["close"], "values") else df["close"]
        highs      = df["high"].values if hasattr(df["high"], "values") else df["high"]
        lows       = df["low"].values if hasattr(df["low"], "values") else df["low"]
        timestamps = df["timestamp"].values if hasattr(df["timestamp"], "values") else df["timestamp"]
        opens      = df["open"].values if hasattr(df["open"], "values") else df["open"]
        n          = len(candles)

        # 1. Swing Highs / Lows
        swing_highs, swing_lows = cls._find_swing_points(highs, lows, timestamps, pivot=3)

        # 2. BOS & CHoCH
        structure_points, trend_direction = cls._detect_structure_breaks(
            closes, timestamps, swing_highs, swing_lows
        )

        # 3. Order Blocks
        order_blocks = cls._detect_order_blocks(closes, opens, highs, lows, timestamps, swing_highs, swing_lows)

        # 4. FVGs
        fvgs = cls._detect_fair_value_gaps(highs, lows, timestamps, pip_size)

        # 5. Liquidity Sweeps
        sweeps = cls._detect_liquidity_sweeps(highs, lows, closes, timestamps, swing_highs, swing_lows)

        # 6. Market Regime
        regime = cls._evaluate_market_regime(df, trend_direction)

        # 7. Volume Profile & VWAP
        vp       = IndicatorService.calculate_volume_profile(candles[-50:])
        vwap_val = None
        if "vwap" in df:
            vwap_val = cls._get_last_val(df["vwap"])
        if vwap_val is None:
            vwap_val = candles[-1].close

        latest_point = structure_points[-1] if structure_points else None

        return MarketStructureAnalysis(
            symbol=symbol,
            timeframe=timeframe,
            trend=trend_direction,
            regime=regime,
            latest_structure=latest_point,
            order_blocks=order_blocks[-5:],
            fvgs=fvgs[-5:],
            liquidity_sweeps=sweeps[-5:],
            vwap=round(vwap_val, 5),
            poc_price=round(vp["poc"], 5),
            value_area_high=round(vp["vah"], 5),
            value_area_low=round(vp["val"], 5)
        )

    @staticmethod
    def _find_swing_points(
        highs, lows, timestamps, pivot: int = 3
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        swing_highs = []
        swing_lows  = []
        n = len(highs)
        if n < (pivot * 2 + 1):
            return swing_highs, swing_lows

        def get_ts(idx):
            ts = timestamps[idx]
            if not isinstance(ts, datetime):
                try:
                    ts = datetime.fromisoformat(str(ts))
                except Exception:
                    ts = datetime.now()
            return ts

        if hasattr(highs, "shape"):
            import numpy as np
            h_sub = highs[pivot:n - pivot]
            l_sub = lows[pivot:n - pivot]
            is_sh_mask = np.ones(len(h_sub), dtype=bool)
            is_sl_mask = np.ones(len(l_sub), dtype=bool)
            for k in range(1, pivot + 1):
                is_sh_mask &= (h_sub >= highs[pivot - k:n - pivot - k]) & (h_sub >= highs[pivot + k:n - pivot + k])
                is_sl_mask &= (l_sub <= lows[pivot - k:n - pivot - k]) & (l_sub <= lows[pivot + k:n - pivot + k])
            
            sh_indices = np.where(is_sh_mask)[0] + pivot
            sl_indices = np.where(is_sl_mask)[0] + pivot
            
            for idx in sh_indices:
                swing_highs.append({"index": int(idx), "price": float(highs[idx]), "timestamp": get_ts(idx)})
            for idx in sl_indices:
                swing_lows.append({"index": int(idx), "price": float(lows[idx]), "timestamp": get_ts(idx)})
        else:
            for i in range(pivot, n - pivot):
                if all(highs[i] >= highs[i - k] for k in range(1, pivot + 1)) and all(highs[i] >= highs[i + k] for k in range(1, pivot + 1)):
                    swing_highs.append({"index": i, "price": float(highs[i]), "timestamp": get_ts(i)})
                if all(lows[i] <= lows[i - k] for k in range(1, pivot + 1)) and all(lows[i] <= lows[i + k] for k in range(1, pivot + 1)):
                    swing_lows.append({"index": i, "price": float(lows[i]), "timestamp": get_ts(i)})

        return swing_highs, swing_lows

    @staticmethod
    def _detect_structure_breaks(
        closes, timestamps,
        swing_highs: List[Dict[str, Any]], swing_lows: List[Dict[str, Any]]
    ) -> Tuple[List[MarketStructurePoint], str]:
        points        = []
        current_trend = "UNKNOWN"

        all_swings = sorted(swing_highs + swing_lows, key=lambda x: x["index"])

        last_high = None
        last_low  = None

        high_set = {s["index"] for s in swing_highs}

        for swing in all_swings:
            is_high = swing["index"] in high_set

            if is_high:
                if last_high and swing["price"] > last_high["price"]:
                    points.append(MarketStructurePoint(
                        type="BOS_BULLISH", price=swing["price"], timestamp=swing["timestamp"]
                    ))
                    current_trend = "BULLISH"
                elif last_low and last_high and current_trend == "BEARISH" and swing["price"] > last_high["price"]:
                    points.append(MarketStructurePoint(
                        type="CHoCH_BULLISH", price=swing["price"], timestamp=swing["timestamp"]
                    ))
                    current_trend = "BULLISH"
                last_high = swing
            else:
                if last_low and swing["price"] < last_low["price"]:
                    points.append(MarketStructurePoint(
                        type="BOS_BEARISH", price=swing["price"], timestamp=swing["timestamp"]
                    ))
                    current_trend = "BEARISH"
                elif last_high and last_low and current_trend == "BULLISH" and swing["price"] < last_low["price"]:
                    points.append(MarketStructurePoint(
                        type="CHoCH_BEARISH", price=swing["price"], timestamp=swing["timestamp"]
                    ))
                    current_trend = "BEARISH"
                last_low = swing

        return points, current_trend

    @staticmethod
    def _detect_order_blocks(
        closes, opens, highs, lows, timestamps,
        swing_highs: List[Dict[str, Any]], swing_lows: List[Dict[str, Any]]
    ) -> List[OrderBlock]:
        order_blocks = []

        def get_ts(i):
            ts = timestamps[i]
            if not isinstance(ts, datetime):
                try:
                    ts = datetime.fromisoformat(str(ts))
                except Exception:
                    ts = datetime.now()
            return ts

        for sh in swing_highs:
            idx = sh["index"]
            if idx > 3:
                for k in range(1, 4):
                    c_idx = idx - k
                    if closes[c_idx] < opens[c_idx]:
                        order_blocks.append(OrderBlock(
                            side=TradeSide.BUY,
                            top=float(highs[c_idx]),
                            bottom=float(lows[c_idx]),
                            timestamp=get_ts(c_idx),
                            mitigated=False
                        ))
                        break

        for sl in swing_lows:
            idx = sl["index"]
            if idx > 3:
                for k in range(1, 4):
                    c_idx = idx - k
                    if closes[c_idx] > opens[c_idx]:
                        order_blocks.append(OrderBlock(
                            side=TradeSide.SELL,
                            top=float(highs[c_idx]),
                            bottom=float(lows[c_idx]),
                            timestamp=get_ts(c_idx),
                            mitigated=False
                        ))
                        break

        return order_blocks

    @staticmethod
    def _detect_fair_value_gaps(highs, lows, timestamps, pip_size: float = 0.0001) -> List[FairValueGap]:
        fvgs = []
        n    = len(highs)

        def get_ts(i):
            ts = timestamps[i]
            if not isinstance(ts, datetime):
                try:
                    ts = datetime.fromisoformat(str(ts))
                except Exception:
                    ts = datetime.now()
            return ts

        min_gap = 1.0 * pip_size
        for i in range(2, n):
            if lows[i] > highs[i - 2]:
                gap = lows[i] - highs[i - 2]
                if gap > min_gap:
                    fvgs.append(FairValueGap(
                        side=TradeSide.BUY,
                        top=float(lows[i]),
                        bottom=float(highs[i - 2]),
                        timestamp=get_ts(i),
                        filled=False
                    ))
            elif highs[i] < lows[i - 2]:
                gap = lows[i - 2] - highs[i]
                if gap > min_gap:
                    fvgs.append(FairValueGap(
                        side=TradeSide.SELL,
                        top=float(lows[i - 2]),
                        bottom=float(highs[i]),
                        timestamp=get_ts(i),
                        filled=False
                    ))

        return fvgs

    @staticmethod
    def _detect_liquidity_sweeps(
        highs, lows, closes, timestamps,
        swing_highs: List[Dict[str, Any]], swing_lows: List[Dict[str, Any]]
    ) -> List[LiquiditySweep]:
        sweeps = []
        n      = len(highs)

        def get_ts(i):
            ts = timestamps[i]
            if not isinstance(ts, datetime):
                try:
                    ts = datetime.fromisoformat(str(ts))
                except Exception:
                    ts = datetime.now()
            return ts

        for sh in swing_highs:
            max_fw = min(n, sh["index"] + 60)
            for i in range(sh["index"] + 3, max_fw):
                if highs[i] > sh["price"] and closes[i] < sh["price"]:
                    sweeps.append(LiquiditySweep(
                        side=TradeSide.SELL,
                        price_level=sh["price"],
                        timestamp=get_ts(i)
                    ))
                    break

        for sl in swing_lows:
            max_fw = min(n, sl["index"] + 60)
            for i in range(sl["index"] + 3, max_fw):
                if lows[i] < sl["price"] and closes[i] > sl["price"]:
                    sweeps.append(LiquiditySweep(
                        side=TradeSide.BUY,
                        price_level=sl["price"],
                        timestamp=get_ts(i)
                    ))
                    break

        return sweeps

    @staticmethod
    def _get_last_val(col) -> Optional[float]:
        if col is None:
            return None
        
        # Check if pandas Series
        try:
            import pandas as pd
            is_series = isinstance(col, pd.Series)
        except ImportError:
            is_series = False
            
        if is_series:
            if col.empty:
                return None
            val = col.iloc[-1]
            if pd.isna(val):
                return None
            return float(val)
        else:
            # It's a python list
            if not col:
                return None
            val = col[-1]
            if val is None:
                return None
            try:
                import math
                if isinstance(val, float) and math.isnan(val):
                    return None
            except Exception:
                pass
            return float(val)

    @staticmethod
    def _evaluate_market_regime(df, trend: str) -> MarketRegime:
        adx_val = None
        if "adx" in df:
            adx_val = MarketStructureEngine._get_last_val(df["adx"])

        bb_width_val = None
        if "bb_width" in df:
            bb_width_val = MarketStructureEngine._get_last_val(df["bb_width"])

        adx      = adx_val if adx_val is not None else 15.0
        bb_width = bb_width_val if bb_width_val is not None else 0.003

        if bb_width > 0.006:
            return MarketRegime.HIGH_VOLATILITY
        elif adx > 25.0:
            if trend == "BULLISH":
                return MarketRegime.BULLISH_TREND
            elif trend == "BEARISH":
                return MarketRegime.BEARISH_TREND
            else:
                return MarketRegime.RANGING
        else:
            return MarketRegime.RANGING
