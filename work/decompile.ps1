param([Parameter(Mandatory=$true)][string]$Name, [Parameter(Mandatory=$true)][string[]]$Addresses)
$rsWorkspace = Split-Path -Parent $PSScriptRoot
$env:JAVA_HOME = Join-Path $PSScriptRoot 'tools\jdk-25.0.5+8'
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
$rsAnalyzer = Join-Path $PSScriptRoot 'tools\ghidra_12.2_PUBLIC\support\analyzeHeadless.bat'
$rsRunLog = Join-Path $PSScriptRoot ($Name + '.ghidra.log')
$rsOutput = Join-Path $PSScriptRoot ($Name + '.c')
$rsEntries = @($Addresses | ForEach-Object { 'entry:' + $_ })
& $rsAnalyzer (Join-Path $PSScriptRoot 'ghidra_projects') RS3_Targeted -process rs2client.exe -noanalysis -scriptPath (Join-Path $PSScriptRoot 'ghidra_scripts') -postScript ExportFunctions.java $rsOutput @rsEntries -max-cpu 2 -log (Join-Path $PSScriptRoot 'ghidra_targeted.log') *> $rsRunLog
if ($LASTEXITCODE -ne 0) { Get-Content -LiteralPath $rsRunLog -Tail 12; exit $LASTEXITCODE }
if (Test-Path -LiteralPath $rsOutput) { Get-Item -LiteralPath $rsOutput | ForEach-Object { '{0}: {1} bytes' -f $_.Name,$_.Length } }
Select-String -LiteralPath $rsRunLog -Pattern 'ERROR|REPORT: Save' | ForEach-Object { $_.Line }
