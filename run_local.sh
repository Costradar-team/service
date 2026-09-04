#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-}"
SKIP_PROFILING=0
RUN_TESTS=0
TRAIN_MODEL=0
ESTIMATOR="gradient-boosting"
FORECAST_HORIZON=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      PYTHON="$2"
      shift 2
      ;;
    --skip-profiling)
      SKIP_PROFILING=1
      shift
      ;;
    --run-tests)
      RUN_TESTS=1
      shift
      ;;
    --train-model)
      TRAIN_MODEL=1
      shift
      ;;
    --estimator)
      ESTIMATOR="$2"
      shift 2
      ;;
    --forecast-horizon)
      FORECAST_HORIZON="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: ./run_local.sh [options]"
      echo "  --python <path>           Python executable path"
      echo "  --skip-profiling          Skip data profiling step"
      echo "  --run-tests               Run unit test suite"
      echo "  --train-model             Train/retrain ML models"
      echo "  --estimator <name>        gradient-boosting (default) or lightgbm"
      echo "  --forecast-horizon <int>  Forecast steps (default: 1)"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

if [[ -z "$PYTHON" ]]; then
  if [[ -f "$REPO_ROOT/../.venv/bin/python" ]]; then
    PYTHON="$REPO_ROOT/../.venv/bin/python"
  elif [[ -f "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

RAW_DATA_ROOT="$REPO_ROOT/data-pipeline/data/raw"
KCA_RAW_DATA="$RAW_DATA_ROOT/kca"
if [[ ! -d "$KCA_RAW_DATA" ]]; then
  KCA_RAW_DATA="$RAW_DATA_ROOT"
fi

ARTIFACT_ROOT="$REPO_ROOT/artifacts"
QUALITY_DIR="$ARTIFACT_ROOT/data-quality"
PROCESSED_DIR="$ARTIFACT_ROOT/processed"
TRANSFORM_REPORT_DIR="$QUALITY_DIR/transform"
ML_DIR="$ARTIFACT_ROOT/ml"
PRODUCT_ML_DIR="$ML_DIR/product"
BRAND_ML_DIR="$ML_DIR/brand"
STORE_ML_DIR="$ML_DIR/store"
FIS_PROCESSED_DIR="$REPO_ROOT/data-pipeline/data/processed/fis"
KAMIS_PROCESSED_DIR="$REPO_ROOT/data-pipeline/data/processed/kamis"

mkdir -p "$QUALITY_DIR" "$PROCESSED_DIR" "$TRANSFORM_REPORT_DIR" "$ML_DIR" "$PRODUCT_ML_DIR" "$BRAND_ML_DIR" "$STORE_ML_DIR"

invoke_step() {
  local name="$1"
  shift
  echo ""
  echo "[$name]"
  "$PYTHON" "$@"
}

if [[ "$SKIP_PROFILING" -eq 0 ]]; then
  invoke_step "1/7 Data profiling" \
    "$REPO_ROOT/data-pipeline/scripts/profile/profile_kca.py" \
    "$KCA_RAW_DATA" \
    --output "$QUALITY_DIR/profiling_summary.json"
else
  echo ""
  echo "[1/7 Data profiling skipped]"
fi

invoke_step "2/7 Data transform" \
  "$REPO_ROOT/data-pipeline/scripts/transform/transform_kca.py" \
  "$KCA_RAW_DATA" \
  --output-dir "$PROCESSED_DIR" \
  --report-dir "$TRANSFORM_REPORT_DIR"

BUILD_ARGS=(
  "$REPO_ROOT/ml/scripts/build_model_dataset.py"
  --input "$PROCESSED_DIR/kca_prices_processed.csv"
  --output-dir "$ML_DIR"
)

if [[ -f "$FIS_PROCESSED_DIR/fis_item.csv" && -f "$FIS_PROCESSED_DIR/fis_price_observation.csv" ]]; then
  BUILD_ARGS+=(--fis-dir "$FIS_PROCESSED_DIR")
fi

if [[ -f "$KAMIS_PROCESSED_DIR/kamis_item.csv" && -f "$KAMIS_PROCESSED_DIR/kamis_price_observation.csv" ]]; then
  BUILD_ARGS+=(--kamis-dir "$KAMIS_PROCESSED_DIR")
fi

invoke_step "3/7 Unit-price normalization and external-feature dataset build" "${BUILD_ARGS[@]}"

invoke_step "4/7 Subtype baseline evaluation" \
  "$REPO_ROOT/ml/scripts/evaluate_baselines.py" \
  --input "$ML_DIR/model_dataset.csv" \
  --output-dir "$ML_DIR"

invoke_step "5/7 Product baseline evaluation" \
  "$REPO_ROOT/ml/scripts/evaluate_baselines.py" \
  --input "$PRODUCT_ML_DIR/model_dataset.csv" \
  --output-dir "$PRODUCT_ML_DIR" \
  --series-level "product"

invoke_step "6/7 Brand baseline evaluation" \
  "$REPO_ROOT/ml/scripts/evaluate_baselines.py" \
  --input "$BRAND_ML_DIR/model_dataset.csv" \
  --output-dir "$BRAND_ML_DIR" \
  --series-level "brand"

invoke_step "7/7 Store baseline evaluation" \
  "$REPO_ROOT/ml/scripts/evaluate_baselines.py" \
  --input "$STORE_ML_DIR/model_dataset.csv" \
  --output-dir "$STORE_ML_DIR" \
  --series-level "store"

if [[ "$TRAIN_MODEL" -eq 1 ]]; then
  invoke_step "Subtype price model training" \
    "$REPO_ROOT/ml/scripts/train_price_model.py" \
    --input "$ML_DIR/model_dataset.csv" \
    --output-dir "$ML_DIR/model" \
    --estimator "$ESTIMATOR"

  invoke_step "Product price model training" \
    "$REPO_ROOT/ml/scripts/train_price_model.py" \
    --input "$PRODUCT_ML_DIR/model_dataset.csv" \
    --output-dir "$PRODUCT_ML_DIR/model" \
    --series-level "product" \
    --estimator "$ESTIMATOR"

  invoke_step "Brand price model training" \
    "$REPO_ROOT/ml/scripts/train_price_model.py" \
    --input "$BRAND_ML_DIR/model_dataset.csv" \
    --output-dir "$BRAND_ML_DIR/model" \
    --series-level "brand" \
    --estimator "$ESTIMATOR"

  invoke_step "Direct store price model training" \
    "$REPO_ROOT/ml/scripts/train_price_model.py" \
    --input "$STORE_ML_DIR/model_dataset.csv" \
    --output-dir "$STORE_ML_DIR/model" \
    --series-level "store" \
    --estimator "$ESTIMATOR"
fi

SUBTYPE_MODEL="$ML_DIR/model/price_model.joblib"
PRODUCT_MODEL="$PRODUCT_ML_DIR/model/price_model.joblib"
BRAND_MODEL="$BRAND_ML_DIR/model/price_model.joblib"
STORE_MODEL="$STORE_ML_DIR/model/price_model.joblib"

if [[ ! -f "$SUBTYPE_MODEL" || ! -f "$PRODUCT_MODEL" || ! -f "$BRAND_MODEL" || ! -f "$STORE_MODEL" ]]; then
  echo "Trained models not found. Run once with --train-model."
  exit 1
fi

invoke_step "Subtype price prediction (saved model)" \
  "$REPO_ROOT/ml/scripts/predict_prices.py" \
  --input "$ML_DIR/model_dataset.csv" \
  --model "$SUBTYPE_MODEL" \
  --output-dir "$ML_DIR/model" \
  --forecast-horizon "$FORECAST_HORIZON"

invoke_step "Product price prediction (saved model)" \
  "$REPO_ROOT/ml/scripts/predict_prices.py" \
  --input "$PRODUCT_ML_DIR/model_dataset.csv" \
  --model "$PRODUCT_MODEL" \
  --output-dir "$PRODUCT_ML_DIR/model" \
  --series-level "product" \
  --forecast-horizon "$FORECAST_HORIZON"

invoke_step "Brand price prediction (saved model)" \
  "$REPO_ROOT/ml/scripts/predict_prices.py" \
  --input "$BRAND_ML_DIR/model_dataset.csv" \
  --model "$BRAND_MODEL" \
  --output-dir "$BRAND_ML_DIR/model" \
  --series-level "brand" \
  --forecast-horizon "$FORECAST_HORIZON"

invoke_step "Direct store price prediction (saved model)" \
  "$REPO_ROOT/ml/scripts/predict_prices.py" \
  --input "$STORE_ML_DIR/model_dataset.csv" \
  --model "$STORE_MODEL" \
  --output-dir "$STORE_ML_DIR/model" \
  --series-level "store" \
  --forecast-horizon "$FORECAST_HORIZON"

invoke_step "Backend forecast payload export" \
  "$REPO_ROOT/ml/scripts/export_backend_forecasts.py" \
  --subtype-input "$ML_DIR/model/future_predictions.csv" \
  --product-input "$PRODUCT_ML_DIR/model/future_predictions.csv" \
  --brand-input "$BRAND_ML_DIR/model/future_predictions.csv" \
  --store-input "$STORE_ML_DIR/model/future_predictions.csv" \
  --output-dir "$ML_DIR/backend"

if [[ "$RUN_TESTS" -eq 1 ]]; then
  invoke_step "Tests" -m unittest discover -s ml/tests -v
fi

echo ""
echo "Pipeline completed successfully!"
