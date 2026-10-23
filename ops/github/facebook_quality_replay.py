"""One-time AET Pro integration check using ONLY existing encrypted Facebook data.

No Apify or Facebook network access. Re-ranks cached candidates with the
updated trade quality gates, logs only aggregate counts, and syncs at most
one fresh, previously completed scan snapshot to AET Pro.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ops.github import standalone_hunter_runner as secure
from public_hunter_runtime import require_sync

OWNER = "ali00978/aet-hunter-public"
WORKFLOW = "AET Facebook review saved ads and sync quality ranking"
MARKER = "facebook_quality_replay_once.json"
MAX_AGE = timedelta(hours=4)


def check_owner(env):
    if (env.get("GITHUB_REPOSITORY") != OWNER
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "push"
            or env.get("GITHUB_RUN_ATTEMPT") != "1"
            or env.get("GITHUB_WORKFLOW") != WORKFLOW
            or env.get("AET_FACEBOOK_QUALITY_REPLAY") != "ONE_SAVED_BATCH"):
        raise PermissionError("Owner-only single quality integration test")
    return require_sync(env)


def execute(env=None, send=None):
    env = dict(os.environ if env is None else env)
    endpoint, token = check_owner(env)
    file = Path(env.get("AET_ENCRYPTED_STATE_PATH",secure.DEFAULT_ENCRYPTED_FILE))
    if not file.is_absolute():
        file = Path.cwd() / file
    if not file.is_file() or file.stat().st_size > secure.CACHE_LIMIT:
        raise ValueError("Encrypted Hunter state missing; no data sent")
    saved = secure.unseal(file.read_bytes(),token)
    if MARKER in saved["files"]:
        raise ValueError("Quality replay already executed")
    with tempfile.TemporaryDirectory(prefix="aet-facebook-quality-replay-") as temp:
        root = Path(temp)
        secure.restore_state(root,saved)
        source = root / "apify_status.json"
        listings = root / "apify_kandidaten.json"
        if not source.is_file() or not listings.is_file():
            raise ValueError("No completed saved Facebook scan")
        status = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(status,dict) or status.get("status") != "ok":
            raise ValueError("Only successful Facebook snapshots can be re-ranked")
        now = datetime.now(timezone.utc)
        stamp = status.get("checked_at")
        try:
            dt = datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
        except (ValueError,TypeError):
            raise ValueError("Invalid saved scan time") from None
        if dt.tzinfo is None or now < dt or now-dt > MAX_AGE:
            print("Saved Facebook results are older than 4 hours; no re-import.")
            return "stale"
        entries = json.loads(listings.read_text(encoding="utf-8"))
        if not isinstance(entries,list) or len(entries)>1000:
            raise ValueError("Saved Facebook records missing or oversized")

        import facebook_hunter
        import hunter_feed
        import hunter_pro_combined
        import hunter_sync
        previous=(facebook_hunter.ROOT,hunter_feed.ROOT,
                  hunter_pro_combined.OUTPUT,hunter_sync.ROOT)
        try:
            facebook_hunter.ROOT=root
            hunter_feed.ROOT=root
            hunter_pro_combined.OUTPUT=root / "hunter_pro_combined.json"
            hunter_sync.ROOT=root
            ranked=facebook_hunter.find_candidates(now=now, limit=30)
            selected=ranked["candidates"]
            # Only high-quality records are sent before lower-confidence
            # manual research items; low-signal seller details are never
            # promoted to confirmed margin estimates.
            good=sum(1 for r in selected if r["score"]>=65)
            with_km=sum(1 for r in selected if r["mileage_km"] is not None)
            private=sum(1 for r in selected
                        if r["seller_classification"]=="particulier")
            with_photo=sum(1 for r in selected if r["photo_urls"])
            print("Saved Facebook provider records: "+str(len(entries)))
            print("Trade-filtered shortlisted: "+str(len(selected)))
            print("Strong research candidates: "+str(good))
            print("Shortlist with known km: "+str(with_km))
            print("Shortlist private seller confirmed: "+str(private))
            print("Shortlist with photos: "+str(with_photo))
            if not selected:
                print("No AET Pro sync: no supported car brands survived filters.")
                return "no_candidates"
            combined=hunter_pro_combined.write(
                path=root / "hunter_pro_combined.json",now=now)
            facebook=combined["platform_counts"]["facebook"]
            if facebook<1 or facebook>30:
                raise ValueError("Combined app batch not within 30 car limit")
            package=hunter_sync.envelope(
                "facebook-apify",str(env.get("GITHUB_RUN_ID","0")),"1",
                now=now,combined=combined,source_status=status)
            if len(package["scan"]["candidate_ids"]) != facebook:
                raise ValueError("Facebook sync ID count mismatch")
            # The marker must be saved before the outbound request to
            # prevent a re-run from duplicating a possibly accepted batch.
            from public_apify_collector import atomic_json
            atomic_json(root / MARKER,
                        {"attempted_at":now.isoformat(),
                         "records":len(entries),
                         "shortlisted":facebook})
            secure.encrypted_checkpoint(root,file,token)
            delivery=(send or hunter_sync.send)(package,endpoint,token)
            if not isinstance(delivery,dict) or delivery.get("received") is not True:
                raise RuntimeError("AET Pro did not confirm the quality replay")
            print("Existing Facebook selections resynchronized to AET Pro: "+str(facebook))
            return "synced"
        finally:
            (facebook_hunter.ROOT,hunter_feed.ROOT,
             hunter_pro_combined.OUTPUT,hunter_sync.ROOT)=previous
            secure.encrypted_checkpoint(root,file,token)


def main():
    try:
        result=execute()
        return 0 if result in ("synced","stale","no_candidates") else 1
    except Exception as error:
        print("One-time quality replay stopped safely ("+type(error).__name__+
              "). No Apify Actor started or listing details published.")
        return 1


if __name__=="__main__":
    sys.exit(main())
