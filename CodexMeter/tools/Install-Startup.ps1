$ErrorActionPreference = 'Stop'

$appDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$interpreter = Join-Path $appDirectory '.venv\Scripts\pythonw.exe'
$entrypoint = Join-Path $appDirectory 'CodexMeter.pyw'
if (-not (Test-Path -LiteralPath $interpreter -PathType Leaf)) {
    throw 'Run uv sync --locked --python 3.11 in the CodexMeter directory first.'
}
if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
    throw 'CodexMeter.pyw was not found.'
}

$startupDirectory = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDirectory 'Codex Meter.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $interpreter
$shortcut.Arguments = '"{0}"' -f $entrypoint
$shortcut.WorkingDirectory = $appDirectory
$shortcut.Description = 'Codex Meter tray monitor'
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Output "Startup shortcut installed: $shortcutPath"
