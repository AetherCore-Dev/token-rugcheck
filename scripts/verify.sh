#!/usr/bin/env bash
# =============================================================================
# verify.sh — 5-layer deployment verification for Token RugCheck MCP
#
# Usage:
#   bash scripts/verify.sh [--server-ip IP] [--domain DOMAIN] [--phase L1|L2|L3|L4|L5]
#
# Verification layers:
#   L1: Docker containers running + health status
#   L2: Host port accessibility (localhost:8000, localhost:80/8001)
#   L3: External IP accessibility (requires --server-ip)
#   L4: Domain HTTPS (requires --domain)
#   L5: Functional tests (402 paywall, direct audit, schema, stats, metrics)
#
# Output format: STATUS|COMPONENT|MESSAGE
# Summary line:  VERIFY|SUMMARY|pass=N fail=N skip=N
# Exit codes: 0=all pass, 1=any fail
# =============================================================================
set -euo pipefail

# --- Defaults ---
SERVER_IP=""
DOMAIN=""
PHASE=""
MINT="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# --- Counters ---
PASS=0
FAIL=0
SKIP=0

# --- Helpers ---
log_ok()   { echo "OK|$1|$2"; PASS=$((PASS + 1)); }
log_fail() { echo "FAIL|$1|$2"; FAIL=$((FAIL + 1)); }
log_skip() { echo "SKIP|$1|$2"; SKIP=$((SKIP + 1)); }
log_info() { echo "INFO|$1|$2"; }

# --- Parse CLI args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --server-ip) SERVER_IP="$2"; shift 2 ;;
        --domain)    DOMAIN="$2"; shift 2 ;;
        --phase)     PHASE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash scripts/verify.sh [--server-ip IP] [--domain DOMAIN] [--phase L1|L2|L3|L4|L5]"
            exit 0
            ;;
        *) echo "FAIL|ARGS|Unknown argument: $1"; exit 1 ;;
    esac
done

should_run() {
    [ -z "$PHASE" ] || [ "$PHASE" = "$1" ]
}

# ============================================================
# L1: Docker containers
# ============================================================
if should_run "L1"; then
    log_info "L1" "Checking Docker containers"

    # Check audit-server container
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'audit-server'; then
        HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$(docker ps --format '{{.Names}}' | grep audit-server | head -1)" 2>/dev/null || echo "unknown")
        if [ "$HEALTH" = "healthy" ]; then
            log_ok "L1" "audit-server container running (healthy)"
        else
            log_fail "L1" "audit-server container running but health=$HEALTH"
        fi
    else
        log_fail "L1" "audit-server container not found"
    fi

    # Check gateway container
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'ag402-gateway'; then
        HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$(docker ps --format '{{.Names}}' | grep ag402-gateway | head -1)" 2>/dev/null || echo "unknown")
        # "starting" means healthcheck hasn't run yet (30s interval) — if the port
        # responds, treat it as OK since L2 will do the real connectivity check.
        if [ "$HEALTH" = "healthy" ]; then
            log_ok "L1" "ag402-gateway container running (healthy)"
        elif [ "$HEALTH" = "starting" ]; then
            # Container is up but first healthcheck hasn't completed — acceptable
            log_ok "L1" "ag402-gateway container running (starting — healthcheck pending)"
        else
            log_fail "L1" "ag402-gateway container running but health=$HEALTH"
        fi
    else
        log_fail "L1" "ag402-gateway container not found"
    fi
fi

# ============================================================
# L2: Host port accessibility
# ============================================================
if should_run "L2"; then
    log_info "L2" "Checking host port accessibility"

    # Audit server on port 8000
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8000/health 2>/dev/null) || HTTP="000"
    if [ "$HTTP" = "200" ]; then
        log_ok "L2" "localhost:8000/health returned 200"
    else
        log_fail "L2" "localhost:8000/health returned $HTTP (expected 200)"
    fi

    # Gateway — try port 80 (production) then 8001 (dev)
    HTTP80=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:80/health 2>/dev/null) || HTTP80="000"
    HTTP8001=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8001/health 2>/dev/null) || HTTP8001="000"

    if [ "$HTTP80" = "200" ]; then
        log_ok "L2" "localhost:80/health returned 200 (production port)"
    elif [ "$HTTP8001" = "200" ]; then
        log_ok "L2" "localhost:8001/health returned 200 (dev port)"
    else
        log_fail "L2" "Gateway not accessible on port 80 ($HTTP80) or 8001 ($HTTP8001)"
    fi
fi

