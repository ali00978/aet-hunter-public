"""One idempotent AET PRO handoff combining Marktplaats and Facebook Hunter.

Backward-compatible hunter_pro_export.json (Marktplaats v1) is untouched.
New combined v2 is offline, committed to private GitHub, not sent to PRO.
"""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from facebook_hunter import find_candidates, for_pro
from hunter_pro_export import build_payload as marktplaats_payload

SCHEMA="aet-pro.hunter.v2"
OUTPUT=Path(os.environ.get("AET_HUNTER_STATE_DIR", Path(__file__).resolve().parent))/"hunter_pro_combined.json"

def combine(mp_feed,facebook_feed,now=None,known_mp_fingerprints=None,mp_limit=30):
    now=now or datetime.now(timezone.utc)
    mp=marktplaats_payload(mp_feed,known_fingerprints=known_mp_fingerprints,limit=mp_limit)
    if not isinstance(facebook_feed,dict) or not isinstance(facebook_feed.get("candidates"),list):
        raise ValueError("Facebook Hunter feed invalid")
    fb_raw=facebook_feed["candidates"][:30]
    valid=[]
    seen=set()
    for row in fb_raw:
        if not isinstance(row,dict) or not str(row.get("id") or "").startswith("facebook:"):
            continue
        if row["id"] in seen:continue
        seen.add(row["id"])
        valid.append(row)
    stale=[]
    for record in valid:
        try:observed=datetime.fromisoformat(record["checked_at"])
        except (ValueError,TypeError,KeyError):observed=None
        archive=(observed is None or observed.tzinfo is None or
                 now-observed>timedelta(hours=48) or observed>now+timedelta(hours=3))
        stale.extend(for_pro([record],archived=archive))
    items=mp["candidates"]+stale
    if len({r["id"] for r in items})!=len(items):
        raise ValueError("Duplicated cross-source ID")
    fingerprint=hashlib.sha256(json.dumps(sorted((r["id"],r["content_hash"]) for r in items),
                                  separators=(",",":")).encode()).hexdigest()
    return {
        "schema":SCHEMA,"source":"aet-cars-ai-hunter",
        "integration_mode":"offline_export_no_upload",
        "generated_at":now.isoformat(),
        "revision":fingerprint,
        "count":len(items),
        "platform_counts":{"marktplaats":len(mp["candidates"]),"facebook":len(stale)},
        "platform_status":{
            "marktplaats":{"scan_checked_at":mp["scan_checked_at"],"archived":mp["archived"]},
            "facebook":{"provider_scans":facebook_feed.get("sources",[]),
                        "status":"research_only_unverified_prices"},
        },
        "candidates":items,
    }
def build(now=None,mp_feed=None,known_mp_fingerprints=None,mp_limit=30,
          known_fb_ids=None):
    from hunter_feed import build as build_mp_feed
    now=now or datetime.now(timezone.utc)
    return combine(
        mp_feed if mp_feed is not None else build_mp_feed(now=now),
        find_candidates(now=now,known_delivery_ids=known_fb_ids),now=now,
        known_mp_fingerprints=known_mp_fingerprints,mp_limit=mp_limit)
def write(path=OUTPUT,now=None,mp_feed=None,known_mp_fingerprints=None,mp_limit=30,
          known_fb_ids=None):
    result=build(now=now,mp_feed=mp_feed,known_mp_fingerprints=known_mp_fingerprints,
                 mp_limit=mp_limit,known_fb_ids=known_fb_ids)
    destination=Path(path)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile("w",dir=destination.parent,
               prefix="hunter-combined-",suffix=".tmp",delete=False,encoding="utf-8") as f:
            temporary=Path(f.name)
            json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        os.replace(temporary,destination)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    return result
if __name__=="__main__":
    result=write()
    print("AET PRO combined feed:",result["platform_counts"],"total:",result["count"])
