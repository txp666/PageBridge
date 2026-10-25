[CmdletBinding()]
param([switch]$SkipEngineSetup, [switch]$NoArchive, [switch]$SkipInstaller)
# NoArchive is a developer-only folder build and also skips the installer.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root 'engine\.venv\Scripts\python.exe'
$Project = Join-Path $Root 'windows\PageBridge.Windows\PageBridge.Windows.csproj'
$PackageTool = Join-Path $Root 'scripts\windows_package.py'
$Dotnet = if ($env:PAGEBRIDGE_DOTNET) { $env:PAGEBRIDGE_DOTNET } else { 'dotnet' }

function Invoke-Checked([string]$Command, [string[]]$Arguments) {
    # Windows PowerShell 5 turns redirected native stderr into ErrorRecords.
    # Keep diagnostics in the log, but determine native failure by exit code.
    $PreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Command @Arguments
        $NativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    if ($NativeExitCode -ne 0) { throw "Command failed with exit code ${NativeExitCode}: $Command" }
}

function Move-BundleDirectory([string]$Source, [string]$Target) {
    # Reuse the bounded Windows DLL-lock retry after the private runtime smoke
    # check. A persistent failure leaves the staged directory available.
    $MoveCode = @'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('bundle_builder', sys.argv[1])
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
builder.rename_with_retry(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
'@
    Invoke-Checked $Python @('-c', $MoveCode, (Join-Path $Root 'scripts\build-windows-engine.py'), $Source, $Target)
}

if ($env:OS -ne 'Windows_NT') { throw 'Build this package inside Windows.' }
if (!(Test-Path $Project)) { throw "WinUI project is missing: $Project" }
if (!(Get-Command $Dotnet -ErrorAction SilentlyContinue)) { throw 'Install the .NET 10 SDK, then rerun this script.' }
if (!$SkipEngineSetup) { & (Join-Path $Root 'engine\setup-windows.ps1') }
if (!(Test-Path $Python)) { throw 'Run engine/setup-windows.ps1 first.' }

Invoke-Checked $Python @($PackageTool, 'version', '--root', $Root)
$Version = Get-Content (Join-Path $Root 'VERSION.json') -Raw | ConvertFrom-Json
if (!(Test-Path (Join-Path (Split-Path $Project -Parent) 'packages.lock.json'))) { throw 'The checked-in NuGet dependency lock is required.' }
$PackageName = "PageBridge-$($Version.version)-windows-x64"
$Dist = Join-Path $Root 'dist'
$Stage = Join-Path $Dist ('.PageBridge-windows-x64-staging-' + [Guid]::NewGuid().ToString('N'))
$Final = Join-Path $Dist $PackageName
$Archive = Join-Path $Dist ($PackageName + '.zip')
New-Item -ItemType Directory -Path $Stage -Force | Out-Null

Push-Location $Root
try {
    Invoke-Checked $Dotnet @('publish', $Project, '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true',
        '-p:Platform=x64', '-p:WindowsPackageType=None', '-p:WindowsAppSDKSelfContained=true',
        '-p:PublishSingleFile=false', '-p:PublishTrimmed=false', '-p:RestoreLockedMode=true', '-o', $Stage)
    $ExpectedFileVersion = "$($Version.version).$($Version.build)"
    foreach ($Binary in @('PageBridge.Windows.exe', 'PageBridge.Windows.dll')) {
        $ActualVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $Stage $Binary)).FileVersion
        if ($ActualVersion -ne $ExpectedFileVersion) { throw "Published $Binary version $ActualVersion differs from $ExpectedFileVersion." }
    }
    $Executables = @(@('PageBridge.Windows.exe', 'PageBridge.exe') | Where-Object { Test-Path (Join-Path $Stage $_) })
    if ($Executables.Count -eq 0) { throw 'Publish did not produce the WinUI application executable.' }
    $ApplicationPri = Join-Path $Stage ([IO.Path]::GetFileNameWithoutExtension($Executables[0]) + '.pri')
    if (!(Test-Path $ApplicationPri -PathType Leaf) -or (Get-Item $ApplicationPri).Length -eq 0) {
        throw 'Publish omitted the application PRI resources; this WinUI bundle cannot be shipped.'
    }
    $ApplicationXbf = Join-Path $Stage 'App.xbf'
    if (!(Test-Path $ApplicationXbf -PathType Leaf) -or (Get-Item $ApplicationXbf).Length -eq 0) {
        throw 'Publish omitted the compiled App.xaml resources (App.xbf); this WinUI bundle cannot be shipped.'
    }
    foreach ($PeName in @($Executables[0], 'Microsoft.ui.xaml.dll', 'Microsoft.WindowsAppRuntime.dll')) {
        $PeStream = [IO.File]::OpenRead((Join-Path $Stage $PeName))
        try {
            if ($PeStream.ReadByte() -ne 0x4d -or $PeStream.ReadByte() -ne 0x5a) {
                throw ("Published executable resource is not a PE file: " + $PeName)
            }
        } finally { $PeStream.Dispose() }
    }
    $Resolved = Get-Content (Join-Path (Split-Path $Project -Parent) 'obj\project.assets.json') -Raw | ConvertFrom-Json
    $UnusedSdkPackages = @($Resolved.libraries.PSObject.Properties.Name | Where-Object { $_ -match '^Microsoft\.WindowsAppSDK(?:\.(?:AI|ML|Search|Widgets))?/' })
    if ($UnusedSdkPackages.Count -gt 0) { throw ('Unused Windows App SDK components entered the build: ' + ($UnusedSdkPackages -join ', ')) }
    $Resources = Join-Path $Stage 'resources'
    New-Item -ItemType Directory -Path $Resources -Force | Out-Null
    Invoke-Checked $Python @((Join-Path $Root 'scripts\build-windows-engine.py'), '--output', (Join-Path $Resources 'engine'))
    $CopyCode = @'
