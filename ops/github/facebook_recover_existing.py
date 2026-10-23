"""Recover an EXISTING timed-out Apify run using READ-ONLY API calls.

No new Apify Actor POST, no chargeable starts, no marketplace scraping.
Only the specific HydeData actor run launched on 2026-10-23 ~16:22 UTC
may be imported into AET Pro, at most once. All listings and state stay
inside encrypted Hunter cache, never in public GitHub files or logs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from ops.github import standalone_hunter_runner as secure

OWNER = "ali00978/aet-hunter-public"
REF = "refs/heads/main"
WORKFLOW = "AET Facebook recover existing Apify run (GET only)"
ACTOR_URL = "https://api.apify.com/v2/actors/hydedata~facebook-marketplace-scraper"
RUNS_URL = "https://api.apify.com/v2/actor-runs"
START = datetime.fromisoformat("2026-10-23T16:20:00+00:00")
END = datetime.fromisoformat("2026-10-23T16:26:00+00:00")
MAX_SIZE = 1_500_000
DATASET_LIMIT = 80


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def authorize(env):
    if (env.get("GITHUB_REPOSITORY") != OWNER
            or env.get("GITHUB_REF") != REF
            or env.get("GITHUB_EVENT_NAME") != "push"
            or env.get("GITHUB_RUN_ATTEMPT") != "1"
            or env.get("GITHUB_WORKFLOW") != WORKFLOW
            or env.get("AET_READONLY_RECOVER") != "EXISTING_RUN_ONLY"):
        raise PermissionError("Recover existing Apify run: owner/main one time only")
    if not isinstance(env.get("APIFY_TOKEN"), str) or len(env["APIFY_TOKEN"]) < 15:
        raise ValueError("Missing Apify credential")
    from public_hunter_runtime import require_sync
    require_sync(env)
    return env


def get_api(url, token, opener=None):
    """Restrict GET destinations so tokens never leak to arbitrary hosts."""
    valid = (
        url == ACTOR_URL or
        url.startswith(RUNS_URL + "?") or
        bool(re.fullmatch(r"https://api\.apify\.com/v2/actor-runs/[A-Za-z0-9]{8,35}", url)) or
        bool(re.fullmatch(r"https://api\.apify\.com/v2/datasets/[A-Za-z0-9]{8,35}/items\?format=json&limit=80&clean=1",url))
    )
    if not valid:
        raise ValueError("Unapproved Apify recovery GET destination")
    if url.startswith(RUNS_URL + "?") and url != (
        RUNS_URL + "?" + urlencode({
            "limit": 50, "desc": 1,
            "startedAfter": "2026-10-23T16:20:00.000Z",
            "startedBefore": "2026-10-23T16:26:00.000Z"})):
        raise ValueError("Unapproved run-list query")
    request = Request(url, method="GET", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": "AET-Hunter-ReadOnly-Recovery/1",
    })
    opener = opener or build_opener(NoRedirect())
    with opener.open(request, timeout=15) as resp:
        if resp.status != 200:
            raise ValueError("Read-only Apify API error")
        raw = resp.read(MAX_SIZE + 1)
    if len(raw) > MAX_SIZE:
        raise ValueError("Oversized recovery response")
    return json.loads(raw.decode("utf-8"))


def select_run(token, getter=get_api):
    actor = getter(ACTOR_URL, token)
    actor_data = actor.get("data") if isinstance(actor, dict) else None
    actor_id = actor_data.get("id") if isinstance(actor_data, dict) else None
    if not isinstance(actor_id, str) or not re.fullmatch(r"[A-Za-z0-9]{8,35}", actor_id):
        raise ValueError("Cannot verify actor identity")
    query = urlencode({
        "limit": 50, "desc": 1,
        "startedAfter": "2026-10-23T16:20:00.000Z",
        "startedBefore": "2026-10-23T16:26:00.000Z"})
    response = getter(RUNS_URL + "?" + query, token)
    info = response.get("data") if isinstance(response, dict) else None
    entries = info.get("items") if isinstance(info, dict) else None
    if not isinstance(entries, list) or len(entries) > 50:
        raise ValueError("Unverifiable recent Actor runs")
    accepted = []
    for item in entries:
        if not isinstance(item, dict) or item.get("actId") != actor_id:
            continue
        try:
            when = datetime.fromisoformat(item["startedAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError, TypeError):
            continue
        if when.tzinfo is not None and START <= when <= END:
            accepted.append(item)
    if len(accepted) != 1:
        raise ValueError("Expected exactly one known October 23 actor run")
    run = accepted[0]
    run_id = run.get("id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9]{8,35}", run_id):
        raise ValueError("Invalid actor run reference")
    detail = getter(RUNS_URL + "/" + run_id, token)
    data = detail.get("data") if isinstance(detail, dict) else None
    if (not isinstance(data, dict) or data.get("id") != run_id
            or data.get("actId") != actor_id):
        raise ValueError("Actor run details do not match")
    return data


def fetch_existing(token, getter=get_api):
    run = select_run(token, getter=getter)
    state = str(run.get("status") or "unknown")
    if state != "SUCCEEDED":
        print("Existing Apify actor status: " +
              (state if state in ("RUNNING","READY","FAILED","ABORTED",
                                  "TIMED-OUT","TIMING-OUT","unknown") else "unknown"))
        return None
    dataset_id = run.get("defaultDatasetId")
    if not isinstance(dataset_id, str) or not re.fullmatch(r"[A-Za-z0-9]{8,35}", dataset_id):
        raise ValueError("Completed run has no verifiable dataset")
    url = ("https://api.apify.com/v2/datasets/" + dataset_id +
           "/items?format=json&limit=80&clean=1")
    rows = getter(url, token)
    if not isinstance(rows, list) or len(rows) > DATASET_LIMIT:
        raise ValueError("Unexpected recovered Apify dataset format")
    print("Existing Apify actor status: SUCCEEDED")
    print("Existing Apify dataset rows: " + str(len(rows)))
    return rows


def retain_candidates(root, rows, now):
    """Import already paid-for rows with the same conservative vehicle gate."""
    import facebook_hunter
    import public_apify_collector as apify
    result = {}
    fields = ("url", "title", "price", "listedAt", "city", "location",
              "mileage_km", "mileage", "year", "year_detected",
              "photoUrl", "images", "photos", "seller_type", "description")
    seen_path = Path(root) / "apify_seen.json"
    if seen_path.is_file():
        seen = json.loads(seen_path.read_text(encoding="utf-8"))
        if not isinstance(seen, dict):
            raise ValueError("Invalid history; cannot safely restore")
    else:
        seen = {}
    for raw in rows:
        if (not isinstance(raw, dict) or raw.get("isSold") is True
                or raw.get("isPending") is True):
            continue
        url = raw.get("url")
        if not isinstance(url, str) or len(url) > 600:
            continue
        obj = {name:raw[name] for name in fields if name in raw}
        obj["first_seen_at"] = seen.get(url) or now.isoformat()
        seller = str(raw.get("sellerType") or "").upper()
        if seller == "PRIVATE_SELLER":
            obj["seller_type"] = "particulier"
        elif seller in ("DEALER", "DEALERSHIP", "BUSINESS_SELLER"):
            obj["seller_type"] = "handelaar"
        car = facebook_hunter.normalize(obj, "Apify", now, provider_at=now)
        if car is None:
            continue
        if car["id"] in result:
            continue
        obj["photo_urls"] = car["photo_urls"]
        obj["seller_type"] = car["seller_classification"]
        result[car["id"]] = obj
        if url not in seen:
            seen[url] = now.isoformat()
    if not result:
        return 0
    apify.atomic_json(Path(root) / "apify_seen.json",
                      dict(list(seen.items())[-4000:]))
    apify.atomic_json(Path(root) / "apify_kandidaten.json",
                      list(result.values()))
    apify.atomic_json(Path(root) / "apify_status.json", {
        "status": "ok", "checked_at": now.isoformat(),
        "provider": "Apify HydeData recovered completed actor",
        "received": len(rows), "accepted": len(result),
        "recovered_existing": True,
    })
    return len(result)


def execute(env=None, getter=get_api, sync_fn=None):
    env = authorize(dict(os.environ if env is None else env))
    cache = Path(env.get("AET_ENCRYPTED_STATE_PATH",
                         secure.DEFAULT_ENCRYPTED_FILE))
    if not cache.is_absolute():
        cache = Path.cwd() / cache
    if not cache.is_file() or cache.stat().st_size > secure.CACHE_LIMIT:
        raise ValueError("No safe encrypted Hunter history")
    token = env["AET_HUNTER_SYNC_TOKEN"]
    state = secure.unseal(cache.read_bytes(), token)
    if "apify_recovery_once.json" in state["files"]:
        raise ValueError("This completed Actor run has already been recovered")
    with tempfile.TemporaryDirectory(prefix="aet-facebook-get-only-") as directory:
        root = Path(directory)
        secure.restore_state(root, state)
        now = datetime.now(timezone.utc)
        rows = fetch_existing(env["APIFY_TOKEN"], getter=getter)
        if rows is None:
            print("No completed Actor result is available to import.")
            return "no_completed_actor"
        accepted = retain_candidates(root, rows, now)
        print("Existing Facebook cars accepted: " + str(accepted))
        if accepted == 0:
            return "no_vehicles"
        from public_hunter_runtime import require_sync
        endpoint, key = require_sync(env)
        import facebook_hunter
        import hunter_feed
        import hunter_pro_combined
        import hunter_sync
        previous = (facebook_hunter.ROOT, hunter_feed.ROOT,
                    hunter_pro_combined.OUTPUT, hunter_sync.ROOT)
        cwd = Path.cwd()
        try:
            facebook_hunter.ROOT = root
            hunter_feed.ROOT = root
            hunter_pro_combined.OUTPUT = root / "hunter_pro_combined.json"
            hunter_sync.ROOT = root
            os.chdir(root)
            combined = hunter_pro_combined.write(
                path=root / "hunter_pro_combined.json", now=now)
            if combined["platform_counts"]["facebook"] < 1:
                raise ValueError("No valid Facebook cars for AET Pro")
            status = json.loads((root / "apify_status.json").read_text())
            package = hunter_sync.envelope(
                "facebook-apify", str(os.getpid()), "1", now=now,
                combined=combined, source_status=status)
            if not package["scan"]["candidate_ids"]:
                raise ValueError("Facebook IDs missing in sync")
            # Commit attempt marker BEFORE sending, to avoid repeated import
            # after a timeout or an uncertain worker response.
            from public_apify_collector import atomic_json
            atomic_json(root / "apify_recovery_once.json", {
                "attempted_at": now.isoformat(),
                "run_digest": hashlib.sha256(
                    json.dumps(sorted([x["url"] for x in rows if isinstance(x,dict)
                                       and isinstance(x.get("url"),str)]),
                               separators=(",",":")).encode("utf-8")).hexdigest(),
            })
            secure.encrypted_checkpoint(root, cache, token)
            send = sync_fn or hunter_sync.send
            reply = send(package, endpoint, key)
            if not isinstance(reply, dict) or reply.get("received") is not True:
                raise RuntimeError("AET Pro receipt was not confirmed")
            print("Existing Facebook recovered, synced to AET Pro: " +
                  str(len(package["scan"]["candidate_ids"])))
            return "synced"
        finally:
            os.chdir(cwd)
            (facebook_hunter.ROOT, hunter_feed.ROOT,
             hunter_pro_combined.OUTPUT, hunter_sync.ROOT) = previous
            secure.encrypted_checkpoint(root, cache, token)


def main():
    try:
        result = execute()
        return 0 if result in ("synced", "no_vehicles", "no_completed_actor") else 1
    except Exception as error:
        print("Read-only Apify recovery stopped safely (" +
              type(error).__name__ + "). No paid Actor start occurred.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
