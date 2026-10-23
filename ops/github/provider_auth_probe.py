"""Read-only, manually invoked credential probes for AET CARS Hunter.

Checks one authorized GitHub PRIVATE file and the Apify current-user endpoint.
No advertising scans, provider actor runs, synchronization, writes, secrets in logs,
or cached result files. A failed authentication does not reveal response bodies.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

TARGETS = {
    "github": (
        "AET_GITHUB_TOKEN",
        "https://api.github.com/repos/ali00978/aet-cars-ai-hunter/contents/hunter_sync.py",
    ),
    "apify": (
        "APIFY_TOKEN",
        "https://api.apify.com/v2/users/me",
    ),
}


class NoRedirects(HTTPRedirectHandler):
    """Never forward Authorization headers to redirected locations."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(kind: str, env: dict[str, str], opener=None) -> bool:
    if kind not in TARGETS:
        raise ValueError("Unknown provider")
    key, url = TARGETS[kind]
    token = env.get(key, "").strip()
    label = "Private GitHub file READ" if kind == "github" else "Apify account READ"
    if not token:
        print(label + ": FAIL (missing encrypted secret name: " + key + ")")
        return False
    request = Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json" if kind == "github" else "application/json",
            "User-Agent": "AET-Hunter-Manual-Auth-Check",
        },
        method="GET",
    )
    opener = opener if opener is not None else build_opener(NoRedirects())
    try:
        with opener.open(request, timeout=15) as response:
            if response.geturl() != url or response.status != 200:
                raise ValueError("Non-exact destination or unexpected HTTP status")
            # Read a bounded response only to prove it is the expected resource.
            # This may contain private data: NEVER print or persist it.
            answer = json.loads(response.read(128_000))
        if not isinstance(answer, dict):
            raise ValueError("Unexpected response type")
        if kind == "github":
            valid = (answer.get("type") == "file"
                     and answer.get("path") == "hunter_sync.py")
        else:
            data = answer.get("data")
            valid = isinstance(data, dict) and bool(data.get("id"))
        if not valid:
            raise ValueError("Unexpected resource")
    except HTTPError as error:
        # Never print the response or original exception: could include user data.
        print(label + ": FAIL (HTTP " + str(error.code) + "; body hidden)")
        return False
    except (URLError, TimeoutError, OSError, ValueError, TypeError):
        print(label + ": FAIL (network issue or unexpected response; details hidden)")
        return False
    print(label + ": PASS (authentication accepted; read-only)")
    return True


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in TARGETS:
        print("Usage: provider_auth_probe.py github|apify")
        return 2
    kind = args[0]
    passed = check(kind, dict(os.environ))
    if kind == "github":
        print("GitHub write access is NOT checked; this is a safe read-only test.")
    else:
        print("Apify Actor availability, paid runs, or quota are NOT checked.")
    print("No listings, Facebook/Marktplaats, or AET Pro data were changed.")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
