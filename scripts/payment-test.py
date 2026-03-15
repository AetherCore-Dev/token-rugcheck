#!/usr/bin/env python3
"""
Standardized ag402 payment test.

Usage (on server, as root):
    python3 scripts/payment-test.py --manifest ops/manifest.yaml

Known constraints (do NOT attempt alternatives):
    - ag402 pay CLI: blocks localhost (SSRF protection)
    - ag402 pay CLI: blocks HTTP for remote targets
    - httpx sync Client: ag402 only patches AsyncClient.send
    - Container execution: wallet DB path not writable — run on host, not in container
    - Ledger needs explicit deposit: AgentWallet.deposit() required before first payment
    - Server needs ag402-core[crypto] extras installed
"""
import argparse
import asyncio
import os
import sys
import re


# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

def check_dependency(module_name, install_hint):
    """Check if a Python module is importable; exit with FAIL line if not."""
    try:
        __import__(module_name)
    except ImportError:
        print(f"PAYMENT_TEST:FAIL:Missing dependency '{module_name}'. Install: {install_hint}")
        sys.exit(1)


def check_all_dependencies():
    """Verify all required third-party packages are available."""
    check_dependency("httpx", "pip install httpx")
    check_dependency("ag402_core", "pip install ag402-core[crypto]")


# ---------------------------------------------------------------------------
# Secrets loading (sets env vars from .env.secrets file)
# ---------------------------------------------------------------------------

