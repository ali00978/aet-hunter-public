"""Regression tests for trade-quality replay without paid provider calls."""
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from ops.github import facebook_quality_replay as replay
from ops.github import standalone_hunter_runner as secure

KEY="synthetic-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
BASE={
    "GITHUB_REPOSITORY":replay.OWNER,
    "GITHUB_REF":"refs/heads/main",
    "GITHUB_EVENT_NAME":"push",
    "GITHUB_RUN_ATTEMPT":"1",
    "GITHUB_WORKFLOW":replay.WORKFLOW,
    "AET_FACEBOOK_QUALITY_REPLAY":"ONE_SAVED_BATCH",
    "GITHUB_RUN_ID":"12345678",
    "AET_HUNTER_SYNC_TOKEN":KEY,
    "AET_PRO_SYNC_URL":
       "https://aet-cars-pro.eltakech100.workers.dev/api/integrations/hunter/sync",
}

class OneBatchReplayTests(unittest.TestCase):
    def test_owner_guards_and_no_apify_credential_needed(self):
        self.assertEqual(replay.check_owner(BASE)[1],KEY)
        for k,v in (("GITHUB_REPOSITORY","evil/fork"),
                    ("GITHUB_REF","refs/heads/dev"),
                    ("GITHUB_RUN_ATTEMPT","2"),
                    ("AET_FACEBOOK_QUALITY_REPLAY",""),
                    ("GITHUB_WORKFLOW","not-replay")):
            with self.subTest(k=k),self.assertRaises(PermissionError):
                replay.check_owner({**BASE,k:v})

    def sample(self, dir, old=False):
        now=datetime.now(timezone.utc)
        timestamp=(now-timedelta(hours=5 if old else 1)).isoformat()
        records=[{"url":"https://www.facebook.com/marketplace/item/123456789/",
                 "title":"Opel Corsa 2012", "price":950,
                 "mileage_km":120000,"seller_type":"particulier",
                 "photoUrl":"https://scontent.xx.fbcdn.net/v/a.jpg",
                 "listedAt":timestamp,"first_seen_at":timestamp}]
        cache=Path(dir)/"state.enc"
        cache.write_bytes(secure.seal({
            "schema":"aet.hunter.state.v1",
            "files":{
                "apify_kandidaten.json":records,
                "apify_status.json":{"status":"ok","checked_at":timestamp},
                "marktplaats_status.json":{"scan_health":"ok","checked_at":timestamp},
                "marktplaats_proefresultaten.json":[],
                "marktplaats_handmatig_controleren.json":[],
            }
        },KEY))
        return cache

    def test_fresh_snapshot_replays_same_candidates_once(self):
        with tempfile.TemporaryDirectory() as d:
            cache=self.sample(d)
            calls=[]
            def send(payload,endpoint,token):
                self.assertEqual(token,KEY)
                self.assertEqual(payload["scan"]["platform"],"facebook")
                self.assertEqual(payload["batch"]["platform_counts"]["facebook"],1)
                calls.append(payload)
                return {"received":True}
            env={**BASE,"AET_ENCRYPTED_STATE_PATH":str(cache)}
            self.assertEqual(replay.execute(env,send=send),"synced")
            self.assertEqual(len(calls),1)
            stored=secure.unseal(cache.read_bytes(),KEY)["files"]
            self.assertIn(replay.MARKER,stored)
            with self.assertRaisesRegex(ValueError,"already executed"):
                replay.execute(env,send=send)
            self.assertEqual(len(calls),1)

    def test_stale_does_not_falsely_claim_new_scan(self):
        with tempfile.TemporaryDirectory() as d:
            cache=self.sample(d,old=True)
            env={**BASE,"AET_ENCRYPTED_STATE_PATH":str(cache)}
            self.assertEqual(replay.execute(env,send=lambda *_:None),"stale")
            self.assertNotIn(replay.MARKER,
                secure.unseal(cache.read_bytes(),KEY)["files"])

    def test_no_private_details_in_logs_or_public_repo(self):
        self.assertNotIn("APIFY_TOKEN",BASE)


if __name__=="__main__":
    unittest.main()
