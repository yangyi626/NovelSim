[CmdletBinding()]
param(
    [string]$UnityPath = $env:UNITY_EDITOR_PATH,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$versionFile = Join-Path $projectRoot "ProjectSettings\ProjectVersion.txt"
$versionLine = Get-Content -Encoding utf8 $versionFile |
    Where-Object { $_ -like "m_EditorVersion:*" } |
    Select-Object -First 1
$editorVersion = ($versionLine -split ":", 2)[1].Trim()

function Resolve-UnityEditor {
    param([string]$ExplicitPath)

    $candidates = @()
    if ($ExplicitPath) {
        $candidates += $ExplicitPath
    }
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles (
            "Unity\Hub\Editor\$editorVersion\Editor\Unity.exe"
        )
    }
    $hubConfig = Join-Path $env:APPDATA (
        "UnityHub\secondaryInstallPath.json"
    )
    if (Test-Path -LiteralPath $hubConfig) {
        $hubRoot = Get-Content -Raw -Encoding utf8 $hubConfig |
            ConvertFrom-Json
        if ($hubRoot) {
            $candidates += Join-Path ([string]$hubRoot) (
                "$editorVersion\Editor\Unity.exe"
            )
        }
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Unity $editorVersion was not found. Pass Unity.exe with -UnityPath."
}

$editorPath = Resolve-UnityEditor $UnityPath
$resolvedOutput = if ($OutputPath) {
    [System.IO.Path]::GetFullPath($OutputPath)
} else {
    Join-Path $projectRoot "Builds\Windows\NovelSim3D.exe"
}
$logDirectory = Join-Path $projectRoot "Logs\Build"
New-Item -ItemType Directory -Force $logDirectory | Out-Null
$logPath = Join-Path $logDirectory "windows.log"
$env:NOVELSIM_WINDOWS_BUILD_PATH = $resolvedOutput
$arguments = @(
    "-batchmode",
    "-nographics",
    "-quit",
    "-projectPath",
    "`"$projectRoot`"",
    "-executeMethod",
    "NovelSim.Editor.NovelSimWindowsBuild.BuildWindowsFromCommandLine",
    "-logFile",
    "`"$logPath`""
)
$process = Start-Process `
    -FilePath $editorPath `
    -ArgumentList $arguments `
    -PassThru `
    -WindowStyle Hidden
try {
    Wait-Process -Id $process.Id -Timeout 1800
} catch {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Unity Windows build timed out after 30 minutes."
}
$process.Refresh()
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $resolvedOutput)) {
    Get-Content -Encoding utf8 -Tail 250 $logPath
    throw "Windows build failed with Unity exit code $($process.ExitCode)."
}
$artifact = Get-Item -LiteralPath $resolvedOutput
Write-Host "Windows build passed: $($artifact.FullName) ($($artifact.Length) bytes)"
