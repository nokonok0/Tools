$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Xml.Linq
$path = & (Join-Path $PSScriptRoot 'build.ps1')
$assembly = [Reflection.Assembly]::LoadFrom($path)
$type = $assembly.GetType('MonitorBrightnessSync')
$flags = [Reflection.BindingFlags]'Static,NonPublic'
$parse = $type.GetMethod('ParseSettings', $flags)
$example = [System.Xml.Linq.XDocument]::Load((Join-Path $PSScriptRoot 'config.example.xml'))
$rules = $parse.Invoke($null, @($example))
if ($rules.Count -ne 2) { throw '設定例の読み込みに失敗しました' }
$invalid = @(
    '<monitors/>',
    '<other/>',
    '<monitors><monitor id="TEST" gamma="49"/></monitors>',
    '<monitors><monitor id="TEST" scale="NaN"/></monitors>',
    '<monitors><monitor id="TEST" minimum="0"/></monitors>',
    '<monitors><monitor id="TEST" blueGain="101"/></monitors>',
    '<monitors><monitor id="TEST" gamma="60" softwareFollow="true"/></monitors>',
    '<monitors><monitor id="TEST"/><monitor id="test"/></monitors>',
    '<monitors><monitor id="TEST" typo="1"/></monitors>'
)
foreach ($xml in $invalid) {
    $rejected = $false
    try { $null = $parse.Invoke($null, @([System.Xml.Linq.XDocument]::Parse($xml))) }
    catch {
        if ($_.Exception.GetBaseException() -is [FormatException]) { $rejected = $true }
        else { throw }
    }
    if (-not $rejected) { throw '不正な設定が受け付けられました' }
}
Write-Output '10項目合格。モニター設定は変更していません。'
