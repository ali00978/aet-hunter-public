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
    "ops/github/readonly_connection_check.py", "test_readonly_connection_check.py",
    ".github/workflows/verify-github-apify-readonly.yml",
    "ops/github/aet_pro_handshake.py", "test_aet_pro_handshake.py",
    ".github/workflows/verify-aet-pro-handshake.yml",
    "ops/github/apify_actor_readiness.py", "test_apify_actor_readiness.py",
    ".github/workflows/verify-apify-actor-readiness.yml",
    "ops/github/private_snapshot_preflight.py", "test_private_snapshot_preflight.py",
    ".github/workflows/verify-private-snapshot-readonly.yml",
    "ops/github/live_marktplaats_smoke.py", "test_live_marktplaats_smoke.py",
    ".github/workflows/live-search-smoke.yml",
    "hunter_feed.py", "hunter_pro_export.py", "hunter_pro_combined.py",
    "hunter_sync.py", "public_apify_collector.py", "public_hunter_runtime.py",
    "test_public_hunter_runtime.py", "ops/modal/public_hunter_modal.py",
    "PUBLIC_STANDALONE_SETUP.md",
    "ops/github/standalone_hunter_runner.py", "test_github_standalone.py",
    "ops/github/facebook_live_once.py", "test_facebook_live_once.py",
    "ops/github/facebook_recover_existing.py", "test_facebook_recover_existing.py",
    "ops/github/facebook_quality_replay.py", "test_facebook_quality_replay.py",
    ".github/workflows/facebook-quality-replay.yml",
    ".github/workflows/facebook-recover-existing.yml",
    ".github/workflows/facebook-one-time-integration.yml",
    ".github/workflows/marktplaats-one-time-integration.yml",
    "ops/github/public_marktplaats_integration.py",
    "ops/github/private_pr_offline_validation.py",
    "test_private_pr_offline_validation.py",
    ".github/workflows/verify-private-pr-offline.yml",
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
        or "  push:" in owner_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in owner_workflow
        or "runs-on: ubuntu-latest" not in owner_workflow
        or "cron: '17 */2 * * *'" not in owner_workflow
        or "cron: '47 2,14 * * *'" not in owner_workflow
        or "secrets.AET_HUNTER_SYNC_TOKEN" not in owner_workflow
        or "secrets.AET_PRO_SYNC_URL" not in owner_workflow
        or "secrets.APIFY_TOKEN" not in owner_workflow
        or "AET_HUNTER_PLATFORM: marktplaats" not in owner_workflow
        or "AET_HUNTER_PLATFORM: facebook" not in owner_workflow
        or "inputs.platform == 'marktplaats'" not in owner_workflow
        or "inputs.platform == 'facebook'" not in owner_workflow
        or "actions/cache/restore@v4" not in owner_workflow
        or "actions/cache/save@v4" not in owner_workflow
        or "python -m ops.github.standalone_hunter_runner" not in owner_workflow
        or "persist-credentials: false" not in owner_workflow
        or "  pull_request:" in owner_workflow
        or "secrets.AET_GITHUB_TOKEN" in owner_workflow
        or "public_actions_runner" in owner_workflow
        or "legacy_bridge" in owner_workflow
        or "git clone" in owner_workflow
        or "upload-artifact" in owner_workflow):
    raise SystemExit("Public GitHub production must remain owner-only, terms-gated, encrypted and private-data-safe")
if ("secrets.AET_GITHUB_TOKEN" not in owner_workflow and "AET_STATE_BOOTSTRAP:" in owner_workflow
        and "vars.AET_GITHUB_TERMS_APPROVED" in owner_workflow):
    pass
else:
    raise SystemExit("Public Hunter requires confirmed terms approval and manual encrypted-state bootstrap")

read_only_workflow = Path(".github/workflows/verify-github-apify-readonly.yml").read_text(encoding="utf-8")
if ("  workflow_dispatch:" not in read_only_workflow
        or "  schedule:" in read_only_workflow
        or "github.ref == 'refs/heads/main'" not in read_only_workflow
        or "persist-credentials: false" not in read_only_workflow
        or "ops/github/readonly_connection_check.py" not in read_only_workflow
        or "secrets.AET_GITHUB_TOKEN" not in read_only_workflow
        or "secrets.APIFY_TOKEN" not in read_only_workflow
        or "secrets.AET_HUNTER_SYNC_TOKEN" in read_only_workflow):
    raise SystemExit("Manual API probe must remain read-only and owner-gated")
