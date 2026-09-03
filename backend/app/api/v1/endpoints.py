"""
FastAPI REST API Endpoints Module (v1)
Provides interfaces for Verified MT5 Account Connection, Account Previews, Bot Control,
Manual Trading, Pending Orders, Position Management, Historical Backtesting & Risk Settings
"""

from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query, Body, Depends, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.adapters.mt5_adapter import mt5_adapter
from app.services.session_manager import session_manager
from app.services.trade_learner import trade_learner
from app.domain.models import (
    Candle, TickerData, AccountInfo, AccountPreview, SavedAccount, SavedAccountPublic, LoginHistoryEntry,
    MarketStructureAnalysis, HeuristicScoringResult, TradeDecisionReport, RiskCheckResult,
    TradeRecord, PendingOrderRecord, MT5ConnectRequest, ManualOrderRequest, RiskSettings, BacktestRequest,
    AccountConnectionResponse, ConnectionDiagnostics
)
from app.services.indicators import IndicatorService
from app.services.market_structure import MarketStructureEngine
from app.services.heuristic_scorer import heuristic_scorer
from app.services.decision_engine import decision_engine
from app.services.risk_manager import risk_manager
from app.services.trade_manager import trade_manager
from app.services.backtester import backtester
from app.services.trading_worker import trading_worker

router = APIRouter()

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: Optional[str] = Security(api_key_header)):
    if not api_key or api_key != settings.API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API Key"
        )


# ── HEALTH & ACCOUNT MANAGEMENT ─────────────────────────────────

@router.get("/health")
def get_health() -> Dict[str, Any]:
    account = mt5_adapter.get_account_info()
    return {
        "status": "ONLINE",
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "mt5_connection": account.connection_mode,
        "is_connected": account.is_connected,
        "bot_status": trade_manager.bot_status.value
    }


@router.get("/account/diagnostics")
def run_mt5_diagnostics(server: str = "MetaQuotes-Demo") -> Dict[str, Any]:
    """
    Run pre-login MT5 environment diagnostics without requiring credentials.
    Checks: MT5 package, terminal path, process, internet, server reachability.
    """
    from app.services.session_manager import MT5_AVAILABLE
    logs = []

    def log(msg: str) -> str:
        from datetime import datetime
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        logs.append(entry)
        return entry

    log("Running MT5 environment diagnostics...")

    # Check MT5 Python package
    pkg_ok = MT5_AVAILABLE
    log(f"MetaTrader5 Python package: {'INSTALLED' if pkg_ok else 'NOT INSTALLED — run: pip install MetaTrader5'}")

    # Check terminal path
    terminal_path = session_manager._find_mt5_terminal()
    log(f"MT5 terminal path: {terminal_path if terminal_path else 'NOT FOUND'}")

    # Check process running
    proc_running = session_manager._is_mt5_process_running()
    log(f"MT5 process (terminal64.exe): {'RUNNING' if proc_running else 'NOT RUNNING'}")

    # Check internet
    internet_ok = session_manager._check_internet()
    log(f"Internet connectivity: {'OK' if internet_ok else 'FAILED'}")

    # Check server reachable
    server_ok = session_manager._check_server_reachable(server)
    log(f"Server {server} reachability: {'REACHABLE' if server_ok else 'UNREACHABLE'}")

    overall_ok = pkg_ok and internet_ok
    return {
        "overall": "READY" if overall_ok else "ISSUES_DETECTED",
        "mt5_package_installed": pkg_ok,
        "terminal_path": terminal_path,
        "terminal_process_running": proc_running,
        "internet_ok": internet_ok,
        "server_reachable": server_ok,
        "diagnostic_logs": logs
    }


@router.get("/account", response_model=AccountInfo)
def get_account_info() -> AccountInfo:
    return mt5_adapter.get_account_info()


@router.post("/account/fetch-preview", response_model=AccountPreview)
def fetch_account_preview(req: MT5ConnectRequest) -> AccountPreview:
    return mt5_adapter.fetch_account_preview(req.login, req.password, req.server, req.broker, req.terminal_path)


