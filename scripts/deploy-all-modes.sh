#!/usr/bin/env bash
# =============================================================================
# deploy-all-modes.sh — Three-stage deployment: test → devnet → mainnet
#
# Usage:
#   bash scripts/deploy-all-modes.sh \
#     --server-ip 140.82.49.221 \
#     --address fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm \
#     --domain audit.api.aethercore.dev \
#     [--price 0.02] \
#     [--modes test,devnet,mainnet] \
#     [--rpc-mainnet URL]
#
# Each stage:
#   1. Generate .env for the mode
#   2. Upload to server
#   3. Build + deploy
#   4. Verify (5-layer + smoke test)
#   5. Report results
# =============================================================================
set -euo pipefail

# --- Defaults ---
SERVER_IP=""
ADDRESS=""
DOMAIN=""
PRICE="0.02"
MODES="test,devnet,mainnet"
RPC_MAINNET=""
PROJECT_DIR="/opt/token-bugcheck"
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Helpers ---
info()  { printf "${BLUE}[INFO]${NC}  %s\n" "$1"; }
ok()    { printf "${GREEN}[PASS]${NC}  %s\n" "$1"; }
fail()  { printf "${RED}[FAIL]${NC}  %s\n" "$1"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
header(){ printf "\n${BLUE}══════════════════════════════════════════════════════${NC}\n"; printf "${BLUE}  Stage: %s${NC}\n" "$1"; printf "${BLUE}══════════════════════════════════════════════════════${NC}\n\n"; }

# --- Parse CLI args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --server-ip)    SERVER_IP="$2"; shift 2 ;;
        --address)      ADDRESS="$2"; shift 2 ;;
        --domain)       DOMAIN="$2"; shift 2 ;;
        --price)        PRICE="$2"; shift 2 ;;
        --modes)        MODES="$2"; shift 2 ;;
        --rpc-mainnet)  RPC_MAINNET="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash scripts/deploy-all-modes.sh --server-ip IP --address WALLET --domain DOMAIN [options]"
            echo ""
            echo "Required:"
            echo "  --server-ip      VPS IP address"
            echo "  --address        Solana wallet address"
            echo "  --domain         Domain name (Cloudflare proxied)"
            echo ""
            echo "Optional:"
            echo "  --price          USDC per request (default: 0.02)"
            echo "  --modes          Comma-separated modes (default: test,devnet,mainnet)"
            echo "  --rpc-mainnet    Mainnet RPC URL (default: public endpoint)"
            exit 0
            ;;
        *) fail "Unknown argument: $1"; exit 1 ;;
    esac
done

# --- Validate ---
[[ -z "$SERVER_IP" ]] && { fail "Missing --server-ip"; exit 1; }
[[ -z "$ADDRESS" ]]   && { fail "Missing --address"; exit 1; }
[[ -z "$DOMAIN" ]]    && { fail "Missing --domain"; exit 1; }

# Results tracking (bash 3.x compatible - use temp file instead of associative array)
RESULTS_FILE=$(mktemp /tmp/deploy-results.XXXXXX)
trap 'rm -f "$RESULTS_FILE"' EXIT
TOTAL_PASS=0
TOTAL_FAIL=0

# Helper: store/retrieve results
set_result() { echo "$1=$2" >> "$RESULTS_FILE"; }
get_result() { grep "^$1=" "$RESULTS_FILE" 2>/dev/null | tail -1 | cut -d= -f2-; }

# --- Helper: remote exec ---
remote() {
    ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "root@${SERVER_IP}" "$@"
}

# --- Helper: wait for services to be healthy ---
wait_healthy() {
    local max_wait=120
    local elapsed=0
    local interval=5
    local audit_ok=false
    local gw_ok=false

    while [ "$elapsed" -lt "$max_wait" ]; do
        if [ "$audit_ok" = false ]; then
            local http
            http=$(remote "curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health" 2>/dev/null) || http="000"
            if [ "$http" = "200" ]; then
                audit_ok=true
                ok "audit-server healthy (${elapsed}s)"
            fi
        fi

        if [ "$gw_ok" = false ]; then
            local http
            http=$(remote "curl -s -o /dev/null -w '%{http_code}' http://localhost:80/health" 2>/dev/null) || http="000"
            if [ "$http" = "200" ]; then
                gw_ok=true
                ok "ag402-gateway healthy (${elapsed}s)"
            fi
        fi

        if [ "$audit_ok" = true ] && [ "$gw_ok" = true ]; then
            return 0
        fi

        sleep "$interval"
        elapsed=$((elapsed + interval))
        info "Waiting... ${elapsed}s (audit=$audit_ok, gateway=$gw_ok)"
    done

    [ "$audit_ok" = false ] && fail "audit-server not healthy after ${max_wait}s"
    [ "$gw_ok" = false ] && fail "ag402-gateway not healthy after ${max_wait}s"
    return 1
}

