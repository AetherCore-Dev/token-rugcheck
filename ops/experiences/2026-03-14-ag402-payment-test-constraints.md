# Experience: ag402 payment testing requires async client, ledger init, and crypto extras

**Date**: 2026-03-14
**Project**: Token RugCheck MCP
**Scenario**: Testing ag402 on-chain micropayments during v0.1.7 deployment
**Applies to**: ag402-core, v0.1.7+
**Occurrences**: 1
**Occurrence dates**: [2026-03-14]

## Problem
During v0.1.7 deployment, 6 different approaches to payment testing failed before finding the working method. Each failure revealed an undocumented constraint in ag402's security model or runtime requirements, turning a simple payment verification into a multi-hour debugging session.

## Root Cause
ag402 has undocumented security restrictions and implementation details that prevent most obvious approaches from working. The library only patches `httpx.AsyncClient.send` (not sync), enforces SSRF protection on private IPs, requires HTTPS for remote targets, needs an explicit ledger deposit before first use, and depends on optional crypto extras for Solana support.

## Solution
Use async httpx on the server host (not inside the Docker container), initialize the internal ledger with `AgentWallet.deposit()` before attempting payment, and ensure `ag402-core[crypto]` is installed so the `solana` module is available. Run the test from the host where `~/.ag402/wallet.db` is writable.

## Failed Attempts (avoid these)
- Attempt: `ag402 pay` CLI with localhost target → Result: SSRF protection blocks private IP addresses
- Attempt: `ag402 pay` CLI with HTTP remote URL → Result: Security requires HTTPS for non-localhost
- Attempt: `httpx.Client` (sync) with ag402 auto-payment → Result: ag402 only patches `httpx.AsyncClient.send`, not sync client
- Attempt: Run payment test inside Docker container → Result: `/home/appuser/.ag402/wallet.db` path not writable in container
- Attempt: Send payment without initializing ledger → Result: "Insufficient balance: $0" — internal ledger empty despite on-chain balance
- Attempt: Run on server without crypto extras → Result: Missing `solana` module — needs `ag402-core[crypto]`

## Status
- [x] Verified effective
- [x] Graduated to Runbook: ops/runbooks/templates/04-payment-test.md
