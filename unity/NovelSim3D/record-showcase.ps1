[CmdletBinding()]
param(
    [string]$ExecutablePath = "",
    [string]$OutputPath = "",
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [ValidateRange(30, 180)]
    [int]$DurationSeconds = 50
)

$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class NovelSimCaptureWindow
{
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(
        IntPtr window,
        out Rect rectangle);

    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(
        IntPtr window,
        IntPtr insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags);
}
"@
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path -LiteralPath (
    Join-Path $projectRoot "..\.."
)).Path
$resolvedExecutable = if ($ExecutablePath) {
    [System.IO.Path]::GetFullPath($ExecutablePath)
} else {
    Join-Path $projectRoot "Builds\Windows\NovelSim3D.exe"
}
$resolvedOutput = if ($OutputPath) {
    [System.IO.Path]::GetFullPath($OutputPath)
} else {
    Join-Path $repositoryRoot (
        "portfolio\video\NovelSim-core-demo-v1.mp4"
    )
}
$reportPath = [System.IO.Path]::ChangeExtension(
    $resolvedOutput,
    ".json")
$checksumPath = "$resolvedOutput.sha256"
$logPath = [System.IO.Path]::ChangeExtension(
    $resolvedOutput,
    ".unity.log")

if (-not (Test-Path -LiteralPath $resolvedExecutable)) {
    throw (
        "Windows build not found: $resolvedExecutable`n" +
        "Run .\build-windows.ps1 first."
    )
}

$outputDirectory = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Force $outputDirectory | Out-Null
foreach ($path in @(
    $resolvedOutput,
    $reportPath,
    $checksumPath,
    $logPath
)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}

$ffmpegPath = & (Join-Path $projectRoot "ensure-ffmpeg.ps1")
if (
    $LASTEXITCODE -ne 0 -or
    -not (Test-Path -LiteralPath $ffmpegPath)
) {
    throw "Pinned FFmpeg dependency could not be prepared."
}

$venvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    "python"
}
$healthUrl = "$($ApiUrl.TrimEnd('/'))/api/meta/contract"
$startedBackend = $false
$unityProcess = $null
$ffmpegProcess = $null

