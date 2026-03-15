# Runbook: Fresh Server Initialization

**Scope**: Cross-project — works for any ag402 + FastAPI + Docker deployment.
**When to use**: First-time deployment when the project directory does not exist on the server yet.
**References**: `scripts/setup-server.sh`, `scripts/generate-env.sh`

---

## Steps

### Step 1: Install Docker
**Do**: `ssh root@{server.ip} "curl -fsSL https://get.docker.com | sh && systemctl enable docker && systemctl start docker"`
Then verify: `ssh root@{server.ip} "docker --version && docker compose version"`
**Expect**: Both `docker --version` and `docker compose version` return version strings without error.
**On failure**:
  - curl/pipe install fails -> try manual apt fallback: `ssh root@{server.ip} "apt-get update && apt-get install -y docker.io docker-compose-plugin"`
  - `docker compose version` fails but `docker --version` succeeds -> install compose plugin: `ssh root@{server.ip} "apt-get update && apt-get install -y docker-compose-plugin"`
  - Both fallbacks fail -> escalate-to-human ("Docker installation failed — check OS compatibility and network access")
**Do NOT attempt**: `snap install docker` — causes permission issues with volume mounts and compose socket access

### Step 2: Configure firewall
**Do**: `ssh root@{server.ip} "ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable"`
Then verify: `ssh root@{server.ip} "ufw status verbose"`
**Expect**: ufw status shows `Status: active` with rules for 22, 80, and 443. Default incoming policy is deny.
**On failure**:
  - `ufw: command not found` -> install it first: `ssh root@{server.ip} "apt-get update && apt-get install -y ufw"` then retry
  - ufw enable fails -> escalate-to-human ("firewall configuration failed — check for conflicting firewall tools like firewalld or iptables-persistent")
**Do NOT attempt**: Disabling the firewall entirely to work around port issues

### Step 3: Clone repository
**Do**: `ssh root@{server.ip} "git clone {project.repo} {server.project_dir}"`
**Expect**: `ssh root@{server.ip} "ls {server.project_dir}/docker-compose.yml"` succeeds — project directory exists with code.
**On failure**:
  - `git: command not found` -> install git: `ssh root@{server.ip} "apt-get update && apt-get install -y git"` then retry clone
  - Permission denied (publickey) -> escalate-to-human ("SSH key not authorized for repo. Add server's public key to the repository or use HTTPS with a token.")
  - Directory already exists -> `ssh root@{server.ip} "cd {server.project_dir} && git pull origin main"` to update instead
**Do NOT attempt**: Cloning via HTTPS with inline credentials in the URL

### Step 4: Generate .env and transfer to server
**Do**: Generate the complete .env file locally using manifest and secrets values, then scp to server (per Secrets Rule #6):
```
bash scripts/generate-env.sh \
  --mode {ag402.mode} \
  --address {ag402.address} \
  --price {ag402.price} \
  --rpc-url {ag402.rpc_url} \
  --output /tmp/.env.generated
scp /tmp/.env.generated root@{server.ip}:{server.project_dir}/.env
rm -f /tmp/.env.generated
```
Then verify: `ssh root@{server.ip} "grep -cE '^[A-Z_]+=' {server.project_dir}/.env"`
**Expect**: .env file exists on server with all required variables. Variable count matches or exceeds what .env.example defines.
**On failure**:
  - generate-env.sh validation error -> fix the input values (check manifest.yaml and .env.secrets) and retry
  - scp fails -> check SSH connectivity (should already work from Step 1) and target directory permissions
  - Variables missing after transfer -> compare against .env.example: `diff <(grep -E '^[A-Z_]+=' .env.example | sed 's/=.*//') <(ssh root@{server.ip} "grep -E '^[A-Z_]+=' {server.project_dir}/.env | sed 's/=.*//'")` and add missing ones
**Do NOT attempt**: Echoing secret values into SSH command strings; piping secrets through stdin over SSH; hardcoding secrets in scripts

### Step 5: Initial build and start
**Do**:
```
ssh root@{server.ip} "cd {server.project_dir} && docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
```
Then verify:
```
ssh root@{server.ip} "cd {server.project_dir} && docker compose -f docker-compose.yml -f docker-compose.prod.yml ps --format 'table {{.Name}}\t{{.Status}}'"
```
**Expect**: All containers show status `Up` or `healthy`. No containers in `Restarting` or `Exit` state.
**On failure**:
  - Build fails -> check build logs: `ssh root@{server.ip} "cd {server.project_dir} && docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache 2>&1 | tail -50"` and fix the reported error
  - Containers exit immediately -> check logs: `ssh root@{server.ip} "cd {server.project_dir} && docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 50"` — usually a missing .env variable or port conflict
  - Health check fails -> verify .env completeness (return to Step 4), check that required ports (80, 8000, 8001) are not occupied by non-project processes
**Do NOT attempt**: Running `docker compose up` without the `--no-cache` flag on first build (stale layers from prior attempts cause confusing errors); using `docker-compose` (legacy v1) instead of `docker compose` (v2 plugin)
