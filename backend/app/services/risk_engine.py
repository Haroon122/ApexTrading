"""
Centralized, Stateless Risk Management & Circuit Breaker Engine
Shared between Live Trading (TradeManager) and Historical Backtesting (BacktestingEngine).
Enforces independent risk protections without un-intended if/elif coupling.
"""

from datetime import datetime, date
from typing import Dict, Any, Optional, Tuple
from enum import Enum


class RiskBlockReason(str, Enum):
    NONE = "NONE"
    ACCOUNT_BANKRUPT = "ACCOUNT_BANKRUPT"
    INSUFFICIENT_EQUITY = "INSUFFICIENT_EQUITY"
    FIXED_LOT_RISK_EXCEEDS_LIMIT = "FIXED_LOT_RISK_EXCEEDS_LIMIT"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    COOLDOWN = "COOLDOWN"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    CONSECUTIVE_LOSS_LIMIT = "CONSECUTIVE_LOSS_LIMIT"
    CONFIDENCE_TOO_LOW = "CONFIDENCE_TOO_LOW"
    INSUFFICIENT_RISK_REWARD = "INSUFFICIENT_RISK_REWARD"
    NEWS_BLACKOUT = "NEWS_BLACKOUT"
    MAX_TRADES_PER_DAY = "MAX_TRADES_PER_DAY"


class RiskEngine:
    """
    Stateless Risk Management & Circuit Breaker Engine.
    Receives current market, account, and risk settings explicitly.
    """

    @classmethod
    def evaluate_trade_risk(
        cls,
        symbol: str,
        current_time: datetime,
        current_spread_pips: float,
        report_confidence: float,
        report_rr_ratio: float,
        account_balance: float,
        start_of_day_balance: float,
        daily_realized_pnl: float,
        consecutive_losses: int,
        last_trade_exit_time: Optional[datetime],
        trade_lots: float,
        sl_pips: float,
        pip_value_per_lot: float,
        commission_per_lot: float,
        max_spread_pips: float,
        auto_trade_cooldown_seconds: int,
        daily_loss_limit_pct: float,
        max_consecutive_losses: int,
        min_ai_confidence: float,
        min_risk_reward_ratio: float,
        lot_sizing_mode: str = "FIXED"
    ) -> Tuple[bool, str, str]:
        """
        Evaluates trade safety against all independent risk gates in pipeline order.
        Returns: Tuple[passed: bool, block_reason: str, block_details: str]
        """
        # 1. Account Bankruptcy / Equity Check
        if account_balance <= 0.0:
            return False, RiskBlockReason.ACCOUNT_BANKRUPT.value, f"Account balance exhausted (${account_balance:.2f})"

        # 2. Fixed Lot / Position Sizing Safety Check
        if trade_lots > 0:
            estimated_loss_usd = (sl_pips * pip_value_per_lot * trade_lots) + (trade_lots * commission_per_lot)
            if estimated_loss_usd > account_balance:
                return False, RiskBlockReason.FIXED_LOT_RISK_EXCEEDS_LIMIT.value, (
                    f"Estimated trade risk (${estimated_loss_usd:.2f}) exceeds current account equity (${account_balance:.2f})"
                )

        # 3. Spread Filter Check
        if max_spread_pips > 0 and current_spread_pips > max_spread_pips:
            return False, RiskBlockReason.SPREAD_TOO_WIDE.value, (
                f"Current spread ({current_spread_pips:.1f} pips) exceeds maximum limit ({max_spread_pips:.1f} pips)"
            )

        # 4. Cooldown Filter Check
        if auto_trade_cooldown_seconds > 0 and last_trade_exit_time is not None:
            elapsed_sec = (current_time - last_trade_exit_time).total_seconds()
            if elapsed_sec < auto_trade_cooldown_seconds:
                remaining = int(auto_trade_cooldown_seconds - elapsed_sec)
                return False, RiskBlockReason.COOLDOWN.value, (
                    f"Auto-trade cooldown active ({remaining}s remaining)"
                )

        # 5. Daily Loss Limit Filter Check
        if daily_loss_limit_pct > 0 and start_of_day_balance > 0:
            max_daily_loss_usd = start_of_day_balance * (daily_loss_limit_pct / 100.0)
            if daily_realized_pnl <= -max_daily_loss_usd:
                return False, RiskBlockReason.DAILY_LOSS_LIMIT.value, (
                    f"Daily loss limit breached: Realized P&L (${daily_realized_pnl:.2f}) <= -{daily_loss_limit_pct}% limit (${max_daily_loss_usd:.2f})"
                )

        # 6. Max Consecutive Loss Filter Check
        if max_consecutive_losses > 0 and consecutive_losses >= max_consecutive_losses:
            return False, RiskBlockReason.CONSECUTIVE_LOSS_LIMIT.value, (
                f"Max consecutive loss limit reached ({consecutive_losses}/{max_consecutive_losses})"
            )

        # 7. AI Confidence Filter Check
        if min_ai_confidence > 0 and report_confidence < min_ai_confidence:
            return False, RiskBlockReason.CONFIDENCE_TOO_LOW.value, (
                f"Signal confidence ({report_confidence * 100:.1f}%) < required minimum ({min_ai_confidence * 100:.1f}%)"
            )

        # 8. Risk:Reward Ratio Filter Check
        if min_risk_reward_ratio > 0 and report_rr_ratio < min_risk_reward_ratio:
            return False, RiskBlockReason.INSUFFICIENT_RISK_REWARD.value, (
                f"Risk:Reward ratio ({report_rr_ratio:.2f}:1) < required minimum ({min_risk_reward_ratio:.2f}:1)"
            )

        return True, RiskBlockReason.NONE.value, "All risk checks passed"

risk_engine = RiskEngine()
