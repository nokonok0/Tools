param([string]$OutputPath = (Join-Path $PSScriptRoot 'bin\MonitorBrightnessSync.exe'))
$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework 4.xのC#コンパイラがありません' }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
New-Item -ItemType Directory -Path (Split-Path $OutputPath) -Force | Out-Null
& $compiler /nologo /target:winexe /reference:System.Management.dll /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Xml.Linq.dll "/out:$OutputPath" (Join-Path $PSScriptRoot 'MonitorBrightnessSync.cs')
if ($LASTEXITCODE -ne 0) { throw 'ビルドに失敗しました' }
Write-Output $OutputPath
