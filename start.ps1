$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskExecutable = Join-Path $taskRoot 'gpt-converter-server.exe'
if (Test-Path -LiteralPath $taskExecutable) {
    Start-Process -FilePath $taskExecutable -ArgumentList '--open' -WorkingDirectory $taskRoot -WindowStyle Hidden
    exit 0
}
$taskPython = Get-Command python -ErrorAction SilentlyContinue
$taskServer = Join-Path $taskRoot 'server.py'
if ($taskPython) {
    Start-Process -FilePath $taskPython.Source -ArgumentList @(('"' + $taskServer + '"'), '--open') -WorkingDirectory $taskRoot -WindowStyle Hidden
    exit 0
}
$taskLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($taskLauncher) {
    Start-Process -FilePath $taskLauncher.Source -ArgumentList @('-3', ('"' + $taskServer + '"'), '--open') -WorkingDirectory $taskRoot -WindowStyle Hidden
    exit 0
}
Write-Host 'The tool needs gpt-converter-server.exe or Python 3 with requirements.txt installed.'
Write-Host 'Extract the full package, then run this launcher again.'
exit 1
