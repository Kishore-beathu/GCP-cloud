@echo off
REM Stop this project's servers. See start.cmd for why a .cmd wrapper exists.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
