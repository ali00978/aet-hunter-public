"""Network-blocked tests for exact-run Apify GET recovery."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ops.github import facebook_recover_existing as rescue
from ops.github import standalone_hunter_runner as secure

TOKEN = "good-synthetic-secret-this-is-not-a-real-token"
ACTOR_ID = "Aha1234AaBabcD"
RUN_ID = "Abcd9876RunX12"
DATASET_ID = "XyZ123456789Datas"
ENV = {
    "GITHUB_REPOSITORY": rescue.OWNER,
    "GITHUB_REF": rescue.REF,
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_WORKFLOW": rescue.WORKFLOW,
    "AET_READONLY_RECOVER": "EXISTING_RUN_ONLY",
    "APIFY_TOKEN": "synthetic-token-123456",
    "AET_PRO_SYNC_URL":
        "https://aet-cars-pro.eltakech100.workers.dev/api/integrations/hunter/sync",
    "AET_HUNTER_SYNC_TOKEN": TOKEN,
}


def mock_get(url, token):
    if url == rescue.ACTOR_URL:
        return {"data":{"id":ACTOR_ID}}
    if url.startswith(rescue.RUNS_URL + "?"):
        return {"data":{"items":[{
            "id":RUN_ID, "actId":ACTOR_ID,
            "status":"SUCCEEDED", "startedAt":"2026-10-23T16:22:30.000Z"}]}}
    if url == rescue.RUNS_URL + "/" + RUN_ID:
        return {"data":{"id":RUN_ID,"actId":ACTOR_ID,
                        "status":"SUCCEEDED","defaultDatasetId":DATASET_ID}}
    if url.startswith("https://api.apify.com/v2/datasets/" + DATASET_ID + "/items?"):
        return [{"url":"https://www.facebook.com/marketplace/item/123456789/",
                 "title":"Opel Corsa 2012 1.2", "price":950,
                 "listedAt":"2026-10-23T16:22:00.000Z",
                 "photoUrl":"https://scontent.fbcdn.net/v/photo.jpg",
                 "sellerType":"PRIVATE_SELLER"}]
    raise AssertionError("Unexpected recovery API URL " + url)


class ReadOnlyRecoveryTests(unittest.TestCase):
    def test_only_owner_main_once(self):
        for name, value in (
            ("GITHUB_REPOSITORY", "evil/repo"),
            ("GITHUB_REF", "refs/heads/other"),
            ("GITHUB_EVENT_NAME", "workflow_dispatch"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_WORKFLOW", "other"),
            ("AET_READONLY_RECOVER", ""),
        ):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                rescue.authorize({**ENV, name:value})

    def test_no_external_token_leak(self):
        for destination in (
            "https://evil.example",
            "http://api.apify.com/v2/actor-runs",
            "https://api.apify.com/v2/actors/another-actor",
            "https://api.apify.com/v2/datasets/foo/items",
        ):
            with self.assertRaises(ValueError):
                rescue.get_api(destination, "secret")

    def test_select_only_exact_actor_time_and_run(self):
        x = rescue.select_run("synthetic", getter=mock_get)
        self.assertEqual(x["id"], RUN_ID)
        def malicious(url, token):
            if url.startswith(rescue.RUNS_URL + "?"):
                return {"data":{"items":[{
                    "id":RUN_ID,"actId": "some-other-actor-id",
                    "startedAt": "2026-10-23T16:22:30.000Z"}]}}
            return mock_get(url, token)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            rescue.select_run("synthetic", getter=malicious)

    def test_import_existing_run_with_no_new_actor_post(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.enc"
            target.write_bytes(secure.seal({
                "schema":"aet.hunter.state.v1",
                "files":{}}, TOKEN))
            captures = []
            def sync(batch,endpoint,token):
                self.assertEqual(token,TOKEN)
                self.assertEqual(batch["scan"]["platform"],"facebook")
                self.assertEqual(batch["batch"]["platform_counts"]["facebook"],1)
                captures.append(batch)
                return {"received":True}
            state = rescue.execute(
                {**ENV,"AET_ENCRYPTED_STATE_PATH":str(target)},
                getter=mock_get, sync_fn=sync)
            self.assertEqual(state,"synced")
            self.assertEqual(len(captures),1)
            persisted=secure.unseal(target.read_bytes(),TOKEN)["files"]
            self.assertIn("apify_recovery_once.json",persisted)
            self.assertEqual(len(persisted["apify_kandidaten.json"]),1)
            with self.assertRaisesRegex(ValueError, "already been recovered"):
                rescue.execute({**ENV,"AET_ENCRYPTED_STATE_PATH":str(target)},
                               getter=mock_get,sync_fn=sync)
            self.assertEqual(len(captures),1)

    def test_running_actor_is_not_claimed_as_complete(self):
        def pending(url, token):
            item = mock_get(url, token)
            if url.endswith("/"+RUN_ID):
                item["data"]["status"]="RUNNING"
            return item
        self.assertIsNone(rescue.fetch_existing("synthetic",getter=pending))

    def test_no_vehicles_no_aet_pro_import(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/"state.enc"
            target.write_bytes(secure.seal({
                "schema":"aet.hunter.state.v1","files":{}},TOKEN))
            def no_rows(url,token):
                if "/datasets/" in url:
                    return []
                return mock_get(url,token)
            result=rescue.execute(
                {**ENV,"AET_ENCRYPTED_STATE_PATH":str(target)},
                getter=no_rows,
                sync_fn=lambda *_a: (_ for _ in ()).throw(
                    AssertionError("No sync should happen")))
            self.assertEqual(result,"no_vehicles")


if __name__=="__main__":
    unittest.main()