@router.post("/account/connect", response_model=AccountConnectionResponse, dependencies=[Depends(verify_api_key)])
async def connect_account(req: MT5ConnectRequest) -> AccountConnectionResponse:
    success, msg, acc, diag = mt5_adapter.connect_account(
        req.login, req.password, req.server, req.broker, req.name, req.terminal_path
    )
    return AccountConnectionResponse(
        success=success,
        message=msg,
        account=acc if success else None,
        diagnostics=diag
    )


@router.post("/account/refresh", dependencies=[Depends(verify_api_key)])
def refresh_account() -> Dict[str, Any]:
    session_manager.sync_history_to_sqlite()
    acc = mt5_adapter.get_account_info()
    positions = mt5_adapter.get_positions()
    pending = mt5_adapter.get_pending_orders()
    return {
        "success": True,
        "account": acc,
        "positions": [p.model_dump() for p in positions],
        "pending_orders": [p.model_dump() for p in pending]
    }


@router.post("/account/disconnect", dependencies=[Depends(verify_api_key)])
async def disconnect_account() -> Dict[str, Any]:
    try:
        if trading_worker.is_running():
            await trading_worker.stop()
    except Exception:
        pass
    res = mt5_adapter.disconnect_account()
    return {"success": res, "message": "Disconnected from MetaTrader 5" if res else "Disconnect failed"}


@router.get("/account/saved", response_model=List[SavedAccountPublic])
def get_saved_accounts() -> List[SavedAccountPublic]:
    saved = session_manager.get_saved_accounts()
    return [
        SavedAccountPublic(
            id=sa.id,
            name=sa.name,
            login=sa.login,
            server=sa.server,
            broker=sa.broker,
            account_type=sa.account_type,
            currency=sa.currency,
            leverage=sa.leverage,
            terminal_path=sa.terminal_path,
            last_connected=sa.last_connected,
            is_active=sa.is_active
        )
        for sa in saved
    ]


@router.post("/account/saved/{account_id}/connect", response_model=AccountConnectionResponse, dependencies=[Depends(verify_api_key)])
async def connect_saved_account(account_id: str) -> AccountConnectionResponse:
    if account_id not in session_manager.saved_accounts:
        raise HTTPException(status_code=404, detail="Saved account not found")
    sa = session_manager.saved_accounts[account_id]
    success, msg, acc, diag = mt5_adapter.connect_account(sa.login, sa.password, sa.server, sa.broker, sa.name, sa.terminal_path)
    return AccountConnectionResponse(
        success=success,
        message=msg,
        account=acc if success else None,
        diagnostics=diag
    )


@router.post("/account/saved/{account_id}/refresh", dependencies=[Depends(verify_api_key)])
def refresh_saved_account(account_id: str) -> Dict[str, Any]:
    acc_info = session_manager.refresh_saved_account(account_id)
    if not acc_info:
        raise HTTPException(status_code=400, detail="Failed to refresh saved account or account not found.")
    return {"success": True, "account": acc_info}


@router.delete("/account/saved/{account_id}", dependencies=[Depends(verify_api_key)])
def remove_saved_account(account_id: str) -> Dict[str, Any]:
    res = session_manager.remove_saved_account(account_id)
    return {"success": res, "message": "Account removed from saved list" if res else "Account not found"}


@router.get("/account/login-history", response_model=List[LoginHistoryEntry])
def get_login_history() -> List[LoginHistoryEntry]:
    return session_manager.login_history


# ── BOT STATUS & CONTROLS ───────────────────────────────────────

@router.get("/bot/status")
def get_bot_status() -> Dict[str, Any]:
    from app.services.trading_worker import trading_worker
    status_data = trade_manager.get_status()
    status_data["engine_state"] = trading_worker.state.value if hasattr(trading_worker, 'state') else "STOPPED"
    status_data["engine_reason"] = trading_worker.status_reason if hasattr(trading_worker, 'status_reason') else ""
    live_reason = ""
    if hasattr(trading_worker, "last_payload") and isinstance(trading_worker.last_payload, dict):
        live_reason = trading_worker.last_payload.get("auto_trade_live_reason", "")
    status_data["auto_trade_live_reason"] = live_reason
    return status_data


