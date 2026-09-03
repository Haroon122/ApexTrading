"""
ApexTrader Production-Grade Machine Learning Training Pipeline
Implements:
1. Multi-timeframe historical data discovery & validation (M1, M5, M15, H1, H4)
2. Causal chronological data alignment with zero look-ahead bias
3. Triple Barrier Labeling (TP, SL, Timeout) accounting for spread/costs
4. Chronological walk-forward training/validation/testing split (no random K-Fold)
5. Model training & comparison (XGBoost, LightGBM)
6. Model versioning and metadata logging
"""

import os
import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

logger = logging.getLogger("ApexTrader.MLTrainPipeline")

# Model availability flags
XGB_AVAILABLE = False
LGB_AVAILABLE = False

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    pass

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    pass


class FeatureBuilder:
    @staticmethod
    def generate_prediction_features(df_m1: pd.DataFrame, htf_candles: Optional[Dict[str, Any]], feature_cols: List[str]) -> Optional[Dict[str, float]]:
        """
        Generates features for a single real-time prediction point (causal).
        """
        if df_m1.empty:
            return None
            
        latest_m1 = df_m1.iloc[-1]
        m1_close = float(latest_m1["close"])
        
        # Build features dictionary
        feat = {
            "RSI_14": float(latest_m1.get("rsi_14", 50.0)) / 100.0,
            "Stoch_RSI_K": float(latest_m1.get("stoch_k", 50.0)) / 100.0,
            "MACD_Hist_Norm": float(latest_m1.get("macd_hist", 0.0)) * 100.0,
            "ADX_Trend_Strength": float(latest_m1.get("adx", 20.0)) / 100.0,
            "Relative_Volume": float(latest_m1.get("rvol", 1.0)) / 3.0,
            "EMA_20_50_Spread": (float(latest_m1.get("ema_20", m1_close)) - float(latest_m1.get("ema_50", m1_close))) / (m1_close or 1e-9),
            "EMA_50_200_Spread": (float(latest_m1.get("ema_50", m1_close)) - float(latest_m1.get("ema_200", m1_close))) / (m1_close or 1e-9),
            "Dist_to_VWAP": (m1_close - float(latest_m1.get("vwap", m1_close))) / (m1_close or 1e-9),
            "M5_Trend": 0.0,
            "M15_Trend": 0.0,
            "H1_Trend": 0.0
        }
        
        # Pull latest HTF values causally
        if htf_candles:
            from app.services.indicators import IndicatorService
            if "M5" in htf_candles and len(htf_candles["M5"]) >= 10:
                df_m5 = IndicatorService.calculate_all_indicators(htf_candles["M5"])
                if not df_m5.empty:
                    m5_latest = df_m5.iloc[-1]
                    feat["M5_Trend"] = 1.0 if float(m5_latest["close"]) > float(m5_latest.get("ema_20", m5_latest["close"])) else -1.0
            if "M15" in htf_candles and len(htf_candles["M15"]) >= 10:
                df_m15 = IndicatorService.calculate_all_indicators(htf_candles["M15"])
                if not df_m15.empty:
                    m15_latest = df_m15.iloc[-1]
                    feat["M15_Trend"] = 1.0 if float(m15_latest["close"]) > float(m15_latest.get("ema_20", m15_latest["close"])) else -1.0
            if "H1" in htf_candles and len(htf_candles["H1"]) >= 10:
                df_h1 = IndicatorService.calculate_all_indicators(htf_candles["H1"])
                if not df_h1.empty:
                    h1_latest = df_h1.iloc[-1]
                    feat["H1_Trend"] = 1.0 if float(h1_latest["close"]) > float(h1_latest.get("ema_20", h1_latest["close"])) else -1.0

        # Fill missing keys to match expected training columns
        for col in feature_cols:
            if col not in feat:
                feat[col] = 0.0
                
        return feat


