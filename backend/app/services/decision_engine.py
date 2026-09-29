"""
Multi-Confirmation Decision Engine & Explainable AI Module
Optimized specifically for XAUUSD / GOLD scalping.
Aggregates Market Structure, Trend, Momentum, Wyckoff Context, Heuristics, AI Predictions & Volatility-based Spread Model.
"""

import logging
import pandas as pd
from datetime import datetime, time
from typing import List, Dict, Tuple, Optional, Any

from app.core.config import settings
from app.domain.models import (
    Candle, TickerData, TradeSide, TradeDecisionReport,
    ConfirmationBreakdown, ConditionCheckItem, MarketStructureAnalysis, HeuristicScoringResult
)
from app.services.indicators import IndicatorService
from app.services.market_structure import MarketStructureEngine
from app.services.heuristic_scorer import heuristic_scorer
from app.services.news_filter import news_filter
from app.services.trade_learner import trade_learner
from app.adapters.mt5_adapter import mt5_adapter
from app.services.ai_predictor import ai_predictor
from app.services.dark_venus_bb import dark_venus_engine
from app.services.wyckoff_engine import wyckoff_engine
from app.services.signal_history import signal_history

logger = logging.getLogger("ApexTrader.DecisionEngine")


class DecisionEngine:
    """
    Production-Grade XAUUSD Gold Specialist Trading Decision Engine.
    Executes multi-timeframe regime, structure, setup, Wyckoff context, and risk checks.
    """
    def __init__(self):
        self.spread_buffer = []
        self._htf_cache = {}

    def detect_market_regime(self, df) -> str:
        """
        Detects distinct market regimes using EMA, ATR, ADX, and price action.
        """
        if df.empty or len(df) < 20:
            return "CHOPPY"
            
        latest = df.iloc[-1]
        close = float(latest["close"])
        ema20 = float(latest.get("ema_20", close))
        ema50 = float(latest.get("ema_50", close))
        ema200 = float(latest.get("ema_200", close))
        atr = float(latest.get("atr_14", 0.0) or 0.0)
        adx = float(latest.get("adx", 20.0) or 20.0)
        plus_di = float(latest.get("plus_di", 20.0) or 20.0)
        minus_di = float(latest.get("minus_di", 20.0) or 20.0)
        supertrend_dir = bool(latest.get("supertrend_dir", True))
        
        # Calculate ATR rolling mean
        if len(df) >= 35:
            atr_history = df["atr_14"].dropna()
            avg_atr = atr_history.iloc[-20:].mean() if len(atr_history) >= 20 else atr
        else:
            avg_atr = atr

        is_high_vol = atr > avg_atr * 1.4 if avg_atr > 0 else False
        is_low_vol = atr < avg_atr * 0.7 if avg_atr > 0 else False
        
        # Choppy crossings check
        crossings = 0
        if len(df) >= 15:
            last_15 = df.iloc[-15:]
            for idx in range(1, len(last_15)):
                c_prev = last_15.iloc[idx - 1]["close"]
                c_curr = last_15.iloc[idx]["close"]
                e_prev = last_15.iloc[idx - 1].get("ema_50", c_prev)
                e_curr = last_15.iloc[idx].get("ema_50", c_curr)
                if (c_prev > e_prev and c_curr < e_curr) or (c_prev < e_prev and c_curr > e_curr):
                    crossings += 1
        is_choppy = crossings >= 4

        if is_choppy:
            return "CHOPPY"
            
        # Reversal check
        rsi = float(latest.get("rsi_14", 50.0))
        prev = df.iloc[-2] if len(df) > 1 else latest
        prev_st = bool(prev.get("supertrend_dir", True))
        if supertrend_dir != prev_st:
            return "REVERSAL"
        if (rsi > 70 and close < float(latest["open"])) or (rsi < 30 and close > float(latest["open"])):
            return "REVERSAL"

        # Breakout check
        bb_upper = float(latest.get("bb_upper", close))
        bb_lower = float(latest.get("bb_lower", close))
        if close > bb_upper or close < bb_lower:
            return "BREAKOUT"

        # Trend regimes
        if close > ema20 > ema50 > ema200:
            return "STRONG_UPTREND" if (adx > 25 and plus_di > minus_di) else "WEAK_UPTREND"
                
        if close < ema20 < ema50 < ema200:
            return "STRONG_DOWNTREND" if (adx > 25 and minus_di > plus_di) else "WEAK_DOWNTREND"

        if close > ema50 and ema20 > ema50:
            return "WEAK_UPTREND"
        if close < ema50 and ema20 < ema50:
            return "WEAK_DOWNTREND"
            
        if is_high_vol:
            return "HIGH_VOLATILITY"
        if is_low_vol:
            return "LOW_VOLATILITY"
            
        return "RANGE"

    def get_active_sessions(self, dt: datetime) -> List[str]:
        """
        Determines active trading sessions based on time (UTC).
        """
        t = dt.time()
        sessions = []
        if time(0, 0) <= t < time(8, 0):
            sessions.append("ASIAN")
        if time(8, 0) <= t < time(16, 0):
            sessions.append("LONDON")
        if time(13, 0) <= t < time(21, 0):
            sessions.append("NEWYORK")
        if time(13, 0) <= t < time(16, 0):
            sessions.append("LONDON_NY_OVERLAP")
        return sessions

    def evaluate_setups(
        self, symbol: str, candles: List[Candle], ticker: TickerData, timeframe: str, htf_candles: Optional[Dict[str, List[Candle]]], df: Any, structure: MarketStructureAnalysis, regime: str
    ) -> List[Dict[str, Any]]:
        """
        Evaluates Multi-Setup Architecture combining Dark Venus BB Engine, SMC Structure,
        Liquidity Sweeps, Breakout Retest, EMA Trend Pullbacks, and Momentum.
        """
        setups = []
        if df.empty or len(df) < 10:
            return setups

        from app.services.trade_manager import trade_manager
        bb_period_val = getattr(trade_manager.risk_settings, "bb_period", 20)
        bb_std_val = getattr(trade_manager.risk_settings, "bb_std", 2.0)
        df_bb = dark_venus_engine.calculate_bollinger_features(df, period=bb_period_val, std_dev=bb_std_val)
        latest = df_bb.iloc[-1]
        prev = df_bb.iloc[-2] if len(df_bb) >= 2 else latest

        close = float(latest["close"])
        high = float(latest["high"])
        low = float(latest["low"])
        open_p = float(latest["open"])

        bb_upper = float(latest.get("bb_upper", close))
        bb_middle = float(latest.get("bb_middle", close))
        bb_lower = float(latest.get("bb_lower", close))
        bb_squeeze = bool(latest.get("bb_squeeze", False))
        bb_expansion = bool(latest.get("bb_expansion", False))
        percent_b = float(latest.get("percent_b", 0.5) or 0.5)

        prev_close = float(prev["close"])
        prev_middle = float(prev.get("bb_middle", bb_middle))

        ema9 = float(latest.get("ema_9", close))
        ema20 = float(latest.get("ema_20", close))
        ema50 = float(latest.get("ema_50", close))
        atr = float(latest.get("atr_14", 0.15) or 0.15)
        rvol = float(latest.get("rvol", 1.0) or 1.0)
        rsi = float(latest.get("rsi_14", 50.0) or 50.0)
        macd_hist = float(latest.get("macd_hist", 0.0) or 0.0)
        digits = ticker.digits or 2
        point = ticker.point_size or 0.01

        # Fetch HTF trend (cached by latest H1 candle timestamp)
        h1_trend = "NEUTRAL"
        if htf_candles and "H1" in htf_candles and len(htf_candles["H1"]) >= 20:
            h1_last_ts = htf_candles["H1"][-1].timestamp
            cache_key = ("H1_trend", h1_last_ts)
            if cache_key in self._htf_cache:
                h1_trend = self._htf_cache[cache_key]
            else:
                df_h1 = IndicatorService.calculate_all_indicators(htf_candles["H1"])
                h1_latest = df_h1.iloc[-1]
                h1_close = float(h1_latest["close"])
                h1_ema20 = float(h1_latest.get("ema_20", h1_close))
                h1_ema50 = float(h1_latest.get("ema_50", h1_close))
                h1_trend = "BULLISH" if h1_close > h1_ema20 >= h1_ema50 else ("BEARISH" if h1_close < h1_ema20 <= h1_ema50 else "NEUTRAL")
                self._htf_cache[cache_key] = h1_trend

        # -------------------------------------------------------------
        # SETUP A: DARK VENUS BOLLINGER BANDS (6 MODES)
        # -------------------------------------------------------------
        from app.services.trade_manager import trade_manager
        strat_profile = getattr(trade_manager.risk_settings, "strategy_profile", "HYBRID_AUTO")
        sig_mode = getattr(trade_manager.risk_settings, "signal_mode", "CLOSED_CANDLE")
        dv_setups = dark_venus_engine.evaluate_bb_signals(df_bb, regime, h1_trend, strategy_mode=strat_profile, signal_mode=sig_mode)
        setups.extend(dv_setups)

        # Cache M15 Market Structure evaluation
        struct_m15 = None
        if htf_candles and "M15" in htf_candles and len(htf_candles["M15"]) >= 10:
            m15_last_ts = htf_candles["M15"][-1].timestamp
            cache_key = ("M15_struct", m15_last_ts)
            if cache_key in self._htf_cache:
                struct_m15 = self._htf_cache[cache_key]
            else:
                struct_m15 = MarketStructureEngine.analyze(htf_candles["M15"], "M15", symbol)
                self._htf_cache[cache_key] = struct_m15

        if len(self._htf_cache) > 200:
            self._htf_cache.clear()

        # -------------------------------------------------------------
        # SETUP B: SMC PULLBACK (ORDER BLOCK / FVG REJECTION)
        # -------------------------------------------------------------
        if struct_m15 is not None:
            for ob in struct_m15.order_blocks:
                if ob.side == TradeSide.BUY and ob.bottom <= ticker.ask <= ob.top and low <= ob.top:
                    if close > open_p and h1_trend != "BEARISH" and "DOWNTREND" not in regime:
                        setups.append({
                            "id": "setup_smc_pullback_buy",
                            "name": "SETUP B: SMC Demand OB Pullback Rejection",
                            "active": True, "side": TradeSide.BUY, "confidence": 0.88,
                            "trigger": "M15 Demand OB Rejection with Bullish Candle Confirmation",
                            "invalidation": f"Below OB Bottom {ob.bottom:.2f}",
                            "reason": "SMC Demand Order Block Pullback Rejection",
                            "type": "pullback"
                        })
                        break
                elif ob.side == TradeSide.SELL and ob.bottom <= ticker.bid <= ob.top and high >= ob.bottom:
                    if close < open_p and h1_trend != "BULLISH" and "UPTREND" not in regime:
                        setups.append({
                            "id": "setup_smc_pullback_sell",
                            "name": "SETUP B: SMC Supply OB Pullback Rejection",
                            "active": True, "side": TradeSide.SELL, "confidence": 0.88,
                            "trigger": "M15 Supply OB Rejection with Bearish Candle Confirmation",
                            "invalidation": f"Above OB Top {ob.top:.2f}",
                            "reason": "SMC Supply Order Block Pullback Rejection",
                            "type": "pullback"
                        })
                        break

        # -------------------------------------------------------------
        # SETUP C: LIQUIDITY SWEEP REVERSAL (HTF & RSI Guarded)
        # DISABLED — negative expectancy across 65 combined live trades.
        # Re-enable via settings.SETUP_C_LIQUIDITY_SWEEP_ENABLED = True
        # (or env var SETUP_C_LIQUIDITY_SWEEP_ENABLED=true) after rework.
        # -------------------------------------------------------------
        if getattr(settings, "SETUP_C_LIQUIDITY_SWEEP_ENABLED", False) and struct_m15 is not None:
            if struct_m15.liquidity_sweeps:
                last_sweep = struct_m15.liquidity_sweeps[-1]
                if last_sweep.side == TradeSide.SELL and close > open_p:
                    if h1_trend in ("BULLISH", "NEUTRAL") and rsi > 35:
                        setups.append({
                            "id": "setup_liq_sweep_buy",
                            "name": "SETUP C: Sell-Side Liquidity Sweep Reversal",
                            "active": True, "side": TradeSide.BUY, "confidence": 0.90,
                            "trigger": "Price swept sell-side liquidity + bullish rejection",
                            "invalidation": f"Below Sweep Level {last_sweep.price_level:.2f}",
                            "reason": "Sell-Side Liquidity Grab Bullish Reversal",
                            "type": "reversal"
                        })
                elif last_sweep.side == TradeSide.BUY and close < open_p:
                    if h1_trend in ("BEARISH", "NEUTRAL") and rsi < 65:
                        setups.append({
                            "id": "setup_liq_sweep_sell",
                            "name": "SETUP C: Buy-Side Liquidity Sweep Reversal",
                            "active": True, "side": TradeSide.SELL, "confidence": 0.90,
                            "trigger": "Price swept buy-side liquidity + bearish rejection",
                            "invalidation": f"Above Sweep Level {last_sweep.price_level:.2f}",
                            "reason": "Buy-Side Liquidity Grab Bearish Reversal",
                            "type": "reversal"
                        })

        # -------------------------------------------------------------
        # SETUP D: BREAKOUT + RETEST
        # -------------------------------------------------------------
        if structure.latest_structure:
            struct_type = structure.latest_structure.type
            struct_price = structure.latest_structure.price
            if "BULLISH" in struct_type and abs(low - struct_price) <= 1.5 * atr and close > open_p:
                if h1_trend != "BEARISH" and "DOWNTREND" not in regime:
                    setups.append({
                        "id": "setup_breakout_retest_buy",
                        "name": "SETUP D: Bullish BOS Breakout & Retest",
                        "active": True, "side": TradeSide.BUY, "confidence": 0.86,
                        "trigger": "Retest rejection at broken resistance structure",
                        "invalidation": f"Below Structure Level {struct_price:.2f}",
                        "reason": "BOS Resistance Breakout and Retest",
                        "type": "breakout_retest"
                    })
            elif "BEARISH" in struct_type and abs(high - struct_price) <= 1.5 * atr and close < open_p:
                if h1_trend != "BULLISH" and "UPTREND" not in regime:
                    setups.append({
                        "id": "setup_breakout_retest_sell",
                        "name": "SETUP D: Bearish BOS Breakdown & Retest",
                        "active": True, "side": TradeSide.SELL, "confidence": 0.86,
                        "trigger": "Retest rejection at broken support structure",
                        "invalidation": f"Above Structure Level {struct_price:.2f}",
                        "reason": "BOS Support Breakdown and Retest",
                        "type": "breakout_retest"
                    })

        # -------------------------------------------------------------
        # SETUP E: EMA TREND PULLBACK
        # -------------------------------------------------------------
        if ema9 > ema20 and low <= ema20 <= high and close > open_p:
            if "DOWNTREND" not in regime and h1_trend != "BEARISH" and (struct_m15 is None or struct_m15.trend != "BEARISH"):
                setups.append({
                    "id": "setup_ema_pullback_buy",
                    "name": "SETUP E: EMA 9/20 Dynamic Trend Pullback",
                    "active": True, "side": TradeSide.BUY, "confidence": 0.85,
                    "trigger": "Pullback rejection at dynamic EMA 20 support",
                    "invalidation": f"Below EMA 50 {ema50:.2f}",
                    "reason": "Bullish EMA 9/20 Trend Pullback",
                    "type": "trend_following"
                })
        elif ema9 < ema20 and low <= ema20 <= high and close < open_p:
            if "UPTREND" not in regime and h1_trend != "BULLISH" and (struct_m15 is None or struct_m15.trend != "BULLISH"):
                setups.append({
                    "id": "setup_ema_pullback_sell",
                    "name": "SETUP E: EMA 9/20 Dynamic Trend Pullback",
                    "active": True, "side": TradeSide.SELL, "confidence": 0.85,
                    "trigger": "Pullback rejection at dynamic EMA 20 resistance",
                    "invalidation": f"Above EMA 50 {ema50:.2f}",
                    "reason": "Bearish EMA 9/20 Trend Pullback",
                    "type": "trend_following"
                })

        # -------------------------------------------------------------
        # SETUP F: MOMENTUM CONTINUATION
        # -------------------------------------------------------------
        if rvol > 1.3 and rsi > 55 and macd_hist > 0 and close > open_p:
            if "DOWNTREND" not in regime and h1_trend != "BEARISH" and (struct_m15 is None or struct_m15.trend != "BEARISH"):
                setups.append({
                    "id": "setup_momentum_buy",
                    "name": "SETUP F: Bullish Volume & Momentum Expansion",
                    "active": True, "side": TradeSide.BUY, "confidence": 0.84,
                    "trigger": "High RVOL + RSI & MACD Momentum Expansion",
                    "invalidation": f"Below Low {low:.2f}",
                    "reason": "Volume and Momentum Continuation",
                    "type": "momentum"
                })
        elif rvol > 1.3 and rsi < 45 and macd_hist < 0 and close < open_p:
            if "UPTREND" not in regime and h1_trend != "BULLISH" and (struct_m15 is None or struct_m15.trend != "BULLISH"):
                setups.append({
                    "id": "setup_momentum_sell",
                    "name": "SETUP F: Bearish Volume & Momentum Expansion",
                    "active": True, "side": TradeSide.SELL, "confidence": 0.84,
                    "trigger": "High RVOL + RSI & MACD Momentum Expansion",
                    "invalidation": f"Above High {high:.2f}",
                    "reason": "Volume and Momentum Continuation",
                    "type": "momentum"
                })

        disabled = getattr(trade_manager.risk_settings, "disabled_setups", [])
        if disabled:
            setups = [s for s in setups if s.get("name") not in disabled and s.get("id") not in disabled]

        return setups

    def evaluate_trade_opportunity(
        self,
        symbol: str,
        candles: List[Candle],
        ticker: TickerData,
        timeframe: str = "M1",
        htf_candles: Optional[Dict[str, List[Candle]]] = None,
        simulated_balance: Optional[float] = None,
        precomputed_df: Optional[pd.DataFrame] = None
    ) -> TradeDecisionReport:
        from app.services.trade_manager import trade_manager
        
        reasoning = []
        rejection_reasons = []
        
        # Determine signal mode (CURRENT_CANDLE vs CLOSED_CANDLE)
        sig_mode = getattr(trade_manager.risk_settings, "signal_mode", "CLOSED_CANDLE")
        
        if sig_mode == "CLOSED_CANDLE":
            analysis_candles = candles[:-1] if candles and not candles[-1].is_closed else candles
        else:
            analysis_candles = candles
            
        if len(analysis_candles) < 30:
            rejection_reasons.append(f"Insufficient candle history ({len(analysis_candles)}/30 required)")
            return self._build_rejection_report(symbol, timeframe, rejection_reasons, primary_reason="DATA_UNAVAILABLE")

        bb_period_val = getattr(trade_manager.risk_settings, "bb_period", 20)
        bb_std_val = getattr(trade_manager.risk_settings, "bb_std", 2.0)

        if precomputed_df is not None and not precomputed_df.empty:
            df = precomputed_df
        else:
            df = IndicatorService.calculate_all_indicators(analysis_candles, bb_period=bb_period_val, bb_std=bb_std_val)

        structure = MarketStructureEngine.analyze(analysis_candles, timeframe, symbol, df=df)
        pattern_name, pattern_bias = IndicatorService.detect_candlestick_pattern(analysis_candles)
        wyckoff_res = wyckoff_engine.analyze(df, timeframe="M15")

        latest = df.iloc[-1]
        close = float(latest["close"])
        atr = float(latest.get("atr_14", 0.15) or 0.15)
        digits = ticker.digits or 2
        point = ticker.point_size or 0.01

        # -------------------------------------------------------------
        # A. Spread & Volatility Model
        # -------------------------------------------------------------
        spread_price = ticker.ask - ticker.bid
        spread_pts = int(round(spread_price / point)) if point > 0 else 0
        spread_pips = round(spread_price / 0.10, 1)

        self.spread_buffer.append(spread_pips)
        if len(self.spread_buffer) > 100:
            self.spread_buffer.pop(0)

        avg_spread = sum(self.spread_buffer) / len(self.spread_buffer)
        sorted_spreads = sorted(self.spread_buffer)
        median_spread = sorted_spreads[len(sorted_spreads)//2]
        percentile_90 = sorted_spreads[int(len(sorted_spreads)*0.90)] if len(sorted_spreads) > 10 else spread_pips

        m1_atr = atr
        spread_atr_m1_ratio = spread_price / m1_atr if m1_atr > 0 else 0.0

        max_allowed_pips = getattr(trade_manager.risk_settings, "max_spread_pips", settings.MAX_SPREAD_PIPS)

        spread_excessive = False
        spread_status = "NORMAL"
        spread_block_reason = ""

        if spread_pips > max_allowed_pips:
            spread_excessive = True
            spread_status = "EXCESSIVE"
            spread_block_reason = f"Spread {spread_pips} pips exceeds config limit of {max_allowed_pips} pips"
        elif spread_atr_m1_ratio > 1.5:
            spread_excessive = True
            spread_status = "EXCESSIVE"
            spread_block_reason = f"Spread {spread_pips} pips abnormally high vs M1 volatility (Spread/ATR M1 = {spread_atr_m1_ratio:.2f} > 1.5)"
        elif len(self.spread_buffer) >= 15 and spread_pips > percentile_90 * 1.4:
            spread_excessive = True
            spread_status = "EXCESSIVE"
            spread_block_reason = f"Spread {spread_pips} pips exceeds 90th percentile ({percentile_90:.1f} pips)"
        elif spread_pips > median_spread * 1.5:
            spread_status = "HIGH"

        reasoning.append(f"[SPREAD VOLATILITY MODEL] Spread: {spread_pips} pips | Points: {spread_pts} | ATR(M1): {m1_atr:.2f} | Status: {spread_status}")

        # -------------------------------------------------------------
        # B. Regime Detection & Setup Evaluation
        # -------------------------------------------------------------
        regime = self.detect_market_regime(df)
        reasoning.append(f"[MARKET REGIME DETECTED] Current regime: {regime}")
        reasoning.append(f"[WYCKOFF CONTEXT] Schematic: {wyckoff_res.schematic} | Phase: {wyckoff_res.phase} | Bias: {wyckoff_res.bias} | Boost: +{int(wyckoff_res.confidence_boost*100)}%")

        active_setups = self.evaluate_setups(symbol, analysis_candles, ticker, timeframe, htf_candles, df, structure, regime)

        # -------------------------------------------------------------
        # C. Weighted Evidence Scoring Model (100% Total)
        # -------------------------------------------------------------
        # 1. Market Context:      20% (Macro H1 + Wyckoff)
        # 2. Market Structure:    20% (M15/M1 Trend & BOS)
        # 3. Entry Setup:        25% (Best Active Setup Confidence)
        # 4. Momentum:           10% (RSI & MACD)
        # 5. Volume:             10% (RVOL)
        # 6. MTF Alignment:      10% (Multi-timeframe confluence)
        # 7. Execution Quality:   5% (Session & Spread)
        # TOTAL = 100%

        h1_trend = "NEUTRAL"
        if htf_candles and "H1" in htf_candles and len(htf_candles["H1"]) >= 20:
            h1_last_ts = htf_candles["H1"][-1].timestamp
            cache_key = ("H1_trend", h1_last_ts)
            if cache_key in self._htf_cache:
                h1_trend = self._htf_cache[cache_key]
            else:
                df_h1 = IndicatorService.calculate_all_indicators(htf_candles["H1"], bb_period=bb_period_val, bb_std=bb_std_val)
                h1_latest = df_h1.iloc[-1]
                h1_close = float(h1_latest["close"])
                h1_ema20 = float(h1_latest.get("ema_20", h1_close))
                h1_ema50 = float(h1_latest.get("ema_50", h1_close))
                h1_trend = "BULLISH" if h1_close > h1_ema20 >= h1_ema50 else ("BEARISH" if h1_close < h1_ema20 <= h1_ema50 else "NEUTRAL")
                self._htf_cache[cache_key] = h1_trend

        h4_trend = "NEUTRAL"
        if htf_candles and "H4" in htf_candles and len(htf_candles["H4"]) >= 20:
            h4_last_ts = htf_candles["H4"][-1].timestamp
            cache_key = ("H4_trend", h4_last_ts)
            if cache_key in self._htf_cache:
                h4_trend = self._htf_cache[cache_key]
            else:
                df_h4 = IndicatorService.calculate_all_indicators(htf_candles["H4"], bb_period=bb_period_val, bb_std=bb_std_val)
                h4_latest = df_h4.iloc[-1]
                h4_close = float(h4_latest["close"])
                h4_ema20 = float(h4_latest.get("ema_20", h4_close))
                h4_ema50 = float(h4_latest.get("ema_50", h4_close))
                h4_trend = "BULLISH" if h4_close > h4_ema20 >= h4_ema50 else ("BEARISH" if h4_close < h4_ema20 <= h4_ema50 else "NEUTRAL")
                self._htf_cache[cache_key] = h4_trend

        buy_setups = [s for s in active_setups if s.get("active") and s.get("side") == TradeSide.BUY]
        sell_setups = [s for s in active_setups if s.get("active") and s.get("side") == TradeSide.SELL]

        best_buy_setup = max(buy_setups, key=lambda s: s.get("confidence", 0.0)) if buy_setups else None
        best_sell_setup = max(sell_setups, key=lambda s: s.get("confidence", 0.0)) if sell_setups else None

        best_buy_conf = best_buy_setup.get("confidence", 0.0) if best_buy_setup else 0.0
        best_sell_conf = best_sell_setup.get("confidence", 0.0) if best_sell_setup else 0.0

        is_buy_reversal = best_buy_setup and best_buy_setup.get("type") in ("reversal", "breakout_retest")
        is_sell_reversal = best_sell_setup and best_sell_setup.get("type") in ("reversal", "breakout_retest")

        # 1. Context Score (20 pts: Macro H1 + H4 + Wyckoff)
        base_long = 20.0 if h1_trend == "BULLISH" else (10.0 if h1_trend == "NEUTRAL" else 0.0)
        base_short = 20.0 if h1_trend == "BEARISH" else (10.0 if h1_trend == "NEUTRAL" else 0.0)

        if h4_trend == "BEARISH" and h1_trend == "BULLISH" and not is_buy_reversal:
            context_long = max(5.0, base_long - 7.5)
        else:
            context_long = base_long

        if h4_trend == "BULLISH" and h1_trend == "BEARISH" and not is_sell_reversal:
            context_short = max(5.0, base_short - 7.5)
        else:
            context_short = base_short

        if wyckoff_res.bias == "BULLISH":
            context_long = min(20.0, context_long + 5.0)
        elif wyckoff_res.bias == "BEARISH":
            context_short = min(20.0, context_short + 5.0)

        # 2. Structure Score (20 pts)
        struct_long = 20.0 if structure.trend == "BULLISH" else (10.0 if structure.trend == "NEUTRAL" else 0.0)
        struct_short = 20.0 if structure.trend == "BEARISH" else (10.0 if structure.trend == "NEUTRAL" else 0.0)

        # 3. Entry Setup Score (25 pts)
        setup_long = best_buy_conf * 25.0
        setup_short = best_sell_conf * 25.0

        # 4. Momentum Score (10 pts)
        rsi = float(latest.get("rsi_14", 50.0))
        macd_hist = float(latest.get("macd_hist", 0.0))
        mom_long = 10.0 if rsi > 50 and macd_hist > 0 else (5.0 if rsi > 50 or macd_hist > 0 else 0.0)
        mom_short = 10.0 if rsi < 50 and macd_hist < 0 else (5.0 if rsi < 50 or macd_hist < 0 else 0.0)

        # 5. Volume Score (10 pts)
        rvol = float(latest.get("rvol", 1.0) or 1.0)
        vol_long = 10.0 if rvol > 1.3 else (6.0 if rvol >= 1.0 else 3.0)
        vol_short = 10.0 if rvol > 1.3 else (6.0 if rvol >= 1.0 else 3.0)

        # 6. MTF Alignment Score (10 pts)
        mtf_long = 10.0 if h1_trend == "BULLISH" and structure.trend == "BULLISH" else (5.0 if is_buy_reversal else 2.5)
        mtf_short = 10.0 if h1_trend == "BEARISH" and structure.trend == "BEARISH" else (5.0 if is_sell_reversal else 2.5)

        # 7. Execution Quality Score (5 pts)
        active_sessions = self.get_active_sessions(datetime.now())
        session_score = 5.0 if any(s in active_sessions for s in ["LONDON", "NEWYORK", "LONDON_NY_OVERLAP"]) else 2.5
        spread_score = 0.0 if spread_excessive else 5.0
        exec_quality = (session_score + spread_score) / 2.0

        raw_long = context_long + struct_long + setup_long + mom_long + vol_long + mtf_long + exec_quality
        raw_short = context_short + struct_short + setup_short + mom_short + vol_short + mtf_short + exec_quality

        if wyckoff_res.bias == "BULLISH":
            raw_long += wyckoff_res.confidence_boost * 100.0
        elif wyckoff_res.bias == "BEARISH":
            raw_short += wyckoff_res.confidence_boost * 100.0

        LONG_SCORE = min(1.0, raw_long / 100.0)
        SHORT_SCORE = min(1.0, raw_short / 100.0)

        # AI Predictor Model Ensemble (Confidence-Gated Veto Logic)
        p_buy, p_sell, p_no_trade, ml_status = ai_predictor.predict(symbol, df, htf_candles)
        if ai_predictor.model is not None:
            ml_conf = max(p_buy, p_sell)
            ml_dir = "BUY" if p_buy > p_sell else ("SELL" if p_sell > p_buy else "NEUTRAL")
            rule_dir = "BUY" if LONG_SCORE > SHORT_SCORE and LONG_SCORE >= 0.40 else ("SELL" if SHORT_SCORE > LONG_SCORE and SHORT_SCORE >= 0.40 else "NEUTRAL")
            margin = abs(p_buy - p_sell)
            
            if ml_conf >= 0.45 and margin >= 0.15 and ml_dir != "NEUTRAL" and rule_dir != "NEUTRAL":
                if ml_dir != rule_dir:
                    penalty = 0.15 + (margin * 0.20)
                    if rule_dir == "BUY":
                        LONG_SCORE = max(0.0, LONG_SCORE - penalty)
                    elif rule_dir == "SELL":
                        SHORT_SCORE = max(0.0, SHORT_SCORE - penalty)
                    logger.info(f"[DECISION ENGINE] ML Veto Penalty (-{penalty:.4f}) applied. Rule: {rule_dir}, ML: {ml_dir} (conf: {ml_conf:.2f}, margin: {margin:.2f})")

        # Regime-Aware Dynamic Confidence Thresholds
        regime_thresholds = {
            "STRONG_UPTREND": getattr(settings, "MIN_CONFIDENCE_STRONG_TREND", 0.60),
            "STRONG_DOWNTREND": getattr(settings, "MIN_CONFIDENCE_STRONG_TREND", 0.60),
            "WEAK_UPTREND": getattr(settings, "MIN_CONFIDENCE_NORMAL", 0.65),
            "WEAK_DOWNTREND": getattr(settings, "MIN_CONFIDENCE_NORMAL", 0.65),
            "RANGE": getattr(settings, "MIN_CONFIDENCE_RANGE", 0.70),
            "CHOPPY": getattr(settings, "MIN_CONFIDENCE_RANGE", 0.70),
            "REVERSAL": getattr(settings, "MIN_CONFIDENCE_REVERSAL", 0.70),
            "BREAKOUT": getattr(settings, "MIN_CONFIDENCE_NORMAL", 0.65),
            "HIGH_VOLATILITY": getattr(settings, "MIN_CONFIDENCE_VOLATILITY", 0.70),
            "LOW_VOLATILITY": getattr(settings, "MIN_CONFIDENCE_NORMAL", 0.65),
        }
        min_conf_required = regime_thresholds.get(regime, getattr(trade_learner.get_effective_thresholds(), "min_confidence", 0.65))

        # AI Trading Strategy Profile Threshold Adjustment (CONSERVATIVE: 78%+, BALANCED: 72%+, AGGRESSIVE: 65%+)
        profile_mode = getattr(trade_manager.risk_settings, "trading_mode_profile", "BALANCED").upper()
        if profile_mode == "CONSERVATIVE":
            min_conf_required = max(min_conf_required, 0.78)
        elif profile_mode == "BALANCED":
            min_conf_required = max(min_conf_required, 0.72)
        elif profile_mode == "AGGRESSIVE":
            min_conf_required = min(min_conf_required, 0.65)

        candidate_side = TradeSide.HOLD
        if LONG_SCORE >= min_conf_required and LONG_SCORE > SHORT_SCORE and len(buy_setups) > 0:
            candidate_side = TradeSide.BUY
        elif SHORT_SCORE >= min_conf_required and SHORT_SCORE > LONG_SCORE and len(sell_setups) > 0:
            candidate_side = TradeSide.SELL

        # -------------------------------------------------------------
        # HTF (H1 + M15) Alignment Hard Gate (Opt-in via config / risk_settings)
        # -------------------------------------------------------------
        htf_filter_enabled = getattr(trade_manager.risk_settings, "htf_alignment_filter_enabled", getattr(settings, "HTF_ALIGNMENT_FILTER_ENABLED", False))
        htf_mode = getattr(trade_manager.risk_settings, "htf_alignment_mode", getattr(settings, "HTF_ALIGNMENT_MODE", "MODERATE")).upper()

        htf_blocked = False
        htf_block_reason = ""
        if htf_filter_enabled and candidate_side in (TradeSide.BUY, TradeSide.SELL):
            m15_trend = structure.trend
            if candidate_side == TradeSide.BUY:
                if htf_mode == "STRICT":
                    if not (h1_trend == "BULLISH" and m15_trend == "BULLISH"):
                        htf_blocked = True
                        htf_block_reason = f"HTF Alignment (STRICT) blocked BUY: H1={h1_trend}, M15={m15_trend} (both must be BULLISH)"
                else:  # MODERATE
                    if h1_trend == "BEARISH" or m15_trend == "BEARISH":
                        htf_blocked = True
                        htf_block_reason = f"HTF Alignment (MODERATE) blocked BUY: H1={h1_trend}, M15={m15_trend} (opposing BEARISH trend)"
            elif candidate_side == TradeSide.SELL:
                if htf_mode == "STRICT":
                    if not (h1_trend == "BEARISH" and m15_trend == "BEARISH"):
                        htf_blocked = True
                        htf_block_reason = f"HTF Alignment (STRICT) blocked SELL: H1={h1_trend}, M15={m15_trend} (both must be BEARISH)"
                else:  # MODERATE
                    if h1_trend == "BULLISH" or m15_trend == "BULLISH":
                        htf_blocked = True
                        htf_block_reason = f"HTF Alignment (MODERATE) blocked SELL: H1={h1_trend}, M15={m15_trend} (opposing BULLISH trend)"

            if htf_blocked:
                candidate_side = TradeSide.HOLD

        # -------------------------------------------------------------
        # D. Separate Risk Gates & Structured Decision Logic
        # -------------------------------------------------------------
        primary_reason = "NONE"
        secondary_reasons = []

        if htf_blocked:
            secondary_reasons.append(htf_block_reason)

        # 1. Spread Check Gate
        spread_passed = not spread_excessive
        if not spread_passed:
            secondary_reasons.append(spread_block_reason)

        # 2. News Blackout Check Gate
        current_time = getattr(ticker, 'timestamp', None)
        is_news_blackout, news_msg = news_filter.is_news_blackout(symbol, current_time=current_time)
        news_passed = not is_news_blackout
        if not news_passed:
            secondary_reasons.append(news_msg)

        # 3. Dynamic Stop Loss & Take Profit & Risk/Reward Calculation
        effective_thresholds = trade_learner.get_effective_thresholds()
        required_rr = effective_thresholds["min_rr_ratio"]
        base_sl_distance = max(40 * point, atr * 2.5)
        sl_distance = base_sl_distance

        if candidate_side == TradeSide.BUY:
            entry_price = ticker.ask
            bullish_obs = [ob for ob in structure.order_blocks if ob.side == TradeSide.BUY and ob.bottom < entry_price]
            if bullish_obs:
                closest_ob = max(bullish_obs, key=lambda ob: ob.bottom)
                ob_sl_dist = entry_price - closest_ob.bottom + (10 * point)
                if base_sl_distance <= ob_sl_dist <= max(250 * point, atr * 3.0):
                    sl_distance = ob_sl_dist
        elif candidate_side == TradeSide.SELL:
            entry_price = ticker.bid
            bearish_obs = [ob for ob in structure.order_blocks if ob.side == TradeSide.SELL and ob.top > entry_price]
            if bearish_obs:
                closest_ob = min(bearish_obs, key=lambda ob: ob.top)
                ob_sl_dist = closest_ob.top - entry_price + (10 * point)
                if base_sl_distance <= ob_sl_dist <= max(250 * point, atr * 3.0):
                    sl_distance = ob_sl_dist

        tp_distance = sl_distance * required_rr

        if candidate_side == TradeSide.BUY:
            entry_price = ticker.ask
            targets = []
            bearish_obs = [ob for ob in structure.order_blocks if ob.side == TradeSide.SELL and ob.bottom > entry_price]
            if bearish_obs:
                closest_bear_ob = min(bearish_obs, key=lambda ob: ob.bottom)
                targets.append(closest_bear_ob.bottom - entry_price)
            if structure.value_area_high > entry_price:
                targets.append(structure.value_area_high - entry_price)
            if structure.poc_price > entry_price:
                targets.append(structure.poc_price - entry_price)
            targets.append(atr * 3.5)
            valid_targets = [t for t in targets if t > 30 * point]
            if valid_targets:
                tp_distance = max(min(valid_targets), sl_distance * required_rr)
        elif candidate_side == TradeSide.SELL:
            entry_price = ticker.bid
            targets = []
            bullish_obs = [ob for ob in structure.order_blocks if ob.side == TradeSide.BUY and ob.top < entry_price]
            if bullish_obs:
                closest_bull_ob = max(bullish_obs, key=lambda ob: ob.top)
                targets.append(entry_price - closest_bull_ob.top)
            if structure.value_area_low < entry_price:
                targets.append(entry_price - structure.value_area_low)
            if structure.poc_price < entry_price:
                targets.append(entry_price - structure.poc_price)
            targets.append(atr * 3.5)
            valid_targets = [t for t in targets if t > 30 * point]
            if valid_targets:
                tp_distance = max(min(valid_targets), sl_distance * required_rr)

        rr_ratio = round(tp_distance / sl_distance, 2) if sl_distance > 0 else required_rr
        rr_passed = rr_ratio >= required_rr
        if not rr_passed:
            secondary_reasons.append(f"Risk:Reward ratio 1:{rr_ratio} < required 1:{required_rr}")

        # Account Loss / Risk Limits Check
        if simulated_balance is not None:
            acc_balance = simulated_balance
        else:
            acc_info = mt5_adapter.get_account_info()
            acc_balance = getattr(acc_info, 'balance', 10000.0) if acc_info else 10000.0

        # Determine Final Decision & Structured Primary Reason
        final_decision = TradeSide.HOLD

        if htf_blocked:
            primary_reason = "HTF_ALIGNMENT_BLOCKED"
        elif not spread_passed:
            primary_reason = "SPREAD_BLOCKED"
        elif not news_passed:
            primary_reason = "NEWS_BLOCKED"
        elif candidate_side in (TradeSide.BUY, TradeSide.SELL):
            if not rr_passed:
                primary_reason = "RR_BLOCKED"
            else:
                final_decision = candidate_side
                primary_reason = f"{candidate_side.value}_READY"
        else:
            if len(active_setups) == 0:
                primary_reason = "NO_SETUP"
                secondary_reasons.append("Scanning live market structure & indicators for qualified setup...")
            elif max(LONG_SCORE, SHORT_SCORE) < min_conf_required:
                primary_reason = "LOW_CONFIDENCE"
                secondary_reasons.append(f"Signal score {round(max(LONG_SCORE, SHORT_SCORE)*100,1)}% is below required {round(min_conf_required*100,1)}% threshold ({regime})")
            else:
                primary_reason = "NO_SETUP"
                secondary_reasons.append("Scanning live market structure & indicators for qualified setup...")

        active_score = LONG_SCORE if candidate_side == TradeSide.BUY else (SHORT_SCORE if candidate_side == TradeSide.SELL else max(LONG_SCORE, SHORT_SCORE))

        # Record signal evaluation history
        best_setup_name = (best_buy_setup.get("name") if candidate_side == TradeSide.BUY and best_buy_setup else (best_sell_setup.get("name") if candidate_side == TradeSide.SELL and best_sell_setup else "None"))
        signal_history.record_evaluation(
            symbol=symbol,
            direction=candidate_side.value,
            best_setup=best_setup_name,
            regime=regime,
            buy_score=LONG_SCORE,
            sell_score=SHORT_SCORE,
            confidence=active_score,
            spread_pips=spread_pips,
            rr_ratio=rr_ratio,
            news_status=news_filter.get_news_status(),
            decision=final_decision.value,
            primary_reason=primary_reason,
            secondary_reasons=secondary_reasons
        )

        # Build condition matrix
        condition_matrix = [
            ConditionCheckItem(id="entry_trigger", name="Primary Setup Entry Trigger Active", category="Trigger", passed=len(active_setups) > 0, details=f"Active Setups: {[s.get('name') for s in active_setups if s.get('active')]}", weight=0.25, score_contribution=0.25 if len(active_setups) > 0 else 0.0, is_critical=True),
            ConditionCheckItem(id="confidence_score", name="Regime Confidence Score Threshold", category="Confidence", passed=active_score >= min_conf_required, details=f"Score: {round(active_score*100,1)}% vs Required {round(min_conf_required*100,1)}% ({regime})", weight=0.25, score_contribution=0.25 if active_score >= min_conf_required else 0.0, is_critical=True),
            ConditionCheckItem(id="spread_filter", name="Spread Volatility Filter", category="Risk", passed=spread_passed, details=f"Spread: {spread_pips} pips | Status: {spread_status}", weight=0.15, score_contribution=0.15 if spread_passed else 0.0, is_critical=True),
            ConditionCheckItem(id="news_filter", name="Calendar High-Impact News Filter", category="Risk", passed=news_passed, details=news_msg, weight=0.15, score_contribution=0.15 if news_passed else 0.0, is_critical=True),
            ConditionCheckItem(id="risk_reward", name="Minimum Risk/Reward Ratio Gate", category="Risk", passed=rr_passed, details=f"Calculated R:R: 1:{rr_ratio} | Required: 1:{required_rr}", weight=0.20, score_contribution=0.20 if rr_passed else 0.0, is_critical=True)
        ]

        breakdown = ConfirmationBreakdown(
            trend_score_long=round(context_long / 20.0, 4),
            trend_score_short=round(context_short / 20.0, 4),
            market_structure_score_long=round(struct_long / 20.0, 4),
            market_structure_score_short=round(struct_short / 20.0, 4),
            momentum_volume_score_long=round(mom_long / 10.0, 4),
            momentum_volume_score_short=round(mom_short / 10.0, 4),
            heuristic_score_long=round(LONG_SCORE, 4),
            heuristic_score_short=round(SHORT_SCORE, 4),
            risk_news_score=round(1.0 if spread_passed and news_passed else 0.0, 4),
            long_score=round(LONG_SCORE, 4),
            short_score=round(SHORT_SCORE, 4),
            passed=final_decision in (TradeSide.BUY, TradeSide.SELL),
            trend_score=round(max(context_long, context_short) / 20.0, 4),
            market_structure_score=round(max(struct_long, struct_short) / 20.0, 4),
            momentum_volume_score=round(max(mom_long, mom_short) / 10.0, 4),
            heuristic_score=round(max(LONG_SCORE, SHORT_SCORE), 4),
            total_score=round(active_score, 4)
        )

        outweighing_factors = []
        if candidate_side == TradeSide.BUY and best_buy_setup:
            outweighing_factors.append(f"Active Buy Setup ({best_buy_setup.get('name')})")
            outweighing_factors.append(f"Wyckoff Context: {wyckoff_res.reason}")
        elif candidate_side == TradeSide.SELL and best_sell_setup:
            outweighing_factors.append(f"Active Sell Setup ({best_sell_setup.get('name')})")
            outweighing_factors.append(f"Wyckoff Context: {wyckoff_res.reason}")

        summary_explanation = f"=== GOLD AI DECISION TRACE ===\n"
        summary_explanation += f"Symbol: {symbol} | Time: {datetime.now().strftime('%H:%M:%S')}\n"
        summary_explanation += f"Regime: {regime}\n"
        summary_explanation += f"H1 Context: {h1_trend}\n"
        summary_explanation += f"M15 Structure: {structure.trend}\n"
        summary_explanation += f"Wyckoff Context: {wyckoff_res.schematic} ({wyckoff_res.phase}) | {wyckoff_res.reason}\n"
        summary_explanation += f"Active Setups: {[s.get('name') for s in active_setups if s.get('active')] or ['None']}\n"
        summary_explanation += f"Spread Status: {spread_status} ({spread_pips} pips)\n"
        summary_explanation += f"LONG SCORE: {round(LONG_SCORE*100, 1)}% | SHORT SCORE: {round(SHORT_SCORE*100, 1)}% (Threshold: {round(min_conf_required*100, 1)}%)\n"

        if final_decision in (TradeSide.BUY, TradeSide.SELL):
            summary_explanation += f"\nWHY {final_decision.value}?\n"
            summary_explanation += f"Because:\n"
            summary_explanation += f"  1. Macro H1 Trend = {h1_trend}\n"
            summary_explanation += f"  2. M15 Structure = {structure.trend}\n"
            summary_explanation += f"  3. Active Setup = {[s.get('name') for s in (buy_setups if final_decision == TradeSide.BUY else sell_setups)]}\n"
            summary_explanation += f"  4. Wyckoff Context = {wyckoff_res.reason}\n"
            summary_explanation += f"  5. Target R:R = 1:{rr_ratio}\n"
            summary_explanation += f"  6. Final Confidence = {round(active_score*100, 1)}%\n"
            summary_explanation += f"Therefore: EXECUTE {final_decision.value}"
        else:
            summary_explanation += f"\nWHY HOLD?\n"
            summary_explanation += f"Primary State: {primary_reason}\n"
            if secondary_reasons:
                summary_explanation += "Blocking Reasons:\n"
                for i, sr in enumerate(secondary_reasons, 1):
                    summary_explanation += f"  {i}. {sr}\n"
            else:
                summary_explanation += "Reason: No active setup triggered or score below regime threshold.\n"
            summary_explanation += f"Result: HOLD — Engine waiting for qualified setup."

        sl_pips = sl_distance / 0.10
        calc_lots = trade_manager.calculate_risk_based_lot_size(symbol, getattr(trade_manager.risk_settings, 'max_risk_per_trade_pct', 0.5), sl_pips, acc_balance)

        if final_decision == TradeSide.BUY:
            suggested_entry = round(ticker.ask, digits)
            suggested_sl = round(suggested_entry - sl_distance, digits)
            suggested_tp = round(suggested_entry + tp_distance, digits)
        elif final_decision == TradeSide.SELL:
            suggested_entry = round(ticker.bid, digits)
            suggested_sl = round(suggested_entry + sl_distance, digits)
            suggested_tp = round(suggested_entry - tp_distance, digits)
        else:
            suggested_entry = round(ticker.ask, digits)
            suggested_sl = 0.0
            suggested_tp = 0.0

        if final_decision in (TradeSide.BUY, TradeSide.SELL):
            logger.info(
                f"[EXECUTION TRACE] SIGNAL={final_decision.value} | "
                f"MODEL SCORE={round(active_score*100, 1)}% | "
                f"SPREAD=PASS ({spread_pips}p) | RR={rr_ratio} | LOT={calc_lots} | "
                f"SL={suggested_sl} | TP={suggested_tp} | ORDER=SENT"
            )

        liquidity_status = "Liquidity Grab Confirmed" if pattern_bias != "NEUTRAL" or "Sweep" in str(outweighing_factors) else "Normal Market Liquidity"

        from app.services.symbol_specs import calculate_potential_pnl_usd

        if final_decision == TradeSide.BUY and best_buy_setup:
            active_setup_name = best_buy_setup.get("name", "Unknown Setup")
        elif final_decision == TradeSide.SELL and best_sell_setup:
            active_setup_name = best_sell_setup.get("name", "Unknown Setup")
        elif candidate_side == TradeSide.BUY and best_buy_setup:
            active_setup_name = best_buy_setup.get("name", "Unknown Setup")
        elif candidate_side == TradeSide.SELL and best_sell_setup:
            active_setup_name = best_sell_setup.get("name", "Unknown Setup")
        else:
            active_setup_name = "None"

        eval_side = final_decision if final_decision in (TradeSide.BUY, TradeSide.SELL) else candidate_side
        pot_loss_usd = calculate_potential_pnl_usd(symbol, eval_side, suggested_entry, suggested_sl, calc_lots) if suggested_sl > 0 else 0.0
        pot_profit_usd = calculate_potential_pnl_usd(symbol, eval_side, suggested_entry, suggested_tp, calc_lots) if suggested_tp > 0 else 0.0

        return TradeDecisionReport(
            symbol=symbol,
            decision=final_decision,
            timeframe=timeframe,
            confidence=round(active_score, 4),
            confirmation_score=round(active_score, 4),
            long_score=round(LONG_SCORE, 4),
            short_score=round(SHORT_SCORE, 4),
            reasoning=reasoning,
            rejection_reasons=[primary_reason] + secondary_reasons,
            suggested_entry=suggested_entry,
            suggested_sl=suggested_sl,
            suggested_tp=suggested_tp,
            suggested_lots=calc_lots,
            risk_reward_ratio=rr_ratio,
            breakdown=breakdown,
            condition_matrix=condition_matrix,
            htf_alignment={"H1": h1_trend, "H4": h4_trend},
            candlestick_pattern=f"{pattern_name} ({pattern_bias})",
            liquidity_status=liquidity_status,
            estimated_holding_bars=6,
            summary_explanation=summary_explanation,
            outweighing_factors=outweighing_factors,
            setup_name=active_setup_name,
            potential_loss_usd=pot_loss_usd,
            potential_profit_usd=pot_profit_usd,
            timestamp=datetime.now()
        )

    def _build_rejection_report(self, symbol: str, timeframe: str, reasons: List[str], primary_reason: str = "NO_SETUP") -> TradeDecisionReport:
        breakdown = ConfirmationBreakdown(passed=False)
        return TradeDecisionReport(
            symbol=symbol,
            decision=TradeSide.HOLD,
            timeframe=timeframe,
            confidence=0.0,
            confirmation_score=0.0,
            long_score=0.0,
            short_score=0.0,
            reasoning=[],
            rejection_reasons=[primary_reason] + reasons,
            suggested_entry=0.0,
            suggested_sl=0.0,
            suggested_tp=0.0,
            suggested_lots=0.0,
            risk_reward_ratio=0.0,
            breakdown=breakdown,
            condition_matrix=[],
            htf_alignment={},
            candlestick_pattern="None",
            liquidity_status="Normal",
            estimated_holding_bars=6,
            summary_explanation=f"Rejection: {primary_reason} - {', '.join(reasons)}",
            outweighing_factors=[],
            timestamp=datetime.now()
        )


decision_engine = DecisionEngine()
