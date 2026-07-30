[CmdletBinding()]
param(
    [string]$ExecutablePath = "",
    [string]$ApiUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    "python"
}
$executable = if ($ExecutablePath) {
    [System.IO.Path]::GetFullPath($ExecutablePath)
} else {
    Join-Path $projectRoot "unity\NovelSim3D\Builds\Windows\NovelSim3D.exe"
}

if (-not (Test-Path -LiteralPath $executable)) {
    throw (
        "Windows build not found: $executable`n" +
        "Run .\unity\NovelSim3D\build-windows.ps1 first."
    )
}

Push-Location $projectRoot
try {
    $healthUrl = "$($ApiUrl.TrimEnd('/'))/api/meta/contract"
    $backendHealthy = $false
    try {
        $metadata = Invoke-RestMethod `
            -Uri $healthUrl `
            -Method Get `
            -TimeoutSec 3
        $backendHealthy = $metadata.status -eq "ok"
    } catch {
        $backendHealthy = $false
    }
    if (-not $backendHealthy) {
        & $python -m web.stack start
        if ($LASTEXITCODE -ne 0) {
            throw "NovelSim backend failed to start."
        }
    }

    $arguments = @("-novelsim-api-url", $ApiUrl)
    $process = Start-Process `
        -FilePath $executable `
        -ArgumentList $arguments `
        -PassThru `
        -WindowStyle Normal
    Write-Host (
        "NovelSim demo started: backend=$ApiUrl, " +
        "unity_pid=$($process.Id)"
    )
} finally {
    Pop-Location
}
