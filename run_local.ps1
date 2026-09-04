param(
    [string]$Python = "python",
    [switch]$SkipProfiling,
    [switch]$RunTests,
    [switch]$TrainModel,
    [ValidateSet("gradient-boosting", "lightgbm")]
    [string]$Estimator = "gradient-boosting",
    [ValidateRange(1, 4)]
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
$ItemMlDir = Join-Path $MlDir "item"
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

New-Item -ItemType Directory -Force $QualityDir, $ProcessedDir, $TransformReportDir, $MlDir, $ItemMlDir | Out-Null

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
    Invoke-PythonStep "1/4 Data profiling" @(
        (Join-Path $RepoRoot "data-pipeline\scripts\profile\profile_kca.py"),
        $KcaRawData,
        "--output", (Join-Path $QualityDir "profiling_summary.json")
    )
}
else {
    Write-Host "`n[1/4 Data profiling skipped]"
}

Invoke-PythonStep "2/4 Data transform" @(
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
Invoke-PythonStep "3/4 Unit-price normalization and external-feature dataset build" $BuildDatasetArguments

Invoke-PythonStep "4/4 Item baseline evaluation" @(
    (Join-Path $RepoRoot "ml\scripts\evaluate_baselines.py"),
    "--input", (Join-Path $ItemMlDir "model_dataset.csv"),
    "--output-dir", $ItemMlDir,
    "--series-level", "item"
)

if ($TrainModel) {
    Invoke-PythonStep "Advanced direct item price model training" @(
        (Join-Path $RepoRoot "ml\scripts\train_advanced_item_model.py"),
        "--input", (Join-Path $ItemMlDir "model_dataset.csv"),
        "--output-dir", (Join-Path $ItemMlDir "model"),
        "--estimator", $Estimator,
        "--max-forecast-horizon", "4"
    )

}

$ItemModelDir = Join-Path $ItemMlDir "model"
$ItemModel = Join-Path $ItemModelDir "price_model.joblib"
if (-not (Test-Path -LiteralPath $ItemModel)) {
    throw "Trained item model not found. Run once with -TrainModel before prediction-only runs."
}

Invoke-PythonStep "Advanced direct item price prediction (saved model)" @(
    (Join-Path $RepoRoot "ml\scripts\predict_advanced_item_prices.py"),
    "--input", (Join-Path $ItemMlDir "model_dataset.csv"),
    "--model", $ItemModel,
    "--output-dir", $ItemModelDir,
    "--forecast-horizon", $ForecastHorizon
)

Invoke-PythonStep "Backend forecast payload export" @(
    (Join-Path $RepoRoot "ml\scripts\export_backend_forecasts.py"),
    "--item-input", (Join-Path $ItemModelDir "future_predictions.csv"),
    "--output-dir", (Join-Path $MlDir "backend")
)

if (Test-Path -LiteralPath (Join-Path $ItemModelDir "backtest_predictions.csv")) {
    Invoke-PythonStep "Backtest MAPE and purchase-timing savings" @(
        (Join-Path $RepoRoot "ml\scripts\evaluate_backtest_savings.py"),
        "--artifact-root", $MlDir,
        "--output", (Join-Path $MlDir "backtest_business_metrics.json")
    )
}
else {
    Write-Warning (
        "Backtest metrics skipped because the item model training output is missing."
    )
}

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
