$ErrorActionPreference = "Stop"

# Keep all third-party caches under the project root.
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:REM_HOME = $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "app"
$env:HF_HOME = Join-Path $ProjectRoot "cache\huggingface"
$env:HUGGINGFACE_HUB_CACHE = Join-Path $ProjectRoot "cache\huggingface\hub"
$env:TORCH_HOME = Join-Path $ProjectRoot "cache\torch"
$env:PIP_CACHE_DIR = Join-Path $ProjectRoot "cache\pip"
$env:UV_CACHE_DIR = Join-Path $ProjectRoot "cache\uv"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot "runtime\uv-python"
$env:TEMP = Join-Path $ProjectRoot "tmp"
$env:TMP = Join-Path $ProjectRoot "tmp"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$RequiredDirs = @(
    $env:HF_HOME,
    $env:HUGGINGFACE_HUB_CACHE,
    $env:TORCH_HOME,
    $env:PIP_CACHE_DIR,
    $env:UV_CACHE_DIR,
    $env:UV_PYTHON_INSTALL_DIR,
    $env:TEMP
)
foreach ($Dir in $RequiredDirs) {
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
}

$PythonCandidates = @(
    (Join-Path $ProjectRoot "runtime\control\Scripts\python.exe"),
    "py.exe",
    "python.exe"
)
$Python = $null
foreach ($Candidate in $PythonCandidates) {
    if (Test-Path -LiteralPath $Candidate) {
        $Python = $Candidate
        break
    }
    $Command = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($Command) {
        $Python = $Command.Source
        break
    }
}
if (-not $Python) {
    throw "Python was not found. Install Python 3.11 or 3.12 and retry."
}

Set-Location $ProjectRoot
& $Python -m remcompanion.control_center
exit $LASTEXITCODE
