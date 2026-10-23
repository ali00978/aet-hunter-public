"""One-off, developer-initiated live integration of the public Marktplaats scanner.

No private repositories, third-party provider calls, cloud sync, secrets,
published candidate URLs, saved outputs, or scheduled runs.
This is not a production background Hunter or an AET Pro import.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

OWNER = "ali00978/aet-hunter-public"
REF = "refs/heads/main"
SAFE_COUNTERS = (
    "candidate_count", "hotlist_count", "bid_opportunity_count",
    "search_pages_requested", "detail_pages_attempted",
    "requests_attempted_total", "search_errors", "detail_errors",
)


def main() -> int:
    if os.environ.get("GITHUB_REPOSITORY") != OWNER or os.environ.get("GITHUB_REF") != REF:
        print("REFUSED: the integration test only runs on the owner's public main branch")
        return 1

    # Import after owner check; no repository tokens or private code are used.
    import scanner

    previous_directory = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="aet-public-integration-") as workdir:
        try:
            os.chdir(workdir)
            # The scanner may print links or descriptions on unexpected errors.
            # Never expose that output in PUBLIC Actions logs.
            with open(os.devnull, "w", encoding="utf-8") as sink:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    scanner.main()
            status = json.loads(Path("marktplaats_status.json").read_text(encoding="utf-8"))
            if not isinstance(status, dict):
                raise ValueError("invalid aggregate scan status")
        except Exception as error:
            print("Live scanner integration FAILED (failure class: "
                  + type(error).__name__ + "). No raw records or URLs logged.")
            return 1
        finally:
            os.chdir(previous_directory)

        print("Public Marktplaats live integration finished (no private repository or AET Pro access).")
        for field in SAFE_COUNTERS:
            value = status.get(field, 0)
            if type(value) is int and 0 <= value <= 1000000:
                print(field + ": " + str(value))
        print("scan_health: " + str(status.get("scan_health") in ("ok", "healthy")))
        print("blocked_this_run: " + str(status.get("blocked_this_run") is True))
        print("Candidate details and any temporary scan JSON are deleted after the test.")

        if status.get("blocked_this_run") or status.get("global_backoff_active"):
            print("Integration marked unsuccessful: marketplace imposed a restriction; no retries.")
            return 1
        if not isinstance(status.get("search_pages_requested"), int) or status["search_pages_requested"] < 1:
            print("Integration marked unsuccessful: no search pages requested.")
            return 1
        if not isinstance(status.get("detail_pages_attempted"), int) or status["detail_pages_attempted"] < 1:
            print("Integration marked incomplete: no advert detail page tested.")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