@router.post("/bot/start", dependencies=[Depends(verify_api_key)])
async def start_bot(
    symbol: str = Query(default=settings.DEFAULT_SYMBOL),
    timeframe: str = Query(default="M15")
) -> Dict[str, Any]:
    if timeframe:
        trade_manager.update_risk_settings({"entry_timeframe": timeframe})

    if trading_worker.is_running():
        return {
            "success": True,
            "message": f"AUTO TRADING ALREADY RUNNING for {trading_worker.symbol} ({trading_worker.timeframe}).",
            "status": trade_manager.bot_status.value
        }
    success, msg = trade_manager.start_bot()
    if success:
        worker_ok, worker_msg = await trading_worker.start(symbol, timeframe)
        if not worker_ok:
            logger.warning(f"[BOT START] Centralized worker failed to start: {worker_msg}")
            trade_manager.pause_bot() # Revert status
            return {"success": False, "message": f"Worker start failed: {worker_msg}", "status": trade_manager.bot_status.value}
    return {"success": success, "message": msg, "status": trade_manager.bot_status.value}


@router.post("/bot/pause", dependencies=[Depends(verify_api_key)])
def pause_bot() -> Dict[str, Any]:
    success, msg = trade_manager.pause_bot()
    return {"success": success, "message": msg, "status": trade_manager.bot_status.value}


@router.post("/bot/resume", dependencies=[Depends(verify_api_key)])
def resume_bot() -> Dict[str, Any]:
    success, msg = trade_manager.resume_bot()
    return {"success": success, "message": msg, "status": trade_manager.bot_status.value}


@router.post("/bot/stop", dependencies=[Depends(verify_api_key)])
async def stop_bot() -> Dict[str, Any]:
    success, msg = trade_manager.stop_bot()
    await trading_worker.stop()
    return {"success": success, "message": msg, "status": trade_manager.bot_status.value}


@router.post("/bot/emergency-stop", dependencies=[Depends(verify_api_key)])
async def emergency_stop() -> Dict[str, Any]:
    success, msg = trade_manager.emergency_stop()
    await trading_worker.stop()
    return {"success": success, "message": msg, "status": trade_manager.bot_status.value}


# ── MARKET DATA & ANALYTICS ─────────────────────────────────────

@router.get("/market/ticker", response_model=TickerData)
def get_ticker(symbol: str = Query(default=settings.DEFAULT_SYMBOL)) -> TickerData:
    return mt5_adapter.get_ticker(symbol)


@router.get("/market/candles", response_model=List[Candle])
def get_candles(
    symbol: str = Query(default=settings.DEFAULT_SYMBOL),
    timeframe: str = Query(default="M15"),
    count: int = Query(default=200, ge=10, le=1000)
) -> List[Candle]:
    return mt5_adapter.fetch_candles(symbol, timeframe, count)


@router.get("/market/structure", response_model=MarketStructureAnalysis)
def get_market_structure(
    symbol: str = Query(default=settings.DEFAULT_SYMBOL),
    timeframe: str = Query(default="M15")
) -> MarketStructureAnalysis:
    candles = mt5_adapter.fetch_candles(symbol, timeframe, 200)
    return MarketStructureEngine.analyze(candles, timeframe, symbol)


@router.get("/heuristic/scoring", response_model=HeuristicScoringResult)
def get_heuristic_scoring(
    symbol: str = Query(default=settings.DEFAULT_SYMBOL),
    timeframe: str = Query(default="M15")
) -> HeuristicScoringResult:
    candles = mt5_adapter.fetch_candles(symbol, timeframe, 200)
    return heuristic_scorer.calculate_score(candles, timeframe, symbol)


