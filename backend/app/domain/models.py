"""
Domain Models and Data Schemas
Clean Architecture Data Definitions for Trading System
"""

from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, field_validator


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class TradeState(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class OrderType(str, Enum):
    MARKET = "MARKET"
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"


class BotStatus(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    EMERGENCY_HALT = "EMERGENCY_HALT"


class EngineState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    ANALYZING = "ANALYZING"
    SIGNAL_READY = "SIGNAL_READY"
    EXECUTING = "EXECUTING"
    POSITION_OPEN = "POSITION_OPEN"
    MANAGING_POSITION = "MANAGING_POSITION"
    EXITING = "EXITING"
    ERROR = "ERROR"
    PAUSED = "PAUSED"


class ConnectionState(str, Enum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting..."
    VALIDATING = "Validating Credentials..."
    FETCHING = "Fetching Account..."
    CONNECTED = "Connected"
    CONNECTION_LOST = "Connection Lost"
    RECONNECTING = "Reconnecting..."
    RECONNECT_FAILED = "Reconnect Failed"


class MarketRegime(str, Enum):
    BULLISH_TREND = "BULLISH_TREND"
    BEARISH_TREND = "BEARISH_TREND"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "M15"
    is_closed: bool = True


class TickerData(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread_pips: float
    spread_points: int = 0
    raw_spread: float = 0.0
    point_size: float = 0.00001
    digits: int = 5
    timestamp: datetime


class MarketStructurePoint(BaseModel):
    type: str
    price: float
    timestamp: datetime
    confirmed: bool = True


class OrderBlock(BaseModel):
    side: TradeSide
    top: float
    bottom: float
    timestamp: datetime
    mitigated: bool = False


class FairValueGap(BaseModel):
    side: TradeSide
    top: float
    bottom: float
    timestamp: datetime
    filled: bool = False


class LiquiditySweep(BaseModel):
    side: TradeSide
    price_level: float
    timestamp: datetime


class MarketStructureAnalysis(BaseModel):
    symbol: str
    timeframe: str
    trend: str
    regime: MarketRegime
    latest_structure: Optional[MarketStructurePoint] = None
    order_blocks: List[OrderBlock] = []
    fvgs: List[FairValueGap] = []
    liquidity_sweeps: List[LiquiditySweep] = []
    vwap: float
    poc_price: float
    value_area_high: float
    value_area_low: float


class HeuristicScoringResult(BaseModel):
    symbol: str
    timeframe: str
    buy_score: float
    sell_score: float
    confidence_score: float
    expected_movement_pips: float
    expected_holding_bars: int
    top_factors: Dict[str, float]
    timestamp: datetime


class ConditionCheckItem(BaseModel):
    id: str
    name: str
    category: str
    passed: bool
    details: str
    weight: float = 0.0
    score_contribution: float = 0.0
    is_critical: bool = False


class ConfirmationBreakdown(BaseModel):
    trend_score_long: float = 0.0
    trend_score_short: float = 0.0
    market_structure_score_long: float = 0.0
    market_structure_score_short: float = 0.0
    momentum_volume_score_long: float = 0.0
    momentum_volume_score_short: float = 0.0
    heuristic_score_long: float = 0.0
    heuristic_score_short: float = 0.0
    risk_news_score: float = 0.0
    long_score: float = 0.0
    short_score: float = 0.0
    passed: bool = False

    # Retired fields (kept as optional to maintain existing tests compatibility)
    trend_score: Optional[float] = 0.0
    market_structure_score: Optional[float] = 0.0
    momentum_volume_score: Optional[float] = 0.0
    heuristic_score: Optional[float] = 0.0
    total_score: Optional[float] = 0.0


class TradeDecisionReport(BaseModel):
    symbol: str
    decision: TradeSide
    timeframe: str
    confidence: float
    confirmation_score: float
    long_score: float = 0.0
    short_score: float = 0.0
    reasoning: List[str]
    rejection_reasons: List[str]
    suggested_entry: float
    suggested_sl: float
    suggested_tp: float
    suggested_lots: float
    risk_reward_ratio: float
    breakdown: ConfirmationBreakdown
    condition_matrix: List[ConditionCheckItem] = []
    htf_alignment: Dict[str, str] = {}
    candlestick_pattern: str = "None"
    liquidity_status: str = "Normal"
    estimated_holding_bars: int = 6
    summary_explanation: str = ""
    outweighing_factors: List[str] = []
    setup_name: str = "None"
    potential_loss_usd: float = 0.0
    potential_profit_usd: float = 0.0
    timestamp: datetime


class RiskCheckResult(BaseModel):
    passed: bool
    calculated_lots: float
    sl_pips: float
    tp_pips: float
    risk_amount_usd: float
    reasons: List[str]


class TradeRecord(BaseModel):
    trade_id: str
    symbol: str
    side: TradeSide
    order_type: OrderType
    entry_price: float
    sl_price: float
    tp_price: float
    lots: float
    state: TradeState
    open_time: datetime
    close_time: Optional[datetime] = None
    close_price: Optional[float] = None
    pnl_usd: float = 0.0
    pnl_pips: float = 0.0
    peak_profit_rr: float = 0.0
    exit_reason: Optional[str] = None
    ai_confidence_at_entry: float = 0.80
    entry_reasoning: List[str] = []
    max_favorable_excursion_pips: float = 0.0
    max_adverse_excursion_pips: float = 0.0
    setup_name: str = "Unknown"
    timeframe: str = "M15"
    planned_loss_usd: float = 0.0
    planned_profit_usd: float = 0.0
    potential_loss_usd: float = 0.0
    potential_profit_usd: float = 0.0


class PendingOrderRecord(BaseModel):
    order_id: str
    symbol: str
    side: TradeSide
    order_type: OrderType
    target_price: float
    sl_price: float
    tp_price: float
    lots: float
    open_time: datetime
    state: TradeState = TradeState.PENDING


class MT5ConnectRequest(BaseModel):
    login: int
    password: str
    server: str
    broker: Optional[str] = ""
    name: Optional[str] = None
    terminal_path: Optional[str] = None


class ManualOrderRequest(BaseModel):
    symbol: str
    side: TradeSide
    order_type: OrderType = OrderType.MARKET
    lots: float = 0.01
    price: Optional[float] = 0.0
    sl: Optional[float] = 0.0
    tp: Optional[float] = 0.0


class RiskSettings(BaseModel):
    max_risk_per_trade_pct: float = 1.5
    daily_loss_limit_pct: float = 5.0
    weekly_loss_limit_pct: float = Field(default=7.1, gt=0.0, le=100.0)

    @field_validator("weekly_loss_limit_pct", mode="after")
    @classmethod
    def validate_weekly_loss_limit(cls, v: float) -> float:
        if v <= 0.0 or v > 100.0:
            raise ValueError("weekly_loss_limit_pct must be > 0.0 and <= 100.0")
        return v
    max_daily_loss_amount: float = 5.0
    max_open_positions: int = 2
    max_positions_per_direction: int = 1
    min_minutes_between_entries: float = 30.0
    blocked_hours: List[int] = Field(default_factory=lambda: [18, 19, 20, 21])
    min_risk_reward_ratio: float = 2.0
    trading_mode_profile: str = "BALANCED"  # CONSERVATIVE, BALANCED, AGGRESSIVE
    strategy_profile: str = "HYBRID_AUTO"  # HYBRID_AUTO, BB_MEAN_REVERSION, BB_TREND_FOLLOWING, BB_BREAKOUT, INSTITUTIONAL_ONLY
    signal_mode: str = "CLOSED_CANDLE"  # CLOSED_CANDLE or CURRENT_CANDLE
    entry_timeframe: str = "M15"  # M15 (default), M5, M1, M30, H1, H4, D1
    entry_confirmation_mode: str = "IMMEDIATE"  # IMMEDIATE (default) or CONFIRM_NEXT_CANDLE
    bb_period: int = 20
    bb_std: float = 2.0
    allowed_sessions: List[str] = ["LONDON", "NEWYORK", "LONDON_NY_OVERLAP"]
    lot_sizing_mode: str = "DYNAMIC_RISK"  # "FIXED" or "DYNAMIC_RISK"
    user_configured_lot: float = 0.01
    max_allowed_lot: float = 0.10
    default_lot_size: float = 0.01
    trailing_stop_pips: float = 15.0
    trailing_activation_rr: float = 1.2
    trailing_atr_multiplier: float = 2.0
    break_even_rr: float = 1.0
    giveback_guard_enabled: bool = True
    giveback_guard_min_rr: float = 1.5
    giveback_guard_retrace_pct: float = 50.0
    auto_breakeven_enabled: bool = True
    auto_trailing_stop_enabled: bool = True
    min_ai_confidence: float = 0.70
    auto_trade_enabled: bool = False
    auto_trade_cooldown_seconds: int = 300
    max_trades_per_day: int = 10
    max_consecutive_losses: int = 3
    max_spread_pips: float = 2.5
    enable_live_trading: bool = False
    htf_alignment_filter_enabled: bool = True
    htf_alignment_mode: str = "STRICT"
    disabled_setups: List[str] = Field(default_factory=lambda: [
        "BB Midline Pullback",
        "SETUP D: Bullish BOS Breakout & Retest",
        "SETUP D: Bearish BOS Breakdown & Retest",
        "SETUP F: Bullish Volume & Momentum Expansion",
        "SETUP F: Bearish Volume & Momentum Expansion",
        "BB Band Breakout / Momentum"
    ])
    active_model_version: Optional[str] = None



class BacktestRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "M15"
    count: int = 500
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    initial_balance: float = 10000.0
    risk_pct: float = 1.0
    lot_sizing_mode: str = "FIXED"
    user_configured_lot: float = 0.10
    min_risk_reward_ratio: float = 2.0
    auto_trade_cooldown_seconds: int = 300
    daily_loss_limit_pct: float = 3.0
    max_consecutive_losses: int = 3
    max_spread_pips: float = 2.5
    min_ai_confidence: float = 0.70


class AccountPreview(BaseModel):
    success: bool
    account_number: int
    account_name: Optional[str] = ""
    broker: Optional[str] = ""
    server: Optional[str] = ""
    account_type: Optional[str] = ""  # Demo or Real
    currency: Optional[str] = ""
    leverage: Optional[str] = ""
    balance: float = 0.0
    equity: float = 0.0
    free_margin: float = 0.0
    margin: float = 0.0
    margin_level_pct: float = 0.0
    company: Optional[str] = ""
    terminal_build: Optional[str] = ""
    status: str = ""  # Verified or Failed
    error_reason: Optional[str] = None
    diagnostic_logs: List[str] = []  # Step-by-step MT5 diagnostic messages


class SavedAccount(BaseModel):
    id: str
    name: str
    login: int
    password: str
    server: str
    broker: str
    account_type: Optional[str] = "Demo"
    currency: Optional[str] = "USD"
    leverage: Optional[str] = ""
    terminal_path: Optional[str] = None
    last_connected: Optional[datetime] = None
    is_active: bool = False


class SavedAccountPublic(BaseModel):
    id: str
    name: str
    login: int
    server: str
    broker: str
    account_type: Optional[str] = "Demo"
    currency: Optional[str] = "USD"
    leverage: Optional[str] = ""
    terminal_path: Optional[str] = None
    last_connected: Optional[datetime] = None
    is_active: bool = False


class LoginHistoryEntry(BaseModel):
    timestamp: datetime
    account_number: int
    broker: str
    server: str
    success: bool
    reason: str


class AccountInfo(BaseModel):
    account_number: int
    account_name: Optional[str] = ""
    broker: Optional[str] = ""
    server: Optional[str] = ""
    account_type: Optional[str] = ""
    currency: Optional[str] = ""
    leverage: Optional[str] = ""
    company: Optional[str] = ""
    terminal_build: Optional[str] = ""
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level_pct: float = 0.0
    floating_profit: float = 0.0
    daily_pnl_usd: float = 0.0
    weekly_pnl_usd: float = 0.0
    peak_equity: float = 0.0
    drawdown_pct: float = 0.0
    open_positions_count: int = 0
    pending_orders_count: int = 0
    is_connected: bool = False
    connection_mode: str = "LIVE_MT5"
    detailed_status: ConnectionState = ConnectionState.DISCONNECTED
    last_error: Optional[str] = None


class ConnectionDiagnostics(BaseModel):
    terminal_path: Optional[str] = ""
    terminal_exists: str = "NO"  # "YES" / "NO"
    mt5_initialize: str = "FAIL"  # "PASS" / "FAIL"
    mt5_initialize_error: Optional[str] = ""
    login: str = "FAIL"  # "PASS" / "FAIL"
    login_error: Optional[str] = ""
    requested_account: int = 0
    connected_account: int = 0
    requested_server: str = ""
    connected_server: str = ""
    account_info: str = "FAIL"  # "PASS" / "FAIL"
    final_status: str = "FAILED"  # "CONNECTED" / "FAILED"


class AccountConnectionResponse(BaseModel):
    success: bool
    message: str
    account: Optional[AccountInfo] = None
    diagnostics: Optional[ConnectionDiagnostics] = None



