[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$NsisExe = "",
    [switch]$SkipCargoBuild
)

$ErrorActionPreference = "Stop"

$PackagingDir = $PSScriptRoot
$AppRoot = Split-Path -Parent $PackagingDir
$RepoRoot = Split-Path -Parent $AppRoot
$TauriRoot = Join-Path $AppRoot "tauri-app\src-tauri"
$BuildRoot = Join-Path $AppRoot "build\installer"
$PayloadDir = Join-Path $BuildRoot "payload"
$OutputDir = Join-Path $RepoRoot "dist"
$DefaultNsisExe = Join-Path $env:LOCALAPPDATA "tauri\NSIS\makensis.exe"
$WebView2Setup = Join-Path $PackagingDir "vendor\MicrosoftEdgeWebView2Setup.exe"
$WebView2Url = "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/0126536e-e048-416f-923b-202f7f6fa64b/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
$WebView2Sha256 = "2688308AF55753C62FDA4E8140024E230C1FFC16B7C091B25889572F38DFF6E8"
$IconFile = Join-Path $TauriRoot "icons\icon.ico"
$NsiFile = Join-Path $PackagingDir "Prisma.nsi"
$VenvPython = Join-Path $AppRoot ".venv\Scripts\python.exe"
$PyvenvConfig = Join-Path $AppRoot ".venv\pyvenv.cfg"
$VenvSitePackages = Join-Path $AppRoot ".venv\Lib\site-packages"

function Assert-Path {
    param([string]$Path, [string]$Description)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Description not found: $Path"
    }
}

function Resolve-NsisCompiler {
    param([string]$ConfiguredPath)

    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        if (-not (Test-Path -LiteralPath $ConfiguredPath -PathType Leaf)) {
            throw "NSIS compiler not found: $ConfiguredPath"
        }
        return (Resolve-Path -LiteralPath $ConfiguredPath).Path
    }
    if (Test-Path -LiteralPath $DefaultNsisExe -PathType Leaf) {
        return $DefaultNsisExe
    }
    $command = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw "NSIS compiler not found. Install NSIS 3 or pass -NsisExe with the absolute path to makensis.exe."
}

function Copy-RobocopyTree {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExcludedDirectories = @()
    )
    Assert-Path $Source $Source
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $arguments = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
    if ($ExcludedDirectories.Count -gt 0) {
        $arguments += "/XD"
        $arguments += $ExcludedDirectories
    }
    & robocopy @arguments | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE while copying $Source"
    }
}

