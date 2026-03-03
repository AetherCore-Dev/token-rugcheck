# Token RugCheck MCP — AI-Agent Deployment Guide

> **For AI coding agents (Claude Code, Cursor, etc.):** This guide is structured so an AI agent can execute the entire deployment pipeline automatically. Each step is tagged with its automation type.
>
> **For humans:** See [OPERATIONS.md](OPERATIONS.md) for manual operations, monitoring, and troubleshooting.

---

## Overview

Deploy a Token RugCheck audit API as a paid service using ag402 micropayments. The pipeline:

1. Collect credentials from the human operator
2. Initialize the server (Docker, firewall, repo)
3. Generate configuration from inputs
4. Build and deploy Docker containers
5. (Optional) Configure custom domain via Cloudflare
6. Verify end-to-end

**Machine-readable spec:** See [`deploy.yaml`](deploy.yaml) for the structured deployment contract.

---

## Prerequisites

- A Linux server (Ubuntu 20.04+) with root SSH access
- A Solana wallet address (to receive payments)
- For devnet/production: a Solana private key (base58 format)
- (Optional) A domain name with Cloudflare DNS

---

## Phase 1: Collect Inputs

### Step 1.1: Server Access
**Type:** HUMAN_INPUT
**Collect:** Server IP address with root SSH access
**Validation:** `^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$`

### Step 1.2: Deployment Mode
**Type:** HUMAN_DECISION
**Options:**
| Mode | Payments | Blockchain | Use Case |
|------|----------|-----------|----------|
| `test` | Mock (simulated) | None | Development, CI |
| `devnet` | Real transactions | Solana Devnet | Integration testing |
| `production` | Real USDC | Solana Mainnet | Live service |

### Step 1.3: Wallet Address
**Type:** HUMAN_INPUT
**Collect:** Solana wallet address (base58, 32-44 characters)
**Validation:** `^[1-9A-HJ-NP-Za-km-z]{32,44}$`
**Example:** `fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm`

### Step 1.4: Price
**Type:** HUMAN_DECISION
**Collect:** USDC price per audit request
**Default:** `0.05`
**Validation:** `^\d+(\.\d+)?$`

### Step 1.5: Private Key (devnet/production only)
**Type:** HUMAN_INPUT
**Collect:** Solana private key in base58 format (44-88 characters)
**Security:** Never log, echo, or store in version control
**Skip if:** mode is `test`

### Step 1.6: Custom Domain
**Type:** HUMAN_DECISION
**Collect:** Domain name (e.g., `api.example.com`) — or skip for IP-only access
**Note:** Requires Cloudflare account if provided

---

## Phase 2: Server Initialization

### Step 2.1: Run Server Setup
**Type:** AUTOMATED
**Command:**
```bash
ssh root@${SERVER_IP} 'bash -s' < scripts/setup-server.sh
```
**Expected:** Lines containing `OK|SETUP|` — exit code 0 (fresh) or 2 (already set up)
**On failure:** Check output for `FAIL|` lines. Common issues:
- Port 80 in use by another service (e.g., nginx/apache) → stop it first
- Network issues preventing Docker install → check server internet access

### Step 2.2: Verify Server Setup
**Type:** AUTOMATED
**Command:**
```bash
ssh root@${SERVER_IP} 'docker compose version && ls /opt/token-bugcheck/docker-compose.yml'
```
**Expected:** Docker Compose version string + file listing with no errors

---

## Phase 3: Configure Environment

### Step 3.1: Generate .env
**Type:** AUTOMATED
**Command:**
```bash
bash scripts/generate-env.sh \
  --mode ${MODE} \
  --address ${WALLET_ADDRESS} \
  --price ${PRICE} \
  --private-key ${PRIVATE_KEY} \
  --output /tmp/token-bugcheck.env
```
**Expected:** `OK|GENERATE|Written to /tmp/token-bugcheck.env`
**On failure:** Check `FAIL|VALIDATE|` messages — usually invalid address or missing private key

