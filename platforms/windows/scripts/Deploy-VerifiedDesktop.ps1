param(
    [string]$TargetRoot = '',
    [string]$PackageDirectory = '',
    [string]$SourceRoot = '',
    [string[]]$SourceRelativePaths = @(),
    [switch]$PlanOnly
)

# Publish a verified onedir build. User config/data/models are never copied.
# Motion/emoji assets require an explicit, tightly scoped opt-in list; other assets stay untouched.
# Existing program files are backed up first; no recursive deletion is used.
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($TargetRoot)) {
    throw 'Specify -TargetRoot for the existing application project to update.'
}
if (-not $SourceRoot) { $SourceRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) }
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$TargetRoot = (Resolve-Path -LiteralPath $TargetRoot).Path
if (-not $PackageDirectory) { $PackageDirectory = Join-Path $SourceRoot 'dist\RemChat' }
$PackageDirectory = (Resolve-Path -LiteralPath $PackageDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $TargetRoot 'config\rem_companion.toml') -PathType Leaf)) {
    throw 'Target must be an existing RemCompanion project, not a drive or workspace root.'
}
if (-not (Test-Path -LiteralPath (Join-Path $PackageDirectory 'RemChat.exe') -PathType Leaf)) {
    throw 'Verified RemChat.exe package was not found.'
}
function Assert-OnedirPackage([string]$Directory) {
    $Runtime = Join-Path $Directory '_internal'
    if (-not (Test-Path -LiteralPath $Runtime -PathType Container) -or
        -not (Get-ChildItem -LiteralPath $Runtime -Filter 'python3*.dll' -File | Where-Object { $_.Name -match '^python3[0-9]+\.dll$' }) -or
        -not (Test-Path -LiteralPath (Join-Path $Runtime 'base_library.zip') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $Runtime '_tkinter.pyd') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $Runtime '_tcl_data\init.tcl') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $Runtime '_tk_data\tk.tcl') -PathType Leaf)) {
        throw "Incomplete Windows onedir package: $Directory"
    }
}
Assert-OnedirPackage $PackageDirectory