@router.get("/trade/evaluate", response_model=TradeDecisionReport)
def evaluate_trade(
    symbol: str = Query(default=settings.DEFAULT_SYMBOL),
    timeframe: str = Query(default="M15")
) -> TradeDecisionReport:
    candles = mt5_adapter.fetch_candles(symbol, timeframe, 200)
    ticker = mt5_adapter.get_ticker(symbol)
    
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
    return decision_engine.evaluate_trade_opportunity(symbol, candles, ticker, timeframe, htf_candles=htf_candles)


# ── TRADE EXECUTION & MANAGEMENT ────────────────────────────────

@router.post("/trade/execute", dependencies=[Depends(verify_api_key)])
def execute_ai_trade(
    symbol: str = Query(default=settings.DEFAULT_SYMBOL),
    timeframe: str = Query(default="M15")
) -> Dict[str, Any]:
    res = trade_manager.evaluate_and_execute_pipeline(symbol, timeframe, force_execution=True)
    
    fresh_account = mt5_adapter.get_account_info()
    fresh_positions = mt5_adapter.get_positions()
    fresh_pending = mt5_adapter.get_pending_orders()

    report_obj = res.get("report")

    return {
        "success": res["success"],
        "message": res["message"],
        "trade": res.get("trade").model_dump() if res.get("trade") else None,
        "report": report_obj.model_dump() if report_obj else None,
        "risk_reasons": res.get("risk_reasons", []),
        "account": fresh_account.model_dump(),
        "positions": [p.model_dump() for p in fresh_positions],
        "pending_orders": [p.model_dump() for p in fresh_pending]
    }


@router.post("/trade/manual", dependencies=[Depends(verify_api_key)])
def execute_manual_order(req: ManualOrderRequest) -> Dict[str, Any]:
    success, order, msg = trade_manager.execute_manual_order(req)

    fresh_account = mt5_adapter.get_account_info()
    fresh_positions = mt5_adapter.get_positions()
    fresh_pending = mt5_adapter.get_pending_orders()

    return {
        "success": success,
        "message": msg,
        "order": order.model_dump() if order else None,
        "account": fresh_account.model_dump(),
        "positions": [p.model_dump() for p in fresh_positions],
        "pending_orders": [p.model_dump() for p in fresh_pending]
    }


@router.get("/trade/positions")
def get_positions() -> List[TradeRecord]:
    return mt5_adapter.get_positions()


@router.post("/trade/positions/modify", dependencies=[Depends(verify_api_key)])
def modify_position(
    trade_id: str = Query(...),
    sl: float = Query(...),
    tp: float = Query(...)
) -> Dict[str, Any]:
    success, msg = trade_manager.modify_position(trade_id, sl, tp)
    return {"success": success, "message": msg}


@router.post("/trade/positions/close", dependencies=[Depends(verify_api_key)])
def close_trade(trade_id: str = Query(...)) -> Dict[str, Any]:
    success, msg = mt5_adapter.close_order(trade_id, "USER_MANUAL_CLOSE")

    fresh_account = mt5_adapter.get_account_info()
    fresh_positions = mt5_adapter.get_positions()

    return {
        "success": success,
        "message": msg,
        "account": fresh_account.model_dump(),
        "positions": [p.model_dump() for p in fresh_positions]
    }


@router.post("/trade/positions/close-all", dependencies=[Depends(verify_api_key)])
def close_all_positions() -> Dict[str, Any]:
    count = mt5_adapter.close_all_positions("USER_CLOSE_ALL")

    fresh_account = mt5_adapter.get_account_info()
    fresh_positions = mt5_adapter.get_positions()

    return {
        "success": True,
        "closed_count": count,
        "message": f"Closed {count} active positions",
        "account": fresh_account.model_dump(),
        "positions": [p.model_dump() for p in fresh_positions]
    }


@router.get("/trade/pending")
def get_pending_orders() -> List[PendingOrderRecord]:
    return mt5_adapter.get_pending_orders()


