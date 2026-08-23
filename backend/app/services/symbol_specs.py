"""
Shared Symbol Specifications Lookup Service
"""
from typing import Dict, Any, Optional

def get_symbol_specs(symbol: str) -> Dict[str, Any]:
    """
    Returns correct pip_size, digits, point_size, and pip_value_per_lot for a given symbol.
    Delegates to GoldSymbolSpecService if the symbol represents Gold.
    """
    if not symbol:
        return {
            "pip_size": 0.0001,
            "digits": 5,
            "point_size": 0.00001,
            "pip_value_per_lot": 10.0
        }

    symbol = symbol.upper()
    from app.services.gold_specs import gold_specs
    
    # Check if the symbol represents Gold
    if "XAU" in symbol or "GOLD" in symbol or symbol == gold_specs.discovered_symbol.upper():
        specs = gold_specs.get_specs()
        # Ensure it has pip_size and pip_value_per_lot
        return specs

    # Standard Forex Pairs (Default fallback for other symbols if queried, though disabled in worker)
    # 1. Try Live MT5 lookup first if connected
    try:
        from app.services.session_manager import session_manager, MT5_AVAILABLE
        if MT5_AVAILABLE and session_manager.use_live_mt5:
            import MetaTrader5 as mt5
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            if info:
                point = float(info.point)
                digits = int(info.digits)
                tick_size = float(info.trade_tick_size or point)
                tick_val = float(info.trade_tick_value or 1.0)
                
                if "JPY" in symbol:
                    pip_size = 0.01
                elif "XAG" in symbol or "SILVER" in symbol:
                    pip_size = 0.01
                elif "BTC" in symbol or "ETH" in symbol:
                    pip_size = 1.0
                elif any(idx in symbol for idx in ["US30", "US500", "USTEC", "US2000", "UK100", "GER40", "JP225", "GER30", "DE30", "NAS100", "SPX"]):
                    pip_size = 1.0
                else:
                    pip_size = 10.0 * point if digits in (3, 5) else point

                pip_value_per_lot = (pip_size / (tick_size or 1e-9)) * tick_val
                
                return {
                    "pip_size": pip_size,
                    "digits": digits,
                    "point_size": point,
                    "pip_value_per_lot": round(pip_value_per_lot, 4)
                }
    except Exception:
        pass

    # Static Fallback Table for non-Gold symbols
    INDEX_SYMBOLS = {"US30", "US500", "USTEC", "US2000", "UK100", "GER40", "JP225", "GER30", "DE30", "NAS100", "SPX"}
    if symbol in INDEX_SYMBOLS or any(symbol.startswith(idx) for idx in INDEX_SYMBOLS):
        return {
            "pip_size": 1.0,
            "digits": 2,
            "point_size": 0.1,
            "pip_value_per_lot": 1.0
        }

    if "BTC" in symbol or "ETH" in symbol:
        return {
            "pip_size": 1.0,
            "digits": 2,
            "point_size": 0.1,
            "pip_value_per_lot": 1.0
        }

    if "XAG" in symbol or "SILVER" in symbol:
        return {
            "pip_size": 0.01,
            "digits": 3,
            "point_size": 0.001,
            "pip_value_per_lot": 50.0
        }

    if "JPY" in symbol:
        return {
            "pip_size": 0.01,
            "digits": 3,
            "point_size": 0.001,
            "pip_value_per_lot": 6.5
        }

    return {
        "pip_size": 0.0001,
        "digits": 5,
        "point_size": 0.00001,
        "pip_value_per_lot": 10.0
    }


def calculate_potential_pnl_usd(
    symbol: str,
    side: Any,
    entry_price: float,
    target_price: float,
    lots: float
) -> float:
    """
    Computes expected PnL in USD from entry_price to target_price for a given symbol, side, and lot size.
    Calculates exact price_distance / pip_size, and multiplies by pip_value_per_lot * lots.
    """
    if entry_price <= 0 or target_price <= 0 or lots <= 0:
        return 0.0

    specs = get_symbol_specs(symbol)
    pip_size = float(specs.get("pip_size", 0.10) or 0.10)
    pip_value = float(specs.get("pip_value_per_lot", 10.0) or 10.0)

    side_str = side.value if hasattr(side, "value") else str(side).upper()

    price_distance = abs(target_price - entry_price)
    pips = price_distance / pip_size

    if side_str == "BUY":
        pnl = pips * pip_value * lots if target_price >= entry_price else -pips * pip_value * lots
    else:
        pnl = pips * pip_value * lots if entry_price >= target_price else -pips * pip_value * lots

    return round(pnl, 2)