function Reset-StagingDirectory {
    $resolvedBuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
    $resolvedAppRoot = [System.IO.Path]::GetFullPath($AppRoot)
    if (-not $resolvedBuildRoot.StartsWith($resolvedAppRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a build directory outside the application root: $resolvedBuildRoot"
    }
    if (Test-Path -LiteralPath $BuildRoot) {
        Remove-Item -LiteralPath $BuildRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $PayloadDir | Out-Null
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
}

function Get-ConfiguredVersion {
    $cargoToml = Get-Content -Raw -LiteralPath (Join-Path $TauriRoot "Cargo.toml")
    $match = [regex]::Match($cargoToml, '(?m)^version\s*=\s*"([^"]+)"')
    if (-not $match.Success) {
        throw "Could not read version from Cargo.toml"
    }
    return $match.Groups[1].Value
}

function Download-WebView2Standalone {
    Assert-Path $PackagingDir "Packaging directory"
    $vendorDir = Split-Path -Parent $WebView2Setup
    New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
    $needsDownload = -not (Test-Path -LiteralPath $WebView2Setup)
    if (-not $needsDownload) {
        $existingHash = (Get-FileHash -LiteralPath $WebView2Setup -Algorithm SHA256).Hash
        $needsDownload = $existingHash -ne $WebView2Sha256
    }
    if ($needsDownload) {
        Write-Host "Downloading the Microsoft WebView2 x64 offline installer..."
        $downloaded = $false
        for ($attempt = 1; $attempt -le 2; $attempt++) {
            if ($attempt -eq 2 -and (Test-Path -LiteralPath $WebView2Setup)) {
                Remove-Item -LiteralPath $WebView2Setup -Force
            }
            $resumeArgs = if ($attempt -eq 1 -and (Test-Path -LiteralPath $WebView2Setup)) {
                @("-C", "-")
            } else {
                @()
            }
            & curl.exe -L --fail --retry 8 --retry-all-errors --retry-delay 2 --connect-timeout 30 @resumeArgs --output $WebView2Setup $WebView2Url
            if ($LASTEXITCODE -eq 0) {
                $downloaded = $true
                break
            }
            Write-Warning "WebView2 download attempt $attempt failed with curl exit code $LASTEXITCODE"
        }
        if (-not $downloaded) {
            throw "Could not download the WebView2 x64 offline installer"
        }
    }
    $hash = (Get-FileHash -LiteralPath $WebView2Setup -Algorithm SHA256).Hash
    if ($hash -ne $WebView2Sha256) {
        throw "WebView2 installer SHA256 mismatch: $hash"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $WebView2Setup
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft") {
        throw "WebView2 installer signature validation failed: $($signature.Status)"
    }
    return $WebView2Setup
}

function Build-PythonRuntime {
    Assert-Path $VenvPython "Virtual environment Python"
    Assert-Path $PyvenvConfig "pyvenv.cfg"

    $configText = Get-Content -Raw -LiteralPath $PyvenvConfig
    $homeMatch = [regex]::Match($configText, '(?m)^home\s*=\s*(.+)$')
    if (-not $homeMatch.Success) {
        throw "Could not determine the base Python installation from pyvenv.cfg"
    }
    $BasePython = $homeMatch.Groups[1].Value.Trim()
    Assert-Path (Join-Path $BasePython "python.exe") "Base Python"

    $RuntimeDir = Join-Path $PayloadDir "python-runtime"
    $RuntimeLib = Join-Path $RuntimeDir "Lib"
    $RuntimeSitePackages = Join-Path $RuntimeLib "site-packages"

    Write-Host "Building self-contained Python runtime..."
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    foreach ($name in @("python.exe", "pythonw.exe", "python3.dll", "python314.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt")) {
        $source = Join-Path $BasePython $name
        Assert-Path $source "Python runtime file"
        Copy-Item -LiteralPath $source -Destination $RuntimeDir -Force
    }

    $excludedStdlib = @(
        (Join-Path $BasePython "Lib\site-packages"),
        "__pycache__"
    )
    Copy-RobocopyTree -Source (Join-Path $BasePython "Lib") -Destination $RuntimeLib -ExcludedDirectories $excludedStdlib
    Copy-RobocopyTree -Source (Join-Path $BasePython "DLLs") -Destination (Join-Path $RuntimeDir "DLLs")

    foreach ($directory in @("test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip", "venv", "__pycache__")) {
        $path = Join-Path $RuntimeLib $directory
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }

    New-Item -ItemType Directory -Force -Path $RuntimeSitePackages | Out-Null
    foreach ($name in @("yaml", "_yaml", "openpyxl", "et_xmlfile", "PyYAML-6.0.2.dist-info", "openpyxl-3.1.5.dist-info", "et_xmlfile-2.0.0.dist-info")) {
        $source = Join-Path $VenvSitePackages $name
        Assert-Path $source "Required Python package"
        Copy-Item -LiteralPath $source -Destination $RuntimeSitePackages -Recurse -Force
    }

    $runtimePython = Join-Path $RuntimeDir "python.exe"
    & $runtimePython -I -c "import sys, yaml, openpyxl; print(sys.version); print(yaml.__version__); print(openpyxl.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "The staged Python runtime failed its import check"
    }
    return $RuntimeDir
}

function Build-Payload {
    Write-Host "Staging Prisma application files..."
    Copy-Item -LiteralPath (Join-Path $TauriRoot "target\release\Prisma.exe") -Destination $PayloadDir -Force
    foreach ($name in @("api", "common", "gui", "keywords", "run_prisma", "software", "config", "data", "inputs", "software-library")) {
        $source = Join-Path $AppRoot $name
        Assert-Path $source "Application resource"
        Copy-RobocopyTree -Source $source -Destination (Join-Path $PayloadDir $name) -ExcludedDirectories @("__pycache__")
    }
    foreach ($file in @("project_store.py", "requirements.txt", "README.md")) {
        $source = Join-Path $AppRoot $file
        Assert-Path $source "Application file"
        Copy-Item -LiteralPath $source -Destination $PayloadDir -Force
    }
    foreach ($doc in Get-ChildItem -LiteralPath $AppRoot -File -Filter "*.md" |
            Where-Object { $_.Name -ne "README.md" }) {
        Copy-Item -LiteralPath $doc.FullName -Destination $PayloadDir -Force
    }

    # Bundle clean starter projects, but never package developer logs or results.
    $projectsSource = Join-Path $AppRoot "projects"
    $projectsDestination = Join-Path $PayloadDir "projects"
    New-Item -ItemType Directory -Force -Path $projectsDestination | Out-Null
    if (Test-Path -LiteralPath $projectsSource) {
        foreach ($project in Get-ChildItem -LiteralPath $projectsSource -Directory) {
            Copy-RobocopyTree -Source $project.FullName -Destination (Join-Path $projectsDestination $project.Name) -ExcludedDirectories @("test_cases", "log", "exports", "__pycache__")
        }
    }
    $activeFile = Join-Path $projectsSource ".active"
    if (Test-Path -LiteralPath $activeFile) {
        Copy-Item -LiteralPath $activeFile -Destination $projectsDestination -Force
    }

    # Include the legacy sample paths used by older scripts.
    foreach ($name in @("log", "test_cases", "exports")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $PayloadDir $name) | Out-Null
    }
}

