"""
Heuristic Signal Scoring Engine Module
Calculates a rule-based signal score based on Trend, Momentum, and Volatility heuristics.
Works without numpy/pandas via pure-Python fallback.
"""

import os
import math
import logging
from datetime import datetime
from typing import List, Dict, Tuple, Any, Optional

from app.core.config import settings
from app.domain.models import Candle, HeuristicScoringResult, TradeSide
from app.services.indicators import IndicatorService

# Optional numpy
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

logger = logging.getLogger("ApexTrader.HeuristicScorer")


class HeuristicSignalScorer:
    """
    Rule-Based Heuristic Signal Scorer.
    Combines weighted technical indicators (trend, momentum, and volume) into a heuristic score.
    """

    def __init__(self):
        # We reuse the backward-compatible configuration weights mapping:
        # - "xgboost": Trend heuristics contribution
        # - "lightgbm": Momentum heuristics contribution
        # - "pytorch": Composite/Volatility/Composite heuristics contribution
        # Note: This is a pure rule-based heuristic scorer and no ML models are run.
        self.weights = settings.ENSEMBLE_WEIGHTS
        self.min_score_threshold = settings.MIN_AI_CONFIDENCE

    def calculate_score(self, candles: List[Candle], timeframe: str = "M15", symbol: str = "EURUSD", df: Optional[Any] = None) -> HeuristicScoringResult:
        if len(candles) < 30:
            return HeuristicScoringResult(
                symbol=symbol,
                timeframe=timeframe,
                buy_score=0.33,
                sell_score=0.33,
                confidence_score=0.33,
                expected_movement_pips=0.0,
                expected_holding_bars=4,
                top_factors={"Insufficient_Data": 1.0},
                timestamp=datetime.now()
            )

        if df is None:
            df = IndicatorService.calculate_all_indicators(candles)

        # 1. Feature / Indicator extraction
        features, factor_dict = self._extract_indicators(df)

        # 2. Individual Heuristic Evaluators
        trend_buy, trend_sell = self._evaluate_trend_heuristics(features)
        mom_buy, mom_sell   = self._evaluate_momentum_heuristics(features)
        comp_buy, comp_sell  = self._evaluate_composite_heuristics(features)

        # 3. Weighted Heuristic Fusion
        buy_score = (
            self.weights.get("xgboost", 0.40) * trend_buy +
            self.weights.get("lightgbm", 0.35) * mom_buy +
            self.weights.get("pytorch", 0.25) * comp_buy
        )
        sell_score = (
            self.weights.get("xgboost", 0.40) * trend_sell +
            self.weights.get("lightgbm", 0.35) * mom_sell +
            self.weights.get("pytorch", 0.25) * comp_sell
        )

        # Normalize scores
        total_score = buy_score + sell_score + 1e-9
        buy_score = buy_score / total_score
        sell_score = sell_score / total_score

        # Neutral Filter: If directional edge is weak (<15% margin) or score low (<55%), declare NEUTRAL
        margin = abs(buy_score - sell_score)
        if margin < 0.15 or max(buy_score, sell_score) < 0.55:
            buy_score = 0.333
            sell_score = 0.333
            confidence = 0.333
        else:
            confidence = float(max(buy_score, sell_score))

        # Expected movement from ATR
        # Use iloc[-1] if it's a pandas DataFrame or list-access fallback
        if hasattr(df, "iloc"):
            last = df.iloc[-1]
            atr = float(last.get("atr_14", 0.0015) or 0.0015)
        else:
            atr = float(df["atr_14"][-1] if df.get("atr_14") and df["atr_14"][-1] is not None else 0.0015)

        from app.services.symbol_specs import get_symbol_specs
        pip_size = get_symbol_specs(symbol)["pip_size"]
        expected_pips = round((atr * 2.0) / pip_size, 1)

        top_factors = self._calculate_factor_contributions(factor_dict, buy_score, sell_score)

        return HeuristicScoringResult(
            symbol=symbol,
            timeframe=timeframe,
            buy_score=round(buy_score, 3),
            sell_score=round(sell_score, 3),
            confidence_score=round(confidence, 3),
            expected_movement_pips=expected_pips,
            expected_holding_bars=6,
            top_factors=top_factors,
            timestamp=datetime.now()
        )

    def _extract_indicators(self, df) -> Tuple[List[float], Dict[str, float]]:
        if hasattr(df, "iloc"):
            latest = df.iloc[-1]
        else:
            latest = {k: v[-1] for k, v in df.items() if isinstance(v, list) and len(v) > 0}

        rsi       = float(latest.get("rsi_14",     50.0) if latest.get("rsi_14") is not None else 50.0)
        stoch_k   = float(latest.get("stoch_rsi_k", 50.0) if latest.get("stoch_rsi_k") is not None else 50.0)
        macd_hist = float(latest.get("macd_hist",   0.0) if latest.get("macd_hist") is not None else 0.0)
        adx       = float(latest.get("adx",         20.0) if latest.get("adx") is not None else 20.0)
        rvol      = float(latest.get("rvol",          1.0) if latest.get("rvol") is not None else 1.0)
        close     = float(latest.get("close",         1.0) if latest.get("close") is not None else 1.0)
        ema20     = float(latest.get("ema_20",      close) if latest.get("ema_20") is not None else close)
        ema50     = float(latest.get("ema_50",      close) if latest.get("ema_50") is not None else close)
        ema200    = float(latest.get("ema_200",     close) if latest.get("ema_200") is not None else close)
        vwap_val  = float(latest.get("vwap",        close) if latest.get("vwap") is not None else close)

        ema_diff_20_50  = (ema20  - ema50)  / (close or 1.0)
        ema_diff_50_200 = (ema50  - ema200) / (close or 1.0)
        dist_to_vwap    = (close  - vwap_val) / (close or 1.0)

        def clip(v, lo, hi):
            return max(lo, min(hi, v))

        factor_dict = {
            "RSI_14":          clip(rsi / 100.0, 0.0, 1.0),
            "Stoch_RSI_K":     clip(stoch_k / 100.0, 0.0, 1.0),
            "MACD_Hist_Norm":  clip(macd_hist * 1000.0, -1.0, 1.0),
            "ADX_Trend_Strength": clip(adx / 50.0, 0.0, 1.0),
            "Relative_Volume": clip(rvol / 3.0, 0.0, 1.0),
            "EMA_20_50_Spread":  clip(ema_diff_20_50  * 1000.0, -1.0, 1.0),
            "EMA_50_200_Spread": clip(ema_diff_50_200 * 1000.0, -1.0, 1.0),
            "Dist_to_VWAP":    clip(dist_to_vwap * 1000.0, -1.0, 1.0),
        }

        factors = list(factor_dict.values())
        return factors, factor_dict

    def _evaluate_trend_heuristics(self, factors: List[float]) -> Tuple[float, float]:
        rsi         = factors[0]
        macd        = factors[2]
        ema20_50    = factors[5]
        ema50_200   = factors[6]

        # Trend alignment is primary filter
        trend_bullish = 1.0 if (ema20_50 > 0 and ema50_200 > 0) else (0.4 if ema20_50 > 0 else 0.0)
        trend_bearish = 1.0 if (ema20_50 < 0 and ema50_200 < 0) else (0.4 if ema20_50 < 0 else 0.0)

        # Momentum confirmation
        mom_bullish = 0.5 if (rsi > 0.50 and macd > 0) else (0.2 if macd > 0 else 0.0)
        mom_bearish = 0.5 if (rsi < 0.50 and macd < 0) else (0.2 if macd < 0 else 0.0)

        # Reversal only counted if MACD histogram confirms pivot
        rev_bullish = 0.3 if (rsi < 0.35 and macd > 0) else 0.0
        rev_bearish = 0.3 if (rsi > 0.65 and macd < 0) else 0.0

        bullish = trend_bullish * 0.50 + mom_bullish * 0.35 + rev_bullish * 0.15
        bearish = trend_bearish * 0.50 + mom_bearish * 0.35 + rev_bearish * 0.15

        return float(bullish), float(bearish)

    def _evaluate_momentum_heuristics(self, factors: List[float]) -> Tuple[float, float]:
        stoch     = factors[1]
        adx       = factors[3]
        vwap_dist = factors[7]

        adx_weight = min(1.0, adx * 1.5)

        bullish = (0.4 if vwap_dist > 0 else 0.0) + (0.3 if stoch > 0.5 else 0.0) + (0.3 * adx_weight if vwap_dist > 0 else 0.0)
        bearish = (0.4 if vwap_dist < 0 else 0.0) + (0.3 if stoch < 0.5 else 0.0) + (0.3 * adx_weight if vwap_dist < 0 else 0.0)

        return float(bullish), float(bearish)

    def _evaluate_composite_heuristics(self, factors: List[float]) -> Tuple[float, float]:
        weights_bullish = [0.15, 0.10, 0.20, 0.05, 0.05, 0.20, 0.20, 0.05]
        weights_bearish = [-0.15, -0.10, -0.20, 0.05, 0.05, -0.20, -0.20, -0.05]

        score_bull = sum(f * w for f, w in zip(factors, weights_bullish))
        score_bear = sum(f * w for f, w in zip(factors, weights_bearish))

        exp_b = math.exp(max(-5.0, min(5.0, score_bull * 3.0)))
        exp_s = math.exp(max(-5.0, min(5.0, score_bear * 3.0)))
        tot   = exp_b + exp_s + 1e-9

        return float(exp_b / tot), float(exp_s / tot)

    def _calculate_factor_contributions(
        self, factor_dict: Dict[str, float], buy_score: float, sell_score: float
    ) -> Dict[str, float]:
        attributions = {}
        for k, v in factor_dict.items():
            attributions[k] = round(float(abs(v - 0.5) * 2.0), 3)

        sorted_attr = sorted(attributions.items(), key=lambda item: item[1], reverse=True)[:4]
        return dict(sorted_attr)


# Global Heuristic Scorer Singleton Instance
heuristic_scorer = HeuristicSignalScorer()
