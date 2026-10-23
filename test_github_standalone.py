"""Synthetic, offline tests of the self-contained public GitHub scanner."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.github import standalone_hunter_runner as gh

TOKEN = "strong-random-sync-placeholder-aaaaaaaaaaaaaaaaaaaaaaaaaaa"
ENV = {
    "AET_PUBLIC_HUNTER_ENABLED": "true",
    "AET_GITHUB_TERMS_APPROVED": "true",
    "GITHUB_REPOSITORY": gh.OWNER,
    "GITHUB_REF": gh.MAIN,
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "AET_HUNTER_PLATFORM": "marktplaats",
    "AET_PRO_SYNC_URL": "https://aet-cars-pro.eltakech100.workers.dev/api/integrations/hunter/sync",
    "AET_HUNTER_SYNC_TOKEN": TOKEN,
}


class GithubStandaloneTests(unittest.TestCase):
    def test_owner_and_event_permissions(self):
        for name, value in (
            ("AET_PUBLIC_HUNTER_ENABLED", "false"),
            ("AET_GITHUB_TERMS_APPROVED", "false"),
            ("GITHUB_REPOSITORY", "random/fork"),
            ("GITHUB_REF", "refs/heads/feature"),
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("GITHUB_EVENT_NAME", "push"),
        ):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                gh.authorize({**ENV, name: value})

    def test_first_push_cannot_start_production_even_with_bootstrap(self):
        with self.assertRaises(PermissionError):
            gh.authorize({**ENV, "GITHUB_EVENT_NAME": "push",
                          "AET_STATE_BOOTSTRAP": "true"})

    def test_missing_approval_and_enabled_flags_are_fail_closed(self):
        for updates in ({ "AET_GITHUB_TERMS_APPROVED": "" },
                        { "AET_PUBLIC_HUNTER_ENABLED": "" },
                        { "AET_GITHUB_TERMS_APPROVED": "false" }):
            with self.subTest(updates=updates), self.assertRaises(PermissionError):
                gh.authorize({**ENV, **updates})

    def test_no_private_git_auth_needed(self):
        self.assertEqual(gh.authorize(dict(ENV)), "marktplaats")
        self.assertNotIn("AET_GITHUB_TOKEN", ENV)

    def test_round_trip_and_tamper_rejection(self):
        data = {"schema": "aet.hunter.state.v1", "files": {
            "marktplaats_seen.json": {"https://example.invalid/v/1234": 17300000},
            "apify_budget.json": {"month": "2026-10", "calls": 5, "reserved_usd": 0.5}
        }}
        blob = gh.seal(data, TOKEN)
        self.assertNotIn(b"example.invalid", blob)
        self.assertEqual(gh.unseal(blob, TOKEN), data)
        corrupted = bytearray(blob)
        corrupted[-2] ^= 1
        with self.assertRaises(ValueError):
            gh.unseal(bytes(corrupted), TOKEN)
        with self.assertRaises(ValueError):
            gh.unseal(blob, "rotated-" + TOKEN)
        with self.assertRaises(ValueError):
            gh.unseal(b"not encrypted", TOKEN)

    def test_unapproved_state_names_rejected(self):
        with self.assertRaises(ValueError):
            gh.seal({"schema": "aet.hunter.state.v1",
                     "files": {"../../unsafe.json": {}}}, TOKEN)

    def test_schedule_refuses_to_run_without_persistent_state(self):
        with tempfile.TemporaryDirectory() as scratch:
            env = {**ENV, "GITHUB_EVENT_NAME": "schedule",
                   "AET_STATE_BOOTSTRAP": "true",
                   "AET_ENCRYPTED_STATE_PATH": str(Path(scratch) / "sealed.bin")}
            with self.assertRaisesRegex(RuntimeError, "Encrypted state missing"):
                gh.execute(env=env, platform_runner=lambda *_a, **_k: "synced")

    def test_manual_bootstrap_marktplaats_creates_ciphertext_and_syncs(self):
        with tempfile.TemporaryDirectory() as scratch:
            cache = Path(scratch) / "sealed.bin"
            env = {**ENV, "AET_STATE_BOOTSTRAP": "true",
                   "AET_ENCRYPTED_STATE_PATH": str(cache)}
            observed = []
            def stub(platform, env, persist, reload):
                observed.append(platform)
                self.assertEqual(env["AET_MODAL_LIVE_ENABLED"], "I_ACCEPT_LIVE_SCANS")
                root = Path(env["AET_HUNTER_STATE_DIR"])
                (root / "marktplaats_status.json").write_text(
                    json.dumps({"scan_health": "ok"}), encoding="utf-8")
                persist()
                return "synced"
            self.assertTrue(gh.execute(env=env, platform_runner=stub))
            self.assertEqual(observed, ["marktplaats"])
            self.assertEqual(gh.unseal(cache.read_bytes(), TOKEN)["files"]
                             ["marktplaats_status.json"]["scan_health"], "ok")

    def test_normal_run_restores_existing_budget_and_history(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "sealed.bin"
            state = {"schema": "aet.hunter.state.v1", "files": {
                "apify_budget.json": {"month": "2026-10", "calls": 51, "reserved_usd": 5.10}}}
            path.write_bytes(gh.seal(state, TOKEN))
            def stub(platform, env, persist, reload):
                root = Path(env["AET_HUNTER_STATE_DIR"])
                self.assertEqual(json.loads((root / "apify_budget.json").read_text())["calls"], 51)
                return "synced"
            self.assertTrue(gh.execute(env={**ENV, "AET_ENCRYPTED_STATE_PATH": str(path)},
                                       platform_runner=stub))
            self.assertEqual(gh.unseal(path.read_bytes(), TOKEN)["files"]
                             ["apify_budget.json"]["calls"], 51)

    def test_unhealthy_run_still_encrypts_restriction(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "sealed.bin"
            env = {**ENV, "AET_STATE_BOOTSTRAP": "true",
                   "AET_ENCRYPTED_STATE_PATH": str(path)}
            def stub(platform, env, persist, reload):
                root = Path(env["AET_HUNTER_STATE_DIR"])
                (root / "marktplaats_cooldown.json").write_text(
                    json.dumps({"__global_backoff_until__": 2000000000}),
                    encoding="utf-8")
                raise RuntimeError("blocked")
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                gh.execute(env=env, platform_runner=stub)
            self.assertEqual(gh.unseal(path.read_bytes(), TOKEN)["files"]
                             ["marktplaats_cooldown.json"]["__global_backoff_until__"], 2000000000)

    def test_facebook_cannot_bootstrap_missing_budget(self):
        with tempfile.TemporaryDirectory() as scratch:
            env = {**ENV, "AET_HUNTER_PLATFORM": "facebook",
                   "AET_FACEBOOK_ENABLED": "true",
                   "AET_APIFY_MONTHLY_BUDGET_USD": "1.00",
                   "AET_APIFY_MAX_CHARGE_USD": "0.10",
                   "APIFY_TOKEN": "safe-token-value-1234567",
                   "AET_STATE_BOOTSTRAP": "true",
                   "AET_ENCRYPTED_STATE_PATH": str(Path(scratch) / "sealed.bin")}
            with self.assertRaisesRegex(RuntimeError, "Encrypted state missing"):
                gh.execute(env=env, platform_runner=lambda *_a, **_k: "synced")


if __name__ == "__main__":
    unittest.main()
