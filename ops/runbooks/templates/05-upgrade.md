# Runbook Template: Upgrade

**Scope**: Template — copy to `ops/runbooks/project/{project.name}/05-upgrade.md` and customize.
**When to use**: Upgrade a running deployment to a new version.
**Prerequisites**: Service is currently deployed and running.
**References**: 00-preflight.md, 02-deploy.md, 03-verify.md, 04-payment-test.md

---

## Steps

### Step 1: Compare current version vs target
**Do**: Check whether an upgrade is needed:
```
CURRENT=$(ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && git rev-parse HEAD")
TARGET={project.git_ref}
echo "current=$CURRENT target=$TARGET"
```
**Expect**: Two distinct commit hashes indicating an upgrade is needed.
**On failure**:
  - SSH fails -> escalate-to-human ("cannot reach server to check version")
  - git not found on server -> server may not be initialized; run 01-server-init.md first
**Do NOT attempt**: Comparing version strings instead of commit hashes — tags can be moved, commits are immutable

### Step 2: Skip if already at target
**Do**: If `CURRENT` equals `TARGET`, report and stop:
```
# If CURRENT == TARGET:
echo "Already at target version $TARGET — skipping upgrade"
# Exit the runbook here. No further steps needed.
```
**Expect**: Either "already at target" (done) or versions differ (continue to Step 3).
**On failure**:
  - N/A — this step always succeeds
**Do NOT attempt**: Re-deploying the same version "just to be safe" — it wastes time and risks breaking a working deployment

### Step 3: Run preflight checks
**Do**: Execute the preflight runbook to validate the environment:
```
# Follow 00-preflight.md steps in full
bash scripts/preflight-check.sh --manifest ops/manifest.yaml
```
**Expect**: All preflight checks pass (`fail=0`). Environment is ready for upgrade.
**On failure**:
  - Preflight fails -> follow 00-preflight.md remediation steps before proceeding
  - Blocking issues (SSH/Docker) -> escalate-to-human per 00-preflight.md Step 4
**Do NOT attempt**: Skipping preflight to save time — upgrades are the most common source of regressions

### Step 4: Run deploy
**Do**: Execute the deploy runbook with the target version:
```
# Follow 02-deploy.md steps in full
bash scripts/quick-update.sh {server.ip} {domain.name} {project.git_ref}
```
**Expect**: Deploy succeeds. Exit code 0 with "所有验证通过" in output.
**On failure**:
  - Deploy fails -> follow 02-deploy.md failure handling
  - Build error -> reference 07-troubleshoot.md "Build failing" symptom
**Do NOT attempt**: Running `git pull` and `docker compose up` manually — the deploy script handles the full sequence safely

### Step 5: Run verify
**Do**: Execute the verification runbook:
```
# Follow 03-verify.md steps in full
bash scripts/verify.sh --server-ip {server.ip} --domain {domain.name}
```
**Expect**: All 5 verification layers pass or show acceptable degrade-continue.
**On failure**:
  - Auto-rollback conditions met -> execute 06-rollback.md immediately
  - Degrade-continue conditions -> log warnings and proceed to Step 6
**Do NOT attempt**: Skipping verification after upgrade — version changes are the highest-risk operation

### Step 6: Run payment test
**Do**: Execute the payment test runbook:
```
# Follow 04-payment-test.md steps in full
ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && python3 scripts/payment-test.py --manifest {manifest_path}"
```
**Expect**: `PAYMENT_TEST:PASS` — payment flow works on the new version.
**On failure**:
  - Payment test fails -> follow 04-payment-test.md diagnostics
  - Payment test skipped (no buyer key) -> acceptable, log as degrade-continue
**Do NOT attempt**: Treating payment test failure as a rollback trigger unless the 402 paywall itself is broken — payment infra issues are degrade-continue
