"""Manual AET Pro authorization handshake with a deliberately invalid payload.

The production AET Pro Hunter sync endpoint is expected to check the
X-Hunter-Token, then reject a body with an invalid schema (HTTP 422). We never
send a valid batch, scan listings, request marketplace APIs, or log credentials.
A second intentionally unauthenticated request must be rejected as well.
"""
from __future__ import annotations

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

EXPECTED_HOST = "aet-cars-pro.eltakech100.workers.dev"
EXPECTED_PATH = "/api/integrations/hunter/sync"
INVALID_BODY = b'{"schema":"invalid-intentional-auth-check"}'
REPO = "ali00978/aet-hunter-public"
REF = "refs/heads/main"


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def valid_endpoint(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname == EXPECTED_HOST
            and parsed.path == EXPECTED_PATH
            and parsed.port in (None, 443)
            and not (parsed.username or parsed.password or parsed.query
                     or parsed.fragment)
        )
    except ValueError:
        return False


def probe(url: str, token: str | None, opener) -> int | None:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AET-Hunter-GitHub-Actions/1",
    }
    if token is not None:
        headers["X-Hunter-Token"] = token
    req = Request(url, data=INVALID_BODY, method="POST", headers=headers)
    try:
        with opener.open(req, timeout=12) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except (URLError, TimeoutError, OSError):
        return None


def main() -> int:
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != REF):
        print("REFUSED: the manual handshake runs only on owner/main")
        return 1

    url = os.environ.get("AET_PRO_SYNC_URL", "")
    token = os.environ.get("AET_HUNTER_SYNC_TOKEN", "")
    if not valid_endpoint(url):
        print("FAILED: AET_PRO_SYNC_URL is not the expected HTTPS Cloudflare sync URL")
        return 1
    if not token or len(token) < 24:
        print("FAILED: AET_HUNTER_SYNC_TOKEN is missing or too short")
        return 1

    opener = build_opener(NoRedirects())
    authenticated = probe(url, token, opener)
    print("AET Pro invalid-payload authenticated response: "
          + ("HTTP " + str(authenticated) if authenticated is not None else "network failure"))
    if authenticated != 422:
        print("FAILED: did not receive expected rejection of authenticated invalid data.")
        print("No valid payload or vehicle data was submitted.")
        return 1

    unauthenticated = probe(url, None, opener)
    print("AET Pro invalid-payload no-token response: "
          + ("HTTP " + str(unauthenticated) if unauthenticated is not None else "network failure"))
    if unauthenticated not in (401, 403):
        print("FAILED: endpoint did not demonstrably reject a request without the token.")
        return 1
    print("PASS: token accepted, invalid payload rejected; no valid Hunter sync sent.")
    print("No advertisements scanned, provider Actors launched, or real records submitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
