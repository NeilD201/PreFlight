#Requires -Version 5.1
<#
.SYNOPSIS
  Start PCB PreFlight on Windows.
.EXAMPLE
  .\Start-Preflight.ps1
.EXAMPLE
  .\Start-Preflight.ps1 --port 8765
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AppArgs)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
  $py = $venvPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  $py = 'py'
  $AppArgs = @('-3', (Join-Path $root 'launch_app.py')) + $AppArgs
  & $py @AppArgs
  exit $LASTEXITCODE
} else {
  $py = 'python'
}
& $py (Join-Path $root 'launch_app.py') @AppArgs
if ($LASTEXITCODE -ne 0) {
  Write-Host ''
  Write-Host 'PreFlight could not start. See SETUP.md for setup steps.'
  Read-Host 'Press Enter to close'
}
