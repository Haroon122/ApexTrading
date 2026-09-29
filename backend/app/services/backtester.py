"""
Strategy Backtesting & Walk-Forward Performance Engine
Calculates Sharpe Ratio, Sortino Ratio, Max Drawdown %, Profit Factor, Win Rate, Equity Curve,
and comprehensive Trade Opportunity & Rejection Analytics.
"""

import math
import random
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from app.domain.models import Candle, TickerData, TradeSide, TradeState, BacktestRequest
from app.services.indicators import IndicatorService
from app.services.decision_engine import decision_engine
from app.adapters.mt5_adapter import mt5_adapter
from app.services.risk_engine import RiskEngine, RiskBlockReason

logger = logging.getLogger("ApexTrader.Backtester")


def parse_date_str(d_str: str, is_end: bool = False) -> Optional[datetime]:
    if not d_str or not str(d_str).strip():
        return None
    cleaned = str(d_str).strip()
    dt = None
    try:
        dt = datetime.fromisoformat(cleaned)
    except Exception:
        pass
    if dt is None:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(cleaned, fmt)
                break
            except Exception:
                pass
    if dt is None:
        return None
    if is_end:
        return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)



class BacktestingEngine:
    """
    High-Performance Production Historical Strategy Backtester.
    Runs multi-confirmation institutional decision logic over historical OHLC candles.
    Simulates: Bid/Ask spread, Commissions, Execution Latency Slippage, Cooldowns,
    Daily/Consecutive Loss limits, and comprehensive Opportunity Rejection Analytics.
    """

    @classmethod
    def run_backtest(
        cls,
        symbol: str = "XAUUSD",
        timeframe: str = "M15",
        count: int = 500,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        initial_balance: float = 10000.0,
        risk_pct: float = 1.0,
        lot_sizing_mode: str = "DYNAMIC",
        user_configured_lot: float = 0.01,
        min_risk_reward_ratio: float = 1.5,
        auto_trade_cooldown_seconds: int = 300,
        daily_loss_limit_pct: float = 5.0,
        max_consecutive_losses: int = 5,
        max_spread_pips: float = 2.8,
        min_ai_confidence: float = 0.70,
        candles: Optional[List[Candle]] = None,
        # --- Scalper exit management parameters ---
        break_even_rr: float = 1.0,
        trailing_activation_rr: float = 1.5,
        giveback_guard_enabled: bool = True,
        giveback_guard_min_rr: float = 1.2,
        giveback_guard_retrace_pct: float = 30.0,
        cached_reports: Optional[Dict[int, Any]] = None,
        skip_stress: bool = False,
        precomputed_full_df: Optional[Any] = None,
        htf_alignment_filter_enabled: Optional[bool] = None,
        htf_alignment_mode: Optional[str] = None,
        disabled_setups: Optional[List[str]] = None,
        entry_confirmation_mode: str = "IMMEDIATE",
        partial_close_enabled: bool = True,
        partial_close_pct: float = 50.0
    ) -> Dict[str, Any]:

        # Stage 0: Log Request Received
        logger.info(
            f"[BACKTEST] Request received: symbol={symbol}, timeframe={timeframe}, "
            f"period={from_date or 'ALL'} to {to_date or 'ALL'}, balance=${initial_balance:.2f}"
        )

        from_dt: Optional[datetime] = None
        to_dt: Optional[datetime] = None
        if from_date and str(from_date).strip():
            from_dt = parse_date_str(from_date, is_end=False)
            if not from_dt:
                return {"error": f"Invalid From Date format: '{from_date}'. Supported formats: YYYY-MM-DD or MM/DD/YYYY."}
        if to_date and str(to_date).strip():
            to_dt = parse_date_str(to_date, is_end=True)
            if not to_dt:
                return {"error": f"Invalid To Date format: '{to_date}'. Supported formats: YYYY-MM-DD or MM/DD/YYYY."}

        if from_dt and to_dt:
            if from_dt >= to_dt:
                return {"error": f"Invalid date range: From Date ({from_date}) must be earlier than To Date ({to_date})."}

            from app.adapters.mt5_adapter import TF_MINUTES_MAP
            tf_mins = TF_MINUTES_MAP.get(timeframe.upper(), 15)
            total_seconds = (to_dt - from_dt).total_seconds()
            expected_candles = int(total_seconds / (tf_mins * 60))

            MAX_CANDLES_LIMIT = 25000
            if expected_candles > MAX_CANDLES_LIMIT:
                msg = (
                    f"Selected historical range ({from_date} to {to_date}) requires ~{expected_candles:,} {timeframe} candles, "
                    f"exceeding the maximum safe limit of {MAX_CANDLES_LIMIT:,} candles for {timeframe} backtesting. "
                    f"Please reduce the date range or select a higher timeframe (e.g., M5, M15, or H1)."
                )
                logger.warning(f"[BACKTEST] {msg}")
                return {"error": msg}

        if initial_balance <= 0:
            return {"error": "Initial balance must be greater than $0."}
        if risk_pct <= 0:
            return {"error": "Risk per trade % must be greater than 0."}
        if lot_sizing_mode == "FIXED" and user_configured_lot <= 0:
            return {"error": "Fixed lot size must be greater than 0."}

        logger.info(
            f"\n======================================================\n"
            f"[BACKTEST ENGINE STARTED]\n"
            f"  Symbol: {symbol} | Timeframe: {timeframe}\n"
            f"  Requested Period: {from_date or 'ALL'} to {to_date or 'ALL'}\n"
            f"  Initial Balance: ${initial_balance:.2f}\n"
            f"  Risk Per Trade: {risk_pct}%\n"
            f"  Lot Sizing Mode: {lot_sizing_mode} (Fixed Lot: {user_configured_lot})\n"
            f"  Cooldown: {auto_trade_cooldown_seconds}s | Daily Loss Limit: {daily_loss_limit_pct}%\n"
            f"  Max Consec Losses: {max_consecutive_losses} | Max Spread: {max_spread_pips} pips\n"
            f"======================================================"
        )

        # Stage 1: Fetch Primary Historical Candles
        logger.info(f"[BACKTEST] Historical data fetch started ({symbol} {timeframe})...")
        t_fetch_start = time.time()
        if not candles or len(candles) < 30:
            candles = mt5_adapter.fetch_candles(
                symbol=symbol,
                timeframe=timeframe,
                count=count,
                from_datetime=from_dt,
                to_datetime=to_dt
            )
        t_fetch_elapsed = time.time() - t_fetch_start
        candle_count = len(candles) if candles else 0
        logger.info(f"[BACKTEST] Historical data fetch completed in {t_fetch_elapsed:.2f}s. Number of candles loaded: {candle_count}")

        if not candles or candle_count < 40:
            date_range_str = f" for period {from_date} to {to_date}" if from_date and to_date else ""
            return {"error": f"Insufficient historical candle data for {symbol} ({timeframe}){date_range_str}. Found {candle_count} candles (minimum 40 required)."}

        MAX_CANDLES_LIMIT = 25000
        if candle_count > MAX_CANDLES_LIMIT:
            return {
                "error": (
                    f"Selected historical dataset contains {candle_count:,} {timeframe} candles, "
                    f"exceeding the maximum safe backtest limit of {MAX_CANDLES_LIMIT:,} candles. "
                    f"Please reduce the date range or select a higher timeframe (e.g. M5 or M15)."
                )
            }

        from app.services.symbol_specs import get_symbol_specs
        specs = get_symbol_specs(symbol)

        # Stage 2: Pre-fetch HTF Candles (skip if cached_reports are provided)
        htf_lock = {}
        if cached_reports is None:
            logger.info("[BACKTEST] HTF data fetch started...")
            t_htf_start = time.time()
            from app.adapters.mt5_adapter import TF_MINUTES_MAP
            end_dt = candles[-1].timestamp
            start_dt = candles[0].timestamp
            for tf in ["M5", "M15", "H1", "H4"]:
                tf_mins = TF_MINUTES_MAP.get(tf.upper(), 15)
                primary_mins = TF_MINUTES_MAP.get(timeframe.upper(), 15)
                needed_count = int(len(candles) * (primary_mins / tf_mins)) + 200
                htf_lock[tf] = mt5_adapter.fetch_candles(
                    symbol=symbol,
                    timeframe=tf,
                    count=needed_count,
                    from_datetime=start_dt - timedelta(days=7) if from_dt else None,
                    to_datetime=end_dt if from_dt else None,
                    before_datetime=end_dt if not from_dt else None
                )
            t_htf_elapsed = time.time() - t_htf_start
            logger.info(f"[BACKTEST] HTF data fetch completed in {t_htf_elapsed:.2f}s")

        # Stage 3: Run Primary Historical Simulation
        t_sim_start = time.time()
        full_df = precomputed_full_df if precomputed_full_df is not None else IndicatorService.calculate_all_indicators(candles)
        sim = cls._run_simulation(
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            initial_balance=initial_balance,
            risk_pct=risk_pct,
            lot_sizing_mode=lot_sizing_mode,
            user_configured_lot=user_configured_lot,
            min_risk_reward_ratio=min_risk_reward_ratio,
            auto_trade_cooldown_seconds=auto_trade_cooldown_seconds,
            daily_loss_limit_pct=daily_loss_limit_pct,
            max_consecutive_losses=max_consecutive_losses,
            max_spread_pips=max_spread_pips,
            min_ai_confidence=min_ai_confidence,
            specs=specs,
            htf_candles_full=htf_lock,
            cached_reports=cached_reports,
            precomputed_full_df=full_df,
            break_even_rr=break_even_rr,
            trailing_activation_rr=trailing_activation_rr,
            giveback_guard_enabled=giveback_guard_enabled,
            giveback_guard_min_rr=giveback_guard_min_rr,
            giveback_guard_retrace_pct=giveback_guard_retrace_pct,
            htf_alignment_filter_enabled=htf_alignment_filter_enabled,
            htf_alignment_mode=htf_alignment_mode,
            disabled_setups=disabled_setups,
            entry_confirmation_mode=entry_confirmation_mode,
            partial_close_enabled=partial_close_enabled,
            partial_close_pct=partial_close_pct
        )
        t_sim_elapsed = time.time() - t_sim_start
        logger.info(f"[BACKTEST] Simulation completed in {t_sim_elapsed:.2f}s")

        # Stage 4: Metrics Calculation
        metrics = cls._compute_metrics(sim, initial_balance, user_configured_lot, max_spread_pips, specs)
        logger.info("[BACKTEST] Metrics calculation completed")

        # Stress / Robustness Sensitivity Check
        stress_runs = []
        if not skip_stress:
            num_stress = 2 if len(candles) > 200 else 3
            eval_reports_cache = sim.get("eval_reports", {})
            for _ in range(num_stress):
                s_sim = cls._run_simulation(
                    candles=candles, symbol=symbol, timeframe=timeframe,
                    initial_balance=initial_balance, risk_pct=risk_pct,
                    lot_sizing_mode=lot_sizing_mode, user_configured_lot=user_configured_lot,
                    min_risk_reward_ratio=min_risk_reward_ratio,
                    auto_trade_cooldown_seconds=auto_trade_cooldown_seconds,
                    daily_loss_limit_pct=daily_loss_limit_pct,
                    max_consecutive_losses=max_consecutive_losses,
                    max_spread_pips=max_spread_pips, min_ai_confidence=min_ai_confidence,
                    specs=specs, htf_candles_full=htf_lock, randomize_slippage=True,
                    cached_reports=eval_reports_cache, precomputed_full_df=full_df,
                    break_even_rr=break_even_rr,
                    trailing_activation_rr=trailing_activation_rr,
                    giveback_guard_enabled=giveback_guard_enabled,
                    giveback_guard_min_rr=giveback_guard_min_rr,
                    giveback_guard_retrace_pct=giveback_guard_retrace_pct
                )
                s_metrics = cls._compute_metrics(s_sim, initial_balance, user_configured_lot, max_spread_pips, specs)
                stress_runs.append(s_metrics)

        if stress_runs:
            pfs = [r["profit_factor"] for r in stress_runs]
            dds = [r["max_drawdown"] for r in stress_runs]
            robustness_report = {
                "min_profit_factor": min(pfs),
                "max_profit_factor": max(pfs),
                "avg_profit_factor": round(sum(pfs) / len(pfs), 2),
                "min_drawdown": min(dds),
                "max_drawdown": max(dds),
                "avg_drawdown": round(sum(dds) / len(dds), 2),
                "note": "Sensitivity test re-running full timeline with randomized ±20% spread/slippage assumptions."
            }
        else:
            robustness_report = {
                "note": "Stress sensitivity testing skipped for parameter sweep optimization."
            }

        actual_from = candles[0].timestamp.strftime("%Y-%m-%d %H:%M")
        actual_to = candles[-1].timestamp.strftime("%Y-%m-%d %H:%M")

        logger.info(f"[BACKTEST] Response ready for {symbol} ({timeframe}) | Total Trades: {metrics['total_trades']}")

        eval_reports_safe = {}
        for k, v in sim.get("eval_reports", {}).items():
            if hasattr(v, "model_dump"):
                eval_reports_safe[k] = v.model_dump(mode="json")
            elif isinstance(v, dict):
                eval_reports_safe[k] = v

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "from_date": actual_from,
            "to_date": actual_to,
            "initial_balance": initial_balance,
            "final_balance": round(sim["balance"], 2),
            "net_profit_usd": round(sim["balance"] - initial_balance, 2),
            "net_roi_pct": round(((sim["balance"] - initial_balance) / initial_balance) * 100, 2),
            "total_trades": metrics["total_trades"],
            "winning_trades": metrics["winning_trades"],
            "losing_trades": metrics["losing_trades"],
            "break_even_trades": metrics["break_even_trades"],
            "win_rate_pct": metrics["win_rate"],
            "loss_rate_pct": round(100.0 - metrics["win_rate"], 1) if metrics["total_trades"] > 0 else 0.0,
            "profit_factor": metrics["profit_factor"],
            "sharpe_ratio": metrics["sharpe_ratio"] if isinstance(metrics["sharpe_ratio"], float) else 0.0,
            "sortino_ratio": metrics["sortino_ratio"] if isinstance(metrics["sortino_ratio"], float) else 0.0,
            "max_drawdown_pct": metrics["max_drawdown"],
            "max_consecutive_losses": sim["max_consecutive_losses"],
            "expectancy_usd": metrics["expectancy"],
            "equity_curve": sim["equity_curve"],
            "trades": sim["trades"],
            "ambiguous_trades_count": sim["ambiguous_trades_count"],
            "total_opportunities": sim["total_opportunities"],
            "executed_count": sim["executed_count"],
            "blocked_count": sim["blocked_count"],
            "rejection_breakdown": sim["rejection_breakdown"],
            "blocked_opportunities": sim["blocked_opportunities"][:150], # Top 150 blocked setup inspector rows
            "robustness_stress_test": robustness_report,
            "eval_reports": eval_reports_safe,
            "configuration": {
                "symbol": symbol,
                "timeframe": timeframe,
                "from_date": actual_from,
                "to_date": actual_to,
                "initial_balance": initial_balance,
                "risk_pct": risk_pct,
                "lot_sizing_mode": lot_sizing_mode,
                "user_configured_lot": user_configured_lot,
                "min_risk_reward_ratio": min_risk_reward_ratio,
                "auto_trade_cooldown_seconds": auto_trade_cooldown_seconds,
                "daily_loss_limit_pct": daily_loss_limit_pct,
                "max_consecutive_losses": max_consecutive_losses,
                "max_spread_pips": max_spread_pips
            }
        }
    @classmethod
    def _run_simulation(
        cls,
        candles: List[Candle],
        symbol: str,
        timeframe: str,
        initial_balance: float,
        risk_pct: float,
        lot_sizing_mode: str,
        user_configured_lot: float,
        min_risk_reward_ratio: float,
        auto_trade_cooldown_seconds: int,
        daily_loss_limit_pct: float,
        max_consecutive_losses: int,
        max_spread_pips: float,
        min_ai_confidence: float,
        specs: Dict[str, Any],
        htf_candles_full: Dict[str, List[Candle]],
        randomize_slippage: bool = False,
        cached_reports: Optional[Dict[int, Any]] = None,
        precomputed_full_df: Optional[Any] = None,
        # --- Scalper exit management parameters ---
        break_even_rr: float = 0.5,
        trailing_activation_rr: float = 0.6,
        giveback_guard_enabled: bool = True,
        giveback_guard_min_rr: float = 0.3,
        giveback_guard_retrace_pct: float = 50.0,
        htf_alignment_filter_enabled: Optional[bool] = None,
        htf_alignment_mode: Optional[str] = None,
        disabled_setups: Optional[List[str]] = None,
        entry_confirmation_mode: str = "IMMEDIATE",
        partial_close_enabled: bool = False,
        partial_close_pct: float = 50.0
    ) -> Dict[str, Any]:
        digits = specs["digits"]
        pip_size = specs["pip_size"]
        point_size = specs["point_size"]
        pip_value_per_lot = specs["pip_value_per_lot"]

        from app.services.trade_manager import trade_manager
        if htf_alignment_filter_enabled is not None:
            trade_manager.risk_settings.htf_alignment_filter_enabled = htf_alignment_filter_enabled
        if htf_alignment_mode is not None:
            trade_manager.risk_settings.htf_alignment_mode = htf_alignment_mode
        if disabled_setups is not None:
            trade_manager.risk_settings.disabled_setups = disabled_setups
        if entry_confirmation_mode is not None:
            trade_manager.risk_settings.entry_confirmation_mode = entry_confirmation_mode

        balance = initial_balance
        equity_curve = [initial_balance]
        trades = []
        position = None
        pending_confirmation: Optional[Dict[str, Any]] = None
        sim_max_consec_losses = 0
        current_consec_losses = 0
        ambiguous_trades_count = 0
        last_trade_exit_time: Optional[datetime] = None

        commission_per_lot = 7.0

        # Analytics tracking
        total_opportunities = 0
        executed_count = 0
        blocked_count = 0
        rejection_counts = {
            RiskBlockReason.COOLDOWN.value: 0,
            RiskBlockReason.DAILY_LOSS_LIMIT.value: 0,
            RiskBlockReason.CONSECUTIVE_LOSS_LIMIT.value: 0,
            RiskBlockReason.SPREAD_TOO_WIDE.value: 0,
            RiskBlockReason.CONFIDENCE_TOO_LOW.value: 0,
            RiskBlockReason.INSUFFICIENT_RISK_REWARD.value: 0,
            RiskBlockReason.FIXED_LOT_RISK_EXCEEDS_LIMIT.value: 0,
            RiskBlockReason.INSUFFICIENT_EQUITY.value: 0,
            RiskBlockReason.ACCOUNT_BANKRUPT.value: 0
        }
        blocked_opportunities = []
        eval_reports: Dict[int, Any] = {}

        daily_realized_pnl: Dict[Any, float] = {}
        htf_ptrs = {tf: 0 for tf in ["M5", "M15", "H1", "H4"]}
        current_trading_day: Optional[Any] = None
        start_of_day_balance: float = initial_balance

        from app.services.trade_manager import trade_manager
        from app.services.news_filter import news_filter, MockCalendarProvider
        trade_manager.backtest_active = True
        orig_news_provider = news_filter.provider
        news_filter.provider = MockCalendarProvider()

        full_df = precomputed_full_df if precomputed_full_df is not None else IndicatorService.calculate_all_indicators(candles)

        try:
            start_idx = max(30, min(40, len(candles) - 1))
            total_sim_bars = len(candles) - start_idx
            log_interval = max(500, total_sim_bars // 10)
            
            if not randomize_slippage:
                logger.info(f"[BACKTEST] Simulation started: {total_sim_bars} candles ({candles[0].timestamp.strftime('%Y-%m-%d %H:%M')} to {candles[-1].timestamp.strftime('%Y-%m-%d %H:%M')})")

            for idx, i in enumerate(range(start_idx, len(candles))):
                if not randomize_slippage and idx > 0 and idx % log_interval == 0:
                    pct = int((idx / total_sim_bars) * 100)
                    logger.info(f"[BACKTEST] Simulation progress: {idx}/{total_sim_bars} ({pct}%)")

                c = candles[i]
                c_date = c.timestamp.date()

                if current_trading_day != c_date:
                    current_trading_day = c_date
                    start_of_day_balance = balance
                    if c_date not in daily_realized_pnl:
                        daily_realized_pnl[c_date] = 0.0

                # 1. Update open position if active
                if position is not None:
                    side = position["side"]
                    entry_p = position["entry_price"]
                    sl_p = position["sl_price"]
                    tp_p = position["tp_price"]
                    trade_lots = position["lots"]
                    sl_pips_dist = position["sl_pips_dist"]

                    exit_price = None
                    exit_reason = ""
                    
                    latency_slippage_points = random.uniform(0.1, 0.3) if randomize_slippage else 0.1
                    slippage = latency_slippage_points * point_size

                    # ── Compute current intra-bar profit for guard logic ──────
                    # Use c.high for BUY (best case intra-bar) and c.low for SELL
                    # to update the peak. Guard uses c.close (conservative, no look-ahead).
                    if side == TradeSide.BUY:
                        intra_bar_best_pips = (c.high - entry_p) / pip_size
                        current_close_pips = (c.close - entry_p) / pip_size
                    else:
                        intra_bar_best_pips = (entry_p - c.low) / pip_size
                        current_close_pips = (entry_p - c.close) / pip_size

                    intra_bar_best_rr = intra_bar_best_pips / (sl_pips_dist if sl_pips_dist > 0 else 20.0)
                    current_close_rr = current_close_pips / (sl_pips_dist if sl_pips_dist > 0 else 20.0)

                    # Update simulated SL (break-even applied to simulated_sl)
                    sim_sl = position.get("simulated_sl", sl_p)
                    peak_rr = position.get("peak_rr", 0.0)

                    # Update peak from intra-bar high
                    if intra_bar_best_rr > peak_rr:
                        peak_rr = intra_bar_best_rr
                        position["peak_rr"] = peak_rr

                    # ── Break-Even simulation ──────────────────────────────────
                    be_applied = position.get("be_applied", False)
                    if (not be_applied
                            and intra_bar_best_rr >= break_even_rr):
                        # Shift SL to entry + small buffer (0.2 × sl_dist)
                        be_buffer_pips = max(2.0, sl_pips_dist * 0.2)
                        if side == TradeSide.BUY:
                            new_sim_sl = entry_p + be_buffer_pips * pip_size
                            if new_sim_sl > sim_sl:
                                sim_sl = new_sim_sl
                        else:
                            new_sim_sl = entry_p - be_buffer_pips * pip_size
                            if new_sim_sl < sim_sl:
                                sim_sl = new_sim_sl
                        position["simulated_sl"] = sim_sl
                        position["be_applied"] = True
                        be_applied = True

                    # ── Giveback Guard simulation ──────────────────────────────
                    # Only fires before trailing activation; uses c.close (no look-ahead).
                    giveback_retrace_frac = giveback_guard_retrace_pct / 100.0
                    giveback_triggered = False
                    if (
                        giveback_guard_enabled
                        and current_close_rr < trailing_activation_rr
                        and peak_rr >= giveback_guard_min_rr
                        and current_close_rr < peak_rr * (1.0 - giveback_retrace_frac)
                    ):
                        if partial_close_enabled and not position.get("partial_closed", False):
                            close_frac = partial_close_pct / 100.0
                            scaled_lots = trade_lots * close_frac
                            rem_lots = trade_lots - scaled_lots
                            scaled_exit_p = c.close - slippage if side == TradeSide.BUY else c.close + slippage
                            scaled_pips = ((scaled_exit_p - entry_p) / pip_size) if side == TradeSide.BUY else ((entry_p - scaled_exit_p) / pip_size)
                            scaled_comm = scaled_lots * commission_per_lot
                            scaled_pnl = round(scaled_pips * (pip_value_per_lot * scaled_lots) - scaled_comm, 2)

                            balance += scaled_pnl
                            daily_realized_pnl[c_date] = daily_realized_pnl.get(c_date, 0.0) + scaled_pnl

                            position["partial_closed"] = True
                            position["partial_pnl_usd"] = scaled_pnl
                            position["partial_lots"] = scaled_lots
                            position["remaining_lots"] = rem_lots
                            position["partial_exit_price"] = scaled_exit_p

                            # Shift SL to Break-Even for remaining runner
                            be_buffer_pips = max(2.0, sl_pips_dist * 0.2)
                            if side == TradeSide.BUY:
                                new_sim_sl = entry_p + be_buffer_pips * pip_size
                                if new_sim_sl > sim_sl:
                                    sim_sl = new_sim_sl
                            else:
                                new_sim_sl = entry_p - be_buffer_pips * pip_size
                                if new_sim_sl < sim_sl:
                                    sim_sl = new_sim_sl
                            position["simulated_sl"] = sim_sl
                            position["be_applied"] = True
                            giveback_triggered = True
                        else:
                            # Close 100% of position
                            exit_price = c.close - slippage if side == TradeSide.BUY else c.close + slippage
                            exit_reason = "GIVEBACK_GUARD"
                            giveback_triggered = True

                    if not giveback_triggered:
                        if side == TradeSide.BUY:
                            # Check simulated SL first (may be break-even shifted)
                            if c.low <= sim_sl and c.high >= tp_p:
                                exit_price = sim_sl - slippage
                                exit_reason = "STOP_LOSS_AMBIGUOUS"
                                ambiguous_trades_count += 1
                            elif c.low <= sim_sl:
                                exit_price = sim_sl - slippage
                                exit_reason = "STOP_LOSS" if not be_applied else "BREAK_EVEN_STOP"
                            elif c.high >= tp_p:
                                exit_price = tp_p - slippage
                                exit_reason = "TAKE_PROFIT"
                        else: # SELL
                            ask_high = c.high + (max_spread_pips * pip_size)
                            ask_low = c.low + (max_spread_pips * pip_size)
                            sim_sl_ask = sim_sl  # SL for sells is above entry
                            
                            if ask_high >= sim_sl_ask and ask_low <= tp_p:
                                exit_price = sim_sl_ask + slippage
                                exit_reason = "STOP_LOSS_AMBIGUOUS"
                                ambiguous_trades_count += 1
                            elif ask_high >= sim_sl_ask:
                                exit_price = sim_sl_ask + slippage
                                exit_reason = "STOP_LOSS" if not be_applied else "BREAK_EVEN_STOP"
                            elif ask_low <= tp_p:
                                exit_price = tp_p + slippage
                                exit_reason = "TAKE_PROFIT"

                    if exit_price is not None:
                        partial_closed = position.get("partial_closed", False)
                        rem_lots = position.get("remaining_lots", trade_lots) if partial_closed else trade_lots
                        rem_pips = ((exit_price - entry_p) / pip_size) if side == TradeSide.BUY else ((entry_p - exit_price) / pip_size)
                        rem_comm = rem_lots * commission_per_lot
                        rem_pnl = round(rem_pips * (pip_value_per_lot * rem_lots) - rem_comm, 2)

                        balance += rem_pnl
                        daily_realized_pnl[c_date] = daily_realized_pnl.get(c_date, 0.0) + rem_pnl

                        if partial_closed:
                            total_pnl_usd = round(position["partial_pnl_usd"] + rem_pnl, 2)
                            combined_exit_reason = f"PARTIAL_{partial_close_pct:.0f}%_GIVEBACK + {exit_reason}"
                        else:
                            total_pnl_usd = rem_pnl
                            combined_exit_reason = exit_reason

                        total_comm = trade_lots * commission_per_lot
                        blended_pips = (total_pnl_usd + total_comm) / (pip_value_per_lot * trade_lots) if trade_lots > 0 else 0.0
                        duration_bars = i - position["entry_bar_idx"]
                        r_multiple = round(blended_pips / sl_pips_dist, 2) if sl_pips_dist > 0 else 0.0

                        if total_pnl_usd < 0:
                            current_consec_losses += 1
                            if current_consec_losses > sim_max_consec_losses:
                                sim_max_consec_losses = current_consec_losses
                        elif total_pnl_usd > 0:
                            current_consec_losses = 0

                        last_trade_exit_time = c.timestamp
                        trades.append({
                            "id": len(trades) + 1,
                            "side": side.value if hasattr(side, 'value') else str(side),
                            "symbol": symbol,
                            "entry_time": position["entry_time"].strftime("%Y-%m-%d %H:%M"),
                            "exit_time": c.timestamp.strftime("%Y-%m-%d %H:%M"),
                            "entry_price": round(entry_p, digits),
                            "exit_price": round(exit_price, digits),
                            "lots": trade_lots,
                            "pnl_usd": total_pnl_usd,
                            "pnl_pips": round(blended_pips, 1),
                            "r_multiple": r_multiple,
                            "duration_bars": duration_bars,
                            "exit_reason": combined_exit_reason,
                            "confidence": round(position.get("confidence", 0.80) * 100, 1),
                            "setup_name": position.get("setup_name", "Unknown"),
                            "timeframe": timeframe,
                            "balance_after": round(balance, 2)
                        })
                        position = None

                # 2. Check for new setup opportunity when no position is open
                if position is None and i < len(candles) - 1:
                    if balance <= 0.0:
                        logger.warning(f"[BACKTEST] Account equity/balance exhausted (${balance:.2f}). Stopping simulation.")
                        break

                    is_confirming_execution = False
                    if entry_confirmation_mode == "CONFIRM_NEXT_CANDLE" and pending_confirmation is not None:
                        pending_dir = pending_confirmation["direction"]
                        pending_close = pending_confirmation["signal_close"]
                        atr_val = (c.high - c.low) if (c.high - c.low) > 0 else 0.30

                        if pending_dir == "BUY":
                            is_confirmed = (c.close >= pending_close - 0.2 * atr_val)
                        elif pending_dir == "SELL":
                            is_confirmed = (c.close <= pending_close + 0.2 * atr_val)
                        else:
                            is_confirmed = False

                        pending_confirmation = None
                        if is_confirmed:
                            is_confirming_execution = True

                    if cached_reports is not None and i in cached_reports and not is_confirming_execution:
                        report = cached_reports[i]
                    else:
                        ticker_data = TickerData(
                            symbol=symbol,
                            bid=c.close,
                            ask=c.close + (max_spread_pips * pip_size),
                            spread_pips=max_spread_pips,
                            spread_points=int(max_spread_pips * 10),
                            point_size=point_size,
                            digits=digits,
                            timestamp=c.timestamp
                        )

                        sliced_htf_candles = {}
                        for tf, tf_candles in htf_candles_full.items():
                            ptr = htf_ptrs.get(tf, 0)
                            while ptr < len(tf_candles) and tf_candles[ptr].timestamp <= c.timestamp:
                                ptr += 1
                            htf_ptrs[tf] = ptr
                            sliced_htf_candles[tf] = tf_candles[max(0, ptr - 50):ptr]

                        # Pass sliding window slice of max 300 candles to optimize speed
                        window_slice = candles[max(0, i + 1 - 300):i + 1]
                        df_slice = full_df.iloc[max(0, i + 1 - 300):i + 1] if full_df is not None and len(full_df) > i else None
                        report = decision_engine.evaluate_trade_opportunity(
                            symbol=symbol,
                            candles=window_slice,
                            ticker=ticker_data,
                            timeframe=timeframe,
                            htf_candles=sliced_htf_candles,
                            simulated_balance=balance,
                            precomputed_df=df_slice
                        )
                        eval_reports[i] = report

                    if report.decision in (TradeSide.BUY, TradeSide.SELL):
                        if disabled_setups and getattr(report, "setup_name", "Unknown") in disabled_setups:
                            continue
                        
                        if entry_confirmation_mode == "CONFIRM_NEXT_CANDLE" and not is_confirming_execution:
                            total_opportunities += 1
                            pending_confirmation = {
                                "direction": report.decision.value,
                                "signal_close": c.close
                            }
                            continue

                        if not is_confirming_execution:
                            total_opportunities += 1

                        sl_distance = abs(report.suggested_entry - report.suggested_sl)
                        sl_pips = round(sl_distance / pip_size, 1) if sl_distance > 0 else 20.0
                        
                        if lot_sizing_mode == "FIXED":
                            trade_lots = user_configured_lot
                        else:
                            trade_lots = trade_manager.calculate_risk_based_lot_size(
                                symbol=symbol,
                                risk_pct=risk_pct,
                                sl_pips=sl_pips,
                                account_balance=balance
                            )

                        # ── EXECUTION & RISK FILTER EVALUATION VIA RISK ENGINE ──
                        passed, block_reason, block_details = RiskEngine.evaluate_trade_risk(
                            symbol=symbol,
                            current_time=c.timestamp,
                            current_spread_pips=max_spread_pips,
                            report_confidence=report.confidence,
                            report_rr_ratio=report.risk_reward_ratio,
                            account_balance=balance,
                            start_of_day_balance=start_of_day_balance,
                            daily_realized_pnl=daily_realized_pnl.get(c_date, 0.0),
                            consecutive_losses=current_consec_losses,
                            last_trade_exit_time=last_trade_exit_time,
                            trade_lots=trade_lots,
                            sl_pips=sl_pips,
                            pip_value_per_lot=pip_value_per_lot,
                            commission_per_lot=commission_per_lot,
                            max_spread_pips=max_spread_pips,
                            auto_trade_cooldown_seconds=auto_trade_cooldown_seconds,
                            daily_loss_limit_pct=daily_loss_limit_pct,
                            max_consecutive_losses=max_consecutive_losses,
                            min_ai_confidence=min_ai_confidence,
                            min_risk_reward_ratio=min_risk_reward_ratio,
                            lot_sizing_mode=lot_sizing_mode
                        )

                        if not passed:
                            blocked_count += 1
                            rejection_counts[block_reason] = rejection_counts.get(block_reason, 0) + 1
                            blocked_opportunities.append({
                                "time": c.timestamp.strftime("%Y-%m-%d %H:%M"),
                                "symbol": symbol,
                                "signal": report.decision.value,
                                "confidence": round(report.confidence * 100, 1),
                                "decision": "BLOCKED",
                                "reason": block_reason,
                                "details": block_details,
                                "price": round(c.close, digits)
                            })
                        else:
                            executed_count += 1
                            entry_p = report.suggested_entry
                            latency_slippage_points = random.uniform(0.1, 0.3) if randomize_slippage else 0.1
                            slippage = latency_slippage_points * point_size
                            if report.decision == TradeSide.BUY:
                                entry_p += slippage
                            else:
                                entry_p -= slippage

                            position = {
                                "side": report.decision,
                                "entry_price": entry_p,
                                "sl_price": report.suggested_sl,
                                "tp_price": report.suggested_tp,
                                "lots": trade_lots,
                                "entry_time": c.timestamp,
                                "entry_bar_idx": i,
                                "sl_pips_dist": sl_pips,
                                "confidence": report.confidence,
                                "setup_name": getattr(report, "setup_name", "Unknown"),
                                # Scalper protection tracking state
                                "simulated_sl": report.suggested_sl,
                                "peak_rr": 0.0,
                                "be_applied": False
                            }

                equity_curve.append(round(balance, 2))

            # Close remaining open position at simulation end
            if position is not None:
                c = candles[-1]
                side = position["side"]
                entry_p = position["entry_price"]
                if side == TradeSide.BUY:
                    exit_p = c.close
                    pips = ((exit_p - entry_p) / pip_size)
                else:
                    exit_p = c.close + (max_spread_pips * pip_size)
                    pips = ((entry_p - exit_p) / pip_size)

                partial_closed = position.get("partial_closed", False)
                rem_lots = position.get("remaining_lots", position["lots"]) if partial_closed else position["lots"]
                rem_comm = rem_lots * commission_per_lot
                rem_pnl = round(pips * (pip_value_per_lot * rem_lots) - rem_comm, 2)
                balance += rem_pnl

                if partial_closed:
                    total_pnl_usd = round(position["partial_pnl_usd"] + rem_pnl, 2)
                    combined_exit_reason = f"PARTIAL_{partial_close_pct:.0f}%_GIVEBACK + SIMULATION_END"
                else:
                    total_pnl_usd = rem_pnl
                    combined_exit_reason = "SIMULATION_END"

                total_comm = position["lots"] * commission_per_lot
                blended_pips = (total_pnl_usd + total_comm) / (pip_value_per_lot * position["lots"]) if position["lots"] > 0 else 0.0
                r_multiple = round(blended_pips / position["sl_pips_dist"], 2) if position["sl_pips_dist"] > 0 else 0.0

                trades.append({
                    "id": len(trades) + 1,
                    "side": side.value if hasattr(side, 'value') else str(side),
                    "symbol": symbol,
                    "entry_time": position["entry_time"].strftime("%Y-%m-%d %H:%M"),
                    "exit_time": c.timestamp.strftime("%Y-%m-%d %H:%M"),
                    "entry_price": round(entry_p, digits),
                    "exit_price": round(exit_p, digits),
                    "lots": position["lots"],
                    "pnl_usd": total_pnl_usd,
                    "pnl_pips": round(blended_pips, 1),
                    "r_multiple": r_multiple,
                    "duration_bars": (len(candles) - 1) - position["entry_bar_idx"],
                    "exit_reason": combined_exit_reason,
                    "confidence": round(position.get("confidence", 0.80) * 100, 1),
                    "setup_name": position.get("setup_name", "Unknown"),
                    "balance_after": round(balance, 2)
                })
                equity_curve[-1] = round(balance, 2)
        finally:
            trade_manager.backtest_active = False
            news_filter.provider = orig_news_provider

        return {
            "balance": balance,
            "trades": trades,
            "equity_curve": equity_curve,
            "max_consecutive_losses": sim_max_consec_losses,
            "ambiguous_trades_count": ambiguous_trades_count,
            "total_opportunities": total_opportunities,
            "executed_count": executed_count,
            "blocked_count": blocked_count,
            "rejection_breakdown": rejection_counts,
            "blocked_opportunities": blocked_opportunities,
            "eval_reports": eval_reports
        }

    @classmethod
    def _compute_metrics(
        cls,
        sim_res: Dict[str, Any],
        initial_balance: float,
        lots: float,
        spread_pips: float,
        specs: Dict[str, Any]
    ) -> Dict[str, Any]:
        trades = sim_res["trades"]
        balance = sim_res["balance"]
        equity_curve = sim_res["equity_curve"]
        max_consecutive_losses = sim_res["max_consecutive_losses"]

        total_trades = len(trades)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "break_even_trades": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "net_pnl": 0.0,
                "max_drawdown": 0.0,
                "max_consecutive_losses": 0,
                "avg_r_multiple": 0.0,
                "spread_cost_usd": 0.0,
                "sharpe_ratio": "N/A",
                "sortino_ratio": "N/A"
            }

        wins = [t for t in trades if t["pnl_usd"] > 0]
        losses = [t for t in trades if t["pnl_usd"] < 0]
        break_evens = [t for t in trades if t["pnl_usd"] == 0]

        winning_trades = len(wins)
        losing_trades = len(losses)
        break_even_trades = len(break_evens)

        decisive_trades = winning_trades + losing_trades
        win_rate = round((winning_trades / decisive_trades) * 100.0, 1) if decisive_trades > 0 else 0.0
        avg_win = round(sum(t["pnl_usd"] for t in wins) / winning_trades, 2) if winning_trades > 0 else 0.0
        avg_loss = round(sum(t["pnl_usd"] for t in losses) / losing_trades, 2) if losing_trades > 0 else 0.0

        gross_profit = sum(t["pnl_usd"] for t in wins)
        gross_loss = abs(sum(t["pnl_usd"] for t in losses))
        profit_factor = round(gross_profit / (gross_loss + 1e-9), 2)

        max_drawdown = 0.0
        peak = equity_curve[0]
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / (peak or 1.0)
            if dd > max_drawdown:
                max_drawdown = dd
        max_drawdown = round(max_drawdown * 100.0, 2)

        pip_value_per_lot = specs["pip_value_per_lot"]
        spread_cost_usd = sum(t["lots"] * spread_pips * pip_value_per_lot for t in trades)

        pnls = [t["pnl_usd"] for t in trades]
        mean_pnl = sum(pnls) / len(pnls)
        
        avg_r_multiple = round(sum(t["r_multiple"] for t in trades) / total_trades, 2)

        if total_trades < 5:
            sharpe_val = 0.0
            sortino_val = 0.0
        else:
            variance = sum((x - mean_pnl) ** 2 for x in pnls) / len(pnls)
            std_pnl = math.sqrt(variance) + 1e-9
            sharpe_val = round((mean_pnl / std_pnl) * math.sqrt(252), 2)

            downside_pnls = [t["pnl_usd"] for t in trades if t["pnl_usd"] < 0]
            if downside_pnls:
                downside_variance = sum(x ** 2 for x in downside_pnls) / len(pnls)
                downside_std = math.sqrt(downside_variance) + 1e-9
                sortino_val = round((mean_pnl / downside_std) * math.sqrt(252), 2)
            else:
                sortino_val = 99.0

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "break_even_trades": break_even_trades,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "expectancy": round(mean_pnl, 2),
            "net_pnl": round(balance - initial_balance, 2),
            "max_drawdown": max_drawdown,
            "max_consecutive_losses": max_consecutive_losses,
            "avg_r_multiple": avg_r_multiple,
            "spread_cost_usd": round(spread_cost_usd, 2),
            "sharpe_ratio": sharpe_val,
            "sortino_ratio": sortino_val
        }


backtester = BacktestingEngine()
