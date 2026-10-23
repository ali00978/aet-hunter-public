"""Validate private Hunter code changes in isolated OFFLINE tests.

This is a software-development test. It never runs a marketplace scan,
starts an Apify Actor, commits private files, or posts to AET Pro.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

OWNER = "ali00978/aet-hunter-public"
PRIVATE_REMOTE = "https://github.com/ali00978/aet-cars-ai-hunter.git"
BRANCH = "fix/private-schedules-and-sync-guardrails"

# Must remain read-only: tests exercise every changed workflow's Python code.
TEST_NAMES = (
    "test_hunter", "test_marktplaats", "test_marktplaats_data",
    "test_marktplaats_potential", "test_hunter_integration",
    "test_hunter_feed", "test_hunter_pro_export",
    "test_facebook_hunter", "test_facebook_vehicle_filter",
    "test_hunter_pro_combined", "test_facebook_tracking",
    "test_hunter_sync", "test_apify_budget_guard",
)

OFFLINE_TEST_RUNNER = """
import io, socket, sys, unittest

def no_network(*_args, **_kwargs):
    raise AssertionError("Offline private test tried to contact the network")

socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network

tests = unittest.TestLoader().loadTestsFromNames(<<<TEST_NAMES>>>)
buf = io.StringIO()
result = unittest.TextTestRunner(stream=buf, verbosity=0).run(tests)
print("Private OFFLINE tests:", result.testsRun,
      "failures:", len(result.failures),
      "errors:", len(result.errors),
      "skipped:", len(result.skipped))
sys.exit(0 if result.wasSuccessful() and result.testsRun >= 50 else 1)
""".replace("<<<TEST_NAMES>>>", repr(TEST_NAMES))

ASKPASS = """#!/bin/sh
case "$1" in
  *Username*) printf 'x-access-token\\n' ;;
  *Password*) printf '%s\\n' "$AET_GITHUB_TOKEN" ;;
  *) exit 1 ;;
esac
"""


def main() -> int:
    if os.environ.get("GITHUB_REPOSITORY") != OWNER or os.environ.get("GITHUB_REF") != "refs/heads/main":
        print("REFUSED: this development check is owner/main only")
        return 1
    token = os.environ.get("AET_GITHUB_TOKEN", "").strip()
    if not token:
        print("FAILED: private repository read-only access is unavailable")
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="aet-offline-pr-check-") as tmp:
            root = Path(tmp)
            askpass = root / "credential-helper.sh"
            askpass.write_text(ASKPASS, encoding="utf-8")
            askpass.chmod(0o700)
            clone = root / "private-worktree"
            git_env = {
                **os.environ, "GIT_ASKPASS": str(askpass),
                "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
            }
            for name in ("APIFY_TOKEN", "AET_PRO_SYNC_URL", "AET_HUNTER_SYNC_TOKEN"):
                git_env.pop(name, None)
            git = subprocess.run(
                ["git", "-c", "credential.helper=", "clone", "--quiet",
                 "--depth", "1", "--single-branch", "--branch", BRANCH,
                 PRIVATE_REMOTE, str(clone)],
                cwd=root, env=git_env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=80, check=False,
            )
            if git.returncode:
                print("FAILED: private branch could not be cloned in read-only mode")
                return 1

            # No authentication or cloud credentials are inherited by tests.
            test_env = dict(os.environ)
            for name in ("AET_GITHUB_TOKEN", "GIT_ASKPASS", "APIFY_TOKEN",
                         "AET_PRO_SYNC_URL", "AET_HUNTER_SYNC_TOKEN"):
                test_env.pop(name, None)
            test_env["GITHUB_EVENT_NAME"] = "offline_validation"
            completed = subprocess.run(
                [sys.executable, "-c", OFFLINE_TEST_RUNNER],
                cwd=clone, env=test_env, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, timeout=200,
                check=False,
            )
            # The runner only emits aggregate counts; no private test output.
            line = completed.stdout.strip()
            if not re.fullmatch(
                r"Private OFFLINE tests: [0-9]+ failures: [0-9]+ errors: [0-9]+ skipped: [0-9]+",
                line,
            ):
                print("FAILED: unexpected private test execution result (details suppressed)")
                return 1
            print(line)
            print("No live search, provider run, private writes, or AET Pro POST.")
            return 0 if completed.returncode == 0 else 1
    except (OSError, subprocess.TimeoutExpired):
        print("FAILED: private offline checks could not finish safely")
        return 1


if __name__ == "__main__":
    sys.exit(main())
