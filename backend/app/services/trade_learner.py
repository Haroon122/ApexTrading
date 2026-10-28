"""
ApexTrader Trade Memory & Self-Learning Optimization Engine
Persists every executed trade, market snapshot, indicator values, and PnL outcome into SQLite.
Dynamically optimizes strategy confidence thresholds and risk factors based on real historical performance.
"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ApexTrader.TradeLearner")

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "trading_memory.db")


class TradeLearnerService:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trade_history_memory (
                        trade_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL,
                        lots REAL NOT NULL,
                        sl_price REAL,
                        tp_price REAL,
                        pnl_usd REAL DEFAULT 0.0,
                        pnl_pips REAL DEFAULT 0.0,
                        win_loss TEXT DEFAULT 'PENDING',
                        regime TEXT DEFAULT 'UNKNOWN',
                        rsi REAL DEFAULT 50.0,
                        macd_hist REAL DEFAULT 0.0,
                        rvol REAL DEFAULT 1.0,
                        atr REAL DEFAULT 0.0015,
                        confidence_score REAL DEFAULT 0.80,
                        setup_name TEXT DEFAULT 'Unknown',
                        open_timestamp TEXT NOT NULL,
                        close_timestamp TEXT,
                        exit_reason TEXT
                    )
                """)
                try:
                    cursor.execute("ALTER TABLE trade_history_memory ADD COLUMN setup_name TEXT DEFAULT 'Unknown'")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE trade_history_memory ADD COLUMN ml_pred_side TEXT DEFAULT NULL")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE trade_history_memory ADD COLUMN ml_confidence REAL DEFAULT 0.0")
                except Exception:
                    pass
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS system_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)
                conn.commit()
                logger.info(f"[TRADE LEARNER] Initialized SQLite Trade Memory database at {self.db_path}")
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Database initialization error: {e}")

    def get_last_open_timestamp(self, symbol: Optional[str] = None) -> Optional[datetime]:
        """Most recent open_timestamp in trade memory (optionally for one symbol)."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                if symbol:
                    cur.execute("SELECT MAX(open_timestamp) FROM trade_history_memory WHERE symbol = ?", (symbol,))
                else:
                    cur.execute("SELECT MAX(open_timestamp) FROM trade_history_memory")
                row = cur.fetchone()
                if row and row[0]:
                    return datetime.fromisoformat(str(row[0])[:26])
        except Exception as e:
            logger.warning(f"[TRADE LEARNER] get_last_open_timestamp failed: {e}")
        return None

    def log_trade_execution(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        timeframe: str,
        entry_price: float,
        lots: float,
        sl_price: float,
        tp_price: float,
        regime: str = "UNKNOWN",
        rsi: float = 50.0,
        macd_hist: float = 0.0,
        rvol: float = 1.0,
        atr: float = 0.0015,
        confidence_score: float = 0.80,
        setup_name: str = "Unknown",
        open_timestamp: Optional[datetime] = None,
        ml_pred_side: Optional[str] = None,
        ml_confidence: float = 0.0
    ) -> bool:
        ts = (open_timestamp or datetime.now()).isoformat()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO trade_history_memory (
                        trade_id, symbol, side, timeframe, entry_price, lots, sl_price, tp_price,
                        win_loss, regime, rsi, macd_hist, rvol, atr, confidence_score, setup_name, open_timestamp,
                        ml_pred_side, ml_confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (trade_id, symbol, side, timeframe, entry_price, lots, sl_price, tp_price,
                      regime, rsi, macd_hist, rvol, atr, confidence_score, setup_name, ts,
                      ml_pred_side, ml_confidence))
                conn.commit()
                logger.info(f"[TRADE LEARNER] Logged new trade #{trade_id} ({symbol} {side} @ {entry_price}) [Setup: {setup_name}] [ML Pred: {ml_pred_side}] into trade memory")
                return True
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Failed to log trade execution #{trade_id}: {e}")
            return False

    def update_trade_outcome(
        self,
        trade_id: str,
        exit_price: float,
        pnl_usd: float,
        pnl_pips: float,
        exit_reason: str = "CLOSED",
        close_timestamp: Optional[datetime] = None
    ) -> bool:
        ts = (close_timestamp or datetime.now()).isoformat()
        win_loss = "WIN" if pnl_usd > 0.05 else ("LOSS" if pnl_usd < -0.05 else "EVEN")
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE trade_history_memory
                    SET exit_price = ?, pnl_usd = ?, pnl_pips = ?, win_loss = ?, exit_reason = ?, close_timestamp = ?
                    WHERE trade_id = ?
                """, (exit_price, pnl_usd, pnl_pips, win_loss, exit_reason, ts, trade_id))
                conn.commit()
                logger.info(f"[TRADE LEARNER] Updated outcome for trade #{trade_id}: {win_loss} (${pnl_usd:+.2f} USD / {pnl_pips:+.1f} pips)")
                self.evaluate_live_ml_performance()
                return True
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Failed to update trade outcome #{trade_id}: {e}")
            return False

    def evaluate_live_ml_performance(self, rolling_window: int = 30) -> Dict[str, Any]:
        """
        Calculates live ML prediction accuracy & macro F1 over rolling window of closed trades.
        Logs a degradation alert if live F1 drops below 0.40.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT side, win_loss, ml_pred_side, ml_confidence
                    FROM trade_history_memory
                    WHERE win_loss IN ('WIN', 'LOSS', 'EVEN') AND ml_pred_side IS NOT NULL AND ml_pred_side != ''
                    ORDER BY close_timestamp DESC LIMIT ?
                """, (rolling_window,))
                rows = cursor.fetchall()
                if not rows:
                    return {"sample_count": 0, "accuracy": 0.0, "f1": 0.0, "degraded": False}

                y_true = []
                y_pred = []
                for r in rows:
                    side = r["side"]
                    win_loss = r["win_loss"]
                    pred_side = r["ml_pred_side"]

                    if win_loss == "WIN":
                        true_class = 1 if side == "BUY" else 2
                    else:
                        true_class = 0

                    pred_class = 1 if pred_side == "BUY" else (2 if pred_side == "SELL" else 0)
                    
                    y_true.append(true_class)
                    y_pred.append(pred_class)

                from sklearn.metrics import accuracy_score, f1_score
                acc = float(accuracy_score(y_true, y_pred))
                f1 = float(f1_score(y_true, y_pred, average="macro", zero_division=0))

                degraded = f1 < 0.40 and len(rows) >= 5
                if degraded:
                    logger.warning(
                        f"[ML PERFORMANCE DEGRADATION ALERT] Live ML model F1 score dropped to {f1:.4f} "
                        f"(< 0.40 limit) over last {len(rows)} trades (Accuracy: {acc:.2%})!"
                    )

                return {
                    "sample_count": len(rows),
                    "accuracy": acc,
                    "f1": f1,
                    "degraded": degraded
                }
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Error evaluating live ML performance: {e}")
            return {"sample_count": 0, "accuracy": 0.0, "f1": 0.0, "degraded": False}

    def mark_trade_discrepancy(
        self,
        trade_id: str,
        reason: str = "RECONCILIATION_DISCREPANCY",
        close_timestamp: Optional[datetime] = None
    ) -> bool:
        ts = (close_timestamp or datetime.now()).isoformat()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE trade_history_memory
                    SET win_loss = 'DISCREPANCY', exit_reason = ?, close_timestamp = ?
                    WHERE trade_id = ?
                """, (reason, ts, trade_id))
                conn.commit()
                logger.info(f"[TRADE LEARNER] Marked trade #{trade_id} as DISCREPANCY ({reason})")
                return True
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Failed to mark trade discrepancy #{trade_id}: {e}")
            return False


    def get_optimized_thresholds(self, symbol: str = "EURUSD", regime: str = "UNKNOWN") -> Dict[str, float]:
        """
        Queries historical performance in memory and dynamically calculates optimal thresholds.
        If recent trades in regime have high win rate (>70%), threshold is relaxed slightly to capture setups.
        If win rate is low (<45%), threshold is elevated to protect capital.
        """
        default_thresholds = {
            "min_confidence": 0.70,
            "min_rvol": 0.80,
            "max_spread_multiplier": 1.0,
            "adaptive_rr_ratio": 2.0
        }
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT win_loss, pnl_usd FROM trade_history_memory
                    WHERE win_loss IN ('WIN', 'LOSS')
                    ORDER BY open_timestamp DESC LIMIT 30
                """)
                rows = cursor.fetchall()
                if len(rows) < 5:
                    return default_thresholds

                wins = sum(1 for r in rows if r["win_loss"] == "WIN")
                win_rate = wins / len(rows)
                total_pnl = sum(r["pnl_usd"] for r in rows)

                if win_rate < 0.45 or total_pnl < -50.0:
                    # High caution mode: Elevate confidence threshold
                    return {
                        "min_confidence": 0.80,
                        "min_rvol": 1.10,
                        "max_spread_multiplier": 0.85,
                        "adaptive_rr_ratio": 2.5
                    }
                elif win_rate >= 0.70 and total_pnl > 50.0:
                    # High performance mode: Slightly relax threshold
                    return {
                        "min_confidence": 0.65,
                        "min_rvol": 0.75,
                        "max_spread_multiplier": 1.20,
                        "adaptive_rr_ratio": 2.0
                    }
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Threshold optimization query error: {e}")

        return default_thresholds

    def propose_new_thresholds(self) -> Dict[str, float]:
        """Generates and logs proposed thresholds to the database for review."""
        import json
        proposed = self.get_optimized_thresholds()
        self.set_metadata("proposed_thresholds", json.dumps(proposed))
        logger.info(f"[TRADE LEARNER] Proposed new adaptive thresholds logged for review: {proposed}")
        return proposed

    def get_effective_thresholds(self) -> Dict[str, float]:
        """Loads approved adaptive thresholds or falls back to system risk settings."""
        import json
        from app.services.trade_manager import trade_manager
        risk_cfg = trade_manager.risk_settings
        
        effective = {
            "min_confidence": float(getattr(risk_cfg, 'min_ai_confidence', 0.70)),
            "min_rvol": 0.70,
            "min_rr_ratio": float(getattr(risk_cfg, 'min_risk_reward_ratio', 2.0))
        }
        
        approved_str = self.get_metadata("approved_thresholds")
        if approved_str:
            try:
                approved = json.loads(approved_str)
                effective["min_confidence"] = approved.get("min_confidence", effective["min_confidence"])
                effective["min_rvol"] = approved.get("min_rvol", effective["min_rvol"])
                effective["min_rr_ratio"] = approved.get("adaptive_rr_ratio", effective["min_rr_ratio"])
                logger.info(f"[TRADE LEARNER] Using APPROVED adaptive thresholds: {effective}")
            except Exception as e:
                logger.error(f"[TRADE LEARNER] Error parsing approved thresholds: {e}")
                
        return effective

    def get_learning_stats(self) -> Dict[str, Any]:
        """Returns summary statistics of trade memory for UI audit logs."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN win_loss = 'WIN' THEN 1 ELSE 0 END) as win_count,
                        SUM(CASE WHEN win_loss = 'LOSS' THEN 1 ELSE 0 END) as loss_count,
                        COALESCE(SUM(pnl_usd), 0.0) as net_pnl_usd,
                        COALESCE(SUM(pnl_pips), 0.0) as net_pnl_pips
                    FROM trade_history_memory
                    WHERE win_loss IN ('WIN', 'LOSS')
                """)
                row = cursor.fetchone()
                total = row["total_trades"] or 0
                wins = row["win_count"] or 0
                win_rate = (wins / total * 100.0) if total > 0 else 0.0
                return {
                    "total_completed_trades": total,
                    "win_count": wins,
                    "loss_count": row["loss_count"] or 0,
                    "win_rate_pct": round(win_rate, 1),
                    "net_pnl_usd": round(row["net_pnl_usd"], 2),
                    "net_pnl_pips": round(row["net_pnl_pips"], 1),
                    "adaptive_learning_active": True
                }
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Failed to fetch learning stats: {e}")
            return {
                "total_completed_trades": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate_pct": 0.0,
                "net_pnl_usd": 0.0,
                "net_pnl_pips": 0.0,
                "adaptive_learning_active": True
            }

    def set_metadata(self, key: str, value: str):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO system_metadata (key, value) VALUES (?, ?)", (key, value))
                conn.commit()
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Error setting metadata {key}: {e}")

    def get_metadata(self, key: str, default: str = None) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM system_metadata WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return row["value"]
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Error getting metadata {key}: {e}")
        return default

    def sync_deal_to_db(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        lots: float,
        pnl_usd: float,
        open_time: datetime,
        close_time: datetime,
        comment: str = "CLOSED",
        setup_name: str = "Unknown"
    ):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT trade_id, win_loss, setup_name FROM trade_history_memory WHERE trade_id = ?", (trade_id,))
                row = cursor.fetchone()
                
                open_ts = open_time.isoformat()
                close_ts = close_time.isoformat()
                win_loss = "WIN" if pnl_usd > 0.05 else ("LOSS" if pnl_usd < -0.05 else "EVEN")
                
                # Recover setup_name hierarchy:
                # 1. Existing DB row setup_name (if non-Unknown)
                # 2. Passed parameter or trade_manager.active_trades memory lookup
                # 3. Fallback to 'Unknown'
                resolved_setup = setup_name
                if row and row["setup_name"] and row["setup_name"] != "Unknown":
                    resolved_setup = row["setup_name"]
                elif resolved_setup == "Unknown":
                    try:
                        from app.services.trade_manager import trade_manager
                        if trade_id in trade_manager.active_trades:
                            active_t = trade_manager.active_trades[trade_id]
                            if getattr(active_t, 'setup_name', None):
                                resolved_setup = active_t.setup_name
                    except Exception:
                        pass

                if row:
                    if row["win_loss"] == "PENDING" or pnl_usd != 0.0:
                        cursor.execute("""
                            UPDATE trade_history_memory
                            SET exit_price = ?, pnl_usd = ?, win_loss = ?, exit_reason = ?, close_timestamp = ?, setup_name = ?
                            WHERE trade_id = ?
                        """, (exit_price, pnl_usd, win_loss, comment, close_ts, resolved_setup, trade_id))
                else:
                    cursor.execute("""
                        INSERT INTO trade_history_memory (
                            trade_id, symbol, side, timeframe, entry_price, exit_price, lots,
                            pnl_usd, win_loss, open_timestamp, close_timestamp, exit_reason, setup_name
                        ) VALUES (?, ?, ?, 'M15', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (trade_id, symbol, side, entry_price, exit_price, lots,
                          pnl_usd, win_loss, open_ts, close_ts, comment, resolved_setup))
                conn.commit()
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Error syncing deal {trade_id} to DB: {e}")

    def get_broker_current_time(self) -> datetime:
        """
        Gets current MT5 broker/server time if connected, or local time fallback.
        """
        try:
            from app.services.session_manager import session_manager, MT5_AVAILABLE
            if session_manager.use_live_mt5 and MT5_AVAILABLE:
                import MetaTrader5 as mt5
                from app.services.gold_specs import gold_specs
                symbol = gold_specs.get_specs()["symbol"]
                tick = mt5.symbol_info_tick(symbol)
                if tick and tick.time > 0:
                    return datetime.fromtimestamp(tick.time)
        except Exception:
            pass
        return datetime.now()

    def get_broker_week_start(self, server_dt: Optional[datetime] = None) -> datetime:
        """
        Calculates the start of the current MT5 broker trading week (Monday 00:00:00 server time).
        """
        from datetime import timedelta
        dt = server_dt or self.get_broker_current_time()
        weekday = dt.weekday()  # 0 = Monday, 1 = Tuesday, ..., 6 = Sunday
        if weekday == 6:
            # Sunday market open belongs to the current week starting on Sunday
            week_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            week_start = (dt - timedelta(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
        return week_start

    def check_and_apply_auto_weekly_reset(self) -> str:
        """
        Checks if a new broker trading week has started. If so, automatically resets the weekly loss counter and lock.
        Returns the effective start of the weekly calculation window (ISO format string).
        """
        server_dt = self.get_broker_current_time()
        broker_week_start = self.get_broker_week_start(server_dt)
        broker_week_start_iso = broker_week_start.isoformat()

        last_known_week_start = self.get_metadata("last_known_broker_week_start")
        if not last_known_week_start or broker_week_start_iso > last_known_week_start:
            logger.info(
                f"[WEEKLY RISK ENGINE] New MT5 broker trading week detected ({broker_week_start_iso}). "
                f"Automatically resetting weekly loss counter and clearing weekly trading lock."
            )
            self.set_metadata("last_known_broker_week_start", broker_week_start_iso)
            self.set_metadata("weekly_loss_reset_timestamp", broker_week_start_iso)
            self.set_metadata("weekly_loss_locked", "false")
            return broker_week_start_iso

        weekly_reset_ts = self.get_metadata("weekly_loss_reset_timestamp")
        if not weekly_reset_ts:
            self.set_metadata("weekly_loss_reset_timestamp", broker_week_start_iso)
            weekly_reset_ts = broker_week_start_iso

        # Effective start is the later of broker week start or manual reset timestamp
        return max(broker_week_start_iso, weekly_reset_ts)

    def is_real_account(self) -> bool:
        try:
            from app.adapters.mt5_adapter import mt5_adapter
            account = mt5_adapter.get_account_info()
            if account and (str(account.account_type).upper() == "REAL" or str(account.connection_mode).upper() == "REAL"):
                return True
        except Exception:
            pass
        return False

    def reset_testing_risk_baseline(self) -> Tuple[bool, str]:
        """
        SAFE testing-session reset mechanism for circuit breakers:
        1. Resets daily loss baseline to current timestamp
        2. Resets weekly loss baseline to current timestamp
        3. Resets drawdown peak equity baseline to current account equity
        4. Resets consecutive loss counter timestamp for testing session
        5. Preserves all configured percentages & limits
        6. Rejects execution if connected to a REAL live account
        7. Logs: 'TESTING RISK BASELINE RESET — ALL RISK PROTECTIONS ACTIVE'
        """
        if self.is_real_account():
            err_msg = "RESET BLOCKED: Risk baseline reset is strictly restricted to DEMO/TESTING sessions and is NOT permitted on REAL live trading accounts."
            logger.error(f"[SECURITY LOCK] {err_msg}")
            return False, err_msg

        server_dt = self.get_broker_current_time()
        now_iso = server_dt.isoformat()

        self.set_metadata("testing_risk_reset_timestamp", now_iso)
        self.set_metadata("weekly_loss_reset_timestamp", now_iso)
        self.set_metadata("weekly_loss_locked", "false")

        # Reset peak equity baseline for current active account
        try:
            from app.adapters.mt5_adapter import mt5_adapter
            acc = mt5_adapter.get_account_info()
            if acc and acc.account_number > 0 and acc.equity > 0:
                self.set_metadata(f"peak_equity_{acc.account_number}", str(acc.equity))
                logger.info(f"[RISK MANAGEMENT RESET] Peak equity baseline for account {acc.account_number} reset to ${acc.equity:.2f}")
        except Exception as e:
            logger.error(f"[RISK MANAGEMENT RESET] Error resetting peak equity metadata: {e}")

        exact_log_msg = "TESTING RISK BASELINE RESET — ALL RISK PROTECTIONS ACTIVE"
        logger.info(f"[RISK MANAGEMENT RESET] {exact_log_msg}")
        return True, exact_log_msg

    def reset_weekly_loss_lock(self) -> Tuple[bool, str]:
        """
        SAFE manual reset mechanism for current demo/testing session:
        Delegates to reset_testing_risk_baseline to ensure full circuit breaker baseline sync.
        """
        return self.reset_testing_risk_baseline()

    def get_realized_pnl(self) -> Tuple[float, float]:
        """Returns (daily_pnl, weekly_pnl) from the SQLite database."""
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        
        testing_reset_ts = self.get_metadata("testing_risk_reset_timestamp")
        if testing_reset_ts:
            effective_daily_start = max(start_of_day, testing_reset_ts)
        else:
            effective_daily_start = start_of_day

        start_of_week = self.check_and_apply_auto_weekly_reset()

        daily_pnl = 0.0
        weekly_pnl = 0.0

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT SUM(pnl_usd) as total FROM trade_history_memory
                    WHERE close_timestamp >= ? AND win_loss != 'PENDING'
                """, (effective_daily_start,))
                row = cursor.fetchone()
                if row and row["total"] is not None:
                    daily_pnl = row["total"]

                cursor.execute("""
                    SELECT SUM(pnl_usd) as total FROM trade_history_memory
                    WHERE close_timestamp >= ? AND win_loss != 'PENDING'
                """, (start_of_week,))
                row = cursor.fetchone()
                if row and row["total"] is not None:
                    weekly_pnl = row["total"]
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Error calculating realized P&L: {e}")

        return round(daily_pnl, 2), round(weekly_pnl, 2)

    def get_trades_count_today(self) -> int:
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        testing_reset_ts = self.get_metadata("testing_risk_reset_timestamp")
        effective_daily_start = max(start_of_day, testing_reset_ts) if testing_reset_ts else start_of_day

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM trade_history_memory WHERE open_timestamp >= ?",
                    (effective_daily_start,)
                )
                row = cursor.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Error querying daily trades count: {e}")
            return 0

    def get_recent_closed_trades(
        self, limit: Optional[int] = None, today_only: bool = False, since_timestamp: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                query = "SELECT * FROM trade_history_memory WHERE win_loss != 'PENDING'"
                params = []

                if since_timestamp:
                    query += " AND close_timestamp >= ?"
                    params.append(since_timestamp)
                elif today_only:
                    start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                    query += " AND close_timestamp >= ?"
                    params.append(start_of_day)

                query += " ORDER BY close_timestamp DESC"

                if limit and limit > 0:
                    query += " LIMIT ?"
                    params.append(limit)

                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[TRADE LEARNER] Error querying recent closed trades: {e}")
            return []


trade_learner = TradeLearnerService()