def load_secrets(path):
    """Load KEY=VALUE lines from secrets file into os.environ.

    Only sets values that are NOT already in the environment and are NOT
    placeholder values (containing '<' or 'placeholder').
    """
    import os
    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" in stripped:
                    key, _, value = stripped.partition("=")
                    key = key.strip()
                    value = value.strip()
                    # Skip placeholder values
                    if "<" in value or "placeholder" in value.lower():
                        continue
                    # Don't overwrite existing env vars
                    if key not in os.environ:
                        os.environ[key] = value
        # Map BUYER_PRIVATE_KEY to SOLANA_PRIVATE_KEY if ag402 needs it
        if "BUYER_PRIVATE_KEY" in os.environ and "SOLANA_PRIVATE_KEY" not in os.environ:
            os.environ["SOLANA_PRIVATE_KEY"] = os.environ["BUYER_PRIVATE_KEY"]
    except FileNotFoundError:
        print(f"PAYMENT_TEST:FAIL:Secrets file not found: {path}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Manifest parsing (no PyYAML — stdlib only)
# ---------------------------------------------------------------------------

def parse_manifest(path):
    """Parse flat YAML manifest — no PyYAML dependency needed.

    Handles:
      - Top-level and one-level-nested key: value pairs
      - List values (lines starting with ``- ``)
      - Comments and blank lines
    """
    config = {}
    current_section = ""
    list_key = ""
    list_values = []

    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                # Save any pending list
                if list_key:
                    config[list_key] = list_values
                    list_key = ""
                    list_values = []
                continue

            # List item
            if stripped.startswith("- ") and list_key:
                list_values.append(stripped[2:].strip())
                continue

            # Save pending list from previous key
            if list_key and not stripped.startswith("- "):
                config[list_key] = list_values
                list_key = ""
                list_values = []

            # Section header (no colon-value, just "key:")
            match = re.match(r"^(\w[\w_]*):\s*$", stripped)
            if match:
                current_section = match.group(1)
                continue

            # Key-value pair
            match = re.match(r"^(\w[\w_]*):\s+(.+)$", stripped)
            if match:
                key = match.group(1)
                value = match.group(2).strip().strip('"').strip("'")
                full_key = f"{current_section}.{key}" if current_section else key

                if not value or value == "":
                    list_key = full_key
                    list_values = []
                else:
                    config[full_key] = value
                continue

            # Key with empty value on same line → upcoming list
            match = re.match(r"^(\w[\w_]*):\s*$", stripped)
            if match:
                key = match.group(1)
                full_key = f"{current_section}.{key}" if current_section else key
                list_key = full_key
                list_values = []

    # Save final list if any
    if list_key:
        config[list_key] = list_values

    return config


# ---------------------------------------------------------------------------
# Field traversal helper
# ---------------------------------------------------------------------------

def check_field(data, field_path):
    """Check that a dot-separated field path exists in *data*.

    For example ``"action.risk_score"`` checks ``data["action"]["risk_score"]``.
    Returns (True, value) or (False, reason).
    """
    parts = field_path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, f"field '{field_path}' missing at '{part}'"
    return True, current


# ---------------------------------------------------------------------------
# Payment test
# ---------------------------------------------------------------------------

async def run_payment_test(config, paid_mode=False):
    """Execute the ag402 payment test and return (success, detail)."""
    import os
    import httpx  # noqa: E402 — deferred so --help works without deps

    # 0. Enable ag402 monkey-patching only in paid mode
    if paid_mode:
        os.environ.setdefault("X402_MODE", "production")
        os.environ.setdefault("X402_NETWORK", config.get("blockchain.network", "mainnet"))
        import ag402_core
        ag402_core.enable()

    # 1. Initialize wallet if needed (only for paid mode)
    if paid_mode:
        from ag402_core.wallet.agent_wallet import AgentWallet  # noqa: delayed import after dep check

        wallet = AgentWallet()
        await wallet.init_db()
        balance = await wallet.get_balance()
        if balance <= 0:
            deposit_amount = float(config.get("service.test_deposit_amount", "0.10"))
            await wallet.deposit(deposit_amount)
            balance = await wallet.get_balance()
            if balance <= 0:
                return False, "Wallet deposit failed — balance still $0"

    # 2. Build request URL (gateway on port 80)
    endpoint = config.get("service.test_endpoint")
    if not endpoint:
        return False, "Manifest missing 'service.test_endpoint'"
    url = f"http://localhost:80{endpoint}"

    # 3. Send request through ag402 async client
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0)
    except httpx.ConnectError as exc:
        return False, f"Connection refused — is the gateway running? ({exc})"
    except httpx.TimeoutException:
        return False, f"Request to {url} timed out after 30s"

    # 4. Validate status code
    expected_status = int(config.get("service.test_expect_status", "200"))
    if response.status_code != expected_status:
        return False, f"Expected status {expected_status}, got {response.status_code}"

    # 5. Parse response JSON
    try:
        data = response.json()
    except Exception:
        return False, "Response is not valid JSON"

    # 6. Check required fields
    expected_fields = config.get("service.test_expect_fields", [])
    if isinstance(expected_fields, str):
        expected_fields = [expected_fields]

    for field in expected_fields:
        ok, info = check_field(data, field)
        if not ok:
            return False, f"Response validation failed: {info}"

    return True, f"status={response.status_code}, fields={len(expected_fields)} OK"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Standardized ag402 payment test for Token RugCheck MCP.",
        epilog=(
            "This script runs ON THE SERVER (not locally) and validates the "
            "ag402 payment flow end-to-end."
        ),
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to ops/manifest.yaml",
    )
    parser.add_argument(
        "--paid",
        action="store_true",
        default=False,
        help="Paid mode: expect 200 (after ag402 auto-payment) instead of manifest's test_expect_status",
    )
    parser.add_argument(
        "--secrets",
        default=None,
        help="Path to .env.secrets file (sources BUYER_PRIVATE_KEY, SOLANA_RPC_URL)",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Check dependencies after argparse so --help works without deps
    check_all_dependencies()

    # Load secrets if provided (sets env vars before ag402 reads them)
    # Load project .env FIRST (has real SOLANA_RPC_URL), then secrets (adds BUYER key)
    project_env = os.path.join(os.path.dirname(args.manifest), "..", ".env")
    if os.path.exists(project_env):
        load_secrets(project_env)
    if args.secrets:
        load_secrets(args.secrets)

    # Parse manifest
    try:
        config = parse_manifest(args.manifest)
    except FileNotFoundError:
        print(f"PAYMENT_TEST:FAIL:Manifest not found: {args.manifest}")
        sys.exit(1)
    except Exception as exc:
        print(f"PAYMENT_TEST:FAIL:Manifest parse error: {exc}")
        sys.exit(1)

    # Override expected status for paid mode
    if args.paid:
        config["service.test_expect_status"] = "200"
        config["service.test_expect_fields"] = ["action.risk_score", "action.is_safe", "metadata.data_sources"]

    # Run async test
    try:
        success, detail = asyncio.run(run_payment_test(config, paid_mode=args.paid))
    except Exception as exc:
        print(f"PAYMENT_TEST:FAIL:Unexpected error: {exc}")
        sys.exit(1)

    if success:
        print(f"PAYMENT_TEST:PASS:{detail}")
        sys.exit(0)
    else:
        print(f"PAYMENT_TEST:FAIL:{detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
