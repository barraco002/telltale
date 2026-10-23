# Creates Telltale shortcuts on the Desktop and in the Start menu.
# Usage:  powershell -ExecutionPolicy Bypass -File .\install-shortcuts.ps1 [-Name "Telltale"]
param([string]$Name = 'Telltale')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Output 'MISSING pythonw.exe: create the virtual environment or put Python on PATH.'
        exit 1
    }
    $pythonw = $found.Source
}

$script = Join-Path $root 'telltale.py'
$icon = Join-Path $root 'assets\telltale.ico'
foreach ($needed in @($script, $icon)) {
    if (-not (Test-Path $needed)) {
        Write-Output "MISSING $needed"
        exit 1
    }
}

$shell = New-Object -ComObject WScript.Shell
$folders = @(
    [Environment]::GetFolderPath('Desktop'),
    [Environment]::GetFolderPath('Programs')
)

$created = 0
foreach ($folder in $folders) {
    if (-not (Test-Path $folder)) { continue }
    $link = Join-Path $folder ($Name + '.lnk')
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = '"' + $script + '"'
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = $icon + ',0'
    $shortcut.Description = 'Telltale - a quiet hardware gauge'
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    Write-Output "created: $link"
    $created++
}

if ($created -eq 0) {
    Write-Output 'No destination folder found.'
    exit 1
}
Write-Output "shortcuts: $created"
exit 0
