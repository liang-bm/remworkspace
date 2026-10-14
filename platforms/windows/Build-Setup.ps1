param(
    [Parameter(Mandatory = $true)][string]$ReleaseRoot,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?$')][string]$Version,
    [string]$IsccPath = '',
    [string]$OutputDirectory = '',
    [switch]$VerificationBuild,
    [switch]$PrivateLocalBuild
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot).Path
foreach ($Name in @('data', 'models', 'runtime', 'engines', 'background', 'rem-voice', 'rem-voice2', 'resources')) {
    if (Test-Path -LiteralPath (Join-Path $ReleaseRoot $Name)) {
        throw "Refusing a payload containing user/project content: $Name. Build an isolated public release payload."
    }
}
foreach ($Path in @($ReleaseRoot, (Join-Path $ReleaseRoot 'desktop-app'), (Join-Path $ReleaseRoot 'config'))) {
    if ((Test-Path -LiteralPath $Path) -and ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Linked release payload is not supported: $Path"
    }
}
if (Get-ChildItem -LiteralPath (Join-Path $ReleaseRoot 'desktop-app') -Recurse -Force -ErrorAction Stop |
    Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
    throw 'Linked content in desktop package is not supported.'
}
if (-not (Test-Path -LiteralPath (Join-Path $ReleaseRoot 'desktop-app\RemChat.exe') -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $ReleaseRoot 'desktop-app\_internal\base_library.zip') -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $ReleaseRoot 'config\rem_companion.toml') -PathType Leaf)) {
    throw 'ReleaseRoot must contain a verified Windows desktop-app bundle and a reviewed PUBLIC default config. Never use the live user project as ReleaseRoot.'
}
if ((Get-Item -LiteralPath (Join-Path $ReleaseRoot 'config\rem_companion.toml') -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Linked public configuration is not supported.'
}
$ReviewName = if ($PrivateLocalBuild) { 'local-build-review.json' } else { 'public-release-review.json' }
$ReviewPath = Join-Path $ReleaseRoot $ReviewName
if (-not (Test-Path -LiteralPath $ReviewPath -PathType Leaf)) {
    throw "Missing $ReviewName. Public builds require redistribution review; explicitly private local builds require a hash-bound local-only review."
}
if ((Get-Item -LiteralPath $ReviewPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Linked release review is not supported.'
}
$Review = Get-Content -LiteralPath $ReviewPath -Raw -Encoding UTF8 | ConvertFrom-Json
$ConfigHash = (Get-FileHash -LiteralPath (Join-Path $ReleaseRoot 'config\rem_companion.toml') -Algorithm SHA256).Hash
if ($PrivateLocalBuild) {
    $ExeHash = (Get-FileHash -LiteralPath (Join-Path $ReleaseRoot 'desktop-app\RemChat.exe') -Algorithm SHA256).Hash
    if ($Review.schema -ne 1 -or $Review.local_only -isnot [bool] -or $Review.local_only -ne $true -or
        -not $Review.reviewer -or $Review.config_sha256 -ne $ConfigHash -or $Review.exe_sha256 -ne $ExeHash) {
        throw 'Private build review does not match the exact configuration/executable. This mode grants no public redistribution rights.'
    }
} elseif ($Review.schema -ne 1 -or $Review.config_public -isnot [bool] -or $Review.assets_redistributable -isnot [bool] -or
    $Review.config_public -ne $true -or $Review.assets_redistributable -ne $true -or
    -not $Review.reviewer -or $Review.config_sha256 -ne $ConfigHash) {
    throw 'Public-release review is missing, not approved, or does not match this configuration. Current Rem pet assets/voice data are not automatically licensed for redistribution.'
}
if (-not $IsccPath) {
    $LocalCompiler = Join-Path $ProjectRoot 'runtime\windows-tools\inno-6.7.3\ISCC.exe'
    if (Test-Path -LiteralPath $LocalCompiler -PathType Leaf) { $IsccPath = $LocalCompiler }
    else {
        $Compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($Compiler) { $IsccPath = $Compiler.Source }
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'Inno Setup ISCC.exe is not available. Supply -IsccPath to an already installed compiler; this command will not install system software.'
}
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $ProjectRoot 'dist\windows-setup' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$Suffix = if ($PrivateLocalBuild -and $VerificationBuild) { 'local-verification-setup' }
    elseif ($PrivateLocalBuild) { 'local-setup' }
    elseif ($VerificationBuild) { 'verification-setup' } else { 'setup' }
$Artifact = Join-Path $OutputDirectory "RemChat-$Version-windows-x64-$Suffix.exe"
if ((Test-Path -LiteralPath $Artifact) -or (Test-Path -LiteralPath "$Artifact.sha256")) {
    throw "Existing Setup artifact preserved: $Artifact"
}
$CompilerArguments = @("/DReleaseRoot=$ReleaseRoot", "/DAppVersion=$Version", "/DOutputDirectory=$OutputDirectory")
if ($VerificationBuild) { $CompilerArguments += '/DVerificationBuild' }
if ($PrivateLocalBuild) { $CompilerArguments += '/DPrivateLocalBuild' }
& $IsccPath @CompilerArguments (Join-Path $PSScriptRoot 'RemChat.iss')
if ($LASTEXITCODE -ne 0) { throw "Windows Setup compilation failed: $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) { throw 'Expected Setup artifact is missing.' }
$Hash = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $([IO.Path]::GetFileName($Artifact))" | Set-Content -LiteralPath "$Artifact.sha256" -Encoding ASCII
Write-Output $Artifact
