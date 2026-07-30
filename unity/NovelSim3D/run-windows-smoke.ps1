[CmdletBinding()]
param(
    [string]$Executable = "",
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [int]$TimeoutSeconds = 480
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
    Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $reportPath -Force -ErrorAction SilentlyContinue
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
$minimumSequence = $interaction.version * 1000 + 1
if (
    $interaction.presentation_commands -lt 1 -or
    $interaction.presentation_sequence -lt $minimumSequence
) {
    throw "Presentation commands were not consumed after the real turn."
}
$resume = Invoke-SmokeRun "resume" @("-novelsim-smoke-resume")
$expectedResumeSequence = $interaction.version * 1000 + 999
$resumeMatches = (
    $resume.status -eq "resume_ok" -and
    $resume.session_id -eq $interaction.session_id -and
    $resume.version -eq $interaction.version -and
    $resume.presentation_sequence -ge $interaction.presentation_sequence -and
    $resume.presentation_sequence -eq $expectedResumeSequence
)
if (-not $resumeMatches) {
    throw "Saved session was not restored exactly."
}
Write-Host "Interaction/resume smoke passed: session $($resume.session_id), version $($resume.version), presentation sequence $($resume.presentation_sequence)"

$routes = @(
    @{
        Route = "destroy_letter"
        Ending = "letter_destroyed"
        Version = 2
    },
    @{
        Route = "intercept_letter"
        Ending = "player_intercepted"
        Version = 2
    },
    @{
        Route = "expose_truth"
        Ending = "truth_exposed"
        Version = 5
    }
)
foreach ($case in $routes) {
    $route = $case.Route
    $result = Invoke-SmokeRun "secret-letter-$route" @(
        "-novelsim-smoke-secret-letter",
        $route,
        "-novelsim-new-session"
    )
    $routePassed = (
        $result.status -eq "secret_letter_ok" -and
        $result.route -eq $route -and
        $result.ending -eq $case.Ending -and
        $result.world_package_id -eq "secret_letter_v1" -and
        $result.version -eq $case.Version -and
        $result.memory_record_count -ge $case.Version -and
        $result.presentation_sequence -ge (
            $case.Version * 1000 + 999
        )
    )
    if (-not $routePassed) {
        throw "Secret-letter route '$route' did not reach its expected authoritative ending."
    }

    $routeResume = Invoke-SmokeRun "secret-letter-$route-resume" @(
        "-novelsim-smoke-resume"
    )
    $routeResumeMatches = (
        $routeResume.status -eq "resume_ok" -and
        $routeResume.session_id -eq $result.session_id -and
        $routeResume.version -eq $result.version -and
        $routeResume.presentation_sequence -eq (
            $result.version * 1000 + 999
        )
    )
    if (-not $routeResumeMatches) {
        throw "Secret-letter route '$route' was not restored exactly."
    }
    Write-Host (
        "Route smoke passed: $route -> $($result.ending), " +
        "session $($result.session_id), version $($result.version)"
    )
}
