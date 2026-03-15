# AI-Driven Ops Framework — Design Spec

**Date**: 2026-03-15
**Status**: Draft
**Scope**: A Runbook-based framework enabling AI to autonomously deploy, upgrade, verify, and maintain ag402 + FastAPI + Docker services with minimal human involvement.

## Context

### Origin

During the v0.1.7 deployment of Token RugCheck MCP (2026-03-14), AI (Claude Code) completed the entire deployment via SSH — .env fix, code deployment, multi-layer verification, and real mainnet payment testing. However, the process exposed significant inefficiencies:

- 6 failed attempts before payment test succeeded (ag402 ledger/SSRF/HTTPS restrictions undocumented)
- DNS misconfiguration discovered only at verification step (no pre-flight check)
- .env missing critical variables that .env.example had (generate-env.sh gap)
- Private key exposed in tool output (no secrets handling)

### Goal

Build a framework where AI reads a project manifest + Runbook library, then autonomously executes from zero-to-live or upgrade-and-verify, with humans only providing infrastructure purchasing and approving major decisions.

### Design Principles

1. **AI does everything it physically can** — only "login to third-party web console" operations go to humans
2. **No trial-and-error** — Runbooks encode known solutions; AI reads before acting
3. **Progressive evolution** — every deployment adds to the knowledge base
4. **Secrets never in plaintext** — separation of config and secrets at every layer
5. **Scripts do, Runbooks direct** — no duplication of logic between the two

## Architecture

### Three-Layer System

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Project Manifest                               │
│  manifest.yaml (non-sensitive) + .env.secrets (gitignored)│
│  Human prepares infrastructure, fills in config           │
└──────────────────────────┬──────────────────────────────┘
                           │ AI reads
┌──────────────────────────▼──────────────────────────────┐
│  Layer 2: Runbook Library (ops/runbooks/)                 │
│                                                          │
│  common/         — truly cross-project (preflight, init) │
│  templates/      — parameterized patterns (deploy, verify)│
│  project/<name>/ — project-specific instantiation         │
│  lessons/        — accumulated experience (auto-evolve)   │
└──────────────────────────┬──────────────────────────────┘
                           │ AI orchestrates
┌──────────────────────────▼──────────────────────────────┐
│  Layer 3: Deterministic Scripts (scripts/)                │
│  Existing: quick-update.sh, deploy-oneclick.sh, etc.     │
│  New: preflight-check.sh, payment-test.py, monitor-deps.py│
└─────────────────────────────────────────────────────────┘
```

### AI Execution Flow

```
Human: "Please deploy/maintain this project"
         │
    AI reads manifest.yaml + .env.secrets
         │
    AI reads lessons/ (scan for relevant experience)
         │
    AI SSH to server → auto-detect state
         │
    ┌────▼─────────────────────────┐
    │ Project dir exists?           │
    │ Containers running?           │
    │ Current git commit?           │
    └────┬─────────────────────────┘
         │
    ┌────▼──────────────────────────────────┐
    │                    │                   │
    ▼                    ▼                   ▼
  Fresh Install      Needs Upgrade        Healthy
  (no project dir)   (version mismatch)   (version match)
    │                    │                   │
    ▼                    ▼                   ▼
  common/preflight   common/preflight     Health check
  common/server-init project/deploy       If issues →
  project/deploy     project/verify         project/troubleshoot
  project/verify     project/payment-test Report status
  project/payment-test
```

## Layer 1: Project Manifest

### manifest.yaml (git-tracked, non-sensitive)

```yaml
# === Project ===
project:
  name: token-rugcheck
  repo: https://github.com/AetherCore-Dev/Token_RugCheck_MCP
  git_ref: v0.1.7                      # tag/branch/commit to deploy

# === Server ===
server:
  ip: 140.82.49.221
  ssh_user: root
  ssh_key_path: ~/.ssh/id_ed25519     # local SSH private key path
  project_dir: /opt/token-rugcheck

# === Domain & CDN ===
domain:
  name: rugcheck.aethercore.dev
  cdn: cloudflare                      # cloudflare | none
  ssl_mode: flexible                   # flexible | full

