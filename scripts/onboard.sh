#!/usr/bin/env bash
# =============================================================================
# Life Scheduler — Onboarding Script (Linux / macOS)
# =============================================================================
# Usage:
#   chmod +x scripts/onboard.sh
#   ./scripts/onboard.sh              # interactive menu
#   ./scripts/onboard.sh --docker     # Docker / user mode (no Python needed)
#   ./scripts/onboard.sh --dev        # Local developer mode
#   ./scripts/onboard.sh --help       # Show this help
# =============================================================================

set -euo pipefail

# ── colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
step()    { echo -e "\n${BOLD}==> $*${RESET}"; }

# ── helpers ───────────────────────────────────────────────────────────────────

# Detect the available system package manager.
_pkg_manager() {
    if [[ "$(uname)" == "Darwin" ]]; then
        command -v brew &>/dev/null && echo "brew" || echo ""
    elif command -v apt-get &>/dev/null; then echo "apt"
    elif command -v dnf     &>/dev/null; then echo "dnf"
    elif command -v yum     &>/dev/null; then echo "yum"
    else echo ""
    fi
}

# Attempt to install a tool using the detected package manager.
# Returns non-zero if the tool could not be installed.
_try_install() {
    local tool="$1" pm="$2"
    case "$pm" in
        brew)
            case "$tool" in
                docker)  brew install --cask docker ;;
                python3) brew install python ;;
                *)       brew install "$tool" ;;
            esac ;;
        apt)
            case "$tool" in
                docker)  sudo apt-get install -y docker.io ;;
                *)       sudo apt-get install -y "$tool" ;;
            esac ;;
        dnf)  sudo dnf install -y "$tool" ;;
        yum)  sudo yum install -y "$tool" ;;
        *)    return 1 ;;
    esac
}

require() {
    if ! command -v "$1" &>/dev/null; then
        error "'$1' is not installed or not on PATH."
        echo "       Install it from: $2"
        local pm
        pm="$(_pkg_manager)"
        if [ -n "$pm" ]; then
            local _ans=""
            read -rp "       Would you like to install '$1' now? [y/N]: " _ans </dev/tty || true
            if [[ "$_ans" =~ ^[Yy] ]]; then
                info "Installing '$1' via $pm …"
                if _try_install "$1" "$pm"; then
                    if command -v "$1" &>/dev/null; then
                        success "'$1' installed successfully."
                    else
                        error "Installation of '$1' failed. Please install it manually from: $2"
                        exit 1
                    fi
                else
                    error "Installation of '$1' failed. Please install it manually from: $2"
                    exit 1
                fi
            else
                exit 1
            fi
        else
            exit 1
        fi
    fi
    success "$1 is available ($(command -v "$1"))"
}

open_browser() {
    local url="$1"
    if command -v xdg-open &>/dev/null; then
        xdg-open "$url" 2>/dev/null || true
    elif command -v open &>/dev/null; then
        open "$url" 2>/dev/null || true
    else
        info "Open your browser and navigate to: $url"
    fi
}

wait_for_http() {
    local url="$1"
    local max_attempts="${2:-30}"
    local attempt=0
    info "Waiting for $url to become reachable …"
    until curl -sf "$url" -o /dev/null 2>/dev/null; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge "$max_attempts" ]; then
            error "Service at $url did not respond after $max_attempts seconds."
            return 1
        fi
        sleep 1
    done
    success "$url is up."
}

# ── repo root detection ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"

# ── usage / help ─────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
${BOLD}Life Scheduler — Onboarding Script${RESET}

  Supported platforms : Linux, macOS
  Supported modes     : Docker (user) | Local development (developer)

${BOLD}Usage:${RESET}
  $0 [--docker | --dev | --help]

${BOLD}Modes:${RESET}
  --docker   Spin up the full stack with Docker Compose.
             Requires: git, docker, docker compose
             No Python installation needed.

  --dev      Set up a local Python development environment.
             Requires: git, docker (for DB), python 3.11+

  (none)     Interactive: asks which mode you want.

${BOLD}Examples:${RESET}
  ./scripts/onboard.sh --docker
  ./scripts/onboard.sh --dev
EOF
}

