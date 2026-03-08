# Token RugCheck — Solana Token Safety Audit for AI Agents

> **We're LIVE on Mainnet!** Try it now: [`https://rugcheck.aethercore.dev`](https://rugcheck.aethercore.dev)

```bash
# Quick test — no setup needed
curl https://rugcheck.aethercore.dev/health

# See the 402 paywall in action
curl https://rugcheck.aethercore.dev/v1/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

**What you get**: A three-layer safety audit for any Solana token — machine-readable verdict, LLM-friendly analysis, and raw evidence — all in one API call for **$0.02 USDC**.

Powered by [ag402](https://github.com/AetherCore-Dev/ag402) on-chain micropayments.

> **Disclaimer:** Not financial advice (NFA). Token safety scores are automated heuristics — may be inaccurate or outdated. DYOR. The authors accept no liability for losses.

---

## Live Demos (Mainnet)

> All demos below are **real mainnet transactions** — left side is the client, right side is the production server logs.

**1. Wallet Setup** (`ag402 setup`)

https://github.com/user-attachments/assets/fb6b1ecb-8d42-43b7-9939-4751ca09b63f

**2. Auditing a Safe Token** (BONK — risk score 3, SAFE)

https://github.com/user-attachments/assets/4b3814ae-96af-496b-86b7-c80cddef1475

**3. Auditing a Risky Token** (TRUMP — risk score 60, HIGH)

https://github.com/user-attachments/assets/6e359374-8caf-41c7-8bd1-14f63eb6d6e8

---

## How It Works

```
Your AI Agent                        RugCheck Service
     │                                      │
     │  GET /v1/audit/{mint}                │
     ├─────────────────────────────────────▶│
     │                                      │
     │  402 Payment Required                │
     │  (pay 0.02 USDC on Solana)           │
     │◀─────────────────────────────────────┤
     │                                      │
     │  USDC payment (on-chain)             │
     ├─────────────────────────────────────▶│
     │                                      │
     │  200 OK + Audit Report               │
     │◀─────────────────────────────────────┤
```

Input a Solana token mint address → get a three-layer report:

| Layer | For | Content |
|-------|-----|---------|
| **Action** | Machines | `is_safe`, `risk_score` (0-100), `risk_level` (SAFE/LOW/MEDIUM/HIGH/CRITICAL) |
| **Analysis** | LLMs | Summary, red flags, green flags |
| **Evidence** | Humans | Price, liquidity, holder distribution, mint/freeze authority, raw data |

Data sources: **RugCheck.xyz** + **DexScreener** + **GoPlus Security** (concurrent fetch, graceful degradation).

---

## Try It Now (3 minutes)

### Step 1: Install

```bash
pip install "ag402-core[crypto]" httpx
```

### Step 2: Set up your wallet

```bash
ag402 setup
# Choose: Consumer → Mainnet
# Enter your Solana private key (encrypted locally with AES)
# Set safety limits (default: $10/day max)
```

### Step 3: Run an audit

```bash
# Command-line test script (included in this repo)
python3 mainnet_buyer_test.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

Or in Python:

```python
import asyncio, httpx, ag402_core

ag402_core.enable()  # Auto-handles 402 → pay → retry

async def check_token(mint: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://rugcheck.aethercore.dev/v1/audit/{mint}"
        )
        report = resp.json()
        action = report["action"]

        if not action["is_safe"]:
            print(f"DANGER — risk score {action['risk_score']}/100")
            for flag in report["analysis"]["red_flags"]:
                print(f"  🚩 {flag['message']}")
        else:
            print(f"SAFE — {report['analysis']['summary']}")

asyncio.run(check_token("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"))
```

**Requirements**: Your wallet needs **USDC** (for payments) and a small amount of **SOL** (for transaction fees, ~0.01 SOL).

---

## `mainnet_buyer_test.py` — Command-line Audit Tool

A standalone script to audit any Solana token via the paid gateway.

```bash
# Audit one token
python3 mainnet_buyer_test.py <mint_address>

# Audit multiple tokens
python3 mainnet_buyer_test.py <mint1> <mint2> <mint3>

# Use a custom gateway
python3 mainnet_buyer_test.py --gateway https://your-server.com <mint>
```

**Private key sources** (picks the first available):

| Priority | Source | Setup |
|----------|--------|-------|
| 1 | `SOLANA_PRIVATE_KEY` env var | `export SOLANA_PRIVATE_KEY=<base58>` |
| 2 | `~/.ag402/.env` | Written by `ag402 setup` |
| 3 | `~/.ag402/wallet.key` (encrypted) | Created by `ag402 setup`, prompts for password |

