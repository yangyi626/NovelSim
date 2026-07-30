[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path -LiteralPath (
    Join-Path $projectRoot "..\.."
)).Path
$dependencyRoot = Join-Path $repositoryRoot (
    ".deps\ffmpeg-imageio-0.6.0"
)
$ffmpegPath = Join-Path $dependencyRoot "ffmpeg.exe"
$wheelPath = Join-Path $dependencyRoot (
    "imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl"
)
$wheelSha256 = (
    "02fa47c83703c37df6bfe4896aab3390" +
    "13f62bf02c5ebf2dce6da56af04ffc0a"
)
$binarySha256 = (
    "2ce797a0f88d7f067180338fb227f7b1" +
    "928ea727bd9a4d7a1d022f7c52af71a3"
)

function Test-Hash {
    param(
        [string]$Path,
        [string]$Expected
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $actual = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $Path
    ).Hash.ToLowerInvariant()
    return $actual -eq $Expected
}

if (Test-Hash $ffmpegPath $binarySha256) {
    Write-Output $ffmpegPath
    exit 0
}

New-Item -ItemType Directory -Force $dependencyRoot | Out-Null
if (-not (Test-Hash $wheelPath $wheelSha256)) {
    $metadata = Invoke-RestMethod `
        -Uri "https://pypi.org/pypi/imageio-ffmpeg/0.6.0/json" `
        -TimeoutSec 30
    $asset = $metadata.urls |
        Where-Object {
            $_.filename -eq (
                "imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl"
            )
        } |
        Select-Object -First 1
    if (-not $asset) {
        throw "Pinned imageio-ffmpeg Windows wheel was not found."
    }
    if ($asset.digests.sha256 -ne $wheelSha256) {
        throw "Pinned PyPI wheel hash metadata changed."
    }
    $downloadPath = "$wheelPath.download"
    if (Test-Path -LiteralPath $downloadPath) {
        Remove-Item -LiteralPath $downloadPath -Force
    }
    Invoke-WebRequest `
        -Uri $asset.url `
        -OutFile $downloadPath `
        -TimeoutSec 180
    if (-not (Test-Hash $downloadPath $wheelSha256)) {
        Remove-Item -LiteralPath $downloadPath -Force
        throw "Downloaded imageio-ffmpeg wheel failed SHA-256."
    }
    Move-Item -LiteralPath $downloadPath -Destination $wheelPath -Force
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($wheelPath)
try {
    $entry = $archive.Entries |
        Where-Object {
            $_.FullName -like (
                "imageio_ffmpeg/binaries/ffmpeg-*.exe"
            )
        } |
        Select-Object -First 1
    if (-not $entry) {
        throw "FFmpeg binary is missing from the pinned wheel."
    }
    $temporaryPath = "$ffmpegPath.extracting"
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
    $inputStream = $entry.Open()
    try {
        $outputStream = [System.IO.File]::Open(
            $temporaryPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write)
        try {
            $inputStream.CopyTo($outputStream)
        } finally {
            $outputStream.Dispose()
        }
    } finally {
        $inputStream.Dispose()
    }
    if (-not (Test-Hash $temporaryPath $binarySha256)) {
        Remove-Item -LiteralPath $temporaryPath -Force
        throw "Extracted FFmpeg binary failed SHA-256."
    }
    Move-Item `
        -LiteralPath $temporaryPath `
        -Destination $ffmpegPath `
        -Force
} finally {
    $archive.Dispose()
}

Write-Output $ffmpegPath
