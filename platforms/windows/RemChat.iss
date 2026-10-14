; Independent Windows Setup template. No user data/models/voices are packaged.
#ifndef ReleaseRoot
  #error Supply /DReleaseRoot with a reviewed public release payload
#endif
#ifndef AppVersion
  #error Supply /DAppVersion
#endif
#ifndef OutputDirectory
  #error Supply /DOutputDirectory
#endif
#if !FileExists(ReleaseRoot + "\desktop-app\RemChat.exe")
  #error Verified Windows desktop package is missing
#endif
#if !FileExists(ReleaseRoot + "\config\rem_companion.toml")
  #error Reviewed public default configuration is missing
#endif

; Isolated acceptance installs never share the production uninstall/shortcut ID.
#ifdef VerificationBuild
  #define ProductName "RemChat Setup Verification"
  #define ProductId "{{3591F944-302C-4D07-B8F5-737D5CA45734}"
  #ifdef PrivateLocalBuild
    #define ArtifactSuffix "local-verification-setup"
  #else
    #define ArtifactSuffix "verification-setup"
  #endif
#elif defined(PrivateLocalBuild)
  #define ProductName "RemChat Local Build"
  #define ProductId "{{8D2BACF5-6B4C-4918-9BBC-BD090D9E6F26}"
  #define ArtifactSuffix "local-setup"
#else
  #define ProductName "RemChat"
  #define ProductId "{{EAD40547-4332-40A7-A3AF-52A481897D12}"
  #define ArtifactSuffix "setup"
#endif

[Setup]
AppId={#ProductId}
AppName={#ProductName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\{#ProductName}
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDirectory}
OutputBaseFilename=RemChat-{#AppVersion}-windows-x64-{#ArtifactSuffix}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\desktop-app\RemChat.exe
CloseApplications=no
RestartApplications=no
; Do not replace libraries while the shared desktop instance owns them.
AppMutex=Local\RemCompanionUI

#ifdef PrivateLocalBuild
[Messages]
WelcomeLabel2=This is a PRIVATE LOCAL build, not a public redistribution package.%n%nExisting character artwork and dependency redistribution rights have not been approved by this build. Do not upload this installer to a public Release.
#endif

[Files]
Source: "{#ReleaseRoot}\desktop-app\RemChat.exe"; DestDir: "{app}\desktop-app"; Flags: ignoreversion
Source: "{#ReleaseRoot}\desktop-app\_internal\*"; DestDir: "{app}\desktop-app\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ReleaseRoot}\config\rem_companion.toml"; DestDir: "{app}\config"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#ReleaseRoot}\config\release.json"; DestDir: "{app}\config"; Flags: ignoreversion
#ifdef PrivateLocalBuild
Source: "{#ReleaseRoot}\local-build-review.json"; DestDir: "{app}"; Flags: ignoreversion
#else
Source: "{#ReleaseRoot}\public-release-review.json"; DestDir: "{app}"; Flags: ignoreversion
#endif
Source: "{#ReleaseRoot}\README*.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseRoot}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#ProductName}"; Filename: "{app}\desktop-app\RemChat.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\desktop-app\RemChat.exe"; WorkingDir: "{app}"; Description: "Launch RemChat"; Flags: nowait postinstall skipifsilent
