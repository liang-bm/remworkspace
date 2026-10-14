$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:REM_HOME = $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "app"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

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
& $Python -m remcompanion.desktop