# ============================================================
# L3: External IP accessibility
# ============================================================
if should_run "L3"; then
    if [ -n "$SERVER_IP" ]; then
        log_info "L3" "Checking external IP accessibility ($SERVER_IP)"

        HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "http://$SERVER_IP:80/health" 2>/dev/null) || HTTP="000"
        if [ "$HTTP" = "200" ]; then
            log_ok "L3" "$SERVER_IP:80/health returned 200"
        else
            log_fail "L3" "$SERVER_IP:80/health returned $HTTP (expected 200)"
        fi

        # Also check audit server direct access
        HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "http://$SERVER_IP:8000/health" 2>/dev/null) || HTTP="000"
        if [ "$HTTP" = "200" ]; then
            log_ok "L3" "$SERVER_IP:8000/health returned 200"
        else
            log_skip "L3" "$SERVER_IP:8000/health returned $HTTP (port 8000 may be firewalled)"
        fi
    else
        log_skip "L3" "No --server-ip provided — skipping external IP check"
    fi
fi

# ============================================================
# L4: Domain HTTPS
# ============================================================
if should_run "L4"; then
    if [ -n "$DOMAIN" ]; then
        log_info "L4" "Checking domain HTTPS ($DOMAIN)"

        HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://$DOMAIN/health" 2>/dev/null) || HTTP="000"
        if [ "$HTTP" = "200" ]; then
            log_ok "L4" "https://$DOMAIN/health returned 200"
        elif [ "$HTTP" = "000" ]; then
            log_fail "L4" "https://$DOMAIN/health connection failed (DNS or Cloudflare not configured)"
        else
            log_fail "L4" "https://$DOMAIN/health returned $HTTP (expected 200)"
        fi
    else
        log_skip "L4" "No --domain provided — skipping domain HTTPS check"
    fi
fi

# ============================================================
# L5: Functional tests
# ============================================================
if should_run "L5"; then
    log_info "L5" "Running functional tests"

    # Determine gateway URL (prefer domain, then localhost:80, then localhost:8001)
    if [ -n "$DOMAIN" ]; then
        GW_URL="https://$DOMAIN"
    elif curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://localhost:80/health 2>/dev/null | grep -q "200"; then
        GW_URL="http://localhost:80"
    else
        GW_URL="http://localhost:8001"
    fi
    AUDIT_URL="http://localhost:8000"

    log_info "L5" "Using gateway=$GW_URL, audit=$AUDIT_URL"

    # L5.1: 402 paywall check
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$GW_URL/audit/$MINT" 2>/dev/null) || HTTP="000"
    if [ "$HTTP" = "402" ]; then
        log_ok "L5" "Gateway returns 402 Payment Required (paywall active)"
    elif [ "$HTTP" = "200" ]; then
        log_fail "L5" "Gateway returned 200 without payment — paywall may be disabled"
    else
        log_fail "L5" "Gateway audit returned $HTTP (expected 402)"
    fi

    # L5.2: Direct audit (bypassing gateway)
    HTTP=$(curl -s -o /tmp/verify_audit.json -w "%{http_code}" --max-time 15 "$AUDIT_URL/audit/$MINT" 2>/dev/null) || HTTP="000"
    if [ "$HTTP" = "200" ]; then
        log_ok "L5" "Direct audit returned 200"
    else
        log_fail "L5" "Direct audit returned $HTTP (expected 200)"
    fi

    # L5.3: Schema validation
    if [ -f /tmp/verify_audit.json ] && [ "$HTTP" = "200" ]; then
        ALL_FIELDS=true
        for field in contract_address action analysis evidence metadata; do
            if ! grep -q "\"$field\"" /tmp/verify_audit.json 2>/dev/null; then
                ALL_FIELDS=false
                break
            fi
        done
        if [ "$ALL_FIELDS" = true ]; then
            log_ok "L5" "Audit response schema valid (all required fields present)"
        else
            log_fail "L5" "Audit response missing required fields"
        fi
    else
        log_skip "L5" "No audit response to validate schema"
    fi

    # L5.4: Stats endpoint
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$AUDIT_URL/stats" 2>/dev/null) || HTTP="000"
    if [ "$HTTP" = "200" ]; then
        log_ok "L5" "/stats endpoint returned 200"
    else
        log_fail "L5" "/stats returned $HTTP (expected 200)"
    fi

    # L5.5: Metrics endpoint
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$AUDIT_URL/metrics" 2>/dev/null) || HTTP="000"
    if [ "$HTTP" = "200" ]; then
        log_ok "L5" "/metrics endpoint returned 200"
    else
        log_fail "L5" "/metrics returned $HTTP (expected 200)"
    fi

    # Cleanup
    rm -f /tmp/verify_audit.json
fi

# ============================================================
# Summary
# ============================================================
echo ""
echo "VERIFY|SUMMARY|pass=$PASS fail=$FAIL skip=$SKIP"

if [ "$FAIL" -gt 0 ]; then
    exit 1
else
    exit 0
fi
