"""The Modal wrapper never runs live requests in public GitHub Actions."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ops.modal import legacy_bridge as bridge


class BridgeTests(unittest.TestCase):
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
