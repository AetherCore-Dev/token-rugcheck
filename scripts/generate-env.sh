#!/usr/bin/env bash
# =============================================================================
# generate-env.sh — Generate .env file from CLI arguments
#
# Usage:
#   bash scripts/generate-env.sh \
#     --mode test|devnet|production \
#     --address <SOLANA_WALLET_ADDRESS> \
#     --price 0.05 \
#     [--private-key <BASE58_KEY> | --private-key-file <PATH>] \
#     [--rpc-url <URL>] \
#     [--output .env]
#
# Output format: STATUS|COMPONENT|MESSAGE
# Exit codes: 0=success, 1=validation error
# =============================================================================
set -euo pipefail

# --- Helpers ---
log_ok()   { echo "OK|$1|$2"; }
log_fail() { echo "FAIL|$1|$2"; }
log_info() { echo "INFO|$1|$2"; }
bail()     { log_fail "$1" "$2"; exit 1; }

# --- Defaults ---
MODE=""
ADDRESS=""
PRICE="0.05"
PRIVATE_KEY=""
PRIVATE_KEY_FILE=""
RPC_URL=""
OUTPUT=".env"
LOG_LEVEL="info"
GOPLUS_APP_KEY=""
GOPLUS_APP_SECRET=""

# --- Parse CLI args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)           MODE="$2"; shift 2 ;;
        --address)        ADDRESS="$2"; shift 2 ;;
        --price)          PRICE="$2"; shift 2 ;;
        --private-key)    PRIVATE_KEY="$2"; shift 2 ;;
        --private-key-file) PRIVATE_KEY_FILE="$2"; shift 2 ;;
        --rpc-url)        RPC_URL="$2"; shift 2 ;;
        --output)         OUTPUT="$2"; shift 2 ;;
        --log-level)      LOG_LEVEL="$2"; shift 2 ;;
        --goplus-key)     GOPLUS_APP_KEY="$2"; shift 2 ;;
        --goplus-secret)  GOPLUS_APP_SECRET="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash scripts/generate-env.sh --mode <test|devnet|production> --address <WALLET> --price <USDC> [options]"
            echo ""
            echo "Required:"
            echo "  --mode              test, devnet, or production"
            echo "  --address           Solana wallet address (base58, 32-44 chars)"
            echo ""
            echo "Optional:"
            echo "  --price             USDC per request (default: 0.05)"
            echo "  --private-key       Solana private key, base58 (required for devnet/production)"
            echo "  --private-key-file  Path to file containing the private key"
            echo "  --rpc-url           Solana RPC URL (auto-derived from mode if omitted)"
            echo "  --output            Output file path (default: .env)"
            echo "  --log-level         Log level: debug|info|warning|error (default: info)"
            echo "  --goplus-key        GoPlus API key (optional)"
            echo "  --goplus-secret     GoPlus API secret (optional)"
            exit 0
            ;;
        *) bail "ARGS" "Unknown argument: $1" ;;
    esac
done

# --- Validate required args ---
if [ -z "$MODE" ]; then
    bail "VALIDATE" "Missing required argument: --mode"
fi

if [ -z "$ADDRESS" ]; then
    bail "VALIDATE" "Missing required argument: --address"
fi

# --- Validate mode ---
case "$MODE" in
    test|devnet|production) ;;
    *) bail "VALIDATE" "Invalid mode '$MODE' — must be test, devnet, or production" ;;
esac

# --- Validate Solana address (base58, 32-44 characters) ---
if ! echo "$ADDRESS" | grep -qP '^[1-9A-HJ-NP-Za-km-z]{32,44}$'; then
    bail "VALIDATE" "Invalid Solana address format: '$ADDRESS' (must be base58, 32-44 chars)"
fi
log_ok "VALIDATE" "Address format valid: ${ADDRESS:0:8}...${ADDRESS: -4}"

# --- Resolve private key from file if specified ---
if [ -n "$PRIVATE_KEY_FILE" ] && [ -z "$PRIVATE_KEY" ]; then
    if [ ! -f "$PRIVATE_KEY_FILE" ]; then
        bail "VALIDATE" "Private key file not found: $PRIVATE_KEY_FILE"
    fi
    PRIVATE_KEY=$(tr -d '[:space:]' < "$PRIVATE_KEY_FILE")
    log_ok "VALIDATE" "Read private key from file"
