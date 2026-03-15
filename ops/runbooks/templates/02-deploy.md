# Runbook Template: Deploy

**Scope**: Template — copy to `ops/runbooks/project/{project.name}/02-deploy.md` and customize.
**When to use**: Deploy a new version of the project to the server.
**Prerequisites**: Preflight checks passed (00-preflight.md).
**References**: `scripts/quick-update.sh`, `scripts/deploy-oneclick.sh`

---

## Steps

### Step 0: State snapshot
**Do**: Record the current state before making any changes:
```
COMMIT_BEFORE=$(ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && git rev-parse HEAD")
CONTAINERS_BEFORE=$(ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && docker compose ps --format '{{.Name}} {{.Status}}'")
ENV_HASH_BEFORE=$(ssh {server.ssh_user}@{server.ip} "sha256sum {server.project_dir}/.env | cut -d' ' -f1")
echo "commit=$COMMIT_BEFORE containers=$CONTAINERS_BEFORE env_hash=$ENV_HASH_BEFORE"
```
**Expect**: Three values captured and logged. These are needed for rollback (06-rollback.md) if deployment fails.
**On failure**:
  - SSH connection fails -> escalate-to-human ("server unreachable — check SSH access")
  - .env file missing -> note as `ENV_HASH_BEFORE=NONE`, continue (first deploy scenario)
**Do NOT attempt**: Skipping state snapshot to save time — rollback depends on this data

### Step 1: Pre-deploy .env backup
**Do**: Create a timestamped backup of the current .env:
```
ssh {server.ssh_user}@{server.ip} "cp {server.project_dir}/.env {server.project_dir}/.env.bak.$(date +%s)"
```
**Expect**: Backup file created at `{server.project_dir}/.env.bak.<timestamp>`.
**On failure**:
  - .env does not exist (first deploy) -> skip this step, proceed to Step 2
  - Permission denied -> escalate-to-human ("file permission issue on server")
**Do NOT attempt**: Backing up .env to a publicly accessible location or logging its contents

### Step 2: Run deploy script
**Do**: Execute the quick-update script:
```
bash scripts/quick-update.sh {server.ip} {domain.name} {project.git_ref}
```
**Expect**: Exit code 0. Output contains "所有验证通过" (all verifications passed).
**On failure**:
  - Exit 1 + output contains "构建失败" -> build error, reference 07-troubleshoot.md "Build failing" symptom
  - Exit 1 + other error -> capture full output, reference 07-troubleshoot.md for matching symptom
  - SSH timeout during deploy -> retry once after 30 seconds; if still failing, escalate-to-human
**Do NOT attempt**: Running `docker compose up` manually without the deploy script — the script handles git pull, build, env validation, and restart in the correct order

### Step 3: Parse script output
**Do**: Evaluate the deploy script result:
```
# Success criteria:
# - Exit code 0
# - Output contains "所有验证通过"
#
# Failure criteria:
# - Exit code 1 + "构建失败" = build error
# - Exit code 1 + "容器未运行" = container start failure
# - Exit code 1 + other = unknown failure
```
**Expect**: Success criteria met. Proceed to verification (03-verify.md).
**On failure**:
  - Build error -> reference 07-troubleshoot.md "Build failing" symptom
  - Container start failure -> reference 07-troubleshoot.md "Container not starting" symptom
  - Unknown failure -> capture output, escalate-to-human with full error context
**Do NOT attempt**: Ignoring non-zero exit codes and proceeding to verification anyway
