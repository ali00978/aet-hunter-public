"""Apify Facebook Actor + usage headroom: manual GET-only readiness check.

No Actor runs, no listings, no marketplace requests, no writes and no secret
values or private API bodies in console output.
"""
from __future__ import annotations

import json
import math
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "ali00978/aet-hunter-public"
REF = "refs/heads/main"
ACTOR = "https://api.apify.com/v2/actors/hydedata~facebook-marketplace-scraper"
LIMITS = "https://api.apify.com/v2/users/me/limits"
MAX_BYTES = 120000


class RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(url: str, token: str, opener) -> tuple[int | None, dict | None]:
    """Get one fixed authenticated API endpoint, with strict response cap."""
    if url not in (ACTOR, LIMITS):
        raise ValueError("Unknown read-only endpoint")
    request = Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": "AET-Hunter-Apify-Readiness/1",
    }, method="GET")
    try:
        with opener.open(request, timeout=12) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return response.status, None
            value = json.loads(raw.decode("utf-8"))
            return response.status, value if isinstance(value, dict) else None
    except HTTPError as exc:
        return exc.code, None
    except (URLError, TimeoutError, OSError, ValueError, UnicodeError):
        return None, None


def summarize(actor: tuple[int | None, dict | None],
              limits: tuple[int | None, dict | None]) -> tuple[bool, list[str]]:
    """Emit only coarse, safe statuses. Never display account identities or costs."""
    messages = []
    actor_code, actor_body = actor
    body = actor_body.get("data") if isinstance(actor_body, dict) else None
    actor_ok = (
        actor_code == 200 and isinstance(body, dict)
        and body.get("username") == "hydedata"
        and body.get("name") == "facebook-marketplace-scraper"
        and body.get("isPublic") is not False
    )
    messages.append("Facebook Actor: " + ("FOUND" if actor_ok else "NOT VERIFIED") +
                    " (HTTP " + str(actor_code or "network failure") + ")")

    limits_code, limits_body = limits
    values = limits_body.get("data") if isinstance(limits_body, dict) else None
    scope = values.get("limits") if isinstance(values, dict) else None
    current = values.get("current") if isinstance(values, dict) else None
    max_usd = scope.get("maxMonthlyUsageUsd") if isinstance(scope, dict) else None
    spent_usd = current.get("monthlyUsageUsd") if isinstance(current, dict) else None
    numbers = (
        isinstance(max_usd, (int, float)) and not isinstance(max_usd, bool)
        and isinstance(spent_usd, (int, float)) and not isinstance(spent_usd, bool)
        and math.isfinite(max_usd) and math.isfinite(spent_usd)
        and max_usd > 0 and spent_usd >= 0
    )
    ready = limits_code == 200 and numbers and spent_usd < max_usd * 0.9
    if ready:
        message = "WITHIN LIMIT (below 90% of monthly USD limit)"
    elif limits_code != 200:
        message = "NOT VERIFIED (HTTP " + str(limits_code or "network failure") + ")"
    elif numbers and spent_usd >= max_usd * 0.9:
        message = "STOP (at or above 90% of monthly USD limit)"
    else:
        message = "NOT VERIFIED (missing usable monthly USD limit)"
    messages.append("Apify usage headroom: " + message)
    messages.append("No provider run performed; Actor execution entitlement and actual future costs remain untested.")
    return bool(actor_ok and ready), messages


def main() -> int:
    if (os.environ.get("GITHUB_REPOSITORY") != REPO or
            os.environ.get("GITHUB_REF") != REF):
        print("REFUSED: owner/main only")
        return 1
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        print("NOT READY: APIFY_TOKEN absent")
        return 1
    opener = build_opener(RejectRedirect())
    ok, lines = summarize(read_json(ACTOR, token, opener),
                          read_json(LIMITS, token, opener))
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
