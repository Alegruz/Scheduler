@echo off
:: =============================================================================
:: Life Scheduler — Onboarding Script (Windows Command Prompt / CMD)
:: =============================================================================
:: Usage:
::   scripts\onboard.bat              -- interactive menu
::   scripts\onboard.bat --docker     -- Docker / user mode
::   scripts\onboard.bat --dev        -- Local developer mode
::   scripts\onboard.bat --help       -- Show this help
:: =============================================================================
setlocal EnableDelayedExpansion

:: ── colour helpers (requires ANSI support, Windows 10 1511+) ─────────────────
:: Fallback: plain text only — no ANSI escape codes in legacy cmd.
set "INFO=[INFO] "
set "OK=[OK]   "
set "WARN=[WARN] "
set "ERR=[ERROR]"

:: ── banner ───────────────────────────────────────────────────────────────────
echo.
echo ==========================================
echo   Life Scheduler -- Onboarding (Windows)
echo ==========================================
echo.

:: ── parse argument ───────────────────────────────────────────────────────────
set "MODE=%~1"

if /i "%MODE%"=="--help" goto :show_help
if /i "%MODE%"=="-h"     goto :show_help
if /i "%MODE%"=="--docker" goto :mode_docker
if /i "%MODE%"=="--dev"    goto :mode_dev
if "%MODE%"==""            goto :interactive
echo %ERR% Unknown argument: %MODE%
goto :show_help

:: ── interactive ───────────────────────────────────────────────────────────────
:interactive
echo Which setup mode would you like?
echo   1) Docker / user mode  (recommended -- no Python needed)
echo   2) Local developer mode (requires Python 3.11+)
echo.
set /p "CHOICE=Enter 1 or 2: "
if "%CHOICE%"=="1" goto :mode_docker
if "%CHOICE%"=="2" goto :mode_dev
echo %ERR% Invalid choice: %CHOICE%
exit /b 1

:: ── help ─────────────────────────────────────────────────────────────────────
:show_help
echo.
echo Life Scheduler -- Onboarding Script (Windows CMD)
echo.
echo   Supported modes:
echo     --docker   Spin up the full stack with Docker Compose.
echo                Requires: git, docker, docker compose
echo     --dev      Set up a local Python development environment.
echo                Requires: git, docker (for DB), python 3.11+
echo     (none)     Interactive: asks which mode you want.
echo.
echo   Examples:
echo     scripts\onboard.bat --docker
echo     scripts\onboard.bat --dev
echo.
exit /b 0

:: =============================================================================
:: MODE: DOCKER
:: =============================================================================
:mode_docker
echo.
echo =^> Checking Docker prerequisites ...
call :require_cmd git "https://git-scm.com/downloads" || exit /b 1
call :require_cmd docker "https://docs.docker.com/get-docker/" || exit /b 1

:: Prefer docker compose v2 (plugin), fall back to docker-compose v1
docker compose version >nul 2>&1
if %ERRORLEVEL%==0 (
    set "DC=docker compose"
) else (
    docker-compose --version >nul 2>&1
    if %ERRORLEVEL%==0 (
        set "DC=docker-compose"
    ) else (
        echo %ERR% Docker Compose is not available.
        echo        Install it from: https://docs.docker.com/compose/install/
        exit /b 1
    )
)
echo %OK% Docker Compose is available

echo.
echo =^> Starting all services with Docker Compose ...
cd /d "%~dp0.."
%DC% up -d --build
if %ERRORLEVEL% neq 0 (
    echo %ERR% Docker Compose failed. Check the output above.
    exit /b 1
)

echo.
echo =^> Waiting for the API to become available ...
call :wait_for_http "http://localhost:8000/" 60 || exit /b 1

echo.
echo %OK% Life Scheduler is running!
echo.
echo   API base  : http://localhost:8000/
echo   Swagger UI: http://localhost:8000/docs
echo.
start "" "http://localhost:8000/docs"

echo.
echo   To stop all services:
echo       cd %~dp0.. ^&^& %DC% down
echo   To stop and remove all data:
echo       cd %~dp0.. ^&^& %DC% down -v
goto :eof

:: =============================================================================
:: MODE: DEV
:: =============================================================================
:mode_dev
echo.
echo =^> Checking development prerequisites ...
call :require_cmd git    "https://git-scm.com/downloads"   || exit /b 1
call :require_cmd docker "https://docs.docker.com/get-docker/" || exit /b 1
call :require_cmd python "https://www.python.org/downloads/" || exit /b 1

:: Verify Python version >= 3.11
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set "PYMAJOR=%%a"
    set "PYMINOR=%%b"
)
if !PYMAJOR! LSS 3 (
    echo %ERR% Python 3.11+ is required. Found: %PYVER%
    exit /b 1
)
if !PYMAJOR! EQU 3 if !PYMINOR! LSS 11 (
    echo %ERR% Python 3.11+ is required. Found: %PYVER%
    exit /b 1
)
echo %OK% Python %PYVER% found

:: Prefer docker compose v2, fall back to v1
docker compose version >nul 2>&1
if %ERRORLEVEL%==0 (
    set "DC=docker compose"
) else (
    set "DC=docker-compose"
)

:: ── 1. Start PostgreSQL ───────────────────────────────────────────────────────
echo.
echo =^> Starting PostgreSQL via Docker Compose (db service only) ...
cd /d "%~dp0.."
%DC% up -d db
if %ERRORLEVEL% neq 0 (
    echo %ERR% Failed to start database service.
    exit /b 1
)

