# Runbook Template: Rollback

**Scope**: Template — copy to `ops/runbooks/project/{project.name}/06-rollback.md` and customize.
**When to use**: When a deploy or upgrade fails verification and auto-rollback is triggered.
**Prerequisites**: State snapshot from 02-deploy.md Step 0 is available.
**References**: 02-deploy.md (state snapshot), 03-verify.md (post-rollback verify)

---

## Steps

### Step 1: Identify previous commit
**Do**: Retrieve the commit hash to roll back to from the deploy state snapshot or git history:
```
# Option A: Use state snapshot from 02-deploy.md Step 0
ROLLBACK_TO=$COMMIT_BEFORE

# Option B: If no snapshot, use git log
ROLLBACK_TO=$(ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && git log --oneline -5" | head -2 | tail -1 | awk '{print $1}')

echo "Rolling back to: $ROLLBACK_TO"
```
**Expect**: A valid commit hash identified as the rollback target.
**On failure**:
  - No state snapshot and git log empty -> escalate-to-human ("cannot determine rollback target — no deploy history available")
  - Commit hash is the same as current -> escalate-to-human ("rollback target is same as current — manual intervention needed")
**Do NOT attempt**: Rolling back to an arbitrary "known good" commit without verifying it was the immediately previous state

### Step 2: Git checkout previous commit
**Do**: Switch the server's working directory to the rollback target:
```
ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && git fetch origin && git checkout {ROLLBACK_TO}"
```
**Expect**: Git checkout succeeds. `HEAD` now points to `{ROLLBACK_TO}`.
**On failure**:
  - Checkout fails due to local changes -> force checkout: `git checkout -f {ROLLBACK_TO}`
  - Commit not found -> `git fetch origin` first, then retry
**Do NOT attempt**: Using `git reset --hard` on a shared branch — checkout of a specific commit is safer and preserves branch history

### Step 3: Docker compose rebuild and restart
**Do**: Rebuild and restart containers with the rolled-back code:
```
ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && docker compose down && docker compose up -d --build"
```
If pre-built images are available for the rollback commit, skip the build:
```
ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && docker compose down && docker compose up -d --no-build"
```
**Expect**: Containers start successfully. `docker compose ps` shows all services running.
**On failure**:
  - Build fails on old code -> try `--no-build` if images are cached; otherwise escalate-to-human
  - Containers fail to start -> reference 07-troubleshoot.md "Container not starting" symptom
**Do NOT attempt**: Using `docker compose restart` alone — it does not pick up code changes from the git checkout

### Step 4: Restore .env backup
**Do**: Restore the .env file from the most recent backup:
```
BAK=$(ssh {server.ssh_user}@{server.ip} "ls -1t {server.project_dir}/.env.bak.* 2>/dev/null | head -1")
if [ -n "$BAK" ]; then
  ssh {server.ssh_user}@{server.ip} "cp $BAK {server.project_dir}/.env"
  echo "Restored .env from $BAK"
else
  echo "NO_BACKUP"
fi
```
**Expect**: .env restored from backup, or `NO_BACKUP` reported.
**On failure**:
  - `NO_BACKUP` -> AI must reconstruct .env from manifest + .env.secrets + .env.example using `scripts/generate-env.sh`
  - Backup file corrupted (empty or malformed) -> treat as `NO_BACKUP`, reconstruct .env
**Do NOT attempt**: Proceeding without a valid .env — the service will fail to start or behave incorrectly

### Step 5: Verify after rollback
**Do**: Run the full verification suite on the rolled-back deployment:
```
# Follow 03-verify.md steps in full
bash scripts/verify.sh --server-ip {server.ip} --domain {domain.name}
```
**Expect**: All verification checks pass. Service is healthy on the previous version.
**On failure**:
  - Verification fails after rollback -> escalate-to-human ("rollback did not restore healthy state — manual investigation required")
  - Container not running after rollback -> reference 07-troubleshoot.md; likely .env or port conflict issue
**Do NOT attempt**: Triggering another rollback from a failed rollback — this creates a loop. Escalate to human instead.
