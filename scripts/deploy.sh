#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Master deployment orchestration for Token RugCheck MCP
#
# Usage:
#   bash scripts/deploy.sh [--project-dir /opt/token-bugcheck] [--server-ip IP] [--domain DOMAIN]
#
# Phases:
#   1. Validate .env
#   2. Docker Compose build
#   3. Docker Compose up
#   4. Health check polling (max 90s)
#   5. Verification (calls scripts/verify.sh)
#
# Output format: STATUS|COMPONENT|MESSAGE
# Exit codes: 0=success, 1=error
# =============================================================================
set -euo pipefail

# --- Defaults ---
PROJECT_DIR="${PROJECT_DIR:-/opt/token-bugcheck}"
SERVER_IP=""
DOMAIN=""
MAX_HEALTH_WAIT=90
HEALTH_INTERVAL=5

# --- Helpers ---
log_ok()   { echo "OK|$1|$2"; }
log_fail() { echo "FAIL|$1|$2"; }
log_info() { echo "INFO|$1|$2"; }
log_skip() { echo "SKIP|$1|$2"; }
bail()     { log_fail "$1" "$2"; exit 1; }

# --- Parse CLI args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-dir) PROJECT_DIR="$2"; shift 2 ;;
        --server-ip)   SERVER_IP="$2"; shift 2 ;;
        --domain)      DOMAIN="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash scripts/deploy.sh [--project-dir DIR] [--server-ip IP] [--domain DOMAIN]"
            exit 0
            ;;
        *) bail "ARGS" "Unknown argument: $1" ;;
    esac
done

# --- Pre-flight ---
if [ ! -d "$PROJECT_DIR" ]; then
    bail "PREFLIGHT" "Project directory not found: $PROJECT_DIR"
fi

cd "$PROJECT_DIR"
log_info "DEPLOY" "Starting deployment in $PROJECT_DIR"

# ============================================================
# Phase 1: Validate .env
# ============================================================
log_info "PHASE1" "Validating .env file"

if [ ! -f ".env" ]; then
    bail "PHASE1" ".env file not found — run scripts/generate-env.sh first"
fi

# Check for placeholder values
PLACEHOLDERS=0
while IFS='=' read -r key value; do
    # Skip comments and empty lines
    [[ "$key" =~ ^#.*$ ]] && continue
    [[ -z "$key" ]] && continue
    # Trim whitespace
    value=$(echo "$value" | xargs)
    # Check for common placeholder patterns
    if echo "$value" | grep -qP '<.*>|YOUR_|CHANGE_ME|TODO|FIXME'; then
        log_fail "PHASE1" "Placeholder found: $key=$value"
        PLACEHOLDERS=$((PLACEHOLDERS + 1))
    fi
done < .env

if [ "$PLACEHOLDERS" -gt 0 ]; then
    bail "PHASE1" "$PLACEHOLDERS placeholder value(s) in .env — edit before deploying"
fi

# Verify critical variables are set
for var in AG402_ADDRESS X402_MODE; do
    val=$(grep "^$var=" .env 2>/dev/null | cut -d'=' -f2- | xargs)
    if [ -z "$val" ]; then
        bail "PHASE1" "Required variable $var is empty in .env"
    fi
done

log_ok "PHASE1" ".env validated — no placeholders, critical vars present"

# ============================================================
# Phase 2: Docker Compose build
# ============================================================
log_info "PHASE2" "Building Docker images"

COMPOSE_FILES="-f docker-compose.yml"
if [ -f "docker-compose.prod.yml" ]; then
    COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.prod.yml"
    log_info "PHASE2" "Using production override (docker-compose.prod.yml)"
fi

if docker compose $COMPOSE_FILES build 2>&1 | tail -5; then
    log_ok "PHASE2" "Docker images built successfully"
else
    bail "PHASE2" "Docker build failed — check Dockerfile errors above"
fi

# ============================================================
# Phase 3: Docker Compose up
# ============================================================
log_info "PHASE3" "Starting services"

# Stop existing containers first (idempotent)
docker compose $COMPOSE_FILES down 2>/dev/null || true

if docker compose $COMPOSE_FILES up -d 2>&1; then
    log_ok "PHASE3" "Services started"
else
    bail "PHASE3" "docker compose up failed"
fi

# ============================================================
# Phase 4: Health check polling
# ============================================================
log_info "PHASE4" "Waiting for services to become healthy (max ${MAX_HEALTH_WAIT}s)"

ELAPSED=0
AUDIT_HEALTHY=false
GATEWAY_HEALTHY=false

while [ "$ELAPSED" -lt "$MAX_HEALTH_WAIT" ]; do
    # Check audit server
    if [ "$AUDIT_HEALTHY" = false ]; then
        HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null) || HTTP="000"
        if [ "$HTTP" = "200" ]; then
            AUDIT_HEALTHY=true
            log_ok "PHASE4" "audit-server healthy (${ELAPSED}s)"
        fi
    fi

    # Check gateway (on port 80 if prod override, else 8001)
    if [ "$GATEWAY_HEALTHY" = false ]; then
        # Try port 80 first (production), then 8001 (dev)
        HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:80/health 2>/dev/null) || HTTP="000"
        if [ "$HTTP" != "200" ]; then
            HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/health 2>/dev/null) || HTTP="000"
        fi
        if [ "$HTTP" = "200" ]; then
            GATEWAY_HEALTHY=true
            log_ok "PHASE4" "ag402-gateway healthy (${ELAPSED}s)"
        fi
    fi

    # Both healthy?
    if [ "$AUDIT_HEALTHY" = true ] && [ "$GATEWAY_HEALTHY" = true ]; then
        log_ok "PHASE4" "All services healthy after ${ELAPSED}s"
        break
    fi

    sleep "$HEALTH_INTERVAL"
    ELAPSED=$((ELAPSED + HEALTH_INTERVAL))
    log_info "PHASE4" "Waiting... ${ELAPSED}s (audit=$AUDIT_HEALTHY, gateway=$GATEWAY_HEALTHY)"
done

if [ "$AUDIT_HEALTHY" = false ] || [ "$GATEWAY_HEALTHY" = false ]; then
    log_fail "PHASE4" "Timeout: audit=$AUDIT_HEALTHY, gateway=$GATEWAY_HEALTHY"
    log_info "PHASE4" "Check logs: docker compose logs --tail 50"
    exit 1
fi

# ============================================================
# Phase 5: Verification
# ============================================================
log_info "PHASE5" "Running verification"

VERIFY_ARGS=""
if [ -n "$SERVER_IP" ]; then
    VERIFY_ARGS="$VERIFY_ARGS --server-ip $SERVER_IP"
fi
if [ -n "$DOMAIN" ]; then
    VERIFY_ARGS="$VERIFY_ARGS --domain $DOMAIN"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/verify.sh" ]; then
    if bash "$SCRIPT_DIR/verify.sh" $VERIFY_ARGS; then
        log_ok "PHASE5" "All verification checks passed"
    else
        log_fail "PHASE5" "Some verification checks failed — review output above"
        exit 1
    fi
else
    log_skip "PHASE5" "verify.sh not found — skipping automated verification"
fi

# ============================================================
# Summary
# ============================================================
echo ""
log_ok "DEPLOY" "Deployment complete"
log_info "DEPLOY" "Audit server: http://localhost:8000"
log_info "DEPLOY" "Gateway: http://localhost:80 (or :8001)"
if [ -n "$DOMAIN" ]; then
    log_info "DEPLOY" "Domain: https://$DOMAIN"
fi
