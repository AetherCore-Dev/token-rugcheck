# Experience: DNS A record pointed to wrong IP causing HTTPS verification failure

**Date**: 2026-03-14
**Project**: Token RugCheck MCP
**Scenario**: Final verification of v0.1.7 deployment — HTTPS health check
**Applies to**: All deployments with Cloudflare DNS
**Occurrences**: 1
**Occurrence dates**: [2026-03-14]

## Problem
During v0.1.7 verification, the HTTPS check returned HTTP status 000 (connection failure). Running `dig +short rugcheck.aethercore.dev` revealed the domain resolved to `198.54.117.242` instead of the correct server IP `140.82.49.221`. This was only discovered at the final verification step — no pre-flight DNS check existed to catch the mismatch earlier.

## Root Cause
The Cloudflare DNS A record for `rugcheck.aethercore.dev` pointed to the wrong IP address. Because DNS validation was not part of the deployment preflight process, the mismatch went undetected until the very last step, wasting time debugging SSL and connectivity issues that were actually caused by incorrect DNS resolution.

## Solution
Added a DNS resolution check to `scripts/preflight-check.sh` that runs before deployment begins. The check resolves the domain and compares the result against the expected server IP from the manifest. When DNS does not match, the script stops and provides the human with specific Cloudflare dashboard instructions to fix the A record. This is an AI Autonomy Level 3 issue — DNS changes in Cloudflare require human action.

## Failed Attempts (avoid these)
- Attempt: Retry HTTPS requests multiple times → Result: DNS issue is persistent, retrying does not help
- Attempt: Debug Cloudflare SSL/TLS settings → Result: Root cause was DNS resolution, not SSL configuration

## Status
- [x] Verified effective
- [x] Graduated to Runbook: ops/runbooks/common/00-preflight.md
