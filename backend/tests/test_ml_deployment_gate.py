import sys
import os
import json
import tempfile
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.ai_predictor import AIPredictor, MIN_ACCEPTABLE_F1, MIN_ACCEPTABLE_ACCURACY
from ml.train_pipeline import MLTrainingPipeline
from sklearn.utils.class_weight import compute_sample_weight

@pytest.fixture
def setup_temp_models_dir(tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    return models_dir

def test_activate_model_rejects_subpar_metrics(setup_temp_models_dir):
    predictor = AIPredictor()
    predictor.models_dir = str(setup_temp_models_dir)
    predictor.model = "dummy_model"
    predictor.active_model_version = "old_version"

    # Create dummy failing meta and bin files
    version = "bad_model"
    meta_path = setup_temp_models_dir / f"{version}_meta.json"
    bin_path = setup_temp_models_dir / f"{version}.bin"
    bin_path.write_bytes(b"dummy_binary_content")

    meta_content = {
        "version": version,
        "model_type": "xgboost",
        "features": ["feature_1", "feature_2"],
        "test_metrics": {
            "xgboost": {
                "accuracy": 0.303,
                "f1": 0.176,
                "precision": 0.20,
                "recall": 0.25
            }
        }
    }
    meta_path.write_text(json.dumps(meta_content))

    result = predictor.activate_model(version)
    assert result is False
    assert predictor.model is None
    assert predictor.active_model_version is None
    assert predictor.feature_columns == []

def test_activate_model_accepts_good_metrics(setup_temp_models_dir):
    predictor = AIPredictor()
    predictor.models_dir = str(setup_temp_models_dir)
    predictor.model = None

    version = "good_model"
    meta_path = setup_temp_models_dir / f"{version}_meta.json"
    bin_path = setup_temp_models_dir / f"{version}.bin"
    bin_path.write_bytes(b"dummy_binary_content")

    meta_content = {
        "version": version,
        "model_type": "xgboost",
        "features": ["feature_1", "feature_2"],
        "test_metrics": {
            "xgboost": {
                "accuracy": 0.52,
                "f1": 0.48,
                "precision": 0.50,
                "recall": 0.47
            }
        }
    }
    meta_path.write_text(json.dumps(meta_content))

    with patch("xgboost.XGBClassifier") as MockXGB:
        mock_xgb = MagicMock()
        MockXGB.return_value = mock_xgb
        with patch("app.services.trade_learner.trade_learner.set_metadata"):
            with patch("app.services.trade_manager.trade_manager.update_risk_settings"):
                result = predictor.activate_model(version)

    assert result is True
    assert predictor.active_model_version == version
    assert predictor.feature_columns == ["feature_1", "feature_2"]
    assert predictor.model is not None

def test_activate_model_force_override_bypasses_quality_gate(setup_temp_models_dir):
    predictor = AIPredictor()
    predictor.models_dir = str(setup_temp_models_dir)
    predictor.model = None

    version = "subpar_model"
    meta_path = setup_temp_models_dir / f"{version}_meta.json"
    bin_path = setup_temp_models_dir / f"{version}.bin"
    bin_path.write_bytes(b"dummy_binary_content")

    meta_content = {
        "version": version,
        "model_type": "xgboost",
        "features": ["feature_1"],
        "test_metrics": {
            "xgboost": {
                "accuracy": 0.32,
                "f1": 0.30
            }
        }
    }
    meta_path.write_text(json.dumps(meta_content))

    # Without force -> Rejection
    res_normal = predictor.activate_model(version, force=False)
    assert res_normal is False
    assert "Quality gate REJECTED" in predictor.last_error_reason

    # With force -> Success
    with patch("xgboost.XGBClassifier") as MockXGB:
        mock_xgb = MagicMock()
        MockXGB.return_value = mock_xgb
        with patch("app.services.trade_learner.trade_learner.set_metadata"):
            with patch("app.services.trade_manager.trade_manager.update_risk_settings"):
                res_force = predictor.activate_model(version, force=True)

    assert res_force is True
    assert predictor.active_model_version == version

def test_load_active_model_returns_false_when_no_valid_models(setup_temp_models_dir):
    predictor = AIPredictor()
    predictor.models_dir = str(setup_temp_models_dir)
    predictor.model = None

    # Create bad model on disk
    version = "bad_model_v1"
    meta_path = setup_temp_models_dir / f"{version}_meta.json"
    bin_path = setup_temp_models_dir / f"{version}.bin"
    bin_path.write_bytes(b"dummy")
    meta_path.write_text(json.dumps({
        "version": version,
        "timestamp": "2026-09-05T10:00:00",
        "model_type": "xgboost",
        "test_metrics": {"xgboost": {"accuracy": 0.30, "f1": 0.15}}
    }))

    with patch("app.services.trade_learner.trade_learner.get_metadata", return_value=None):
        activated = predictor.load_active_model()

    assert activated is False
    assert predictor.model is None

def test_predict_returns_default_when_no_model():
    predictor = AIPredictor()
    predictor.model = None
    predictor.feature_columns = []
    
    p_buy, p_sell, p_no_trade, status = predictor.predict("XAUUSD", MagicMock())
    assert (p_buy, p_sell, p_no_trade) == (0.33, 0.33, 0.33)
    assert "No active ML model" in status

def test_pipeline_rejects_insufficient_sample_size_below_1000(setup_temp_models_dir):
    import pandas as pd
    import numpy as np

    pipeline = MLTrainingPipeline(symbol="XAUUSD")
    pipeline.models_dir = str(setup_temp_models_dir)

    dummy_df = pd.DataFrame({"close": np.random.randn(500)}, index=pd.date_range("2026-01-01", periods=500, freq="min"))
    dummy_X = pd.DataFrame({"f1": np.random.randn(500), "f2": np.random.randn(500)})
    dummy_y = pd.Series(np.random.choice([0, 1, 2], size=500))

    with patch.object(pipeline, "download_and_validate_data", return_value={}):
        with patch.object(pipeline, "align_data_causally", return_value=dummy_df):
            with patch.object(pipeline, "apply_triple_barrier_labeling", return_value=(dummy_X, dummy_y)):
                with pytest.raises(ValueError, match="minimum 1,000 required"):
                    pipeline.run_training_pipeline("test_tiny_sample_v1")

def test_pipeline_gate_blocks_subpar_model_activation(setup_temp_models_dir):
    import pandas as pd
    import numpy as np

    pipeline = MLTrainingPipeline(symbol="XAUUSD")
    pipeline.models_dir = str(setup_temp_models_dir)

    dummy_df = pd.DataFrame({"close": np.random.randn(1050)}, index=pd.date_range("2026-01-01", periods=1050, freq="min"))
    dummy_X = pd.DataFrame({"f1": np.random.randn(1050), "f2": np.random.randn(1050)})
    dummy_y = pd.Series(np.random.choice([0, 1, 2], size=1050))

    with patch.object(pipeline, "download_and_validate_data", return_value={}):
        with patch.object(pipeline, "align_data_causally", return_value=dummy_df):
            with patch.object(pipeline, "apply_triple_barrier_labeling", return_value=(dummy_X, dummy_y)):
                with patch("xgboost.XGBClassifier") as MockXGB:
                    mock_xgb = MagicMock()
                    mock_xgb.predict.side_effect = lambda X_in: np.zeros(len(X_in), dtype=int)
                    MockXGB.return_value = mock_xgb
                    # Mock low metrics (F1 0.30 < 0.45)
                    with patch("ml.train_pipeline.f1_score", return_value=0.30):
                        with patch("ml.train_pipeline.accuracy_score", return_value=0.35):
                            with patch("app.services.trade_manager.trade_manager"):
                                report = pipeline.run_training_pipeline("test_version_v1")

    assert report["success"] is True
    assert report["model_saved"] is True
    assert report["model_activated"] is False

def test_download_data_defaults_to_180_days_and_m1_base():
    from app.domain.models import Candle
    pipeline = MLTrainingPipeline(symbol="XAUUSD")
    
    # Create 35,000 dummy candles for M1 (>= 30k)
    dummy_candles = [
        Candle(timestamp="2026-01-01 00:00", open=2000.0, high=2001.0, low=1999.0, close=2000.5, volume=10.0, is_closed=True)
    ] * 35000

    with patch("app.adapters.mt5_adapter.mt5_adapter.discover_gold_symbol", return_value="XAUUSD"):
        with patch("app.adapters.mt5_adapter.mt5_adapter.fetch_candles", return_value=dummy_candles) as mock_fetch:
            with patch("app.services.indicators.IndicatorService.calculate_all_indicators") as mock_ind:
                import pandas as pd
                mock_df = pd.DataFrame({"close": [2000.0] * 35000, "rsi_14": [50.0] * 35000, "atr_14": [1.0] * 35000})
                mock_ind.return_value = mock_df
                
                data = pipeline.download_and_validate_data(lookback_days=180)

    assert pipeline.base_tf == "M1"
    assert "M1" in data
    assert mock_fetch.call_args_list[0][0][2] == 180 * 1440

def test_download_data_m5_fallback_when_m1_limited():
    from app.domain.models import Candle
    pipeline = MLTrainingPipeline(symbol="XAUUSD")
    
    # Create only 5,000 dummy candles for M1 (< 30k limit)
    dummy_m1 = [
        Candle(timestamp="2026-01-01 00:00", open=2000.0, high=2001.0, low=1999.0, close=2000.5, volume=10.0, is_closed=True)
    ] * 5000
    dummy_m5 = [
        Candle(timestamp="2026-01-01 00:00", open=2000.0, high=2001.0, low=1999.0, close=2000.5, volume=10.0, is_closed=True)
    ] * 10000

    def mock_fetch_candles(symbol, tf, count):
        if tf == "M1":
            return dummy_m1
        return dummy_m5

    with patch("app.adapters.mt5_adapter.mt5_adapter.discover_gold_symbol", return_value="XAUUSD"):
        with patch("app.adapters.mt5_adapter.mt5_adapter.fetch_candles", side_effect=mock_fetch_candles):
            with patch("app.services.indicators.IndicatorService.calculate_all_indicators") as mock_ind:
                import pandas as pd
                mock_df = pd.DataFrame({"close": [2000.0] * 10000, "rsi_14": [50.0] * 10000, "atr_14": [1.0] * 10000})
                mock_ind.return_value = mock_df
                
                data = pipeline.download_and_validate_data(lookback_days=180)

    assert pipeline.base_tf == "M5"
    assert "M5" in data
    assert "M1" not in data

def test_triple_barrier_labeling_timeout_scaled_for_m5():
    import pandas as pd
    import numpy as np
    pipeline = MLTrainingPipeline(symbol="XAUUSD")
    pipeline.base_tf = "M5"
    
    dates = pd.date_range("2026-01-01", periods=100, freq="5min")
    df_aligned = pd.DataFrame({
        "close": np.full(100, 2000.0),
        "high": np.full(100, 2001.0),
        "low": np.full(100, 1999.0),
        "atr_14": np.full(100, 2.0),
        "rsi_14": np.full(100, 50.0),
        "M5_Trend": np.full(100, 1.0),
        "M15_Trend": np.full(100, 1.0),
        "H1_Trend": np.full(100, 1.0)
    }, index=dates)

    X, y = pipeline.apply_triple_barrier_labeling(df_aligned)
    # 100 - 6 (barrier_timeout for M5) = 94 samples
    assert len(X) == 94
    assert len(y) == 94

def test_walk_forward_validation_computes_fold_metrics(setup_temp_models_dir):
    import pandas as pd
    import numpy as np

    pipeline = MLTrainingPipeline(symbol="XAUUSD")
    pipeline.models_dir = str(setup_temp_models_dir)

    dates = pd.date_range("2026-01-01", periods=1050, freq="1min")
    dummy_df = pd.DataFrame({"close": np.full(1050, 2000.0)}, index=dates)
    dummy_X = pd.DataFrame({"f1": np.random.randn(1050), "f2": np.random.randn(1050)})
    dummy_y = pd.Series(np.random.choice([0, 1, 2], size=1050))

    with patch.object(pipeline, "download_and_validate_data", return_value={}):
        with patch.object(pipeline, "align_data_causally", return_value=dummy_df):
            with patch.object(pipeline, "apply_triple_barrier_labeling", return_value=(dummy_X, dummy_y)):
                with patch("xgboost.XGBClassifier") as MockXGB:
                    mock_xgb = MagicMock()
                    mock_xgb.predict.side_effect = lambda X_in: np.zeros(len(X_in), dtype=int)
                    MockXGB.return_value = mock_xgb
                    with patch("app.services.trade_manager.trade_manager"):
                        report = pipeline.run_training_pipeline("test_wf_v1")

    assert report["success"] is True
    assert "xgboost" in report["metrics"]
    metrics = report["metrics"]["xgboost"]
    assert "mean_f1" in metrics
    assert "mean_accuracy" in metrics
    assert "folds" in metrics
    assert len(metrics["folds"]) == 5

def test_sample_weight_computation_passed_during_fit(setup_temp_models_dir):
    import pandas as pd
    import numpy as np

    pipeline = MLTrainingPipeline(symbol="XAUUSD")
    pipeline.models_dir = str(setup_temp_models_dir)

    dates = pd.date_range("2026-01-01", periods=1050, freq="1min")
    dummy_df = pd.DataFrame({"close": np.full(1050, 2000.0)}, index=dates)
    dummy_X = pd.DataFrame({"f1": np.random.randn(1050), "f2": np.random.randn(1050)})
    dummy_y = pd.Series(np.random.choice([0, 1, 2], size=1050))

    with patch.object(pipeline, "download_and_validate_data", return_value={}):
        with patch.object(pipeline, "align_data_causally", return_value=dummy_df):
            with patch.object(pipeline, "apply_triple_barrier_labeling", return_value=(dummy_X, dummy_y)):
                with patch("xgboost.XGBClassifier") as MockXGB:
                    mock_xgb = MagicMock()
                    mock_xgb.predict.side_effect = lambda X_in: np.zeros(len(X_in), dtype=int)
                    MockXGB.return_value = mock_xgb
                    with patch("ml.train_pipeline.compute_sample_weight", wraps=compute_sample_weight) as mock_sw:
                        with patch("app.services.trade_manager.trade_manager"):
                            report = pipeline.run_training_pipeline("test_sw_v1")

    assert report["success"] is True
    assert mock_sw.called
    assert mock_sw.call_args[0][0] == "balanced"

def test_confidence_gated_veto_logic_agreement_and_disagreement():
    from app.services.decision_engine import decision_engine
    from app.domain.models import Candle, TickerData
    import pandas as pd
    import numpy as np

    dates = pd.date_range("2026-01-01", periods=50, freq="15min")
    dummy_df = pd.DataFrame({
        "timestamp": dates,
        "open": np.full(50, 2000.0),
        "high": np.full(50, 2005.0),
        "low": np.full(50, 1995.0),
        "close": np.full(50, 2002.0),
        "volume": np.full(50, 100.0),
        "rsi_14": np.full(50, 55.0),
        "atr_14": np.full(50, 2.0),
        "ema_20": np.full(50, 1998.0),
        "ema_50": np.full(50, 1990.0),
        "ema_200": np.full(50, 1980.0)
    }, index=dates)

    from datetime import datetime
    candles = [
        Candle(timestamp=dates[i].to_pydatetime(), open=2000.0, high=2005.0, low=1995.0, close=2002.0, volume=100.0, is_closed=True)
        for i in range(50)
    ]
    ticker = TickerData(symbol="XAUUSD", bid=2000.0, ask=2000.2, spread_pips=2.0, timestamp=datetime.now(), digits=2, point_size=0.01)

    # 1. Test Neutral/No Veto (Margin < 0.15 or ML conf < 0.45)
    with patch("app.services.decision_engine.ai_predictor") as mock_predictor:
        mock_predictor.model = "dummy_loaded_model"
        mock_predictor.predict.return_value = (0.40, 0.40, 0.20, "OK")
        rep_neutral = decision_engine.evaluate_trade_opportunity("XAUUSD", candles=candles, ticker=ticker, precomputed_df=dummy_df)

    # 2. Test Confident Disagreement Veto (Rule Engine BUY, ML SELL 85%)
    # Margin = |0.05 - 0.85| = 0.80 >= 0.15
    # Expected Penalty = 0.15 + (0.80 * 0.20) = 0.31
    with patch("app.services.decision_engine.ai_predictor") as mock_predictor:
        mock_predictor.model = "dummy_loaded_model"
        mock_predictor.predict.return_value = (0.05, 0.85, 0.10, "OK")
        rep_veto = decision_engine.evaluate_trade_opportunity("XAUUSD", candles=candles, ticker=ticker, precomputed_df=dummy_df)

    # Confirm Veto penalty of 0.31 was applied cleanly
    assert rep_neutral.long_score > rep_veto.long_score
    assert rep_neutral.long_score - rep_veto.long_score == pytest.approx(0.31)

def test_live_ml_performance_tracking_and_degradation_alert(tmp_path):
    from app.services.trade_learner import TradeLearnerService
    db_file = str(tmp_path / "test_memory.db")
    learner = TradeLearnerService(db_path=db_file)

    # Log 10 trades where ML predicted BUY but trades ended up as LOSS
    for i in range(10):
        learner.log_trade_execution(
            trade_id=f"T_{i}",
            symbol="XAUUSD",
            side="BUY",
            timeframe="M15",
            entry_price=2000.0,
            lots=0.01,
            sl_price=1990.0,
            tp_price=2020.0,
            ml_pred_side="BUY",
            ml_confidence=0.80
        )
        learner.update_trade_outcome(
            trade_id=f"T_{i}",
            exit_price=1990.0,
            pnl_usd=-10.0,
            pnl_pips=-100.0,
            exit_reason="SL_HIT"
        )

    stats = learner.evaluate_live_ml_performance(rolling_window=30)
    assert stats["sample_count"] == 10
    assert stats["degraded"] is True
    assert stats["f1"] < 0.40