```bash
# Non-interactive (CI/Docker)
export AG402_UNLOCK_PASSWORD=<wallet_password>
python3 mainnet_buyer_test.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

---

## API Reference

**Base URL**: `https://rugcheck.aethercore.dev`

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/audit/{mint_address}` | USDC payment | Full safety audit report |
| GET | `/health` | None | Service health + upstream status |
| GET | `/stats` | Loopback only | Request counts + cache hit rate |
| GET | `/metrics` | Loopback only | Prometheus metrics |
| GET | `/audit/{mint_address}` | USDC payment | **Deprecated** — use `/v1/` |

### Audit Response Schema

```json
{
  "contract_address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
  "chain": "solana",
  "audit_timestamp": "2026-03-08T12:34:56.789012+00:00",
  "degraded": false,
  "action": {
    "is_safe": true,
    "risk_level": "SAFE",
    "risk_score": 3
  },
  "analysis": {
    "summary": "No significant risk signals detected. Always do your own research (DYOR).",
    "red_flags": [
      {"level": "LOW", "message": "Token metadata is mutable — common for Solana tokens."}
    ],
    "green_flags": [
      {"message": "Mint authority renounced (Mint Renounced)."},
      {"message": "Liquidity pool is sufficiently protected (LP Burned or Locked)."},
      {"message": "No freeze authority (Not Freezable)."}
    ]
  },
  "evidence": {
    "token_name": "Bonk",
    "token_symbol": "Bonk",
    "price_usd": 0.00012,
    "liquidity_usd": 85000000.0,
    "is_mintable": false
  },
  "metadata": {
    "data_sources": ["RugCheck", "DexScreener", "GoPlus"],
    "data_completeness": "full",
    "cache_hit": false,
    "data_age_seconds": 0,
    "response_time_ms": 738,
    "disclaimer": "This report is generated by automated data aggregation. Not financial advice (NFA)."
  }
}
```

### Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `action.is_safe` | `bool` | Machine-readable verdict |
| `action.risk_score` | `int` | 0 (safest) to 100 (most dangerous) |
| `action.risk_level` | `string` | `SAFE` / `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `degraded` | `bool` | `true` if upstream sources failed — treat with extra caution |
| `metadata.data_completeness` | `string` | `full` / `partial` / `minimal` / `unavailable` |
| `metadata.data_age_seconds` | `int\|null` | `0` = fresh, `>0` = cached |

### Health Check

```
GET /health → {"status": "healthy", "mode": "production", "uptime_seconds": 4242.6}
```

| `status` | Meaning |
|----------|---------|
| `healthy` | All systems normal |
| `degraded` | Upstream API failures — service continues with available data |

---

## Deployment Guide (Self-hosting)

> **Automated Deployment**: See **[OPERATIONS.md](OPERATIONS.md)** for one-click scripts and ops runbook.

### Architecture

```
Client (AI Agent)
  │ HTTPS
  ▼
Cloudflare (SSL termination, DDoS protection)
  │ HTTP:80
  ▼
┌─────────────────────────────────────────────┐
│  Docker Compose                             │
│  ┌─────────────┐     ┌──────────────────┐   │
│  │ ag402-gateway│────▶│ rugcheck-audit   │   │
│  │ :80 (public) │     │ :8000 (internal) │   │
│  │ Payment gate │     │ Audit engine     │   │
│  └─────────────┘     └──────────────────┘   │
└─────────────────────────────────────────────┘
```

### Three Environments

| Environment | Blockchain | Real Funds | Use Case |
|-------------|-----------|------------|----------|
| **Test (mock)** | None | No | Local dev, CI |
| **Devnet** | Solana Devnet | No (faucet) | Integration testing |
| **Production** | Solana Mainnet | **Yes** | Live service |

### Quick Start — Test Mode (zero config)

```bash
pip install -e .
python -m rugcheck.main &          # Audit server on :8000
python -m rugcheck.gateway &       # Gateway on :8001 (mock payments)
curl http://localhost:8001/v1/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

### Quick Start — Production (Docker)

```bash
# 1. One-click deploy to your server
bash scripts/deploy-oneclick.sh

# Or manually:
cp .env.example .env               # Edit with your wallet address + keys
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Provider `.env` (Production)

```bash
X402_MODE=production
X402_NETWORK=mainnet
AG402_ADDRESS=<your_solana_wallet>  # Receives USDC payments
AG402_PRICE=0.02                    # USDC per audit
SOLANA_PRIVATE_KEY=<base58_key>     # For payment verification
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com

# Optional: higher rate limits
GOPLUS_APP_KEY=<key>
GOPLUS_APP_SECRET=<secret>
```

### Consumer Setup

```bash
pip install "ag402-core[crypto]" httpx
ag402 setup                         # Interactive wizard
ag402 status                        # Verify wallet + balance
```

```python
import ag402_core
ag402_core.enable()
# Now all httpx requests auto-handle 402 → pay → retry
```

---

## Configuration Reference

### Service