aet_pro_handshake_workflow = Path(".github/workflows/verify-aet-pro-handshake.yml").read_text(encoding="utf-8")
if ("  workflow_dispatch:" not in aet_pro_handshake_workflow
        or "  schedule:" in aet_pro_handshake_workflow
        or "  pull_request:" in aet_pro_handshake_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in aet_pro_handshake_workflow
        or "github.ref == 'refs/heads/main'" not in aet_pro_handshake_workflow
        or "persist-credentials: false" not in aet_pro_handshake_workflow
        or "ops/github/aet_pro_handshake.py" not in aet_pro_handshake_workflow
        or "secrets.AET_PRO_SYNC_URL" not in aet_pro_handshake_workflow
        or "secrets.AET_HUNTER_SYNC_TOKEN" not in aet_pro_handshake_workflow
        or "secrets.AET_GITHUB_TOKEN" in aet_pro_handshake_workflow
        or "secrets.APIFY_TOKEN" in aet_pro_handshake_workflow):
    raise SystemExit("Manual AET Pro handshake must be owner-only and contain no live scanner actions")
actor_workflow = Path(".github/workflows/verify-apify-actor-readiness.yml").read_text(encoding="utf-8")
if ("  workflow_dispatch:" not in actor_workflow
        or "  schedule:" in actor_workflow
        or "  pull_request:" in actor_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in actor_workflow
        or "github.ref == 'refs/heads/main'" not in actor_workflow
        or "persist-credentials: false" not in actor_workflow
        or "ops/github/apify_actor_readiness.py" not in actor_workflow
        or "secrets.APIFY_TOKEN" not in actor_workflow
        or "secrets.AET_HUNTER_SYNC_TOKEN" in actor_workflow
        or "secrets.AET_GITHUB_TOKEN" in actor_workflow):
    raise SystemExit("Apify readiness workflow must run only manually and without real scans")
state_workflow = Path(".github/workflows/verify-private-snapshot-readonly.yml").read_text(encoding="utf-8")
if ("  workflow_dispatch:" not in state_workflow
        or "  schedule:" in state_workflow
        or "  pull_request:" in state_workflow
        or "  push:" in state_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in state_workflow
        or "github.ref == 'refs/heads/main'" not in state_workflow
        or "persist-credentials: false" not in state_workflow
        or "secrets.AET_GITHUB_TOKEN" not in state_workflow
        or "ops/github/private_snapshot_preflight.py" not in state_workflow
        or "secrets.APIFY_TOKEN" in state_workflow
        or "secrets.AET_HUNTER_SYNC_TOKEN" in state_workflow):
    raise SystemExit("Private snapshot check must be owner/main manual and read-only")
live_smoke_workflow = Path(".github/workflows/live-search-smoke.yml").read_text(encoding="utf-8")
if ("  workflow_dispatch:" not in live_smoke_workflow
        or "  schedule:" in live_smoke_workflow
        or "  pull_request:" in live_smoke_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in live_smoke_workflow
        or "github.ref == 'refs/heads/main'" not in live_smoke_workflow
        or "persist-credentials: false" not in live_smoke_workflow
        or "python -m ops.github.live_marktplaats_smoke" not in live_smoke_workflow
        or "secrets." in live_smoke_workflow):
    raise SystemExit("One-request live search smoke must never hold secrets or be scheduled")
private_pr_workflow = Path(".github/workflows/verify-private-pr-offline.yml").read_text(encoding="utf-8")
if ("  workflow_dispatch:" not in private_pr_workflow
        or "  schedule:" in private_pr_workflow
        or "  push:" in private_pr_workflow
        or "  pull_request:" in private_pr_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in private_pr_workflow
        or "github.ref == 'refs/heads/main'" not in private_pr_workflow
        or "persist-credentials: false" not in private_pr_workflow
        or "secrets.AET_GITHUB_TOKEN" not in private_pr_workflow
        or "ops/github/private_pr_offline_validation.py" not in private_pr_workflow
        or "secrets.APIFY_TOKEN" in private_pr_workflow
        or "secrets.AET_HUNTER_SYNC_TOKEN" in private_pr_workflow):
    raise SystemExit("Private PR validation must be owner/main manual and never run paid providers")
