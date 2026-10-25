; Compiled only through build-windows.ps1; VERSION.json supplies these values.
#if (Ver & 0xFFFFFF00) != 0x06070300
  #error Use the pinned Inno Setup 6.7.3 from scripts/setup-inno.ps1
#endif
#ifndef PackageDirectory
  #error PackageDirectory is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef BuildNumber
  #error BuildNumber is required
#endif
#ifndef SourceHash
  #error SourceHash is required
#endif
#ifndef BundleHash
  #error BundleHash is required
#endif

[Setup]
AppId={{754B2DF4-3A18-4BE2-B103-6F6437765B2B}
AppName=PageBridge
AppVersion={#AppVersion}
AppVerName=PageBridge {#AppVersion}
AppPublisher=PageBridge contributors
AppPublisherURL=https://github.com/txp666/PageBridge
AppSupportURL=https://github.com/txp666/PageBridge/issues
AppUpdatesURL=https://github.com/txp666/PageBridge/releases
DefaultDirName={localappdata}\Programs\PageBridge
DefaultGroupName=PageBridge
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#OutputDirectory}
OutputBaseFilename=PageBridge-{#AppVersion}-windows-x64-setup
SetupIconFile={#PackageDirectory}\Assets\PageBridge.ico
UninstallDisplayIcon={app}\PageBridge.Windows.exe
LicenseFile={#PackageDirectory}\resources\LICENSE
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
VersionInfoVersion={#AppVersion}.{#BuildNumber}
VersionInfoProductVersion={#AppVersion}.{#BuildNumber}
VersionInfoProductTextVersion={#AppVersion}+build.{#BuildNumber}
; Inno loader resource slots are 60 chars; URL-safe base64 keeps every hash bit.
VersionInfoDescription=PageBridge src={#SourceHash}
VersionInfoProductName=PageBridge pkg={#BundleHash}
VersionInfoOriginalFileName=PageBridge-{#AppVersion}-windows-x64-setup.exe
UninstallLogMode=append

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PackageDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PageBridge"; Filename: "{app}\PageBridge.Windows.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\PageBridge"; Filename: "{app}\PageBridge.Windows.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\PageBridge.Windows.exe"; Description: "{cm:LaunchProgram,PageBridge}"; Flags: nowait postinstall skipifsilent

; No UninstallDelete/InstallDelete wildcard: documents, settings and credentials
; live outside {app}; uninstall removes only files tracked by this installer.
