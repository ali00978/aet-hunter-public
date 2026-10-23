"""One-request live Marktplaats software-development smoke test.

Tests one public search page with the same parser as the Hunter, no secrets,
no private repo, no detail pages, no retries, no state writes or AET Pro sync.
Never prints listing titles/URLs or remote response bodies.
"""
from __future__ import annotations

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit

import scanner
from marktplaats_data import search_page_records

OWNER = "ali00978/aet-hunter-public"
REF = "refs/heads/main"
TERM = "opel corsa"
MAX_RECOGNIZED = 250


def smoke(fetcher) -> tuple[bool, str]:
    url = scanner.BASE + "/q/" + quote(TERM) + "/"
    try:
        html, final, status = fetcher(url)
    except HTTPError as err:
        return False, "Marketplace blocked or refused search (HTTP " + str(err.code) + "); no retry"
    except (URLError, TimeoutError, OSError, ValueError):
        return False, "Marketplace search/network error; no retry"

    dest = urlsplit(final)
    if status != 200 or dest.scheme != "https" or dest.hostname not in (
            "www.marktplaats.nl", "marktplaats.nl") or dest.username or dest.password:
        return False, "Marketplace search did not return a valid allowed HTTP 200 page"

    page = scanner.parse(html)
    all_urls = list(page.links) + list(search_page_records(page.scripts))
    ids: set[str] = set()
    for raw in all_urls[:3000]:
        normalized = scanner.normalize_link(raw)
        if normalized and scanner.approved_listing_brand(normalized):
            ident = scanner.listing_id(normalized)
            if ident:
                ids.add(str(ident))
        if len(ids) > MAX_RECOGNIZED:
            break
    if not ids:
        return False, "HTTP 200 but no valid complete-car listing links parsed"
    return True, "HTTP 200; search parser found " + str(len(ids)) + " unique car listing links"


def main() -> int:
    if os.environ.get("GITHUB_REPOSITORY") != OWNER or os.environ.get("GITHUB_REF") != REF:
        print("REFUSED: owner/main-only development smoke test")
        return 1
    ok, message = smoke(scanner.fetch)
    print("One live public Marktplaats search request: " + message)
    print("No real Hunter scan, detail requests, saved ads, private API, or AET Pro sync.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