# === Blockchain ===
blockchain:
  network: mainnet                     # mainnet | devnet
  seller_address: EtfTwndhRFLaWUe64ZbCBBdXBqfaK9H6QqCAeSnNXLLK

# === Service ===
service:
  price: "0.02"
  free_daily_quota: 20
  production_mode: true

# === Secrets Reference ===
# Sensitive values are in .env.secrets (gitignored).
# AI reads .env.secrets at runtime, never logs or reports full values.
secrets_file: .env.secrets
```

### .env.secrets (gitignored, local-only)

```bash
# AI reads this file but NEVER logs full values.
# In reports: show first 4 + last 4 chars only (e.g., c944...3fd)
SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=c9443614-...
BUYER_PRIVATE_KEY=42E9QW...Bdsqv
# Optional
AG402_PREPAID_SIGNING_KEY=
GOPLUS_APP_KEY=
GOPLUS_APP_SECRET=
```

### Secrets Handling Rules

1. **manifest.yaml**: never contains secrets — git-tracked, safe to commit
2. **.env.secrets**: gitignored, only on local machine and server
3. **AI command execution**: pass secrets via environment variables, never in command strings
4. **Execution reports**: redact all secrets — show only `first4...last4`
5. **Lessons and Runbooks**: never contain actual secret values
6. **Server .env**: AI writes secrets from .env.secrets into server .env via SSH, using `echo "$VAR" >> .env` (variable expansion on local side only)

### manifest.schema.yaml (validation rules)

```yaml
# AI uses this to validate manifest before execution.
required_fields:
  - project.name
  - project.repo
  - project.git_ref
  - server.ip
  - server.ssh_user
  - server.project_dir
  - blockchain.network
  - blockchain.seller_address
  - service.price

ip_format: "^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$"
seller_address_format: "^[1-9A-HJ-NP-Za-km-z]{32,44}$"  # base58
```

## Layer 2: Runbook Library

### Runbook Structure Standard

Every Runbook step has exactly four elements:

```markdown
### Step N: <title>
**Do**: <exact command or script to run, with {manifest.field} placeholders>
**Expect**: <what success looks like — exit code, output pattern, HTTP status>
**On failure**:
  - <condition> → <AI action: auto-fix / degrade-continue / escalate-to-human>
  - <condition> → <AI action>
**Do NOT attempt**: <things that waste time — learned from experience>
```

The fourth element ("Do NOT attempt") is critical. It prevents the trial-and-error pattern observed during the v0.1.7 deployment. Each entry traces back to a specific failed attempt.

### Runbook Hierarchy: common → templates → project

```
ops/runbooks/
├── common/                    # Truly cross-project, use as-is
│   ├── 00-preflight.md        # DNS, SSH, disk, ports, .env completeness
│   └── 01-server-init.md      # Docker install, firewall, git clone, .env generation
│
├── templates/                 # Parameterized patterns, copy and customize per project
│   ├── 02-deploy.md           # Script orchestration + output parsing
│   ├── 03-verify.md           # Multi-layer verification framework
│   ├── 04-payment-test.md     # ag402 payment testing protocol
│   ├── 05-upgrade.md          # Version upgrade workflow
│   ├── 06-rollback.md         # Rollback with .env restoration
│   └── 07-troubleshoot.md     # Symptom-based diagnostic trees
│
└── project/
    └── rugcheck/              # Token RugCheck instantiation
        ├── deploy.md          # Calls scripts/quick-update.sh, interprets output
        ├── verify.md          # 7 verification points specific to this project
        ├── payment-test.md    # BONK audit test via async httpx + localhost
        └── troubleshoot.md    # Project-specific failure modes
```

**Why three levels?**

- `common/`: identical across all ag402+FastAPI+Docker projects. Never needs customization.
- `templates/`: defines the workflow structure. New projects copy and fill in project-specific commands, endpoints, and validation criteria.
- `project/<name>/`: the actual Runbook AI reads during execution. References common/ for shared steps, has project-specific details inline.

### Runbook-Script Relationship

**Scripts do. Runbooks direct.** Runbooks never duplicate script logic.

```markdown
# WRONG — Runbook duplicates script internals:
### Step 3: Deploy
**Do**: ssh root@{ip} "cd {dir} && git fetch && git checkout {ref} && docker compose build..."

