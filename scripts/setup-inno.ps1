[CmdletBinding()]
param([string]$CacheDirectory = (Join-Path $env:LOCALAPPDATA 'PageBridgeBuildTools\InnoSetup-6.7.3'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Pinned official release; do not silently consume the runner's changing ISCC.
$Version = '6.7.3'
$Url = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
$Sha256 = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
$CompilerDirectory = Join-Path $CacheDirectory 'compiler'
$Compiler = Join-Path $CompilerDirectory 'ISCC.exe'
$Installer = Join-Path $CacheDirectory ('innosetup-' + $Version + '.exe')
New-Item -ItemType Directory -Path $CacheDirectory -Force | Out-Null
if (!(Test-Path $Installer)) {
    Write-Host "Downloading official Inno Setup $Version"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $Installer -UseBasicParsing
}
if ((Get-FileHash $Installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
    throw 'Inno Setup download SHA-256 mismatch. Remove the cached installer and retry.'
}
$Signature = Get-AuthenticodeSignature $Installer
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Pyrsys B.V.') {
    throw 'The official Inno Setup installer signature is not valid for Pyrsys B.V.'
}
# Reinstall from the authenticated payload so a stale/tampered compiler is not reused.
$Process = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
    '/NOCANCEL', '/CURRENTUSER', ('/DIR="' + $CompilerDirectory + '"'), '/NOICONS') -Wait -PassThru
if ($Process.ExitCode -ne 0 -or !(Test-Path $Compiler)) { throw 'Inno Setup installation failed.' }
# Official ISCC.exe uses FileVersion 0.0.0.0; the .iss also checks the compiler's
# actual Ver preprocessor constant. The authenticated installer pins its bytes.
Write-Host "Inno Setup $Version ready."
Write-Output $Compiler
