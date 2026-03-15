# Experience: Solana private key exposed in tool output via inline env var

**Date**: 2026-03-14
**Project**: Token RugCheck MCP
**Scenario**: Deploying v0.1.7 and configuring Solana wallet credentials
**Applies to**: All deployments handling secrets
**Occurrences**: 1
**Occurrence dates**: [2026-03-14]

## Problem
During v0.1.7 deployment, a Solana private key was exposed in plain text in tool output. A TaskStop command displayed the full command string including the inline PRIVATE_KEY value, making the secret visible in logs and conversation history.

## Root Cause
The secret was passed as an inline environment variable in a command string (`PRIVATE_KEY='xxx' python3 script.py`), which caused it to appear in process listings and tool output. Any tool that displays the command being run — or any `ps` invocation — would reveal the secret.

## Solution
Never pass secrets as inline environment variables or CLI arguments. Instead, write secrets to a `.env` file or temporary file and let scripts read from there. Transfer secrets to remote servers via `scp`, not SSH echo. After transfer, delete any local temporary copies. This is now codified in Secrets Handling Rules #6 and #7 in CLAUDE.md.

## Failed Attempts (avoid these)
- Attempt: Pass secret as inline env var in command string → Result: Secret visible in tool output and process listings

## Status
- [x] Verified effective
- [x] Graduated to Runbook: CLAUDE.md Secrets Handling Rules #6 and #7
