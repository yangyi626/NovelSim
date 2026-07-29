[CmdletBinding()]
param(
    [string]$ExecutablePath = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$resolvedExecutable = if ($ExecutablePath) {
    [System.IO.Path]::GetFullPath($ExecutablePath)
} else {
    Join-Path $projectRoot "Builds\Windows\NovelSim3D.exe"
}
if (-not (Test-Path -LiteralPath $resolvedExecutable)) {
    throw "Windows build not found: $resolvedExecutable"
}

$resolvedOutput = if ($OutputPath) {
    [System.IO.Path]::GetFullPath($OutputPath)
} else {
    Join-Path $projectRoot "Logs\VisualPreview\phase2.png"
}
$outputDirectory = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Force $outputDirectory | Out-Null
if (Test-Path -LiteralPath $resolvedOutput) {
    Remove-Item -LiteralPath $resolvedOutput -Force
}

$arguments = @(
    "-screen-width", "1600",
    "-screen-height", "900",
    "-screen-fullscreen", "0",
    "-popupwindow",
    "-novelsim-capture", "`"$resolvedOutput`""
)
$process = Start-Process `
    -FilePath $resolvedExecutable `
    -ArgumentList $arguments `
    -PassThru `
    -WindowStyle Hidden
try {
    Wait-Process -Id $process.Id -Timeout 90
} catch {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Visual preview timed out after 90 seconds."
}
$process.Refresh()
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $resolvedOutput)) {
    throw "Visual preview failed with exit code $($process.ExitCode)."
}
$preview = Get-Item -LiteralPath $resolvedOutput
$message = "Visual preview captured: $($preview.FullName) " +
    "($($preview.Length) bytes)"
Write-Host $message