@router.post("/trade/pending/cancel", dependencies=[Depends(verify_api_key)])
def cancel_pending_order(order_id: str = Query(...)) -> Dict[str, Any]:
    success, msg = mt5_adapter.cancel_pending_order(order_id)

    fresh_account = mt5_adapter.get_account_info()
    fresh_pending = mt5_adapter.get_pending_orders()

    return {
        "success": success,
        "message": msg,
        "account": fresh_account.model_dump(),
        "pending_orders": [p.model_dump() for p in fresh_pending]
    }


@router.get("/trade/history")
def get_trade_history(
    today_only: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=500)
) -> Dict[str, Any]:
    records = mt5_adapter.get_history(today_only=today_only)
    total_count = len(records)
    total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count > 0 else 1

    start_idx = (page - 1) * page_size
    end_idx = min(start_idx + page_size, total_count)
    paged_records = records[start_idx:end_idx] if start_idx < total_count else []

    return {
        "trades": [r.model_dump(mode="json") for r in paged_records],
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }


# ── RISK SETTINGS & BACKTESTING ─────────────────────────────────

@router.get("/settings/risk", response_model=RiskSettings)
def get_risk_settings() -> RiskSettings:
    return trade_manager.risk_settings


@router.post("/settings/risk", dependencies=[Depends(verify_api_key)])
def update_risk_settings(settings_data: RiskSettings) -> Dict[str, Any]:
    updated = trade_manager.update_risk_settings(settings_data)
    return {"success": True, "message": "Risk settings updated and saved to disk", "settings": updated.model_dump()}


@router.post("/settings/risk/reset-testing-baseline", dependencies=[Depends(verify_api_key)])
def reset_testing_risk_baseline() -> Dict[str, Any]:
    success, msg = trade_manager.reset_testing_risk_baseline()
    if not success:
        raise HTTPException(status_code=403, detail=msg)
    fresh_account = mt5_adapter.get_account_info()
    return {
        "success": success,
        "message": msg,
        "account": fresh_account.model_dump()
    }


@router.post("/settings/risk/reset-weekly-loss-lock", dependencies=[Depends(verify_api_key)])
def reset_weekly_loss_lock() -> Dict[str, Any]:
    success, msg = trade_manager.reset_weekly_loss_lock()
    if not success:
        raise HTTPException(status_code=403, detail=msg)
    fresh_account = mt5_adapter.get_account_info()
    return {
        "success": success,
        "message": msg,
        "account": fresh_account.model_dump()
    }


@router.get("/settings/learning/proposed")
def get_proposed_learning_thresholds() -> Dict[str, Any]:
    """Retrieves proposed adaptive thresholds for user review."""
    import json
    proposed_str = trade_learner.get_metadata("proposed_thresholds")
    proposed = json.loads(proposed_str) if proposed_str else trade_learner.get_optimized_thresholds()
    
    approved_str = trade_learner.get_metadata("approved_thresholds")
    approved = json.loads(approved_str) if approved_str else None
    
    return {
        "proposed": proposed,
        "approved": approved,
        "is_pending_review": approved != proposed
    }


@router.post("/settings/learning/approve", dependencies=[Depends(verify_api_key)])
def approve_proposed_learning_thresholds() -> Dict[str, Any]:
    """Approves proposed adaptive thresholds and applies them to the live decision engine."""
    import json
    proposed_str = trade_learner.get_metadata("proposed_thresholds")
    if not proposed_str:
        proposed = trade_learner.get_optimized_thresholds()
        proposed_str = json.dumps(proposed)
        
    trade_learner.set_metadata("approved_thresholds", proposed_str)
    return {
        "success": True, 
        "message": "Proposed adaptive thresholds approved and applied to live decision engine", 
        "approved_thresholds": json.loads(proposed_str)
    }


