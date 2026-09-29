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
from sklearn.model_selection import TimeSeriesSplit
from sklearn.utils.class_weight import compute_sample_weight

logger = logging.getLogger("ApexTrader.MLTrainPipeline")

# Deployment Quality Gate Constants
MIN_ACCEPTABLE_F1 = 0.45
MIN_ACCEPTABLE_ACCURACY = 0.40

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

        feat["HTF_Confluence"] = feat["M5_Trend"] + feat["M15_Trend"] + feat["H1_Trend"]

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
        self.base_tf = "M1"
        
        self.feature_columns = [
            "RSI_14", "Stoch_RSI_K", "MACD_Hist_Norm", "ADX_Trend_Strength",
            "Relative_Volume", "EMA_20_50_Spread", "EMA_50_200_Spread", "Dist_to_VWAP",
            "M5_Trend", "M15_Trend", "H1_Trend", "HTF_Confluence"
        ]

    def download_and_validate_data(self, lookback_days: Optional[int] = None) -> Dict[str, pd.DataFrame]:
        """
        Discovers, downloads, and validates historical multi-timeframe candles.
        Returns a dictionary of cleaned DataFrames indexed by timestamp.
        """
        from app.adapters.mt5_adapter import mt5_adapter
        from app.core.config import settings
        
        if lookback_days is None:
            lookback_days = int(getattr(settings, "ML_TRAINING_LOOKBACK_DAYS", int(os.getenv("ML_TRAINING_LOOKBACK_DAYS", 180))))

        logger.info(f"[ML PIPELINE] Fetching historical candle data for {self.symbol} (lookback_days={lookback_days})...")
        
        # Resolve dynamic symbol first
        disc_symbol = mt5_adapter.discover_gold_symbol() or self.symbol
        self.symbol = disc_symbol
        
        tf_candle_counts = {
            "M1": lookback_days * 1440,
            "M5": lookback_days * 288,
            "M15": lookback_days * 96,
            "H1": lookback_days * 24,
            "H4": lookback_days * 6
        }
        
        # Check M1 historical data volume to see if M5 fallback is required
        count_m1 = min(300000, tf_candle_counts["M1"])
        candles_m1 = mt5_adapter.fetch_candles(self.symbol, "M1", count_m1)
        m1_count = len(candles_m1) if candles_m1 else 0
        
        if m1_count < 30000:
            logger.warning(
                f"[ML PIPELINE] M1 history limited ({m1_count} candles < 30,000 minimum). "
                f"Falling back to M5 as base timeframe for ML training."
            )
            self.base_tf = "M5"
            timeframes = ["M5", "M15", "H1", "H4"]
        else:
            self.base_tf = "M1"
            timeframes = ["M1", "M5", "M15", "H1", "H4"]

        data = {}
        for tf in timeframes:
            if tf == "M1" and candles_m1:
                candles = candles_m1
            else:
                count = min(300000, tf_candle_counts[tf])
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
        Time-aligns multi-timeframe candles with zero look-ahead bias.
        Uses self.base_tf ('M1' or 'M5').
        """
        logger.info(f"[ML PIPELINE] Aligning multi-timeframe features causally (base_tf='{self.base_tf}')...")
        base_key = self.base_tf if self.base_tf in dfs else ("M1" if "M1" in dfs else list(dfs.keys())[0])
        df_base = dfs[base_key].copy()
        if "timestamp" in df_base.columns:
            df_base.set_index("timestamp", inplace=True)
            
        # Prepare HTF trend fields
        df_base["M5_Trend"] = 0.0
        df_base["M15_Trend"] = 0.0
        df_base["H1_Trend"] = 0.0
        
        base_times = df_base.index

        if base_key == "M5":
            df_base["M5_Trend"] = (df_base["close"] > df_base.get("ema_20", df_base["close"])).astype(float) * 2.0 - 1.0
        elif "M5" in dfs:
            df_m5 = dfs["M5"].copy()
            if "timestamp" in df_m5.columns:
                df_m5.set_index("timestamp", inplace=True)
            trend_m5 = (df_m5["close"] > df_m5.get("ema_20", df_m5["close"])).astype(float) * 2.0 - 1.0
            m5_times = trend_m5.index
            idx_m5 = np.searchsorted(m5_times, base_times - pd.Timedelta(minutes=5)) - 1
            valid_m5 = idx_m5 >= 0
            df_base.loc[valid_m5, "M5_Trend"] = trend_m5.iloc[idx_m5[valid_m5]].values
            
        if "M15" in dfs:
            df_m15 = dfs["M15"].copy()
            if "timestamp" in df_m15.columns:
                df_m15.set_index("timestamp", inplace=True)
            trend_m15 = (df_m15["close"] > df_m15.get("ema_20", df_m15["close"])).astype(float) * 2.0 - 1.0
            m15_times = trend_m15.index
            idx_m15 = np.searchsorted(m15_times, base_times - pd.Timedelta(minutes=15)) - 1
            valid_m15 = idx_m15 >= 0
            df_base.loc[valid_m15, "M15_Trend"] = trend_m15.iloc[idx_m15[valid_m15]].values

        if "H1" in dfs:
            df_h1 = dfs["H1"].copy()
            if "timestamp" in df_h1.columns:
                df_h1.set_index("timestamp", inplace=True)
            trend_h1 = (df_h1["close"] > df_h1.get("ema_20", df_h1["close"])).astype(float) * 2.0 - 1.0
            h1_times = trend_h1.index
            idx_h1 = np.searchsorted(h1_times, base_times - pd.Timedelta(minutes=60)) - 1
            valid_h1 = idx_h1 >= 0
            df_base.loc[valid_h1, "H1_Trend"] = trend_h1.iloc[idx_h1[valid_h1]].values
            
        # Drop rows with incomplete alignments
        df_aligned = df_base.dropna(subset=["rsi_14", "atr_14"])
        return df_aligned

    def apply_triple_barrier_labeling(self, df_aligned: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Applies Triple Barrier Labeling (TP, SL, Timeout) on historical data.
        Returns:
            X: features matrix
            y: labels (0 = NO_TRADE/LOSS, 1 = BUY_WIN, 2 = SELL_WIN)
        """
        logger.info(f"[ML PIPELINE] Applying Triple Barrier Labeling (base_tf='{self.base_tf}')...")
        
        # Define parameters
        barrier_timeout = 6 if self.base_tf == "M5" else 30
        tp_atr_mult = 2.5
        sl_atr_mult = 1.5
        spread_points = 3.0 # Approx spread offset in gold points ($0.30)
        
        labels = []
        feature_list = []
        
        n_samples = len(df_aligned)
        
        # Pre-extract values for fast numpy loop
        timestamps = df_aligned.index
        closes = df_aligned["close"].values
        highs = df_aligned["high"].values
        lows = df_aligned["low"].values
        atrs = df_aligned["atr_14"].values
        
        # Feature builder
        rsi = (df_aligned["rsi_14"].values if "rsi_14" in df_aligned.columns else np.full(n_samples, 50.0)) / 100.0
        stoch_k = (df_aligned["stoch_rsi_k"].values if "stoch_rsi_k" in df_aligned.columns else (df_aligned["stoch_k"].values if "stoch_k" in df_aligned.columns else np.full(n_samples, 50.0))) / 100.0
        macd = (df_aligned["macd_hist"].values if "macd_hist" in df_aligned.columns else np.zeros(n_samples)) * 100.0
        adx = (df_aligned["adx"].values if "adx" in df_aligned.columns else np.full(n_samples, 20.0)) / 100.0
        rvol = (df_aligned["rvol"].values if "rvol" in df_aligned.columns else np.full(n_samples, 1.0)) / 3.0
        ema20 = df_aligned["ema_20"].values if "ema_20" in df_aligned.columns else closes
        ema50 = df_aligned["ema_50"].values if "ema_50" in df_aligned.columns else closes
        ema200 = df_aligned["ema_200"].values if "ema_200" in df_aligned.columns else closes
        vwap = df_aligned["vwap"].values if "vwap" in df_aligned.columns else closes
        
        m5_trend = df_aligned["M5_Trend"].values
        m15_trend = df_aligned["M15_Trend"].values
        h1_trend = df_aligned["H1_Trend"].values
        
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
                "H1_Trend": h1_trend[i],
                "HTF_Confluence": m5_trend[i] + m15_trend[i] + h1_trend[i]
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
            
            n_samples = len(X)
            if n_samples < 1000:
                msg = f"[ML PIPELINE] Insufficient dataset size for training: found {n_samples} samples, minimum 1,000 required."
                logger.error(msg)
                raise ValueError(msg)
            
            n_splits = 5
            tscv = TimeSeriesSplit(n_splits=n_splits)
            logger.info(f"[ML PIPELINE] Initializing 5-fold chronological Walk-Forward Validation (total_samples={n_samples})")
            
            best_model_type = "heuristics"
            test_metrics = {}
            model_saved = False
            
            xgb_mean_f1 = -1.0
            lgb_mean_f1 = -1.0
            xgb_final_model = None
            lgb_final_model = None
            
            # 5. Walk-Forward Train & Evaluate models
            if XGB_AVAILABLE:
                logger.info(f"[ML PIPELINE] Evaluating XGBoost via {n_splits}-fold Walk-Forward Validation...")
                xgb_fold_metrics = []
                for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(X)):
                    X_tr, y_tr = X.iloc[train_idx], y.iloc[train_idx]
                    X_te, y_te = X.iloc[test_idx], y.iloc[test_idx]
                    
                    val_cutoff = int(len(X_tr) * 0.8)
                    X_tr_inner, y_tr_inner = X_tr.iloc[:val_cutoff], y_tr.iloc[:val_cutoff]
                    X_val_inner, y_val_inner = X_tr.iloc[val_cutoff:], y_tr.iloc[val_cutoff:]
                    
                    sw_fold = compute_sample_weight("balanced", y_tr_inner)
                    m = xgb.XGBClassifier(
                        n_estimators=150, max_depth=5, learning_rate=0.03,
                        objective="multi:softprob", num_class=3, random_state=42
                    )
                    m.fit(X_tr_inner, y_tr_inner, sample_weight=sw_fold, eval_set=[(X_val_inner, y_val_inner)], verbose=False)
                    preds = m.predict(X_te)
                    
                    acc = accuracy_score(y_te, preds)
                    prec = precision_score(y_te, preds, average="macro", zero_division=0)
                    rec = recall_score(y_te, preds, average="macro", zero_division=0)
                    f1 = f1_score(y_te, preds, average="macro", zero_division=0)
                    xgb_fold_metrics.append({"accuracy": acc, "precision": prec, "recall": rec, "f1": f1})

                xgb_mean_acc = float(np.mean([m["accuracy"] for m in xgb_fold_metrics]))
                xgb_mean_prec = float(np.mean([m["precision"] for m in xgb_fold_metrics]))
                xgb_mean_rec = float(np.mean([m["recall"] for m in xgb_fold_metrics]))
                xgb_mean_f1 = float(np.mean([m["f1"] for m in xgb_fold_metrics]))
                
                test_metrics["xgboost"] = {
                    "mean_accuracy": xgb_mean_acc,
                    "mean_precision": xgb_mean_prec,
                    "mean_recall": xgb_mean_rec,
                    "mean_f1": xgb_mean_f1,
                    "accuracy": xgb_mean_acc,
                    "f1": xgb_mean_f1,
                    "folds": xgb_fold_metrics
                }
                logger.info(f"[ML PIPELINE] XGBoost Walk-Forward Mean F1: {xgb_mean_f1:.4f} | Mean Accuracy: {xgb_mean_acc:.2%}")

                logger.info("[ML PIPELINE] Fitting final XGBoost model on full dataset...")
                sw_full = compute_sample_weight("balanced", y)
                xgb_final_model = xgb.XGBClassifier(
                    n_estimators=150, max_depth=5, learning_rate=0.03,
                    objective="multi:softprob", num_class=3, random_state=42
                )
                xgb_final_model.fit(X, y, sample_weight=sw_full, verbose=False)

            if LGB_AVAILABLE:
                logger.info(f"[ML PIPELINE] Evaluating LightGBM via {n_splits}-fold Walk-Forward Validation...")
                lgb_fold_metrics = []
                for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(X)):
                    X_tr, y_tr = X.iloc[train_idx], y.iloc[train_idx]
                    X_te, y_te = X.iloc[test_idx], y.iloc[test_idx]
                    
                    val_cutoff = int(len(X_tr) * 0.8)
                    X_tr_inner, y_tr_inner = X_tr.iloc[:val_cutoff], y_tr.iloc[:val_cutoff]
                    X_val_inner, y_val_inner = X_tr.iloc[val_cutoff:], y_tr.iloc[val_cutoff:]
                    
                    sw_fold = compute_sample_weight("balanced", y_tr_inner)
                    m = lgb.LGBMClassifier(
                        n_estimators=150, max_depth=5, learning_rate=0.03,
                        objective="multiclass", num_class=3, random_state=42, verbose=-1
                    )
                    m.fit(X_tr_inner, y_tr_inner, sample_weight=sw_fold, eval_set=[(X_val_inner, y_val_inner)])
                    preds = m.predict(X_te)
                    
                    acc = accuracy_score(y_te, preds)
                    prec = precision_score(y_te, preds, average="macro", zero_division=0)
                    rec = recall_score(y_te, preds, average="macro", zero_division=0)
                    f1 = f1_score(y_te, preds, average="macro", zero_division=0)
                    lgb_fold_metrics.append({"accuracy": acc, "precision": prec, "recall": rec, "f1": f1})

                lgb_mean_acc = float(np.mean([m["accuracy"] for m in lgb_fold_metrics]))
                lgb_mean_prec = float(np.mean([m["precision"] for m in lgb_fold_metrics]))
                lgb_mean_rec = float(np.mean([m["recall"] for m in lgb_fold_metrics]))
                lgb_mean_f1 = float(np.mean([m["f1"] for m in lgb_fold_metrics]))
                
                test_metrics["lightgbm"] = {
                    "mean_accuracy": lgb_mean_acc,
                    "mean_precision": lgb_mean_prec,
                    "mean_recall": lgb_mean_rec,
                    "mean_f1": lgb_mean_f1,
                    "accuracy": lgb_mean_acc,
                    "f1": lgb_mean_f1,
                    "folds": lgb_fold_metrics
                }
                logger.info(f"[ML PIPELINE] LightGBM Walk-Forward Mean F1: {lgb_mean_f1:.4f} | Mean Accuracy: {lgb_mean_acc:.2%}")

                logger.info("[ML PIPELINE] Fitting final LightGBM model on full dataset...")
                sw_full = compute_sample_weight("balanced", y)
                lgb_final_model = lgb.LGBMClassifier(
                    n_estimators=150, max_depth=5, learning_rate=0.03,
                    objective="multiclass", num_class=3, random_state=42, verbose=-1
                )
                lgb_final_model.fit(X, y, sample_weight=sw_full)
                
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
                if xgb_mean_f1 >= lgb_mean_f1 and xgb_final_model is not None:
                    best_model_type = "xgboost"
                    model_path = os.path.join(self.models_dir, f"{version}.bin")
                    xgb_final_model.save_model(model_path)
                    meta["model_type"] = "xgboost"
                    model_saved = True
                elif lgb_final_model is not None:
                    best_model_type = "lightgbm"
                    model_path = os.path.join(self.models_dir, f"{version}.bin")
                    lgb_final_model.booster_.save_model(model_path)
                    meta["model_type"] = "lightgbm"
                    model_saved = True
                    
                meta_path = os.path.join(self.models_dir, f"{version}_meta.json")
                with open(meta_path, "w") as f:
                    json.dump(meta, f, indent=4)
                    
                logger.info(f"[ML PIPELINE] Saved best model ({best_model_type}) to {model_path}")
                
                # Check deployment quality gate before auto-activating newly trained model
                model_activated = False
                best_metrics = test_metrics.get(best_model_type, {})
                best_f1 = float(best_metrics.get("mean_f1", best_metrics.get("f1", 0.0)))
                best_acc = float(best_metrics.get("mean_accuracy", best_metrics.get("accuracy", 0.0)))

                if best_f1 >= MIN_ACCEPTABLE_F1 and best_acc >= MIN_ACCEPTABLE_ACCURACY:
                    from app.services.ai_predictor import ai_predictor
                    model_activated = ai_predictor.activate_model(version)
                else:
                    logger.warning(
                        f"[ML PIPELINE] Quality Gate FAILED for model '{version}' ({best_model_type}): "
                        f"F1={best_f1:.4f} (min {MIN_ACCEPTABLE_F1}), Accuracy={best_acc:.4f} (min {MIN_ACCEPTABLE_ACCURACY}). "
                        f"Model saved to disk but NOT activated."
                    )
            else:
                logger.warning("[ML PIPELINE] No machine learning libraries (XGBoost/LightGBM) available. Trained heuristics baseline only.")
                model_activated = False
                
            report = {
                "success": True,
                "version": version,
                "best_model_type": best_model_type,
                "samples_count": n_samples,
                "metrics": test_metrics,
                "model_saved": model_saved,
                "model_activated": model_activated
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
