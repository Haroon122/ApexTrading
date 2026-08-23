"""
System Configuration Module
Production-Grade AI Trading Bot Configuration
"""

import os
from typing import List, Dict, Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # System Info
    PROJECT_NAME: str = "ApexTrader AI - Production MT5 Bot"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = Field(default="production", env="ENVIRONMENT")
    SECRET_KEY: str = Field(default="super-secret-production-key-change-in-env", env="SECRET_KEY")
    API_KEY: str = Field(default="apex_secret_api_key_2026", env="API_KEY")
    CORS_ALLOWED_ORIGINS: List[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5000", "http://localhost:5173", "http://127.0.0.1:5173"],
        env="CORS_ALLOWED_ORIGINS"
    )

    # MT5 Settings
    MT5_LOGIN: int = Field(default=12345678, env="MT5_LOGIN")
    MT5_PASSWORD: str = Field(default="demo_password", env="MT5_PASSWORD")
    MT5_SERVER: str = Field(default="MetaQuotes-Demo", env="MT5_SERVER")
    DEFAULT_SYMBOL: str = Field(default="XAUUSD", env="DEFAULT_SYMBOL")
    DEFAULT_TIMEFRAME: str = Field(default="M1", env="DEFAULT_TIMEFRAME")
    SUPPORTED_SYMBOLS: List[str] = ["XAUUSD", "XAUUSDm", "XAUUSD.a", "GOLD", "GOLDm", "XAUUSD.s", "XAUUSD.i", "XAUUSD.g", "XAUUSD.c"]
    
    # Ensemble Weights (Rule Engine vs ML Model)
    WEIGHT_RULE_ENGINE: float = 0.80
    WEIGHT_ML_MODEL: float = 0.20
    
    # Session Filtering Settings
    ENABLE_SESSION_FILTER: bool = False
    ALLOWED_SESSIONS: List[str] = ["ASIAN", "LONDON", "NEWYORK", "LONDON_NY_OVERLAP"]


    # Risk Management Parameters
    MAX_RISK_PER_TRADE_PCT: float = Field(default=1.0, env="MAX_RISK_PER_TRADE_PCT")  # 1% equity
    DAILY_LOSS_LIMIT_PCT: float = Field(default=3.0, env="DAILY_LOSS_LIMIT_PCT")      # 3% max daily loss
    WEEKLY_LOSS_LIMIT_PCT: float = Field(default=7.0, env="WEEKLY_LOSS_LIMIT_PCT")    # 7% max weekly loss
    MAX_DRAWDOWN_LIMIT_PCT: float = Field(default=12.0, env="MAX_DRAWDOWN_LIMIT_PCT") # 12% total max drawdown
    MIN_RISK_REWARD_RATIO: float = Field(default=2.0, env="MIN_RISK_REWARD_RATIO")    # Minimum 1:2 R:R
    MAX_OPEN_TRADES: int = Field(default=5, env="MAX_OPEN_TRADES")
    MAX_OPEN_POSITIONS: int = Field(default=5, env="MAX_OPEN_POSITIONS")
    ENABLE_BREAK_EVEN: bool = True
    BREAK_EVEN_AT_RR: float = 1.0
    ENABLE_TRAILING_STOP: bool = True
    TRAILING_STOP_ATR_MULT: float = 1.5
    MAX_SPREAD_PIPS: float = 2.5
    ENABLE_LIVE_TRADING: bool = False

    # Heuristic Scoring Engine Parameters (Configured with backward-compatible AI parameter names)
    MIN_AI_CONFIDENCE: float = Field(default=0.70, env="MIN_AI_CONFIDENCE")  # 70% confidence required
    FEATURE_WINDOW_SIZE: int = 100
    ENSEMBLE_WEIGHTS: Dict[str, float] = {
        "xgboost": 0.40,  # Weight for Trend heuristics
        "lightgbm": 0.35, # Weight for Momentum heuristics
        "pytorch": 0.25   # Weight for Composite/Volatility heuristics
    }

    # Decision Engine Weights (Total = 1.0)
    WEIGHT_TREND: float = 0.25
    WEIGHT_MARKET_STRUCTURE: float = 0.25
    WEIGHT_MOMENTUM_VOLUME: float = 0.20
    WEIGHT_AI_CONFIDENCE: float = 0.20
    WEIGHT_RISK_NEWS: float = 0.10
    MIN_CONFIRMATION_SCORE: float = 0.72  # 72% minimum score to enter trade

    # Database
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./trading_bot.db",
        env="DATABASE_URL"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
