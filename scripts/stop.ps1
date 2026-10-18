<#
.SYNOPSIS
    Stop this project's backend and frontend processes.

.DESCRIPTION
    Every launch that fails partway leaves something behind: a uvicorn holding
    a port, a Vite dev server still serving a page pointed at a backend that is
    gone. They accumulate silently, and the symptom is a dashboard that loads
    and cannot reach its API — which looks like a broken build rather than like
    the wrong one of four servers answering.

    Matches on command line, so it stops this repository's processes and leaves
    any other Python or Node work alone.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Get-Process cannot see a command line, so this goes through CIM. Matching on
# the repository path is what keeps an unrelated Python process running.
$targets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like "*$root*" -and
        ($_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*vite*')
    }

if (-not $targets) {
    Write-Host 'Nothing running for this project.' -ForegroundColor Green
    return
}

foreach ($target in $targets) {
    $kind = if ($target.CommandLine -like '*uvicorn*') { 'backend' } else { 'frontend' }
    Write-Host "Stopping $kind (PID $($target.ProcessId))" -ForegroundColor Cyan
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host 'Stopped.' -ForegroundColor Green
