"""Fail if files beyond the reviewed, public, offline development set are tracked."""
from pathlib import Path
import re
import subprocess

ALLOW = {
    ".gitignore", "README.md", "public_guard.py", "run_offline_tests.py",
    ".github/workflows/offline-tests.yml",
    "scanner.py", "car_triage.py", "seller_filter.py",
    "marktplaats_data.py", "marktplaats_market.py",
    "marktplaats_potential.py",
    "test_hunter.py", "test_marktplaats.py", "test_marktplaats_potential.py",
    "facebook_vehicle_filter.py", "facebook_hunter.py",
    "test_facebook_vehicle_filter.py", "test_facebook_hunter.py",
    "ops/modal/legacy_bridge.py", "ops/modal/hunter_modal.py",
    "test_modal_bridge.py", ".github/workflows/deploy-modal.yml",
    "MODAL_HUNTER_SETUP.md",
    "PUBLIC_GITHUB_HUNTER_SETUP.md",
    ".github/workflows/public-original-hunter.yml",
    "ops/github/public_actions_runner.py", "test_public_actions.py",
    "ops/github/connection_preflight.py", "test_connection_preflight.py",
    ".github/workflows/manual-connection-preflight.yml",
}

tracked = set(filter(None, subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\x00")))
unexpected = sorted(tracked - ALLOW)
missing = sorted(ALLOW - tracked)
if unexpected or missing:
    raise SystemExit(f"Unexpected tracked public files: {unexpected}; missing: {missing}")

workflow = Path(".github/workflows/offline-tests.yml").read_text(encoding="utf-8")
if re.search(r"(?m)^\s*schedule\s*:", workflow):
    raise SystemExit("Public CI must not include a scheduled scraping workflow")
if re.search(r"(?i)(?:\bscanner\.py\b|\bapify_test\.py\b|\bhunter_sync\.py\b)", workflow):
    raise SystemExit("Public CI must only run network-blocked offline tests")

for filename in tracked:
    if not filename.endswith(".py"):
        continue
    text = Path(filename).read_text(encoding="utf-8")
    checks = [
        r"-----BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY-----",
        r"\bgh[pousr]_[A-Za-z0-9]{24,}\b",
        r"\bgithub_pat_[A-Za-z0-9_]{24,}\b",
        r"\bapify_api_[A-Za-z0-9]{16,}\b",
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b",
        r"(?i)\b(?:password|client_secret|api_key|access_token)\s*=\s*['\"][^'\"]{16,}['\"]",
    ]
    if filename == "public_guard.py":
        continue  # The guard contains its own detection patterns as literal examples.
    for pattern in checks:
        if re.search(pattern, text):
            raise SystemExit(f"Potential credential found in {filename}")
owner_workflow = Path(".github/workflows/public-original-hunter.yml").read_text(encoding="utf-8")
if ("vars.AET_PUBLIC_HUNTER_ENABLED == 'true'" not in owner_workflow
        or "vars.AET_GITHUB_TERMS_APPROVED == 'true'" not in owner_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in owner_workflow
        or "runs-on: ubuntu-latest" not in owner_workflow
        or "secrets.AET_GITHUB_TOKEN" not in owner_workflow
        or "secrets.AET_HUNTER_SYNC_TOKEN" not in owner_workflow
        or "secrets.APIFY_TOKEN" not in owner_workflow
        or "persist-credentials: false" not in owner_workflow
        or "  pull_request:" in owner_workflow):
    raise SystemExit("Public scheduled runner must stay owner-gated and secret-protected")
print("Public development tree passes allowlist, secrets and owner-gate checks")