fb_quality_workflow = Path(".github/workflows/facebook-quality-replay.yml").read_text(encoding="utf-8")
if (not all(word in fb_quality_workflow for word in (
        "github.repository == 'ali00978/aet-hunter-public'",
        "github.ref == 'refs/heads/main'",
        "github.event.head_commit.message == 'test: one saved Facebook batch quality replay to AET Pro'",
        "python public_guard.py", "python run_offline_tests.py",
        "python -m ops.github.facebook_quality_replay",
        "secrets.AET_PRO_SYNC_URL", "secrets.AET_HUNTER_SYNC_TOKEN",
        "actions/cache/restore@v4", "actions/cache/save@v4",
        "aet-public-original-hunter", "persist-credentials: false"))
        or "  schedule:" in fb_quality_workflow
        or "  workflow_dispatch:" in fb_quality_workflow
        or "  pull_request:" in fb_quality_workflow
        or "secrets.APIFY_TOKEN" in fb_quality_workflow
        or "secrets.AET_GITHUB_TOKEN" in fb_quality_workflow):
    raise SystemExit("Quality replay must be a single offline-to-AET Pro integration check")

read_only_recover_workflow = Path(".github/workflows/facebook-recover-existing.yml").read_text(encoding="utf-8")
if (not all(phrase in read_only_recover_workflow for phrase in (
        "github.repository == 'ali00978/aet-hunter-public'",
        "github.ref == 'refs/heads/main'",
        "github.event.head_commit.message == 'test: recover one already-started Apify run without a new scan'",
        "python -m ops.github.facebook_recover_existing",
        "python run_offline_tests.py",
        "secrets.APIFY_TOKEN", "secrets.AET_PRO_SYNC_URL",
        "secrets.AET_HUNTER_SYNC_TOKEN",
        "actions/cache/restore@v4", "actions/cache/save@v4",
        "aet-public-original-hunter", "persist-credentials: false"))
        or "  schedule:" in read_only_recover_workflow
        or "  pull_request:" in read_only_recover_workflow
        or "  workflow_dispatch:" in read_only_recover_workflow
        or "secrets.AET_GITHUB_TOKEN" in read_only_recover_workflow):
    raise SystemExit("Recover must run only once, read existing Apify data, and keep private state encrypted")

facebook_once_workflow = Path(".github/workflows/facebook-one-time-integration.yml").read_text(encoding="utf-8")
if (not all(word in facebook_once_workflow for word in (
        "github.repository == 'ali00978/aet-hunter-public'",
        "github.ref == 'refs/heads/main'",
        "github.event.head_commit.message == 'test: one-time Facebook Apify integration to AET Pro'",
        "python run_offline_tests.py", "python public_guard.py",
        "python -m ops.github.facebook_live_once",
        "secrets.APIFY_TOKEN", "secrets.AET_PRO_SYNC_URL",
        "secrets.AET_HUNTER_SYNC_TOKEN", "actions/cache/restore@v4",
        "actions/cache/save@v4", "persist-credentials: false",
        "aet-public-original-hunter"))
        or "  schedule:" in facebook_once_workflow
        or "  pull_request:" in facebook_once_workflow
        or "  workflow_dispatch:" in facebook_once_workflow
        or "AET_GITHUB_TERMS_APPROVED" in facebook_once_workflow
        or "secrets.AET_GITHUB_TOKEN" in facebook_once_workflow):
    raise SystemExit("One-shot Apify Facebook test must remain owner-only, single-triggered, encrypted, capped and never scheduled")

one_time_workflow = Path(".github/workflows/marktplaats-one-time-integration.yml").read_text(encoding="utf-8")
if ("  workflow_dispatch:" not in one_time_workflow
        or "  schedule:" in one_time_workflow
        or "  pull_request:" in one_time_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in one_time_workflow
        or "github.ref == 'refs/heads/main'" not in one_time_workflow
        or "persist-credentials: false" not in one_time_workflow
        or "python -m ops.github.public_marktplaats_integration" not in one_time_workflow
        or "secrets." in one_time_workflow
        or "actions/upload-artifact" in one_time_workflow):
    raise SystemExit("One-time Marktplaats integration must stay owner-only, unscheduled, and secret-free")

modal_workflow = Path(".github/workflows/deploy-modal.yml").read_text(encoding="utf-8")
if ("modal deploy ops/modal/public_hunter_modal.py" not in modal_workflow
        or "  schedule:" in modal_workflow
        or "python -m unittest -v test_public_hunter_runtime.py" not in modal_workflow
        or "persist-credentials: false" not in modal_workflow
        or "github.repository == 'ali00978/aet-hunter-public'" not in modal_workflow):
    raise SystemExit("Modal deployment must deploy standalone public code only")

print("Public development tree passes allowlist, secrets and owner-gate checks")
