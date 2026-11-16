param(
    [Parameter(Position = 0)]
    [ValidateSet("preflight", "smoke", "evaluate")]
    [string]$Command = "preflight",

    [ValidateRange(1, 4)]
    [int]$MaxWorkers = 2
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

Push-Location $RepoRoot
try {
    $BgeHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8081/health" -TimeoutSec 10
    if ($BgeHealth.status -ne "ok") {
        throw "BGE-M3 health check failed"
    }
    $BgeModels = Invoke-RestMethod -Uri "http://127.0.0.1:8081/v1/models" -TimeoutSec 10
    if ($BgeModels.data.id -notcontains "BAAI/bge-m3") {
        throw "BGE-M3 /v1/models does not expose BAAI/bge-m3"
    }

    $QwenHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8080/health" -TimeoutSec 10
    if ($QwenHealth.status -ne "ok") {
        throw "Qwen3 health check failed"
    }
    $QwenModels = Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/models" -TimeoutSec 10
    $QwenModel = $QwenModels.data[0].id
    if ([string]::IsNullOrWhiteSpace($QwenModel)) {
        throw "Qwen3 /v1/models did not return a model id"
    }

    $env:RAG_RUNTIME_MODE = "DEV_LOCAL"
    $env:LLM_BASE_URL = "https://matreshkaapi.mooo.com/v1"
    $env:LLM_MODEL = "deepseek-v4.1-flash"
    $env:EMBEDDER_BASE_URL = "http://127.0.0.1:8081/v1"
    $env:EMBEDDER_API_KEY = "local"
    $env:EMB_MODEL = "BAAI/bge-m3"
    $env:RERANKER_BASE_URL = "http://127.0.0.1:8080/v1"
    $env:RERANKER_API_KEY = "local"
    $env:RERANKER_MODEL = $QwenModel
    $env:INDEX_DIR = (Resolve-Path "submission/assets/bge_m3").Path
    $env:MAX_WORKERS = [string]$MaxWorkers
    $env:API_MODE = "live"

    & poetry run python -m submission.dev.preflight $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Local RAG command failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
