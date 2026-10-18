@echo off
REM Launch the trading platform.
REM
REM A .cmd wrapper exists because PowerShell's default execution policy on
REM Windows desktop is Restricted: it refuses to run .ps1 files at all, with a
REM SecurityError that reads like the script is broken rather than like a
REM machine-wide setting is blocking it. Batch files carry no such restriction,
REM so this runs the real script with a per-invocation bypass -- scoped to this
REM one process, changing nothing about the system.
REM
REM Pass through any arguments: start.cmd -BackendPort 8010 -Reload
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
