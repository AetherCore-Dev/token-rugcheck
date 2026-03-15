# Runbook Template: Payment Test

**Scope**: Template — copy to `ops/runbooks/project/{project.name}/04-payment-test.md` and customize.
**When to use**: After deploy/upgrade to confirm the ag402 payment flow works end-to-end.
**Prerequisites**: Service is running and health check passes (03-verify.md).
**References**: `scripts/payment-test.py`

---

## Steps

### Step 1: Upload payment test script
**Do**: Copy the payment test script to the server:
```
scp scripts/payment-test.py {server.ssh_user}@{server.ip}:{server.project_dir}/scripts/payment-test.py
```
**Expect**: File transferred successfully. Exit code 0.
**On failure**:
  - SCP fails -> check SSH connectivity; reference 07-troubleshoot.md "Container not starting" for SSH diagnostics
  - scripts/ directory missing on server -> create it: `ssh {server.ssh_user}@{server.ip} "mkdir -p {server.project_dir}/scripts"`
**Do NOT attempt**: Pasting the script contents via SSH echo — risks quoting issues and secret exposure

### Step 2: Install ag402 dependency
**Do**: Ensure the ag402 crypto library is available on the server:
```
ssh {server.ssh_user}@{server.ip} "pip install 'ag402-core[crypto]'"
```
**Expect**: Package installed or already satisfied. Exit code 0.
**On failure**:
  - pip not found -> try `pip3` instead: `ssh {server.ssh_user}@{server.ip} "pip3 install 'ag402-core[crypto]'"`
  - Permission denied -> use `--user` flag: `pip install --user 'ag402-core[crypto]'`
  - Network error -> escalate-to-human ("server cannot reach PyPI — check outbound network")
**Do NOT attempt**: Installing ag402 inside the Docker container — the payment test must run on the host to avoid SSRF restrictions

### Step 3: Run payment test
**Do**: Execute the payment test against the manifest configuration:
```
ssh {server.ssh_user}@{server.ip} "cd {server.project_dir} && python3 scripts/payment-test.py --manifest {manifest_path}"
```
**Expect**: Output contains `PAYMENT_TEST:PASS`. Exit code 0.
**On failure**:
  - Output contains `PAYMENT_TEST:FAIL` -> proceed to Step 4 for diagnostics
  - Script crashes with ImportError -> re-run Step 2 to ensure ag402-core is installed
  - Timeout -> the service may be overloaded; wait 10 seconds and retry once
**Do NOT attempt**: Running the payment test without `--manifest` — it needs the manifest to discover endpoints and payment config

### Step 4: Parse payment test result
**Do**: Evaluate the `PAYMENT_TEST:PASS` or `PAYMENT_TEST:FAIL` output:
```
# PASS -> payment flow works end-to-end (402 -> pay -> 200 with receipt)
# FAIL -> check the error detail line following PAYMENT_TEST:FAIL for specific cause
```
**Expect**: `PAYMENT_TEST:PASS` — the full pay-and-verify cycle succeeded.
**On failure**:
  - "402 not returned" -> service is not requiring payment; check ag402 middleware configuration
  - "payment rejected" -> wallet may be unfunded or ledger has replay-protection issue; reference 07-troubleshoot.md "Payment test failing"
  - "receipt invalid" -> ag402 version mismatch between client and server; check installed versions
**Do NOT attempt**:
  - `ag402 pay` CLI for localhost targets — SSRF protection blocks loopback requests
  - `ag402 pay` CLI for HTTP (non-HTTPS) remote targets — the CLI enforces HTTPS
  - `httpx.Client` (synchronous) for ag402 — only `httpx.AsyncClient` is patched by ag402
  - Running the payment test inside the Docker container — wallet DB path is not writable in the container filesystem
