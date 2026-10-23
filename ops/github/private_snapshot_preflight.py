"""Read-only verification of the ORIGINAL private Hunter state and token role.

Manually triggered from the public owner repository. Fetches only four known
JSON snapshots and GitHub repository metadata, using a private fine-grained
token. No listings, URLs, tokens, or private response bodies reach workflow logs.
Never pushes, scans, syncs, edits, or triggers provider Actors.
"""
from __future__ import annotations

import base64
import binascii
from collections import Counter
from datetime import datetime, timezone
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PUBLIC_REPO = "ali00978/aet-hunter-public"
PRIVATE_REPO = "ali00978/aet-cars-ai-hunter"
FILES = ("hunter_pro_combined.json", "marktplaats_status.json",
         "apify_status.json", "apify_budget.json")
HOST = "https://api.github.com/repos/" + PRIVATE_REPO
MAX_RESPONSE = 400000
MAX_CONTENT = 180000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(url: str, token: str, opener) -> dict:
    allowed = {HOST} | {HOST + "/contents/" + filename + "?ref=main" for filename in FILES}
    if url not in allowed:
        raise ValueError("Unknown private GitHub read-only endpoint")
    req = Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "AET-Hunter-State-Preflight/1",
    }, method="GET")
    with opener.open(req, timeout=15) as response:
        if response.status != 200:
            raise ValueError("unexpected HTTP status")
        payload = response.read(MAX_RESPONSE + 1)
        if len(payload) > MAX_RESPONSE:
            raise ValueError("oversized GitHub API response")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("GitHub JSON was not an object")
        return data


def decode_snapshot(response: dict) -> object:
    if response.get("type") != "file" or response.get("encoding") != "base64":
        raise ValueError("Invalid private JSON file metadata")
    raw = response.get("content")
    if not isinstance(raw, str):
        raise ValueError("Private file content missing")
    try:
        payload = base64.b64decode(raw, validate=False)
        if len(payload) > MAX_CONTENT:
            raise ValueError("Private file exceeds safe size")
        return json.loads(payload.decode("utf-8"))
    except (binascii.Error, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Private file is not valid UTF-8 JSON") from exc


def _when(value) -> datetime | None:
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def validate(snapshots: dict, repo_metadata: dict, now: datetime) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    combined = snapshots.get("hunter_pro_combined.json")
    mp = snapshots.get("marktplaats_status.json")
    fb = snapshots.get("apify_status.json")
    budget = snapshots.get("apify_budget.json")
    if not all(isinstance(item, dict) for item in (combined, mp, fb, budget)):
        return False, ["FAILED: missing or invalid private snapshot objects"]
    if combined.get("schema") != "aet-pro.hunter.v2":
        return False, ["FAILED: unsupported AET Pro batch schema"]
    items = combined.get("candidates")
    if not isinstance(items, list) or len(items) > 100:
        return False, ["FAILED: combined candidates structure/count invalid"]
    counted = Counter()
    ids = set()
    for item in items:
        if not isinstance(item, dict):
            return False, ["FAILED: malformed private candidate entry"]
        source = item.get("source")
        ident = item.get("id")
        if source not in ("marktplaats", "facebook") or not isinstance(ident, str):
            return False, ["FAILED: unknown source or invalid candidate ID"]
        if not ident.startswith(source + ":") or ident in ids:
            return False, ["FAILED: conflicting or duplicate candidate IDs"]
        if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("content_hash") or "")):
            return False, ["FAILED: missing candidate content hash"]
        ids.add(ident)
        counted[source] += 1
    advertised = combined.get("platform_counts")
    if (combined.get("count") != len(items) or not isinstance(advertised, dict)
            or any(advertised.get(kind) != counted[kind]
                   for kind in ("marktplaats", "facebook"))):
        return False, ["FAILED: combined export count inconsistency"]
    warnings.append("AET Pro v2 candidate format and unique IDs: OK")
    warnings.append("Saved candidates by platform: Marktplaats " + str(counted["marktplaats"]) +
                    ", Facebook " + str(counted["facebook"]))
    if mp.get("scan_health") != "ok" or fb.get("status") != "ok":
        warnings.append("WARNING: latest saved source health is not OK")
    else:
        warnings.append("Saved source health flags: OK")

    for kind, status in (("Marktplaats", mp), ("Facebook", fb)):
        timestamp = _when(status.get("checked_at"))
        age_hours = (now - timestamp).total_seconds()/3600 if timestamp else float("inf")
        if age_hours < -0.25:
            return False, ["FAILED: future-dated private scan"]
        if age_hours > 4:
            warnings.append(kind + ": HISTORICAL ONLY (older than four hours)")
        else:
            warnings.append(kind + ": recently saved; no live scan verified")

    calls = budget.get("calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or not 0 <= calls <= 100:
        return False, ["FAILED: malformed or exceeded provider call counter"]
    if budget.get("month") != now.strftime("%Y-%m"):
        warnings.append("Facebook call counter is from a different month")
    warnings.append("Recorded Facebook Actor calls this budget month: " + str(calls) +
                    "/100 (provider-specific cost not verified)")

    permissions = repo_metadata.get("permissions")
    push = permissions.get("push") if isinstance(permissions, dict) else None
    if push is True:
        warnings.append("GitHub metadata reports push permission: YES (not a write test)")
    elif push is False:
        warnings.append("GitHub metadata reports push permission: NO (write access unresolved)")
    else:
        warnings.append("GitHub token write permission: UNKNOWN (no write test performed)")
    warnings.append("READ-ONLY: no GitHub commits, marketplace calls or AET Pro posts")
    return True, warnings


def main() -> int:
    if (os.environ.get("GITHUB_REPOSITORY") != PUBLIC_REPO
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        print("REFUSED: owner/main-only private-state preflight")
        return 1
    token = os.environ.get("AET_GITHUB_TOKEN", "").strip()
    if not token:
        print("FAILED: AET_GITHUB_TOKEN not configured")
        return 1
    opener = build_opener(NoRedirect())
    try:
        metadata = read_json(HOST, token, opener)
        snapshots = {}
        for filename in FILES:
            raw = read_json(HOST + "/contents/" + filename + "?ref=main", token, opener)
            snapshots[filename] = decode_snapshot(raw)
        passed, results = validate(snapshots, metadata, datetime.now(timezone.utc))
        for result in results:
            print(result)
        return 0 if passed else 1
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        if isinstance(exc, HTTPError):
            print("FAILED: private GitHub GET returned HTTP " + str(exc.code))
        else:
            print("FAILED: private GitHub read or data validation error (details suppressed)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
