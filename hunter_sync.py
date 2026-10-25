"""Push finished Hunter research snapshots to AET PRO with traceable scan history.

Uses ONLY saved source snapshots, not new Marktplaats or Facebook HTTP calls.
Credentials stay in GitHub Actions secrets. Dry-run exercises the same envelope.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from hunter_pro_combined import build as build_combined

ROOT = Path(os.environ.get("AET_HUNTER_STATE_DIR", Path(__file__).resolve().parent))
CONFIG = {
    "marktplaats": ("marktplaats_status.json", "marktplaats"),
    "facebook-apify": ("apify_status.json", "facebook"),
    "facebook-socialfetch": ("socialfetch_status.json", "facebook"),
}


def _date(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def envelope(source, run_id, attempt, now=None, combined=None, source_status=None):
    if source not in CONFIG:
        raise ValueError("Unknown Hunter provider")
    if not str(run_id).isdigit() or not str(attempt).isdigit():
        raise ValueError("GitHub scan run ID or attempt not set")
    now = now or datetime.now(timezone.utc)
    filename, platform = CONFIG[source]
    status = source_status
    if status is None:
        status = json.loads((ROOT / filename).read_text(encoding="utf-8"))
    if not isinstance(status, dict):
        raise ValueError("Scan status is invalid")
    valid = status.get("scan_health") == "ok" if source == "marktplaats" else status.get("status") == "ok"
    if not valid:
        raise ValueError("Only completed healthy provider scans may synchronize")
    scanned = _date(status.get("checked_at"))
    if scanned is None or scanned > now + timedelta(minutes=15) or now - scanned > timedelta(hours=4):
        raise ValueError("Scan date is missing, future, or outdated")
    combined = combined if combined is not None else build_combined(now=now)
    if combined.get("schema") != "aet-pro.hunter.v2" or not isinstance(combined.get("candidates"), list):
        raise ValueError("Invalid combined Hunter export")
    cars = [x["id"] for x in combined["candidates"] if x.get("source") == platform]
    if len(cars) > (100 if platform == "marktplaats" else 30):
        raise ValueError("Too many source candidates")
    if len(cars) != len(set(cars)):
        raise ValueError("Duplicate source IDs")
    # An observation is the *set displayed after a completed scan*, not a claim
    # that every car was discovered for the first time at this timestamp.
    scan = {
        "id": source + ":" + str(run_id) + ":" + str(attempt),
        "source": source,
        "platform": platform,
        "scanned_at": scanned.isoformat(),
        "synchronized_at": now.astimezone(timezone.utc).isoformat(),
        "candidate_ids": cars,
    }
    return {"schema": "aet-pro.hunter.sync.v1", "scan": scan, "batch": combined}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(payload, endpoint, token):
    url = urlsplit(endpoint)
    if (url.scheme != "https" or url.hostname != "aet-cars-pro.eltakech100.workers.dev"
            or url.path != "/api/integrations/hunter/sync" or url.port not in (None, 443)
            or url.username or url.password or url.fragment or url.query):
        raise ValueError("Configure AET_PRO_SYNC_URL to the HTTPS Cloudflare sync endpoint")
    if not token or len(token) < 24:
        raise ValueError("AET_HUNTER_SYNC_TOKEN secret must be at least 24 characters")
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(body) > 1800000:
        raise ValueError("Hunter sync payload is too large")
    for attempt in range(3):
        request = Request(endpoint, data=body, method="POST",
                          headers={"Content-Type": "application/json",
                                   "X-Hunter-Token": token,
                                   "User-Agent": "AET-Hunter-Modal/1"})
        try:
            with build_opener(NoRedirects()).open(request, timeout=20) as response:
                if response.status != 200:
                    raise ValueError("Unexpected sync HTTP " + str(response.status))
                # A public GitHub job must never stream an unbounded response
                # from AET Pro or mistake malformed JSON for confirmation.
                raw = response.read(64_001)
                if len(raw) > 64_000:
                    raise ValueError("AET Pro sync receipt exceeds safety limit")
                try:
                    answer = json.loads(raw.decode("utf-8"))
                except (UnicodeError, ValueError):
                    raise ValueError("AET Pro sync receipt is not valid JSON") from None
                if not isinstance(answer, dict) or answer.get("received") is not True:
                    raise ValueError("AET PRO did not confirm synchronization")
                return answer
        except HTTPError as error:
            if error.code not in (429, 502, 503, 504) or attempt == 2:
                raise ValueError("AET PRO refused sync (HTTP " + str(error.code) + ")") from error
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("Sync failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=CONFIG)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    payload = envelope(args.source, os.environ.get("GITHUB_RUN_ID", "1"),
                       os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    details = payload["scan"]
    print("Prepared completed scan", details["id"], details["scanned_at"],
          "candidate associations", len(details["candidate_ids"]))
    if not args.send:
        return 0
    endpoint = os.environ.get("AET_PRO_SYNC_URL", "")
    token = os.environ.get("AET_HUNTER_SYNC_TOKEN", "")
    if not endpoint or not token:
        # Previous behavior silently marked the scan successful without
        # delivering any results to AET Pro. Fail safely and visibly instead.
        raise RuntimeError("AET Pro sync missing required Actions secrets: "
                           "AET_PRO_SYNC_URL and/or AET_HUNTER_SYNC_TOKEN")
    outcome = send(payload, endpoint, token)
    # Never echo a potentially unexpected private Worker response into
    # public GitHub Actions logs. The receipt itself is sufficient.
    print("AET PRO confirmed receipt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