# RIGHT — Runbook orchestrates script:
### Step 3: Deploy
**Do**: `bash scripts/quick-update.sh {server.ip} {domain.name} {project.git_ref}`
**Expect**: exit code 0, output contains "所有验证通过"
**On failure**:
  - exit code 1, output contains "构建失败" → check docker build logs, retry once
  - exit code 2, output contains "HTTPS 域名异常" → run DNS diagnostic (see 00-preflight Step 1)
  - exit code other → capture full output, check 07-troubleshoot.md
```

When existing scripts lack functionality (e.g., quick-update.sh has no DNS diagnostics), we enhance the script — not bypass it in the Runbook.

### Rollback: .env Backup Addition

Current rollback only reverts code. Enhanced rollback in `06-rollback.md`:

```markdown
### Step 1: Pre-deploy .env Snapshot (called from 02-deploy before any changes)
**Do**: `ssh root@{ip} "cp {dir}/.env {dir}/.env.bak.$(date +%s)"`
**Expect**: file created

### Step 4: Rollback .env (if needed)
**Do**: `ssh root@{ip} "ls -1t {dir}/.env.bak.* | head -1 | xargs -I{} cp {} {dir}/.env"`
**Expect**: .env restored to pre-deploy state
```

### Pre-Execution: State Snapshot

Before any destructive operation (deploy/upgrade/rollback), AI records:

```markdown
### Step 0: State Snapshot (all deploy/upgrade Runbooks start with this)
**Do**:
  - `ssh root@{ip} "cd {dir} && git rev-parse HEAD"`
  - `ssh root@{ip} "cd {dir} && docker compose ps --format json"`
  - `ssh root@{ip} "md5sum {dir}/.env"`
**Record**: save output to execution report under "Pre-operation state"
**Purpose**: audit trail, not human approval gate
```

## Layer 2b: Experience Library

### Experience File Format

```markdown
# Experience: <concise title>

**Date**: YYYY-MM-DD
**Project**: <project name>
**Scenario**: <what was being done>
**Applies to**: <version/component scope>

## Problem
<what went wrong — one paragraph>

## Root Cause
<why it happened — one paragraph>

## Solution
<what actually worked — concrete steps>

## Failed Attempts (avoid these)
- Attempt: <what was tried> → Result: <why it failed>
- Attempt: <what was tried> → Result: <why it failed>

## Status
- [ ] Verified effective
- [ ] Graduated to Runbook: <path>
```

### Experience Lifecycle

```
AI encounters unknown problem → resolves it → writes lessons/YYYY-MM-DD-<topic>.md
     │
     ▼ (every subsequent execution)
AI starts → scans lessons/ for relevant entries → applies knowledge proactively
     │
     ▼ (graduation check at end of execution)
AI checks: any ungraduated lesson that is
  (a) blocking-severity, OR
  (b) encountered 2+ times (check dates)?
     │
  Yes → incorporate into project Runbook step or "Do NOT attempt" entry
     → mark lesson as graduated
```

**Graduation trigger**: AI explicitly checks this at the end of every execution, as the final step before writing the execution report.

## Layer 3: Scripts

### All Scripts Live in Project Root `scripts/`

No separate `ops/scripts/` directory. Single location reduces AI cognitive load.

| Script | Status | Purpose |
|--------|--------|---------|
| `quick-update.sh` | Existing, enhance | Add DNS diagnostics on HTTPS failure |
| `deploy-oneclick.sh` | Existing | First-time deployment |
| `backup-data.sh` | Existing | Data backup |
| `verify.sh` | Existing | 5-layer verification |
| `preflight-check.sh` | **New** | Pre-deploy checks: DNS, SSH, disk, .env completeness |
| `payment-test.py` | **New** | Standardized ag402 payment test (async httpx + localhost) |
| `monitor-deps.py` | **New** | Check PyPI for ag402 version updates |

### Script Enhancement: quick-update.sh DNS Diagnostics

When HTTPS check returns 000, add diagnostic output:

```bash
if [ "$DOMAIN_HTTP" = "000" ]; then
    RESOLVED_IP=$(dig +short "$DOMAIN" 2>/dev/null | head -1)
    if [ -z "$RESOLVED_IP" ]; then
        warn "DNS 未解析: $DOMAIN — 请检查 DNS 配置"
    elif [ "$RESOLVED_IP" != "$SERVER_IP" ]; then
        warn "DNS 指向 $RESOLVED_IP，期望 $SERVER_IP — 请修正 Cloudflare A 记录"
    else
        warn "DNS 正确但 HTTPS 连接失败 — 检查 Cloudflare SSL/TLS 设置"
    fi
