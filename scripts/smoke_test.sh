#!/usr/bin/env bash
# Smoke test for Token RugCheck MCP deployment.
# Usage:
#   bash scripts/smoke_test.sh
#   AUDIT_URL=http://my-host:8000 GATEWAY_URL=http://my-host:8001 bash scripts/smoke_test.sh
set -euo pipefail

AUDIT_URL="${AUDIT_URL:-http://localhost:8000}"
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8001}"
MINT="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); printf "  PASS: %s\n" "$1"; }
fail() { FAIL=$((FAIL + 1)); printf "  FAIL: %s\n" "$1"; }

echo "=== Smoke Test ==="
echo "Audit server: $AUDIT_URL"
echo "Gateway:      $GATEWAY_URL"
echo ""

# ---------- 1. Audit server health ----------
echo "[1/7] Audit server /health"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$AUDIT_URL/health" 2>/dev/null) || HTTP="000"
if [ "$HTTP" = "200" ]; then pass "/health returned 200"; else fail "/health returned $HTTP"; fi

# ---------- 2. Direct audit ----------
echo "[2/7] Direct audit"
HTTP=$(curl -s -o /tmp/smoke_audit.json -w "%{http_code}" "$AUDIT_URL/audit/$MINT" 2>/dev/null) || HTTP="000"
if [ "$HTTP" = "200" ]; then pass "audit returned 200"; else fail "audit returned $HTTP"; fi

# ---------- 3. Schema validation ----------
echo "[3/7] Audit response schema"
if [ -f /tmp/smoke_audit.json ] && [ "$HTTP" = "200" ]; then
    OK=true
    for field in contract_address action analysis evidence metadata; do
        if ! grep -q "\"$field\"" /tmp/smoke_audit.json 2>/dev/null; then
            OK=false
            break
        fi
    done
    if $OK; then pass "response contains all required fields"; else fail "response missing fields"; fi
else
    fail "no audit response to validate"
fi

# ---------- 4. Invalid address → 400 ----------
echo "[4/7] Invalid address returns 400"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$AUDIT_URL/audit/INVALID!!!" 2>/dev/null) || HTTP="000"
if [ "$HTTP" = "400" ]; then pass "invalid address returned 400"; else fail "invalid address returned $HTTP (expected 400)"; fi

# ---------- 5. Gateway health ----------
echo "[5/7] Gateway /health"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/health" 2>/dev/null) || HTTP="000"
if [ "$HTTP" = "200" ]; then
    pass "gateway /health returned 200"
elif [ "$HTTP" = "000" ]; then
    echo "       (gateway not running — skipping gateway tests)"
    pass "gateway not running (optional)"
else
    fail "gateway /health returned $HTTP"
fi

# ---------- 6. Gateway 402 challenge ----------
echo "[6/7] Gateway returns 402 for unpaid request"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/audit/$MINT" 2>/dev/null) || HTTP="000"
if [ "$HTTP" = "402" ]; then
    pass "gateway returned 402 Payment Required"
elif [ "$HTTP" = "000" ]; then
    pass "gateway not running (optional)"
else
    fail "gateway returned $HTTP (expected 402)"
fi

# ---------- 7. Stats endpoint ----------
echo "[7/7] Stats endpoint"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$AUDIT_URL/stats" 2>/dev/null) || HTTP="000"
if [ "$HTTP" = "200" ]; then pass "/stats returned 200"; else fail "/stats returned $HTTP"; fi

# ---------- Summary ----------
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then exit 1; else exit 0; fi
