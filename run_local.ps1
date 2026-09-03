param(
    [string]$Python = "python",
    [switch]$SkipProfiling,
    [switch]$RunTests,
    [switch]$TrainModel,
    [ValidateSet("gradient-boosting", "lightgbm")]
    [string]$Estimator = "gradient-boosting",
    [ValidateRange(1, 52)]
    [int]$ForecastHorizon = 1
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = $PSScriptRoot
$RawDataRoot = Join-Path $RepoRoot "data-pipeline\data\raw"
$KcaRawData = Join-Path $RawDataRoot "kca"
if (-not (Test-Path -LiteralPath $KcaRawData)) {
    $KcaRawData = $RawDataRoot
}
$ArtifactRoot = Join-Path $RepoRoot "artifacts"
$QualityDir = Join-Path $ArtifactRoot "data-quality"
$ProcessedDir = Join-Path $ArtifactRoot "processed"
$TransformReportDir = Join-Path $QualityDir "transform"
$MlDir = Join-Path $ArtifactRoot "ml"
$ProductMlDir = Join-Path $MlDir "product"
$BrandMlDir = Join-Path $MlDir "brand"
$StoreMlDir = Join-Path $MlDir "store"
$FisProcessedDir = Join-Path $RepoRoot "data-pipeline\data\processed\fis"
$KamisProcessedDir = Join-Path $RepoRoot "data-pipeline\data\processed\kamis"

if (-not (Test-Path -LiteralPath $KcaRawData)) {
    throw "KCA raw data directory not found: $KcaRawData"
}

$RawCsvCount = @(
    Get-ChildItem -LiteralPath $KcaRawData -File |
        Where-Object { $_.Extension -ieq ".csv" }
).Count
if ($RawCsvCount -eq 0) {
    throw "No KCA raw CSV files found in: $KcaRawData"
}

New-Item -ItemType Directory -Force $QualityDir, $ProcessedDir, $TransformReportDir, $MlDir, $ProductMlDir, $BrandMlDir, $StoreMlDir | Out-Null

function Invoke-PythonStep {
    param(
        [string]$Name,
        [string[]]$Arguments
    )

    Write-Host "`n[$Name]"
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipProfiling) {
    Invoke-PythonStep "1/7 Data profiling" @(
        (Join-Path $RepoRoot "data-pipeline\scripts\profile\profile_kca.py"),
        $KcaRawData,
        "--output", (Join-Path $QualityDir "profiling_summary.json")
    )
}
else {
    Write-Host "`n[1/7 Data profiling skipped]"
}

Invoke-PythonStep "2/7 Data transform" @(
    (Join-Path $RepoRoot "data-pipeline\scripts\transform\transform_kca.py"),
    $KcaRawData,
    "--output-dir", $ProcessedDir,
    "--report-dir", $TransformReportDir
)

$BuildDatasetArguments = @(
    (Join-Path $RepoRoot "ml\scripts\build_model_dataset.py"),
    "--input", (Join-Path $ProcessedDir "kca_prices_processed.csv"),
    "--output-dir", $MlDir
)
if (
    (Test-Path -LiteralPath (Join-Path $FisProcessedDir "fis_item.csv")) -and
    (Test-Path -LiteralPath (Join-Path $FisProcessedDir "fis_price_observation.csv"))
) {
    $BuildDatasetArguments += @("--fis-dir", $FisProcessedDir)
}
if (
    (Test-Path -LiteralPath (Join-Path $KamisProcessedDir "kamis_item.csv")) -and
    (Test-Path -LiteralPath (Join-Path $KamisProcessedDir "kamis_price_observation.csv"))
) {
    $BuildDatasetArguments += @("--kamis-dir", $KamisProcessedDir)
}
Invoke-PythonStep "3/7 Unit-price normalization and external-feature dataset build" $BuildDatasetArguments

Invoke-PythonStep "4/7 Subtype baseline evaluation" @(
    (Join-Path $RepoRoot "ml\scripts\evaluate_baselines.py"),
    "--input", (Join-Path $MlDir "model_dataset.csv"),
    "--output-dir", $MlDir
)

Invoke-PythonStep "5/7 Product baseline evaluation" @(
    (Join-Path $RepoRoot "ml\scripts\evaluate_baselines.py"),
    "--input", (Join-Path $ProductMlDir "model_dataset.csv"),
    "--output-dir", $ProductMlDir,
    "--series-level", "product"
)

Invoke-PythonStep "6/7 Brand baseline evaluation" @(
    (Join-Path $RepoRoot "ml\scripts\evaluate_baselines.py"),
    "--input", (Join-Path $BrandMlDir "model_dataset.csv"),
    "--output-dir", $BrandMlDir,
    "--series-level", "brand"
)

Invoke-PythonStep "7/7 Store baseline evaluation" @(
    (Join-Path $RepoRoot "ml\scripts\evaluate_baselines.py"),
    "--input", (Join-Path $StoreMlDir "model_dataset.csv"),
    "--output-dir", $StoreMlDir,
    "--series-level", "store"
)

if ($TrainModel) {
    Invoke-PythonStep "Subtype price model training" @(
        (Join-Path $RepoRoot "ml\scripts\train_price_model.py"),
        "--input", (Join-Path $MlDir "model_dataset.csv"),
        "--output-dir", (Join-Path $MlDir "model"),
        "--estimator", $Estimator
    )

    Invoke-PythonStep "Product price model training" @(
        (Join-Path $RepoRoot "ml\scripts\train_price_model.py"),
        "--input", (Join-Path $ProductMlDir "model_dataset.csv"),
        "--output-dir", (Join-Path $ProductMlDir "model"),
        "--series-level", "product",
        "--estimator", $Estimator
    )

    Invoke-PythonStep "Brand price model training" @(
        (Join-Path $RepoRoot "ml\scripts\train_price_model.py"),
        "--input", (Join-Path $BrandMlDir "model_dataset.csv"),
        "--output-dir", (Join-Path $BrandMlDir "model"),
        "--series-level", "brand",
        "--estimator", $Estimator
    )

    Invoke-PythonStep "Direct store price model training" @(
        (Join-Path $RepoRoot "ml\scripts\train_price_model.py"),
        "--input", (Join-Path $StoreMlDir "model_dataset.csv"),
        "--output-dir", (Join-Path $StoreMlDir "model"),
        "--series-level", "store",
        "--estimator", $Estimator
    )

}

$SubtypeModelDir = Join-Path $MlDir "model"
$ProductModelDir = Join-Path $ProductMlDir "model"
$BrandModelDir = Join-Path $BrandMlDir "model"
$StoreModelDir = Join-Path $StoreMlDir "model"
$SubtypeModel = Join-Path $SubtypeModelDir "price_model.joblib"
$ProductModel = Join-Path $ProductModelDir "price_model.joblib"
$BrandModel = Join-Path $BrandModelDir "price_model.joblib"
$StoreModel = Join-Path $StoreModelDir "price_model.joblib"
if (
    -not (Test-Path -LiteralPath $SubtypeModel) -or
    -not (Test-Path -LiteralPath $ProductModel) -or
    -not (Test-Path -LiteralPath $BrandModel) -or
    -not (Test-Path -LiteralPath $StoreModel)
) {
    throw "Trained models not found. Run once with -TrainModel before prediction-only runs."
}

Invoke-PythonStep "Subtype price prediction (saved model)" @(
    (Join-Path $RepoRoot "ml\scripts\predict_prices.py"),
    "--input", (Join-Path $MlDir "model_dataset.csv"),
    "--model", $SubtypeModel,
    "--output-dir", $SubtypeModelDir,
    "--forecast-horizon", $ForecastHorizon
)

Invoke-PythonStep "Product price prediction (saved model)" @(
    (Join-Path $RepoRoot "ml\scripts\predict_prices.py"),
    "--input", (Join-Path $ProductMlDir "model_dataset.csv"),
    "--model", $ProductModel,
    "--output-dir", $ProductModelDir,
    "--series-level", "product",
    "--forecast-horizon", $ForecastHorizon
)

Invoke-PythonStep "Brand price prediction (saved model)" @(
    (Join-Path $RepoRoot "ml\scripts\predict_prices.py"),
    "--input", (Join-Path $BrandMlDir "model_dataset.csv"),
    "--model", $BrandModel,
    "--output-dir", $BrandModelDir,
    "--series-level", "brand",
    "--forecast-horizon", $ForecastHorizon
)

Invoke-PythonStep "Direct store price prediction (saved model)" @(
    (Join-Path $RepoRoot "ml\scripts\predict_prices.py"),
    "--input", (Join-Path $StoreMlDir "model_dataset.csv"),
    "--model", $StoreModel,
    "--output-dir", $StoreModelDir,
    "--series-level", "store",
    "--forecast-horizon", $ForecastHorizon
)

Invoke-PythonStep "Backend forecast payload export" @(
    (Join-Path $RepoRoot "ml\scripts\export_backend_forecasts.py"),
    "--subtype-input", (Join-Path $SubtypeModelDir "future_predictions.csv"),
    "--product-input", (Join-Path $ProductModelDir "future_predictions.csv"),
    "--brand-input", (Join-Path $BrandModelDir "future_predictions.csv"),
    "--store-input", (Join-Path $StoreModelDir "future_predictions.csv"),
    "--output-dir", (Join-Path $MlDir "backend")
)

if ($RunTests) {
    Push-Location $RepoRoot
    try {
        Invoke-PythonStep "Tests" @(
            "-m", "unittest", "discover", "-s", "ml\tests", "-v"
        )
    }
    finally {
        Pop-Location
    }
}

Write-Host "`nCostRadar local pipeline completed."
Write-Host "Artifacts: $ArtifactRoot"
