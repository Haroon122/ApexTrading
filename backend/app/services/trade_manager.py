"""
Trade Management Lifecycle Engine & Bot Controller
Handles order execution, break-even shift, trailing stops, position monitoring, and bot controls
"""

import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple, Any
from app.core.config import settings
from app.adapters.mt5_adapter import mt5_adapter
from app.domain.models import (
    TradeRecord, PendingOrderRecord, TradeDecisionReport, TradeSide, TradeState,
    OrderType, BotStatus, RiskSettings, ManualOrderRequest
)
from app.services.trade_learner import trade_learner

logger = logging.getLogger("ApexTrader.TradeManager")


import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent.parent / "data" / "risk_settings.json"


class TradeManager:
    """
    Automated Trade Lifecycle Manager & Bot Controller.
    Continuously monitors active positions to apply Break-Even rules, Trailing Stops,
    handles Bot Status (RUNNING, PAUSED, STOPPED, EMERGENCY_HALT), and executes Manual/Pending Orders.
    """

    def __init__(self):
        from datetime import datetime, date
        self.active_trades: Dict[str, TradeRecord] = {}
        self.bot_status: BotStatus = BotStatus.STOPPED
        self.last_auto_trade_time: Optional[datetime] = None
        self.daily_trade_count: int = 0
        self.last_trade_date: Optional[date] = None
        self.risk_settings: RiskSettings = self._load_persisted_risk_settings()
        self.pending_confirmation: Optional[Dict[str, Any]] = None
        self.executed_signals: Dict[str, datetime] = {}
        self.training_active: bool = False
        self.backtest_active: bool = False
        self._load_recent_signals()

    def _load_recent_signals(self):
        try:
            from app.services.trade_learner import trade_learner
            from datetime import datetime, timedelta
            four_hours_ago = (datetime.now() - timedelta(hours=4)).isoformat()
            
            # Fetch current account number to construct exact signal key
            account = mt5_adapter.get_account_info()
            account_id = account.account_number
            
            with trade_learner._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT symbol, side, timeframe, open_timestamp FROM trade_history_memory
                    WHERE open_timestamp >= ? AND win_loss != 'PENDING'
                """, (four_hours_ago,))
                rows = cursor.fetchall()
                
                for r in rows:
                    direction = r["side"]
                    open_ts = r["open_timestamp"]
                    dt = datetime.fromisoformat(open_ts)
                    candle_ts = dt.strftime("%Y-%m-%d %H:%M")
                    
                    sig_id = f"{account_id}_{r['symbol']}_{r['timeframe']}_{candle_ts}_{direction}"
                    self.executed_signals[sig_id] = dt
            logger.info(f"[TRADE MANAGER] Loaded {len(self.executed_signals)} recent signals from trade history memory")
        except Exception as e:
            logger.error(f"[TRADE MANAGER] Error loading recent signals from DB: {e}")

    def _load_persisted_risk_settings(self) -> RiskSettings:
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(f"[TRADE MANAGER] Loaded persisted risk_settings from {CONFIG_PATH}")
                    return RiskSettings(**data)
        except Exception as e:
            logger.warning(f"[TRADE MANAGER] Could not load persisted risk_settings: {e}")

        return RiskSettings(
            max_risk_per_trade_pct=settings.MAX_RISK_PER_TRADE_PCT,
            daily_loss_limit_pct=settings.DAILY_LOSS_LIMIT_PCT,
            weekly_loss_limit_pct=settings.WEEKLY_LOSS_LIMIT_PCT,
            max_open_positions=settings.MAX_OPEN_POSITIONS,
            entry_timeframe="M15",
            entry_confirmation_mode="IMMEDIATE",
            lot_sizing_mode="DYNAMIC_RISK",
            user_configured_lot=0.01,
            max_allowed_lot=0.05,
            default_lot_size=0.01,
            trailing_stop_pips=15.0,
            trailing_activation_rr=1.2,
            trailing_atr_multiplier=2.0,
            break_even_rr=1.0,
            giveback_guard_enabled=False,
            giveback_guard_min_rr=1.5,
            giveback_guard_retrace_pct=50.0,
            min_ai_confidence=settings.MIN_AI_CONFIDENCE,
            auto_trade_enabled=False,
            auto_trade_cooldown_seconds=300,
            max_trades_per_day=10,
            max_spread_pips=settings.MAX_SPREAD_PIPS,
            enable_live_trading=getattr(settings, "ENABLE_LIVE_TRADING", False)
        )

    def update_risk_settings(self, new_settings: Any) -> RiskSettings:
        if isinstance(new_settings, dict):
            current = self.risk_settings.model_dump()
            current.update(new_settings)
            self.risk_settings = RiskSettings(**current)
        elif isinstance(new_settings, RiskSettings):
            self.risk_settings = new_settings

        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.risk_settings.model_dump(), f, indent=2)
            logger.info(f"[TRADE MANAGER] Persisted updated risk_settings to {CONFIG_PATH}")
        except Exception as e:
            logger.error(f"[TRADE MANAGER] Failed to persist risk_settings: {e}")
        return self.risk_settings
        return self.risk_settings

    def can_auto_trade(self) -> Tuple[bool, str]:
        from datetime import datetime, date
        if self.bot_status != BotStatus.RUNNING:
            res, msg = False, f"Bot status is {self.bot_status.value} (must be RUNNING)."
            logger.info(f"[CAN AUTO TRADE] Result: {res}, Reason: {msg}")
            return res, msg

        if not self.risk_settings.auto_trade_enabled:
            res, msg = False, "Automated trading is disabled in Risk Settings."
            logger.info(f"[CAN AUTO TRADE] Result: {res}, Reason: {msg}")
            return res, msg

        today = date.today()
        if self.last_trade_date != today:
            self.daily_trade_count = 0
            self.last_trade_date = today

        # Check Daily/Weekly Loss Circuit Breaker locks
        try:
            account = mt5_adapter.get_account_info()
            if account and account.balance > 0:
                daily_loss_pct = float(getattr(self.risk_settings, "daily_loss_limit_pct", settings.DAILY_LOSS_LIMIT_PCT))
                if account.daily_pnl_usd <= -(account.balance * (daily_loss_pct / 100.0)):
                    res, msg = False, f"Daily Loss Circuit Breaker has been triggered (Loss ${abs(account.daily_pnl_usd):.2f} >= {daily_loss_pct}% limit). New trades are currently disabled."
                    logger.info(f"[CAN AUTO TRADE] Result: {res}, Reason: {msg}")
                    return res, msg

                max_daily_loss_amt = float(getattr(self.risk_settings, "max_daily_loss_amount", 500.0) or 0.0)
                if max_daily_loss_amt > 0.0 and account.daily_pnl_usd <= -max_daily_loss_amt:
                    res, msg = False, f"Daily Loss Circuit Breaker has been triggered (Loss ${abs(account.daily_pnl_usd):.2f} >= ${max_daily_loss_amt:.2f} limit). New trades are currently disabled."
                    logger.info(f"[CAN AUTO TRADE] Result: {res}, Reason: {msg}")
                    return res, msg

                weekly_loss_pct = float(getattr(self.risk_settings, "weekly_loss_limit_pct", settings.WEEKLY_LOSS_LIMIT_PCT))
                if account.weekly_pnl_usd <= -(account.balance * (weekly_loss_pct / 100.0)):
                    res, msg = False, f"Weekly Loss Circuit Breaker has been triggered (Loss ${abs(account.weekly_pnl_usd):.2f} >= {weekly_loss_pct}% limit). New trades are currently disabled."
                    logger.info(f"[CAN AUTO TRADE] Result: {res}, Reason: {msg}")
                    return res, msg
        except Exception as e:
            logger.warning(f"[CAN AUTO TRADE] Account check error: {e}")

        if self.daily_trade_count >= self.risk_settings.max_trades_per_day:
            res, msg = False, f"Daily trade limit reached ({self.daily_trade_count}/{self.risk_settings.max_trades_per_day}). New trades are currently disabled."
            logger.info(f"[CAN AUTO TRADE] Result: {res}, Reason: {msg}")
            return res, msg

        cooldown_sec = int(getattr(self.risk_settings, "auto_trade_cooldown_seconds", 300))
        if cooldown_sec > 0 and self.last_auto_trade_time is not None:
            elapsed = (datetime.now() - self.last_auto_trade_time).total_seconds()
            if elapsed < cooldown_sec:
                remaining = int(cooldown_sec - elapsed)
                res, msg = False, f"Auto-trade blocked: Cooldown active ({remaining}s remaining)."
                logger.info(f"[CAN AUTO TRADE] Result: {res}, Reason: {msg}")
                return res, msg

        res, msg = True, "Auto-trading conditions met."
        logger.info(f"[CAN AUTO TRADE] Result: {res}, Reason: {msg}")
        return res, msg

    # ── BOT STATUS CONTROL ────────────────────────────────────────

    def start_bot(self) -> Tuple[bool, str]:
        self.bot_status = BotStatus.RUNNING
        logger.info("[BOT CONTROLLER] Bot status set to RUNNING")
        return True, "Trading Bot Engine STARTED (RUNNING)"

    def pause_bot(self) -> Tuple[bool, str]:
        if self.bot_status == BotStatus.RUNNING:
            self.bot_status = BotStatus.PAUSED
            logger.info("[BOT CONTROLLER] Bot status set to PAUSED")
            return True, "Trading Bot Engine PAUSED"
        return False, f"Cannot pause bot from status {self.bot_status.value}"

    def resume_bot(self) -> Tuple[bool, str]:
        if self.bot_status == BotStatus.PAUSED:
            self.bot_status = BotStatus.RUNNING
            logger.info("[BOT CONTROLLER] Bot status RESUMED")
            return True, "Trading Bot Engine RESUMED (RUNNING)"
        return False, f"Cannot resume bot from status {self.bot_status.value}"

    def stop_bot(self) -> Tuple[bool, str]:
        self.bot_status = BotStatus.STOPPED
        logger.info("[BOT CONTROLLER] Bot status set to STOPPED")
        return True, "Trading Bot Engine STOPPED"

    def emergency_stop(self) -> Tuple[bool, str]:
        self.bot_status = BotStatus.EMERGENCY_HALT
        logger.warning("[EMERGENCY STOP] Triggered Emergency Stop! Halting bot & closing all open positions...")
        closed_count = mt5_adapter.close_all_positions("EMERGENCY_STOP")
        self.active_trades.clear()
        msg = f"EMERGENCY STOP EXECUTED: Bot Halted & {closed_count} open position(s)/order(s) closed!"
        logger.warning(msg)
        return True, msg

    def reset_testing_risk_baseline(self) -> Tuple[bool, str]:
        """
        Resets testing session circuit breaker baselines (Daily Loss, Weekly Loss, Drawdown Peak, Consecutive Losses),
        subject to REAL account security checks, and logs: 'TESTING RISK BASELINE RESET — ALL RISK PROTECTIONS ACTIVE'
        """
        from app.services.trade_learner import trade_learner
        return trade_learner.reset_testing_risk_baseline()

    def reset_weekly_loss_lock(self) -> Tuple[bool, str]:
        """
        Clears current weekly trading lock, resets weekly loss counter for testing week,
        and logs: 'WEEKLY LOSS LOCK RESET — NORMAL RISK CHECKS ACTIVE'
        """
        from app.services.trade_learner import trade_learner
        return trade_learner.reset_testing_risk_baseline()

    def get_status(self) -> Dict[str, Any]:
        return {
            "status": self.bot_status.value,
            "risk_settings": self.risk_settings.model_dump(),
            "active_positions_count": len(mt5_adapter.get_positions()),
            "pending_orders_count": len(mt5_adapter.get_pending_orders())
        }

    def _check_execution_safety(self, symbol: str) -> Tuple[bool, str]:
        """
        Implements hard execution locks to prevent live trading in non-live modes.
        """
        if getattr(self, "training_active", False):
            return False, "Execution locked: Training is currently active."
        if getattr(self, "backtest_active", False):
            return False, "Execution locked: Backtester simulation running."
            
        use_live = mt5_adapter.session_manager.use_live_mt5
        from app.services.session_manager import MT5_AVAILABLE
        if not MT5_AVAILABLE or not use_live:
            return True, "PAPER_MODE"
            
        try:
            import MetaTrader5 as mt5
            acc_info = mt5.account_info()
            if acc_info is None:
                return False, "Execution blocked: Could not retrieve live account details from MT5."
                
            trade_mode = int(acc_info.trade_mode)
            if trade_mode == 2: # REAL Account
                live_enabled = getattr(self.risk_settings, "enable_live_trading", False) or getattr(settings, "ENABLE_LIVE_TRADING", False)
                if not live_enabled:
                    return False, "CRITICAL SAFETY LOCK: Live trading on REAL account is not explicitly enabled. Set ENABLE_LIVE_TRADING=True in Risk Settings to unlock."
                return True, "LIVE"
            else:
                return True, "DEMO"
        except Exception as e:
            return False, f"Execution blocked: Safety check failed with error: {str(e)}"

    # ── SINGLE AUTHORITATIVE AI EXECUTION PIPELINE ──────────────────

    def evaluate_and_execute_pipeline(
        self,
        symbol: str,
        timeframe: str = "M15",
        force_execution: bool = False,
        is_confirming_execution: bool = False
    ) -> Dict[str, Any]:
        """
        Single Authoritative AI Execution Pipeline.
        Executes pre-flight, strategy decision, risk evaluation, and broker order placement.
        Used identically by manual 'Execute AI Signal' and background Auto Trading worker.
        """
        from app.adapters.mt5_adapter import mt5_adapter
        from app.services.decision_engine import decision_engine
        from app.services.risk_manager import risk_manager

        account = mt5_adapter.get_account_info()
        
        # 1. Pre-flight & MT5 Connection / Permissions Check
        if mt5_adapter.session_manager.use_live_mt5:
            if not account or not account.is_connected:
                err = "MT5 Account is not connected. Please connect via MT5 Connection Manager."
                return {
                    "success": False,
                    "stage": "PRE_FLIGHT",
                    "reason": f"Trade Blocked [Connection]: {err}",
                    "message": err
                }
            
            import MetaTrader5 as mt5
            from app.services.session_manager import MT5_AVAILABLE
            if MT5_AVAILABLE:
                t_info = mt5.terminal_info()
                if t_info is not None and not t_info.trade_allowed:
                    err = "Algo Trading is DISABLED in MT5 terminal. Please click 'Algo Trading' (Ctrl+E) in MT5 toolbar to allow automated orders."
                    return {
                        "success": False,
                        "stage": "PERMISSIONS",
                        "reason": f"AI Execution Blocked: {err}",
                        "message": err
                    }

        # 2. Check Auto Trade Permissions if not forced
        if not force_execution:
            can_trade, can_msg = self.can_auto_trade()
            if not can_trade:
                return {
                    "success": False,
                    "stage": "AUTO_TRADE_PERMISSIONS",
                    "reason": f"Trade Blocked: {can_msg}",
                    "message": f"Trade Blocked: {can_msg}"
                }

        # 3. Strategy Decision Engine Evaluation
        candles = mt5_adapter.fetch_candles(symbol, timeframe, 200)
        ticker = mt5_adapter.get_ticker(symbol)
        
        m1_candles = mt5_adapter.fetch_candles(symbol, "M1", 100)
        m5_candles = mt5_adapter.fetch_candles(symbol, "M5", 100)
        m15_candles = mt5_adapter.fetch_candles(symbol, "M15", 100)
        h1_candles = mt5_adapter.fetch_candles(symbol, "H1", 100)
        h4_candles = mt5_adapter.fetch_candles(symbol, "H4", 100)
        htf_candles = {"M1": m1_candles, "M5": m5_candles, "M15": m15_candles, "H1": h1_candles, "H4": h4_candles}

        report = decision_engine.evaluate_trade_opportunity(symbol, candles, ticker, timeframe, htf_candles=htf_candles)
        
        # Validation for pending signal confirmation: abort execution if fresh evaluation direction differs from pending direction
        if is_confirming_execution and self.pending_confirmation is not None:
            expected_dir = self.pending_confirmation.get("direction")
            if report.decision.value != expected_dir:
                msg = f"[CONFIRMATION ABORTED - SIGNAL CHANGED] Pending {expected_dir} signal for {symbol} on {timeframe} aborted because fresh evaluation returned {report.decision.value}."
                logger.info(msg)
                self.pending_confirmation = None
                display_report = report.model_copy()
                display_report.decision = TradeSide.HOLD
                return {
                    "success": False,
                    "stage": "CONFIRMATION_ABORTED",
                    "reason": msg,
                    "message": msg,
                    "report": display_report
                }
        
        if report.decision.value == "HOLD":
            rej_list = report.rejection_reasons or []
            primary_code = rej_list[0] if rej_list else "HOLD"
            clean_detail = rej_list[1] if len(rej_list) > 1 else (rej_list[0] if rej_list else "Strategy decision is HOLD")
            
            if primary_code == "SPREAD_BLOCKED":
                live_reason = f"Trade Blocked [High Spread]: {clean_detail}"
            elif primary_code == "NEWS_BLOCKED":
                live_reason = f"Trade Blocked [News Blackout]: {clean_detail}"
            elif primary_code == "HTF_ALIGNMENT_BLOCKED":
                live_reason = f"Trade Blocked [Market Structure]: {clean_detail}"
            elif primary_code == "RR_BLOCKED":
                live_reason = f"Trade Blocked [Risk/Reward]: {clean_detail}"
            elif primary_code == "LOW_CONFIDENCE":
                live_reason = f"Trade Held [Low Confidence]: {clean_detail}"
            else:
                live_reason = f"Trade Held: {clean_detail}"

            reason_msg = f"No trade executed. Strategy decision is HOLD. {clean_detail}"
            return {
                "success": False,
                "stage": "STRATEGY_HOLD",
                "reason": live_reason,
                "message": reason_msg,
                "report": report
            }

        # 4. Institutional Risk Evaluation
        risk_check = risk_manager.evaluate_risk(account, report, symbol)
        if not risk_check.passed:
            first_risk = risk_check.reasons[0] if risk_check.reasons else "Risk Management Rejected Order"
            return {
                "success": False,
                "stage": "RISK_REJECTED",
                "reason": f"Trade Blocked by Risk Guard: {first_risk}",
                "message": f"Risk Management Rejected Order: {first_risk}",
                "risk_reasons": risk_check.reasons,
                "report": report
            }

        # 4.5. Entry Confirmation Gate (IMMEDIATE vs CONFIRM_NEXT_CANDLE)
        confirm_mode = getattr(self.risk_settings, "entry_confirmation_mode", "IMMEDIATE")
        if confirm_mode == "CONFIRM_NEXT_CANDLE" and not force_execution and not is_confirming_execution:
            latest_closed = candles[-1] if (candles and candles[-1].is_closed) else (candles[-2] if candles and len(candles) >= 2 else (candles[-1] if candles else None))
            signal_ts = latest_closed.timestamp if latest_closed else datetime.now()
            signal_close = latest_closed.close if latest_closed else (ticker.bid if ticker else 0.0)
            
            self.pending_confirmation = {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": report.decision.value,
                "signal_candle_ts": signal_ts,
                "signal_close": signal_close,
                "report": report
            }
            pending_dir = report.decision.value
            tf_mins = timeframe[1:] if len(timeframe) > 1 else "1"
            hold_msg = f"Trade Held [Confirmation Delay]: {pending_dir} signal pending on {timeframe}. Awaiting next candle close confirmation (~{tf_mins} min delay)."
            logger.info(f"[ENTRY CONFIRMATION INITIATED] Stored pending {pending_dir} signal for {symbol} on {timeframe}.")
            
            display_report = report.model_copy()
            display_report.decision = TradeSide.HOLD
            
            return {
                "success": False,
                "stage": "AWAITING_CONFIRMATION",
                "reason": hold_msg,
                "message": hold_msg,
                "report": display_report
            }

        # 5. Broker / Adapter Trade Execution
        success, trade, exec_msg = self.execute_decision(report, risk_check.calculated_lots, force_execution=force_execution)
        
        if not success:
            return {
                "success": False,
                "stage": "EXECUTION_FAILED",
                "reason": f"AI Execution Blocked: {exec_msg}",
                "message": exec_msg,
                "report": report
            }

        return {
            "success": True,
            "stage": "EXECUTED",
            "reason": f"Trade Executed Successfully: {exec_msg}",
            "message": exec_msg,
            "trade": trade,
            "report": report
        }

    def execute_decision(self, report: TradeDecisionReport, lots: float, force_execution: bool = False) -> Tuple[bool, Optional[TradeRecord], str]:
        logger.info(f"[EXECUTE DECISION] execute_decision reached: symbol={report.symbol}, decision={report.decision.value}, lots={lots}, force_execution={force_execution}")
        
        # Hard Volume Safety Validation
        max_allowed = getattr(self.risk_settings, "max_allowed_lot", 1.00) or 1.00
        if lots <= 0:
            err_msg = "ORDER BLOCKED — INVALID LOT SIZE: requested volume must be > 0"
            logger.error(f"[TRADE MANAGER PIPELINE STAGE] {err_msg}")
            return False, None, err_msg
        if lots > max_allowed:
            err_msg = f"ORDER BLOCKED — EXCEEDS MAX ALLOWED LOT: requested volume {lots} > max allowed lot {max_allowed}"
            logger.error(f"[TRADE MANAGER PIPELINE STAGE] {err_msg}")
            return False, None, err_msg

        logger.info(f"[TRADE MANAGER PIPELINE STAGE] final_execution_lot={lots} (max_allowed={max_allowed})")

        # Check safety locks
        allowed, mode = self._check_execution_safety(report.symbol)
        if not allowed:
            logger.warning(f"[HARD SAFETY LOCK] Blocked order execution: {mode}")
            return False, None, mode
            
        if not force_execution and self.bot_status != BotStatus.RUNNING:
            logger.info(f"[EXECUTE DECISION] Blocked: bot status is {self.bot_status.value}")
            return False, None, f"Automated execution blocked. Bot status is {self.bot_status.value}."

        if report.decision == TradeSide.HOLD:
            reasons_str = f"Rejection reasons: {', '.join(report.rejection_reasons)}" if report.rejection_reasons else ""
            logger.info(f"[EXECUTE DECISION] Blocked: decision is HOLD. {reasons_str}")
            return False, None, f"Decision is HOLD. {reasons_str}"

        # ── SIGNAL IDEMPOTENCY / DUPLICATE-TRADE PROTECTION ─────────────────────
        account = mt5_adapter.get_account_info()
        account_id = account.account_number if account else "MOCK_ACC"
        symbol = report.symbol
        timeframe = report.timeframe
        direction = report.decision.value

        from datetime import datetime
        candles = mt5_adapter.fetch_candles(symbol, timeframe, 1)
        candle_ts = candles[-1].timestamp.strftime("%Y-%m-%d %H:%M") if candles else datetime.now().strftime("%Y-%m-%d %H:%M")
        
        signal_id = f"{account_id}_{symbol}_{timeframe}_{candle_ts}_{direction}"

        if not force_execution and mode != "PAPER_MODE":
            if signal_id in self.executed_signals:
                elapsed = (datetime.now() - self.executed_signals[signal_id]).total_seconds()
                if elapsed < 14400: # 4-hour window TTL
                    logger.info(f"[SIGNAL IDEMPOTENCY] Reentry blocked. Signal {signal_id} was already executed {elapsed:.1f}s ago. Skipping duplicate trade.")
                    return False, None, f"Duplicate signal detected (Signal ID: {signal_id})."

        # ── PAPER/SIMULATION MODE EXECUTION ─────────────────────────────────────
        if mode == "PAPER_MODE":
            import random
            ticket = random.randint(10000000, 99999999)
            trade = TradeRecord(
                trade_id=str(ticket),
                symbol=symbol,
                side=report.decision,
                order_type=OrderType.MARKET,
                entry_price=report.suggested_entry,
                sl_price=report.suggested_sl,
                tp_price=report.suggested_tp,
                lots=lots,
                state=TradeState.OPEN,
                open_time=datetime.now(),
                ai_confidence_at_entry=report.confidence,
                entry_reasoning=report.reasoning,
                setup_name=report.setup_name,
                timeframe=report.timeframe,
                planned_loss_usd=report.potential_loss_usd,
                planned_profit_usd=report.potential_profit_usd,
                potential_loss_usd=report.potential_loss_usd,
                potential_profit_usd=report.potential_profit_usd
            )
            self.active_trades[trade.trade_id] = trade
            self.last_auto_trade_time = datetime.now()
            self.daily_trade_count += 1
            logger.info(f"[PAPER MODE] Simulated position #{ticket} created locally (Setup: {report.setup_name}, TF: {report.timeframe}).")
            return True, trade, f"[PAPER MODE] Mock order #{ticket} executed successfully @ {report.suggested_entry}"

        # ── LIVE broker order placement ──
        logger.info(f"[EXECUTE DECISION] Calling mt5_adapter.place_order for symbol={report.symbol}, side={report.decision.value}, lots={lots}")
        success, trade, msg = mt5_adapter.place_order(
            symbol=report.symbol,
            side=report.decision,
            lots=lots,
            sl=report.suggested_sl,
            tp=report.suggested_tp,
            reasoning=report.reasoning,
            ai_confidence=report.confidence
        )

        if success and trade:
            from datetime import datetime, date
            trade.setup_name = report.setup_name
            trade.timeframe = report.timeframe
            trade.planned_loss_usd = report.potential_loss_usd
            trade.planned_profit_usd = report.potential_profit_usd
            trade.potential_loss_usd = report.potential_loss_usd
            trade.potential_profit_usd = report.potential_profit_usd

            self.active_trades[trade.trade_id] = trade
            self.last_auto_trade_time = datetime.now()
            self.last_trade_date = date.today()
            self.daily_trade_count += 1
            
            # Cache successfully placed signal ID
            self.executed_signals[signal_id] = datetime.now()

            # Snapshot real indicator values so the learner gets useful data
            # (previously defaults like RSI=50 / ATR=0.0015 were stored for every trade).
            feat = {}
            try:
                from app.services.indicators import IndicatorService
                snap_candles = mt5_adapter.fetch_candles(symbol, timeframe, 100)
                if snap_candles and len(snap_candles) >= 30:
                    row = IndicatorService.calculate_all_indicators(snap_candles).iloc[-1]
                    for key, col in (("rsi", "rsi_14"), ("atr", "atr_14"), ("macd_hist", "macd_hist"), ("rvol", "rvol")):
                        val = row.get(col)
                        if val is not None and val == val:  # skip NaN
                            feat[key] = float(val)
            except Exception as e:
                logger.warning(f"[EXECUTE DECISION] Indicator snapshot failed: {e}")

            trade_learner.log_trade_execution(
                **feat,
                trade_id=trade.trade_id,
                symbol=trade.symbol,
                side=trade.side.value,
                timeframe=report.timeframe,
                entry_price=trade.entry_price,
                lots=trade.lots,
                sl_price=trade.sl_price,
                tp_price=trade.tp_price,
                confidence_score=report.confidence,
                setup_name=report.setup_name
            )
            logger.info(f"[EXECUTE DECISION] Trade {trade.trade_id} placed successfully! (Setup: {report.setup_name}, Daily count: {self.daily_trade_count})")
            return True, trade, msg
        else:
            logger.error(f"[EXECUTE DECISION] Order placement failed: {msg}")
            return False, None, msg

    def execute_manual_order(self, req: ManualOrderRequest) -> Tuple[bool, Optional[Any], str]:
        if self.bot_status == BotStatus.EMERGENCY_HALT:
            return False, None, "Manual execution blocked: System is in EMERGENCY_HALT state. Reset bot first."

        # Hard Volume Safety Validation
        max_allowed = getattr(self.risk_settings, "max_allowed_lot", 1.00) or 1.00
        if req.lots <= 0:
            err_msg = "ORDER BLOCKED — INVALID LOT SIZE: requested volume must be > 0"
            logger.error(f"[TRADE MANAGER PIPELINE STAGE] {err_msg}")
            return False, None, err_msg
        if req.lots > max_allowed:
            err_msg = f"ORDER BLOCKED — EXCEEDS MAX ALLOWED LOT: requested volume {req.lots} > max allowed lot {max_allowed}"
            logger.error(f"[TRADE MANAGER PIPELINE STAGE] {err_msg}")
            return False, None, err_msg

        logger.info(f"[TRADE MANAGER PIPELINE STAGE] execute_manual_order: requested_lots={req.lots} (max_allowed={max_allowed})")

        # Check safety locks
        allowed, mode = self._check_execution_safety(req.symbol)
        if not allowed:
            logger.warning(f"[HARD SAFETY LOCK] Blocked manual order: {mode}")
            return False, None, mode

        ticker = mt5_adapter.get_ticker(req.symbol)
        
        # Calculate default SL/TP if not explicitly provided
        sl = req.sl or 0.0
        tp = req.tp or 0.0

        from app.services.symbol_specs import calculate_potential_pnl_usd

        if mode == "PAPER_MODE":
            import random
            from datetime import datetime
            ticket = random.randint(10000000, 99999999)
            entry_p = req.price if req.price and req.price > 0 else (ticker.ask if req.side == TradeSide.BUY else ticker.bid)
            pot_loss = calculate_potential_pnl_usd(req.symbol, req.side, entry_p, sl, req.lots) if sl > 0 else 0.0
            pot_profit = calculate_potential_pnl_usd(req.symbol, req.side, entry_p, tp, req.lots) if tp > 0 else 0.0

            trade = TradeRecord(
                trade_id=str(ticket),
                symbol=req.symbol,
                side=req.side,
                order_type=req.order_type,
                entry_price=entry_p,
                sl_price=sl,
                tp_price=tp,
                lots=req.lots,
                state=TradeState.OPEN,
                open_time=datetime.now(),
                ai_confidence_at_entry=0.85,
                entry_reasoning=["Manual User Execution"],
                setup_name="Manual Execution",
                timeframe="M15",
                planned_loss_usd=pot_loss,
                planned_profit_usd=pot_profit,
                potential_loss_usd=pot_loss,
                potential_profit_usd=pot_profit
            )
            self.active_trades[trade.trade_id] = trade
            logger.info(f"[PAPER MODE] Simulated manual position #{ticket} created.")
            return True, trade, f"[PAPER MODE] Mock manual order #{ticket} executed @ {trade.entry_price}"

        if req.order_type == OrderType.MARKET:
            success, trade, msg = mt5_adapter.place_order(
                symbol=req.symbol,
                side=req.side,
                lots=req.lots,
                sl=req.sl or 0.0,
                tp=req.tp or 0.0,
                reasoning=["Manual User Execution"]
            )
            if success and trade:
                trade.setup_name = "Manual Execution"
                trade.timeframe = "M15"
                if trade.sl_price > 0:
                    trade.planned_loss_usd = calculate_potential_pnl_usd(trade.symbol, trade.side, trade.entry_price, trade.sl_price, trade.lots)
                if trade.tp_price > 0:
                    trade.planned_profit_usd = calculate_potential_pnl_usd(trade.symbol, trade.side, trade.entry_price, trade.tp_price, trade.lots)
                trade.potential_loss_usd = trade.planned_loss_usd
                trade.potential_profit_usd = trade.planned_profit_usd

                self.active_trades[trade.trade_id] = trade
                trade_learner.log_trade_execution(
                    trade_id=trade.trade_id,
                    symbol=trade.symbol,
                    side=trade.side.value,
                    timeframe="M15",
                    entry_price=trade.entry_price,
                    lots=trade.lots,
                    sl_price=trade.sl_price,
                    tp_price=trade.tp_price,
                    confidence_score=0.85,
                    setup_name="Manual Execution"
                )
            return success, trade, msg

        else:
            # Pending orders: BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP
            target_price = req.price if req.price and req.price > 0 else (ticker.ask if req.side == TradeSide.BUY else ticker.bid)

            return mt5_adapter.place_pending_order(
                symbol=req.symbol,
                side=req.side,
                order_type=req.order_type,
                target_price=target_price,
                lots=req.lots,
                sl=req.sl or 0.0,
                tp=req.tp or 0.0
            )

    def modify_position(self, trade_id: str, new_sl: float, new_tp: float) -> Tuple[bool, str]:
        res = mt5_adapter.update_sl_tp(trade_id, new_sl, new_tp)
        if res:
            return True, f"Position {trade_id} SL/TP updated successfully"
        return False, "Failed to update position SL/TP"

    # ── POSITION MONITORING & TRAILING STOP ────────────────────────

    def reconcile_positions(self, symbol: str) -> List[TradeRecord]:
        """
        Authoritative position reconciliation against live MT5 state.
        Ensures MT5 is the single source of truth for open positions.
        Detects externally closed trades, reconciles outcomes, and synchronizes active_trades memory.
        """
        from datetime import datetime
        from app.services.session_manager import MT5_AVAILABLE
        use_live = mt5_adapter.session_manager.use_live_mt5

        # 1. Fetch live open positions from MT5 (Authoritative Snapshot)
        live_positions_list = mt5_adapter.get_positions()
        live_positions = {p.trade_id: p for p in live_positions_list}

        # If live MT5 mode is disabled (Paper Trading Mode) and get_positions returns empty
        if not use_live and len(live_positions_list) == 0:
            return [t for t in self.active_trades.values() if t.symbol == symbol and t.state == TradeState.OPEN]

        # 2. Detect closed positions (previously tracked, but no longer in MT5)
        for trade_id, trade in list(self.active_trades.items()):
            if trade.symbol == symbol and trade.state == TradeState.OPEN:
                if trade_id not in live_positions:
                    logger.info(
                        f"[POSITION_SYNC] Ticket #{trade_id} ({symbol}) no longer exists in MT5. "
                        f"Position CLOSED externally or by execution. Reconciling outcome..."
                    )
                    
                    exit_price = trade.entry_price
                    pnl_usd = 0.0
                    pips = 0.0
                    exit_reason = "CLOSED"
                    close_time = datetime.now()
                    
                    import MetaTrader5 as mt5
                    if use_live and MT5_AVAILABLE:
                        deals = mt5.history_deals_get(position=int(trade_id))
                        if deals:
                            exit_deal = next((d for d in deals if d.entry == 1), deals[-1])
                            exit_price = float(exit_deal.price)
                            pnl_usd = float(exit_deal.profit + exit_deal.swap + exit_deal.commission)
                            close_time = datetime.fromtimestamp(exit_deal.time)
                            exit_reason = exit_deal.comment or "CLOSED"
                            
                            from app.services.symbol_specs import get_symbol_specs
                            specs = get_symbol_specs(trade.symbol)
                            pip_size = specs["pip_size"]
                            if trade.side == TradeSide.BUY:
                                pips = (exit_price - trade.entry_price) / pip_size
                            else:
                                pips = (trade.entry_price - exit_price) / pip_size
                    
                    trade_learner.update_trade_outcome(
                        trade_id=trade_id,
                        exit_price=exit_price,
                        pnl_usd=round(pnl_usd, 2),
                        pnl_pips=round(pips, 1),
                        exit_reason=exit_reason,
                        close_timestamp=close_time
                    )
                    trade_learner.propose_new_thresholds()
                    
                    trade.state = TradeState.CLOSED
                    self.active_trades.pop(trade_id, None)

        # 3. Detect new positions opened in MT5 not yet in active_trades
        for live_id, live_pos in live_positions.items():
            if live_id not in self.active_trades:
                self.active_trades[live_id] = live_pos
                logger.info(f"[POSITION_SYNC] Registered active MT5 position #{live_id} ({live_pos.symbol} {live_pos.side.value} {live_pos.lots} lots) into state tracking.")

        logger.info(f"[POSITION_SYNC] MT5 open positions count for {symbol}: {len(live_positions_list)} | Active tracked count: {len([t for t in self.active_trades.values() if t.symbol == symbol and t.state == TradeState.OPEN])}")
        return live_positions_list

    def update_open_positions(self, symbol: str):
        ticker = mt5_adapter.get_ticker(symbol)
        if not ticker:
            return
        curr_price = ticker.bid

        # Perform authoritative reconciliation
        live_positions_list = self.reconcile_positions(symbol)
        live_positions = {p.trade_id: p for p in live_positions_list}

        # 2. Apply trailing stop / break-even check for currently active positions
        for trade_id, trade in list(self.active_trades.items()):
            if trade.symbol != symbol or trade.state != TradeState.OPEN:
                continue

            from app.services.symbol_specs import get_symbol_specs
            specs = get_symbol_specs(symbol)
            pip_size = specs["pip_size"]
            digits = specs["digits"]
            
            # Verify ticket existence before attempting management
            live_pos = live_positions.get(trade_id)
            if not live_pos and mt5_adapter.session_manager.use_live_mt5:
                logger.warning(f"[POSITION_SYNC] Skipping management on ticket #{trade_id}: Ticket no longer exists in MT5.")
                continue

            if live_pos:
                trade.sl_price = live_pos.sl_price
                trade.tp_price = live_pos.tp_price
            
            is_buy = (trade.side == TradeSide.BUY or getattr(trade.side, "value", str(trade.side)) == "BUY")
            eval_price = ticker.bid if is_buy else ticker.ask
            
            if is_buy:
                profit_pips = (eval_price - trade.entry_price) / pip_size
                sl_distance_pips = (trade.entry_price - trade.sl_price) / pip_size if trade.sl_price > 0 else 20.0
            else:
                profit_pips = (trade.entry_price - eval_price) / pip_size
                sl_distance_pips = (trade.sl_price - trade.entry_price) / pip_size if trade.sl_price > 0 else 20.0

            trade.pnl_pips = round(profit_pips, 1)
            if not mt5_adapter.session_manager.use_live_mt5:
                pip_val = float(specs.get("pip_value_per_lot", 10.0) or 10.0)
                trade.pnl_usd = round(profit_pips * pip_val * trade.lots, 2)

            profit_rr = profit_pips / (sl_distance_pips if sl_distance_pips > 0 else 20.0)

            # ── PEAK PROFIT RR TRACKING (no look-ahead — uses only current price) ──
            if profit_rr > trade.peak_profit_rr:
                trade.peak_profit_rr = profit_rr

            # Read dynamic trailing settings
            activation_rr = float(getattr(self.risk_settings, "trailing_activation_rr", 1.2))
            atr_mult = float(getattr(self.risk_settings, "trailing_atr_multiplier", 2.0))

            # Fetch M1 ATR for dynamic trailing distance
            atr_m1_price = 0.15 # Fallback Gold $1.50 ATR
            try:
                from app.services.indicators import IndicatorService
                candles = mt5_adapter.fetch_candles(symbol, timeframe="M1", count=30)
                if candles and len(candles) >= 14:
                    df_m1 = IndicatorService.calculate_all_indicators(candles)
                    atr_m1_price = float(df_m1["atr_14"].iloc[-1] or 0.15)
            except Exception as e:
                logger.debug(f"[TRADE MANAGER] Could not fetch M1 ATR for dynamic trailing: {e}")

            # Dynamic trailing distance in pips ($3.50 / 35 pips minimum cushion on Gold)
            dynamic_trail_pips = max(self.risk_settings.trailing_stop_pips, (atr_m1_price * atr_mult) / (pip_size or 0.10))

            # ── GIVEBACK GUARD ─────────────────────────────────────────────────────
            # Fires only BEFORE trailing activation to avoid conflicting with the trailing stop.
            # Closes the position at market if the trade retraces more than giveback_retrace_pct
            # of its peak unrealized R-multiple, provided peak has reached giveback_min_rr.
            giveback_enabled = getattr(self.risk_settings, "giveback_guard_enabled", False)
            giveback_min_rr = float(getattr(self.risk_settings, "giveback_guard_min_rr", 1.5))
            giveback_retrace = float(getattr(self.risk_settings, "giveback_guard_retrace_pct", 50.0)) / 100.0

            if (
                giveback_enabled
                and profit_rr < activation_rr                              # trailing not yet active
                and trade.peak_profit_rr >= giveback_min_rr                # reached minimum profit threshold
                and profit_rr < trade.peak_profit_rr * (1.0 - giveback_retrace)  # retraced beyond guard level
            ):
                logger.info(
                    f"[GIVEBACK GUARD] Trade {trade_id} ({symbol}) triggering market close: "
                    f"peak_rr={trade.peak_profit_rr:.3f}R, current_rr={profit_rr:.3f}R, "
                    f"retrace_threshold={giveback_retrace*100:.0f}% of peak."
                )
                success, msg = mt5_adapter.close_order(trade_id, exit_reason="GIVEBACK_GUARD")
                if success:
                    trade.state = TradeState.CLOSED
                    self.active_trades.pop(trade_id, None)
                    logger.info(f"[GIVEBACK GUARD] Trade {trade_id} successfully closed: {msg}")
                else:
                    logger.warning(f"[GIVEBACK GUARD] Failed to close trade {trade_id}: {msg}")
                continue

            # Break-Even Shift Check
            if self.risk_settings.auto_breakeven_enabled and profit_rr >= self.risk_settings.break_even_rr:
                be_lock_pips = max(2.0, sl_distance_pips * 0.2)
                new_sl = trade.entry_price + (be_lock_pips * pip_size if is_buy else -be_lock_pips * pip_size)
                if (is_buy and trade.sl_price < new_sl) or \
                   (not is_buy and trade.sl_price > new_sl):
                    if abs(trade.sl_price - new_sl) > 2.0 * pip_size:
                        mt5_adapter.update_sl_tp(trade_id, new_sl=round(new_sl, digits), new_tp=trade.tp_price)
                        trade.sl_price = round(new_sl, digits)
                        logger.info(f"[TRADE MANAGER] Trade {trade_id} shifted to BREAK-EVEN @ {new_sl:.{digits}f}")

            # Delayed Dynamic Trailing Stop Check (Requires profit_rr >= activation_rr [e.g. 0.6R])
            if self.risk_settings.auto_trailing_stop_enabled and profit_rr >= activation_rr:
                if is_buy:
                    candidate_sl = eval_price - (dynamic_trail_pips * pip_size)
                    min_allowed_sl = trade.entry_price + (sl_distance_pips * 0.2 * pip_size)
                    final_sl = max(candidate_sl, min_allowed_sl)
                    
                    if final_sl > trade.sl_price:
                        if (final_sl - trade.sl_price) > 1.5 * pip_size:
                            mt5_adapter.update_sl_tp(trade_id, new_sl=round(final_sl, digits), new_tp=trade.tp_price)
                            trade.sl_price = round(final_sl, digits)
                            logger.info(f"[TRADE MANAGER] Delayed Dynamic Trailing updated for {trade_id} @ {final_sl:.{digits}f} (Dynamic Trail Pips: {dynamic_trail_pips:.1f})")
                else:
                    candidate_sl = eval_price + (dynamic_trail_pips * pip_size)
                    max_allowed_sl = trade.entry_price - (sl_distance_pips * 0.2 * pip_size)
                    final_sl = min(candidate_sl, max_allowed_sl)
                    
                    if final_sl < trade.sl_price:
                        if (trade.sl_price - final_sl) > 1.5 * pip_size:
                            mt5_adapter.update_sl_tp(trade_id, new_sl=round(final_sl, digits), new_tp=trade.tp_price)
                            trade.sl_price = round(final_sl, digits)
                            logger.info(f"[TRADE MANAGER] Delayed Dynamic Trailing updated for {trade_id} @ {final_sl:.{digits}f} (Dynamic Trail Pips: {dynamic_trail_pips:.1f})")


    def calculate_risk_based_lot_size(
        self, symbol: str, risk_pct: float, sl_pips: float, account_balance: float = 10000.0
    ) -> float:
        if sl_pips <= 0:
            sl_pips = 20.0

        risk_usd = account_balance * (max(0.1, min(10.0, risk_pct)) / 100.0)
        from app.services.symbol_specs import get_symbol_specs
        pip_value_usd = get_symbol_specs(symbol)["pip_value_per_lot"]
        raw_lots = risk_usd / (sl_pips * pip_value_usd)

        sym_info = mt5_adapter.get_symbol_info(symbol)
        min_vol = getattr(sym_info, 'volume_min', 0.01) or 0.01
        max_vol = getattr(sym_info, 'volume_max', 100.0) or 100.0
        step_vol = getattr(sym_info, 'volume_step', 0.01) or 0.01

        steps = round(raw_lots / step_vol)
        calc_lots = max(min_vol, min(max_vol, steps * step_vol))
        return round(calc_lots, 2)


trade_manager = TradeManager()
