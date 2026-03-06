#Requires -Version 5.1
<#
.SYNOPSIS
    Life Scheduler — Onboarding Script (Windows PowerShell / PowerShell Core)

.DESCRIPTION
    Automates the one-click onboarding experience for Life Scheduler on Windows.
    Supports two modes:
      --Docker  : Spin up the complete stack via Docker Compose (no Python needed).
      --Dev     : Set up a local Python development environment.

.PARAMETER Mode
    Selects the onboarding mode.
    Accepted values: Docker, Dev
    If omitted an interactive menu is shown.

.EXAMPLE
    .\scripts\onboard.ps1
    .\scripts\onboard.ps1 -Mode Docker
    .\scripts\onboard.ps1 -Mode Dev

.NOTES
    Requires: PowerShell 5.1+ (Windows) or PowerShell 7+ (cross-platform).
    Run with:  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#>

[CmdletBinding()]
param(
    [ValidateSet('Docker', 'Dev', '')]
    [string]$Mode = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Colours / output helpers ─────────────────────────────────────────────────
function Write-Info    { param([string]$Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Cyan }
function Write-Success { param([string]$Msg) Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn    { param([string]$Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Err     { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }
function Write-Step    { param([string]$Msg) Write-Host "`n==> $Msg" -ForegroundColor White -BackgroundColor DarkBlue }

# ── Resolve paths ────────────────────────────────────────────────────────────
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot   = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $RepoRoot 'backend'

# ── Helper: check that a command exists ──────────────────────────────────────
function Get-InstallManager {
    if (Get-Command 'winget' -ErrorAction SilentlyContinue) { return 'winget' }
    if (Get-Command 'choco'  -ErrorAction SilentlyContinue) { return 'choco'  }
    return $null
}

function Install-Tool {
    param(
        [string]$Command,
        [string]$InstallUrl
    )
    $pm = Get-InstallManager
    if (-not $pm) {
        Write-Info "No supported package manager (winget/choco) found. Please install '$Command' manually from: $InstallUrl"
        return $false
    }
    $yn = Read-Host "       Would you like to install '$Command' now? [y/N]"
    if ($yn -notmatch '^[Yy]') { return $false }

    $wingetId = switch ($Command) {
        'git'     { 'Git.Git' }
        'docker'  { 'Docker.DockerDesktop' }
        'python'  { 'Python.Python.3.11' }
        'python3' { 'Python.Python.3.11' }
        default   { $null }
    }
    $chocoPkg = switch ($Command) {
        'git'     { 'git' }
        'docker'  { 'docker-desktop' }
        'python'  { 'python' }
        'python3' { 'python' }
        default   { $null }
    }

    Write-Info "Installing '$Command' via $pm …"
    if ($pm -eq 'winget' -and $wingetId) {
        & winget install --id $wingetId -e --source winget | Out-Host
        return ($LASTEXITCODE -eq 0)
    } elseif ($pm -eq 'choco' -and $chocoPkg) {
        & choco install $chocoPkg -y | Out-Host
        return ($LASTEXITCODE -eq 0)
    } else {
        Write-Info "No auto-install recipe for '$Command'. Please install it manually from: $InstallUrl"
        return $false
    }
}

function Assert-Command {
    param(
        [string]$Command,
        [string]$InstallUrl
    )
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        Write-Err "'$Command' is not installed or not on PATH."
        Write-Host "       Install it from: $InstallUrl" -ForegroundColor Gray
        $installed = Install-Tool -Command $Command -InstallUrl $InstallUrl
        if (-not $installed) {
            exit 1
        }
        # Refresh PATH so newly installed tools are discoverable
        $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' +
                    [System.Environment]::GetEnvironmentVariable('PATH', 'User')
        if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
            Write-Err "Installation of '$Command' failed. Please install it manually from: $InstallUrl"
            exit 1
        }
        Write-Success "'$Command' installed successfully."
    }
    Write-Success "$Command is available ($(( Get-Command $Command ).Source))"
}

# ── Helper: resolve Docker Compose command ────────────────────────────────────
function Get-DockerCompose {
    $ok = & docker compose version 2>&1
    if ($LASTEXITCODE -eq 0) { return 'docker compose' }
    if (Get-Command 'docker-compose' -ErrorAction SilentlyContinue) { return 'docker-compose' }
    Write-Err "Docker Compose is not available."
    Write-Host "       Install it from: https://docs.docker.com/compose/install/" -ForegroundColor Gray
    exit 1
}

# ── Helper: wait for an HTTP endpoint ─────────────────────────────────────────
function Wait-ForHttp {
    param(
        [string]$Url,
        [int]$MaxSeconds = 60
    )
    Write-Info "Waiting for $Url to become reachable …"
    for ($i = 0; $i -lt $MaxSeconds; $i++) {
        try {
            $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            Write-Success "$Url is up."
            return
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    Write-Err "Service at $Url did not respond after $MaxSeconds seconds."
    exit 1
}

# ── Helper: wait for Docker Compose db healthcheck ───────────────────────────
function Wait-ForDbHealthy {
    param([string]$DC, [int]$MaxSeconds = 30)
    Write-Info "Waiting for PostgreSQL healthcheck …"
    for ($i = 0; $i -lt $MaxSeconds; $i++) {
        $status = & $DC.Split(' ')[0] $DC.Split(' ')[1..99] ps db 2>&1 | Select-String 'healthy'
        if ($status) {
            Write-Success "PostgreSQL is healthy."
            return
        }
        Start-Sleep -Seconds 1
    }
    Write-Err "PostgreSQL did not become healthy within $MaxSeconds seconds."
    exit 1
}

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Life Scheduler — Onboarding (PowerShell)    ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Interactive menu if no mode supplied ─────────────────────────────────────
if ($Mode -eq '') {
    Write-Host "Which setup mode would you like?"
    Write-Host "  1) Docker / user mode  (recommended — no Python needed)"
    Write-Host "  2) Local developer mode (requires Python 3.11+)"
    Write-Host ""
    $choice = Read-Host "Enter 1 or 2"
    switch ($choice) {
        '1' { $Mode = 'Docker' }
        '2' { $Mode = 'Dev' }
        default {
            Write-Err "Invalid choice: $choice"
            exit 1
        }
    }
}

# =============================================================================
# MODE: DOCKER
# =============================================================================
if ($Mode -ieq 'Docker') {
    Write-Step "Checking Docker prerequisites"
    Assert-Command 'git'    'https://git-scm.com/downloads'
    Assert-Command 'docker' 'https://docs.docker.com/get-docker/'
    $dc = Get-DockerCompose
    Write-Success "Docker Compose is available ($dc)"

    Write-Step "Starting all services with Docker Compose"
    Push-Location $RepoRoot
    try {
        if ($dc -eq 'docker compose') {
            & docker compose up -d --build
        } else {
            & docker-compose up -d --build
        }
        if ($LASTEXITCODE -ne 0) { Write-Err "Docker Compose failed."; exit 1 }
    } finally {
        Pop-Location
    }

    Write-Step "Waiting for the API to become available"
    Wait-ForHttp 'http://localhost:8000/' 60

    Write-Step "Verifying health"
    try {
        $resp = Invoke-RestMethod -Uri 'http://localhost:8000/' -UseBasicParsing
        Write-Success "API responded: $($resp | ConvertTo-Json -Compress)"
    } catch {
        Write-Warn "Could not parse API response — service may still be starting."
    }

    Write-Host ""
    Write-Success "🎉  Life Scheduler is running!"
    Write-Host ""
    Write-Info "  API base  : http://localhost:8000/"
    Write-Info "  Swagger UI: http://localhost:8000/docs"
    Write-Host ""
    Write-Info "Opening Swagger UI in your browser …"
    Start-Process 'http://localhost:8000/docs'

    Write-Host ""
    Write-Info "To stop all services:"
    Write-Host "    cd $RepoRoot; $dc down" -ForegroundColor Gray
    Write-Info "To stop and remove all data:"
    Write-Host "    cd $RepoRoot; $dc down -v" -ForegroundColor Gray
    exit 0
}

# =============================================================================
# MODE: DEV
# =============================================================================
if ($Mode -ieq 'Dev') {
    Write-Step "Checking development prerequisites"
    Assert-Command 'git'    'https://git-scm.com/downloads'
    Assert-Command 'docker' 'https://docs.docker.com/get-docker/'
    # Accept 'python' or 'python3'
    $pythonCmd = $null
    if (Get-Command 'python3' -ErrorAction SilentlyContinue) {
        $pythonCmd = 'python3'
    } elseif (Get-Command 'python' -ErrorAction SilentlyContinue) {
        $pythonCmd = 'python'
    } else {
        Write-Err "'python' is not installed or not on PATH."
        Write-Host "       Install it from: https://www.python.org/downloads/" -ForegroundColor Gray
        $installed = Install-Tool -Command 'python' -InstallUrl 'https://www.python.org/downloads/'
        if (-not $installed) { exit 1 }
        # Refresh PATH
        $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' +
                    [System.Environment]::GetEnvironmentVariable('PATH', 'User')
        if (Get-Command 'python3' -ErrorAction SilentlyContinue) {
            $pythonCmd = 'python3'
        } elseif (Get-Command 'python' -ErrorAction SilentlyContinue) {
            $pythonCmd = 'python'
        } else {
            Write-Err "Installation of 'python' failed. Please install it manually from: https://www.python.org/downloads/"
            exit 1
        }
    }
    Write-Success "$pythonCmd is available"

    # Verify Python >= 3.11
    $pyVerStr = & $pythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    $pyParts  = $pyVerStr.Split('.')
    $pyMajor  = [int]$pyParts[0]
    $pyMinor  = [int]$pyParts[1]
    if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 11)) {
        Write-Err "Python 3.11+ is required. Found: $pyVerStr"
        exit 1
    }
    Write-Success "Python $pyVerStr meets the minimum requirement (3.11+)"

    $dc = Get-DockerCompose

    # ── 1. Start PostgreSQL ───────────────────────────────────────────────────
    Write-Step "Starting PostgreSQL via Docker Compose (db service only)"
    Push-Location $RepoRoot
    try {
        if ($dc -eq 'docker compose') {
            & docker compose up -d db
        } else {
            & docker-compose up -d db
        }
        if ($LASTEXITCODE -ne 0) { Write-Err "Failed to start db service."; exit 1 }
    } finally {
        Pop-Location
    }

    # Wait for healthy
    Write-Info "Waiting for PostgreSQL healthcheck …"
    for ($i = 0; $i -lt 30; $i++) {
        $psOutput = if ($dc -eq 'docker compose') {
            & docker compose ps db 2>&1
        } else {
            & docker-compose ps db 2>&1
        }
        if ($psOutput -match 'healthy') { Write-Success "PostgreSQL is healthy."; break }
        if ($i -eq 29) { Write-Err "PostgreSQL did not become healthy within 30 seconds."; exit 1 }
        Start-Sleep -Seconds 1
    }

    # ── 2. Virtual environment ────────────────────────────────────────────────
    Write-Step "Creating Python virtual environment"
    Push-Location $BackendDir
    try {
        $venvPath = Join-Path $BackendDir '.venv'
        if (-not (Test-Path $venvPath)) {
            & $pythonCmd -m venv .venv
            Write-Success "Virtual environment created at backend\.venv"
        } else {
            Write-Info "Virtual environment already exists, skipping creation"
        }

        # Activate
        $activateScript = Join-Path $venvPath 'Scripts\Activate.ps1'
        if (Test-Path $activateScript) {
            & $activateScript
        } else {
            # PowerShell Core on Windows may use bin/Activate.ps1
            $activateScript = Join-Path $venvPath 'bin\Activate.ps1'
            & $activateScript
        }
        Write-Success "Virtual environment activated"

        # ── 3. Install dependencies ───────────────────────────────────────────
        Write-Step "Installing Python dependencies"
        & pip install --quiet --upgrade pip
        & pip install -e ".[dev]"
        if ($LASTEXITCODE -ne 0) { Write-Err "pip install failed."; exit 1 }
        Write-Success "Dependencies installed"

        # ── 4. Environment variables ──────────────────────────────────────────
        Write-Step "Configuring environment variables"
        $envFile = Join-Path $BackendDir '.env'
        if (-not (Test-Path $envFile)) {
            Copy-Item (Join-Path $BackendDir '.env.example') $envFile
            $secret = & $pythonCmd -c "import secrets; print(secrets.token_hex(32))"
            (Get-Content $envFile) -replace `
                'dev-secret-key-change-in-production-use-long-random-string', $secret |
                Set-Content $envFile
            Write-Success ".env created with a generated SECRET_KEY"
        } else {
            Write-Info ".env already exists, skipping copy"
        }

        # ── 5. Database migrations ────────────────────────────────────────────
        Write-Step "Running database migrations"
        & alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Alembic migrations failed. Check your DATABASE_URL in backend\.env"
            exit 1
        }
        Write-Success "Database schema is up to date"

        # ── 6. Start dev server ───────────────────────────────────────────────
        Write-Host ""
        Write-Success "🎉  Setup complete! Starting the development server …"
        Write-Host ""
        Write-Info "  API base  : http://localhost:8000/"
        Write-Info "  Swagger UI: http://localhost:8000/docs"
        Write-Info "  The server reloads automatically on code changes."
        Write-Info "  Press Ctrl+C to stop."
        Write-Host ""
        Start-Process 'http://localhost:8000/docs'
        & uvicorn app.main:app --reload --port 8000

    } finally {
        Pop-Location
    }
    exit 0
}

Write-Err "Unknown mode: $Mode"
exit 1