### Step 3.2: Upload .env to Server
**Type:** AUTOMATED
**Command:**
```bash
scp /tmp/token-bugcheck.env root@${SERVER_IP}:/opt/token-bugcheck/.env
rm /tmp/token-bugcheck.env
```
**Expected:** File transferred successfully
**On failure:** Check SSH connectivity

---

## Phase 4: Deploy

### Step 4.1: Run Deployment
**Type:** AUTOMATED
**Command:**
```bash
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && bash scripts/deploy.sh --server-ip ${SERVER_IP}'
```
**Expected:** `OK|DEPLOY|Deployment complete` — all 5 phases pass
**On failure:**
- Phase 1 fail → `.env` has placeholders — re-run generate-env.sh
- Phase 2 fail → Docker build error — check Dockerfile syntax
- Phase 3 fail → `docker compose up` failed — check logs
- Phase 4 fail → Health check timeout — check container logs: `docker compose logs --tail 50`
- Phase 5 fail → Verification failed — see verify.sh output for specific layer

### Step 4.2: Quick Verification
**Type:** AUTOMATED
**Command:**
```bash
ssh root@${SERVER_IP} 'bash /opt/token-bugcheck/scripts/verify.sh --phase L2'
```
**Expected:** `VERIFY|SUMMARY|pass=2 fail=0 skip=0`

---

## Phase 5: Domain Setup (Optional)

