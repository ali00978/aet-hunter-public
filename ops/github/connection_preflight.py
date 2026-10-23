"""Check for required settings without ever printing their values.

No HTTP requests, no provider calls, no private repository clone.
"""
from __future__ import annotations

import os
import sys

NAMES = ("AET_GITHUB_TOKEN", "AET_PRO_SYNC_URL",
         "AET_HUNTER_SYNC_TOKEN", "APIFY_TOKEN")


def check(env: dict[str, str]) -> tuple[bool, list[str]]:
    missing = [name for name in NAMES if not env.get(name, "").strip()]
    return not missing, missing


def main() -> int:
    ready, missing = check(dict(os.environ))
    print("AET Hunter — offline GitHub connection preflight")
    if not ready:
        print("Missing encrypted secret NAMES (never values): " + ", ".join(missing))
    else:
        print("Four encrypted secret names are set (not validity tested).")
    print("Public live activation variable: " +
          ("ON" if os.environ.get("AET_PUBLIC_HUNTER_ENABLED") == "true" else "OFF"))
    print("GitHub usage permission gate: " +
          ("ON" if os.environ.get("AET_GITHUB_TERMS_APPROVED") == "true" else "OFF"))
    print("No marketplace, AET Pro, Apify, or private GitHub requests were made.")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