# ── mode: Docker ─────────────────────────────────────────────────────────────
onboard_docker() {
    step "Checking Docker prerequisites"
    require git  "https://git-scm.com/downloads"
    require docker "https://docs.docker.com/get-docker/"
    # docker compose v2 (plugin) or docker-compose v1
    if ! docker compose version &>/dev/null 2>&1 && ! command -v docker-compose &>/dev/null; then
        error "Docker Compose is not available."
        echo "       Install it from: https://docs.docker.com/compose/install/"
        exit 1
    fi
    success "Docker Compose is available"

    step "Starting all services with Docker Compose"
    cd "$REPO_ROOT"
    if docker compose version &>/dev/null 2>&1; then
        docker compose up -d --build
    else
        docker-compose up -d --build
    fi

    step "Waiting for the API to become available"
    wait_for_http "http://localhost:8000/" 60

    step "Verifying health"
    response=$(curl -sf http://localhost:8000/ || true)
    if echo "$response" | grep -q "Life Scheduler"; then
        success "API responded correctly: $response"
    else
        warn "Unexpected response from API: $response"
    fi

    echo ""
    success "🎉  Life Scheduler is running!"
    echo ""
    info "  API base  : http://localhost:8000/"
    info "  Swagger UI: http://localhost:8000/docs"
    echo ""
    info "Opening Swagger UI in your browser …"
    open_browser "http://localhost:8000/docs"

    echo ""
    info "To stop all services:"
    echo "    cd $REPO_ROOT && docker compose down"
    info "To stop and remove all data:"
    echo "    cd $REPO_ROOT && docker compose down -v"
}

# ── mode: local dev ───────────────────────────────────────────────────────────
onboard_dev() {
    step "Checking development prerequisites"
    require git    "https://git-scm.com/downloads"
    require docker "https://docs.docker.com/get-docker/"
    require python3 "https://www.python.org/downloads/"

    # Verify Python version >= 3.11
    py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    py_major=$(echo "$py_version" | cut -d. -f1)
    py_minor=$(echo "$py_version" | cut -d. -f2)
    if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 11 ]; }; then
        error "Python 3.11+ is required. Found: $py_version"
        echo "       Install from: https://www.python.org/downloads/"
        exit 1
    fi
    success "Python $py_version found"

    # ── 1. Start the database via Docker Compose ────────────────────────────
    step "Starting PostgreSQL via Docker Compose (db service only)"
    cd "$REPO_ROOT"
    if docker compose version &>/dev/null 2>&1; then
        docker compose up -d db
    else
        docker-compose up -d db
    fi
    local attempt=0
    info "Waiting for PostgreSQL healthcheck …"
    until docker compose ps db 2>/dev/null | grep -q "healthy" || \
          docker-compose ps db 2>/dev/null | grep -q "healthy"; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            error "PostgreSQL did not become healthy within 30 seconds."
            exit 1
        fi
        sleep 1
    done
    success "PostgreSQL is healthy"

    # ── 2. Virtual environment ───────────────────────────────────────────────
    step "Creating Python virtual environment"
    cd "$BACKEND_DIR"
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
        success "Virtual environment created at backend/.venv"
    else
        info "Virtual environment already exists, skipping creation"
    fi

    # shellcheck source=/dev/null
    source .venv/bin/activate
    success "Virtual environment activated"

    # ── 3. Install dependencies ──────────────────────────────────────────────
    step "Installing Python dependencies"
    pip install --quiet --upgrade pip
    pip install -e ".[dev]"
    success "Dependencies installed"

    # ── 4. Environment variables ─────────────────────────────────────────────
    step "Configuring environment variables"
    if [ ! -f ".env" ]; then
        cp .env.example .env
        # Generate a random SECRET_KEY
        secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' "s|dev-secret-key-change-in-production-use-long-random-string|${secret}|" .env
        else
            sed -i "s|dev-secret-key-change-in-production-use-long-random-string|${secret}|" .env
        fi
        success ".env created with a generated SECRET_KEY"
    else
        info ".env already exists, skipping copy"
    fi

    # ── 5. Database migrations ───────────────────────────────────────────────
    step "Running database migrations"
    alembic upgrade head
    success "Database schema is up to date"

    # ── 6. Start dev server ──────────────────────────────────────────────────
    step "Starting development server"
    echo ""
    success "🎉  Setup complete! Starting the development server …"
    echo ""
    info "  API base  : http://localhost:8000/"
    info "  Swagger UI: http://localhost:8000/docs"
    info "  The server reloads automatically on code changes."
    info "  Press Ctrl+C to stop."
    echo ""
    open_browser "http://localhost:8000/docs" &
    uvicorn app.main:app --reload --port 8000
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}║   Life Scheduler — Onboarding        ║${RESET}"
    echo -e "${BOLD}╚══════════════════════════════════════╝${RESET}"
    echo ""

    local mode="${1:-}"

    case "$mode" in
        --docker)
            onboard_docker
            ;;
        --dev)
            onboard_dev
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        "")
            echo "Which setup mode would you like?"
            echo "  1) Docker / user mode  (recommended — no Python needed)"
            echo "  2) Local developer mode (requires Python 3.11+)"
            echo ""
            read -rp "Enter 1 or 2: " choice
            case "$choice" in
                1) onboard_docker ;;
                2) onboard_dev ;;
                *) error "Invalid choice: $choice"; exit 1 ;;
            esac
            ;;
        *)
            error "Unknown argument: $mode"
            usage
            exit 1
            ;;
    esac
}

main "$@"