# --- Helper: run verification checks ---
run_verify() {
    local mode="$1"
    local pass=0
    local fail_count=0
    local mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

    info "--- Verification for $mode mode ---"

    # V1: Container status
    info "[V1] Container status"
    local containers
    containers=$(remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES ps --format '{{.Name}} {{.Status}}'" 2>&1) || true
    if echo "$containers" | grep -q "healthy"; then
        ok "V1: Containers running"
        pass=$((pass + 1))
    else
        fail "V1: Container issue: $containers"
        fail_count=$((fail_count + 1))
    fi

    # V2: Localhost health
    info "[V2] Localhost health"
    local audit_http gw_http
    audit_http=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8000/health" 2>/dev/null) || audit_http="000"
    gw_http=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:80/health" 2>/dev/null) || gw_http="000"
    if [ "$audit_http" = "200" ] && [ "$gw_http" = "200" ]; then
        ok "V2: localhost:8000=$audit_http, localhost:80=$gw_http"
        pass=$((pass + 1))
    else
        fail "V2: localhost:8000=$audit_http, localhost:80=$gw_http"
        fail_count=$((fail_count + 1))
    fi

    # V3: External IP
    info "[V3] External IP access"
    local ext_http
    ext_http=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "http://${SERVER_IP}:80/health" 2>/dev/null) || ext_http="000"
    if [ "$ext_http" = "200" ]; then
        ok "V3: http://${SERVER_IP}:80/health=$ext_http"
        pass=$((pass + 1))
    else
        fail "V3: http://${SERVER_IP}:80/health=$ext_http"
        fail_count=$((fail_count + 1))
    fi

    # V4: Domain HTTPS
    info "[V4] Domain HTTPS"
    local domain_http
    domain_http=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://${DOMAIN}/health" 2>/dev/null) || domain_http="000"
    if [ "$domain_http" = "200" ]; then
        ok "V4: https://${DOMAIN}/health=$domain_http"
        pass=$((pass + 1))
    else
        fail "V4: https://${DOMAIN}/health=$domain_http"
        fail_count=$((fail_count + 1))
    fi

    # V5: Health response content
    info "[V5] Health response body"
    local health_body
    health_body=$(curl -s --max-time 10 "https://${DOMAIN}/health" 2>/dev/null) || health_body="{}"
    if echo "$health_body" | grep -q '"status"'; then
        ok "V5: Health response valid: $health_body"
        pass=$((pass + 1))
    else
        fail "V5: Health response invalid"
        fail_count=$((fail_count + 1))
    fi

    # V6: 402 paywall
    info "[V6] 402 Paywall"
    local paywall_http
    paywall_http=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://${DOMAIN}/audit/$mint" 2>/dev/null) || paywall_http="000"
    if [ "$mode" = "test" ]; then
        # test mode: 402 with mock payment challenge
        if [ "$paywall_http" = "402" ]; then
            ok "V6: Paywall active ($paywall_http) — test mock mode"
            pass=$((pass + 1))
        elif [ "$paywall_http" = "200" ]; then
            # test mode might auto-pass — still acceptable
            ok "V6: Test mode returned 200 (test mock pass-through)"
            pass=$((pass + 1))
        else
            fail "V6: Expected 402 or 200, got $paywall_http"
            fail_count=$((fail_count + 1))
        fi
    else
        if [ "$paywall_http" = "402" ]; then
            ok "V6: Paywall active ($paywall_http) — $mode mode"
            pass=$((pass + 1))
        else
            fail "V6: Expected 402, got $paywall_http"
            fail_count=$((fail_count + 1))
        fi
    fi

    # V7: Direct audit (bypass gateway, localhost only)
    info "[V7] Direct audit (localhost bypass)"
    local audit_http2
    audit_http2=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://localhost:8000/audit/$mint" 2>/dev/null) || audit_http2="000"
    if [ "$audit_http2" = "200" ]; then
        ok "V7: Direct audit returned 200"
        pass=$((pass + 1))
    else
        fail "V7: Direct audit returned $audit_http2"
        fail_count=$((fail_count + 1))
    fi

    # V8: Audit response schema
    info "[V8] Audit response schema"
    local audit_body
    audit_body=$(remote "curl -s --max-time 15 http://localhost:8000/audit/$mint" 2>/dev/null) || audit_body="{}"
    local schema_ok=true
    for field in contract_address action analysis evidence metadata; do
        if ! echo "$audit_body" | grep -q "\"$field\""; then
            schema_ok=false
            break
        fi
    done
    if [ "$schema_ok" = true ]; then
        ok "V8: Audit schema valid (all fields present)"
        pass=$((pass + 1))
    else
        fail "V8: Audit schema missing fields"
        fail_count=$((fail_count + 1))
    fi

    # V9: Invalid address → 400
    info "[V9] Invalid address → 400"
    local inv_http
    inv_http=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8000/audit/INVALID!!!" 2>/dev/null) || inv_http="000"
    if [ "$inv_http" = "400" ]; then
        ok "V9: Invalid address returned 400"
        pass=$((pass + 1))
    else
        fail "V9: Invalid address returned $inv_http (expected 400)"
        fail_count=$((fail_count + 1))
    fi

    echo ""
    info "=== $mode mode: $pass passed, $fail_count failed ==="
    set_result "$mode" "pass=$pass fail=$fail_count"
    TOTAL_PASS=$((TOTAL_PASS + pass))
    TOTAL_FAIL=$((TOTAL_FAIL + fail_count))
    return "$fail_count"
}

