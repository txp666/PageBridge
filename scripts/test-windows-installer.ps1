[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Installer,
      [Parameter(Mandatory=$true)][string]$PackageDirectory,
      [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = Split-Path $PSScriptRoot -Parent
$Installer = (Resolve-Path $Installer).Path
$PackageDirectory = (Resolve-Path $PackageDirectory).Path
$Output = [IO.Path]::GetFullPath($Output)
$AppKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{754B2DF4-3A18-4BE2-B103-6F6437765B2B}_is1'
foreach ($Hive in @('HKCU:', 'HKLM:')) {
    if (Test-Path ($Hive + '\' + $AppKey)) { throw 'An installed PageBridge is already registered. Installer QA will not replace it.' }
}
$Id = [Guid]::NewGuid().ToString('N')
$Work = Join-Path $env:LOCALAPPDATA ('PageBridge-WindowsInstallerValidation-' + $Id)
$Target = Join-Path $Work 'Program'
$Data = Join-Path $Work 'IsolatedUserData'
New-Item -ItemType Directory -Path $Data -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $Output -Parent) -Force | Out-Null
$Sentinel = Join-Path $Data 'documents-and-settings.txt'
[IO.File]::WriteAllText($Sentinel, ('Installer must preserve external data: ' + $Id))
$SentinelHash = (Get-FileHash $Sentinel -Algorithm SHA256).Hash
$Before = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'PageBridge.Windows.exe' } | Select-Object ProcessId, CreationDate, ExecutablePath)
$Installed = $false
$Report = [ordered]@{ status='running'; installer=$Installer; installedPath=$Target; userDataPath=$Data;
    preExistingProcessCount=$Before.Count; checks=@() }
function Run-Setup {
    param([string]$Log)
    $Process = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',
        '/NOCLOSEAPPLICATIONS','/NORESTARTAPPLICATIONS',('/DIR="' + $Target + '"'),('/GROUP="PageBridge Installer QA ' + $Id + '"'),
        ('/LOG="' + $Log + '"'),'/TASKS=') -Wait -PassThru
    if ($Process.ExitCode -ne 0) { throw "Installer exited with code $($Process.ExitCode)." }
}
function Assert-InstalledBytes {
    $Manifest = Get-Content (Join-Path $PackageDirectory 'BUNDLE_MANIFEST.json') -Raw | ConvertFrom-Json
    $Count = 0
    foreach ($Entry in $Manifest.files.PSObject.Properties) {
        $File = Join-Path $Target $Entry.Name
        if (!(Test-Path $File -PathType Leaf) -or (Get-Item $File).Length -ne $Entry.Value.bytes -or
            (Get-FileHash $File -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Entry.Value.sha256) {
            throw ('Installed payload mismatch: ' + $Entry.Name)
        }
        $Count++
    }
    if ((Get-FileHash (Join-Path $Target 'BUNDLE_MANIFEST.json')).Hash -ne
        (Get-FileHash (Join-Path $PackageDirectory 'BUNDLE_MANIFEST.json')).Hash) { throw 'Installed bundle manifest differs.' }
    return $Count
}
function Uninstall-QA {
    $Uninstaller = Join-Path $Target 'unins000.exe'
    if (Test-Path $Uninstaller) {
        $Process = Start-Process -FilePath $Uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',
            '/NOCLOSEAPPLICATIONS',('/LOG="' + $Output + '.uninstall.log"')) -Wait -PassThru
        if ($Process.ExitCode -ne 0) { throw 'QA uninstall failed.' }
        # Uninstaller's child removes its own image after its parent exits.
        $Deadline = [DateTime]::UtcNow.AddSeconds(20)
        while ((Test-Path $Target) -and [DateTime]::UtcNow -lt $Deadline) { Start-Sleep -Milliseconds 100 }
    }
}
try {
    Run-Setup ($Output + '.install.log'); $Installed=$true
    $Count = Assert-InstalledBytes
    $Report.checks += "installed $Count files and bundle manifest exactly match the ZIP payload"
    $Record = Get-ItemProperty ('HKCU:\' + $AppKey)
    if ([IO.Path]::GetFullPath($Record.InstallLocation).TrimEnd('\') -ne $Target) { throw 'Unexpected uninstall registration path.' }
    $Report.checks += 'per-user uninstall registration uses the independent QA directory'
    Run-Setup ($Output + '.upgrade.log')
    if ((Assert-InstalledBytes) -ne $Count) { throw 'Upgrade changed the payload count.' }
    $Report.checks += 'same-AppId in-place upgrade preserves the exact payload'
    $Python = Join-Path $Target 'resources\engine\runtime\python.exe'
    & $Python -B -X utf8 (Join-Path $Root 'scripts\verify-windows-engine.py') --engine (Join-Path $Target 'resources\engine') --output ($Output + '.engine.json')
    if ($LASTEXITCODE -ne 0) { throw 'Installed private engine health check failed.' }
    $Report.checks += 'installed private Python and translation engine health passed'
    Uninstall-QA; $Installed=$false
    if (Test-Path $Target) { throw 'Uninstall left program files.' }
    if (Test-Path ('HKCU:\' + $AppKey)) { throw 'Uninstall left registration.' }
    if ((Get-FileHash $Sentinel -Algorithm SHA256).Hash -ne $SentinelHash) { throw 'Uninstall changed external document/settings data.' }
    $Report.checks += 'uninstall removed the program and registration, and preserved isolated external data'
    foreach ($Previous in $Before) {
        $Current = Get-CimInstance Win32_Process -Filter "ProcessId=$($Previous.ProcessId)"
        if (!$Current -or $Current.CreationDate -ne $Previous.CreationDate -or $Current.ExecutablePath -ne $Previous.ExecutablePath) {
            throw 'A pre-existing PageBridge process changed during installer QA.'
        }
    }
    $Report.checks += 'pre-existing PageBridge processes remained running'
    $Report.status='passed'; $Report.installedFiles=$Count; $Report.signed=$false
} catch {
    $Report.status='failed'; $Report.error=$_.Exception.Message
    throw
} finally {
    if ($Installed -or (Test-Path (Join-Path $Target 'unins000.exe'))) {
        try { Uninstall-QA } catch { $Report.cleanupError=$_.Exception.Message }
    }
    [IO.File]::WriteAllText($Output, ($Report | ConvertTo-Json -Depth 8) + "`n", [Text.UTF8Encoding]::new($false))
}
Write-Output "Installer QA passed: $Output"
