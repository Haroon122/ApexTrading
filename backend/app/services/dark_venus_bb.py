"""
Dark Venus-Style Bollinger Band Engine Module for XAUUSD Gold Scalping.
Implements 6 Publicly Observable Bollinger Band Trading Modes & Feature Analytics:
  1. Counter-Trend Band Reversion (Mean Reversion, Regime Guarded)
  2. Band Breakout / Momentum Continuation
  3. Trend-Following Band Interaction
  4. Central Band Trend Cross
  5. Pullback to Bollinger Midline
  6. Band Squeeze -> Expansion Breakout
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional
from app.domain.models import TradeSide

logger = logging.getLogger("ApexTrader.DarkVenusBBEngine")


class DarkVenusStyleBBEngine:
    """
    Bollinger Band Strategy & Analytics Engine inspired by public Dark Venus concepts.
    Provides complete Bollinger analytics and evaluates 6 distinct strategy modes.
    """

    @staticmethod
    def calculate_bollinger_features(df: pd.DataFrame, period: Optional[int] = None, std_dev: Optional[float] = None) -> pd.DataFrame:
        """
        Calculates complete Bollinger Band indicator suite dynamically.
        """
        p_val = int(period) if period and period > 0 else 20
        s_val = float(std_dev) if std_dev and std_dev > 0 else 2.0

        if df.empty or len(df) < p_val:
            return df

        if "percent_b" in df.columns and "bb_squeeze" in df.columns and getattr(df, "_bb_period", 20) == p_val and getattr(df, "_bb_std", 2.0) == s_val:
            return df

        df = df.copy()
        df._bb_period = p_val
        df._bb_std = s_val
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # Basic Bands
        sma = close.rolling(window=p_val).mean()
        std = close.rolling(window=p_val).std()
        
        df["bb_middle"] = sma
        df["bb_upper"] = sma + (std * s_val)
        df["bb_lower"] = sma - (std * s_val)

        # Width & %B
        denom = (df["bb_upper"] - df["bb_lower"]).replace(0, 1e-9)
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (df["bb_middle"] + 1e-9)
        df["percent_b"] = (close - df["bb_lower"]) / denom

        # Vectorized Rolling BB Width Percentile (50-bar lookback)
        widths = df["bb_width"].values
        w_len = len(widths)
        pctiles = np.full(w_len, 0.5)
        for i in range(10, w_len):
            sub = widths[max(0, i - 50):i + 1]
            pctiles[i] = float(np.mean(sub < sub[-1])) if len(sub) > 0 else 0.5
        df["bb_width_percentile"] = pctiles

        # Band Distances
        df["dist_bb_upper"] = df["bb_upper"] - close
        df["dist_bb_middle"] = close - df["bb_middle"]
        df["dist_bb_lower"] = close - df["bb_lower"]

        # Band Slope (5-bar linear change of middle band)
        df["bb_middle_slope"] = df["bb_middle"].diff(5) / 5.0

        # Expansion / Contraction State
        prev_width = df["bb_width"].shift(1)
        df["bb_expansion"] = df["bb_width"] > prev_width * 1.05
        df["bb_contraction"] = df["bb_width"] < prev_width * 0.95
        df["bb_squeeze"] = df["bb_width_percentile"] < 0.25

        return df

    @staticmethod
    def evaluate_bb_signals(
        df: pd.DataFrame,
        regime: str,
        h1_trend: str = "NEUTRAL",
        strategy_mode: str = "HYBRID_AUTO",
        signal_mode: str = "CLOSED_CANDLE"
    ) -> List[Dict[str, Any]]:
        """
        Evaluates 6 Dark Venus Bollinger Band strategy modes independently.
        Returns a list of setup dictionaries containing signal details and explainable reasoning.
        """
        setups = []
        if df.empty or len(df) < 20:
            return setups

        # Target candle is iloc[-1] (target candle prepared by decision_engine according to signal_mode)
        target_idx = -1
        latest = df.iloc[target_idx]
        prev = df.iloc[target_idx - 1] if len(df) >= 2 else latest

        close = float(latest["close"])
        high = float(latest["high"])
        low = float(latest["low"])
        open_p = float(latest["open"])

        prev_close = float(prev["close"])

        bb_upper = float(latest.get("bb_upper", close))
        bb_middle = float(latest.get("bb_middle", close))
        bb_lower = float(latest.get("bb_lower", close))
        bb_width = float(latest.get("bb_width", 0.0) or 0.0)
        percent_b = float(latest.get("percent_b", 0.5) or 0.5)
        bb_squeeze = bool(latest.get("bb_squeeze", False))
        bb_expansion = bool(latest.get("bb_expansion", False))
        prev_width = float(prev.get("bb_width", bb_width) or bb_width)

        prev_bb_middle = float(prev.get("bb_middle", bb_middle))

        rsi = float(latest.get("rsi_14", 50.0) or 50.0)
        macd_hist = float(latest.get("macd_hist", 0.0) or 0.0)
        rvol = float(latest.get("rvol", 1.0) or 1.0)
        atr = float(latest.get("atr_14", 0.15) or 0.15)
        body_len = abs(close - open_p)
        candle_len = high - low or 1e-9
        body_ratio = body_len / candle_len

        # -------------------------------------------------------------
        # MODE 1: COUNTER-TREND BAND REVERSION (Mean Reversion)
        # -------------------------------------------------------------
        m1_active = False
        m1_side = TradeSide.HOLD
        m1_conf = 0.0
        m1_reason = "No Counter-Trend BB Reversion"

        # REGIME GUARD: Strict block against fading directional trends!
        is_downtrend = "DOWNTREND" in regime or h1_trend == "BEARISH"
        is_uptrend = "UPTREND" in regime or h1_trend == "BULLISH"

        if strategy_mode in ("HYBRID_AUTO", "BB_MEAN_REVERSION"):
            # Bullish Reversion: Low touches/breaks lower band + bullish rejection (STRICT BLOCK in Downtrend)
            if low <= bb_lower or prev_close <= float(prev.get("bb_lower", close)):
                is_bull_rejection = close > open_p or (open_p - low) > body_len * 1.5
                if is_bull_rejection and percent_b <= 0.20 and not is_downtrend:
                    m1_active = True
                    m1_side = TradeSide.BUY
                    m1_conf = 0.82
                    m1_reason = f"Mode 1: Counter-Trend Lower BB Reversion (%B={percent_b:.2f}, Regime={regime})"

            # Bearish Reversion: High touches/breaks upper band + bearish rejection (STRICT BLOCK in Uptrend)
            elif high >= bb_upper or prev_close >= float(prev.get("bb_upper", close)):
                is_bear_rejection = close < open_p or (high - open_p) > body_len * 1.5
                if is_bear_rejection and percent_b >= 0.80 and not is_uptrend:
                    m1_active = True
                    m1_side = TradeSide.SELL
                    m1_conf = 0.82
                    m1_reason = f"Mode 1: Counter-Trend Upper BB Reversion (%B={percent_b:.2f}, Regime={regime})"

        setups.append({
            "id": "dv_mode_1",
            "name": "BB Counter-Trend Reversion",
            "active": m1_active,
            "side": m1_side,
            "confidence": m1_conf,
            "reason": m1_reason,
            "type": "reversal"
        })

        # -------------------------------------------------------------
        # MODE 2: BAND BREAKOUT / MOMENTUM
        # -------------------------------------------------------------
        m2_active = False
        m2_side = TradeSide.HOLD
        m2_conf = 0.0
        m2_reason = "No BB Momentum Breakout"

        if strategy_mode in ("HYBRID_AUTO", "BB_BREAKOUT"):
            if close > bb_upper and body_ratio > 0.55 and rvol > 1.3 and rsi > 55 and not is_downtrend:
                m2_active = True
                m2_side = TradeSide.BUY
                m2_conf = 0.86
                m2_reason = f"Mode 2: Upper BB Momentum Breakout (BodyRatio={body_ratio:.0%}, RVOL={rvol:.2f})"
            elif close < bb_lower and body_ratio > 0.55 and rvol > 1.3 and rsi < 45 and not is_uptrend:
                m2_active = True
                m2_side = TradeSide.SELL
                m2_conf = 0.86
                m2_reason = f"Mode 2: Lower BB Momentum Breakout (BodyRatio={body_ratio:.0%}, RVOL={rvol:.2f})"

        setups.append({
            "id": "dv_mode_2",
            "name": "BB Band Breakout / Momentum",
            "active": m2_active,
            "side": m2_side,
            "confidence": m2_conf,
            "reason": m2_reason,
            "type": "trend_following"
        })

        # -------------------------------------------------------------
        # MODE 3: TREND-FOLLOWING BAND INTERACTION
        # -------------------------------------------------------------
        m3_active = False
        m3_side = TradeSide.HOLD
        m3_conf = 0.0
        m3_reason = "No Trend-Following BB Interaction"

        if strategy_mode in ("HYBRID_AUTO", "BB_TREND_FOLLOWING"):
            if h1_trend == "BULLISH" and low <= bb_middle and not is_downtrend:
                if close > open_p:
                    m3_active = True
                    m3_side = TradeSide.BUY
                    m3_conf = 0.84
                    m3_reason = "Mode 3: Bullish BB Band Interaction in H1 Bullish Trend"
            elif h1_trend == "BEARISH" and high >= bb_middle and not is_uptrend:
                if close < open_p:
                    m3_active = True
                    m3_side = TradeSide.SELL
                    m3_conf = 0.84
                    m3_reason = "Mode 3: Bearish BB Band Interaction in H1 Bearish Trend"

        setups.append({
            "id": "dv_mode_3",
            "name": "BB Trend-Following Interaction",
            "active": m3_active,
            "side": m3_side,
            "confidence": m3_conf,
            "reason": m3_reason,
            "type": "trend_following"
        })

        # -------------------------------------------------------------
        # MODE 4: CENTRAL BAND TREND CROSS
        # -------------------------------------------------------------
        m4_active = False
        m4_side = TradeSide.HOLD
        m4_conf = 0.0
        m4_reason = "No Central Band Trend Cross"

        if strategy_mode in ("HYBRID_AUTO", "BB_TREND_FOLLOWING"):
            # Bullish Cross above Middle Band
            if prev_close <= prev_bb_middle and close > bb_middle and rsi > 50 and macd_hist > 0 and not is_downtrend:
                m4_active = True
                m4_side = TradeSide.BUY
                m4_conf = 0.83
                m4_reason = "Mode 4: Central BB Middle Band Bullish Cross"
            # Bearish Cross below Middle Band
            elif prev_close >= prev_bb_middle and close < bb_middle and rsi < 50 and macd_hist < 0 and not is_uptrend:
                m4_active = True
                m4_side = TradeSide.SELL
                m4_conf = 0.83
                m4_reason = "Mode 4: Central BB Middle Band Bearish Cross"

        setups.append({
            "id": "dv_mode_4",
            "name": "BB Central Band Cross",
            "active": m4_active,
            "side": m4_side,
            "confidence": m4_conf,
            "reason": m4_reason,
            "type": "trend_following"
        })

        # -------------------------------------------------------------
        # MODE 5: PULLBACK TO BOLLINGER MIDLINE
        # -------------------------------------------------------------
        m5_active = False
        m5_side = TradeSide.HOLD
        m5_conf = 0.0
        m5_reason = "No Pullback to BB Midline"

        if strategy_mode in ("HYBRID_AUTO", "BB_TREND_FOLLOWING"):
            if ("UPTREND" in regime or h1_trend == "BULLISH") and not is_downtrend:
                if abs(low - bb_middle) <= 1.2 * atr or low <= bb_middle:
                    if close > open_p and bb_width >= 0.0015:
                        m5_active = True
                        m5_side = TradeSide.BUY
                        m5_conf = 0.85
                        m5_reason = "Mode 5: Pullback Rejection at BB Middle Band in Bullish Structure"
            elif ("DOWNTREND" in regime or h1_trend == "BEARISH") and not is_uptrend:
                if abs(high - bb_middle) <= 1.2 * atr or high >= bb_middle:
                    if close < open_p and bb_width >= 0.0015:
                        m5_active = True
                        m5_side = TradeSide.SELL
                        m5_conf = 0.85
                        m5_reason = "Mode 5: Pullback Rejection at BB Middle Band in Bearish Structure"

        setups.append({
            "id": "dv_mode_5",
            "name": "BB Midline Pullback",
            "active": m5_active,
            "side": m5_side,
            "confidence": m5_conf,
            "reason": m5_reason,
            "type": "trend_following"
        })

        # -------------------------------------------------------------
        # MODE 6: BAND SQUEEZE -> EXPANSION BREAKOUT
        # -------------------------------------------------------------
        m6_active = False
        m6_side = TradeSide.HOLD
        m6_conf = 0.0
        m6_reason = "No BB Squeeze Expansion Breakout"

        if strategy_mode in ("HYBRID_AUTO", "BB_BREAKOUT"):
            if (bb_squeeze or bb_width < prev_width) and bb_expansion:
                if close > bb_upper and rvol > 1.4 and not is_downtrend:
                    m6_active = True
                    m6_side = TradeSide.BUY
                    m6_conf = 0.88
                    m6_reason = f"Mode 6: BB Squeeze Volatility Expansion Bullish Breakout (RVOL={rvol:.2f})"
                elif close < bb_lower and rvol > 1.4 and not is_uptrend:
                    m6_active = True
                    m6_side = TradeSide.SELL
                    m6_conf = 0.88
                    m6_reason = f"Mode 6: BB Squeeze Volatility Expansion Bearish Breakout (RVOL={rvol:.2f})"

        setups.append({
            "id": "dv_mode_6",
            "name": "BB Squeeze Expansion Breakout",
            "active": m6_active,
            "side": m6_side,
            "confidence": m6_conf,
            "reason": m6_reason,
            "type": "breakout"
        })

        return setups


dark_venus_engine = DarkVenusStyleBBEngine()
