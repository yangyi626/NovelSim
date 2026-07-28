[CmdletBinding()]
param(
    [string]$Executable = "",
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [int]$TimeoutSeconds = 360
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$resolvedExecutable = if ($Executable) {
    [System.IO.Path]::GetFullPath($Executable)
} else {
    Join-Path $projectRoot "Builds\Windows\NovelSim3D.exe"
}
if (-not (Test-Path -LiteralPath $resolvedExecutable)) {
    throw "Windows build not found: $resolvedExecutable"
}
$resultDirectory = Join-Path $projectRoot "Logs\WindowsSmoke"
New-Item -ItemType Directory -Force $resultDirectory | Out-Null

function Invoke-SmokeRun {
    param(
        [string]$Name,
        [string[]]$ModeArguments
    )

    $logPath = Join-Path $resultDirectory "$Name.log"
    $reportPath = Join-Path $resultDirectory "$Name.json"
    $arguments = @(
        "-batchmode",
        "-nographics",
        "-logFile",
        "`"$logPath`"",
        "-novelsim-smoke-report",
        "`"$reportPath`"",
        "-novelsim-api-url",
        $ApiUrl
    ) + $ModeArguments
    $process = Start-Process `
        -FilePath $resolvedExecutable `
        -ArgumentList $arguments `
        -PassThru `
        -WindowStyle Hidden
    try {
        Wait-Process -Id $process.Id -Timeout $TimeoutSeconds
    } catch {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "Windows smoke run '$Name' timed out."
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) {
        Get-Content -Encoding utf8 -Tail 200 $logPath
        throw "Windows smoke run '$Name' failed with exit code $($process.ExitCode)."
    }
    return Get-Content -Raw -Encoding utf8 $reportPath | ConvertFrom-Json
}

$interaction = Invoke-SmokeRun "interaction" @(
    "-novelsim-smoke-interact",
    "-novelsim-new-session"
)
if ($interaction.status -ne "interaction_ok" -or $interaction.version -lt 1) {
    throw "Real E interaction did not advance the world."
}
$resume = Invoke-SmokeRun "resume" @("-novelsim-smoke-resume")
if (
    $resume.status -ne "resume_ok"
    -or $resume.session_id -ne $interaction.session_id
    -or $resume.version -ne $interaction.version
) {
    throw "Saved session was not restored exactly."
}
Write-Host (
    "Windows smoke passed: session $($resume.session_id), "
    + "version $($resume.version)"
)