from pathlib import Path
import json, shutil, sys, xml.etree.ElementTree as ET
root, resources = map(Path, sys.argv[1:])
ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '.DS_Store')
shutil.copytree(root/'desktop/pagebridge', resources/'desktop/pagebridge', ignore=ignore, copy_function=shutil.copyfile)
shutil.copyfile(root/'desktop/bridge.py', resources/'desktop/bridge.py')
shutil.copytree(root/'demos', resources/'demos', ignore=ignore, copy_function=shutil.copyfile)
(resources/'assets').mkdir()
shutil.copyfile(root/'assets/PageBridge-icon.png', resources/'assets/PageBridge-icon.png')
for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'VERSION.json']:
    shutil.copyfile(root/name, resources/name)
assets = json.loads((root/'windows/PageBridge.Windows/obj/project.assets.json').read_text(encoding='utf-8-sig'))
license_root = resources/'licenses/nuget'
license_root.mkdir(parents=True)
notices = []
for identity, library in assets.get('libraries', {}).items():
    if library.get('type') != 'package': continue
    relative = library['path']
    package = next((Path(folder)/relative for folder in assets['packageFolders'] if (Path(folder)/relative).is_dir()), None)
    if package is None: raise RuntimeError('Missing restored NuGet package: '+identity)
    destination = license_root/identity.replace('/', '-')
    destination.mkdir()
    record = {'package': identity, 'files': [], 'license': []}
    candidates = set(package.glob('*.nuspec'))
    for path in package.iterdir():
        if path.is_file() and any(word in path.name.lower() for word in ('license', 'notice', 'copying')): candidates.add(path)
    for path in package.glob('*.nuspec'):
        for item in ET.parse(path).getroot().iter():
            if item.tag.rsplit('}', 1)[-1] == 'license' and item.text:
                record['license'].append({'type': item.get('type', ''), 'value': item.text})
                if item.get('type') == 'file':
                    candidate = (package/item.text).resolve()
                    if not candidate.is_relative_to(package.resolve()): raise RuntimeError('Invalid NuGet license path')
                    candidates.add(candidate)
    for path in sorted(candidates):
        if not path.is_file(): raise RuntimeError('Missing declared NuGet license: '+identity)
        relative_path = path.relative_to(package)
        target = destination/relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        record['files'].append(relative_path.as_posix())
    notices.append(record)
(license_root/'manifest.json').write_text(json.dumps(notices, indent=2)+'\n', encoding='utf-8')
'@
    Invoke-Checked $Python @('-c', $CopyCode, $Root, $Resources)
    Invoke-Checked $Python @($PackageTool, 'docs', '--root', $Root, '--folder', $Stage)
    Invoke-Checked $Python @($PackageTool, 'metadata', '--root', $Root, '--folder', $Stage)
    Invoke-Checked $Python @($PackageTool, 'manifest', '--root', $Root, '--folder', $Stage)
    if (Test-Path $Final) { Move-BundleDirectory $Final ($Final + '.previous-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')) }
    Move-BundleDirectory $Stage $Final
    # Re-run after relocation: a staging-only pass cannot prove portability.
    $Evidence = Join-Path $Root 'validation\windows'
    New-Item -ItemType Directory -Path $Evidence -Force | Out-Null
    Invoke-Checked $Python @((Join-Path $Root 'scripts\verify-windows-engine.py'), '--engine', (Join-Path $Final 'resources\engine'), '--output', (Join-Path $Evidence 'packaged-engine.json'))
    if (!$NoArchive) {
        $Backup = '.previous-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')
        foreach ($Old in @($Archive, ($Archive + '.sha256'))) {
            if (Test-Path $Old) { Move-Item $Old ($Old + $Backup) }
        }
        Invoke-Checked $Python @($PackageTool, 'archive', '--root', $Root, '--folder', $Final, '--archive', $Archive,
            '--report', (Join-Path $Evidence 'packaged-archive.json'))
        if (!$SkipInstaller) {
            $Iscc = $env:PAGEBRIDGE_ISCC
            if (!$Iscc) { $Iscc = & (Join-Path $Root 'scripts\setup-inno.ps1') }
            if (!(Test-Path $Iscc)) { throw 'Pinned Inno Setup compiler was not found.' }
            # windows-installer.iss validates the compiler's actual Ver value.
            $Setup = Join-Path $Dist ($PackageName + '-setup.exe')
            foreach ($Old in @($Setup, ($Setup + '.sha256'))) {
                if (Test-Path $Old) { Move-Item $Old ($Old + $Backup) }
            }
            $SourceHash = & $Python $PackageTool identity --root $Root
            if ($LASTEXITCODE -ne 0) { throw 'Source identity calculation failed.' }
            $BundleHash = (Get-FileHash (Join-Path $Final 'BUNDLE_MANIFEST.json') -Algorithm SHA256).Hash.ToLowerInvariant()
            Invoke-Checked $Iscc @('/Qp', ('/DPackageDirectory=' + $Final), ('/DOutputDirectory=' + $Dist),
                ('/DAppVersion=' + $Version.version), ('/DBuildNumber=' + $Version.build),
                ('/DSourceHash=' + $SourceHash), ('/DBundleHash=' + $BundleHash), (Join-Path $Root 'scripts\windows-installer.iss'))
            Invoke-Checked $Python @($PackageTool, 'installer', '--root', $Root, '--archive', $Setup,
                '--report', (Join-Path $Evidence 'packaged-installer.json'))
        }
    }
    Write-Output "Windows application: $Final"
} finally {
    Pop-Location
}
