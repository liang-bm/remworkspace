$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$BuildPython = Join-Path $ProjectRoot "runtime\desktop-build\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    throw "Desktop build environment is missing: $BuildPython"
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $ProjectRoot "cache\pyinstaller"
Set-Location $ProjectRoot
& $BuildPython -m PyInstaller --clean --noconfirm "platforms\windows\RemCompanion.spec"
exit $LASTEXITCODE
