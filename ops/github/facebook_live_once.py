"""One owner-initiated, unscheduled integration test of Facebook to AET Pro.

Not a commercial recurring GitHub scanner. One workflow trigger on a specific
reviewed source commit, one Apify Actor start at most, at most $0.06, and never
plaintext candidate artifacts or token exposure. No private repository access.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from ops.github import standalone_hunter_runner as encrypted

OWNER = "ali00978/aet-hunter-public"
REF = "refs/heads/main"
WORKFLOW = "AET Facebook one-time live integration to AET Pro"
RUN_CAP = 0.06
MONTH_CAP = 4.00


def check_owner(env):
    if (env.get("GITHUB_REPOSITORY") != OWNER or
            env.get("GITHUB_REF") != REF or
            env.get("GITHUB_EVENT_NAME") != "push" or
            env.get("GITHUB_RUN_ATTEMPT") != "1" or
            env.get("GITHUB_WORKFLOW") != WORKFLOW or
            env.get("AET_ONE_TIME_FACEBOOK_TEST") != "I_REQUESTED_ONE_TEST"):
        raise PermissionError("One-time test restricted to the reviewed first push")
    from public_hunter_runtime import require_sync
    require_sync(env)
    if len(env.get("APIFY_TOKEN", "").strip()) < 15:
        raise ValueError("Apify secret not configured")
    if env.get("AET_FACEBOOK_ENABLED") != "true":
        raise PermissionError("Facebook one-off test must be explicitly enabled")
    if env.get("AET_ONE_TIME_FACEBOOK_RETRY") != "I_ACCEPT_ONE_CORRECTED_INPUT_TEST":
        raise PermissionError("Explicit one-time corrected-input exception missing")
    from public_apify_collector import bounded_budget
    test_env = dict(env)
    test_env["AET_APIFY_MONTHLY_BUDGET_USD"] = str(MONTH_CAP)
    test_env["AET_APIFY_MAX_CHARGE_USD"] = str(RUN_CAP)
    monthly, cap = bounded_budget(test_env)
    if monthly != MONTH_CAP or cap != RUN_CAP:
        raise ValueError("One-time scan budget limits changed")
    return test_env


def describe(root):
    """Safe aggregate diagnostics; never include a provider item or raw error."""
    try:
        status = json.loads((Path(root) / "apify_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("Facebook provider: no completed aggregate status")
        return
    if not isinstance(status, dict):
        print("Facebook provider: unexpected status format")
        return
    safe_states = {"ok", "http_error", "request_failed", "empty_response",
                   "no_vehicle_candidates", "provider_cooldown", "preflight_failed",
                   "budget_cap_reached", "disabled"}
    name = status.get("status")
    print("Facebook provider status: " + (name if name in safe_states else "unknown"))
    allowed_reason = (
        "apify_balance_format_unknown", "apify_account_data_unknown",
        "apify_included_credit_unknown", "apify_free_monthly_reserve_exhausted",
        "apify_remaining_included_credit_low", "provider_gate_unknown",
    )
    if status.get("preflight_reason") in allowed_reason:
        print("Facebook safety gate: " + status["preflight_reason"])
    safe_provider_types = {
        "invalid-input", "invalid-input-schema", "invalid-parameter",
        "max-total-charge-usd-below-minimum", "actor-run-failed",
        "actor-is-not-rented", "actor-can-not-be-rented", "actor-disabled",
        "apify-plan-required-to-use-paid-actor",
        "not-enough-usage-to-run-paid-actor", "limit-reached",
        "too-many-requests",
    }
    if status.get("provider_error_type") in safe_provider_types:
        print("Facebook provider error type: " + status["provider_error_type"])
    for name in ("received", "accepted", "new_candidates", "http_status"):
        value = status.get(name)
        if type(value) is int and 0 <= value <= 1000000:
            print("Facebook " + name + ": " + str(value))


def execute(env=None, runtime_runner=None):
    env = check_owner(dict(os.environ if env is None else env))
    ciphertext_path = Path(env.get("AET_ENCRYPTED_STATE_PATH",
                                   encrypted.DEFAULT_ENCRYPTED_FILE))
    if not ciphertext_path.is_absolute():
        ciphertext_path = Path.cwd() / ciphertext_path
    if not ciphertext_path.is_file():
        raise RuntimeError(
            "Private encrypted Hunter cache unavailable; refusing provider spending")
    if ciphertext_path.stat().st_size > encrypted.CACHE_LIMIT:
        raise ValueError("Encrypted Hunter cache larger than approved maximum")
    token = env["AET_HUNTER_SYNC_TOKEN"]
    with tempfile.TemporaryDirectory(prefix="aet-facebook-one-shot-") as tmp:
        root = Path(tmp)
        state = encrypted.unseal(ciphertext_path.read_bytes(), token)
        encrypted.restore_state(root, state)
        runtime_env = dict(env)
        runtime_env["AET_HUNTER_STATE_DIR"] = str(root)
        runtime_env["AET_MODAL_LIVE_ENABLED"] = "I_ACCEPT_LIVE_SCANS"
        if runtime_runner is None:
            from public_hunter_runtime import run_platform
            runtime_runner = run_platform
        persist = lambda: encrypted.encrypted_checkpoint(root, ciphertext_path, token)
        try:
            outcome = runtime_runner("facebook", env=runtime_env,
                                     persist=persist, reload=lambda: None)
        finally:
            persist()
            describe(root)
        if outcome != "synced":
            raise RuntimeError("Facebook scan did not complete an AET Pro import")
        print("One-time Facebook test synced successfully with AET Pro")
        return True


def main():
    try:
        execute()
        return 0
    except Exception as error:
        print("One-time Facebook integration safely stopped (" +
              type(error).__name__ + "). No raw adverts or secrets logged.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