| Variable | Default | Description |
|----------|---------|-------------|
| `RUGCHECK_HOST` | `0.0.0.0` | Bind address |
| `RUGCHECK_PORT` | `8000` | Bind port |
| `RUGCHECK_LOG_LEVEL` | `info` | `debug` / `info` / `warning` / `error` |
| `RUGCHECK_PRODUCTION` | `false` | `true` disables `/docs`, `/redoc`, `/openapi.json` |
| `UVICORN_WORKERS` | `1` | Worker processes |
| `CACHE_TTL_SECONDS` | `3` | Cache TTL (short to catch rug pulls) |
| `CACHE_MAX_SIZE` | `5000` | Max cached entries (LRU) |
| `FREE_DAILY_QUOTA` | `20` | Free requests per IP per day |
| `PAID_RATE_LIMIT` | `120` | Paid requests per IP per minute |

### Upstream APIs

| Variable | Default | Description |
|----------|---------|-------------|
| `DEXSCREENER_TIMEOUT_SECONDS` | `1.5` | DexScreener timeout |
| `GOPLUS_TIMEOUT_SECONDS` | `2.5` | GoPlus timeout |
| `RUGCHECK_API_TIMEOUT_SECONDS` | `3.5` | RugCheck timeout |
| `GOPLUS_APP_KEY` | — | GoPlus API key (optional) |
| `GOPLUS_APP_SECRET` | — | GoPlus API secret |

### ag402 Payment

| Variable | Default | Description |
|----------|---------|-------------|
| `AG402_PRICE` | `0.02` | USDC per request |
| `AG402_ADDRESS` | — | Provider wallet (receives payments) |
| `AG402_GATEWAY_PORT` | `8001` | Gateway port |
| `X402_MODE` | `test` | `test` (mock) / `production` (real) |
| `X402_NETWORK` | `devnet` | `mock` / `devnet` / `mainnet` |
| `SOLANA_PRIVATE_KEY` | — | Base58 wallet private key |
| `SOLANA_RPC_URL` | `https://api.devnet.solana.com` | Solana RPC |

### Consumer Safety Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `X402_DAILY_LIMIT` | `10.0` | Max daily spend (USD) |
| `X402_SINGLE_TX_LIMIT` | `5.0` | Max per-transaction |
| `X402_PER_MINUTE_LIMIT` | `2.0` | Max spend per minute |
| `X402_PER_MINUTE_COUNT` | `5` | Max transactions per minute |

---

## ag402 CLI Reference

```bash
ag402 setup                  # Interactive setup wizard
ag402 status                 # Dashboard: mode, wallet, balance
ag402 balance                # Check SOL + USDC balance
ag402 doctor                 # Diagnose environment issues
ag402 history --limit 10     # Recent transactions
ag402 pay <url>              # Send a single paid request
ag402 demo                   # Quick E2E test (mock mode)
ag402 demo --devnet          # E2E test with Devnet transactions
ag402 info                   # Protocol version
```

---

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v              # 118 tests
ruff check src/ tests/                  # Lint
python examples/demo_agent.py           # E2E demo (direct)
python examples/demo_agent.py --with-gateway  # E2E demo (with payment)
```

### Project Structure

```
src/rugcheck/
├── config.py              # Environment-based configuration
├── models.py              # Pydantic models (report schema)
├── cache.py               # Async-safe TTL cache (LRU, asyncio.Lock)
├── server.py              # FastAPI app + rate limiter + health checks
├── main.py                # Audit server entry point
├── gateway.py             # ag402 gateway entry point
├── fetchers/
│   ├── base.py            # BaseFetcher ABC
│   ├── goplus.py          # GoPlus Security API
│   ├── rugcheck.py        # RugCheck.xyz API
│   ├── dexscreener.py     # DexScreener API
│   └── aggregator.py      # Concurrent fetch + merge
└── engine/
    └── risk_engine.py     # Deterministic rule-based scoring
```

### Security

- **Rate limiting** — free: 20/day per IP; paid: 120/min per IP
- **Trusted proxy model** — `CF-Connecting-IP` only trusted from Cloudflare IPs
- **Production hardening** — `/docs`, `/redoc`, `/openapi.json` disabled
- **Gateway fail-safe** — refuses to start if payment verifier fails in production
- **Cache isolation** — deep-copy on get/set prevents shared state corruption
- **Degraded short-TTL** — incomplete reports cached only 10s
- **Prometheus path normalization** — prevents cardinality explosion
- **Upstream protection** — `Semaphore(20)` + `max_connections=50`
- **Error sanitization** — never exposes internal paths

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `402 Payment Required` | Expected — gateway requires USDC payment | Use `ag402_core.enable()` or `ag402 pay <url>` |
| `InsufficientFundsForRent` | Wallet SOL too low for ATA creation | Send ≥ 0.01 SOL to your wallet |
| `Payment not confirmed on-chain` (403) | Solana network confirmation timeout | Retry — transient network issue |
| `ValueError: Production mode requires PaymentVerifier` | Missing crypto deps | `pip install -e ".[crypto]"` |
| `ImportError: solana/solders` | Missing crypto deps | `pip install "ag402-core[crypto]"` |
| Gateway keeps restarting | Missing `SOLANA_PRIVATE_KEY` or `AG402_ADDRESS` | Check `.env` configuration |
| Health returns `degraded` | Upstream APIs failing | Service continues with available sources — check `/health` |

---

## License

MIT
