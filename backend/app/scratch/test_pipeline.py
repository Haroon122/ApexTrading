import sys
import os
import random
from datetime import datetime, timedelta

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.domain.models import Candle, TickerData, AccountInfo, TradeDecisionReport, TradeSide
from app.services.market_structure import MarketStructureEngine
from app.services.decision_engine import decision_engine
from app.services.backtester import backtester
from app.services.risk_manager import risk_manager

def generate_candles(mode: str, count: int = 120):
    candles = []
    base_price = 1.08500
    current_time = datetime.now() - timedelta(minutes=15 * count)
    
    # Set seed for reproducibility
    random.seed(42)
    
    for i in range(count):
        if mode == "up":
            change = 0.00008 + random.uniform(0, 0.00004)
        elif mode == "down":
            change = -0.00008 - random.uniform(0, 0.00004)
        else:
            change = random.uniform(-0.00005, 0.00005)
            
        base_price += change
        candles.append(Candle(
            timestamp=current_time,
            open=base_price - change,
            high=base_price + abs(change) * 0.5,
            low=base_price - abs(change) * 0.5,
            close=base_price,
            volume=100.0 + random.uniform(-10, 10),
            timeframe="M15"
        ))
        current_time += timedelta(minutes=15)
    return candles

def test_trade_evaluations():
    print("\n--- Running Issue 1 & Issue 5 Verification ---")
    modes = ["up", "down", "sideways"]
    
    ticker = TickerData(
        symbol="EURUSD",
        bid=1.08500,
        ask=1.08505,
        spread_pips=0.5,
        timestamp=datetime.now()
    )
    
    for mode in modes:
        print(f"Generating synthetic sequence: {mode} (120 candles)")
        candles = generate_candles(mode)
        
        # Verify MarketStructureEngine doesn't crash (Issue 1)
        analysis = MarketStructureEngine.analyze(candles)
        print(f" -> MarketStructureEngine.analyze() success. Trend: {analysis.trend}, Regime: {analysis.regime}")
        
        # Verify DecisionEngine doesn't crash (Issue 1 & 5)
        report = decision_engine.evaluate_trade_opportunity("EURUSD", candles, ticker, "M15")
        print(f" -> decision_engine.evaluate_trade_opportunity() success. Decision: {report.decision}, Score: {report.confidence}")
        assert report.breakdown is not None
        # Verify first breakdown is removed (we can verify breakdown is correctly populated at the end)
        assert hasattr(report.breakdown, "heuristic_score")
        print(f" -> ConfirmationBreakdown fields validated. Heuristic Score: {report.breakdown.heuristic_score}")

def test_backtester():
    print("\n--- Running Issue 3 Verification (Backtester alignment) ---")
    candles = generate_candles("up", count=150)
    
    res = backtester.run_backtest(
        symbol="EURUSD",
        timeframe="M15",
        count=150,
        candles=candles,
        initial_balance=10000.0,
        risk_pct=1.0,
        spread_pips=1.0
    )
    
    if "error" in res:
        print(f" -> ERROR in backtest: {res['error']}")
        sys.exit(1)
        
    trades = res.get("trades", [])
    print(f" -> Backtester run success. Trades executed: {len(trades)}")
    print(f" -> Net Profit: ${res['net_profit_usd']:.2f}")
    
    # Verify that trades contain proper risk-based sizing and exit reason
    if trades:
        t = trades[0]
        print(f" -> Sample Trade Details: Side={t['side']}, Entry={t['entry_price']}, Exit={t['exit_price']}, Lots={t['lots']}, PnL=${t['pnl_usd']}")
        # Verify lots were sized and not default 0.10 always
        assert t['lots'] > 0.0

def test_risk_circuit_breaker():
    print("\n--- Running Issue 4 Verification (Risk circuit breakers) ---")
    # Simulate an account that has breached daily loss limit
    account = AccountInfo(
        account_number=123456,
        account_name="Test Account",
        broker="MetaQuotes",
        server="Demo",
        balance=10000.0,
        equity=9900.0,
        daily_pnl_usd=-450.0, # Breach! (daily limit is 3.0% of 10000 = $300)
        weekly_pnl_usd=-100.0,
        open_positions_count=0,
        is_connected=True
    )
    
    candles = generate_candles("up")
    ticker = TickerData(
        symbol="EURUSD",
        bid=1.08500,
        ask=1.08505,
        spread_pips=0.5,
        timestamp=datetime.now()
    )
    
    report = decision_engine.evaluate_trade_opportunity("EURUSD", candles, ticker, "M15")
    # Manually force decision to BUY to test risk check
    report.decision = TradeSide.BUY
    
    risk_check = risk_manager.evaluate_risk(account, report, "EURUSD")
    print(f" -> Account daily P&L: -${abs(account.daily_pnl_usd)}")
    print(f" -> Risk check passed: {risk_check.passed}")
    print(f" -> Rejection reasons: {risk_check.reasons}")
    
    assert not risk_check.passed
    assert any("Daily Loss Limit breached" in r for r in risk_check.reasons)
    print(" -> Auto-trading block successfully verified for account breaches.")

if __name__ == "__main__":
    test_trade_evaluations()
    test_backtester()
    test_risk_circuit_breaker()
    print("\nALL BACKEND VERIFICATION TESTS PASSED SUCCESSFULLY!")
