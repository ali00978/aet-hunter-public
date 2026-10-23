"""Owner-gated standalone PUBLIC Hunter on GitHub Actions.

The original private repository is never read or written. All retained car
records and provider budgets are AES-256-GCM encrypted before GitHub caching.
No car URLs, advert bodies or keys are printed to public Actions logs.

This runner must not be activated unless GitHub has approved the intended
commercial scheduled hosted-runner usage.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

OWNER = "ali00978/aet-hunter-public"
MAIN = "refs/heads/main"
MAGIC = b"AETHUNTER-STATE-1\x00"
AAD = b"ali00978/aet-hunter-public:encrypted-state:v1"
CACHE_LIMIT = 16_000_000
FILE_LIMIT = 4_000_000
STATE_FILES = frozenset((
    "marktplaats_wachtrij.json",
    "marktplaats_handmatig_controleren.json",
    "marktplaats_biedkansen.json",
    "marktplaats_koopkansen.json",
    "marktplaats_proefresultaten.json",
    "marktplaats_vergelijkingen.json",
    "marktplaats_seen.json",
    "marktplaats_last_checked.json",
    "marktplaats_cooldown.json",
    "marktplaats_status.json",
    "hunter_feed.json",
    "hunter_pro_export.json",
    "hunter_pro_combined.json",
    "apify_budget.json",
    "apify_backoff.json",
    "apify_kandidaten.json",
    "apify_seen.json",
    "apify_status.json",
))
DEFAULT_ENCRYPTED_FILE = ".aet-secure-state/state.enc"


def authorize(env):
    """Never confuse repository variables with actual permission from GitHub."""
    if env.get("AET_PUBLIC_HUNTER_ENABLED") != "true":
        raise PermissionError("Public Hunter is disabled by repository settings")
    if env.get("AET_GITHUB_TERMS_APPROVED") != "true":
        raise PermissionError("Production hosting requires explicit GitHub authorization")
    if env.get("GITHUB_REPOSITORY") != OWNER or env.get("GITHUB_REF") != MAIN:
        raise PermissionError("Only the owner public main branch may scan")
    event = env.get("GITHUB_EVENT_NAME")
    if event not in ("schedule", "workflow_dispatch"):
        raise PermissionError("Only the owner schedule or manual dispatch is trusted")
    platform = env.get("AET_HUNTER_PLATFORM")
    if platform not in ("marktplaats", "facebook"):
        raise ValueError("Choose exactly one Hunter platform")
    if platform == "facebook":
        if env.get("AET_FACEBOOK_ENABLED") != "true":
            raise PermissionError("Facebook spending is disabled")
        if not env.get("APIFY_TOKEN", "").strip():
            raise ValueError("APIFY_TOKEN is required for Facebook")
        from public_apify_collector import bounded_budget
        bounded_budget(env)
    from public_hunter_runtime import require_sync
    require_sync(env)
    return platform


def _key(secret):
    """Domain-separated KDF; the sync token must itself be high entropy."""
    if not isinstance(secret, str) or len(secret) < 24:
        raise ValueError("AET_HUNTER_SYNC_TOKEN missing or too short")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=b"AET-HUNTER-V1/GITHUB-CACHE/2026",
        info=b"encrypted-scan-state-is-not-sync-auth",
    ).derive(secret.encode("utf-8"))


def seal(state, token):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(state, dict) or state.get("schema") != "aet.hunter.state.v1":
        raise ValueError("Unexpected state envelope")
    files = state.get("files")
    if not isinstance(files, dict) or set(files) - STATE_FILES:
        raise ValueError("State contains unapproved file names")
    data = json.dumps(state, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    if len(data) > CACHE_LIMIT - 2048:
        raise ValueError("State exceeds bounded encrypted cache size")
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(_key(token)).encrypt(nonce, data, AAD)


def unseal(blob, token):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if (not isinstance(blob, bytes) or len(blob) > CACHE_LIMIT or
        not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 12 + 16):
        raise ValueError("Missing or invalid encrypted cache envelope")
    nonce = blob[len(MAGIC):len(MAGIC) + 12]
    ciphertext = blob[len(MAGIC) + 12:]
    try:
        plaintext = AESGCM(_key(token)).decrypt(nonce, ciphertext, AAD)
        result = json.loads(plaintext)
    except Exception as exc:
        raise ValueError("Cache authentication failed; scan stopped") from exc
    if (not isinstance(result, dict) or
        result.get("schema") != "aet.hunter.state.v1" or
        not isinstance(result.get("files"), dict)):
        raise ValueError("Invalid decrypted state structure")
    if set(result["files"]) - STATE_FILES:
        raise ValueError("Decrypted state contains unexpected names")
    for name, value in result["files"].items():
        if not isinstance(name, str) or not isinstance(value, (dict, list)):
            raise ValueError("Invalid decrypted record structure")
        if len(json.dumps(value, allow_nan=False).encode("utf-8")) > FILE_LIMIT:
            raise ValueError("Decrypted state file size exceeds limit")
    return result


def collect_state(root):
    files = {}
    for name in sorted(STATE_FILES):
        path = Path(root) / name
        if not path.exists():
            continue
        if not path.is_file() or path.stat().st_size > FILE_LIMIT:
            raise ValueError("Unbounded/non-file Hunter state cannot be cached")
        with path.open("r", encoding="utf-8") as inp:
            obj = json.load(inp)
        if not isinstance(obj, (dict, list)):
            raise ValueError("Unexpected Hunter state file format")
        files[name] = obj
    return {"schema": "aet.hunter.state.v1",
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "files": files}


def restore_state(root, saved):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name, data in saved["files"].items():
        if name not in STATE_FILES:
            raise ValueError("Unexpected state path")
        dst = root / name
        if dst.exists():
            raise ValueError("Refusing to overwrite existing private state")
        with dst.open("x", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, allow_nan=False)


def encrypted_checkpoint(root, destination, token):
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = seal(collect_state(root), token)
    staged = dest.with_suffix(".tmp")
    with staged.open("wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(staged, dest)


def execute(env=None, platform_runner=None):
    env = dict(env if env is not None else os.environ)
    platform = authorize(env)
    cache = Path(env.get("AET_ENCRYPTED_STATE_PATH", DEFAULT_ENCRYPTED_FILE))
    if not cache.is_absolute():
        cache = Path.cwd() / cache
    token = env["AET_HUNTER_SYNC_TOKEN"]
    initialize = (env.get("AET_STATE_BOOTSTRAP") == "true" and
                  env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
                  platform == "marktplaats")
    with tempfile.TemporaryDirectory(prefix="aet-hunter-private-") as tmp:
        root = Path(tmp)
        if cache.is_file():
            if cache.stat().st_size > CACHE_LIMIT:
                raise ValueError("Cached ciphertext exceeds maximum size")
            restore_state(root, unseal(cache.read_bytes(), token))
        elif initialize:
            # Explicit one-time owner action only: never silently lose history
            # or reset an existing monthly Apify spending counter on cache miss.
            encrypted_checkpoint(root, cache, token)
        else:
            raise RuntimeError(
                "Encrypted state missing. Scheduled scans stopped; owner must inspect "
                "cache retention and explicitly bootstrap before resuming")
        runtime_env = dict(env)
        runtime_env["AET_HUNTER_STATE_DIR"] = str(root)
        runtime_env["AET_MODAL_LIVE_ENABLED"] = "I_ACCEPT_LIVE_SCANS"
        if platform_runner is None:
            from public_hunter_runtime import run_platform
            platform_runner = run_platform
        try:
            status = platform_runner(
                platform, env=runtime_env,
                persist=lambda: encrypted_checkpoint(root, cache, token),
                reload=lambda: None)
        finally:
            # Even a blocked scanner/provider failure can update a cooldown or
            # provider reservation. Protect that state before exiting the run.
            encrypted_checkpoint(root, cache, token)
        if status != "synced":
            raise RuntimeError("Scan did not complete AET Pro synchronization")
    print(platform + ": completed, private encrypted state prepared for caching")
    return True


def main():
    try:
        execute()
        return 0
    except Exception as exc:
        # Public Action logs MUST NOT display private records, raw URLs,
        # tokens, path contents or untrusted third-party exception messages.
        print("GitHub Hunter run stopped safely (" + type(exc).__name__ +
              "). No private adverts, tokens or stack traces were logged.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
