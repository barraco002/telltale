# Checks the published release: both assets are present and the .sha256 matches
# the digest GitHub reports for Telltale.exe.
# Usage:  powershell -ExecutionPolicy Bypass -File .\tools\check_release.ps1 [-Tag v1.0.0] [-Repo barraco002/telltale]
param([string]$Tag = 'v1.0.0', [string]$Repo = 'barraco002/telltale')

$ErrorActionPreference = 'Stop'
$json = gh release view $Tag -R $Repo --json assets,tagName,isDraft | ConvertFrom-Json
if ($json.isDraft) { Write-Output "FAIL release $Tag is a draft"; exit 1 }
Write-Output ("release " + $json.tagName + " is published")

$assets = @{}
foreach ($a in $json.assets) { $assets[$a.name] = $a }
foreach ($name in @('Telltale.exe', 'Telltale.exe.sha256')) {
    if (-not $assets.ContainsKey($name)) { Write-Output "FAIL missing asset: $name"; exit 1 }
    Write-Output ("OK asset " + $name + " " + [math]::Round($assets[$name].size / 1MB, 1) + " MB")
}

$tmp = Join-Path $env:TEMP ("telltale-" + $Tag + ".sha256")
gh release download $Tag -R $Repo --pattern 'Telltale.exe.sha256' --output $tmp --clobber
$expected = ((Get-Content $tmp) -split '\s+')[0].Trim().ToLower()
Remove-Item -Force $tmp

$digest = ($assets['Telltale.exe'].digest -replace '^sha256:', '').ToLower()
if (-not $digest) { Write-Output 'FAIL the release asset has no digest'; exit 1 }
Write-Output ("sha256 " + $digest)
if ($digest -ne $expected) { Write-Output ("FAIL checksum mismatch, file says " + $expected); exit 1 }
Write-Output 'CHECKSUM OK'
Write-Output 'RELEASE OK'
exit 0