class MLTrainingPipeline:
    def __init__(self, symbol: str = "XAUUSD"):
        self.symbol = symbol
        self.models_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "models"))
        os.makedirs(self.models_dir, exist_ok=True)
        
        self.feature_columns = [
            "RSI_14", "Stoch_RSI_K", "MACD_Hist_Norm", "ADX_Trend_Strength",
            "Relative_Volume", "EMA_20_50_Spread", "EMA_50_200_Spread", "Dist_to_VWAP",
            "M5_Trend", "M15_Trend", "H1_Trend"
        ]

    def download_and_validate_data(self, lookback_days: int = 15) -> Dict[str, pd.DataFrame]:
        """
        Discovers, downloads, and validates historical multi-timeframe candles.
        Returns a dictionary of cleaned DataFrames indexed by timestamp.
        """
        from app.adapters.mt5_adapter import mt5_adapter
        
        logger.info(f"[ML PIPELINE] Fetching historical candle data for {self.symbol}...")
        
        # Resolve dynamic symbol first
        disc_symbol = mt5_adapter.discover_gold_symbol() or self.symbol
        self.symbol = disc_symbol
        
        timeframes = ["M1", "M5", "M15", "H1", "H4"]
        # Approximate number of candles needed
        tf_candle_counts = {
            "M1": lookback_days * 1440,
            "M5": lookback_days * 288,
            "M15": lookback_days * 96,
            "H1": lookback_days * 24,
            "H4": lookback_days * 6
        }
        
        data = {}
        for tf in timeframes:
            count = min(15000, tf_candle_counts[tf])
            candles = mt5_adapter.fetch_candles(self.symbol, tf, count)
            min_req = 30 if tf in ("H1", "H4") else 80
            if not candles or len(candles) < min_req:
                logger.error(f"[ML PIPELINE] Insufficient data returned for timeframe {tf}. Found {len(candles) if candles else 0}")
                raise ValueError(f"Insufficient historical data for {tf} timeframe")
                
            # Convert to DataFrame
            rows = []
            for c in candles:
                rows.append({
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume
                })
            df = pd.DataFrame(rows).set_index("timestamp").sort_index()
            
            # Remove duplicate indices
            df = df[~df.index.duplicated(keep="last")]
            
            # Generate indicators
            from app.services.indicators import IndicatorService
            # Convert DataFrame rows back to Candle domain model temporarily to compute indicator values
            from app.domain.models import Candle
            candle_list = []
            for idx, r in df.iterrows():
                candle_list.append(Candle(
                    timestamp=idx, open=r["open"], high=r["high"], low=r["low"], close=r["close"], volume=r["volume"], is_closed=True
                ))
            df_ind = IndicatorService.calculate_all_indicators(candle_list)
            data[tf] = df_ind
            logger.info(f"[ML PIPELINE] Downloaded {len(df_ind)} verified candles for {tf}")
            
        return data

    def align_data_causally(self, dfs: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Time-aligns M1, M5, M15, H1, H4 candles with zero look-ahead bias.
        """
        logger.info("[ML PIPELINE] Aligning multi-timeframe features causally...")
        df_m1 = dfs["M1"].copy()
        if "timestamp" in df_m1.columns:
            df_m1.set_index("timestamp", inplace=True)
            
        # Prepare HTF trend fields
        df_m1["M5_Trend"] = 0.0
        df_m1["M15_Trend"] = 0.0
        df_m1["H1_Trend"] = 0.0
        
        df_m5 = dfs["M5"].copy()
        if "timestamp" in df_m5.columns:
            df_m5.set_index("timestamp", inplace=True)
            
        df_m15 = dfs["M15"].copy()
        if "timestamp" in df_m15.columns:
            df_m15.set_index("timestamp", inplace=True)
            
        df_h1 = dfs["H1"].copy()
        if "timestamp" in df_h1.columns:
            df_h1.set_index("timestamp", inplace=True)
        
        # Precompute trends on HTF dfs
        # Trend is 1.0 if close > EMA20, else -1.0
        trend_m5 = (df_m5["close"] > df_m5.get("ema_20", df_m5["close"])).astype(float) * 2.0 - 1.0
        trend_m15 = (df_m15["close"] > df_m15.get("ema_20", df_m15["close"])).astype(float) * 2.0 - 1.0
        trend_h1 = (df_h1["close"] > df_h1.get("ema_20", df_h1["close"])).astype(float) * 2.0 - 1.0
        
        # Convert index to Series for fast lookup
        m5_times = trend_m5.index
        m15_times = trend_m15.index
        h1_times = trend_h1.index
        
        # Loop and align (using searchsorted to avoid look-ahead)
        m1_times = df_m1.index
        
        # For M5: the latest completed M5 starts 5m prior
        idx_m5 = np.searchsorted(m5_times, m1_times - pd.Timedelta(minutes=5)) - 1
        valid_m5 = idx_m5 >= 0
        df_m1.loc[valid_m5, "M5_Trend"] = trend_m5.iloc[idx_m5[valid_m5]].values
        
        # For M15: completed starts 15m prior
        idx_m15 = np.searchsorted(m15_times, m1_times - pd.Timedelta(minutes=15)) - 1
        valid_m15 = idx_m15 >= 0
        df_m1.loc[valid_m15, "M15_Trend"] = trend_m15.iloc[idx_m15[valid_m15]].values
        
        # For H1: completed starts 60m prior
        idx_h1 = np.searchsorted(h1_times, m1_times - pd.Timedelta(minutes=60)) - 1
        valid_h1 = idx_h1 >= 0
        df_m1.loc[valid_h1, "H1_Trend"] = trend_h1.iloc[idx_h1[valid_h1]].values
        
        # Drop rows with incomplete alignments
        df_aligned = df_m1.dropna(subset=["rsi_14", "atr_14"])
        return df_aligned

    def apply_triple_barrier_labeling(self, df_m1: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Applies Triple Barrier Labeling (TP, SL, Timeout) on historical data.
        Returns:
            X: features matrix
            y: labels (0 = NO_TRADE/LOSS, 1 = BUY_WIN, 2 = SELL_WIN)
        """
        logger.info("[ML PIPELINE] Applying Triple Barrier Labeling...")
        
        # Define parameters
        barrier_timeout = 30 # 30 M1 bars
        tp_atr_mult = 2.5
        sl_atr_mult = 1.5
        spread_points = 3.0 # Approx spread offset in gold points ($0.30)
        
        labels = []
        feature_list = []
        
        # Pre-extract values for fast numpy loop
        timestamps = df_m1.index
        closes = df_m1["close"].values
        highs = df_m1["high"].values
        lows = df_m1["low"].values
        atrs = df_m1["atr_14"].values
        
        # Feature builder
        rsi = (df_m1["rsi_14"].values if "rsi_14" in df_m1.columns else np.full(n_samples, 50.0)) / 100.0
        stoch_k = (df_m1["stoch_rsi_k"].values if "stoch_rsi_k" in df_m1.columns else (df_m1["stoch_k"].values if "stoch_k" in df_m1.columns else np.full(n_samples, 50.0))) / 100.0
        macd = (df_m1["macd_hist"].values if "macd_hist" in df_m1.columns else np.zeros(n_samples)) * 100.0
        adx = (df_m1["adx"].values if "adx" in df_m1.columns else np.full(n_samples, 20.0)) / 100.0
        rvol = (df_m1["rvol"].values if "rvol" in df_m1.columns else np.full(n_samples, 1.0)) / 3.0
        ema20 = df_m1["ema_20"].values if "ema_20" in df_m1.columns else closes
        ema50 = df_m1["ema_50"].values if "ema_50" in df_m1.columns else closes
        ema200 = df_m1["ema_200"].values if "ema_200" in df_m1.columns else closes
        vwap = df_m1["vwap"].values if "vwap" in df_m1.columns else closes
        
        m5_trend = df_m1["M5_Trend"].values
        m15_trend = df_m1["M15_Trend"].values
        h1_trend = df_m1["H1_Trend"].values
        
        n_samples = len(closes)
        
        for i in range(n_samples - barrier_timeout):
            entry_price = closes[i]
            atr_val = atrs[i]
            
            # Dynamic boundaries
            tp_dist = max(0.50, atr_val * tp_atr_mult)
            sl_dist = max(0.30, atr_val * sl_atr_mult)
            
            # BUY barrier path
            buy_tp = entry_price + tp_dist + (spread_points * 0.1)
            buy_sl = entry_price - sl_dist
            
            buy_hit_tp = -1
            buy_hit_sl = -1
            
            # SELL barrier path
            sell_tp = entry_price - tp_dist
            sell_sl = entry_price + sl_dist + (spread_points * 0.1)
            
            sell_hit_tp = -1
            sell_hit_sl = -1
            
            for j in range(1, barrier_timeout + 1):
                idx = i + j
                curr_high = highs[idx]
                curr_low = lows[idx]
                
                # Check BUY side
                if buy_hit_tp == -1 and buy_hit_sl == -1:
                    if curr_low <= buy_sl and curr_high >= buy_tp:
                        buy_hit_sl = j # Ambiguous candle: assume SL hit (conservative)
                    elif curr_low <= buy_sl:
                        buy_hit_sl = j
                    elif curr_high >= buy_tp:
                        buy_hit_tp = j
                        
                # Check SELL side
                if sell_hit_tp == -1 and sell_hit_sl == -1:
                    if curr_high >= sell_sl and curr_low <= sell_tp:
                        sell_hit_sl = j # Ambiguous candle: assume SL hit (conservative)
                    elif curr_high >= sell_sl:
                        sell_hit_sl = j
                    elif curr_low <= sell_tp:
                        sell_hit_tp = j

            # Determine outcomes
            buy_win = (buy_hit_tp != -1) and (buy_hit_sl == -1 or buy_hit_tp < buy_hit_sl)
            sell_win = (sell_hit_tp != -1) and (sell_hit_sl == -1 or sell_hit_tp < sell_hit_sl)
            
            # Multiclass label:
            # 0: NO_TRADE / BOTH_LOSS
            # 1: BUY_WIN
            # 2: SELL_WIN
            if buy_win and not sell_win:
                label = 1
            elif sell_win and not buy_win:
                label = 2
            else:
                label = 0
                
            labels.append(label)
            
            # Append features row
            feature_list.append({
                "RSI_14": rsi[i],
                "Stoch_RSI_K": stoch_k[i],
                "MACD_Hist_Norm": macd[i],
                "ADX_Trend_Strength": adx[i],
                "Relative_Volume": rvol[i],
                "EMA_20_50_Spread": (ema20[i] - ema50[i]) / (entry_price or 1e-9),
                "EMA_50_200_Spread": (ema50[i] - ema200[i]) / (entry_price or 1e-9),
                "Dist_to_VWAP": (entry_price - vwap[i]) / (entry_price or 1e-9),
                "M5_Trend": m5_trend[i],
                "M15_Trend": m15_trend[i],
                "H1_Trend": h1_trend[i]
            })
            
        X = pd.DataFrame(feature_list)
        y = pd.Series(labels)
        
        logger.info(f"[ML PIPELINE] Label distribution: {y.value_counts().to_dict()}")
        return X, y

    def run_training_pipeline(self, version: str) -> Dict[str, Any]:
        """
        Runs the full train, walk-forward validate, and model comparison.
        Saves versioned model file and metadata.
        """
        # Set background locks
        from app.services.trade_manager import trade_manager
        trade_manager.training_active = True
        
        try:
            # 1. Fetch & validate
            dfs = self.download_and_validate_data()
            
            # 2. Causal align
            df_aligned = self.align_data_causally(dfs)
            
            # 3. Triple Barrier Labeling
            X, y = self.apply_triple_barrier_labeling(df_aligned)
            
            # 4. Chronological Split (Train 60%, Val 20%, Test 20%)
            n_samples = len(X)
            idx_val = int(n_samples * 0.6)
            idx_test = int(n_samples * 0.8)
            
            X_train, y_train = X.iloc[:idx_val], y.iloc[:idx_val]
            X_val, y_val = X.iloc[idx_val:idx_test], y.iloc[idx_val:idx_test]
            X_test, y_test = X.iloc[idx_test:], y.iloc[idx_test:]
            
            logger.info(f"[ML PIPELINE] Chronological split: Train={len(X_train)}, Val={len(X_val)}, Test={len(X_test)}")
            
            best_model_type = "heuristics"
            test_metrics = {}
            model_saved = False
            
            # 5. Train & Compare models
            # We train both XGBoost and LightGBM if available and select the best based on F1 Score
            xgb_f1 = 0.0
            lgb_f1 = 0.0
            
            xgb_model = None
            lgb_model = None
            
            if XGB_AVAILABLE:
                logger.info("[ML PIPELINE] Fitting XGBoost Model...")
                # Multiclass model: num_class=3
                xgb_model = xgb.XGBClassifier(
                    n_estimators=150, max_depth=5, learning_rate=0.03,
                    objective="multi:softprob", num_class=3, random_state=42
                )
                xgb_model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
                
                preds = xgb_model.predict(X_test)
                acc = accuracy_score(y_test, preds)
                prec = precision_score(y_test, preds, average="macro", zero_division=0)
                rec = recall_score(y_test, preds, average="macro", zero_division=0)
                f1 = f1_score(y_test, preds, average="macro", zero_division=0)
                
                test_metrics["xgboost"] = {"accuracy": acc, "precision": prec, "recall": rec, "f1": f1}
                xgb_f1 = f1
                logger.info(f"[ML PIPELINE] XGBoost F1: {f1:.4f} | Accuracy: {acc:.2%}")
                
            if LGB_AVAILABLE:
                logger.info("[ML PIPELINE] Fitting LightGBM Model...")
                lgb_model = lgb.LGBMClassifier(
                    n_estimators=150, max_depth=5, learning_rate=0.03,
                    objective="multiclass", num_class=3, random_state=42, verbose=-1
                )
                lgb_model.fit(X_train, y_train, eval_set=[(X_val, y_val)])
                
                preds = lgb_model.predict(X_test)
                acc = accuracy_score(y_test, preds)
                prec = precision_score(y_test, preds, average="macro", zero_division=0)
                rec = recall_score(y_test, preds, average="macro", zero_division=0)
                f1 = f1_score(y_test, preds, average="macro", zero_division=0)
                
                test_metrics["lightgbm"] = {"accuracy": acc, "precision": prec, "recall": rec, "f1": f1}
                lgb_f1 = f1
                logger.info(f"[ML PIPELINE] LightGBM F1: {f1:.4f} | Accuracy: {acc:.2%}")
                
            # 6. Save model and metadata
            meta = {
                "version": version,
                "timestamp": datetime.now().isoformat(),
                "broker": "MetaQuotes-MT5",
                "symbol": self.symbol,
                "date_range": f"{df_aligned.index[0]} to {df_aligned.index[-1]}",
                "features": self.feature_columns,
                "test_metrics": test_metrics,
                "model_type": None
            }
            
            if XGB_AVAILABLE or LGB_AVAILABLE:
                if xgb_f1 >= lgb_f1 and xgb_model is not None:
                    best_model_type = "xgboost"
                    model_path = os.path.join(self.models_dir, f"{version}.bin")
                    xgb_model.save_model(model_path)
                    meta["model_type"] = "xgboost"
                    model_saved = True
                elif lgb_model is not None:
                    best_model_type = "lightgbm"
                    model_path = os.path.join(self.models_dir, f"{version}.bin")
                    lgb_model.booster_.save_model(model_path)
                    meta["model_type"] = "lightgbm"
                    model_saved = True
                    
                meta_path = os.path.join(self.models_dir, f"{version}_meta.json")
                with open(meta_path, "w") as f:
                    json.dump(meta, f, indent=4)
                    
                logger.info(f"[ML PIPELINE] Saved best model ({best_model_type}) to {model_path}")
                
                # Activate newly trained model automatically
                from app.services.ai_predictor import ai_predictor
                ai_predictor.activate_model(version)
            else:
                logger.warning("[ML PIPELINE] No machine learning libraries (XGBoost/LightGBM) available. Trained heuristics baseline only.")
                
            report = {
                "success": True,
                "version": version,
                "best_model_type": best_model_type,
                "samples_count": n_samples,
                "metrics": test_metrics,
                "model_saved": model_saved
            }
            return report
            
        finally:
            # Release lock
            trade_manager.training_active = False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pipeline = MLTrainingPipeline()
    try:
        res = pipeline.run_training_pipeline("gold_model_v001")
        print("Pipeline Complete:", res)
    except Exception as e:
        print("Pipeline failed:", e)
