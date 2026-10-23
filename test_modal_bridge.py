"""The Modal wrapper never runs live requests in public GitHub Actions."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ops.modal import legacy_bridge as bridge


class BridgeTests(unittest.TestCase):
    def test_scanner_never_inherits_provider_or_git_credentials(self):
        env = {
            "AET_GITHUB_TOKEN": "sample-github-secret",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
            "AET_HUNTER_SYNC_TOKEN": "sample-hunter-secret",
            "APIFY_TOKEN": "sample-facebook-token",
            "GIT_ASKPASS": "/tmp/private-askpass",
            "MAX_BUY_PRICE": "3000",
        }
        for args in (["python", "scanner.py"],
                     ["python", "-m", "unittest", "-q", "test_hunter.py"],
                     ["python", "hunter_feed.py"]):
            with self.subTest(args=args):
                child = bridge.stage_environment(args, env)
                self.assertEqual(child["MAX_BUY_PRICE"], "3000")
                for name in ("AET_GITHUB_TOKEN", "AET_PRO_SYNC_URL",
                             "AET_HUNTER_SYNC_TOKEN", "APIFY_TOKEN",
                             "GIT_ASKPASS"):
                    self.assertNotIn(name, child)

    def test_git_only_receives_git_credentials(self):
        env = {
            "AET_GITHUB_TOKEN": "fake-private-github-secret",
            "GIT_ASKPASS": "/tmp/private-askpass",
            "APIFY_TOKEN": "no-facebook-leak",
            "AET_HUNTER_SYNC_TOKEN": "no-sync-leak",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
        }
        child = bridge.stage_environment(["git", "fetch", "origin", "main"], env)
        self.assertEqual(child["AET_GITHUB_TOKEN"], env["AET_GITHUB_TOKEN"])
        self.assertEqual(child["GIT_ASKPASS"], env["GIT_ASKPASS"])
        self.assertNotIn("APIFY_TOKEN", child)
        self.assertNotIn("AET_HUNTER_SYNC_TOKEN", child)

    def test_facebook_and_sync_tokens_are_separated(self):
        env = {
            "AET_GITHUB_TOKEN": "private-github-token",
            "APIFY_TOKEN": "private-apify-token",
            "AET_HUNTER_SYNC_TOKEN": "private-sync-token",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
        }
        facebook = bridge.stage_environment(["python", "apify_test.py"], env)
        self.assertEqual(facebook["APIFY_TOKEN"], env["APIFY_TOKEN"])
        self.assertNotIn("AET_GITHUB_TOKEN", facebook)
        self.assertNotIn("AET_HUNTER_SYNC_TOKEN", facebook)
        sync = bridge.stage_environment(
            ["python", "hunter_sync.py", "facebook-apify", "--send"], env)
        self.assertEqual(sync["AET_HUNTER_SYNC_TOKEN"], env["AET_HUNTER_SYNC_TOKEN"])
        self.assertEqual(sync["AET_PRO_SYNC_URL"], env["AET_PRO_SYNC_URL"])
        self.assertNotIn("AET_GITHUB_TOKEN", sync)
        self.assertNotIn("APIFY_TOKEN", sync)

    def test_disabled_by_default(self):
        with patch.object(bridge, "prepare_clone") as clone:
            bridge.run_original_hunter({"AET_MODAL_LIVE_ENABLED": "0"})
            clone.assert_not_called()

    def test_missing_secret_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "AET_GITHUB_TOKEN"):
            bridge.verify_credentials({})

    def test_private_sync_must_be_https(self):
        env = {key: "correct-length-fake-secret-001" for key in bridge.NEEDED}
        env["AET_PRO_SYNC_URL"] = "http://example.test"
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            bridge.verify_credentials(env)

    def test_refuse_misconfigured_private_sync_url(self):
        valid = {
            key: "example-placeholder-secret-with-required-length"
            for key in bridge.NEEDED
        }
        for bad_url in (
            "https://user:pass@example.test/api/integrations/hunter/sync",
            "https://example.test/api/integrations/hunter/sync?token=leak",
            "https://example.test/other-endpoint",
            "https://example.test:8080/api/integrations/hunter/sync",
        ):
            with self.subTest(endpoint=bad_url):
                env = {**valid, "AET_PRO_SYNC_URL": bad_url}
                with self.assertRaisesRegex(RuntimeError, "HTTPS Hunter sync endpoint"):
                    bridge.verify_credentials(env)

    def test_accept_private_https_sync_endpoint(self):
        env = {
            key: "example-placeholder-secret-with-required-length"
            for key in bridge.NEEDED
        }
        env["AET_PRO_SYNC_URL"] = "https://example.test/api/integrations/hunter/sync"
        bridge.verify_credentials(env)

    def test_only_json_state_may_be_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                bridge.publish_snapshots(Path(tmp), {}, ("../../.env",), "invalid")

    def test_marktplaats_success_uses_original_sync(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(bridge, "execute", return_value=0) as command, \
                patch.object(bridge, "publish_snapshots"), \
                patch.object(bridge, "get_status", return_value={"scan_health": "ok"}):
            bridge.scan_marktplaats(Path(tmp), {})
            self.assertTrue(any("marktplaats" in c.args[0] and "hunter_sync.py" in c.args[0]
                                for c in command.call_args_list))

    def test_marktplaats_blocked_never_syncs(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(bridge, "execute", return_value=0) as command, \
                patch.object(bridge, "publish_snapshots"), \
                patch.object(bridge, "get_status", return_value={"scan_health": "blocked"}):
            with self.assertRaisesRegex(RuntimeError, "not healthy"):
                bridge.scan_marktplaats(Path(tmp), {})
            self.assertFalse(any("hunter_sync.py" in c.args[0] for c in command.call_args_list))

    def test_facebook_quota_never_syncs(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(bridge, "execute", return_value=0) as command, \
                patch.object(bridge, "publish_snapshots"), \
                patch.object(bridge, "get_status", return_value={"status": "monthly_request_cap_reached"}):
            with self.assertRaisesRegex(RuntimeError, "not healthy"):
                bridge.scan_facebook(Path(tmp), {})
            self.assertFalse(any("hunter_sync.py" in c.args[0] for c in command.call_args_list))

    def test_facebook_success_uses_original_sync(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(bridge, "execute", return_value=0) as command, \
                patch.object(bridge, "publish_snapshots"), \
                patch.object(bridge, "get_status", return_value={"status": "ok"}):
            bridge.scan_facebook(Path(tmp), {})
            self.assertTrue(any("facebook-apify" in c.args[0] and "hunter_sync.py" in c.args[0]
                                for c in command.call_args_list))


if __name__ == "__main__":
    unittest.main()
