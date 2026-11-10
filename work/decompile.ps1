<#
.SYNOPSIS
Decompiles selected functions of the locally imported client with headless Ghidra.

.DESCRIPTION
Expects the local tools from bootstrap_tools.py and the Ghidra project
work/ghidra_projects/RS3_Targeted with an imported rs2client.exe. Writes
work/<Name>.c and work/<Name>.ghidra.log; both stay local.

.EXAMPLE
.\work\decompile.ps1 -Name native_vars -Addresses '140c08610','140c08db0'
#>
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string[]]$Addresses
)

$workDirectory = $PSScriptRoot
$env:JAVA_HOME = Join-Path $workDirectory 'tools\jdk-25.0.5+8'
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
$headlessAnalyzer = Join-Path $workDirectory 'tools\ghidra_12.2_PUBLIC\support\analyzeHeadless.bat'
$projectDirectory = Join-Path $workDirectory 'ghidra_projects'
$scriptDirectory = Join-Path $workDirectory 'ghidra_scripts'
$runLog = Join-Path $workDirectory ($Name + '.ghidra.log')
$analyzerLog = Join-Path $workDirectory 'ghidra_targeted.log'
$outputFile = Join-Path $workDirectory ($Name + '.c')
# The export script treats "entry:" addresses as exact function entry points.
$entryArguments = @($Addresses | ForEach-Object { 'entry:' + $_ })

& $headlessAnalyzer $projectDirectory RS3_Targeted -process rs2client.exe -noanalysis `
    -scriptPath $scriptDirectory -postScript ExportFunctions.java $outputFile @entryArguments `
    -max-cpu 2 -log $analyzerLog *> $runLog
if ($LASTEXITCODE -ne 0)
{
    Get-Content -LiteralPath $runLog -Tail 12
    exit $LASTEXITCODE
}
if (Test-Path -LiteralPath $outputFile)
{
    Get-Item -LiteralPath $outputFile | ForEach-Object { '{0}: {1} bytes' -f $_.Name, $_.Length }
}
Select-String -LiteralPath $runLog -Pattern 'ERROR|REPORT: Save' | ForEach-Object { $_.Line }
