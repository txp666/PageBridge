[CmdletBinding()]
param([switch]$Assets)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$EngineDirectory = $PSScriptRoot
$PythonVersion = (Get-Content (Join-Path $EngineDirectory '.python-version') -Raw).Trim()
# The pinned PDF stack has x64 wheels. Use that same runtime on Windows ARM
# through Windows 11's x64 compatibility support, rather than mixing DLL ABIs.
$PythonRequest = "cpython-$PythonVersion-windows-x86_64-none"
$Python = Join-Path $EngineDirectory '.venv\Scripts\python.exe'
$LocalUv = Join-Path $EngineDirectory '.tools\bin\uv.exe'

function Invoke-CheckedNative([string]$Command, [string[]]$Arguments) {
    # Windows PowerShell 5 treats redirected native stderr as ErrorRecords;
    # uv progress belongs on stderr and must not become a terminating error.
    $PreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Command @Arguments
        $ExitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $PreviousPreference }
    if ($ExitCode -ne 0) { throw "Command failed with exit code ${ExitCode}: $Command" }
}

if ($env:OS -ne 'Windows_NT') { throw 'Run this script inside Windows.' }
if ((Test-Path (Join-Path $EngineDirectory '.venv\pyvenv.cfg')) -and !(Test-Path $Python)) {
    throw 'This checkout contains a non-Windows .venv. Copy the source without its .venv into the Windows VM; do not overwrite the macOS environment.'
}
if (!$env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $EngineDirectory '.cache\uv' }

if ($env:PAGEBRIDGE_UV) {
    $Uv = $env:PAGEBRIDGE_UV
} elseif (Test-Path $LocalUv) {
    $Uv = $LocalUv
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    $Uv = (Get-Command uv).Source
} else {
    $BootstrapArguments = @()
    if ($env:PAGEBRIDGE_BOOTSTRAP_PYTHON) {
        $BootstrapPython = $env:PAGEBRIDGE_BOOTSTRAP_PYTHON
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $BootstrapPython = (Get-Command py).Source
        $BootstrapArguments += '-3'
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $BootstrapPython = (Get-Command python).Source
    } else {
        throw 'Install uv or Python 3 from its official distribution, then rerun this script.'
    }
    $BootstrapArguments += @('-m', 'pip', 'install', '--target', (Join-Path $EngineDirectory '.tools'), 'uv==0.9.17')
    Invoke-CheckedNative $BootstrapPython $BootstrapArguments
    if (!(Test-Path $LocalUv)) { throw "uv bootstrap did not create $LocalUv" }
    $Uv = $LocalUv
}

Push-Location $EngineDirectory
try {
    Invoke-CheckedNative $Uv @('sync', '--frozen', '--managed-python', '--python', $PythonRequest)
    Invoke-CheckedNative $Python @('-c', "import platform,struct,sysconfig; assert platform.python_version() == '$PythonVersion'; assert struct.calcsize('P') == 8; assert sysconfig.get_platform() == 'win-amd64', sysconfig.get_platform()")
    Invoke-CheckedNative $Uv @('pip', 'check', '--python', $Python)
    $Metadata = & $Python (Join-Path $EngineDirectory 'runtime_info.py')
    if ($LASTEXITCODE -ne 0) { throw 'Runtime metadata check failed.' }
    [System.IO.File]::WriteAllText((Join-Path $EngineDirectory 'runtime-info.json'), ($Metadata -join "`n") + "`n", [System.Text.UTF8Encoding]::new($false))
    Write-Output $Metadata
    if ($Assets) {
        Invoke-CheckedNative (Join-Path $EngineDirectory '.venv\Scripts\babeldoc.exe') @('--warmup')
    }
} finally {
    Pop-Location
}
