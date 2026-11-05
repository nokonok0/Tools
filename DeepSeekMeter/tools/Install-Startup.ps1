$ErrorActionPreference = 'Stop'

$appDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$interpreter = Join-Path $appDirectory '.venv\Scripts\pythonw.exe'
$entrypoint = Join-Path $appDirectory 'DeepSeekMeter.pyw'
if (-not (Test-Path -LiteralPath $interpreter -PathType Leaf)) {
    throw '先にDeepSeekMeterフォルダで uv sync --locked --python 3.11 を実行してください。'
}
if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
    throw 'DeepSeekMeter.pywが見つかりません。'
}

$startupDirectory = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDirectory 'DeepSeek Meter.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $interpreter
$shortcut.Arguments = '"{0}"' -f $entrypoint
$shortcut.WorkingDirectory = $appDirectory
$shortcut.Description = 'DeepSeekの残高と推定時間を表示するトレイアプリ'
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Output "自動起動のショートカットを設定しました: $shortcutPath"
