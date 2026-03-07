#!/usr/bin/env bash
# =============================================================================
# setup-server.sh — Server initialization for Token RugCheck MCP
#
# Usage (run on the remote server via SSH pipe):
#   ssh root@SERVER_IP 'bash -s' < scripts/setup-server.sh
#
# Or directly on the server:
#   bash scripts/setup-server.sh
#
# Output format: STATUS|COMPONENT|MESSAGE
# Exit codes: 0=success, 1=error, 2=already_setup
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/AetherCore-Dev/token-rugcheck.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/token-rugcheck}"
REQUIRED_PORTS=(22 80)

# --- Helpers ---
log_ok()   { echo "OK|$1|$2"; }
log_fail() { echo "FAIL|$1|$2"; }
log_skip() { echo "SKIP|$1|$2"; }
log_info() { echo "INFO|$1|$2"; }

bail() { log_fail "$1" "$2"; exit 1; }

# --- Pre-flight ---
if [ "$(id -u)" -ne 0 ]; then
    bail "PREFLIGHT" "Must run as root (current uid=$(id -u))"
fi

log_info "PREFLIGHT" "Starting server setup on $(hostname) ($(uname -s) $(uname -m))"

# --- 1. Docker ---
if command -v docker &>/dev/null; then
    DOCKER_VER=$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
    log_skip "DOCKER" "Already installed (v${DOCKER_VER})"
else
    log_info "DOCKER" "Installing Docker..."
    if curl -fsSL https://get.docker.com | sh &>/dev/null; then
        systemctl enable docker &>/dev/null || true
        systemctl start docker &>/dev/null || true
        DOCKER_VER=$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
        log_ok "DOCKER" "Installed Docker v${DOCKER_VER}"
    else
        bail "DOCKER" "Docker installation failed"
    fi
fi

# --- 2. Docker Compose ---
if docker compose version &>/dev/null; then
    COMPOSE_VER=$(docker compose version --short 2>/dev/null)
    log_skip "COMPOSE" "Already installed (v${COMPOSE_VER})"
else
    log_info "COMPOSE" "Installing Docker Compose plugin..."
    apt-get update -qq &>/dev/null && apt-get install -y -qq docker-compose-plugin &>/dev/null
    if docker compose version &>/dev/null; then
        COMPOSE_VER=$(docker compose version --short 2>/dev/null)
        log_ok "COMPOSE" "Installed Docker Compose v${COMPOSE_VER}"
    else
        bail "COMPOSE" "Docker Compose installation failed"
    fi
fi

# --- 3. Firewall (ufw) ---
if command -v ufw &>/dev/null; then
    # Ensure default deny policy before allowing specific ports
    ufw default deny incoming &>/dev/null
    log_ok "FIREWALL" "Set default deny incoming"
    for port in "${REQUIRED_PORTS[@]}"; do
        if ufw status 2>/dev/null | grep -qw "$port"; then
            log_skip "FIREWALL" "Port $port already allowed"
        else
            ufw allow "$port/tcp" &>/dev/null
            log_ok "FIREWALL" "Allowed port $port/tcp"
        fi
    done
    # Enable ufw if not active (non-interactive)
    if ! ufw status 2>/dev/null | grep -q "Status: active"; then
        echo "y" | ufw enable &>/dev/null
        log_ok "FIREWALL" "ufw enabled"
    else
        log_skip "FIREWALL" "ufw already active"
    fi
else
    log_info "FIREWALL" "ufw not found — installing..."
    apt-get update -qq &>/dev/null && apt-get install -y -qq ufw &>/dev/null
    ufw default deny incoming &>/dev/null
    for port in "${REQUIRED_PORTS[@]}"; do
        ufw allow "$port/tcp" &>/dev/null
    done
    echo "y" | ufw enable &>/dev/null
    log_ok "FIREWALL" "Installed and configured ufw"
fi

# --- 4. Check port 80 conflicts ---
if ss -tlnp 2>/dev/null | grep -q ':80 '; then
    LISTENER=$(ss -tlnp 2>/dev/null | grep ':80 ' | head -1)
    # Allow Docker to be the listener (idempotent re-run)
    if echo "$LISTENER" | grep -q 'docker\|com.docker'; then
        log_skip "PORT_CHECK" "Port 80 in use by Docker (expected on re-run)"
    else
        log_fail "PORT_CHECK" "Port 80 in use by another process: $LISTENER"
        log_info "PORT_CHECK" "Stop the conflicting service before deploying"
        exit 1
    fi
else
    log_ok "PORT_CHECK" "Port 80 is available"
fi

# --- 5. Clone or update repo ---
if [ -d "$INSTALL_DIR/.git" ]; then
    log_info "REPO" "Repo exists at $INSTALL_DIR — pulling latest..."
    cd "$INSTALL_DIR"
    if git pull origin main &>/dev/null; then
        COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
        log_ok "REPO" "Updated to $COMMIT"
    else
        log_fail "REPO" "git pull failed — resolve conflicts manually"
        exit 1
    fi
    ALREADY_EXISTS=true
else
    log_info "REPO" "Cloning $REPO_URL to $INSTALL_DIR..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if git clone "$REPO_URL" "$INSTALL_DIR" &>/dev/null; then
        cd "$INSTALL_DIR"
        COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
        log_ok "REPO" "Cloned at $COMMIT"
    else
        bail "REPO" "git clone failed — check network and repo URL"
    fi
    ALREADY_EXISTS=false
fi

# --- Summary ---
echo ""
if [ "$ALREADY_EXISTS" = true ]; then
    log_ok "SETUP" "Server already configured — updated to latest"
    exit 2
else
    log_ok "SETUP" "Server initialization complete"
    exit 0
fi
