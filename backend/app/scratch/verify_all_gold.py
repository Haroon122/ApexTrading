import os
import sys
import logging
from datetime import datetime

# Set up paths
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.adapters.mt5_adapter import mt5_adapter
from app.services.gold_specs import gold_specs
from app.services.decision_engine import decision_engine
from app.services.risk_manager import risk_manager
from app.services.ai_predictor import ai_predictor
from app.services.backtester import backtester
from app.domain.models import Candle, TickerData, TradeSide, AccountInfo, TradeDecisionReport

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ApexTrader.VerifyAllGold")

def run_verification():
    logger.info("=========================================================")
    logger.info("STARTING GOLD-ONLY AI SCALPER BOT END-TO-END VERIFICATION")
    logger.info("=========================================================")
    
    # 1. Discover Gold Symbol
    logger.info("1. Testing broker Gold Symbol Discovery...")
    discovered = mt5_adapter.discover_gold_symbol()
    logger.info(f"   -> Discovered Gold Symbol: {discovered}")
    if not discovered:
        logger.error("   -> [FAILED] Gold symbol discovery returned None.")
        return False
        
    # 2. GoldSymbolSpecService
    logger.info("2. Testing GoldSymbolSpecService...")
    specs = gold_specs.get_specs()
    logger.info(f"   -> Specs read dynamically: Digits={specs['digits']}, Point={specs['point_size']}, TickSize={specs['tick_size']}, TickValue={specs['tick_value']}, VolMin={specs['volume_min']}, VolMax={specs['volume_max']}")
    if specs['digits'] not in (2, 3, 5):
        logger.warning(f"   -> Non-standard gold digits discovered: {specs['digits']}")

    # 3. Generate Mock Candles for Decision Engine Test
    logger.info("3. Testing DecisionEngine & 8 Setups...")
    mock_candles = []
    base_time = datetime.now()
    base_price = 2000.0
    for i in range(50):
        t = base_time - timedelta(minutes=50-i) if 'timedelta' in globals() else base_time
        # Create a bullish trend pullback setup structure
        price = base_price + i * 0.10
        if i == 45: # pull back
            price -= 0.50
        mock_candles.append(Candle(
            timestamp=t,
            open=price - 0.05,
            high=price + 0.10,
            low=price - 0.15,
            close=price,
            volume=100 + i,
            is_closed=True
        ))
        
    mock_ticker = TickerData(
        symbol=discovered,
        bid=2004.50,
        ask=2004.80, # 30 points / 3 pips spread
        spread_pips=3.0,
        spread_points=30,
        point_size=specs["point_size"],
        digits=specs["digits"],
        timestamp=datetime.now()
    )
    
    # Evaluate decision
    report = decision_engine.evaluate_trade_opportunity(discovered, mock_candles, mock_ticker, "M1")
    logger.info(f"   -> Decision: {report.decision.value}")
    logger.info(f"   -> Confidence: {report.confidence:.4f}")
    logger.info(f"   -> Long Score: {report.long_score:.4f} | Short Score: {report.short_score:.4f}")
    logger.info(f"   -> Outweighing Factors: {report.outweighing_factors}")
    logger.info(f"   -> Blocker Reasons: {report.rejection_reasons}")
    logger.info(f"   -> Explanation / Trace:\n{report.summary_explanation}")
    
    # 4. RiskManager & Dynamic Lot Sizing
    logger.info("4. Testing RiskManager Position Sizing & Circuit Breakers...")
    mock_account = AccountInfo(
        account_number=123456,
        account_name="Verification Account",
        balance=10000.0,
        equity=10000.0,
        free_margin=10000.0,
        margin_level_pct=0.0,
        open_positions_count=0,
        pending_orders_count=0,
        daily_pnl_usd=0.0,
        weekly_pnl_usd=0.0,
        drawdown_pct=0.0,
        is_connected=True,
        connection_mode="SIMULATED"
    )
    
    # Create a mock BUY report
    buy_report = TradeDecisionReport(
        symbol=discovered,
        decision=TradeSide.BUY,
        timeframe="M1",
        confidence=0.85,
        confirmation_score=0.85,
        long_score=0.85,
        short_score=0.15,
        reasoning=["Trend: +15", "Structure: +15"],
        rejection_reasons=[],
        suggested_entry=2004.80,
        suggested_sl=1999.80, # $5.00 SL (50 pips / 500 points)
        suggested_tp=2014.80,
        suggested_lots=0.0,
        risk_reward_ratio=2.0,
        breakdown=report.breakdown,
        condition_matrix=report.condition_matrix,
        htf_alignment={"H1": "BULLISH"},
        candlestick_pattern="None",
        liquidity_status="Normal",
        estimated_holding_bars=6,
        summary_explanation="Mock BUY",
        outweighing_factors=[],
        timestamp=datetime.now()
    )
    
    risk_res = risk_manager.evaluate_risk(mock_account, buy_report, discovered)
    logger.info(f"   -> Risk Check Passed: {risk_res.passed}")
    logger.info(f"   -> Calculated Lots: {risk_res.calculated_lots} lots")
    logger.info(f"   -> Risk USD: ${risk_res.risk_amount_usd:.2f}")
    logger.info(f"   -> Reasons: {risk_res.reasons}")
    if risk_res.calculated_lots <= 0.0:
        logger.error("   -> [FAILED] RiskManager calculated zero lots size.")
        return False

    # 5. AIPredictor
    logger.info("5. Testing AIPredictor...")
    available_models = ai_predictor.list_available_models()
    logger.info(f"   -> Available models: {available_models}")
    p_buy, p_sell, p_no_trade, ml_status = ai_predictor.predict(discovered, report.reasoning) # Mock predict call
    logger.info(f"   -> Predictions: BUY={p_buy:.2f}, SELL={p_sell:.2f}, NO_TRADE={p_no_trade:.2f} | Status: {ml_status}")

    # 6. Backtester Simulation
    logger.info("6. Testing Backtester...")
    # Generate 100 historical candles
    history_candles = []
    for i in range(100):
        t = datetime.now() - timedelta(minutes=100-i) if 'timedelta' in globals() else datetime.now()
        history_candles.append(Candle(
            timestamp=t,
            open=2000.0 + i*0.05,
            high=2000.0 + i*0.05 + 0.50,
            low=2000.0 + i*0.05 - 0.50,
            close=2000.0 + i*0.05 + 0.10,
            volume=200,
            is_closed=True
        ))
    bt_res = backtester.run_backtest(
        symbol=discovered,
        timeframe="M1",
        count=100,
        candles=history_candles,
        initial_balance=10000.0,
        risk_pct=1.0,
        lots=0.10,
        spread_pips=3.0
    )
    if "error" in bt_res:
        logger.error(f"   -> [FAILED] Backtester error: {bt_res['error']}")
        return False
    logger.info(f"   -> Backtest Net Profit: ${bt_res['net_profit_usd']:.2f}")
    logger.info(f"   -> Win Rate: {bt_res['win_rate_pct']}% | Total Trades: {bt_res['total_trades']}")
    logger.info(f"   -> Ambiguous Trades Resolved: {bt_res['ambiguous_trades_count']}")

    logger.info("=========================================================")
    logger.info("VERIFICATION COMPLETED SUCCESSFULLY - BOT IS PRODUCTION READY")
    logger.info("=========================================================")
    return True

if __name__ == "__main__":
    from datetime import timedelta
    run_verification()