function Assert-ProjectPath([string]$Path) {
    $Absolute = [IO.Path]::GetFullPath($Path)
    $Prefix = $TargetRoot.TrimEnd('\') + '\'
    if (-not $Absolute.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside project: $Absolute"
    }
    $Cursor = $Absolute
    while ($Cursor -and $Cursor.Length -ge $TargetRoot.Length) {
        if (Test-Path -LiteralPath $Cursor) {
            if ((Get-Item -LiteralPath $Cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing linked destination: $Cursor"
            }
        }
        $Cursor = Split-Path -Parent $Cursor
    }
    return $Absolute
}

$DesktopDirectory = Assert-ProjectPath (Join-Path $TargetRoot 'desktop-app')
$Running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -and $_.Path.StartsWith($DesktopDirectory + '\', [StringComparison]::OrdinalIgnoreCase) }
    catch { $false }
})
if ($Running.Count) { throw 'Close the existing desktop application before deployment.' }

$Sources = @()
$SourceFolders = @('app', 'docs', 'scripts', 'tests', 'platforms\windows', 'platforms\linux', 'config\defaults')
# Verification records are opt-in and still subject to the exact patch list.
if (@($SourceRelativePaths | Where-Object { $_.Replace('/', '\').StartsWith('artifacts\verification\', [StringComparison]::OrdinalIgnoreCase) }).Count) {
    $SourceFolders += 'artifacts\verification'
}
foreach ($OptionalFolder in @('tools','sources')) {
    if (@($SourceRelativePaths | Where-Object { $_.Replace('/', '\').StartsWith($OptionalFolder + '\', [StringComparison]::OrdinalIgnoreCase) }).Count) {
        $SourceFolders += $OptionalFolder
    }
}
foreach ($Folder in $SourceFolders) {
    $Sources += Get-ChildItem -LiteralPath (Join-Path $SourceRoot $Folder) -File -Recurse |
        Where-Object {
            $_.FullName -notmatch '[\\/](__pycache__|assets|runtime|cache|data|models|background|tmp|dist|build)[\\/]' -and
            $_.Extension -in @('.md', '.py', '.ps1', '.sh', '.cmd', '.spec', '.iss', '.json', '.toml', '.txt', '.log') -and
            $_.Name -notlike 'verify_*' -and
            ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
        }
}
$Sources += Get-ChildItem -LiteralPath $SourceRoot -File -Force | Where-Object {
    $_.Extension -in @('.md', '.py', '.ps1', '.sh', '.cmd', '.spec') -or $_.Name -eq '.gitignore'
}
$Sources += Get-ChildItem -LiteralPath (Join-Path $SourceRoot 'platforms') -File | Where-Object {
    $_.Extension -eq '.md' -and ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
}
# New authored motion frames and pinned licensed emoji resources, not the
# user's background, idle, model or custom action library. Exact paths only.
foreach ($Relative in $SourceRelativePaths) {
    $Relative = $Relative.Replace('/', '\')
    $MotionAsset = $Relative -match '^app\\remcompanion\\assets\\pet\\(?:actions|rendered\\(?:075|100|125|150))\\(?:dozing|waking|strolling-left|strolling-right)\\(?:dozing|waking|strolling-left|strolling-right)_[0-9]{2}\.png$'
    $EmojiAsset = $Relative -match '^app\\remcompanion\\assets\\emoji\\(?:[0-9a-f]+(?:-[0-9a-f]+)*\.png|manifest\.json|ATTRIBUTION\.md|LICENSE-GRAPHICS\.txt)$'
    $FarmAsset = $Relative -match '^app\\remcompanion\\assets\\farm\\(?:(?:tomato|corn|pumpkin|strawberry|sunflower)-[0-2]\.png|crop-expansion\.json)$'
    if (-not ($MotionAsset -or $EmojiAsset -or $FarmAsset)) { continue }
    $AssetPath = Join-Path $SourceRoot $Relative
    $Cursor = $AssetPath
    while ($Cursor -and $Cursor.Length -ge $SourceRoot.Length) {
        if ((Test-Path -LiteralPath $Cursor) -and ((Get-Item -LiteralPath $Cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing linked motion source: $Cursor"
        }
        $Cursor = Split-Path -Parent $Cursor
    }
    $Sources += Get-Item -LiteralPath $AssetPath
}
if ($SourceRelativePaths.Count) {
    # A focused patch cannot silently publish unrelated source/OS changes.
    $AllowedPaths = @($SourceRelativePaths | ForEach-Object { $_.Replace('/', '\') } | Select-Object -Unique)
    $Sources = @($Sources | Where-Object { $_.FullName.Substring($SourceRoot.Length + 1) -in $AllowedPaths })
    $FoundPaths = @($Sources | ForEach-Object { $_.FullName.Substring($SourceRoot.Length + 1) })
    foreach ($Relative in $AllowedPaths) {
        if ($Relative -notin $FoundPaths) { throw "Patch source is missing or outside the allowed source scope: $Relative" }
    }
}
$Changes = @()
foreach ($Source in $Sources) {
    $Relative = $Source.FullName.Substring($SourceRoot.Length + 1)
    $Destination = Assert-ProjectPath (Join-Path $TargetRoot $Relative)
    $NewHash = (Get-FileHash -LiteralPath $Source.FullName -Algorithm SHA256).Hash
    $OldHash = if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
    } else { $null }
    if ($OldHash -ne $NewHash) {
        $Changes += [pscustomobject]@{ relative_path=$Relative; source=$Source.FullName;
            destination=$Destination; old_sha256=$OldHash; new_sha256=$NewHash }
    }
}
if ($PlanOnly) {
    $Changes | Select-Object relative_path,old_sha256,new_sha256 | ConvertTo-Json -Depth 4
    return
}

$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupRoot = Assert-ProjectPath (Join-Path $TargetRoot "backups\remchat-$Stamp")
$PackageStage = Assert-ProjectPath (Join-Path $DesktopDirectory ".remchat-stage-$Stamp")
if ((Test-Path -LiteralPath $BackupRoot) -or (Test-Path -LiteralPath $PackageStage)) {
    throw 'Release timestamp collision; existing backup/stage will not be overwritten.'
}
New-Item -ItemType Directory -Path $BackupRoot,$PackageStage -Force | Out-Null
foreach ($Item in Get-ChildItem -LiteralPath $PackageDirectory -Force) {
    Copy-Item -LiteralPath $Item.FullName -Destination $PackageStage -Recurse
}
Assert-OnedirPackage $PackageStage
$StagedExe = Join-Path $PackageStage 'RemChat.exe'
$PackageHash = (Get-FileHash -LiteralPath $StagedExe -Algorithm SHA256).Hash
if ($PackageHash -ne (Get-FileHash -LiteralPath (Join-Path $PackageDirectory 'RemChat.exe') -Algorithm SHA256).Hash) {
    throw 'Staged executable hash does not match the verified package.'
}

$Manifest = [ordered]@{ version=1; deployed_at=(Get-Date).ToString('o'); source=$SourceRoot;
    target=$TargetRoot; backup=$BackupRoot; exe_sha256=$PackageHash; changes=$Changes; status='prepared' }
$ManifestPath = Join-Path $BackupRoot 'deployment-manifest.json'
$Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8

$AppliedChanges = New-Object 'System.Collections.Generic.List[object]'
function Restore-SourceFiles {
    for ($Index = $AppliedChanges.Count - 1; $Index -ge 0; $Index--) {
        $Change = $AppliedChanges[$Index]
        $Backup = Assert-ProjectPath (Join-Path $BackupRoot ('files\' + $Change.relative_path))
        if ($Change.old_sha256) {
            Copy-Item -LiteralPath $Backup -Destination $Change.destination -Force
        } elseif (Test-Path -LiteralPath $Change.destination -PathType Leaf) {
            $NewFile = Assert-ProjectPath (Join-Path $BackupRoot ('failed-new-files\' + $Change.relative_path))
            New-Item -ItemType Directory -Path (Split-Path -Parent $NewFile) -Force | Out-Null
            Move-Item -LiteralPath $Change.destination -Destination $NewFile
        }
    }
}
try {
    foreach ($Change in $Changes) {
        $CurrentHash = if (Test-Path -LiteralPath $Change.destination -PathType Leaf) {
            (Get-FileHash -LiteralPath $Change.destination -Algorithm SHA256).Hash
        } else { $null }
        if ($CurrentHash -ne $Change.old_sha256) {
            throw "Target changed since preflight; preserving it: $($Change.relative_path)"
        }
        if ($Change.old_sha256) {
            $Backup = Assert-ProjectPath (Join-Path $BackupRoot ('files\' + $Change.relative_path))
            New-Item -ItemType Directory -Path (Split-Path -Parent $Backup) -Force | Out-Null
            Copy-Item -LiteralPath $Change.destination -Destination $Backup
        }
        $AppliedChanges.Add($Change)
        New-Item -ItemType Directory -Path (Split-Path -Parent $Change.destination) -Force | Out-Null
        Copy-Item -LiteralPath $Change.source -Destination $Change.destination -Force
        if ((Get-FileHash -LiteralPath $Change.destination -Algorithm SHA256).Hash -ne $Change.new_sha256) {
            throw "Source deployment hash mismatch: $($Change.relative_path)"
        }
    }
} catch {
    $SourceError = $_
    Restore-SourceFiles
    $Manifest.status = 'source-rolled-back'
    $Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
    throw $SourceError
}

$OldInternalMoved = $false
$NewInternalMoved = $false
$BundleReplacementStarted = $false
try {
    $BundleBackup = Assert-ProjectPath (Join-Path $BackupRoot 'desktop-app')
    New-Item -ItemType Directory -Path $BundleBackup -Force | Out-Null
    foreach ($Name in @('RemChat.exe', 'RemCompanion.exe')) {
        $OldExe = Assert-ProjectPath (Join-Path $DesktopDirectory $Name)
        if (Test-Path -LiteralPath $OldExe -PathType Leaf) {
            Copy-Item -LiteralPath $OldExe -Destination (Join-Path $BundleBackup $Name)
        }
    }
    $Internal = Assert-ProjectPath (Join-Path $DesktopDirectory '_internal')
    $InternalBackup = Assert-ProjectPath (Join-Path $BundleBackup '_internal')
    $StagedInternal = Assert-ProjectPath (Join-Path $PackageStage '_internal')
    $BundleReplacementStarted = $true
    if (Test-Path -LiteralPath $Internal -PathType Container) {
        Move-Item -LiteralPath $Internal -Destination $InternalBackup
        $OldInternalMoved = $true
    }
    Move-Item -LiteralPath $StagedInternal -Destination $Internal
    $NewInternalMoved = $true
    Copy-Item -LiteralPath $StagedExe -Destination (Join-Path $DesktopDirectory 'RemChat.exe') -Force
    # Existing shortcuts and the old direct EXE path load the same new build.
    Copy-Item -LiteralPath $StagedExe -Destination (Join-Path $DesktopDirectory 'RemCompanion.exe') -Force
    foreach ($Name in @('RemChat.exe', 'RemCompanion.exe')) {
        if ((Get-FileHash -LiteralPath (Join-Path $DesktopDirectory $Name) -Algorithm SHA256).Hash -ne $PackageHash) {
            throw "Published executable hash mismatch: $Name"
        }
    }
} catch {
    $PublishError = $_
    # Recover the old bundle; preserve failed new files in our release stage.
    if ($BundleReplacementStarted) {
        if ($NewInternalMoved) { Move-Item -LiteralPath $Internal -Destination $StagedInternal }
        if ($OldInternalMoved) { Move-Item -LiteralPath $InternalBackup -Destination $Internal }
        foreach ($Name in @('RemChat.exe', 'RemCompanion.exe')) {
            $Published = Assert-ProjectPath (Join-Path $DesktopDirectory $Name)
            $Previous = Assert-ProjectPath (Join-Path $BundleBackup $Name)
            if (Test-Path -LiteralPath $Previous -PathType Leaf) {
                Copy-Item -LiteralPath $Previous -Destination $Published -Force
            } elseif (Test-Path -LiteralPath $Published -PathType Leaf) {
                Move-Item -LiteralPath $Published -Destination (Assert-ProjectPath (Join-Path $PackageStage "failed-$Name"))
            }
        }
    }
    Restore-SourceFiles
    $Manifest.status = 'bundle-rolled-back'
    $Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
    throw $PublishError
}
$Manifest.status = 'deployed'
$Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
Write-Output "Deployed $($Changes.Count) changed source/doc files."
Write-Output "Application: $(Join-Path $DesktopDirectory 'RemChat.exe')"
Write-Output "Recoverable backup: $BackupRoot"
Write-Output "Package staging retained for safe cleanup: $PackageStage"