fi

# --- Validate private key for non-test modes ---
if [ "$MODE" != "test" ]; then
    if [ -z "$PRIVATE_KEY" ]; then
        bail "VALIDATE" "Mode '$MODE' requires --private-key or --private-key-file"
    fi
    # Basic base58 check (private keys are typically 64-88 chars)
    if ! echo "$PRIVATE_KEY" | grep -qP '^[1-9A-HJ-NP-Za-km-z]{44,88}$'; then
        bail "VALIDATE" "Private key does not look like valid base58 (expected 44-88 chars)"
    fi
    log_ok "VALIDATE" "Private key format valid (${#PRIVATE_KEY} chars)"
fi

# --- Derive mode-specific values ---
case "$MODE" in
    test)
        X402_MODE="test"
        X402_NETWORK="mock"
        DEFAULT_RPC=""
        ;;
    devnet)
        X402_MODE="production"
        X402_NETWORK="devnet"
        DEFAULT_RPC="https://api.devnet.solana.com"
        ;;
    production)
        X402_MODE="production"
        X402_NETWORK="mainnet"
        DEFAULT_RPC="https://api.mainnet-beta.solana.com"
        ;;
esac

# Use provided RPC URL or default
if [ -z "$RPC_URL" ]; then
    RPC_URL="$DEFAULT_RPC"
fi

log_info "DERIVE" "X402_MODE=$X402_MODE, X402_NETWORK=$X402_NETWORK"
if [ -n "$RPC_URL" ]; then
    log_info "DERIVE" "SOLANA_RPC_URL=$RPC_URL"
fi

# --- Validate price ---
if ! echo "$PRICE" | grep -qP '^\d+(\.\d+)?$'; then
    bail "VALIDATE" "Invalid price format: '$PRICE' (must be a number)"
fi
log_ok "VALIDATE" "Price: $PRICE USDC"

# --- Generate .env ---
cat > "$OUTPUT" <<ENVFILE
# =============================================================================
# Token RugCheck MCP — Environment Configuration
# Generated by scripts/generate-env.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Mode: $MODE
# =============================================================================

# --- Service ---
RUGCHECK_HOST=0.0.0.0
RUGCHECK_PORT=8000
RUGCHECK_LOG_LEVEL=$LOG_LEVEL

# --- Cache ---
CACHE_TTL_SECONDS=3
CACHE_MAX_SIZE=5000

# --- Rate Limiting ---
FREE_DAILY_QUOTA=20
PAID_RATE_LIMIT=120

# --- Upstream API Timeouts (seconds) ---
DEXSCREENER_TIMEOUT_SECONDS=1.5
GOPLUS_TIMEOUT_SECONDS=2.5
RUGCHECK_API_TIMEOUT_SECONDS=3.5

# --- GoPlus Authentication (optional) ---
GOPLUS_APP_KEY=$GOPLUS_APP_KEY
GOPLUS_APP_SECRET=$GOPLUS_APP_SECRET

# --- ag402 Payment Gateway ---
AG402_PRICE=$PRICE
AG402_CHAIN=solana
AG402_TOKEN=USDC
AG402_ADDRESS=$ADDRESS
AG402_GATEWAY_PORT=8001
AG402_GATEWAY_HOST=0.0.0.0
AG402_TARGET_URL=http://localhost:8000

# --- ag402 Mode ---
X402_MODE=$X402_MODE
X402_NETWORK=$X402_NETWORK
ENVFILE

# Add Solana keys only for non-test modes
if [ "$MODE" != "test" ]; then
    cat >> "$OUTPUT" <<ENVFILE

# --- Solana Wallet ---
SOLANA_PRIVATE_KEY=$PRIVATE_KEY
SOLANA_RPC_URL=$RPC_URL
ENVFILE
fi

log_ok "GENERATE" "Written to $OUTPUT ($(wc -l < "$OUTPUT") lines)"
log_ok "GENERATE" "mode=$MODE address=${ADDRESS:0:8}... price=$PRICE"