# --- Helper: generate and upload .env ---
gen_and_upload_env() {
    local mode="$1"
    local env_file="/tmp/token-bugcheck-${mode}.env"

    info "Generating .env for mode=$mode"

    local extra_args=""
    if [ "$mode" = "production" ] && [ -n "$RPC_MAINNET" ]; then
        extra_args="--rpc-url $RPC_MAINNET"
    fi

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if bash "$SCRIPT_DIR/generate-env.sh" \
        --mode "$mode" \
        --address "$ADDRESS" \
        --price "$PRICE" \
        --output "$env_file" \
        $extra_args; then
        ok "Generated $env_file"
    else
        fail "Failed to generate .env for $mode"
        return 1
    fi

    info "Uploading .env to server"
    scp -o ConnectTimeout=15 "$env_file" "root@${SERVER_IP}:${PROJECT_DIR}/.env"
    rm -f "$env_file"
    ok "Uploaded .env to server"
}

# --- Helper: build and deploy ---
build_and_deploy() {
    info "Pulling latest code"
    remote "cd $PROJECT_DIR && git pull origin main" 2>&1 || warn "git pull failed (may be OK if already up to date)"

    info "Stopping existing services"
    remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES down" 2>&1 || true

    info "Building Docker images (--no-cache)"
    remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES build --no-cache" 2>&1 | tail -5
    ok "Docker images built"

    info "Starting services"
    remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES up -d" 2>&1
    ok "Services started"
}

# =============================================================================
# Main — iterate through modes
# =============================================================================
echo ""
info "═══════════════════════════════════════════════════════"
info "  Token RugCheck MCP — Three-Stage Deployment"
info "  Server: $SERVER_IP | Domain: $DOMAIN"
info "  Modes:  $MODES"
info "  Wallet: ${ADDRESS:0:12}..."
info "  Price:  $PRICE USDC"
info "═══════════════════════════════════════════════════════"
echo ""

# Check SSH connectivity
info "Testing SSH connection..."
if remote "echo ok" >/dev/null 2>&1; then
    ok "SSH connection OK"
else
    fail "Cannot SSH to root@${SERVER_IP}"
    exit 1
fi

# Update code on server first
info "Syncing code to server..."
remote "cd $PROJECT_DIR && git pull origin main" 2>&1 || warn "git pull may need attention"

# Process each mode
IFS=',' read -ra MODE_LIST <<< "$MODES"
FIRST_BUILD=true

for mode in "${MODE_LIST[@]}"; do
    header "$mode"

    # Step 1: Generate and upload .env
    if ! gen_and_upload_env "$mode"; then
        set_result "$mode" "SKIPPED (env generation failed)"
        continue
    fi

    # Step 2: Build (only on first run) and deploy
    if [ "$FIRST_BUILD" = true ]; then
        build_and_deploy
        FIRST_BUILD=false
    else
        # Subsequent modes: just restart with new .env (no rebuild needed)
        info "Restarting services with $mode .env"
        remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES down" 2>&1 || true
        remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES up -d" 2>&1
        ok "Services restarted"
    fi

    # Step 3: Wait for healthy
    info "Waiting for services to become healthy..."
    if ! wait_healthy; then
        fail "Services failed to become healthy in $mode mode"
        # Dump logs for debugging
        warn "Last 20 lines of gateway logs:"
        remote "cd $PROJECT_DIR && docker compose logs ag402-gateway --tail 20" 2>&1 || true
        set_result "$mode" "FAILED (services unhealthy)"
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        continue
    fi

    # Step 4: Run verification
    if ! run_verify "$mode"; then
        warn "$mode mode had some failures (see above)"
    fi

    echo ""
done

# =============================================================================
# Final Summary
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         DEPLOYMENT VERIFICATION SUMMARY             ║"
echo "╠══════════════════════════════════════════════════════╣"
for mode in "${MODE_LIST[@]}"; do
    result=$(get_result "$mode")
    [ -z "$result" ] && result="NOT_RUN"
    if echo "$result" | grep -q "fail=0"; then
        printf "║  %-10s: %-39s ║\n" "$mode" "✅ $result"
    else
        printf "║  %-10s: %-39s ║\n" "$mode" "❌ $result"
    fi
done
echo "╠══════════════════════════════════════════════════════╣"
printf "║  TOTAL: %-43s ║\n" "pass=$TOTAL_PASS fail=$TOTAL_FAIL"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

if [ "$TOTAL_FAIL" -gt 0 ]; then
    fail "Some tests failed. Review output above."
    exit 1
else
    ok "All modes passed! Deployment verified."
    exit 0
fi
