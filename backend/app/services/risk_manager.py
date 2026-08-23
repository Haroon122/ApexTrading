"""
Institutional Risk Management Service Module
Dynamic position sizing, Daily/Weekly loss limits, Max Drawdown protection & Circuit Breaker
"""

import logging
from datetime import datetime
from typing import List, Tuple, Dict, Any
from app.core.config import settings
from app.domain.models import AccountInfo, RiskCheckResult, TradeDecisionReport, TradeSide

logger = logging.getLogger("ApexTrader.RiskManager")

class RiskManager:
    """
    Institutional Risk Manager.
    Capital Preservation is the highest priority.
    """

    def evaluate_risk(
        self, account: AccountInfo, report: TradeDecisionReport, symbol: str
    ) -> RiskCheckResult:

        reasons = []
        
        # 1. Check Circuit Breaker: Daily Loss Limit (Single source of truth from user Risk Settings)
        from app.services.trade_manager import trade_manager
        risk_cfg = trade_manager.risk_settings
        
        daily_loss_pct = float(getattr(risk_cfg, "daily_loss_limit_pct", settings.DAILY_LOSS_LIMIT_PCT))
        if account.daily_pnl_usd <= -(account.balance * (daily_loss_pct / 100.0)):
            reasons.append(
                f"Daily Loss Limit breached (${account.daily_pnl_usd:.2f} <= -{daily_loss_pct}% of balance). Circuit breaker active."
            )

        # 1b. Check Circuit Breaker: Max Daily Loss Amount ($)
        max_daily_loss_amt = float(getattr(risk_cfg, "max_daily_loss_amount", 500.0) or 0.0)
        if max_daily_loss_amt > 0.0 and account.daily_pnl_usd <= -max_daily_loss_amt:
            reasons.append(
                f"Max Daily Loss Amount breached (${account.daily_pnl_usd:.2f} <= -${max_daily_loss_amt:.2f}). Circuit breaker active."
            )

        # 2. Check Circuit Breaker: Weekly Loss Limit (Single source of truth from user Risk Settings)
        weekly_loss_pct = float(getattr(risk_cfg, "weekly_loss_limit_pct", settings.WEEKLY_LOSS_LIMIT_PCT))
        if account.weekly_pnl_usd <= -(account.balance * (weekly_loss_pct / 100.0)):
            reasons.append(
                f"Weekly Loss Limit breached (${account.weekly_pnl_usd:.2f} <= -{weekly_loss_pct}% of balance). Trading locked."
            )

        # 2b. Check Circuit Breaker: Maximum Drawdown Limit
        drawdown_limit = getattr(settings, "MAX_DRAWDOWN_LIMIT_PCT", 12.0)
        if account.drawdown_pct >= drawdown_limit:
            reasons.append(
                f"Maximum Drawdown Limit breached ({account.drawdown_pct:.2f}% >= {drawdown_limit}% of peak equity). Circuit breaker active."
            )

        # 3. Check Maximum Open Trades (using live MT5 position count or fallback)
        from app.adapters.mt5_adapter import mt5_adapter
        live_positions = mt5_adapter.get_positions()
        open_count = len(live_positions) if mt5_adapter.is_connected else account.open_positions_count
        
        max_positions = int(getattr(risk_cfg, "max_open_positions", settings.MAX_OPEN_TRADES))
        
        if open_count >= max_positions:
            reasons.append(
                f"Maximum open positions limit reached ({open_count}/{max_positions}) across all symbols."
            )

        # 3a. Proximity Stacking Protection: Reject duplicate entries within 20 pips ($2.00 on Gold)
        from app.services.symbol_specs import get_symbol_specs
        from app.domain.models import TradeState
        specs = get_symbol_specs(symbol)
        pip_size = float(specs.get("pip_size", 0.10) or 0.10)

        all_active = live_positions if mt5_adapter.is_connected else list(trade_manager.active_trades.values())
        entry_p = report.suggested_entry
        if entry_p > 0 and all_active:
            for pos in all_active:
                if pos.symbol == symbol and getattr(pos, 'state', None) == TradeState.OPEN:
                    dist_pips = abs(entry_p - pos.entry_price) / pip_size
                    if dist_pips < 20.0:
                        reasons.append(
                            f"PROXIMITY GUARD: Active position #{pos.trade_id} already open at {pos.entry_price:.2f} "
                            f"({dist_pips:.1f} pips away < 20.0 pips minimum buffer). Duplicate entry blocked."
                        )
                        break

        # 3b. Check Maximum Daily Trades
        from app.services.trade_learner import trade_learner
        max_daily_trades = int(getattr(risk_cfg, "max_trades_per_day", 10))
        today_trades_count = trade_learner.get_trades_count_today()
        if today_trades_count >= max_daily_trades:
            reasons.append(
                f"Maximum daily trades limit reached ({today_trades_count}/{max_daily_trades}). Auto trading locked for the day."
            )

        # 3c. Check Maximum Consecutive Losses & Loss Cooldown
        testing_reset_ts = trade_learner.get_metadata("testing_risk_reset_timestamp")
        recent_closed = trade_learner.get_recent_closed_trades(limit=10, since_timestamp=testing_reset_ts)
        
        # Cooldown Check
        cooldown_seconds = int(getattr(risk_cfg, "auto_trade_cooldown_seconds", 300))
        if cooldown_seconds > 0 and recent_closed:
            last_trade = recent_closed[0]
            close_ts_str = last_trade.get("close_timestamp")
            if close_ts_str:
                try:
                    close_ts = datetime.fromisoformat(close_ts_str)
                    seconds_elapsed = (datetime.now() - close_ts).total_seconds()
                    if 0 <= seconds_elapsed < cooldown_seconds:
                        remaining = int(cooldown_seconds - seconds_elapsed)
                        reasons.append(
                            f"Auto-trade blocked: Cooldown active ({remaining}s remaining)."
                        )
                except Exception:
                    pass
                        
        # Consecutive Losses Check
        consecutive_losses = 0
        for t in recent_closed:
            pnl = float(t.get("pnl_usd", 0.0))
            if pnl < 0.0:
                consecutive_losses += 1
            else:
                break
                
        max_consec_losses = int(getattr(risk_cfg, "max_consecutive_losses", 5))
        is_breaker_active = consecutive_losses >= max_consec_losses

        if is_breaker_active:
            reasons.append(
                f"Maximum consecutive losses reached ({consecutive_losses}/{max_consec_losses}). Circuit breaker active."
            )

        # 4. Check Minimum Risk:Reward Ratio (Single source of truth from user Risk Settings)
        min_rr = float(getattr(risk_cfg, "min_risk_reward_ratio", settings.MIN_RISK_REWARD_RATIO))
        if report.risk_reward_ratio < min_rr:
            reasons.append(
                f"Risk:Reward ratio ({report.risk_reward_ratio}:1) below required minimum ({min_rr}:1)."
            )

        # 5. Position Sizing (Lots): FIXED mode vs DYNAMIC_RISK mode
        lot_mode = getattr(risk_cfg, "lot_sizing_mode", "FIXED").upper()
        user_fixed_lot = float(getattr(risk_cfg, "user_configured_lot", 0.10) or 0.10)
        max_allowed_lot = float(getattr(risk_cfg, "max_allowed_lot", 1.00) or 1.00)
        default_lot = float(getattr(risk_cfg, "default_lot_size", 0.10) or 0.10)
        user_max_risk_pct = float(getattr(risk_cfg, "max_risk_per_trade_pct", settings.MAX_RISK_PER_TRADE_PCT))
        
        from app.services.gold_specs import gold_specs
        specs = gold_specs.get_specs()
        
        point = specs["point_size"]
        tick_size = specs["tick_size"]
        tick_value = specs["tick_value"]
        vol_min = specs["volume_min"]
        vol_max = specs["volume_max"]
        vol_step = specs["volume_step"]

        sl_distance = abs(report.suggested_entry - report.suggested_sl)
        
        if sl_distance <= 0:
            reasons.append("Invalid Stop-Loss distance (0 points).")
            sl_distance = 1.50 # $1.50 gold price movement default
            
        sl_points = sl_distance / (point or 1e-9)

        # Risk amount in USD from user risk percentage setting (Single source of truth)
        risk_pct = user_max_risk_pct / 100.0
        risk_amount_usd = account.equity * risk_pct

        # Runtime Risk Snapshot Logging
        logger.info(
            f"[EFFECTIVE RISK SNAPSHOT] "
            f"UI Min R:R={min_rr} | "
            f"UI Risk%={user_max_risk_pct}% (${risk_amount_usd:.2f}) | "
            f"Lot Mode={lot_mode} | User Lot={user_fixed_lot} | Max Lot={max_allowed_lot} | Default Lot={default_lot} | "
            f"Daily Loss%={daily_loss_pct}% | Daily Loss=${max_daily_loss_amt} | Weekly Loss%={weekly_loss_pct}% | "
            f"Max Open={max_positions} | Max Daily Trades={max_daily_trades} | Max Consec Loss={max_consec_losses} | "
            f"Auto Cooldown={cooldown_seconds}s | Live Lock={getattr(risk_cfg, 'enable_live_trading', False)}"
        )

        if lot_mode == "FIXED":
            calculated_lots = user_fixed_lot
            logger.info(f"[RISK MANAGER PIPELINE STAGE] Mode: FIXED | user_configured_lot={user_fixed_lot} | final_lot={calculated_lots}")
        else:
            # Dynamic Lot size using tick specs
            one_point_value = (point / (tick_size or 1e-9)) * tick_value
            calculated_lots = risk_amount_usd / (sl_points * one_point_value + 1e-9)
            
            # Fallback lot size if dynamic calculation produces 0 or NaN
            if calculated_lots <= 0.0 or not (calculated_lots > 0):
                calculated_lots = min(default_lot, max_allowed_lot)
                logger.info(f"[RISK MANAGER PIPELINE STAGE] Mode: DYNAMIC_RISK Fallback | default_lot={default_lot} | final_lot={calculated_lots}")
            else:
                # Round lots to nearest step
                if vol_step > 0:
                    calculated_lots = round(round(calculated_lots / vol_step) * vol_step, 2)
                logger.info(f"[RISK MANAGER PIPELINE STAGE] Mode: DYNAMIC_RISK | calculated_lots={calculated_lots}")

        # Safety Check: Hard Maximum Allowed Lot Cap
        if calculated_lots > max_allowed_lot:
            reasons.append(
                f"ORDER BLOCKED — EXCEEDS MAX ALLOWED LOT: Calculated lot size ({calculated_lots:.2f}) exceeds maximum allowed lot cap of {max_allowed_lot:.2f} lots."
            )
            calculated_lots = 0.0

        # Safety Check: Broker Min Volume
        elif calculated_lots < vol_min:
            reasons.append(
                f"Calculated lot size ({calculated_lots:.2f}) is below broker minimum of {vol_min:.2f} lots. Trade rejected."
            )
            calculated_lots = 0.0

        # Safety Check: Broker Max Volume Clamping
        elif calculated_lots > vol_max:
            reasons.append(
                f"ORDER BLOCKED — EXCEEDS BROKER MAX VOLUME: Calculated lot size ({calculated_lots:.2f}) exceeds broker max volume of {vol_max:.2f} lots."
            )
            calculated_lots = 0.0

        tp_distance = abs(report.suggested_tp - report.suggested_entry)
        tp_points = tp_distance / (point or 1e-9)

        passed = len(reasons) == 0

        if passed:
            reasons.append(
                f"Risk check PASSED. Mode: {lot_mode} | Executing: {calculated_lots} lots (Max Allowed: {max_allowed_lot} lots | Risking ${risk_amount_usd:.2f} over {sl_points:.0f} points SL)"
            )

        logger.info(f"[RISK EVALUATION COMPLETE] symbol={symbol}, passed={passed}, lot_mode={lot_mode}, calculated_lots={calculated_lots}, reasons={reasons}")

        return RiskCheckResult(
            passed=passed,
            calculated_lots=calculated_lots,
            sl_pips=round(sl_points / 10.0, 1), # Pips for Gold = points / 10
            tp_pips=round(tp_points / 10.0, 1),
            risk_amount_usd=risk_amount_usd,
            reasons=reasons
        )

risk_manager = RiskManager()
