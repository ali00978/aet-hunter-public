"""Explicit, read-only GitHub/Apify token verification.

Run manually only. Fixed GET endpoints; no actor runs, marketplace requests,
GitHub writes, AET Pro sync, or response-body logging.
"""
from __future__ import annotations

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "ali00978/aet-hunter-public"
MAIN = "refs/heads/main"
CHECKS = (
    ("GitHub private repository read access", "AET_GITHUB_TOKEN",
     "https://api.github.com/repos/ali00978/aet-cars-ai-hunter/contents/hunter_sync.py"),
    ("Apify account API authentication", "APIFY_TOKEN",
     "https://api.apify.com/v2/users/me"),
)


class RejectRedirect(HTTPRedirectHandler):
    """A redirect must never forward an authentication token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def status_for(url: str, token: str, opener) -> tuple[bool, str]:
    """Only read status; don't read or expose potentially sensitive API bodies."""
    req = Request(
        url,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json, application/json",
                 "User-Agent": "AET-Hunter-ReadOnly-Check/1"},
        method="GET",
    )
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status == 200, "HTTP " + str(resp.status)
    except HTTPError as exc:
        return False, "HTTP " + str(exc.code)
    except (URLError, TimeoutError, OSError, ValueError):
        return False, "network or TLS error"


def main() -> int:
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != MAIN):
        print("Refusing non-owner/non-main connection check")
        return 1
    opener = build_opener(RejectRedirect())
    failed = False
    for label, secret_name, endpoint in CHECKS:
        token = os.environ.get(secret_name, "").strip()
        if not token:
            print(label + ": NOT READY (missing " + secret_name + ")")
            failed = True
            continue
        valid, status = status_for(endpoint, token, opener)
        print(label + ": " + ("OK" if valid else "FAILED") + " (" + status + ")")
        if not valid:
            failed = True
    print("Read-only GET checks only: no scans, actor runs, data writes or syncing.")
    print("GitHub write permission, Apify actor entitlements and AET Pro sync are NOT tested.")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
