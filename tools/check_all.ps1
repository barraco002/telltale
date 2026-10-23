# The delivery gate: local verification (compile, sensors, alert rules, GUI,
# shortcuts) plus the published release and its checksum.
# Usage:  powershell -ExecutionPolicy Bypass -File .\tools\check_all.ps1
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$verify = Join-Path $root 'verify.ps1'
$release = Join-Path $root 'tools\check_release.ps1'

& powershell -NoProfile -ExecutionPolicy Bypass -File $verify -Shortcuts -ShortcutName Telltale
if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL verify.ps1'; exit 1 }

& powershell -NoProfile -ExecutionPolicy Bypass -File $release
if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL check_release.ps1'; exit 1 }

Write-Output 'DELIVERY OK'
exit 0
