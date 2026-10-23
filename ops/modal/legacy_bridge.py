"""Run the ORIGINAL private Hunter with a different, authorized executor.

Never call this from public GitHub Actions. Live scans execute only inside the
owner's private Modal runtime, using Modal Secrets for credentials.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = "https://github.com/ali00978/aet-cars-ai-hunter.git"
MP_STATE = (
    "marktplaats_wachtrij.json", "marktplaats_handmatig_controleren.json",
    "marktplaats_biedkansen.json", "marktplaats_koopkansen.json",
    "marktplaats_proefresultaten.json", "marktplaats_vergelijkingen.json",
    "marktplaats_seen.json", "marktplaats_last_checked.json",
    "marktplaats_cooldown.json", "marktplaats_status.json",
    "hunter_feed.json", "hunter_pro_export.json", "hunter_pro_combined.json",
)
FB_STATE = (
    "apify_status.json", "apify_kandidaten.json", "apify_budget.json",
    "apify_seen.json", "hunter_pro_combined.json",
)
MP_TESTS = (
    "test_hunter.py", "test_marktplaats.py", "test_marktplaats_data.py",
    "test_marktplaats_potential.py", "test_hunter_integration.py",
    "test_hunter_feed.py", "test_hunter_pro_export.py", "test_facebook_hunter.py",
    "test_facebook_vehicle_filter.py", "test_hunter_pro_combined.py",
    "test_facebook_tracking.py",
)
NEEDED = ("AET_GITHUB_TOKEN", "AET_PRO_SYNC_URL", "AET_HUNTER_SYNC_TOKEN", "APIFY_TOKEN")
ASKPASS = """#!/bin/sh
case "$1" in
  *Username*) printf 'x-access-token\\n' ;;
  *Password*) printf '%s\\n' "$AET_GITHUB_TOKEN" ;;
  *) exit 1 ;;
