"""One-shot Facebook production-connection test has no hidden provider usage."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.github import facebook_live_once as once
from ops.github import standalone_hunter_runner as secure

TOKEN = "long-synthetic-sync-token-strong-enough-to-use-for-tests"
SAFE_ENV = {
    "GITHUB_REPOSITORY": once.OWNER,
    "GITHUB_REF": once.REF,
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_WORKFLOW": once.WORKFLOW,
    "AET_ONE_TIME_FACEBOOK_TEST": "I_REQUESTED_ONE_TEST",
    "AET_FACEBOOK_ENABLED": "true",
    "APIFY_TOKEN": "synthetic-no-real-apify-token",
    "AET_PRO_SYNC_URL": "https://aet-cars-pro.eltakech100.workers.dev/api/integrations/hunter/sync",
    "AET_HUNTER_SYNC_TOKEN": TOKEN,
}


class FacebookLiveOnceTests(unittest.TestCase):
    def test_exact_owner_main_and_first_attempt_only(self):
        for key, val in [
            ("GITHUB_REPOSITORY", "evil/fork"),
            ("GITHUB_REF", "refs/heads/feature"),
            ("GITHUB_EVENT_NAME", "schedule"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_WORKFLOW", "not authorized"),
            ("AET_ONE_TIME_FACEBOOK_TEST", ""),
            ("AET_FACEBOOK_ENABLED", "false"),
        ]:
            with self.subTest(key=key), self.assertRaises(PermissionError):
                once.check_owner({**SAFE_ENV, key: val})

    def test_budget_fixed_and_no_production_approval_bypass(self):
        env = once.check_owner(dict(SAFE_ENV))
        self.assertEqual(env["AET_APIFY_MONTHLY_BUDGET_USD"], "4.0")
        self.assertEqual(env["AET_APIFY_MAX_CHARGE_USD"], "0.06")
        self.assertNotIn("AET_GITHUB_TERMS_APPROVED", env)
        self.assertEqual(once.RUN_CAP, 0.06)

    def test_cannot_spend_without_recoverable_state(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**SAFE_ENV, "AET_ENCRYPTED_STATE_PATH": str(Path(d) / "missing")}
            with self.assertRaisesRegex(RuntimeError, "cache unavailable"):
                once.execute(env, lambda *_a, **_kw: "synced")

    def test_explicit_encrypted_cache_restored_and_new_reservation_saved(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.enc"
            state = {"schema": "aet.hunter.state.v1", "files": {
                "apify_budget.json": {"month": "2026-10", "calls": 3,
                                      "reserved_usd": 0.18},
            }}
            target.write_bytes(secure.seal(state, TOKEN))
            def fake_runner(platform, env, persist, reload):
                self.assertEqual(platform, "facebook")
                self.assertEqual(env["AET_FACEBOOK_ENABLED"], "true")
                root = Path(env["AET_HUNTER_STATE_DIR"])
                prior = json.loads((root / "apify_budget.json").read_text())
                self.assertEqual(prior["calls"], 3)
                prior["calls"] = 4
                (root / "apify_budget.json").write_text(json.dumps(prior))
                (root / "apify_status.json").write_text(json.dumps({
                    "status": "ok", "received": 7, "accepted": 3,
                }))
                persist()
                return "synced"
            env = {**SAFE_ENV, "AET_ENCRYPTED_STATE_PATH": str(target)}
            self.assertTrue(once.execute(env, fake_runner))
            after = secure.unseal(target.read_bytes(), TOKEN)["files"]
            self.assertEqual(after["apify_budget.json"]["calls"], 4)
            self.assertEqual(after["apify_status.json"]["accepted"], 3)

    def test_failure_keeps_reserved_budget_encrypted(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.enc"
            target.write_bytes(secure.seal({
                "schema": "aet.hunter.state.v1", "files": {}}, TOKEN))
            def fail(platform, env, persist, reload):
                root = Path(env["AET_HUNTER_STATE_DIR"])
                (root / "apify_budget.json").write_text(
                    json.dumps({"month": "2026-10", "calls": 1,
                                "reserved_usd": 0.06}))
                raise RuntimeError("provider failed")
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                once.execute({**SAFE_ENV, "AET_ENCRYPTED_STATE_PATH": str(target)},
                             fail)
            budget = secure.unseal(target.read_bytes(), TOKEN)["files"]["apify_budget.json"]
            self.assertEqual(budget["calls"], 1)


if __name__ == "__main__":
    unittest.main()
