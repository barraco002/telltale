# Telltale verification: compile, sensor diagnostics, alert rules and GUI smoke test.
# Usage:  powershell -ExecutionPolicy Bypass -File .\verify.ps1 [-NoGui] [-Shortcuts] [-ShortcutName Telltale]
param([switch]$NoGui, [switch]$Shortcuts, [string]$ShortcutName = 'Telltale')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

$icon = Join-Path $root 'assets\telltale.ico'
if (-not (Test-Path $icon)) { Write-Output 'FAIL icon: assets\telltale.ico is missing'; exit 1 }
Write-Output '== icon present =='

Write-Output '== py_compile =='
& $python -m py_compile (Join-Path $root 'telltale.py')
if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL py_compile'; exit 1 }

Write-Output '== sensor self-test =='
& $python (Join-Path $root 'telltale.py') --self-test --samples 1 --interval 0.1
if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL self-test'; exit 1 }

Write-Output '== alert rules =='
& $python (Join-Path $root 'tests\test_advisor.py')
if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL test_advisor'; exit 1 }

if (-not $NoGui) {
    Write-Output '== GUI smoke test =='
    & $python (Join-Path $root 'tests\smoke_gui.py')
    if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL smoke_gui'; exit 1 }
}

if ($Shortcuts) {
    Write-Output '== shortcuts =='
    $links = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) "$ShortcutName.lnk"),
        (Join-Path ([Environment]::GetFolderPath('Programs')) "$ShortcutName.lnk")
    )
    $shell = New-Object -ComObject WScript.Shell
    foreach ($lnk in $links) {
        if (-not (Test-Path $lnk)) { Write-Output "FAIL shortcut missing: $lnk"; exit 1 }
        $sc = $shell.CreateShortcut($lnk)
        if (-not (Test-Path $sc.TargetPath)) { Write-Output "FAIL shortcut target missing in $lnk"; exit 1 }
        $script = $sc.Arguments.Trim('"')
        if (-not (Test-Path $script)) { Write-Output "FAIL shortcut arguments in $lnk : $script"; exit 1 }
        if ($sc.IconLocation -notlike "$icon*") { Write-Output "FAIL shortcut icon in $lnk : $($sc.IconLocation)"; exit 1 }
        Write-Output ('OK ' + (Split-Path -Leaf $lnk) + ' -> ' + (Split-Path -Leaf $sc.TargetPath) + ' :: ' + (Split-Path -Leaf $script))
    }
}

Write-Output 'VERIFICATION OK'
exit 0