echo    Waiting for PostgreSQL healthcheck ...
set /a "ATTEMPT=0"
:wait_db
%DC% ps db 2>nul | find "healthy" >nul 2>&1
if %ERRORLEVEL%==0 goto :db_ready
set /a "ATTEMPT+=1"
if !ATTEMPT! GEQ 30 (
    echo %ERR% PostgreSQL did not become healthy within 30 seconds.
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_db
:db_ready
echo %OK% PostgreSQL is healthy

:: ── 2. Virtual environment ────────────────────────────────────────────────────
echo.
echo =^> Creating Python virtual environment ...
cd /d "%~dp0..\backend"
if not exist ".venv" (
    python -m venv .venv
    echo %OK% Virtual environment created at backend\.venv
) else (
    echo %INFO% Virtual environment already exists, skipping creation
)
call .venv\Scripts\activate.bat
echo %OK% Virtual environment activated

:: ── 3. Install dependencies ───────────────────────────────────────────────────
echo.
echo =^> Installing Python dependencies ...
pip install --quiet --upgrade pip
pip install -e ".[dev]"
if %ERRORLEVEL% neq 0 (
    echo %ERR% pip install failed.
    exit /b 1
)
echo %OK% Dependencies installed

:: ── 4. Environment variables ─────────────────────────────────────────────────
echo.
echo =^> Configuring environment variables ...
if not exist ".env" (
    copy .env.example .env >nul
    :: Generate a random SECRET_KEY using Python and patch .env
    for /f "delims=" %%s in ('python -c "import secrets; print(secrets.token_hex(32))"') do set "SECRET=%%s"
    powershell -Command "(Get-Content .env) -replace 'dev-secret-key-change-in-production-use-long-random-string', '!SECRET!' | Set-Content .env"
    echo %OK% .env created with a generated SECRET_KEY
) else (
    echo %INFO% .env already exists, skipping copy
)

:: ── 5. Database migrations ────────────────────────────────────────────────────
echo.
echo =^> Running database migrations ...
alembic upgrade head
if %ERRORLEVEL% neq 0 (
    echo %ERR% Alembic migrations failed. Check your DATABASE_URL in backend\.env
    exit /b 1
)
echo %OK% Database schema is up to date

:: ── 6. Start dev server ───────────────────────────────────────────────────────
echo.
echo %OK% Setup complete! Starting the development server ...
echo.
echo   API base  : http://localhost:8000/
echo   Swagger UI: http://localhost:8000/docs
echo   The server reloads automatically on code changes.
echo   Press Ctrl+C to stop.
echo.
start "" "http://localhost:8000/docs"
uvicorn app.main:app --reload --port 8000
goto :eof

:: =============================================================================
:: HELPER SUBROUTINES
:: =============================================================================

:require_cmd
:: Usage: call :require_cmd <command> <install-url>
where %~1 >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo %ERR% '%~1' is not installed or not on PATH.
    echo        Install it from: %~2
    call :try_install "%~1" "%~2"
    if !ERRORLEVEL! neq 0 exit /b 1
    where %~1 >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo %ERR% Installation of '%~1' failed. Please install it manually from: %~2
        exit /b 1
    )
    echo %OK% '%~1' installed successfully.
)
echo %OK% %~1 is available
exit /b 0

:try_install
:: Usage: call :try_install <command> <install-url>
set "_TOOL=%~1"
set "_URL=%~2"
set "_PM="
where winget >nul 2>&1
if %ERRORLEVEL%==0 set "_PM=winget"
if "!_PM!"=="" (
    where choco >nul 2>&1
    if %ERRORLEVEL%==0 set "_PM=choco"
)
if "!_PM!"=="" (
    echo        No supported package manager found. Please install '!_TOOL!' manually from: !_URL!
    exit /b 1
)
set /p "_ANS=       Would you like to install '!_TOOL!' now? [y/N]: "
set "_DO_INSTALL="
if /i "!_ANS!"=="y"   set "_DO_INSTALL=1"
if /i "!_ANS!"=="yes" set "_DO_INSTALL=1"
if "!_DO_INSTALL!"=="" exit /b 1
set "_PKG="
if /i "!_TOOL!"=="git"    if "!_PM!"=="winget" set "_PKG=Git.Git"
if /i "!_TOOL!"=="git"    if "!_PM!"=="choco"  set "_PKG=git"
if /i "!_TOOL!"=="docker" if "!_PM!"=="winget" set "_PKG=Docker.DockerDesktop"
if /i "!_TOOL!"=="docker" if "!_PM!"=="choco"  set "_PKG=docker-desktop"
if /i "!_TOOL!"=="python" if "!_PM!"=="winget" set "_PKG=Python.Python.3.11"
if /i "!_TOOL!"=="python" if "!_PM!"=="choco"  set "_PKG=python"
if "!_PKG!"=="" (
    echo        No auto-install recipe for '!_TOOL!'. Please install it manually from: !_URL!
    exit /b 1
)
echo %INFO% Installing '!_TOOL!' via !_PM! ...
if "!_PM!"=="winget" (
    winget install --id !_PKG! -e --source winget
) else (
    choco install !_PKG! -y
)
exit /b %ERRORLEVEL%

:wait_for_http
:: Usage: call :wait_for_http <url> <max_seconds>
set "URL=%~1"
set /a "MAX=%~2"
set /a "TRIES=0"
echo    Waiting for %URL% ...
:http_loop
curl -sf "%URL%" -o nul 2>nul
if %ERRORLEVEL%==0 (
    echo %OK% %URL% is up.
    exit /b 0
)
set /a "TRIES+=1"
if !TRIES! GEQ !MAX! (
    echo %ERR% Service at %URL% did not respond after !MAX! seconds.
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :http_loop
