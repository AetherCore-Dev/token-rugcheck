# CLAUDE.md — Token RugCheck MCP

Solana token safety audit service with ag402 on-chain micropayments.
Built as an MCP (Model Context Protocol) server; clients pay per-query via the ag402 payment layer.

---

## Ops Instructions

When asked to deploy, upgrade, maintain, or check this project:

1. Read `ops/manifest.yaml` for project config
2. Read `ops/.env.secrets` for sensitive values (**NEVER** log or echo these)
3. Scan `ops/experiences/` for relevant experience entries
4. SSH to server -> auto-detect current state (fresh / needs upgrade / stopped / healthy)
5. Follow the appropriate Runbook in `ops/runbooks/project/{project.name}/`
   - If project Runbooks don't exist yet, generate them from `ops/runbooks/templates/`
6. After execution: write report to `ops/reports/`, check experience graduation
7. Summarize result to human

---

## Secrets Handling Rules

These rules apply to **every** session. No exceptions.

1. **manifest.yaml** — never contains secrets. Git-tracked, safe to commit.
2. **.env.secrets** — gitignored. Only exists on local machine and server.
3. **Command execution** — pass secrets via environment variables, never in command strings.
4. **Execution reports** — redact all secrets. Show only `first4...last4`.
5. **Experiences and Runbooks** — never contain actual secret values.
6. **Server .env transfer** — use `scp`, not SSH echo.
   Pattern: generate complete .env locally -> scp to server -> delete local copy.
7. **No inline secrets in commands** — never pass secrets as CLI args or inline env vars.
   Write to .env file, let scripts read from there.

---

## AI Autonomy Levels

### Level 1 — Fully Autonomous
.env fixes, container restart, disk cleanup, port conflicts (project processes only),
build retries, ag402 ledger deposit, dependency installation.

### Level 2 — Autonomous + Report
Version drift, skipped steps, new problems fixed, HTTPS issues.

### Level 3 — Escalate to Human
DNS changes in Cloudflare, SSH unreachable, Cloudflare SSL config,
wallet funding, invalid manifest.

---

## Scripts Reference

| Script | Purpose |
|---|---|
| `scripts/deploy-oneclick.sh` | One-click deploy from zero to production |
| `scripts/quick-update.sh` | Fast update of an already-deployed instance |
| `scripts/generate-env.sh` | Generate .env file from CLI arguments |
| `scripts/setup-server.sh` | Server initialization (run on remote via SSH pipe) |
| `scripts/verify.sh` | 5-layer deployment verification |
| `scripts/backup-data.sh` | ag402 SQLite replay-protection data backup |

---

## Development

```bash
pytest tests/ -x -q
```

Test suite covers: server endpoints, payment security, ag402 e2e flow,
caching, engine logic, fetchers, and general security.

---

## Key Files

- `ops/manifest.yaml.example` — config template (copy to `manifest.yaml`)
- `ops/.env.secrets.example` — secrets template (copy to `.env.secrets`)
- `ops/manifest.schema.yaml` — manifest validation schema
- `ops/experiences/` — operational experience entries
- `ops/runbooks/` — deployment and maintenance runbooks
- `ops/reports/` — execution reports
