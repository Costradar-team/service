param(
    [string]$Python = "python",
    [switch]$SkipProfiling,
    [switch]$RunTests,
    [switch]$TrainModel,
    [ValidateSet("gradient-boosting", "lightgbm")]
    [string]$Estimator = "gradient-boosting"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = $PSScriptRoot
$RawData = Join-Path $RepoRoot "data-pipeline\data\raw"
$ArtifactRoot = Join-Path $RepoRoot "artifacts"
$QualityDir = Join-Path $ArtifactRoot "data-quality"
$ProcessedDir = Join-Path $ArtifactRoot "processed"
$TransformReportDir = Join-Path $QualityDir "transform"
$MlDir = Join-Path $ArtifactRoot "ml"
$ProductMlDir = Join-Path $MlDir "product"

if (-not (Test-Path -LiteralPath $RawData)) {
    throw "Raw data directory not found: $RawData"
}

$RawCsvCount = @(
    Get-ChildItem -LiteralPath $RawData -File |
        Where-Object { $_.Extension -ieq ".csv" }
).Count
if ($RawCsvCount -eq 0) {
    throw "No raw CSV files found in: $RawData"
}

New-Item -ItemType Directory -Force $QualityDir, $ProcessedDir, $TransformReportDir, $MlDir, $ProductMlDir | Out-Null

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
    Invoke-PythonStep "1/5 Data profiling" @(
        (Join-Path $RepoRoot "data-pipeline\scripts\profile_kca.py"),
        $RawData,
        "--output", (Join-Path $QualityDir "profiling_summary.json")
    )
}
else {
    Write-Host "`n[1/5 Data profiling skipped]"
}

Invoke-PythonStep "2/5 Data transform" @(
    (Join-Path $RepoRoot "data-pipeline\scripts\transform_kca.py"),
    $RawData,
    "--output-dir", $ProcessedDir,
    "--report-dir", $TransformReportDir
)

Invoke-PythonStep "3/5 Unit-price normalization and dual dataset build" @(
    (Join-Path $RepoRoot "ml\scripts\build_model_dataset.py"),
    "--input", (Join-Path $ProcessedDir "kca_prices_processed.csv"),
    "--output-dir", $MlDir
)

Invoke-PythonStep "4/5 Subtype baseline evaluation" @(
    (Join-Path $RepoRoot "ml\scripts\evaluate_baselines.py"),
    "--input", (Join-Path $MlDir "model_dataset.csv"),
    "--output-dir", $MlDir
)

Invoke-PythonStep "5/5 Product baseline evaluation" @(
    (Join-Path $RepoRoot "ml\scripts\evaluate_baselines.py"),
    "--input", (Join-Path $ProductMlDir "model_dataset.csv"),
    "--output-dir", $ProductMlDir,
    "--series-level", "product"
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

    Invoke-PythonStep "Backend forecast payload export" @(
        (Join-Path $RepoRoot "ml\scripts\export_backend_forecasts.py"),
        "--subtype-input", (Join-Path $MlDir "model\future_predictions.csv"),
        "--product-input", (Join-Path $ProductMlDir "model\future_predictions.csv"),
        "--output-dir", (Join-Path $MlDir "backend")
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
