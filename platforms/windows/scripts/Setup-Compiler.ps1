param([string]$ProjectRoot='', [string]$DownloadPython='')
# Opt-in, project-local compiler preparation. No PATH/registry/association changes.
$ErrorActionPreference='Stop'
if(-not $ProjectRoot){$ProjectRoot=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))}
$ProjectRoot=(Resolve-Path -LiteralPath $ProjectRoot).Path
if(-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config\rem_companion.toml') -PathType Leaf)){throw 'Choose an existing RemChat project root'}
function Project-Path([string]$Relative){
    $Path=[IO.Path]::GetFullPath((Join-Path $ProjectRoot $Relative))
    if(-not $Path.StartsWith($ProjectRoot.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Tool path escapes project'}
    $Cursor=$Path
    while($Cursor -and $Cursor.Length -ge $ProjectRoot.Length){
        if((Test-Path -LiteralPath $Cursor) -and ((Get-Item -LiteralPath $Cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw "Linked tool path: $Cursor"}
        $Cursor=Split-Path -Parent $Cursor
    }
    return $Path
}
$Directory=Project-Path 'runtime\windows-tools\inno-6.7.3'
$Compiler=Join-Path $Directory 'ISCC.exe'
$CompilerHash='0A8757031B33777E4C9CBFFEE40F11A5062B36D25CBE144C1DB73B6102B80AD7'
if(Test-Path -LiteralPath $Directory){
    if((Test-Path -LiteralPath $Compiler -PathType Leaf) -and (Get-FileHash -LiteralPath $Compiler -Algorithm SHA256).Hash -eq $CompilerHash){Write-Output $Compiler; return}
    throw "Existing incomplete/modified compiler preserved: $Directory"
}
$Cache=Project-Path 'cache\windows-tools'
$Installer=Join-Path $Cache 'innosetup-6.7.3.exe'
$InstallerHash='9C73C3BAE7ED48D44112A0F48E66742C00090BDB5BEF71D9D3C056C66E97B732'
New-Item -ItemType Directory -Path $Cache -Force | Out-Null
if(-not (Test-Path -LiteralPath $Installer -PathType Leaf)){
    $Temporary=Join-Path $Cache ('download-'+[guid]::NewGuid().ToString('N')+'.tmp')
    $Url='https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
    if(-not $DownloadPython){
        $Candidate=Join-Path $ProjectRoot 'runtime\desktop-build\Scripts\python.exe'
        if(Test-Path -LiteralPath $Candidate -PathType Leaf){$DownloadPython=$Candidate}
    }
    if($DownloadPython){
        if(-not (Test-Path -LiteralPath $DownloadPython -PathType Leaf)){throw 'Download Python does not exist'}
        & $DownloadPython -c 'import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' $Url $Temporary
        if($LASTEXITCODE -ne 0){throw "Download failed; partial file preserved: $Temporary"}
    } else {
        $OldProtocol=[Net.ServicePointManager]::SecurityProtocol
        try{[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri $Url -OutFile $Temporary -UseBasicParsing}
        finally{[Net.ServicePointManager]::SecurityProtocol=$OldProtocol}
    }
    if((Get-FileHash -LiteralPath $Temporary -Algorithm SHA256).Hash -ne $InstallerHash){throw "Unverified download preserved: $Temporary"}
    Move-Item -LiteralPath $Temporary -Destination $Installer
}
if((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash -ne $InstallerHash){throw "Existing download hash mismatch; preserved: $Installer"}
$Signature=Get-AuthenticodeSignature -LiteralPath $Installer
if($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'CN=Pyrsys B\.V\.') {throw 'Compiler installer signature/publisher verification failed'}
$Logs=Project-Path 'logs'
New-Item -ItemType Directory -Path $Logs -Force | Out-Null
$Log=Join-Path $Logs ('compiler-portable-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
$Arguments=@('/PORTABLE=1','/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/NOICONS',('/DIR="'+$Directory+'"'),('/LOG="'+$Log+'"'))
$TaskTemp=Project-Path 'tmp\windows-tools'
New-Item -ItemType Directory -Path $TaskTemp -Force | Out-Null
$PreviousTemp=$env:TEMP
$PreviousTmp=$env:TMP
try {
    $env:TEMP=$TaskTemp
    $env:TMP=$TaskTemp
    $Child=Start-Process -FilePath $Installer -ArgumentList $Arguments -WindowStyle Hidden -PassThru -Wait
} finally {
    $env:TEMP=$PreviousTemp
    $env:TMP=$PreviousTmp
}
if($Child.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Compiler -PathType Leaf) -or
   (Get-FileHash -LiteralPath $Compiler -Algorithm SHA256).Hash -ne $CompilerHash){throw "Portable compiler preparation failed; inspect $Log"}
Write-Output $Compiler