@router.post("/backtest/run")
def run_backtest(
    req: Optional[BacktestRequest] = Body(default=None),
    symbol: str = Query(default="XAUUSD"),
    timeframe: str = Query(default="M15"),
    count: int = Query(default=500),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    initial_balance: float = Query(default=10000.0),
    risk_pct: float = Query(default=1.0),
    lot_sizing_mode: str = Query(default="FIXED"),
    user_configured_lot: float = Query(default=0.10),
    min_risk_reward_ratio: float = Query(default=2.0),
    auto_trade_cooldown_seconds: int = Query(default=300),
    daily_loss_limit_pct: float = Query(default=3.0),
    max_consecutive_losses: int = Query(default=3),
    max_spread_pips: float = Query(default=2.5)
) -> Dict[str, Any]:
    if req is not None:
        return backtester.run_backtest(
            symbol=req.symbol,
            timeframe=req.timeframe,
            count=req.count,
            from_date=req.from_date,
            to_date=req.to_date,
            initial_balance=req.initial_balance,
            risk_pct=req.risk_pct,
            lot_sizing_mode=req.lot_sizing_mode,
            user_configured_lot=req.user_configured_lot,
            min_risk_reward_ratio=req.min_risk_reward_ratio,
            auto_trade_cooldown_seconds=req.auto_trade_cooldown_seconds,
            daily_loss_limit_pct=req.daily_loss_limit_pct,
            max_consecutive_losses=req.max_consecutive_losses,
            max_spread_pips=req.max_spread_pips,
            min_ai_confidence=req.min_ai_confidence
        )

    return backtester.run_backtest(
        symbol=symbol,
        timeframe=timeframe,
        count=count,
        from_date=from_date,
        to_date=to_date,
        initial_balance=initial_balance,
        risk_pct=risk_pct,
        lot_sizing_mode=lot_sizing_mode,
        user_configured_lot=user_configured_lot,
        min_risk_reward_ratio=min_risk_reward_ratio,
        auto_trade_cooldown_seconds=auto_trade_cooldown_seconds,
        daily_loss_limit_pct=daily_loss_limit_pct,
        max_consecutive_losses=max_consecutive_losses,
        max_spread_pips=max_spread_pips
    )


# ── MACHINE LEARNING MODELS & TRAINING ─────────────────────────

from fastapi import BackgroundTasks
from app.services.ai_predictor import ai_predictor
from ml.train_pipeline import MLTrainingPipeline
from datetime import datetime
import logging

logger = logging.getLogger("ApexTrader.Endpoints.ML")

@router.get("/ml/models")
def get_ml_models() -> List[Dict[str, Any]]:
    """
    List all available machine learning models in the models directory.
    """
    models = ai_predictor.list_available_models()
    active_ver = ai_predictor.active_model_version
    for m in models:
        m["is_active"] = (m["version"] == active_ver)
    return models


@router.post("/ml/models/activate", dependencies=[Depends(verify_api_key)])
def activate_ml_model(version: str = Query(...)) -> Dict[str, Any]:
    """
    Activates a specific version of the machine learning model.
    """
    success = ai_predictor.activate_model(version)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to activate model version '{version}'. Check server logs.")
    return {
        "success": True,
        "message": f"Successfully activated model version: {version}",
        "active_model_version": ai_predictor.active_model_version
    }


def _run_training_task(version: str):
    try:
        pipeline = MLTrainingPipeline()
        pipeline.run_training_pipeline(version)
        logger.info(f"[BACKGROUND TRAINING] Training of {version} completed successfully.")
    except Exception as e:
        logger.error(f"[BACKGROUND TRAINING] Training of {version} failed: {e}", exc_info=True)


@router.post("/ml/train", dependencies=[Depends(verify_api_key)])
def trigger_ml_training(
    background_tasks: BackgroundTasks,
    version: Optional[str] = Query(default=None)
) -> Dict[str, Any]:
    """
    Triggers chronological walk-forward validation and training of a new model version.
    """
    if getattr(trade_manager, "training_active", False):
        raise HTTPException(status_code=400, detail="Training is already in progress.")
        
    if not version:
        version = f"gold_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
    background_tasks.add_task(_run_training_task, version)
    
    return {
        "success": True,
        "message": f"Training task for version '{version}' started in background.",
        "version": version
    }

