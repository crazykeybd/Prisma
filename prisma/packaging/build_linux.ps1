[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$DockerImage = "centos:7.9.2009"
)

$ErrorActionPreference = "Stop"

$PackagingDir = $PSScriptRoot
$AppRoot = Split-Path -Parent $PackagingDir
$RepoRoot = Split-Path -Parent $AppRoot
$TauriRoot = Join-Path $AppRoot "tauri-app\src-tauri"
$OutputDir = Join-Path $RepoRoot "dist"
$BuildScript = Join-Path $PackagingDir "linux\build_linux.sh"

function Assert-Path {
    param([string]$Path, [string]$Description)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Description not found: $Path"
    }
}

function Get-ConfiguredVersion {
    $cargoToml = Get-Content -Raw -LiteralPath (Join-Path $TauriRoot "Cargo.toml")
    $match = [regex]::Match($cargoToml, '(?m)^version\s*=\s*"([^"]+)"')
    if (-not $match.Success) {
        throw "Could not read version from Cargo.toml"
    }
    return $match.Groups[1].Value
}

Assert-Path $BuildScript "Linux container build script"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required to build a CentOS 7.9 compatible Linux package."
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Get-ConfiguredVersion
}
if ($Version -notmatch '^[0-9]+(?:\.[0-9]+){1,3}$') {
    throw "Invalid application version: $Version"
}

& docker image inspect $DockerImage *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Pulling Linux build image $DockerImage..."
    & docker pull --platform linux/amd64 $DockerImage
    if ($LASTEXITCODE -ne 0) {
        throw "Could not pull Docker image: $DockerImage"
    }
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$ArchiveName = "Prisma-Linux-x64-$Version.tar.gz"
$ArchivePath = Join-Path $OutputDir $ArchiveName
$ChecksumPath = "$ArchivePath.sha256"
foreach ($path in @($ArchivePath, $ChecksumPath)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}

Write-Host "Building Prisma $Version in $DockerImage..."
$mountSpec = "type=bind,source=$RepoRoot,target=/workspace"
$containerCommand = "tr -d '\r' < /workspace/prisma/packaging/linux/build_linux.sh > /tmp/prisma-build-linux.sh && bash /tmp/prisma-build-linux.sh"
& docker run --rm `
    --platform linux/amd64 `
    --mount $mountSpec `
    -e "APP_VERSION=$Version" `
    $DockerImage `
    /bin/bash -lc $containerCommand
if ($LASTEXITCODE -ne 0) {
    throw "CentOS 7.9 Linux package build failed with exit code $LASTEXITCODE"
}

Assert-Path $ArchivePath "Linux package"
$archive = Get-Item -LiteralPath $ArchivePath
if ($archive.Length -lt 10MB) {
    throw "Linux package is unexpectedly small: $($archive.Length) bytes"
}
$hash = Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256
Set-Content -LiteralPath $ChecksumPath `
    -Value ("{0}  {1}" -f $hash.Hash.ToLowerInvariant(), $archive.Name) `
    -Encoding ascii

Write-Host ""
Write-Host "Linux package created:"
Write-Host "  $($archive.FullName)"
Write-Host ("  Size: {0:N1} MB" -f ($archive.Length / 1MB))
Write-Host "  SHA256: $($hash.Hash)"
Write-Host "  Checksum: $ChecksumPath"
Write-Host "  Target: CentOS 7.9 x86_64"

