<#
.SYNOPSIS
    Start the backend and frontend together, on ports that are actually free.

.DESCRIPTION
    Launching this by hand kept failing in ways that looked like different
    problems and were all the same one. Port 8000 stays occupied by a half-dead
    process — one that still owns the listening socket, so the browser's
    connection is accepted and then never answered, which reads as a hang
    rather than as a refusal. Windows also reserves whole port ranges for
    Hyper-V, WSL and Docker, and inside a reserved range nothing can bind and
    no process owns the port, so there is nothing to kill.

    This script does not fight for a port. It finds one that binds, tells the
    frontend which one it picked, and waits until the API actually answers
    before declaring success.

.PARAMETER BackendPort
    Preferred backend port. If it will not bind, the next free port is used.

.PARAMETER FrontendPort
    Preferred Vite port. Vite finds its own alternative if this is taken.

.PARAMETER Reload
    Enable uvicorn's auto-reload. Off by default: reload runs a supervisor and
    a worker, and killing one of the pair leaves the other holding the socket,
    which is how the stuck port happens in the first place.

.EXAMPLE
    .\scripts\start.ps1

.EXAMPLE
    .\scripts\start.ps1 -BackendPort 8010 -Reload
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "    $message" -ForegroundColor Yellow }

# --- Preconditions ----------------------------------------------------------
# Checked up front rather than discovered as a confusing failure later: a
# missing virtualenv makes every command silently do nothing.
if (-not (Test-Path $python)) {
    Write-Error @"
No virtualenv at $python

Create it first:
    cd $backend
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
"@
}

# --- Find a port that actually binds ----------------------------------------
# Asking the OS to bind is the only reliable test. Enumerating listeners misses
# reserved ranges, where the port is unusable and unowned at the same time.
function Test-PortFree([int]$port) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        # Stop() on a listener whose Start() threw can itself throw.
        if ($listener) { try { $listener.Stop() } catch { } }
    }
}

function Get-PortHolder([int]$port) {
    $owners = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $owners) { return $null }
    return Get-Process -Id $owners -ErrorAction SilentlyContinue
}

$chosen = $BackendPort
if (-not (Test-PortFree $BackendPort)) {
    $holder = Get-PortHolder $BackendPort
    if ($holder) {
        Write-Warn "Port $BackendPort is held by $($holder.ProcessName) (PID $($holder.Id))."
        Write-Warn "Stop it with:  taskkill /PID $($holder.Id) /F"
    } else {
        # Bind refused with no owner: almost always a Windows reserved range.
        Write-Warn "Port $BackendPort cannot be bound and no process owns it."
        Write-Warn "Likely a reserved range - check: netsh interface ipv4 show excludedportrange protocol=tcp"
    }

    $chosen = $null
    foreach ($candidate in ($BackendPort + 1)..($BackendPort + 20)) {
        if (Test-PortFree $candidate) { $chosen = $candidate; break }
    }
    if (-not $chosen) { Write-Error "No free port in $BackendPort..$($BackendPort + 20)." }
    Write-Warn "Using port $chosen instead."
}

$apiUrl = "http://localhost:$chosen"

# --- Backend ----------------------------------------------------------------
Write-Step "Starting backend on $apiUrl"
$uvicornArgs = @('-m', 'uvicorn', 'app.main:app', '--port', "$chosen")
if ($Reload) { $uvicornArgs += '--reload' }
$backendProcess = Start-Process -FilePath $python -ArgumentList $uvicornArgs `
    -WorkingDirectory $backend -PassThru

# Wait for the API to answer rather than for the process to exist. Startup
# seeds the universe and starts the scheduler, so "running" and "serving" are
# several seconds apart, and that gap is what looks like a hung frontend.
Write-Step 'Waiting for the API to answer'
$ready = $false
foreach ($attempt in 1..60) {
    if ($backendProcess.HasExited) {
        Write-Error "Backend exited during startup (code $($backendProcess.ExitCode)). Run uvicorn directly to see why."
    }
    try {
        $health = Invoke-RestMethod "$apiUrl/health" -TimeoutSec 2
        if ($health) { $ready = $true; break }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $ready) {
    Write-Error "Backend did not answer on $apiUrl within 30s. It is running (PID $($backendProcess.Id)) but not serving."
}
Write-Host "    API ready (PID $($backendProcess.Id))" -ForegroundColor Green

# --- Frontend ---------------------------------------------------------------
# VITE_API_URL is read at dev-server startup, so it has to be set in the
# environment the child inherits - not after the fact.
Write-Step "Starting frontend on http://localhost:$FrontendPort"
$env:VITE_API_URL = $apiUrl
$frontendProcess = Start-Process -FilePath 'npm.cmd' `
    -ArgumentList @('run', 'dev', '--', '--port', "$FrontendPort") `
    -WorkingDirectory $frontend -PassThru

Write-Host ''
Write-Host "  Dashboard  http://localhost:$FrontendPort" -ForegroundColor Green
Write-Host "  API        $apiUrl" -ForegroundColor Green
Write-Host "  API docs   $apiUrl/docs" -ForegroundColor Green
Write-Host ''
Write-Host "  Stop both:  Stop-Process -Id $($backendProcess.Id),$($frontendProcess.Id) -Force"
Write-Host ''
