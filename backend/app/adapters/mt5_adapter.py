"""
MetaTrader 5 Adapter Module
Live MT5 Terminal Client & Adapter Interface
Integrated directly with Live MT5SessionManager Engine
"""

import math
import random
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Any

from app.core.config import settings
from app.domain.models import (
    Candle, TickerData, AccountInfo, AccountPreview, TradeRecord, PendingOrderRecord,
    TradeSide, TradeState, OrderType, ConnectionState, ConnectionDiagnostics
)
from app.services.session_manager import session_manager, MT5_AVAILABLE

logger = logging.getLogger("ApexTrader.MT5Adapter")

if MT5_AVAILABLE:
    import MetaTrader5 as mt5


TF_MINUTES_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
    "W1": 10080,
    "MN1": 43200,
    "MN": 43200,
}


MT5_RETCODE_DESCRIPTIONS = {
    10004: "Requote - Price changed before execution",
    10006: "Request rejected by broker",
    10007: "Request cancelled by trader",
    10008: "Order placed successfully",
    10009: "Order executed successfully",
    10010: "Only part of the order was executed",
    10011: "Trade request processing error",
    10012: "Request cancelled by timeout",
    10013: "Invalid trade request parameters",
    10014: "Invalid volume / lot size",
    10015: "Invalid order price",
    10016: "Invalid Stop Loss or Take Profit level",
    10017: "Trade disabled by broker",
    10018: "Market is closed for this symbol",
    10019: "Insufficient margin / free funds to open trade",
    10020: "Prices changed - retry order",
    10021: "No quote - server has no price for symbol",
    10022: "Order expiration date invalid",
    10023: "Order state changed",
    10024: "Too many request attempts",
    10025: "No changes in request",
    10026: "Autotrading disabled by server / broker",
    10027: "Algo Trading is DISABLED in MetaTrader 5 terminal! Open MT5 desktop software and click the 'Algo Trading' button at the top toolbar (or press Ctrl+E) to allow API trades.",
    10028: "Position locked",
    10029: "Order volume exceeds symbol limit",
    10030: "Invalid order filling type",
    10031: "Connection with trade server lost",
    10032: "Order allowed for live accounts only",
    10033: "Maximum number of open positions reached",
    10034: "Maximum order volume reached",
}


def _get_retcode_msg(code: int, comment: str = "") -> str:
    desc = MT5_RETCODE_DESCRIPTIONS.get(code, comment or "Order rejected by broker")
    return f"[Retcode {code}]: {desc}"


def _get_symbol_pip_size(symbol: str, point: float, digits: int) -> float:
    from app.services.symbol_specs import get_symbol_specs
    return get_symbol_specs(symbol)["pip_size"]


