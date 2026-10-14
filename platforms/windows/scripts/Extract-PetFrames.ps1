param(
    [string]$Atlas = "",
    [string]$Ffmpeg = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
if (-not $Atlas) {
    $Atlas = Join-Path $env:USERPROFILE ".codex\pets\chibi-rem\spritesheet.webp"
}
if (-not $Ffmpeg) {
    $Ffmpeg = Join-Path $ProjectRoot "engines\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
}
$PetAssetDir = Join-Path $ProjectRoot "app\remcompanion\assets\pet"
$OutputDir = Join-Path $PetAssetDir "idle"
$ActionRoot = Join-Path $PetAssetDir "actions"
$IconPath = Join-Path $ProjectRoot "app\remcompanion\assets\pet\rem-companion.ico"
$AppIcon = Join-Path $ProjectRoot "app\remcompanion\assets\icon.png"

if (-not (Test-Path -LiteralPath $Atlas -PathType Leaf)) {
    throw "Pet atlas not found: $Atlas"
}
if (-not (Test-Path -LiteralPath $Ffmpeg -PathType Leaf)) {
    throw "ffmpeg not found: $Ffmpeg"
}
$Specs = @(
    @{ Name = "idle";          Row = 0; Frames = 6 },
    @{ Name = "running-right"; Row = 1; Frames = 8 },
    @{ Name = "running-left";  Row = 2; Frames = 8 },
    @{ Name = "waving";        Row = 3; Frames = 4 },
    @{ Name = "jumping";       Row = 4; Frames = 5 },
    @{ Name = "failed";        Row = 5; Frames = 8 },
    @{ Name = "waiting";       Row = 6; Frames = 6 },
    @{ Name = "running";       Row = 7; Frames = 6 },
    @{ Name = "review";        Row = 8; Frames = 6 }
)

# chibi-rem is an 8x9 v1 atlas with 192x208 cells. Extract every standard
# semantic row so the independent desktop app can reuse Codex's animations.
foreach ($Spec in $Specs) {
    $TargetDir = if ($Spec.Name -eq "idle") {
        $OutputDir
    } else {
        Join-Path $ActionRoot $Spec.Name
    }
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    for ($Index = 0; $Index -lt $Spec.Frames; $Index++) {
        $X = $Index * 192
        $Y = $Spec.Row * 208
        $Target = Join-Path $TargetDir ("{0}_{1:D2}.png" -f $Spec.Name, $Index)
        & $Ffmpeg -y -v error -i $Atlas `
            -vf "crop=192:208:${X}:${Y}" "-frames:v" 1 $Target
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to extract $($Spec.Name) frame $Index"
        }
    }
}

# Build the Windows executable icon from the dedicated Rem application artwork.
if (-not (Test-Path -LiteralPath $AppIcon -PathType Leaf)) {
    throw "Application icon not found: $AppIcon"
}
& $Ffmpeg -y -v error -i $AppIcon `
    -vf "scale=256:256:flags=lanczos" `
    "-frames:v" 1 $IconPath
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create Windows icon"
}

Write-Output "Extracted pet assets to $PetAssetDir"