function Stop-StagedApp {
    $releaseExe = [System.IO.Path]::GetFullPath((Join-Path $TauriRoot "target\release\Prisma.exe"))
    Get-Process -Name "Prisma" -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            if ($_.Path -and ([System.IO.Path]::GetFullPath($_.Path) -eq $releaseExe)) {
                Write-Host "Stopping development Prisma.exe before rebuilding (PID $($_.Id))..."
                Stop-Process -Id $_.Id -Force
                $_.WaitForExit(5000)
            }
        } catch {
            Write-Warning "Could not stop an existing Prisma process: $($_.Exception.Message)"
        }
    }
}

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Get-ConfiguredVersion
}
$VersionParts = @($Version.Split(".") | ForEach-Object { [int]$_ })
while ($VersionParts.Count -lt 4) {
    $VersionParts += 0
}
$VersionQuad = ($VersionParts[0..3] -join ".")

Assert-Path $NsiFile "NSIS script"
$NsisExe = Resolve-NsisCompiler -ConfiguredPath $NsisExe
Assert-Path $NsisExe "NSIS compiler"
Assert-Path $IconFile "Application icon"
Assert-Path $VenvPython "Virtual environment Python"
$WebView2Setup = Download-WebView2Standalone

if (-not $SkipCargoBuild) {
    Stop-StagedApp
    Write-Host "Building Prisma desktop executable..."
    Push-Location $TauriRoot
    try {
        & cargo build --release --offline
        if ($LASTEXITCODE -ne 0) {
            throw "cargo build failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

Reset-StagingDirectory
$runtimeDir = Build-PythonRuntime
Build-Payload

$runtimePython = Join-Path $PayloadDir "python-runtime\python.exe"
Push-Location $PayloadDir
try {
    # Import the complete backend before NSIS runs so missing runtime modules are
    # caught during packaging rather than after installation.
    & $runtimePython -s -c "import api.server, yaml, openpyxl; print('backend imports ok')"
    if ($LASTEXITCODE -ne 0) {
        throw "The staged application failed its backend import check"
    }
} finally {
    Pop-Location
}

$outputFile = Join-Path $OutputDir ("Prisma-Setup-{0}-x64.exe" -f $Version)
Write-Host "Compiling offline installer..."
& $NsisExe `
    "/DAPP_VERSION=$Version" `
    "/DAPP_VERSION_QUAD=$VersionQuad" `
    "/DPAYLOAD_DIR=$PayloadDir" `
    "/DOUTPUT_FILE=$outputFile" `
    "/DWEBVIEW2_SETUP=$WebView2Setup" `
    "/DICON_FILE=$IconFile" `
    $NsiFile
if ($LASTEXITCODE -ne 0) {
    throw "NSIS compilation failed with exit code $LASTEXITCODE"
}
Assert-Path $outputFile "Installer"

$installer = Get-Item -LiteralPath $outputFile
$hash = Get-FileHash -LiteralPath $outputFile -Algorithm SHA256
$checksumFile = "$outputFile.sha256"
Set-Content -LiteralPath $checksumFile -Value ("{0}  {1}" -f $hash.Hash.ToLowerInvariant(), $installer.Name) -Encoding ascii

Write-Host ""
Write-Host "Installer created:"
Write-Host "  $($installer.FullName)"
Write-Host ("  Size: {0:N1} MB" -f ($installer.Length / 1MB))
Write-Host "  SHA256: $($hash.Hash)"
Write-Host "  Checksum: $checksumFile"
