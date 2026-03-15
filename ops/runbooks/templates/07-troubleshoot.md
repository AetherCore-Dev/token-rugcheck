# Runbook Template: Troubleshoot

**Scope**: Template — copy to `ops/runbooks/project/{project.name}/07-troubleshoot.md` and customize.
**When to use**: When any other runbook encounters a failure and references this document.
**Format**: Symptom-based diagnostic trees — jump to the matching symptom, follow the diagnosis.

---

## Symptom: Container not starting

### Diagnosis
1. Check container logs -> `ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && docker compose logs --tail=50"` -> if "port already in use" then port conflict
2. Check .env file exists -> `ssh {server.ssh_user}@{server.ip} "test -f {server.project_dir}/.env && echo EXISTS || echo MISSING"` -> if MISSING then .env issue
3. Check Docker daemon -> `ssh {server.ssh_user}@{server.ip} "docker info > /dev/null 2>&1 && echo OK || echo FAIL"` -> if FAIL then Docker daemon issue

### Common Causes
- **Port conflict**: Another process or previous container occupying the port. Check with `ss -tlnp | grep {port}`.
- **Missing .env**: Container requires environment variables that are not set. Regenerate with `scripts/generate-env.sh`.
- **Docker daemon stopped**: systemd service not running. Restart with `systemctl restart docker`.
- **Image build failure cached**: Stale build cache causing repeated failures. Clear with `docker compose build --no-cache`.

### Resolution
- Port conflict -> `ssh {server.ssh_user}@{server.ip} "docker compose down && docker compose up -d"` (releases the port first)
- Missing .env -> follow 00-preflight.md Step 2 to regenerate
- Docker daemon -> `ssh {server.ssh_user}@{server.ip} "sudo systemctl restart docker"` then retry
- Stale cache -> `ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && docker compose build --no-cache && docker compose up -d"`

---

## Symptom: Health check failing

### Diagnosis
1. Check container is running -> `ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && docker compose ps"` -> if no containers, see "Container not starting"
2. Check port binding -> `ssh {server.ssh_user}@{server.ip} "curl -s -o /dev/null -w '%{http_code}' http://localhost:{port}/health"` -> if connection refused then process not listening
3. Check application logs -> `ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && docker compose logs --tail=30"` -> look for startup errors, uncaught exceptions

### Common Causes
- **Process crashed inside container**: Application exited but container is still "running" (restart policy keeps restarting it). Logs show repeated crash-restart cycle.
- **Wrong port binding**: Application listens on a different port than the one exposed in docker-compose.yml.
- **Application startup error**: Missing dependency, configuration error, or database connection failure preventing the app from becoming healthy.

### Resolution
- Process crash -> fix the root cause in application code or configuration; check logs for the specific error
- Wrong port -> verify `{port}` in docker-compose.yml matches the application's listen port
- Startup error -> check .env for required variables; ensure all dependencies are available

---

## Symptom: HTTPS failing

### Diagnosis
1. Check DNS resolution -> `dig +short {domain.name}` -> if empty or wrong IP then DNS issue
2. Check HTTP works on IP -> `curl -s -o /dev/null -w '%{http_code}' http://{server.ip}:{port}/health` -> if 200 then app is fine, HTTPS/DNS is the issue
3. Check Cloudflare SSL mode -> Cloudflare dashboard > SSL/TLS > Overview -> should be "Full (strict)" for most setups
4. Check certificate -> `echo | openssl s_client -connect {domain.name}:443 -servername {domain.name} 2>/dev/null | openssl x509 -noout -dates` -> if expired or not found then certificate issue

### Common Causes
- **DNS not pointing to server**: Domain resolves to wrong IP or has no A record. Requires Cloudflare DNS update (Level 3 — escalate-to-human).
- **Cloudflare SSL mode mismatch**: "Flexible" mode causes redirect loops; "Full (strict)" requires a valid origin certificate.
- **Certificate expired**: Origin certificate or Cloudflare edge certificate needs renewal.
- **Cloudflare proxy not enabled**: DNS record is "DNS only" (grey cloud) instead of "Proxied" (orange cloud).