esac
"""


def verify_credentials(env: dict[str, str]) -> None:
    missing = [name for name in NEEDED if not env.get(name, "").strip()]
    if missing:
        raise RuntimeError("Missing required Modal Secret names: " + ", ".join(missing))
    if not env["AET_PRO_SYNC_URL"].startswith("https://"):
        raise RuntimeError("AET_PRO_SYNC_URL must be HTTPS")
    if len(env["AET_HUNTER_SYNC_TOKEN"]) < 24:
        raise RuntimeError("AET_HUNTER_SYNC_TOKEN is too short")


def execute(args: list[str], cwd: Path, env: dict[str, str],
            timeout: int = 60, check: bool = True) -> int:
    """Never print a process's stdout/stderr: logs may contain private data."""
    p = subprocess.run(args, cwd=cwd, env=env, timeout=timeout,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and p.returncode:
        raise RuntimeError("Stage failed: " + str(args[0]) + " (exit " +
                           str(p.returncode) + ")")
    return p.returncode


def get_status(root: Path, filename: str) -> dict:
    try:
        data = json.loads((root / filename).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def prepare_clone(scratch: Path, env: dict[str, str]) -> Path:
    verify_credentials(env)
    askpass = scratch / "askpass.sh"
    askpass.write_text(ASKPASS, encoding="utf-8")
    askpass.chmod(0o700)
    work = scratch / "hunter"
    git_env = {**env, "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0",
               "GCM_INTERACTIVE": "never"}
    execute(["git", "-c", "credential.helper=", "clone", "--quiet",
             "--depth", "1", "--branch", "main", REPOSITORY, str(work)],
            scratch, git_env, 100)
    execute(["git", "config", "user.name", "aet-hunter-bot"], work, git_env)
    execute(["git", "config", "user.email", "aet-hunter-bot@users.noreply.github.com"],
            work, git_env)
    return work


def publish_snapshots(root: Path, env: dict[str, str],
                      filenames: tuple[str, ...], message: str) -> None:
    """Replay only saved JSON snapshots onto the newest main, never force-push."""
    if not filenames or any(not f.endswith(".json") or "/" in f for f in filenames):
        raise ValueError("Only explicit snapshot names may be pushed")
    for attempt in range(3):
        execute(["git", "fetch", "origin", "main"], root, env)
        execute(["git", "reset", "--soft", "origin/main"], root, env)
        execute(["git", "restore", "--staged", "."], root, env)
        existing = [f for f in filenames if (root / f).is_file()]
        if not existing:
            raise RuntimeError("No JSON snapshots were produced")
        execute(["git", "add", "--", *existing], root, env)
        if execute(["git", "diff", "--cached", "--quiet"], root, env, check=False) == 0:
            return
        execute(["git", "commit", "-m", message], root, env)
        if execute(["git", "push", "origin", "HEAD:main"], root, env,
                   timeout=90, check=False) == 0:
            return
    raise RuntimeError("Private GitHub snapshot push failed after three attempts")


def scan_marktplaats(root: Path, env: dict[str, str]) -> None:
    execute([sys.executable, "-m", "unittest", "-q", *MP_TESTS],
            root, env, timeout=180)
    try:
        execute([sys.executable, "scanner.py"], root, env, timeout=1100)
    except Exception:
        cooldown = root / "marktplaats_cooldown.json"
        if cooldown.is_file():
            publish_snapshots(root, env, ("marktplaats_cooldown.json",),
                              "Preserve Marktplaats cooldown after failed scan")
        raise
    for script in ("hunter_feed.py", "hunter_pro_export.py", "hunter_pro_combined.py"):
        execute([sys.executable, script], root, env, timeout=120)
    publish_snapshots(root, env, MP_STATE, "Update marketplace scan diagnostics")
    if get_status(root, "marktplaats_status.json").get("scan_health") != "ok":
        raise RuntimeError("Marktplaats scan was not healthy: sync is skipped")
    execute([sys.executable, "hunter_sync.py", "marktplaats", "--send"],
            root, env, timeout=90)
    print("Marktplaats: completed, stored privately, AET Pro sync attempted")


def scan_facebook(root: Path, env: dict[str, str]) -> None:
    execute([sys.executable, "-m", "unittest", "-q", "test_hunter.py"],
            root, env, timeout=120)
    execute([sys.executable, "apify_test.py"], root, env, timeout=170)
    execute([sys.executable, "hunter_pro_combined.py"], root, env, timeout=100)
    publish_snapshots(root, env, FB_STATE, "Store Apify Facebook test results")
    if get_status(root, "apify_status.json").get("status") != "ok":
        raise RuntimeError("Facebook/Apify result was not healthy: sync is skipped")
    execute([sys.executable, "hunter_sync.py", "facebook-apify", "--send"],
            root, env, timeout=90)
    print("Facebook: completed, stored privately, AET Pro sync attempted")


def run_original_hunter(env: dict[str, str] | None = None) -> None:
    """Each platform still runs once per two-hour cycle; no original code edits."""
    env = dict(os.environ if env is None else env)
    if env.get("AET_MODAL_LIVE_ENABLED") != "I_ACCEPT_LIVE_SCANS":
        print("DISABLED: no live requests or private repository access")
        return
    verify_credentials(env)
    with tempfile.TemporaryDirectory(prefix="aet-hunter-private-") as tmp:
        scratch = Path(tmp)
        work = prepare_clone(scratch, env)
        # Created by prepare_clone; never embed credentials in Git URLs.
        running_env = {**env, "GIT_ASKPASS": str(scratch / "askpass.sh"),
                       "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
        errors = []
        for platform, func in (("marktplaats", scan_marktplaats),
                               ("facebook", scan_facebook)):
            try:
                func(work, running_env)
            except Exception as exc:
                errors.append(platform + ": " + type(exc).__name__)
                print(platform + ": ERROR (no secrets or listing data printed)")
        if errors:
            raise RuntimeError("Hunter stage(s) failed: " + ", ".join(errors))
