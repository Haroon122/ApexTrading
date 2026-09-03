"""
FastAPI Server Entrypoint & Real-Time WebSocket Streaming Engine
Production-Grade MT5 AI Trading Terminal Server
"""

from typing import List, Optional
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.adapters.mt5_adapter import mt5_adapter
from app.services.session_manager import session_manager
from app.api.v1.endpoints import router as api_v1_router
from app.services.decision_engine import decision_engine
from app.services.trade_manager import trade_manager
from app.services.risk_manager import risk_manager
from app.domain.models import BotStatus, TradeSide

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
logger = logging.getLogger("ApexTrader.Main")


# Connection Manager for WebSockets
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket Client connected. Active clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket Client disconnected.")

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to WS client: {e}")


manager = ConnectionManager()


async def send_heartbeat_broadcast():
    """Triggered by MT5SessionManager heartbeat whenever mobile/desktop MT5 state diffs occur."""
    account = mt5_adapter.get_account_info()
    positions = mt5_adapter.get_positions()
    pending = mt5_adapter.get_pending_orders()
    history = mt5_adapter.get_history()[:30]
    
    payload = {
        "event": "HEARTBEAT_DIFF_UPDATE",
        "timestamp": datetime.now().isoformat(),
        "bot_status": trade_manager.bot_status.value,
        "account": account.model_dump(mode="json"),
        "positions": [p.model_dump(mode="json") for p in positions],
        "pending_orders": [p.model_dump(mode="json") for p in pending],
        "history": [h.model_dump(mode="json") for h in history]
    }
    await manager.broadcast(payload)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    logger.info(f"Starting {settings.PROJECT_NAME} (Environment: {settings.ENVIRONMENT})")
    mt5_adapter.initialize()
    
    # Launch Session Manager Heartbeat background loop
    heartbeat_task = asyncio.create_task(session_manager.start_heartbeat_loop(send_heartbeat_broadcast))
    yield
    
    # Clean up worker task if running
    from app.services.trading_worker import trading_worker
    if trading_worker.is_running():
        await trading_worker.stop()
        
    heartbeat_task.cancel()
    logger.info(f"Shutting down {settings.PROJECT_NAME}")


# Initialize FastAPI App
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Production-Grade MT5 Trading Terminal REST & WebSocket Server",
    lifespan=lifespan
)

# Configure Restricted CORS for Dashboard (supports file:// and local origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles
import os

# Mount Static Frontend Directory
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend"))
if os.path.exists(frontend_dir):
    app.mount("/frontend", StaticFiles(directory=frontend_dir, html=True), name="frontend")

# Include API v1 Router
app.include_router(api_v1_router, prefix=settings.API_V1_STR)


@app.get("/")
def read_root():
    return {
        "system": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
        "api_v1": settings.API_V1_STR
    }


@app.websocket("/ws/market")
async def websocket_market_endpoint(
    websocket: WebSocket,
    symbol: str = "XAUUSD",
    timeframe: str = "M1",
    api_key: Optional[str] = Query(None)
):
    # Verify API key
    if not api_key or api_key != settings.API_KEY:
        await websocket.accept()
        await websocket.close(code=1008, reason="Invalid or missing API Key")
        return

    await manager.connect(websocket)
    try:
        from app.services.trading_worker import trading_worker
        
        ws_fallback_seq = 0
        while True:
            ws_fallback_seq += 1
            # Check if background worker is active for this symbol
            if trading_worker.is_running() and (symbol.upper() == trading_worker.symbol.upper() or symbol.upper() in ("XAUUSD", "GOLD", "XAUUSD.A", "XAUUSDM")):
                payload = dict(trading_worker.last_payload) if trading_worker.last_payload else {}
                # If client requested a specific timeframe candles for chart view, update candles field
                if timeframe.upper() != trading_worker.timeframe.upper():
                    tf_candles = mt5_adapter.fetch_candles(trading_worker.symbol, timeframe, 100)
                    if tf_candles:
                        payload["candles"] = [c.model_dump(mode="json") for c in tf_candles[-50:]]
            else:
                # Fallback read-only preview evaluation with HTF context
                ticker = mt5_adapter.get_ticker(symbol)
                candles = mt5_adapter.fetch_candles(symbol, timeframe, 100)
                
                m1_candles = mt5_adapter.fetch_candles(symbol, "M1", 100)
                m5_candles = mt5_adapter.fetch_candles(symbol, "M5", 100)
                m15_candles = mt5_adapter.fetch_candles(symbol, "M15", 100)
                h1_candles = mt5_adapter.fetch_candles(symbol, "H1", 100)
                h4_candles = mt5_adapter.fetch_candles(symbol, "H4", 100)
                htf_candles = {
                    "M1": m1_candles,
                    "M5": m5_candles,
                    "M15": m15_candles,
                    "H1": h1_candles,
                    "H4": h4_candles
                }
                
                report = decision_engine.evaluate_trade_opportunity(symbol, candles, ticker, timeframe, htf_candles=htf_candles)
                
                account = mt5_adapter.get_account_info()
                positions = mt5_adapter.get_positions()
                pending_orders = mt5_adapter.get_pending_orders()
                history = mt5_adapter.get_history()[:30]
                
                fallback_reason = ""
                if report:
                    if (report.decision == TradeSide.HOLD or getattr(report.decision, "value", str(report.decision)) == "HOLD") and report.rejection_reasons:
                        fallback_reason = f"Trade Held: Strategy decision is HOLD. Rejection reasons: {', '.join(report.rejection_reasons[:2])}"
                    elif report.decision != TradeSide.HOLD and getattr(report.decision, "value", str(report.decision)) != "HOLD":
                        risk_check = risk_manager.evaluate_risk(account, report, symbol)
                        if not risk_check.passed:
                            first_risk = risk_check.reasons[0] if getattr(risk_check, "reasons", None) else "Risk limits exceeded"
                            fallback_reason = f"Trade Blocked by Risk Guard: {first_risk}"
                        else:
                            fallback_reason = f"Executing Trade: {getattr(report.decision, 'value', report.decision)} Signal Ready."

                payload = {
                    "timestamp": datetime.now().isoformat(),
                    "sequence": ws_fallback_seq,
                    "bot_status": trade_manager.bot_status.value,
                    "engine_state": trading_worker.state.value if hasattr(trading_worker, 'state') else "STOPPED",
                    "engine_reason": trading_worker.status_reason if hasattr(trading_worker, 'status_reason') else "",
                    "auto_trade_live_reason": trading_worker.last_payload.get("auto_trade_live_reason") if (trading_worker.is_running() and trading_worker.last_payload) else fallback_reason,
                    "ticker": ticker.model_dump(mode="json") if ticker else None,
                    "account": account.model_dump(mode="json") if account else None,
                    "candles": [c.model_dump(mode="json") for c in candles[-50:]] if candles else [],
                    "htf_candles": {tf: [c.model_dump(mode="json") for c in c_list[-50:]] for tf, c_list in htf_candles.items() if c_list},
                    "decision_report": report.model_dump(mode="json") if report else None,
                    "positions": [p.model_dump(mode="json") for p in positions],
                    "pending_orders": [p.model_dump(mode="json") for p in pending_orders] if pending_orders else [],
                    "history": [h.model_dump(mode="json") for h in history] if history else [],
                    "risk_settings": trade_manager.risk_settings.model_dump(mode="json")
                }

            if payload:
                await websocket.send_json(payload)
            await asyncio.sleep(1.0) # 1-second refresh cycle
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket exception: {e}")
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