### Resolution
- DNS wrong -> escalate-to-human ("DNS A record for {domain.name} does not point to {server.ip} — update needed in Cloudflare")
- SSL mode -> escalate-to-human ("Cloudflare SSL mode may need adjustment — current mode causing issues")
- Certificate expired -> escalate-to-human ("SSL certificate for {domain.name} needs renewal")
- Proxy not enabled -> escalate-to-human ("Cloudflare proxy (orange cloud) needs to be enabled for {domain.name}")

---

## Symptom: Payment test failing

### Diagnosis
1. Check 402 response -> `curl -s -o /dev/null -w '%{http_code}' https://{domain.name}/{ag402.protected_endpoint}` -> if not 402 then paywall middleware not active
2. Check wallet balance -> `ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && python3 -c \"from ag402 import check_balance; print(check_balance())\""` -> if zero then wallet unfunded
3. Check ag402 mode -> verify `AG402_MODE` in .env matches `{ag402.mode}` in manifest
4. Check ledger state -> `ssh {server.ssh_user}@{server.ip} "ls -la {server.project_dir}/data/ledger.db"` -> if missing or corrupted then replay-protection database issue

### Common Causes
- **ag402 middleware not loaded**: Application started without payment middleware. Check that ag402 is imported and mounted in the FastAPI app.
- **Wallet unfunded**: Buyer or seller wallet has zero balance. Payment transactions will fail.
- **Mode mismatch**: ag402 running in "test" mode vs "live" mode, or vice versa.
- **Ledger corruption**: SQLite replay-protection database is corrupted or locked. Restore from backup (`scripts/backup-data.sh`).
- **SSRF protection**: Attempting to pay localhost from inside the container is blocked by ag402 SSRF protection.

### Resolution
- Middleware not loaded -> check application code for ag402 middleware registration
- Wallet unfunded -> escalate-to-human ("wallet needs funding — Level 3 action required")
- Mode mismatch -> fix `AG402_MODE` in .env to match manifest, restart container
- Ledger corruption -> restore from backup: `bash scripts/backup-data.sh --restore`; if no backup, delete and let ag402 recreate: `rm {server.project_dir}/data/ledger.db && docker compose restart`
- SSRF -> run payment test from the host, not from inside the Docker container (see 04-payment-test.md)

---

## Symptom: Build failing

### Diagnosis
1. Check disk space -> `ssh {server.ssh_user}@{server.ip} "df -h / | tail -1"` -> if less than 2 GB free then disk space issue
2. Check Docker daemon -> `ssh {server.ssh_user}@{server.ip} "docker info > /dev/null 2>&1 && echo OK || echo FAIL"` -> if FAIL then Docker daemon issue
3. Check build logs -> `ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && docker compose build 2>&1 | tail -30"` -> look for specific error messages
4. Check dependency resolution -> look for pip/npm/cargo errors in build output indicating version conflicts or missing packages

### Common Causes
- **Disk space exhausted**: Docker images and build cache consume all available space. `docker system prune` frees space.
- **Docker daemon not running**: systemd service stopped or crashed. Restart required.
- **Dependency resolution failure**: A package version was yanked, a registry is unreachable, or version constraints conflict.
- **Dockerfile syntax error**: Recent change introduced a Dockerfile error (missing stage, wrong base image, etc.).
- **Build context too large**: `.dockerignore` missing or incomplete, sending gigabytes of context to the Docker daemon.

### Resolution
- Disk space -> follow 00-preflight.md Step 3 to free space with `docker system prune -f && docker image prune -a -f --filter 'until=168h'`
- Docker daemon -> `ssh {server.ssh_user}@{server.ip} "sudo systemctl restart docker"` then retry build
- Dependency failure -> check if the specific package/version is available; update version constraints if needed
- Dockerfile error -> review recent changes to Dockerfile; fix syntax and rebuild
- Build context -> ensure `.dockerignore` excludes `.git/`, `node_modules/`, `__pycache__/`, and other large directories
