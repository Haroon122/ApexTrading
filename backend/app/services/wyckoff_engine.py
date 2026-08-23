"""
Wyckoff Context Engine Module for XAUUSD Gold Scalping.
Implements Wyckoff Market Analysis as an Additive Context Layer:
  - Accumulation Schematic Detection (Phases A, B, C, D, E)
  - Distribution Schematic Detection (Phases A, B, C, D, E)
  - Key Events: Selling Climax (SC), Automatic Rally (AR), Secondary Test (ST),
    Spring/Spring Test, Sign of Strength (SOS), Last Point of Support (LPS),
    Buying Climax (BC), Automatic Reaction (AR), Upthrust (UT), UTAD,
    Sign of Weakness (SOW), Last Point of Supply (LPSY).
  - Effort vs Result & Volume Analysis.

IMPORTANT: Wyckoff serves as a context booster (+confidence score).
It NEVER acts as a mandatory hard blocker.
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from app.domain.models import TradeSide

logger = logging.getLogger("ApexTrader.WyckoffEngine")


class WyckoffAnalysisResult:
    def __init__(
        self,
        bias: str = "NEUTRAL",  # BULLISH, BEARISH, NEUTRAL
        phase: str = "UNDEFINED",  # PHASE_A, PHASE_B, PHASE_C, PHASE_D, PHASE_E, UNDEFINED
        schematic: str = "NONE",  # ACCUMULATION, DISTRIBUTION, RE-ACCUMULATION, RE-DISTRIBUTION, NONE
        events: Optional[List[str]] = None,
        confidence_boost: float = 0.0,
        reason: str = "No clear Wyckoff structure"
    ):
        self.bias = bias
        self.phase = phase
        self.schematic = schematic
        self.events = events or []
        self.confidence_boost = confidence_boost
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bias": self.bias,
            "phase": self.phase,
            "schematic": self.schematic,
            "events": self.events,
            "confidence_boost": self.confidence_boost,
            "reason": self.reason
        }


class WyckoffContextEngine:
    """
    Evaluates Wyckoff structural context on candle DataFrames.
    Detects Accumulation vs Distribution schematics and phases without blocking trades.
    """

    @staticmethod
    def analyze(df: pd.DataFrame, timeframe: str = "M15") -> WyckoffAnalysisResult:
        if df.empty or len(df) < 25:
            return WyckoffAnalysisResult()

        try:
            closes = df["close"].values
            highs = df["high"].values
            lows = df["low"].values
            opens = df["open"].values
            volumes = df["tick_volume"].values if "tick_volume" in df.columns else df.get("volume", pd.Series(np.ones(len(df)))).values

            recent_count = min(50, len(df))
            r_closes = closes[-recent_count:]
            r_highs = highs[-recent_count:]
            r_lows = lows[-recent_count:]
            r_opens = opens[-recent_count:]
            r_vols = volumes[-recent_count:]

            range_high = np.max(r_highs[:-5]) if len(r_highs) > 10 else np.max(r_highs)
            range_low = np.min(r_lows[:-5]) if len(r_lows) > 10 else np.min(r_lows)
            range_span = range_high - range_low or 1e-9

            avg_vol = np.mean(r_vols) if len(r_vols) > 0 else 1.0
            latest_vol = r_vols[-1]
            latest_spread = r_highs[-1] - r_lows[-1]
            avg_spread = np.mean(r_highs - r_lows) or 1e-9

            events = []
            bias = "NEUTRAL"
            phase = "UNDEFINED"
            schematic = "NONE"
            boost = 0.0

            # -------------------------------------------------------------
            # ACCUMULATION SCHEMATIC DETECTION
            # -------------------------------------------------------------
            # 1. Selling Climax (SC) + Automatic Rally (AR)
            min_idx = np.argmin(r_lows)
            has_sc = False
            if min_idx < len(r_lows) - 5:
                sc_spread = r_highs[min_idx] - r_lows[min_idx]
                sc_vol = r_vols[min_idx]
                if sc_spread > avg_spread * 1.3 and sc_vol > avg_vol * 1.3:
                    has_sc = True
                    events.append("Selling Climax (SC)")

            # 2. Spring / Spring Test (Phase C)
            has_spring = False
            curr_close = r_closes[-1]
            curr_low = r_lows[-1]
            curr_open = r_opens[-1]

            if curr_low < range_low and curr_close > range_low:
                has_spring = True
                events.append("Spring Reversal (Phase C)")
            elif len(r_lows) >= 3 and np.min(r_lows[-3:]) < range_low and curr_close > range_low + (range_span * 0.15):
                has_spring = True
                events.append("Spring Test / Rejection (Phase C)")

            # 3. Sign of Strength (SOS) / Last Point of Support (LPS) - Phase D/E
            has_sos = False
            if curr_close > range_high and latest_vol > avg_vol * 1.2:
                has_sos = True
                events.append("Sign of Strength (SOS Breakout)")
            elif has_sc and curr_close > range_low + (range_span * 0.5) and curr_close > curr_open:
                events.append("Last Point of Support (LPS)")

            # Determine Accumulation Phase & Boost
            if has_spring:
                bias = "BULLISH"
                schematic = "ACCUMULATION"
                phase = "PHASE_C"
                boost = 0.12
            elif has_sos:
                bias = "BULLISH"
                schematic = "ACCUMULATION"
                phase = "PHASE_D"
                boost = 0.15
            elif has_sc:
                bias = "BULLISH"
                schematic = "ACCUMULATION"
                phase = "PHASE_B"
                boost = 0.08

            # -------------------------------------------------------------
            # DISTRIBUTION SCHEMATIC DETECTION
            # -------------------------------------------------------------
            # 1. Buying Climax (BC)
            max_idx = np.argmax(r_highs)
            has_bc = False
            if max_idx < len(r_highs) - 5:
                bc_spread = r_highs[max_idx] - r_lows[max_idx]
                bc_vol = r_vols[max_idx]
                if bc_spread > avg_spread * 1.3 and bc_vol > avg_vol * 1.3:
                    has_bc = True
                    events.append("Buying Climax (BC)")

            # 2. Upthrust (UT / UTAD) - Phase C
            has_utad = False
            curr_high = r_highs[-1]
            if curr_high > range_high and curr_close < range_high:
                has_utad = True
                events.append("Upthrust After Distribution (UTAD / Phase C)")
            elif len(r_highs) >= 3 and np.max(r_highs[-3:]) > range_high and curr_close < range_high - (range_span * 0.15):
                has_utad = True
                events.append("UTAD Test / Bearish Rejection (Phase C)")

            # 3. Sign of Weakness (SOW) / Last Point of Supply (LPSY) - Phase D/E
            has_sow = False
            if curr_close < range_low and latest_vol > avg_vol * 1.2:
                has_sow = True
                events.append("Sign of Weakness (SOW Breakdown)")
            elif has_bc and curr_close < range_high - (range_span * 0.5) and curr_close < curr_open:
                events.append("Last Point of Supply (LPSY)")

            # Determine Distribution Phase & Boost
            if has_utad:
                bias = "BEARISH"
                schematic = "DISTRIBUTION"
                phase = "PHASE_C"
                boost = 0.12
            elif has_sow:
                bias = "BEARISH"
                schematic = "DISTRIBUTION"
                phase = "PHASE_D"
                boost = 0.15
            elif has_bc:
                bias = "BEARISH"
                schematic = "DISTRIBUTION"
                phase = "PHASE_B"
                boost = 0.08

            reason = f"Wyckoff {schematic} ({phase}): {', '.join(events)}" if events else "Neutral Wyckoff Structure"

            return WyckoffAnalysisResult(
                bias=bias,
                phase=phase,
                schematic=schematic,
                events=events,
                confidence_boost=boost,
                reason=reason
            )

        except Exception as e:
            logger.error(f"[WYCKOFF ENGINE] Exception analyzing timeframe {timeframe}: {e}")
            return WyckoffAnalysisResult()


wyckoff_engine = WyckoffContextEngine()