### Step 5.1: Configure Cloudflare DNS
**Type:** HUMAN_INPUT
**Instructions for the human operator:**
1. Log in to [Cloudflare Dashboard](https://dash.cloudflare.com)
2. Select your domain
3. Go to **DNS** → **Records** → **Add Record**
4. Type: `A`, Name: `api` (or your subdomain), Content: `${SERVER_IP}`
5. Proxy status: **Proxied** (orange cloud icon)
6. Go to **SSL/TLS** → Set encryption mode to **Flexible**
7. Enable **Always Use HTTPS** under SSL/TLS → Edge Certificates

### Step 5.2: Wait for DNS Propagation
**Type:** AUTOMATED
**Command:**
```bash
# Poll until DNS resolves (max 120s)
for i in $(seq 1 24); do
  if curl -sf --max-time 5 "https://${DOMAIN}/health" >/dev/null 2>&1; then
    echo "OK|DNS|Domain resolved and accessible"
    break
  fi
  sleep 5
done
```
**Expected:** Health endpoint accessible via HTTPS

### Step 5.3: Verify Domain
**Type:** AUTOMATED
**Command:**
```bash
ssh root@${SERVER_IP} 'bash /opt/token-bugcheck/scripts/verify.sh --domain ${DOMAIN} --phase L4'
```
**Expected:** `OK|L4|https://${DOMAIN}/health returned 200`

---

## Phase 6: End-to-End Verification

### Step 6.1: Full Verification Suite
**Type:** AUTOMATED
**Command:**
```bash
ssh root@${SERVER_IP} "cd /opt/token-bugcheck && bash scripts/verify.sh --server-ip ${SERVER_IP} --domain ${DOMAIN}"
```
**Expected:** `VERIFY|SUMMARY|pass=N fail=0 skip=M`
**On failure:** Review each `FAIL|` line in the output — the layer (L1-L5) tells you exactly where the problem is:
- L1 → Container issue (check `docker compose logs`)
- L2 → Port mapping issue (check `docker compose ps`)
- L3 → Firewall issue (check `ufw status`)
- L4 → DNS/Cloudflare issue (check Cloudflare dashboard)
- L5 → Application issue (check service logs)

### Step 6.2: E2E Payment Test (devnet only)
**Type:** AUTOMATED
**Command:**
```bash
ssh root@${SERVER_IP} "cd /opt/token-bugcheck && GATEWAY_URL=https://${DOMAIN} python3 devnet_buyer_test.py"
```
**Expected:** `ALL TESTS PASSED`
**Note:** Requires devnet buyer wallet to be funded with SOL + USDC

### Step 6.3: Report Results
**Type:** AUTOMATED
**Output to human:**
```
Deployment complete!

  Audit API:  http://${SERVER_IP}:8000
  Gateway:    http://${SERVER_IP}:80
  HTTPS:      https://${DOMAIN} (if configured)
  Mode:       ${MODE}
  Price:      ${PRICE} USDC per request

  Status: All verification checks passed.
```

---

## AI Agent Workflow Summary

When an AI agent executes this pipeline, the interaction looks like:

```
Agent: "I'll deploy the Token RugCheck API as a paid service. I need a few inputs."

[HUMAN_INPUT]  → Server IP, wallet address, mode, price, private key
[AUTOMATED]    → ssh root@IP 'bash -s' < scripts/setup-server.sh
[AUTOMATED]    → scripts/generate-env.sh → scp .env to server
[AUTOMATED]    → ssh root@IP 'bash scripts/deploy.sh'
[HUMAN_INPUT]  → Cloudflare DNS setup (if domain requested)
[AUTOMATED]    → scripts/verify.sh --domain api.example.com
[AUTOMATED]    → Report results with live URLs

Total human actions: ~3 decisions + 1 DNS config step
```

---

## Rollback Procedures

### Rollback Deploy (stop services)
```bash
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && docker compose -f docker-compose.yml -f docker-compose.prod.yml down'
```

### Rollback Configuration (remove .env)
```bash
ssh root@${SERVER_IP} 'rm /opt/token-bugcheck/.env'
```

### Rollback Server Init (remove repo)
```bash
ssh root@${SERVER_IP} 'rm -rf /opt/token-bugcheck'
```

### Full Rollback (everything)
```bash
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && docker compose down; rm -rf /opt/token-bugcheck'
```

---

## Appendix: Human Operations

For ongoing operations after deployment:

| Task | Reference |
|------|-----------|
| View logs | `docker compose logs -f` — see [OPERATIONS.md §5](OPERATIONS.md#5-日常运维) |
| Mode switching | `sed + docker compose restart` — see [OPERATIONS.md §5.3](OPERATIONS.md#53-模式切换) |
| Monitoring | Prometheus `/metrics` — see [OPERATIONS.md §8](OPERATIONS.md#8-监控-prometheus) |
| Troubleshooting | Diagnostic flow — see [OPERATIONS.md §6](OPERATIONS.md#6-故障排查) |
| Wallet balance | Solana RPC queries — see [OPERATIONS.md §4.4](OPERATIONS.md#44-钱包余额检查) |
| Update/rebuild | `git pull && docker compose build` — see [OPERATIONS.md §3](OPERATIONS.md#3-一键更新镜像并验证) |

---

## Post-Deployment Operations

After a successful deployment, use the following runbooks for day-to-day operations. Each task has a **Human** version (copy-paste into SSH) and an **AI Agent** version (structured commands an agent can execute).

### Quick Health Test

**Human:**
```bash
ssh root@SERVER_IP
curl -s https://DOMAIN/health | python3 -m json.tool
curl -s -o /dev/null -w "%{http_code}" https://DOMAIN/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
# Expect: 402
```

**AI Agent:**
```bash
# [AUTOMATED] Quick health + paywall check
ssh root@${SERVER_IP} 'bash /opt/token-bugcheck/scripts/verify.sh --phase L2'
# Parse: VERIFY|SUMMARY|pass=N fail=0 → healthy
```

### Full Verification (5-layer)

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
bash scripts/verify.sh --server-ip SERVER_IP --domain DOMAIN
# Review each OK|/FAIL| line; VERIFY|SUMMARY|pass=12 fail=0 skip=0 = all good
```

**AI Agent:**
```bash
# [AUTOMATED] Full 5-layer verification
ssh root@${SERVER_IP} "cd /opt/token-bugcheck && bash scripts/verify.sh --server-ip ${SERVER_IP} --domain ${DOMAIN}"
# Parse: exit code 0 = all pass; exit code 1 = failures present
# Parse: VERIFY|SUMMARY|pass=N fail=M skip=K
```

### Smoke Test (functional)

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
GATEWAY_URL=http://localhost:80 bash scripts/smoke_test.sh
```

**AI Agent:**
```bash
# [AUTOMATED] 7-point functional smoke test
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && GATEWAY_URL=http://localhost:80 bash scripts/smoke_test.sh'
# Parse: "N passed, M failed" in last line; exit code 0 = all pass
```

### Devnet E2E Payment Test

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
GATEWAY_URL=https://DOMAIN python3 devnet_buyer_test.py
# Expect: ALL TESTS PASSED + Solscan TX link
```

**AI Agent:**
```bash
# [AUTOMATED] Real devnet USDC payment E2E
ssh root@${SERVER_IP} "cd /opt/token-bugcheck && GATEWAY_URL=https://${DOMAIN} python3 devnet_buyer_test.py"
# Parse: "ALL TESTS PASSED" = success
```

---

### Change Wallet Address

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
# Edit .env — change AG402_ADDRESS to the new wallet
nano .env
# Restart to pick up changes
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
# Wait ~35s, then verify
sleep 35
curl -s https://DOMAIN/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 | grep address
# Should show the new wallet address in the 402 response
```

**AI Agent:**
```bash
# [HUMAN_INPUT] Collect: new_wallet_address, new_private_key (if also rotating key)
# [AUTOMATED] Regenerate .env with new wallet
bash scripts/generate-env.sh \
  --mode ${MODE} \
  --address ${NEW_WALLET_ADDRESS} \
  --price ${PRICE} \
  --private-key ${PRIVATE_KEY} \
  --output /tmp/token-bugcheck.env
scp /tmp/token-bugcheck.env root@${SERVER_IP}:/opt/token-bugcheck/.env

# [AUTOMATED] Restart services
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml down && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d'

# [AUTOMATED] Wait and verify
sleep 40
ssh root@${SERVER_IP} 'bash /opt/token-bugcheck/scripts/verify.sh --phase L5'
```

### Change Price

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
sed -i 's/AG402_PRICE=.*/AG402_PRICE=0.10/' .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
sleep 35
curl -s https://DOMAIN/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 | python3 -c "import json,sys; print('Price:', json.load(sys.stdin).get('amount'))"
```

**AI Agent:**
```bash
# [HUMAN_INPUT] Collect: new_price
# [AUTOMATED] Update price in .env and restart
ssh root@${SERVER_IP} "cd /opt/token-bugcheck && \
  sed -i 's/AG402_PRICE=.*/AG402_PRICE=${NEW_PRICE}/' .env && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml down && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
sleep 40
ssh root@${SERVER_IP} 'bash /opt/token-bugcheck/scripts/verify.sh --phase L5'
```

### Switch Mode (test ↔ devnet ↔ production)

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
# Option A: Regenerate entire .env (recommended)
bash scripts/generate-env.sh --mode NEW_MODE --address WALLET --price PRICE --private-key KEY --output .env
# Option B: Edit manually
nano .env    # Change X402_MODE, X402_NETWORK, SOLANA_RPC_URL
# Then restart
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
sleep 35
curl -s https://DOMAIN/health | python3 -c "import json,sys; print('Mode:', json.load(sys.stdin)['mode'])"
```

**AI Agent:**
```bash
# [HUMAN_DECISION] Collect: target_mode (test|devnet|production)
# [HUMAN_INPUT]    Collect: private_key (if switching to devnet/production)
# [AUTOMATED] Regenerate .env for new mode
bash scripts/generate-env.sh --mode ${NEW_MODE} --address ${WALLET} --price ${PRICE} --private-key ${PRIVATE_KEY} --output /tmp/token-bugcheck.env
scp /tmp/token-bugcheck.env root@${SERVER_IP}:/opt/token-bugcheck/.env
# [AUTOMATED] Rebuild (mode may affect Docker behavior) and restart
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && bash scripts/deploy.sh --server-ip ${SERVER_IP} --domain ${DOMAIN}'
```

---

### Update to Latest Version

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
sleep 35
bash scripts/verify.sh --server-ip SERVER_IP --domain DOMAIN
```

**AI Agent:**
```bash
# [AUTOMATED] Pull latest code and redeploy
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && git pull origin main'
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && bash scripts/deploy.sh --server-ip ${SERVER_IP} --domain ${DOMAIN}'
# deploy.sh handles: build → up → health wait → verify
```

### Update Single Service Only

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
git pull origin main

# Only rebuild gateway (changed gateway.py or Dockerfile.gateway)
docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache ag402-gateway
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d ag402-gateway

# Only rebuild audit server (changed server.py, engine, fetchers)
# Note: gateway depends on audit-server, so must restart both
docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache audit-server
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**AI Agent:**
```bash
# [AUTOMATED] Selective rebuild — gateway only
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && git pull origin main && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache ag402-gateway && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d ag402-gateway'
sleep 40
ssh root@${SERVER_IP} 'bash /opt/token-bugcheck/scripts/verify.sh --phase L2'

# [AUTOMATED] Selective rebuild — audit server (restarts both)
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && git pull origin main && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache audit-server && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml down && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d'
sleep 40
ssh root@${SERVER_IP} 'bash /opt/token-bugcheck/scripts/verify.sh --server-ip ${SERVER_IP}'
```

---

### View Logs

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
docker compose logs -f                          # All services, live tail
docker compose logs audit-server --tail 100     # Last 100 lines, audit only
docker compose logs ag402-gateway --tail 100    # Last 100 lines, gateway only
docker compose logs ag402-gateway 2>&1 | grep "\[VERIFY\]"  # Payment verification records
```

**AI Agent:**
```bash
# [AUTOMATED] Fetch recent logs
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && docker compose logs --tail 50' 2>&1
# Parse for ERROR|WARNING|VERIFY|FATAL patterns
```

### Check Wallet Balances (devnet)

**Human:**
```bash
# Seller USDC balance
curl -s https://api.devnet.solana.com -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"getTokenAccountsByOwner","params":["SELLER_ADDRESS",{"mint":"4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"},{"encoding":"jsonParsed"}]}' \
  | python3 -c "import json,sys; t=json.load(sys.stdin)['result']['value'][0]['account']['data']['parsed']['info']['tokenAmount']; print('Seller USDC:', t['uiAmountString'])"
```

**AI Agent:**
```bash
# [AUTOMATED] Query on-chain balance via Solana RPC
ssh root@${SERVER_IP} "curl -s https://api.devnet.solana.com -X POST \
  -H 'Content-Type: application/json' \
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getTokenAccountsByOwner\",\"params\":[\"${WALLET_ADDRESS}\",{\"mint\":\"4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU\"},{\"encoding\":\"jsonParsed\"}]}'" 2>&1
# Parse: result.value[0].account.data.parsed.info.tokenAmount.uiAmountString
```

### Restart Services

**Human:**
```bash
ssh root@SERVER_IP
cd /opt/token-bugcheck
# Quick restart (keeps existing images)
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart
# Full restart (recreates containers)
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**AI Agent:**
```bash
# [AUTOMATED] Full restart + verify
ssh root@${SERVER_IP} 'cd /opt/token-bugcheck && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml down && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d'
sleep 40
ssh root@${SERVER_IP} 'bash /opt/token-bugcheck/scripts/verify.sh --phase L2'
```

### Disk Cleanup

**Human:**
```bash
ssh root@SERVER_IP
df -h /
docker image prune -f                # Remove dangling images
docker system prune -f               # Deep clean (careful: removes unused volumes)
```

**AI Agent:**
```bash
# [AUTOMATED] Safe cleanup (images only, no volume deletion)
ssh root@${SERVER_IP} 'df -h / && docker image prune -f'
```

---

## Script Reference

| Script | Purpose | Key Args |
|--------|---------|----------|
| `scripts/setup-server.sh` | Server init (Docker, firewall, repo) | Run via SSH pipe |
| `scripts/generate-env.sh` | Generate `.env` from CLI args | `--mode`, `--address`, `--price`, `--private-key` |
| `scripts/deploy.sh` | Build → start → health → verify | `--project-dir`, `--server-ip`, `--domain` |
| `scripts/verify.sh` | 5-layer verification | `--server-ip`, `--domain`, `--phase` |
| `scripts/smoke_test.sh` | Quick functional smoke test | `AUDIT_URL`, `GATEWAY_URL` env vars |

All scripts output `STATUS|COMPONENT|MESSAGE` format, parseable with `line.split('|')`.
