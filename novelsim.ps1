param(
    [ValidateSet("start", "stop", "status", "restart")]
    [string]$Action = "start",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$StackArguments
)

$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    "python"
}

Push-Location $projectRoot
try {
    & $python -m web.stack $Action @StackArguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
