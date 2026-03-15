#!/usr/bin/env python3
"""
Upstream dependency version checker for ag402 packages.

Compares pinned versions in pyproject.toml against PyPI latest,
and optionally checks the server's installed versions via SSH.

Usage:
    python3 scripts/monitor-deps.py                              # pinned vs PyPI
    python3 scripts/monitor-deps.py --manifest ops/manifest.yaml  # also check server
    python3 scripts/monitor-deps.py --help
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

# Packages to monitor
MONITORED_PACKAGES = ["ag402-core", "ag402-mcp"]

PYPROJECT_PATH = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")


# ---------------------------------------------------------------------------
# Manifest parsing (no PyYAML — stdlib only, same approach as payment-test.py)
# ---------------------------------------------------------------------------

def parse_manifest(path):
    """Parse flat YAML manifest — no PyYAML dependency needed."""
    config = {}
    current_section = ""
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            m = re.match(r'^(\w[\w_]*):\s*$', stripped)
            if m:
                current_section = m.group(1)
                continue
            m = re.match(r'^(\w[\w_]*):\s+(.+)$', stripped)
            if m:
                key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
                full_key = f"{current_section}.{key}" if current_section else key
                config[full_key] = val
    return config


# ---------------------------------------------------------------------------
# pyproject.toml parsing
# ---------------------------------------------------------------------------

def parse_pinned_versions(pyproject_path):
    """Extract pinned versions from pyproject.toml dependency lines.

    Parses lines like:  "ag402-core>=0.1.17",
    Returns dict: {"ag402-core": "0.1.17", ...}
    """
    versions = {}
    try:
        with open(pyproject_path) as f:
            content = f.read()
    except FileNotFoundError:
        print(f"WARN: pyproject.toml not found at {pyproject_path}")
        return versions

    for pkg in MONITORED_PACKAGES:
        # Match patterns like "ag402-core>=0.1.17" or "ag402-core==0.1.17"
        pattern = re.escape(pkg) + r'[>=<~!]+([0-9][0-9a-zA-Z.*_-]*)'
        m = re.search(pattern, content)
        if m:
            versions[pkg] = m.group(1)
    return versions


# ---------------------------------------------------------------------------
# PyPI latest version check
# ---------------------------------------------------------------------------

def fetch_pypi_version(package):
    """Fetch the latest version of a package from PyPI JSON API.

    Returns the version string, or None on failure.
    """
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data["info"]["version"]
    except Exception as exc:
        print(f"WARN: PyPI unreachable for {package}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Server installed version check (via SSH)
# ---------------------------------------------------------------------------

def fetch_server_version(package, ssh_user, server_ip):
    """Check installed version of a package on the server via SSH.

    Returns the version string, or None on failure.
    """
    cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
           f"{ssh_user}@{server_ip}", f"pip show {package} | grep Version"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            # Output like: "Version: 0.1.17"
            m = re.search(r'Version:\s*(\S+)', result.stdout)
            if m:
                return m.group(1)
        return None
    except Exception as exc:
        print(f"WARN: SSH check failed for {package}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Action determination
# ---------------------------------------------------------------------------

def determine_action(pinned, latest, server):
    """Determine the action status based on version comparisons."""
    if latest and latest != pinned:
        return "upgrade-available"
    if server and server != pinned:
        return "server-drift"
    return "up-to-date"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Check ag402 dependency versions: pinned vs PyPI (and optionally server).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/monitor-deps.py\n"
            "  python3 scripts/monitor-deps.py --manifest ops/manifest.yaml\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--manifest",
        metavar="PATH",
        help="Path to ops/manifest.yaml — enables server version check via SSH",
    )
    parser.add_argument(
        "--pyproject",
        metavar="PATH",
        default=PYPROJECT_PATH,
        help="Path to pyproject.toml (default: auto-detected relative to script)",
    )
    args = parser.parse_args()

    # 1. Parse pinned versions
    pinned = parse_pinned_versions(args.pyproject)
    if not pinned:
        print("WARN: No monitored packages found in pyproject.toml")

    # 2. Fetch latest from PyPI
    latest = {}
    for pkg in MONITORED_PACKAGES:
        latest[pkg] = fetch_pypi_version(pkg)

    # 3. Optionally fetch server versions
    server = {}
    if args.manifest:
        manifest = parse_manifest(args.manifest)
        server_ip = manifest.get("server.ip")
        ssh_user = manifest.get("server.ssh_user", "root")
        if server_ip:
            for pkg in MONITORED_PACKAGES:
                server[pkg] = fetch_server_version(pkg, ssh_user, server_ip)
        else:
            print("WARN: No server.ip found in manifest — skipping server check")

    # 4. Output structured lines
    for pkg in MONITORED_PACKAGES:
        p = pinned.get(pkg, "unknown")
        l = latest.get(pkg, "unknown")
        s = server.get(pkg, "n/a") if args.manifest else "n/a"
        # Normalize None to "unknown"/"n/a"
        if l is None:
            l = "unknown"
        if s is None:
            s = "unknown"
        action = determine_action(p, l if l != "unknown" else None, s if s not in ("n/a", "unknown") else None)
        print(f"DEP:{pkg}:pinned={p}:latest={l}:server={s}:ACTION={action}")


if __name__ == "__main__":
    main()