fi
```

### New Script: preflight-check.sh

```bash
#!/usr/bin/env bash
# Usage: bash scripts/preflight-check.sh <manifest.yaml>
# Exit codes: 0 = all pass, 1 = fixable issues (AI can handle), 2 = blocking (needs human)
#
# Checks:
#   1. SSH connectivity
#   2. DNS resolution matches server IP
#   3. HTTPS reachability (if domain configured)
#   4. Disk space > 2GB free
#   5. .env completeness (compare against .env.example required fields)
#   6. Port availability (80, 8000 not occupied by non-project processes)
#   7. Docker and docker compose available
#
# Output: structured lines "CHECK:<name>:PASS|FAIL|WARN:<detail>"
# AI parses these lines to determine next action.
```

### New Script: payment-test.py

Encodes the working payment test pattern discovered during v0.1.7 deployment:

```python
"""
Standardized ag402 payment test.

Usage (on server, as root):
    python3 scripts/payment-test.py --manifest /path/to/manifest.yaml

What it does:
    1. Reads seller_address and test endpoint from manifest
    2. Initializes AgentWallet ledger if balance is $0
    3. Sends request via async httpx to localhost gateway
    4. Validates 200 response and audit report schema
    5. Outputs structured result: PAYMENT_TEST:PASS|FAIL:<detail>

Requirements: ag402-core[crypto] installed on server host.

Known constraints (do NOT attempt alternatives):
    - ag402 pay CLI: blocks localhost (SSRF protection) — don't use
    - ag402 pay CLI: blocks HTTP for remote targets — don't use
    - httpx sync Client: ag402 only patches AsyncClient — use async
    - Container execution: wallet DB path not writable — run on host
"""
```

### New Script: monitor-deps.py

```python
"""
Check upstream dependency versions.

Usage:
    python3 scripts/monitor-deps.py --manifest /path/to/manifest.yaml

What it does:
    1. Reads current pinned versions from pyproject.toml
    2. Checks PyPI for latest ag402-core, ag402-mcp versions
    3. Checks server's installed versions via SSH
    4. Outputs structured comparison:
       DEP:ag402-core:pinned=0.1.17:latest=0.1.20:server=0.1.19:ACTION=upgrade-available

Execution modes:
    - Manual: human says "check for updates" in Claude Code
    - Cron (optional enhancement): server cron runs daily, sends notification on new version
```

## Human Interaction Model

### AI Autonomy Levels

**Level 1: Fully Autonomous (no notification)**

| Scenario | AI Action |
|----------|-----------|
| .env missing variables | Auto-fill from manifest + .env.example, restart containers |
| Container crashed | Read logs, diagnose, fix, restart |
| Disk space low | Clean Docker images + old backups (`docker system prune`, remove old backup tarballs) |
| Port occupied | Identify and kill conflicting process |
| Docker build timeout | Retry up to 3 times |
| ag402 ledger $0 | Auto-deposit from configured amount |
| Payment test needs deps | Install `ag402-core[crypto]` on server |
| Non-critical verification fails but service works | Record in report, continue |

**Level 2: Autonomous + Report (notify in execution report)**

| Scenario | AI Action |
|----------|-----------|
| Deployed version != latest available | Note in report: "ag402 0.1.20 available, server running 0.1.19" |
| Verification step skipped (e.g., no buyer key) | Note in report with reason |
| Fixed a problem not in Runbook | Fix it, write experience, note in report |
| Service healthy but HTTPS not working | Diagnose cause, note in report with specific fix instructions |

**Level 3: Escalate to Human (pause and wait)**

Only when AI physically cannot perform the action:

| Scenario | AI Behavior |
|----------|-------------|
| DNS A record wrong | "DNS resolves to X, expected Y. Please login to Cloudflare → DNS → edit A record for {domain} to {ip}. Tell me when done." |
| Server SSH unreachable | "Cannot SSH to {ip}. Please check VPS console — server may be down. Restart it and tell me when done." |
| Cloudflare SSL issue | "HTTPS fails, DNS is correct. Please check Cloudflare → SSL/TLS → set to Flexible. Tell me when done." |
| Wallet insufficient for test | "USDC balance is $X, need $Y for test. Please fund the buyer wallet. Tell me when done." |
| Manifest has invalid data | "server.ip '{value}' is not a valid IP address. Please fix manifest.yaml." |

**Escalation behavior**:
- AI gives specific, actionable instructions (not just "fix it")
- AI continues with independent subsequent steps if possible
- When human confirms fix, AI re-checks and resumes

## Execution Report

### Format

Generated after every execution to `ops/reports/YYYY-MM-DD-<action>.md`:

```markdown
# Execution Report: <action description>

**Time**: YYYY-MM-DD HH:MM — HH:MM
**Type**: Fresh Install | Upgrade | Health Check | Rollback
**Result**: Success | Success with notes | Failed (rolled back) | Blocked (waiting for human)

## Summary
| Phase | Status | Duration | Notes |
|-------|--------|----------|-------|
| Preflight | PASS | 12s | DNS OK, .env OK |
| Deploy | PASS | 48s | Docker build included ag402 upgrade |
| Verify | PASS | 5s | 7/7 checks passed |
| Payment Test | PASS | 3s | BONK audit $0.02, score 3/100 |

## Human Action Required
- (none, or specific items with instructions)

## Changes Made
- Code: f68a5ce → b4485ff (v0.1.7)
- ag402: 0.1.14 → 0.1.19
- .env: added RUGCHECK_PRODUCTION=true, UVLOOP_INSTALL=0

## Pre-Operation State
- Commit: f68a5ce
- Containers: 2 healthy
- .env hash: a1b2c3d4

## New Experiences
- (none, or link to new lessons/ file)

## Rollback Command
bash scripts/quick-update.sh {ip} "{domain}" f68a5ce
```

### Secrets Redaction in Reports

All sensitive values are redacted automatically:
- Private keys: `42E9...dsqv`
- API keys: `c944...3fd`
- RPC URLs: `https://mainnet.helius-rpc.com/?api-key=c944...3fd`

## Human Setup Guide

### Location: `ops/guides/setup-guide.md`

Step-by-step guide for non-technical users to prepare infrastructure before AI takes over.

### Phase 1: Buy a Server (est. 10 minutes)

- Recommended providers: Vultr, Hetzner, DigitalOcean
- Minimum spec: 1 vCPU, 1GB RAM, 25GB SSD, Ubuntu 22.04
- Steps: create account → create instance → select Ubuntu 22.04 → record IP address
- SSH key setup: how to generate (`ssh-keygen -t ed25519`), how to add to server
- Completion test: `ssh root@<IP> echo OK` returns OK

### Phase 2: Domain & Cloudflare (est. 15 minutes)

- Register domain or use subdomain of existing domain
- Create free Cloudflare account → add domain → change nameservers
- Create A record pointing to server IP → set SSL/TLS to Flexible
- Completion test: `dig +short <domain>` returns correct IP

### Phase 3: Solana Wallet (est. 5 minutes)

- Install Solana CLI or use Phantom browser extension
- Create seller wallet → record public key address
- Get RPC URL: recommend Helius free tier (https://helius.dev)
- Optional: prepare buyer test wallet + small amount of USDC ($1 is enough)
- Completion test: wallet address and RPC URL recorded

### Phase 4: Fill Manifest (est. 5 minutes)

- Copy `ops/manifest.yaml.example` to `ops/manifest.yaml`
- Copy `ops/.env.secrets.example` to `ops/.env.secrets`
- Fill in all fields (each field has inline comments with examples)
- Tell AI: "Manifest is ready, please deploy"

## Directory Structure

```
ops/
├── manifest.yaml                # Project config (git-tracked, no secrets)
├── manifest.yaml.example        # Template with inline docs
├── manifest.schema.yaml         # Validation rules
├── .env.secrets                 # Sensitive values (gitignored)
├── .env.secrets.example         # Template showing required secret fields
│
├── guides/
│   └── setup-guide.md           # Human preparation guide (server/domain/wallet/manifest)
│
├── runbooks/
│   ├── common/                  # Cross-project, use as-is
│   │   ├── 00-preflight.md
│   │   └── 01-server-init.md
│   ├── templates/               # Copy and customize per project
│   │   ├── 02-deploy.md
│   │   ├── 03-verify.md
│   │   ├── 04-payment-test.md
│   │   ├── 05-upgrade.md
│   │   ├── 06-rollback.md
│   │   └── 07-troubleshoot.md
│   └── project/
│       └── rugcheck/            # Token RugCheck instantiation
│           ├── deploy.md
│           ├── verify.md
│           ├── payment-test.md
│           └── troubleshoot.md
│
├── lessons/                     # AI-accumulated experience
│   └── (auto-generated files)
│
└── reports/                     # AI-generated execution reports
    └── (auto-generated files)

scripts/                         # All scripts in project root (single location)
├── quick-update.sh              # Existing — enhance with DNS diagnostics
├── deploy-oneclick.sh           # Existing
├── backup-data.sh               # Existing
├── verify.sh                    # Existing
├── preflight-check.sh           # New — pre-deploy automated checks
├── payment-test.py              # New — standardized ag402 payment test
└── monitor-deps.py              # New — upstream version checker
```

## Generalization Strategy

### New Project Onboarding Flow

1. Human follows `ops/guides/setup-guide.md` to prepare infrastructure
2. Human fills in `ops/manifest.yaml` for the new project
3. Human creates `ops/runbooks/project/<new-name>/` by copying from `templates/` and customizing:
   - `deploy.md`: which deploy script to use, compose file names
   - `verify.md`: project-specific health/API endpoints
   - `payment-test.md`: which endpoint to test, expected response schema
   - `troubleshoot.md`: project-specific failure modes
4. AI reads manifest → detects fresh install → executes common/preflight → common/server-init → project/deploy → project/verify → project/payment-test

### What's Truly Shared vs. Project-Specific

| Component | Shared | Project-Specific |
|-----------|--------|------------------|
| Preflight checks (DNS, SSH, disk, .env) | Yes | No |
| Server init (Docker, firewall, git clone) | Yes | No |
| Deploy workflow structure | Template | Scripts, compose files, git ref |
| Verification framework | Template | Endpoints, expected responses |
| Payment test protocol | Template | Test endpoint, expected schema |
| Troubleshoot decision trees | Template (infra) | Business-level failures |
| Experience library | Per-project | Per-project |
| Manifest schema | Shared core | Project-specific extensions |

## Dependency Monitoring

### Phase 1: Manual (MVP)

Human says "check for updates" in Claude Code. AI runs `scripts/monitor-deps.py`, reports findings.

### Phase 2: Automated (optional enhancement)

Server cron runs `monitor-deps.py` daily. On new version detected, sends notification via configured channel (GitHub Issue creation is simplest — no extra infra needed):

```bash
# Server crontab
0 9 * * * cd /opt/token-rugcheck && python3 scripts/monitor-deps.py --notify github-issue
```

Human sees GitHub Issue → opens Claude Code → says "upgrade to ag402 0.1.20" → AI executes full upgrade flow.

## Success Criteria

- [ ] Fresh install: AI completes zero-to-live from manifest alone (no human intervention after manifest is ready)
- [ ] Upgrade: AI detects version mismatch, completes upgrade + verify + payment test autonomously
- [ ] Rollback: AI can roll back code AND .env to pre-deploy state
- [ ] Payment test: works first try using Runbook (no trial-and-error)
- [ ] Experience: new problems get written to lessons/, existing lessons get applied
- [ ] Secrets: no plaintext secrets in git, reports, or command outputs
- [ ] New project: second ag402 service can onboard by filling manifest + customizing templates
- [ ] DNS failure: AI diagnoses root cause and gives human specific Cloudflare instructions
