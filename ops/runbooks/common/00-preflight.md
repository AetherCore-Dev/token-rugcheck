# Runbook: Pre-Deploy Preflight Checks

**Scope**: Cross-project — works for any ag402 + FastAPI + Docker deployment.
**When to use**: Before every deploy or upgrade. Run this first to catch issues early.
**References**: `scripts/preflight-check.sh`, `scripts/generate-env.sh`

---

## Steps

### Step 1: Run preflight checks
**Do**: `bash scripts/preflight-check.sh --manifest ops/manifest.yaml`
**Expect**: Exit code 0. All output lines show `CHECK:*:PASS`. Summary line shows `fail=0`.
**On failure**:
  - Exit 2 (blocking — SSH or Docker missing) -> proceed to Step 4
  - Exit 1 (fixable issues) -> inspect output for `CHECK:*:FAIL` lines, proceed to Step 2 or Step 3 as needed
  - Manifest not found -> escalate-to-human ("ops/manifest.yaml missing — copy from manifest.yaml.example and fill in values")
**Do NOT attempt**: Re-running preflight in a loop hoping transient failures resolve themselves

### Step 2: Auto-fix: .env gaps
**Do**: If `CHECK:ENV:FAIL` appeared in Step 1, generate a corrected .env:
```
bash scripts/generate-env.sh \
  --mode {ag402.mode} \
  --address {ag402.address} \
  --price {ag402.price} \
  --rpc-url {ag402.rpc_url} \
  --output /tmp/.env.generated
scp /tmp/.env.generated {server.ssh_user}@{server.ip}:{server.project_dir}/.env
rm -f /tmp/.env.generated
```
Then re-run the env check: `ssh {server.ssh_user}@{server.ip} "grep -cE '^[A-Z_]+=' {server.project_dir}/.env"`
**Expect**: .env on server contains all required variables. Re-running `bash scripts/preflight-check.sh --manifest ops/manifest.yaml` shows `CHECK:ENV:PASS`.
**On failure**:
  - generate-env.sh exits non-zero -> escalate-to-human with the specific validation error message
  - Variables still missing after regeneration -> escalate-to-human listing exact missing variable names
**Do NOT attempt**: Echoing secret values into SSH command strings (violates Secrets Rule #6 and #7)

### Step 3: Auto-fix: disk space
**Do**: If `CHECK:DISK:FAIL` appeared in Step 1, free space on the server:
```
ssh {server.ssh_user}@{server.ip} "docker system prune -f && docker image prune -a -f --filter 'until=168h'"
```
Then re-check: `ssh {server.ssh_user}@{server.ip} "df -h / | tail -1"`
**Expect**: At least 2 GB free after cleanup. Re-running preflight shows `CHECK:DISK:PASS`.
**On failure**:
  - Less than 2 GB free even after prune -> escalate-to-human ("manual cleanup needed — large files or non-Docker data consuming disk")
**Do NOT attempt**: Deleting files outside the project directory or Docker storage without human approval

### Step 4: Blocking issues
**Do**: If `CHECK:SSH:FAIL` or `CHECK:DOCKER:FAIL` appeared, stop all automation and report to human with specific instructions:
  - SSH failure -> "Cannot reach {server.ip} via SSH. Verify: (1) server is powered on, (2) IP is correct in manifest, (3) SSH key is authorized, (4) firewall allows port 22."
  - Docker failure -> "Docker not installed or not running on {server.ip}. Run server-init runbook (01-server-init.md) first, or manually install Docker."
**Expect**: Human resolves the issue and confirms ready to retry.
**On failure**:
  - Human unable to fix -> remain escalated, do not proceed with deployment
**Do NOT attempt**: Retrying SSH if the server is unreachable; installing Docker via alternative package managers (snap, brew) as they cause permission conflicts with compose
