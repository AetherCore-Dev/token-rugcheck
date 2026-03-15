# Runbook Template: Verify

**Scope**: Template — copy to `ops/runbooks/project/{project.name}/03-verify.md` and customize.
**When to use**: After every deploy or upgrade to confirm the service is healthy.
**Prerequisites**: Deploy completed (02-deploy.md).
**References**: `scripts/verify.sh`

---

## Steps

### Step 1: Run verification script
**Do**: Execute the 5-layer verification:
```
bash scripts/verify.sh --server-ip {server.ip} --domain {domain.name}
```
**Expect**: Exit code 0. All output lines show `OK`. Summary shows all layers passed.
**On failure**:
  - Exit code non-zero -> proceed to Step 2 to parse individual results
  - Script not found -> ensure scripts/verify.sh exists and is executable (`chmod +x scripts/verify.sh`)
**Do NOT attempt**: Skipping verification after a deploy — silent failures compound into harder-to-debug states

### Step 2: Parse structured output
**Do**: Examine each verification line for `OK`, `FAIL`, or `SKIP` status:
```
# Expected output format (one line per check):
# VERIFY:CONTAINER:OK    — Docker container running
# VERIFY:HEALTH:OK       — Health endpoint returns 200
# VERIFY:BUSINESS:OK     — Business endpoint responds correctly
# VERIFY:HTTPS:OK        — HTTPS accessible via domain
# VERIFY:PAYMENT:OK      — Payment flow returns 402 + receipt
```
**Expect**: All 5 checks show `OK` or acceptable `SKIP`.
**On failure**:
  - Any line shows `FAIL` -> proceed to Step 3 for auto-rollback vs degrade-continue decision
  - Output is empty or malformed -> re-run once; if still malformed, escalate-to-human
**Do NOT attempt**: Treating `SKIP` as `FAIL` — skips are expected in certain configurations

### Step 3: Apply auto-rollback vs degrade-continue rules
**Do**: Evaluate each failure against these five rules:

1. **Container not running** (`VERIFY:CONTAINER:FAIL`) OR **health non-200** (`VERIFY:HEALTH:FAIL`)
   -> **auto-rollback**: execute 06-rollback.md immediately
   Reason: service is fundamentally broken, no traffic can be served.

2. **Business endpoint 5xx** (`VERIFY:BUSINESS:FAIL` with 5xx status)
   -> **auto-rollback**: execute 06-rollback.md immediately
   Reason: application error prevents core functionality.

3. **HTTPS fails but HTTP:IP works** (`VERIFY:HTTPS:FAIL` but `VERIFY:HEALTH:OK`)
   -> **degrade-continue**: log warning, do NOT rollback
   Reason: DNS or Cloudflare external issue — the application itself is healthy.

4. **Payment test fails but 402 works** (`VERIFY:PAYMENT:FAIL` but 402 status returned)
   -> **degrade-continue**: log warning, do NOT rollback
   Reason: paywall is active (402 returned), but payment infrastructure has an issue (wallet balance, RPC, etc.).

5. **Payment test skipped** (`VERIFY:PAYMENT:SKIP` — no buyer key configured)
   -> **degrade-continue**: log info, do NOT rollback
   Reason: payment testing requires a funded buyer wallet; skip is expected in CI or staging.

**Expect**: Either all checks pass, or failure disposition is clear (rollback or degrade-continue).
**On failure**:
  - Multiple rollback-worthy failures -> execute 06-rollback.md once (covers all failures)
  - Rollback itself fails -> escalate-to-human with both the original failure and rollback failure details
**Do NOT attempt**: Partial rollbacks (rolling back only one component) — always roll back the entire deployment as a unit
