import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("ApexTrader.GoldSpecs")

class GoldSymbolSpecService:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GoldSymbolSpecService, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance
        
    def __init__(self):
        if self._initialized:
            return
        self.discovered_symbol: str = "XAUUSD" # Default fallback
        self._initialized = True
        
    def set_discovered_symbol(self, symbol: str):
        self.discovered_symbol = symbol
        logger.info(f"[GOLD SPECS] Set active discovered Gold symbol to: {symbol}")
        
    def get_specs(self) -> Dict[str, Any]:
        """
        Dynamically queries broker specifications for the discovered Gold symbol.
        Falls back to realistic Gold defaults when offline.
        """
        try:
            from app.services.session_manager import session_manager, MT5_AVAILABLE
            import MetaTrader5 as mt5
            use_live = session_manager.use_live_mt5
        except ImportError:
            MT5_AVAILABLE = False
            use_live = False
            mt5 = None
        
        symbol = self.discovered_symbol
        
        # Default high-fidelity specifications for Gold/XAUUSD
        fallback_specs = {
            "symbol": symbol,
            "digits": 2,
            "point_size": 0.01,
            "tick_size": 0.01,
            "tick_value": 1.0,
            "contract_size": 100.0,
            "pip_size": 0.10,
            "pip_value_per_lot": 10.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "stop_level": 30, # in points
            "freeze_level": 0,
            "filling_mode": 2, # IOC by default (ORDER_FILLING_IOC)
            "trade_mode": 4 # Full access
        }
        
        if MT5_AVAILABLE and use_live:
            try:
                # Ensure selected in market watch
                mt5.symbol_select(symbol, True)
                info = mt5.symbol_info(symbol)
                if info:
                    point = float(info.point)
                    digits = int(info.digits)
                    tick_size = float(info.trade_tick_size or point)
                    tick_val = float(info.trade_tick_value or 1.0)
                    contract_size = float(info.trade_contract_size or 100.0)
                    
                    pip_size = 0.10 # Gold convention is 1 pip = 0.10 price change
                    pip_value_per_lot = round(contract_size * pip_size, 4) if contract_size > 0 else 10.0
                    
                    # Resolve filling mode
                    filling_mode = int(getattr(info, 'filling_mode', 2))
                    if filling_mode == 0:
                        filling_mode = 2 # IOC default fallback
                        
                    return {
                        "symbol": symbol,
                        "digits": digits,
                        "point_size": point,
                        "tick_size": tick_size,
                        "tick_value": tick_val,
                        "contract_size": contract_size,
                        "pip_size": pip_size,
                        "pip_value_per_lot": round(pip_value_per_lot, 4),
                        "volume_min": float(info.volume_min or 0.01),
                        "volume_max": float(info.volume_max or 100.0),
                        "volume_step": float(info.volume_step or 0.01),
                        "stop_level": int(getattr(info, 'trade_stops_level', 0) or 0),
                        "freeze_level": int(getattr(info, 'trade_freeze_level', 0) or 0),
                        "filling_mode": filling_mode,
                        "trade_mode": int(getattr(info, 'trade_mode', 4))
                    }
            except Exception as e:
                logger.error(f"[GOLD SPECS] Error fetching live MT5 specs for {symbol}: {e}")
                
        return fallback_specs

gold_specs = GoldSymbolSpecService()
