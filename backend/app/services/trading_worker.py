import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

from app.core.config import settings
from app.adapters.mt5_adapter import mt5_adapter
from app.services.decision_engine import decision_engine
from app.services.risk_manager import risk_manager
from app.services.trade_manager import trade_manager
from app.domain.models import BotStatus, EngineState, TradeSide

logger = logging.getLogger("ApexTrader.GoldTradingWorker")


class GoldTradingWorker:
    """
    Specialized Gold-Only Background Trading Worker.
    Runs the main trading loop (fetching M1, M5, M15, H1, H4 market data for the discovered Gold symbol,
    evaluating setups, monitoring open positions, and executing trades) independently of WebSocket clients.
    """
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._lock = asyncio.Lock()
        self._sequence_num: int = 0
        self.last_payload: Dict[str, Any] = {}
        self.symbol: str = "XAUUSD"  # Will be set to discovered symbol on start/checks
        self.timeframe: str = getattr(getattr(trade_manager, "risk_settings", None), "entry_timeframe", "M1")
        self.pending_confirmation: Optional[Dict[str, Any]] = None
        self.state: EngineState = EngineState.STOPPED
        self.status_reason: str = "Bot stopped by user."

    async def run_pre_flight_checks(self) -> Tuple[bool, str]:
        # 0. Discover Gold Symbol
        discovered_gold = mt5_adapter.discover_gold_symbol()
        if not discovered_gold:
            return False, "Gold/XAUUSD symbol could not be discovered on broker."
        self.symbol = discovered_gold

        # 1. MT5 connection and account info verification
        use_live = mt5_adapter.session_manager.use_live_mt5
        account = mt5_adapter.get_account_info()
        if not account:
            return False, "Failed to retrieve account info."

        if use_live:
            if not getattr(account, "is_connected", False):
                return False, "MT5 terminal is not connected."
            
            # 2. Discovered symbol verified tradeable
            sym_info = mt5_adapter.get_symbol_info(self.symbol)
            if not sym_info:
                return False, f"Discovered Gold symbol {self.symbol} is not found on MT5 server."
            if hasattr(sym_info, 'trade_mode') and sym_info.trade_mode == 0:
                return False, f"Trading is disabled for symbol {self.symbol} on MT5 server."
                
            # 5. Trading permissions verified
            import MetaTrader5 as mt5
            from app.services.session_manager import MT5_AVAILABLE
            if MT5_AVAILABLE:
                terminal_info = mt5.terminal_info()
                if terminal_info is not None and not terminal_info.trade_allowed:
                    return False, "Algo Trading is disabled in MT5 terminal. Please enable it."
        
        # 3. M1/M5/M15/H1/H4 data availability (both live and simulated)
        for tf in ["M1", "M5", "M15", "H1", "H4"]:
            candles = mt5_adapter.fetch_candles(self.symbol, tf, 40)
            if not candles or len(candles) < 40:
                return False, f"Insufficient candle history for {tf} timeframe."
        
        # 4. Spread acceptable
        ticker = mt5_adapter.get_ticker(self.symbol)
        if not ticker:
            return False, "Failed to fetch live symbol ticker data."
        
        # 6. Risk settings loaded
        if not trade_manager.risk_settings:
            return False, "Failed to load risk settings."
            
        return True, "Pre-flight checks passed."

    async def start(self, symbol: str = "XAUUSD", timeframe: Optional[str] = None) -> Tuple[bool, str]:
        """Starts the background Gold trading task if not already running."""
        async with self._lock:
            # Force Gold discovery
            discovered_gold = mt5_adapter.discover_gold_symbol()
            if not discovered_gold:
                self.state = EngineState.ERROR
                self.status_reason = "Pre-flight failed: Gold symbol not discovered."
                return False, "Gold/XAUUSD symbol could not be discovered on broker."
            
            # Read entry_timeframe from user risk settings or passed parameter
            VALID_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
            if timeframe and timeframe.upper() in VALID_TIMEFRAMES:
                entry_tf = timeframe.upper()
                trade_manager.update_risk_settings({"entry_timeframe": entry_tf})
            else:
                configured_tf = getattr(trade_manager.risk_settings, "entry_timeframe", "M15")
                entry_tf = configured_tf.upper() if configured_tf and configured_tf.upper() in VALID_TIMEFRAMES else "M15"

            symbol = discovered_gold
            timeframe = entry_tf

            if self._running or self._task is not None:
                if self.symbol.upper() == symbol.upper() and self.timeframe.upper() == timeframe.upper():
                    return True, f"Trading worker is already running for {symbol} ({timeframe})."
                
                logger.info(f"[TRADING WORKER] Restarting worker to switch from {self.symbol} ({self.timeframe}) to {symbol} ({timeframe})")
                self._running = False
                if self._task:
                    self._task.cancel()
                    try:
                        await self._task
                    except asyncio.CancelledError:
                        pass
                    self._task = None
            
            # Temporary state variables to run checks on new configuration
            old_symbol = self.symbol
            old_timeframe = self.timeframe
            
            self.symbol = symbol
            self.timeframe = timeframe
            self.state = EngineState.STARTING
            self.status_reason = "Running pre-flight checks..."
            
            # Run pre-flight checks
            checks_ok, checks_msg = await self.run_pre_flight_checks()
            if not checks_ok:
                # Revert settings on failure
                self.symbol = old_symbol
                self.timeframe = old_timeframe
                self.state = EngineState.ERROR
                self.status_reason = f"Pre-flight failed: {checks_msg}"
                logger.error(f"[TRADING WORKER] Start failed: {checks_msg}")
                return False, checks_msg
            
            self._running = True
            self.state = EngineState.RUNNING
            self.status_reason = "Waiting for valid setup..."
            self._task = asyncio.create_task(self._loop())
            logger.info(f"[TRADING WORKER] Started background worker for symbol={self.symbol}, timeframe={self.timeframe}")
            return True, f"Trading worker started for {self.symbol} ({self.timeframe})"

    async def stop(self):
        if self._running:
            self._running = False
            self.state = EngineState.STOPPED
            self.status_reason = "Bot stopped by user command"
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
            logger.info("[TRADING WORKER] Stopped background worker")
            return True, "Trading worker stopped"

    def is_running(self) -> bool:
        return self._running

    async def _loop(self):
        VALID_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
        while self._running:
            try:
                self._sequence_num += 1

                # Dynamic sync of entry timeframe from risk settings
                configured_tf = getattr(trade_manager.risk_settings, "entry_timeframe", "M15")
                if configured_tf and configured_tf.upper() in VALID_TIMEFRAMES and self.timeframe != configured_tf.upper():
                    logger.info(f"[TRADING WORKER] Dynamic timeframe change detected: switching active entry timeframe from {self.timeframe} to {configured_tf.upper()}")
                    self.timeframe = configured_tf.upper()

                # 1. Fetch live market ticker and multi-timeframe candles
                ticker = mt5_adapter.get_ticker(self.symbol)
                
                m1_candles = mt5_adapter.fetch_candles(self.symbol, "M1", 100)
                m5_candles = mt5_adapter.fetch_candles(self.symbol, "M5", 100)
                m15_candles = mt5_adapter.fetch_candles(self.symbol, "M15", 100)
                h1_candles = mt5_adapter.fetch_candles(self.symbol, "H1", 100)
                h4_candles = mt5_adapter.fetch_candles(self.symbol, "H4", 100)
                
                if not m1_candles:
                    logger.warning(f"[TRADING WORKER] Temporary data unavailability for {self.symbol}. Retrying in 1s...")
                    await asyncio.sleep(1.0)
                    continue

                htf_candles = {
                    "M1": m1_candles,
                    "M5": m5_candles,
                    "M15": m15_candles,
                    "H1": h1_candles,
                    "H4": h4_candles
                }
                if self.timeframe not in htf_candles:
                    htf_candles[self.timeframe] = mt5_adapter.fetch_candles(self.symbol, self.timeframe, 100) or m15_candles

                candles = htf_candles.get(self.timeframe, m15_candles)
                auto_trade_hold_event = None
                
                can_trade, reason = trade_manager.can_auto_trade()
                logger.info(f"[AUTO LOOP] cycle #{self._sequence_num} at {datetime.now()}, symbol={self.symbol}, timeframe={self.timeframe}, can_auto_trade={can_trade}, reason={reason}")
                
                # 2. Evaluate Single Authoritative AI Execution Pipeline
                res = trade_manager.evaluate_and_execute_pipeline(self.symbol, self.timeframe, force_execution=False)
                report = res.get("report")
                if not report:
                    report = decision_engine.evaluate_trade_opportunity(self.symbol, candles, ticker, self.timeframe, htf_candles=htf_candles)
                
                # Entry Confirmation Mechanism (IMMEDIATE vs CONFIRM_NEXT_CANDLE)
                confirm_mode = getattr(trade_manager.risk_settings, "entry_confirmation_mode", "IMMEDIATE")
                if confirm_mode == "CONFIRM_NEXT_CANDLE":
                    latest_closed = candles[-1] if candles[-1].is_closed else (candles[-2] if len(candles) >= 2 else candles[-1])
                    
                    if self.pending_confirmation is not None:
                        pending_tf = self.pending_confirmation.get("timeframe")
                        pending_ts = self.pending_confirmation.get("signal_candle_ts")
                        pending_dir = self.pending_confirmation.get("direction")
                        pending_close = self.pending_confirmation.get("signal_close", 0.0)
                        
                        if latest_closed.timestamp > pending_ts:
                            atr_val = getattr(candles[-1], 'high', 0.0) - getattr(candles[-1], 'low', 0.0) or 0.30
                            if pending_dir == "BUY":
                                is_confirmed = (latest_closed.close >= pending_close - 0.2 * atr_val)
                            elif pending_dir == "SELL":
                                is_confirmed = (latest_closed.close <= pending_close + 0.2 * atr_val)
                            else:
                                is_confirmed = False

                            if not is_confirmed:
                                logger.info(f"[ENTRY CONFIRMATION FAILED] {pending_dir} signal on {pending_tf} CANCELLED.")
                                self.pending_confirmation = None
                                report.decision = TradeSide.HOLD
                            else:
                                self.pending_confirmation = None
                                logger.info(f"[ENTRY CONFIRMED & RE-VALIDATED] {pending_dir} signal CONFIRMED on {self.timeframe}.")
                        else:
                            report.decision = TradeSide.HOLD
                    elif report.decision != TradeSide.HOLD:
                        self.pending_confirmation = {
                            "symbol": self.symbol,
                            "timeframe": self.timeframe,
                            "direction": report.decision.value,
                            "signal_candle_ts": latest_closed.timestamp,
                            "signal_close": latest_closed.close,
                            "report": report
                        }
                        report.decision = TradeSide.HOLD
                else:
                    self.pending_confirmation = None

                # Authoritative position reconciliation & monitoring
                trade_manager.update_open_positions(self.symbol)
                active_pos = mt5_adapter.get_positions()
                account = mt5_adapter.get_account_info()
                has_active_pos = any(p.symbol == self.symbol for p in active_pos)

                # Determine worker state & live hold reason based on authoritative pipeline result
                if res["success"]:
                    self.state = EngineState.POSITION_OPEN
                    self.status_reason = f"Position open: {res['message']}"
                    live_hold_reason = f"Position Active: {res['message']}"
                elif res["stage"] in ("EXECUTION_FAILED", "RISK_REJECTED", "PERMISSIONS"):
                    self.state = EngineState.ERROR
                    self.status_reason = res["reason"]
                    live_hold_reason = res["reason"]
                    logger.error(f"[TRADING WORKER] Pipeline Execution Blocked: {res['reason']}")
                elif has_active_pos:
                    self.state = EngineState.MANAGING_POSITION
                    self.status_reason = f"POSITION_MANAGING — Monitoring {len(active_pos)} active position(s) for {self.symbol}"
                    live_hold_reason = f"Position Active: Monitoring {len(active_pos)} open position(s) for {self.symbol}."
                elif self.pending_confirmation is not None:
                    pending_dir = self.pending_confirmation.get("direction", "SELL")
                    tf_mins = self.timeframe[1:] if len(self.timeframe) > 1 else "1"
                    self.state = EngineState.RUNNING
                    self.status_reason = f"WAITING_CONFIRMATION — {pending_dir} signal pending on {self.timeframe}. Awaiting next candle close confirmation (~{tf_mins} min delay)..."
                    live_hold_reason = f"Trade Held [Confirmation Delay]: {pending_dir} signal pending on {self.timeframe}. Awaiting next candle close confirmation (~{tf_mins} min delay)."
                else:
                    self.state = EngineState.RUNNING
                    self.status_reason = f"SCANNING_MARKET — {res['reason']}"
                    live_hold_reason = res["reason"]

                # 5. Fetch fresh account info, positions, pending orders, and history after execution
                account = mt5_adapter.get_account_info()
                pending_orders = mt5_adapter.get_pending_orders()
                history = mt5_adapter.get_history(today_only=True)
                
                # Update authoritative payload with sequence number and htf candles
                self.last_payload = {
                    "timestamp": datetime.now().isoformat(),
                    "sequence": self._sequence_num,
                    "bot_status": trade_manager.bot_status.value,
                    "engine_state": self.state.value,
                    "engine_reason": self.status_reason,
                    "auto_trade_live_reason": live_hold_reason,
                    "auto_trade_hold_event": auto_trade_hold_event,
                    "ticker": ticker.model_dump(mode="json") if ticker else None,
                    "account": account.model_dump(mode="json") if account else None,
                    "candles": [c.model_dump(mode="json") for c in candles[-50:]] if candles else [],
                    "htf_candles": {tf: [c.model_dump(mode="json") for c in c_list[-50:]] for tf, c_list in htf_candles.items() if c_list},
                    "decision_report": report.model_dump(mode="json") if report else None,
                    "positions": [p.model_dump(mode="json") for p in active_pos],
                    "pending_orders": [p.model_dump(mode="json") for p in pending_orders] if pending_orders else [],
                    "history": [h.model_dump(mode="json") for h in history] if history else [],
                    "history_total": len(history),
                    "risk_settings": trade_manager.risk_settings.model_dump(mode="json")
                }
                
            except Exception as e:
                self.state = EngineState.RUNNING
                self.status_reason = f"Temporary loop error (recovering): {str(e)}"
                logger.warning(f"[TRADING WORKER] Temporary loop error (recovering): {e}", exc_info=True)
                
            await asyncio.sleep(1.0) # 1-second refresh cycle


# Global Singleton Instance
trading_worker = GoldTradingWorker()

