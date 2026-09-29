import os
import json
import logging
from typing import Dict, Any, Tuple, Optional
import numpy as np

logger = logging.getLogger("ApexTrader.AIPredictor")

# Deployment Quality Gate Constants
MIN_ACCEPTABLE_F1 = 0.45
MIN_ACCEPTABLE_ACCURACY = 0.40

class AIPredictor:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(AIPredictor, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance
        
    def __init__(self):
        if self._initialized:
            return
        self.models_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ml", "models"))
        os.makedirs(self.models_dir, exist_ok=True)
        self.active_model_version: Optional[str] = None
        self.model: Any = None
        self.feature_columns: list = []
        self.last_error_reason: str = ""
        self._initialized = True
        self.load_active_model()
        
    def load_active_model(self) -> bool:
        """
        Loads the currently active model from SQLite or metadata.
        """
        try:
            # Query SQLite for the active model version
            from app.services.trade_learner import trade_learner
            active_version = trade_learner.get_metadata("active_model_version")
            if not active_version:
                # Try finding the latest version in the models directory
                versions = self.list_available_models()
                if versions:
                    active_version = versions[0]["version"]
                    
            if active_version:
                return self.activate_model(active_version)
        except Exception as e:
            logger.error(f"[AI PREDICTOR] Error in load_active_model: {e}")
        return False
        
    def list_available_models(self) -> list:
        """
        Scans models directory for saved model versions.
        """
        if not os.path.exists(self.models_dir):
            return []
        
        models = []
        for f in os.listdir(self.models_dir):
            if f.endswith("_meta.json"):
                version = f.replace("_meta.json", "")
                meta_path = os.path.join(self.models_dir, f)
                try:
                    with open(meta_path, "r") as meta_f:
                        meta = json.load(meta_f)
                    models.append({
                        "version": version,
                        "timestamp": meta.get("timestamp"),
                        "broker": meta.get("broker"),
                        "date_range": meta.get("date_range"),
                        "features_count": len(meta.get("features", [])),
                        "test_metrics": meta.get("test_metrics", {}),
                        "walk_forward_metrics": meta.get("walk_forward_metrics", {})
                    })
                except Exception as e:
                    logger.error(f"[AI PREDICTOR] Error loading meta for {version}: {e}")
                    
        # Sort by timestamp desc
        models.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return models
        
    def activate_model(self, version: str, force: bool = False) -> bool:
        """
        Loads the model file and metadata for a specific version.
        If force=True, bypasses Quality Gate Check metrics threshold.
        """
        self.last_error_reason = ""
        meta_path = os.path.join(self.models_dir, f"{version}_meta.json")
        model_path = os.path.join(self.models_dir, f"{version}.bin")
        
        if not os.path.exists(meta_path) or not os.path.exists(model_path):
            self.last_error_reason = f"Model files for version '{version}' do not exist on server."
            logger.warning(f"[AI PREDICTOR] {self.last_error_reason}")
            self.model = None
            self.active_model_version = None
            self.feature_columns = []
            return False
            
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)

            # Quality Gate Check
            model_type = meta.get("model_type", "xgboost")
            metrics = meta.get("walk_forward_metrics", meta.get("test_metrics", {}))
            type_metrics = metrics.get(model_type, metrics) if isinstance(metrics, dict) else {}
            f1 = float(type_metrics.get("mean_f1", type_metrics.get("f1", 0.0)))
            acc = float(type_metrics.get("mean_accuracy", type_metrics.get("accuracy", 0.0)))

            if (f1 < MIN_ACCEPTABLE_F1 or acc < MIN_ACCEPTABLE_ACCURACY) and not force:
                self.last_error_reason = (
                    f"Quality gate REJECTED model '{version}': "
                    f"F1={f1:.4f} (min {MIN_ACCEPTABLE_F1}), Accuracy={acc:.4f} (min {MIN_ACCEPTABLE_ACCURACY})."
                )
                logger.warning(f"[AI PREDICTOR] {self.last_error_reason}")
                self.model = None
                self.active_model_version = None
                self.feature_columns = []
                return False

            if force and (f1 < MIN_ACCEPTABLE_F1 or acc < MIN_ACCEPTABLE_ACCURACY):
                logger.warning(
                    f"[AI PREDICTOR] Quality gate BYPASSED for model '{version}' (force=True): "
                    f"F1={f1:.4f}, Accuracy={acc:.4f}."
                )

            self.feature_columns = meta.get("features", [])
            
            if model_type == "xgboost":
                import xgboost as xgb
                self.model = xgb.XGBClassifier()
                self.model.load_model(model_path)
            elif model_type == "lightgbm":
                import lightgbm as lgb
                self.model = lgb.Booster(model_file=model_path)
            else:
                self.last_error_reason = f"Unsupported model type: {model_type}"
                logger.error(f"[AI PREDICTOR] {self.last_error_reason}")
                return False
                
            self.active_model_version = version
            
            # Save active version in SQLite and Risk Settings
            from app.services.trade_learner import trade_learner
            trade_learner.set_metadata("active_model_version", version)

            try:
                from app.services.trade_manager import trade_manager
                trade_manager.update_risk_settings({"active_model_version": version})
            except Exception as e:
                logger.warning(f"[AI PREDICTOR] Could not update risk_settings active_model_version: {e}")
            
            logger.info(f"[AI PREDICTOR] Successfully activated model version: {version} ({model_type})")
            return True
        except Exception as e:
            self.last_error_reason = f"Failed to activate model version {version}: {str(e)}"
            logger.error(f"[AI PREDICTOR] {self.last_error_reason}", exc_info=True)
            self.model = None
            self.feature_columns = []
            return False
            
    def predict(self, symbol: str, df: Any, htf_candles: Optional[Dict[str, Any]] = None) -> Tuple[float, float, float, str]:
        """
        Predicts probabilities for BUY (1), SELL (2), and NO_TRADE (0) using the active model.
        Returns: Tuple[p_buy, p_sell, p_no_trade, status_text]
        """
        if self.model is None or not self.feature_columns:
            return 0.33, 0.33, 0.33, "No active ML model (Heuristics Only)"
            
        try:
            # 1. Generate features for the latest row in df
            from ml.train_pipeline import FeatureBuilder
            latest_features = FeatureBuilder.generate_prediction_features(df, htf_candles, self.feature_columns)
            
            if not latest_features:
                return 0.33, 0.33, 0.33, "Feature mismatch / generation failure"
                
            # Convert to correct shape
            X = np.array([latest_features[col] for col in self.feature_columns]).reshape(1, -1)
            
            # Run prediction
            if hasattr(self.model, "predict_proba"):
                probs = self.model.predict_proba(X)[0] # XGBoost returns array of probabilities
            else:
                # LightGBM booster uses predict directly
                probs = self.model.predict(X)[0]
                
            # Mapping target labels:
            # Assuming label classes in train_pipeline are:
            # 0: NO_TRADE
            # 1: BUY_WIN
            # 2: SELL_WIN
            if len(probs) == 3:
                p_no_trade = float(probs[0])
                p_buy = float(probs[1])
                p_sell = float(probs[2])
            elif len(probs) == 2:
                # Binary fallback just in case
                p_no_trade = float(probs[0])
                p_buy = float(probs[1])
                p_sell = 0.0
            else:
                p_no_trade, p_buy, p_sell = 0.33, 0.33, 0.33
                
            status_text = f"Model: {self.active_model_version}"
            return p_buy, p_sell, p_no_trade, status_text
            
        except Exception as e:
            logger.error(f"[AI PREDICTOR] Prediction error: {e}", exc_info=True)
            return 0.33, 0.33, 0.33, f"Prediction error: {str(e)}"

ai_predictor = AIPredictor()
