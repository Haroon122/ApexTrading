# ApexTrader — Production MT5 Algorithmic Trading System

A production-grade algorithmic trading system with:
- **FastAPI** backend with REST API + WebSocket real-time streaming
- **Multi-confirmation Decision Engine** (5-pillar confirmation scoring)
- **Heuristic Signal Scoring Engine** (Weighted combination of Trend, Momentum, and Volatility heuristics)
- **Smart Money Concepts** market structure analysis (BOS, CHoCH, Order Blocks, FVGs)
- **Institutional Risk Management** (daily/weekly loss limits, dynamic position sizing)
- **MT5 Adapter** (live MT5 or high-fidelity simulated trading mode)
- **Vanilla HTML/CSS/JS Dashboard** with real-time candlestick charts

---

## Project Structure

```
Trading_Bot/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI entrypoint + WebSocket
│   │   ├── adapters/
│   │   │   ├── __init__.py
│   │   │   └── mt5_adapter.py         # MT5 live/simulated trading adapter
│   │   ├── api/
│   │   │   └── v1/
│   │   │       ├── __init__.py
│   │   │       └── endpoints.py       # REST API routes
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   └── config.py              # System configuration
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   └── models.py              # Pydantic domain models
│   │   └── services/
│   │       ├── __init__.py
│   │       ├── ai_predictor.py        # Ensemble AI prediction engine
│   │       ├── backtester.py          # Historical strategy backtester
│   │       ├── decision_engine.py     # Multi-pillar trade evaluator
│   │       ├── indicators.py          # Technical analysis (RSI, MACD, ATR...)
│   │       ├── market_structure.py    # Smart money concepts engine
│   │       ├── news_filter.py         # Economic calendar filter
│   │       ├── risk_manager.py        # Institutional risk management
│   │       └── trade_manager.py       # Trade lifecycle management
│   ├── ml/
│   │   └── train_pipeline.py          # ML model training pipeline
│   └── requirements.txt
└── frontend/
    ├── index.html                     # Main dashboard HTML
    ├── styles.css                     # Dark theme styles
    └── app.js                         # Dashboard JavaScript engine
```

---

## Quick Start

### 1. Start the Backend

```powershell
# Navigate to the backend directory
cd C:\Users\localadmin\Desktop\Trading_Bot\backend

# Run with Python
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or use the batch file:
```
start_backend.bat
```

### 2. Open the Dashboard

Open `frontend/index.html` directly in your browser, or run:
```
open_dashboard.bat
```

- **Backend API**: http://localhost:8000
- **API Docs (Swagger)**: http://localhost:8000/docs
- **Dashboard**: Open `frontend/index.html` in browser

---

## Dependencies

Install via pip:
```bash
pip install fastapi uvicorn pydantic pydantic-settings pandas numpy scikit-learn xgboost lightgbm websockets python-dotenv
```

---

## Configuration

All settings are in `backend/app/core/config.py` and can be overridden via environment variables or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `MT5_LOGIN` | `12345678` | MT5 account login |
| `MT5_PASSWORD` | `demo_password` | MT5 password |
| `MT5_SERVER` | `MetaQuotes-Demo` | MT5 server name |
| `MIN_AI_CONFIDENCE` | `0.70` | Minimum AI confidence threshold |
| `MAX_RISK_PER_TRADE_PCT` | `1.0` | Maximum risk per trade (% of equity) |
| `DAILY_LOSS_LIMIT_PCT` | `3.0` | Daily loss circuit breaker |

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/health` | System health check |
| GET | `/api/v1/account` | Account info |
| GET | `/api/v1/market/ticker` | Live ticker |
| GET | `/api/v1/market/candles` | OHLCV candles |
| GET | `/api/v1/market/structure` | Smart money structure analysis |
| GET | `/api/v1/ai/prediction` | AI ensemble prediction |
| GET | `/api/v1/trade/evaluate` | Full trade evaluation report |
| POST | `/api/v1/trade/execute` | Execute AI signal |
| GET | `/api/v1/trade/positions` | Open positions |
| POST | `/api/v1/trade/close` | Close position |
| POST | `/api/v1/backtest/run` | Run historical backtest |
| WS | `/ws/market?symbol=EURUSD` | Real-time WebSocket stream |
