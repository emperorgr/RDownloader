# Build RDownloader.exe using the project-local Python toolchain.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.python\python.exe'

if (-not (Test-Path $python)) {
    throw "Local Python not found at $python. Run: .\.python\python.exe -m pip install -r requirements-dev.txt"
}

Push-Location $root
try {
    # PyInstaller logs to stderr; keep that from being treated as a terminating error.
    $ErrorActionPreference = 'Continue'
    & $python -m PyInstaller --noconfirm --clean 'RDownloader.spec'
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { throw "PyInstaller failed with exit code $code" }
} finally {
    Pop-Location
}

$exe = Join-Path $root 'dist\RDownloader.exe'
Write-Host "Built: $exe"
