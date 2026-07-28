[CmdletBinding()]
param(
    [string]$UnityPath = $env:UNITY_EDITOR_PATH
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

function Invoke-NovelSimUnityTest {
    param(
        [string]$EditorPath,
        [ValidateSet("EditMode", "PlayMode")]
        [string]$Platform
    )

    $resultDirectory = Join-Path $projectRoot "Logs\TestResults"
    New-Item -ItemType Directory -Force $resultDirectory | Out-Null
    $name = $Platform.ToLowerInvariant()
    $logPath = Join-Path $resultDirectory "$name.log"
    $resultPath = Join-Path $resultDirectory "$name.xml"
    $arguments = @(
        "-batchmode",
        "-nographics",
        "-projectPath",
        "`"$projectRoot`"",
        "-runTests",
        "-testPlatform",
        $Platform,
        "-testResults",
        "`"$resultPath`"",
        "-logFile",
        "`"$logPath`""
    )

    $process = Start-Process `
        -FilePath $EditorPath `
        -ArgumentList $arguments `
        -PassThru `
        -WindowStyle Hidden
    Wait-Process -Id $process.Id -Timeout 600
    $process.Refresh()
    if ($process.ExitCode -ne 0) {
        Get-Content -Encoding utf8 -Tail 200 $logPath
        throw "$Platform failed with Unity exit code $($process.ExitCode)."
    }

    [xml]$document = Get-Content -Raw -Encoding utf8 $resultPath
    $run = $document."test-run"
    if ($run.result -ne "Passed") {
        throw "$Platform did not pass: $($run.result)."
    }
    Write-Host (
        "$Platform passed: $($run.passed)/$($run.total)"
    )
}

$editorPath = Resolve-UnityEditor $UnityPath
Write-Host "Unity: $editorPath"
Invoke-NovelSimUnityTest $editorPath "EditMode"
Invoke-NovelSimUnityTest $editorPath "PlayMode"