class MT5Adapter:
    """
    Main MetaTrader 5 Adapter.
    Delegates account validation, session management, and preview directly to MT5SessionManager.
    Supports M1-MN1 timeframes, pending orders, positions management, and real-time execution.
    """
    def __init__(self):
        self.session_manager = session_manager

    def initialize(self) -> bool:
        success, msg, _ = self.session_manager.auto_connect_running_terminal()
        if not success:
            logger.info("[MT5 ADAPTER] Auto-connect at startup did not find an active terminal account. Waiting for user login.")
        return success

    def fetch_account_preview(self, login: int, password: str, server: str, broker: str = "", terminal_path: Optional[str] = None) -> AccountPreview:
        return self.session_manager.fetch_account_preview(login, password, server, broker, custom_terminal_path=terminal_path)

    def connect_account(self, login: int, password: str, server: str, broker: str = "", name: str = None, terminal_path: Optional[str] = None) -> Tuple[bool, str, AccountInfo, ConnectionDiagnostics]:
        return self.session_manager.connect_session(login, password, server, broker, name, custom_terminal_path=terminal_path)

    def disconnect_account(self) -> bool:
        return self.session_manager.disconnect_session()

    def get_account_info(self) -> AccountInfo:
        return self.session_manager.get_account_info()

    @property
    def is_connected(self) -> bool:
        return self.session_manager.use_live_mt5

    def discover_gold_symbol(self) -> str:
        """
        Dynamically scan active broker symbols and discover the Gold symbol.
        Sets the discovered symbol in the GoldSymbolSpecService.
        """
        from app.services.gold_specs import gold_specs
        
        # 1. Fallback if MT5 not available or offline
        if not MT5_AVAILABLE or not self.session_manager.use_live_mt5:
            gold_specs.set_discovered_symbol("XAUUSD")
            return "XAUUSD"
            
        try:
            # Query all symbols
            symbols = mt5.symbols_get()
            if symbols:
                # Priority search: check exact matches in a predefined list first
                priority_list = ["XAUUSD", "GOLD", "XAUUSDm", "XAUUSD.a", "GOLDm", "XAUUSD.s", "XAUUSD.i", "XAUUSD.g", "XAUUSD.c"]
                symbol_names = {s.name.upper(): s.name for s in symbols}
                
                for p_symbol in priority_list:
                    if p_symbol.upper() in symbol_names:
                        orig_name = symbol_names[p_symbol.upper()]
                        gold_specs.set_discovered_symbol(orig_name)
                        return orig_name
                        
                # If no priority exact match, search for any symbol containing "XAU" or "GOLD"
                matches = []
                for s in symbols:
                    name_up = s.name.upper()
                    if "XAU" in name_up or "GOLD" in name_up:
                        matches.append(s.name)
                        
                if matches:
                    # Pick the shortest matching symbol name as it is usually the primary tradeable one
                    shortest_match = min(matches, key=len)
                    gold_specs.set_discovered_symbol(shortest_match)
                    return shortest_match
        except Exception as e:
            logger.error(f"[MT5 ADAPTER] Error in discover_gold_symbol: {e}")
            
        # Default fallback
        gold_specs.set_discovered_symbol("XAUUSD")
        return "XAUUSD"


    def get_symbol_info(self, symbol: str):
        if not MT5_AVAILABLE:
            return None
        try:
            sym_info = mt5.symbol_info(symbol)
            if not sym_info and self.is_connected:
                mt5.symbol_select(symbol, True)
                sym_info = mt5.symbol_info(symbol)
            return sym_info
        except Exception:
            return None

    def get_ticker(self, symbol: str) -> TickerData:
        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                tick = mt5.symbol_info_tick(symbol)
                sym_info = mt5.symbol_info(symbol)
                if tick and sym_info:
                    point = float(sym_info.point or 0.00001)
                    digits = int(sym_info.digits or 5)
                    raw_spread = round(float(tick.ask - tick.bid), digits)
                    pip_size = _get_symbol_pip_size(symbol, point, digits)
                    spread_pips = round(raw_spread / pip_size, 1)
                    spread_points = int(round(raw_spread / point)) if point > 0 else 0

                    return TickerData(
                        symbol=symbol,
                        bid=float(tick.bid),
                        ask=float(tick.ask),
                        spread_pips=spread_pips,
                        spread_points=spread_points,
                        raw_spread=raw_spread,
                        point_size=point,
                        digits=digits,
                        timestamp=datetime.fromtimestamp(tick.time)
                    )
            except Exception as e:
                logger.error(f"Live MT5 get_ticker failed: {e}")
                
        return self._generate_tick(symbol)

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str = "M15",
        count: int = 200,
        before_datetime: Optional[datetime] = None,
        from_datetime: Optional[datetime] = None,
        to_datetime: Optional[datetime] = None
    ) -> List[Candle]:
        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                tf_map = {
                    "M1": mt5.TIMEFRAME_M1,
                    "M5": mt5.TIMEFRAME_M5,
                    "M15": mt5.TIMEFRAME_M15,
                    "M30": mt5.TIMEFRAME_M30,
                    "H1": mt5.TIMEFRAME_H1,
                    "H4": mt5.TIMEFRAME_H4,
                    "D1": mt5.TIMEFRAME_D1,
                    "W1": mt5.TIMEFRAME_W1,
                    "MN1": mt5.TIMEFRAME_MN1,
                    "MN": mt5.TIMEFRAME_MN1,
                }
                mt5_tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M15)
                
                if from_datetime is not None and to_datetime is not None:
                    f_dt = from_datetime.astimezone(timezone.utc).replace(tzinfo=None) if from_datetime.tzinfo else from_datetime
                    t_dt = to_datetime.astimezone(timezone.utc).replace(tzinfo=None) if to_datetime.tzinfo else to_datetime
                    rates = mt5.copy_rates_range(symbol, mt5_tf, f_dt, t_dt)
                elif before_datetime is not None:
                    dt = before_datetime.astimezone(timezone.utc).replace(tzinfo=None) if before_datetime.tzinfo else before_datetime
                    rates = mt5.copy_rates_from(symbol, mt5_tf, dt, count)
                else:
                    rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)

                if rates is not None and len(rates) >= 40:
                    candles = []
                    for i, r in enumerate(rates):
                        is_closed = True if (before_datetime is not None or from_datetime is not None) else (i < len(rates) - 1)
                        candles.append(Candle(
                            timestamp=datetime.fromtimestamp(r['time']),
                            open=float(r['open']),
                            high=float(r['high']),
                            low=float(r['low']),
                            close=float(r['close']),
                            volume=float(r['tick_volume']),
                            timeframe=timeframe,
                            is_closed=is_closed
                        ))
                    return candles
            except Exception as e:
                logger.error(f"Live MT5 fetch_candles failed: {e}")

        return self._generate_candles(symbol, timeframe, count, before_datetime, from_datetime, to_datetime)

    def validate_and_normalize_order_request(
        self,
        symbol: str,
        side: TradeSide,
        order_type: OrderType,
        price: float,
        lots: float,
        sl: float,
        tp: float
    ) -> Tuple[bool, float, float, float, float, str]:
        discovered_gold = self.discover_gold_symbol()
        if symbol.upper() != discovered_gold.upper():
            return False, 0.0, 0.0, 0.0, 0.0, f"Order blocked: Only the active discovered Gold symbol ({discovered_gold}) can be traded. Attempted to trade {symbol}."

        if not MT5_AVAILABLE or not self.session_manager.use_live_mt5:
            # Simulated normalization fallback using GoldSymbolSpecService
            from app.services.gold_specs import gold_specs
            specs = gold_specs.get_specs()
            digits = specs["digits"]
            point = specs["point_size"]
            stops_level_pts = specs["stop_level"]
            min_stop_pts = max(stops_level_pts, 10)
            min_stop_dist = round(min_stop_pts * point, digits)
            
            from app.services.trade_manager import trade_manager
            max_allowed = getattr(trade_manager.risk_settings, "max_allowed_lot", 1.00) or 1.00

            vol_min = specs["volume_min"]
            vol_max = specs["volume_max"]
            vol_step = specs["volume_step"]
            
            norm_lots = float(lots)
            if norm_lots <= 0:
                return False, 0.0, 0.0, 0.0, 0.0, "ORDER BLOCKED — INVALID LOT SIZE: Volume must be > 0"
            if norm_lots > max_allowed:
                return False, 0.0, 0.0, 0.0, 0.0, f"ORDER BLOCKED — EXCEEDS MAX ALLOWED LOT: Requested {norm_lots} lots > max allowed lot cap {max_allowed}"
            if norm_lots < vol_min:
                norm_lots = vol_min
            if norm_lots > vol_max:
                return False, 0.0, 0.0, 0.0, 0.0, f"ORDER BLOCKED — EXCEEDS BROKER MAX VOLUME: Requested {norm_lots} > broker max {vol_max}"
            if vol_step > 0:
                norm_lots = round(round(norm_lots / vol_step) * vol_step, 2)
                
            entry_price = price if price and price > 0 else (2300.00 if side == TradeSide.BUY else 2299.80)
            entry_price = round(entry_price, digits)
        else:
            sym_info = mt5.symbol_info(symbol)
            if not sym_info:
                if not mt5.symbol_select(symbol, True):
                    return False, 0.0, 0.0, 0.0, 0.0, f"Symbol '{symbol}' not found on MT5 broker server"
                sym_info = mt5.symbol_info(symbol)
                if not sym_info:
                    return False, 0.0, 0.0, 0.0, 0.0, f"Symbol '{symbol}' specifications unavailable"

            if not sym_info.visible:
                mt5.symbol_select(symbol, True)
                sym_info = mt5.symbol_info(symbol)

            if hasattr(sym_info, 'trade_mode') and sym_info.trade_mode == 0:
                return False, 0.0, 0.0, 0.0, 0.0, f"Trading is disabled for symbol '{symbol}' on broker server"

            digits = int(sym_info.digits)
            point = float(sym_info.point)
            stops_level_pts = int(getattr(sym_info, 'trade_stops_level', 0) or 0)
            
            min_stop_pts = max(stops_level_pts, 10)
            min_stop_dist = round(min_stop_pts * point, digits)

            from app.services.trade_manager import trade_manager
            max_allowed = getattr(trade_manager.risk_settings, "max_allowed_lot", 1.00) or 1.00

            vol_min = float(getattr(sym_info, 'volume_min', 0.01) or 0.01)
            vol_max = float(getattr(sym_info, 'volume_max', 100.0) or 100.0)
            vol_step = float(getattr(sym_info, 'volume_step', 0.01) or 0.01)

            norm_lots = float(lots)
            if norm_lots <= 0:
                return False, 0.0, 0.0, 0.0, 0.0, "ORDER BLOCKED — INVALID LOT SIZE: Volume must be > 0"
            if norm_lots > max_allowed:
                return False, 0.0, 0.0, 0.0, 0.0, f"ORDER BLOCKED — EXCEEDS MAX ALLOWED LOT: Requested {norm_lots} lots > max allowed lot cap {max_allowed}"
            if norm_lots < vol_min:
                norm_lots = vol_min
            if norm_lots > vol_max:
                return False, 0.0, 0.0, 0.0, 0.0, f"ORDER BLOCKED — EXCEEDS BROKER MAX VOLUME: Requested {norm_lots} > broker max {vol_max}"
            if vol_step > 0:
                norm_lots = round(round(norm_lots / vol_step) * vol_step, 2)

            if order_type == OrderType.MARKET:
                entry_price = sym_info.ask if side == TradeSide.BUY else sym_info.bid
            else:
                entry_price = price if price and price > 0 else (sym_info.ask if side == TradeSide.BUY else sym_info.bid)
            
            entry_price = round(entry_price, digits)

        # Validate requested bounds before normalization: BUY: SL < Entry < TP, SELL: TP < Entry < SL
        if side == TradeSide.BUY:
            if sl and sl > 0 and sl >= entry_price:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid stop loss for BUY: SL ({sl:.{digits}f}) must be < entry ({entry_price:.{digits}f})"
            if tp and tp > 0 and tp <= entry_price:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid take profit for BUY: TP ({tp:.{digits}f}) must be > entry ({entry_price:.{digits}f})"
        elif side == TradeSide.SELL:
            if sl and sl > 0 and sl <= entry_price:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid stop loss for SELL: SL ({sl:.{digits}f}) must be > entry ({entry_price:.{digits}f})"
            if tp and tp > 0 and tp >= entry_price:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid take profit for SELL: TP ({tp:.{digits}f}) must be < entry ({entry_price:.{digits}f})"

        norm_sl = 0.0
        norm_tp = 0.0

        if side == TradeSide.BUY:
            if sl and sl > 0:
                max_allowed_sl = round(entry_price - min_stop_dist, digits)
                if sl >= entry_price or (entry_price - sl) < min_stop_dist:
                    norm_sl = max_allowed_sl
                    logger.info(f"[MT5 STOP LEVEL ADJUST] BUY SL adjusted from {sl:.{digits}f} to minimum valid level {norm_sl:.{digits}f} (min_stop_dist={min_stop_dist:.{digits}f})")
                else:
                    norm_sl = round(sl, digits)

            if tp and tp > 0:
                min_allowed_tp = round(entry_price + min_stop_dist, digits)
                if tp <= entry_price or (tp - entry_price) < min_stop_dist:
                    norm_tp = min_allowed_tp
                    logger.info(f"[MT5 STOP LEVEL ADJUST] BUY TP adjusted from {tp:.{digits}f} to minimum valid level {norm_tp:.{digits}f} (min_stop_dist={min_stop_dist:.{digits}f})")
                else:
                    norm_tp = round(tp, digits)

        elif side == TradeSide.SELL:
            if sl and sl > 0:
                min_allowed_sl = round(entry_price + min_stop_dist, digits)
                if sl <= entry_price or (sl - entry_price) < min_stop_dist:
                    norm_sl = min_allowed_sl
                    logger.info(f"[MT5 STOP LEVEL ADJUST] SELL SL adjusted from {sl:.{digits}f} to minimum valid level {norm_sl:.{digits}f} (min_stop_dist={min_stop_dist:.{digits}f})")
                else:
                    norm_sl = round(sl, digits)

            if tp and tp > 0:
                max_allowed_tp = round(entry_price - min_stop_dist, digits)
                if tp >= entry_price or (entry_price - tp) < min_stop_dist:
                    norm_tp = max_allowed_tp
                    logger.info(f"[MT5 STOP LEVEL ADJUST] SELL TP adjusted from {tp:.{digits}f} to minimum valid level {norm_tp:.{digits}f} (min_stop_dist={min_stop_dist:.{digits}f})")
                else:
                    norm_tp = round(tp, digits)

        logger.info(
            f"[MT5 VALIDATED REQUEST] Symbol: {symbol} ({digits} digits, point={point}, stops_level={stops_level_pts}pts) | "
            f"Side: {side.value} | Lots: {norm_lots} | Entry: {entry_price:.{digits}f} | "
            f"SL: {norm_sl:.{digits}f} | TP: {norm_tp:.{digits}f} | MinStopDist: {min_stop_dist:.{digits}f}"
        )

        # ── RISK AND R:R RE-VALIDATION ──────────────────────────────────────────
        # Check order bounds: BUY: SL < Entry < TP, SELL: TP < Entry < SL
        if side == TradeSide.BUY:
            if norm_sl > 0 and norm_sl >= entry_price:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid stop loss for BUY: SL ({norm_sl:.{digits}f}) must be < entry ({entry_price:.{digits}f})"
            if norm_tp > 0 and norm_tp <= entry_price:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid take profit for BUY: TP ({norm_tp:.{digits}f}) must be > entry ({entry_price:.{digits}f})"
            if norm_sl > 0 and norm_tp > 0 and norm_sl >= norm_tp:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid stop levels for BUY: SL ({norm_sl:.{digits}f}) must be < TP ({norm_tp:.{digits}f})"
        elif side == TradeSide.SELL:
            if norm_sl > 0 and norm_sl <= entry_price:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid stop loss for SELL: SL ({norm_sl:.{digits}f}) must be > entry ({entry_price:.{digits}f})"
            if norm_tp > 0 and norm_tp >= entry_price:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid take profit for SELL: TP ({norm_tp:.{digits}f}) must be < entry ({entry_price:.{digits}f})"
            if norm_sl > 0 and norm_tp > 0 and norm_sl <= norm_tp:
                return False, 0.0, 0.0, 0.0, 0.0, f"Invalid stop levels for SELL: SL ({norm_sl:.{digits}f}) must be > TP ({norm_tp:.{digits}f})"

        # Check recalculated risk and R:R
        if norm_sl > 0:
            from app.services.symbol_specs import get_symbol_specs
            specs = get_symbol_specs(symbol)
            p_size = specs["pip_size"]
            pip_val = specs["pip_value_per_lot"]
            
            sl_dist = abs(entry_price - norm_sl)
            sl_pips = sl_dist / p_size
            recalc_risk_usd = norm_lots * sl_pips * pip_val
            
            # Fetch equity for risk limits check
            account = self.session_manager.get_account_info()
            from app.services.trade_manager import trade_manager
            max_risk_pct = float(getattr(trade_manager.risk_settings, "max_risk_per_trade_pct", settings.MAX_RISK_PER_TRADE_PCT))
            max_risk_usd = account.equity * (max_risk_pct / 100.0)
            
            # If calculated risk exceeds settings limit, reject the order
            if recalc_risk_usd > max_risk_usd * 1.05 and max_risk_usd > 0:
                return False, 0.0, 0.0, 0.0, 0.0, f"Recalculated risk of ${recalc_risk_usd:.2f} exceeds maximum allowed risk of ${max_risk_usd:.2f} ({max_risk_pct}% of equity). Trade rejected."
            
            # If TP is also set, verify R:R ratio
            if norm_tp > 0:
                tp_dist = abs(norm_tp - entry_price)
                recalc_rr = tp_dist / sl_dist if sl_dist > 0 else 0.0
                min_rr = float(getattr(trade_manager.risk_settings, "min_risk_reward_ratio", settings.MIN_RISK_REWARD_RATIO))
                if recalc_rr < min_rr * 0.95:
                    return False, 0.0, 0.0, 0.0, 0.0, f"Recalculated Risk:Reward ratio 1:{recalc_rr:.2f} is below minimum required 1:{min_rr:.2f}. Trade rejected."

        return True, entry_price, norm_lots, norm_sl, norm_tp, "Request validated and normalized"


    def place_order(
        self, symbol: str, side: TradeSide, lots: float, sl: float, tp: float, reasoning: List[str] = None, ai_confidence: float = 0.80
    ) -> Tuple[bool, Optional[TradeRecord], str]:
        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                valid, price, norm_lots, sl_val, tp_val, val_msg = self.validate_and_normalize_order_request(
                    symbol, side, OrderType.MARKET, 0.0, lots, sl, tp
                )
                if not valid:
                    return False, None, val_msg

                # Pre-Send Safety Check: Volume Mismatch Guard
                if abs(norm_lots - lots) > 0.05 * max(lots, 0.01) and abs(norm_lots - lots) > 0.01:
                    err_msg = f"ORDER BLOCKED — VOLUME MISMATCH: Requested {lots:.2f} lots, but normalized volume became {norm_lots:.2f} lots. Order was NOT sent to broker."
                    logger.error(f"[MT5 ADAPTER SAFETY BLOCK] {err_msg}")
                    from app.services.trade_manager import trade_manager
                    trade_manager.stop_bot()
                    return False, None, err_msg

                sym_info = mt5.symbol_info(symbol)
                order_type = mt5.ORDER_TYPE_BUY if side == TradeSide.BUY else mt5.ORDER_TYPE_SELL

                filling_type = mt5.ORDER_FILLING_IOC
                if sym_info.filling_mode & 2:
                    filling_type = mt5.ORDER_FILLING_IOC
                elif sym_info.filling_mode & 1:
                    filling_type = mt5.ORDER_FILLING_FOK
                else:
                    filling_type = mt5.ORDER_FILLING_RETURN

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": float(norm_lots),
                    "type": order_type,
                    "price": price,
                    "sl": sl_val,
                    "tp": tp_val,
                    "deviation": 20,
                    "magic": 998877,
                    "comment": "ApexTrader AI Order",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling_type,
                }

                logger.info(
                    f"[MT5 ORDER REQUEST PRE-SEND] "
                    f"symbol={symbol}, type={order_type} ({side.value}), volume={norm_lots:.2f}, price={price}, sl={sl_val}, tp={tp_val}, magic=998877"
                )
                result = mt5.order_send(request)
                last_err = mt5.last_error()
                logger.info(f"[MT5 PLACE ORDER] Result: {result} | Last Error: {last_err}")

                if not result:
                    ticker = self.get_ticker(symbol)
                    min_stop_pts = max(getattr(sym_info, 'trade_stops_level', 0) or 0, 10)
                    min_stop_dist = round(min_stop_pts * float(sym_info.point), int(sym_info.digits))
                    err_msg = (
                        f"mt5.order_send() returned None. Last error: {last_err[0]} ({last_err[1]}). "
                        f"Details: Symbol={symbol}, Bid={ticker.bid if ticker else 'N/A'}, Ask={ticker.ask if ticker else 'N/A'}, "
                        f"Requested Price={price}, SL={sl_val}, TP={tp_val}, "
                        f"Minimum Stop Distance={min_stop_dist}, Volume={norm_lots}, Filling Mode={filling_type}"
                    )
                    logger.error(err_msg)
                    return False, None, err_msg

                if result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                    ticket = result.order or result.deal
                    
                    # Perform position reconciliation
                    import time
                    position_confirmed = None
                    for _ in range(5):  # retry up to 5 times
                        positions = mt5.positions_get(ticket=ticket)
                        if not positions:
                            all_pos = mt5.positions_get(symbol=symbol)
                            if all_pos:
                                position_confirmed = next((p for p in all_pos if p.ticket == ticket or p.identifier == ticket), None)
                        else:
                            position_confirmed = positions[0]
                        if position_confirmed:
                            break
                        time.sleep(0.05)
                        
                    if position_confirmed:
                        confirmed_price = float(position_confirmed.price_open)
                        confirmed_lots = float(position_confirmed.volume)
                        confirmed_sl = float(position_confirmed.sl)
                        confirmed_tp = float(position_confirmed.tp)
                        logger.info(
                            f"[RECONCILIATION SUCCESS] Position #{ticket} found. "
                            f"Confirmed Price={confirmed_price}, Requested Lots={norm_lots}, Confirmed Lots={confirmed_lots}, SL={confirmed_sl}, TP={confirmed_tp}"
                        )
                        # Post-Execution Volume Mismatch Check
                        if abs(confirmed_lots - norm_lots) > 0.001:
                            err_msg = (
                                f"CRITICAL EXECUTION MISMATCH: Requested {norm_lots:.2f} lots, but MT5 executed position #{ticket} with {confirmed_lots:.2f} lots! "
                                f"Auto-trading has been placed into EMERGENCY HALT."
                            )
                            logger.critical(f"[MT5 RECONCILIATION FAILURE] {err_msg}")
                            from app.services.trade_manager import trade_manager
                            trade_manager.emergency_stop()
                            return False, None, err_msg
                    else:
                        logger.warning(
                            f"[RECONCILIATION DISCREPANCY] Position #{ticket} was not found in open positions list! Using fallback request values."
                        )
                        confirmed_price = result.price or price
                        confirmed_lots = norm_lots
                        confirmed_sl = sl_val
                        confirmed_tp = tp_val

                    trade = TradeRecord(
                        trade_id=str(ticket),
                        symbol=symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        entry_price=confirmed_price,
                        sl_price=confirmed_sl,
                        tp_price=confirmed_tp,
                        lots=confirmed_lots,
                        state=TradeState.OPEN,
                        open_time=datetime.now(),
                        ai_confidence_at_entry=ai_confidence,
                        entry_reasoning=reasoning or []
                    )
                    return True, trade, f"Order #{ticket} placed successfully on MT5 @ {confirmed_price}"
                else:
                    err_desc = MT5_RETCODE_DESCRIPTIONS.get(result.retcode, result.comment or "Order rejected by broker")
                    ticker = self.get_ticker(symbol)
                    min_stop_pts = max(getattr(sym_info, 'trade_stops_level', 0) or 0, 10)
                    min_stop_dist = round(min_stop_pts * float(sym_info.point), int(sym_info.digits))
                    err_msg = (
                        f"MT5 Order Rejected. Retcode: {result.retcode} ({err_desc}). "
                        f"Details: Symbol={symbol}, Bid={ticker.bid if ticker else 'N/A'}, Ask={ticker.ask if ticker else 'N/A'}, "
                        f"Requested Price={price}, SL={sl_val}, TP={tp_val}, "
                        f"Minimum Stop Distance={min_stop_dist}, Volume={norm_lots}, Filling Mode={filling_type}"
                    )
                    logger.error(err_msg)
                    return False, None, err_msg
            except Exception as e:
                logger.error(f"Live MT5 place_order exception: {e}", exc_info=True)
                return False, None, f"Order execution error: {str(e)}"

        return False, None, "MetaTrader 5 terminal offline or not connected"

    def place_pending_order(
        self, symbol: str, side: TradeSide, order_type: OrderType, target_price: float, lots: float, sl: float, tp: float
    ) -> Tuple[bool, Optional[PendingOrderRecord], str]:
        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                valid, price, norm_lots, sl_val, tp_val, val_msg = self.validate_and_normalize_order_request(
                    symbol, side, order_type, target_price, lots, sl, tp
                )
                if not valid:
                    return False, None, val_msg

                sym_info = mt5.symbol_info(symbol)
                type_map = {
                    OrderType.BUY_LIMIT: mt5.ORDER_TYPE_BUY_LIMIT,
                    OrderType.SELL_LIMIT: mt5.ORDER_TYPE_SELL_LIMIT,
                    OrderType.BUY_STOP: mt5.ORDER_TYPE_BUY_STOP,
                    OrderType.SELL_STOP: mt5.ORDER_TYPE_SELL_STOP,
                }
                mt5_type = type_map.get(order_type, mt5.ORDER_TYPE_BUY_LIMIT)

                filling_type = mt5.ORDER_FILLING_IOC
                if sym_info.filling_mode & 2:
                    filling_type = mt5.ORDER_FILLING_IOC
                elif sym_info.filling_mode & 1:
                    filling_type = mt5.ORDER_FILLING_FOK
                else:
                    filling_type = mt5.ORDER_FILLING_RETURN

                request = {
                    "action": mt5.TRADE_ACTION_PENDING,
                    "symbol": symbol,
                    "volume": float(norm_lots),
                    "type": mt5_type,
                    "price": price,
                    "sl": sl_val,
                    "tp": tp_val,
                    "deviation": 20,
                    "magic": 998877,
                    "comment": "ApexTrader AI Pending Order",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling_type,
                }

                logger.info(f"[MT5 PLACE PENDING] Sending request: {request}")
                result = mt5.order_send(request)
                last_err = mt5.last_error()
                logger.info(f"[MT5 PLACE PENDING] Result: {result} | Last Error: {last_err}")

                if not result:
                    ticker = self.get_ticker(symbol)
                    min_stop_pts = max(getattr(sym_info, 'trade_stops_level', 0) or 0, 10)
                    min_stop_dist = round(min_stop_pts * float(sym_info.point), int(sym_info.digits))
                    err_msg = (
                        f"mt5.order_send() returned None. Last error: {last_err[0]} ({last_err[1]}). "
                        f"Details: Symbol={symbol}, Bid={ticker.bid if ticker else 'N/A'}, Ask={ticker.ask if ticker else 'N/A'}, "
                        f"Requested Price={price}, SL={sl_val}, TP={tp_val}, "
                        f"Minimum Stop Distance={min_stop_dist}, Volume={norm_lots}, Filling Mode={filling_type}"
                    )
                    logger.error(err_msg)
                    return False, None, err_msg

                if result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                    record = PendingOrderRecord(
                        order_id=str(result.order),
                        symbol=symbol,
                        side=side,
                        order_type=order_type,
                        target_price=price,
                        sl_price=sl_val,
                        tp_price=tp_val,
                        lots=norm_lots,
                        open_time=datetime.now()
                    )
                    return True, record, f"Pending order #{result.order} placed successfully on MT5 @ {price}"
                else:
                    err_desc = MT5_RETCODE_DESCRIPTIONS.get(result.retcode, result.comment or "Order rejected by broker")
                    ticker = self.get_ticker(symbol)
                    min_stop_pts = max(getattr(sym_info, 'trade_stops_level', 0) or 0, 10)
                    min_stop_dist = round(min_stop_pts * float(sym_info.point), int(sym_info.digits))
                    err_msg = (
                        f"MT5 Order Rejected. Retcode: {result.retcode} ({err_desc}). "
                        f"Details: Symbol={symbol}, Bid={ticker.bid if ticker else 'N/A'}, Ask={ticker.ask if ticker else 'N/A'}, "
                        f"Requested Price={price}, SL={sl_val}, TP={tp_val}, "
                        f"Minimum Stop Distance={min_stop_dist}, Volume={norm_lots}, Filling Mode={filling_type}"
                    )
                    logger.error(err_msg)
                    return False, None, err_msg
            except Exception as e:
                logger.error(f"Live MT5 place_pending_order exception: {e}", exc_info=True)
                return False, None, f"Pending order exception: {str(e)}"

        return False, None, "MetaTrader 5 terminal offline or not connected"

    def cancel_pending_order(self, order_id: str) -> Tuple[bool, str]:
        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                ticket = int(order_id)
                orders = mt5.orders_get(ticket=ticket)
                if not orders:
                    return False, f"Pending order #{order_id} not found in MT5"

                request = {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": ticket,
                    "magic": 998877,
                    "comment": "Cancel Pending Order",
                }

                result = mt5.order_send(request)
                last_err = mt5.last_error()
                logger.info(f"[MT5 CANCEL PENDING] Ticket {ticket} | Result: {result} | Last Error: {last_err}")

                if result and result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                    return True, f"Pending order #{order_id} cancelled"
                else:
                    code = result.retcode if result else last_err[0]
                    comment = result.comment if result else last_err[1]
                    return False, f"Cancel Order Failed {_get_retcode_msg(code, comment)}"
            except Exception as e:
                logger.error(f"Live MT5 cancel_pending_order exception: {e}", exc_info=True)
                return False, f"Cancel order exception: {str(e)}"

        return False, "MetaTrader 5 terminal offline or not connected"

    def close_order(self, trade_id: str, exit_reason: str = "MANUAL_CLOSE") -> Tuple[bool, str]:
        if not MT5_AVAILABLE or not self.session_manager.use_live_mt5:
            # Paper mode close fallback
            from app.services.trade_manager import trade_manager
            if trade_id in trade_manager.active_trades:
                trade = trade_manager.active_trades[trade_id]
                ticker = self.get_ticker(trade.symbol)
                exit_price = ticker.bid if trade.side == TradeSide.BUY else ticker.ask
                pnl_usd = (exit_price - trade.entry_price) * trade.lots * 100.0 if trade.side == TradeSide.BUY else (trade.entry_price - exit_price) * trade.lots * 100.0
                
                from app.services.symbol_specs import get_symbol_specs
                specs = get_symbol_specs(trade.symbol)
                pip_size = specs["pip_size"]
                pips = (exit_price - trade.entry_price) / pip_size if trade.side == TradeSide.BUY else (trade.entry_price - exit_price) / pip_size
                
                from app.services.trade_learner import trade_learner
                trade_learner.update_trade_outcome(
                    trade_id=trade_id,
                    exit_price=exit_price,
                    pnl_usd=round(pnl_usd, 2),
                    pnl_pips=round(pips, 1),
                    exit_reason=exit_reason,
                    close_timestamp=datetime.now()
                )
                trade.state = TradeState.CLOSED
                trade_manager.active_trades.pop(trade_id, None)
                return True, f"[PAPER MODE] Mock trade #{trade_id} closed manually @ {exit_price:.2f}"
            return False, f"Paper position #{trade_id} not found."

        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                ticket = int(trade_id)
                positions = mt5.positions_get(ticket=ticket)
                pos = None
                if positions:
                    pos = positions[0]
                else:
                    # Fallback: get all positions and match ticket manually
                    all_pos = mt5.positions_get()
                    if all_pos:
                        matching = [p for p in all_pos if p.ticket == ticket or getattr(p, "identifier", None) == ticket]
                        if matching:
                            pos = matching[0]

                if not pos:
                    return False, f"Position #{trade_id} not found in MT5"
                symbol = pos.symbol
                sym_info = mt5.symbol_info(symbol)
                if not sym_info:
                    mt5.symbol_select(symbol, True)
                    sym_info = mt5.symbol_info(symbol)

                if not sym_info:
                    return False, f"Symbol '{symbol}' info unavailable"

                close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
                price = sym_info.bid if pos.type == 0 else sym_info.ask
                price = round(price, sym_info.digits)

                filling_type = mt5.ORDER_FILLING_IOC
                if sym_info.filling_mode & 2:
                    filling_type = mt5.ORDER_FILLING_IOC
                elif sym_info.filling_mode & 1:
                    filling_type = mt5.ORDER_FILLING_FOK
                else:
                    filling_type = mt5.ORDER_FILLING_RETURN

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": ticket,
                    "symbol": symbol,
                    "volume": pos.volume,
                    "type": close_type,
                    "price": price,
                    "deviation": 20,
                    "magic": 998877,
                    "comment": f"Close {exit_reason}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling_type,
                }

                logger.info(f"[MT5 CLOSE ORDER] Ticket {ticket} | Request: {request}")
                result = mt5.order_send(request)
                last_err = mt5.last_error()
                logger.info(f"[MT5 CLOSE ORDER] Ticket {ticket} | Result: {result} | Last Error: {last_err}")

                if result and result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                    self.session_manager.sync_history_to_sqlite()
                    return True, f"Position #{trade_id} closed successfully @ {result.price}"
                else:
                    code = result.retcode if result else last_err[0]
                    comment = result.comment if result else last_err[1]
                    return False, f"Close Position Failed {_get_retcode_msg(code, comment)}"
            except Exception as e:
                logger.error(f"Live MT5 close_order exception: {e}", exc_info=True)
                return False, f"Close order exception: {str(e)}"

        return False, "MetaTrader 5 terminal offline or not connected"

    def close_all_positions(self, reason: str = "EMERGENCY_STOP") -> int:
        closed_count = 0
        if not MT5_AVAILABLE or not self.session_manager.use_live_mt5:
            # Paper mode close all
            from app.services.trade_manager import trade_manager
            for trade_id in list(trade_manager.active_trades.keys()):
                success, _ = self.close_order(trade_id, exit_reason=reason)
                if success:
                    closed_count += 1
            return closed_count

        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                positions = mt5.positions_get()
                if positions:
                    for pos in positions:
                        success, _ = self.close_order(str(pos.ticket), exit_reason=reason)
                        if success:
                            closed_count += 1
                    self.session_manager.sync_history_to_sqlite()
            except Exception as e:
                logger.error(f"Error in close_all_positions: {e}", exc_info=True)
        return closed_count

    def update_sl_tp(self, trade_id: str, new_sl: float, new_tp: float) -> bool:
        if not MT5_AVAILABLE or not self.session_manager.use_live_mt5:
            # Paper mode update SL/TP
            from app.services.trade_manager import trade_manager
            if trade_id in trade_manager.active_trades:
                trade = trade_manager.active_trades[trade_id]
                trade.sl_price = new_sl
                trade.tp_price = new_tp
                logger.info(f"[PAPER MODE] Position #{trade_id} SL/TP updated to SL={new_sl}, TP={new_tp}")
                return True
            return False

        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                ticket = int(trade_id)
                positions = mt5.positions_get(ticket=ticket)
                pos = None
                if positions:
                    pos = positions[0]
                else:
                    # Fallback: get all open positions and match ticket manually
                    all_pos = mt5.positions_get()
                    if all_pos:
                        matching = [p for p in all_pos if p.ticket == ticket or getattr(p, "identifier", None) == ticket]
                        if matching:
                            pos = matching[0]

                if not pos:
                    return False
                sym_info = mt5.symbol_info(pos.symbol)
                digits = sym_info.digits if sym_info else 5

                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": ticket,
                    "symbol": pos.symbol,
                    "sl": round(new_sl, digits) if new_sl > 0 else 0.0,
                    "tp": round(new_tp, digits) if new_tp > 0 else 0.0,
                    "magic": 998877,
                }

                result = mt5.order_send(request)
                last_err = mt5.last_error()
                logger.info(f"[MT5 UPDATE SLTP] Position {ticket} | Request: {request} | Result: {result} | Last Error: {last_err}")
                return bool(result and result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED))
            except Exception as e:
                logger.error(f"Live MT5 update_sl_tp exception: {e}", exc_info=True)
        return False

    def get_positions(self) -> List[TradeRecord]:
        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                raw_pos = mt5.positions_get()
                if raw_pos:
                    from app.services.symbol_specs import calculate_potential_pnl_usd, get_symbol_specs
                    from app.services.trade_manager import trade_manager
                    positions = []
                    for p in raw_pos:
                        side_val = TradeSide.BUY if p.type == 0 else TradeSide.SELL
                        cached_t = trade_manager.active_trades.get(str(p.ticket))
                        setup_str = cached_t.setup_name if (cached_t and cached_t.setup_name) else "Unknown"
                        tf_str = cached_t.timeframe if (cached_t and cached_t.timeframe) else "M15"

                        pot_loss = calculate_potential_pnl_usd(p.symbol, side_val, p.price_open, p.sl, p.volume) if p.sl > 0 else 0.0
                        pot_profit = calculate_potential_pnl_usd(p.symbol, side_val, p.price_open, p.tp, p.volume) if p.tp > 0 else 0.0
                        planned_loss = cached_t.planned_loss_usd if (cached_t and cached_t.planned_loss_usd != 0.0) else pot_loss
                        planned_profit = cached_t.planned_profit_usd if (cached_t and cached_t.planned_profit_usd != 0.0) else pot_profit

                        specs = get_symbol_specs(p.symbol)
                        pip_size = float(specs.get("pip_size", 0.10) or 0.10)

                        tick = mt5.symbol_info_tick(p.symbol)
                        if tick:
                            current_price = float(tick.bid if p.type == 0 else tick.ask)
                            pips = (current_price - p.price_open) / pip_size if p.type == 0 else (p.price_open - current_price) / pip_size
                            pnl_pips = round(pips, 1)
                        else:
                            logger.debug(f"[MT5 ADAPTER] Tick info unavailable for {p.symbol}, falling back to pnl_pips=0.0")
                            pnl_pips = 0.0

                        positions.append(TradeRecord(
                            trade_id=str(p.ticket),
                            symbol=p.symbol,
                            side=side_val,
                            order_type=OrderType.MARKET,
                            entry_price=p.price_open,
                            sl_price=p.sl,
                            tp_price=p.tp,
                            lots=p.volume,
                            state=TradeState.OPEN,
                            open_time=datetime.fromtimestamp(p.time),
                            pnl_usd=round(p.profit, 2),
                            pnl_pips=pnl_pips,
                            ai_confidence_at_entry=0.85,
                            setup_name=setup_str,
                            timeframe=tf_str,
                            planned_loss_usd=planned_loss,
                            planned_profit_usd=planned_profit,
                            potential_loss_usd=pot_loss,
                            potential_profit_usd=pot_profit
                        ))
                    return positions
            except Exception as e:
                logger.error(f"Error fetching live MT5 positions: {e}")

        return []

    def get_pending_orders(self) -> List[PendingOrderRecord]:
        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                raw_orders = mt5.orders_get()
                if raw_orders:
                    orders = []
                    for o in raw_orders:
                        orders.append(PendingOrderRecord(
                            order_id=str(o.ticket),
                            symbol=o.symbol,
                            side=TradeSide.BUY if o.type in (2, 4) else TradeSide.SELL,
                            order_type=OrderType.BUY_LIMIT if o.type == 2 else (OrderType.SELL_LIMIT if o.type == 3 else (OrderType.BUY_STOP if o.type == 4 else OrderType.SELL_STOP)),
                            target_price=o.price_open,
                            sl_price=o.sl,
                            tp_price=o.tp,
                            lots=o.volume_current,
                            open_time=datetime.fromtimestamp(o.time_setup)
                        ))
                    return orders
            except Exception as e:
                logger.error(f"Error fetching live MT5 pending orders: {e}")
        return []

    def get_history(self, today_only: bool = True) -> List[TradeRecord]:
        records = []
        now = datetime.now()
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if self.session_manager.use_live_mt5 and MT5_AVAILABLE:
            try:
                from_date = start_of_today if today_only else (now - timedelta(days=30))
                to_date = now + timedelta(days=1)
                history_deals = mt5.history_deals_get(from_date, to_date)
                if history_deals:
                    from app.services.trade_learner import trade_learner
                    from app.services.symbol_specs import calculate_potential_pnl_usd
                    for d in history_deals:
                        if getattr(d, 'entry', 0) == 1 and d.symbol: # Deal out (closed position)
                            close_time = datetime.fromtimestamp(d.time)
                            if today_only and close_time.date() != now.date():
                                continue

                            original_side = TradeSide.BUY if d.type == 1 else TradeSide.SELL
                            
                            entry_price = d.price
                            position_deals = [x for x in history_deals if getattr(x, 'position_id', None) == d.position_id and getattr(x, 'entry', -1) == 0]
                            open_time = close_time
                            if position_deals:
                                entry_price = float(position_deals[0].price)
                                open_time = datetime.fromtimestamp(position_deals[0].time)

                            from app.services.symbol_specs import get_symbol_specs
                            specs = get_symbol_specs(d.symbol)
                            pip_size = specs["pip_size"]
                            pnl = round(d.profit + getattr(d, 'swap', 0.0) + getattr(d, 'commission', 0.0), 2)
                            pips = round((d.price - entry_price) / pip_size if original_side == TradeSide.BUY else (entry_price - d.price) / pip_size, 1)

                            # Lookup setup_name and timeframe from DB if logged
                            setup_str = "MT5 Closed Trade"
                            tf_str = "M15"
                            try:
                                with trade_learner._get_connection() as conn:
                                    cur = conn.cursor()
                                    cur.execute("SELECT setup_name, timeframe FROM trade_history_memory WHERE trade_id = ?", (str(d.position_id or d.ticket),))
                                    row = cur.fetchone()
                                    if row:
                                        setup_str = row["setup_name"] or setup_str
                                        tf_str = row["timeframe"] or tf_str
                            except Exception:
                                pass

                            records.append(TradeRecord(
                                trade_id=str(d.position_id or d.ticket),
                                symbol=d.symbol,
                                side=original_side,
                                order_type=OrderType.MARKET,
                                entry_price=entry_price,
                                close_price=d.price,
                                sl_price=0.0,
                                tp_price=0.0,
                                lots=d.volume,
                                state=TradeState.CLOSED,
                                open_time=open_time,
                                close_time=close_time,
                                pnl_usd=pnl,
                                pnl_pips=pips,
                                exit_reason=d.comment or "CLOSED",
                                setup_name=setup_str,
                                timeframe=tf_str
                            ))
                    if records:
                        records.sort(key=lambda r: r.close_time, reverse=True)
                        return records
            except Exception as e:
                logger.error(f"Error fetching live MT5 history: {e}")

        # Fallback to SQLite Trade Memory (paper trades + synced MT5 trade memory)
        try:
            from app.services.trade_learner import trade_learner
            from app.services.symbol_specs import calculate_potential_pnl_usd
            closed_db = trade_learner.get_recent_closed_trades(limit=None, today_only=today_only)
            for r in closed_db:
                try:
                    op_time = datetime.fromisoformat(r["open_timestamp"]) if r.get("open_timestamp") else datetime.now()
                    cl_time = datetime.fromisoformat(r["close_timestamp"]) if r.get("close_timestamp") else datetime.now()
                    
                    if today_only and cl_time.date() != now.date():
                        continue

                    side_val = TradeSide.BUY if str(r.get("side", "")).upper() == "BUY" else TradeSide.SELL
                    pnl = float(r.get("pnl_usd", 0.0) or 0.0)
                    entry_p = float(r.get("entry_price", 0.0) or 0.0)
                    exit_p = float(r.get("exit_price", entry_p) or entry_p)
                    sym_name = str(r.get("symbol", "XAUUSD"))
                    setup_str = str(r.get("setup_name", "Unknown") or "Unknown")
                    tf_str = str(r.get("timeframe", "M15") or "M15")

                    from app.services.symbol_specs import get_symbol_specs
                    specs = get_symbol_specs(sym_name)
                    pip_size = specs["pip_size"]
                    
                    if entry_p > 0 and exit_p > 0:
                        calc_pips = (exit_p - entry_p) / pip_size if side_val == TradeSide.BUY else (entry_p - exit_p) / pip_size
                        pips = round(calc_pips, 1)
                    else:
                        pips = float(r.get("pnl_pips", 0.0) or 0.0)

                    sl_p = float(r.get("sl_price", 0.0) or 0.0)
                    tp_p = float(r.get("tp_price", 0.0) or 0.0)
                    lots_p = float(r.get("lots", 0.10) or 0.10)

                    planned_loss = calculate_potential_pnl_usd(sym_name, side_val, entry_p, sl_p, lots_p) if sl_p > 0 else 0.0
                    planned_profit = calculate_potential_pnl_usd(sym_name, side_val, entry_p, tp_p, lots_p) if tp_p > 0 else 0.0

                    records.append(TradeRecord(
                        trade_id=str(r["trade_id"]),
                        symbol=sym_name,
                        side=side_val,
                        order_type=OrderType.MARKET,
                        entry_price=entry_p,
                        close_price=exit_p,
                        sl_price=sl_p,
                        tp_price=tp_p,
                        lots=lots_p,
                        state=TradeState.CLOSED,
                        open_time=op_time,
                        close_time=cl_time,
                        pnl_usd=pnl,
                        pnl_pips=pips,
                        exit_reason=str(r.get("exit_reason", "CLOSED")),
                        setup_name=setup_str,
                        timeframe=tf_str,
                        planned_loss_usd=planned_loss,
                        planned_profit_usd=planned_profit
                    ))
                except Exception as ex:
                    logger.error(f"Error parsing SQLite trade record: {ex}")
        except Exception as e:
            logger.error(f"Error fetching SQLite history fallback: {e}")

        records.sort(key=lambda r: r.close_time, reverse=True)
        return records

    # ── TICK & CANDLE HELPER ───────────────────────────────────────

    def _generate_tick(self, symbol: str) -> TickerData:
        if not hasattr(self, "_prices"):
            self._prices = {"EURUSD": 1.0850, "GBPUSD": 1.2720, "USDJPY": 154.50, "BTCUSD": 64500.0, "XAUUSD": 2380.0}

        curr = self._prices.get(symbol, 1.0850)
        vol = 0.0002 if "USD" in symbol and "BTC" not in symbol else (40.0 if "BTC" in symbol else 0.4)
        step = (random.random() - 0.495) * vol
        new_p = max(0.0001, curr + step)
        self._prices[symbol] = new_p

        spread = 0.00015 if "USD" in symbol and "BTC" not in symbol else (0.5 if "XAU" in symbol else 5.0)
        from app.services.symbol_specs import get_symbol_specs
        specs = get_symbol_specs(symbol)
        digits = specs["digits"]
        point_size = specs["point_size"]
        bid = round(new_p, digits)
        ask = round(new_p + spread, digits)
        raw_spread = round(ask - bid, digits)
        pip_size = specs["pip_size"]
        spread_pips = round(raw_spread / pip_size, 1)
        spread_pts = int(round(raw_spread / point_size))

        return TickerData(
            symbol=symbol,
            bid=bid,
            ask=ask,
            spread_pips=spread_pips,
            spread_points=spread_pts,
            raw_spread=raw_spread,
            point_size=point_size,
            digits=digits,
            timestamp=datetime.now()
        )

    def _generate_candles(
        self,
        symbol: str,
        timeframe: str,
        count: int,
        before_datetime: Optional[datetime] = None,
        from_datetime: Optional[datetime] = None,
        to_datetime: Optional[datetime] = None
    ) -> List[Candle]:
        candles = []
        base = getattr(self, "_prices", {}).get(symbol, 4315.00 if "XAU" in symbol else 1.0850)
        tf_mins = TF_MINUTES_MAP.get(timeframe.upper(), 15)
        from app.services.symbol_specs import get_symbol_specs
        digits = get_symbol_specs(symbol)["digits"]

        if from_datetime is not None and to_datetime is not None:
            end_time = to_datetime
            start_time = from_datetime
            total_sec = max(3600.0, (end_time - start_time).total_seconds())
            calc_count = max(40, min(25000, int(total_sec / (tf_mins * 60))))
            num_candles = calc_count
        else:
            end_time = before_datetime or datetime.now()
            num_candles = count

        import random
        local_random = random.Random()
        seed_str = f"{symbol}_{timeframe}_{num_candles}_{end_time.timestamp() if hasattr(end_time, 'timestamp') else end_time}"
        import hashlib
        seed_hash = int(hashlib.md5(seed_str.encode('utf-8')).hexdigest(), 16)
        local_random.seed(seed_hash)

        curr_p = base - (num_candles * (0.10 if "XAU" in symbol else 0.0001))
        for i in range(num_candles):
            t_stamp = end_time - timedelta(minutes=tf_mins * (num_candles - i))
            vol = local_random.uniform(0.50, 2.50) if "XAU" in symbol else local_random.uniform(0.0004, 0.0012)
            open_p = curr_p
            close_p = open_p + local_random.uniform(-vol, vol)
            high_p = max(open_p, close_p) + local_random.uniform(0, vol * 0.4)
            low_p = min(open_p, close_p) - local_random.uniform(0, vol * 0.4)
            
            candles.append(Candle(
                timestamp=t_stamp,
                open=round(open_p, digits),
                high=round(high_p, digits),
                low=round(low_p, digits),
                close=round(close_p, digits),
                volume=round(local_random.uniform(200, 3000), 2),
                timeframe=timeframe,
                is_closed=True
            ))
            curr_p = close_p

        return candles


# Global Singleton Instance
mt5_adapter = MT5Adapter()