try {
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
        Push-Location $repositoryRoot
        try {
            & $python -m web.stack start --no-worker
            if ($LASTEXITCODE -ne 0) {
                throw "NovelSim backend failed to start."
            }
            $startedBackend = $true
        } finally {
            Pop-Location
        }
    }

    $unityArguments = @(
        "-screen-width", "1280",
        "-screen-height", "720",
        "-screen-fullscreen", "0",
        "-popupwindow",
        "-logFile", "`"$logPath`"",
        "-novelsim-api-url", $ApiUrl,
        "-novelsim-showcase",
        "-novelsim-showcase-duration", $DurationSeconds,
        "-novelsim-showcase-report", "`"$reportPath`""
    )
    $unityProcess = Start-Process `
        -FilePath $resolvedExecutable `
        -ArgumentList $unityArguments `
        -PassThru `
        -WindowStyle Normal

    $windowDeadline = [DateTime]::UtcNow.AddSeconds(45)
    $windowTitle = ""
    while ([DateTime]::UtcNow -lt $windowDeadline) {
        if ($unityProcess.HasExited) {
            throw (
                "Unity exited before its capture window appeared. " +
                "See $logPath"
            )
        }
        $unityProcess.Refresh()
        if (
            $unityProcess.MainWindowHandle -ne 0 -and
            -not [string]::IsNullOrWhiteSpace(
                $unityProcess.MainWindowTitle)
        ) {
            $windowTitle = $unityProcess.MainWindowTitle
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if ([string]::IsNullOrWhiteSpace($windowTitle)) {
        throw "Unity capture window was not ready within 45 seconds."
    }
    $matchingWindows = @(
        Get-Process |
            Where-Object {
                $_.MainWindowHandle -ne 0 -and
                $_.MainWindowTitle -eq $windowTitle
            }
    )
    if (
        $matchingWindows.Count -ne 1 -or
        $matchingWindows[0].Id -ne $unityProcess.Id
    ) {
        $owners = (
            $matchingWindows |
                ForEach-Object { "$($_.Id):$($_.ProcessName)" }
        ) -join ", "
        throw (
            "Unity window title is not unique: '$windowTitle' " +
            "(owners: $owners)."
        )
    }
    Write-Host (
        "Capturing Unity window '$windowTitle' " +
        "(PID $($unityProcess.Id))."
    )
    $rectangle = New-Object NovelSimCaptureWindow+Rect
    if (-not [NovelSimCaptureWindow]::GetWindowRect(
        $unityProcess.MainWindowHandle,
        [ref]$rectangle
    )) {
        throw "Could not read the Unity window rectangle."
    }
    $captureWidth = $rectangle.Right - $rectangle.Left
    $captureHeight = $rectangle.Bottom - $rectangle.Top
    if ($captureWidth -lt 640 -or $captureHeight -lt 360) {
        throw (
            "Unity capture region is unexpectedly small: " +
            "${captureWidth}x${captureHeight}."
        )
    }
    $topMost = [IntPtr](-1)
    $noSizeOrMove = [uint32](0x0001 -bor 0x0002 -bor 0x0040)
    if (-not [NovelSimCaptureWindow]::SetWindowPos(
        $unityProcess.MainWindowHandle,
        $topMost,
        0,
        0,
        0,
        0,
        $noSizeOrMove
    )) {
        throw "Could not keep the Unity capture window in the foreground."
    }

    $escapedOutput = $resolvedOutput.Replace('"', '\"')
    $ffmpegInfo = New-Object System.Diagnostics.ProcessStartInfo
    $ffmpegInfo.FileName = $ffmpegPath
    $ffmpegInfo.Arguments = (
        "-hide_banner -loglevel error -y " +
        "-f gdigrab -draw_mouse 0 -framerate 30 " +
        "-offset_x $($rectangle.Left) " +
        "-offset_y $($rectangle.Top) " +
        "-video_size ${captureWidth}x${captureHeight} " +
        "-i desktop " +
        "-vf `"scale=trunc(iw/2)*2:trunc(ih/2)*2`" " +
        "-c:v libx264 -preset veryfast -crf 20 " +
        "-pix_fmt yuv420p -movflags +faststart " +
        "`"$escapedOutput`""
    )
    $ffmpegInfo.UseShellExecute = $false
    $ffmpegInfo.CreateNoWindow = $true
    $ffmpegInfo.RedirectStandardInput = $true
    $ffmpegProcess = New-Object System.Diagnostics.Process
    $ffmpegProcess.StartInfo = $ffmpegInfo
    if (-not $ffmpegProcess.Start()) {
        throw "FFmpeg capture process failed to start."
    }

    $takeDeadline = [DateTime]::UtcNow.AddSeconds(
        $DurationSeconds + 480)
    while (
        -not (Test-Path -LiteralPath $reportPath) -and
        [DateTime]::UtcNow -lt $takeDeadline
    ) {
        if ($unityProcess.HasExited) {
            throw (
                "Unity exited before writing the showcase report. " +
                "See $logPath"
            )
        }
        if ($ffmpegProcess.HasExited) {
            throw "FFmpeg exited before the Unity showcase completed."
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path -LiteralPath $reportPath)) {
        throw "Showcase timed out without a structured report."
    }

    $ffmpegProcess.StandardInput.WriteLine("q")
    $ffmpegProcess.StandardInput.Flush()
    if (-not $ffmpegProcess.WaitForExit(30000)) {
        $ffmpegProcess.Kill()
        throw "FFmpeg did not finalize the MP4 within 30 seconds."
    }
    if ($ffmpegProcess.ExitCode -ne 0) {
        throw "FFmpeg exited with code $($ffmpegProcess.ExitCode)."
    }

    Wait-Process -Id $unityProcess.Id -Timeout 20 -ErrorAction SilentlyContinue
    if (-not $unityProcess.HasExited) {
        Stop-Process -Id $unityProcess.Id -Force
        throw "Unity did not exit after the showcase report was written."
    }
    $unityProcess.Refresh()

    $report = Get-Content `
        -Raw `
        -Encoding utf8 `
        -LiteralPath $reportPath |
        ConvertFrom-Json
    if ($report.status -ne "passed" -or $unityProcess.ExitCode -ne 0) {
        throw (
            "Unity showcase failed: status=$($report.status), " +
            "exit=$($unityProcess.ExitCode), message=$($report.message)"
        )
    }
    $video = Get-Item -LiteralPath $resolvedOutput
    if ($video.Length -lt 1MB) {
        throw "Captured MP4 is unexpectedly small: $($video.Length) bytes."
    }
    & $ffmpegPath `
        -v error `
        -i $resolvedOutput `
        -f null `
        NUL
    if ($LASTEXITCODE -ne 0) {
        throw "Captured MP4 failed a full FFmpeg decode."
    }
    $videoHash = (
        Get-FileHash -LiteralPath $resolvedOutput -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $checksumLine = "$videoHash  $($video.Name)"
    Set-Content `
        -LiteralPath $checksumPath `
        -Value $checksumLine `
        -Encoding ascii

    Write-Host (
        "Showcase video passed: $($video.FullName) " +
        "($($video.Length) bytes), session $($report.session_id), " +
        "v$($report.version), $($report.duration_seconds)s"
    )
} finally {
    if ($ffmpegProcess -and -not $ffmpegProcess.HasExited) {
        try {
            $ffmpegProcess.StandardInput.WriteLine("q")
            $ffmpegProcess.WaitForExit(5000) | Out-Null
        } catch {
            $ffmpegProcess.Kill()
        }
    }
    if ($unityProcess -and -not $unityProcess.HasExited) {
        Stop-Process -Id $unityProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($startedBackend) {
        Push-Location $repositoryRoot
        try {
            & $python -m web.stack stop
        } finally {
            Pop-Location
        }
    }
}
