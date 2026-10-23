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

    def test_marktplaats_credentials_do_not_need_apify(self):
        env = {
            "AET_GITHUB_TOKEN": "placeholder-token",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
            "AET_HUNTER_SYNC_TOKEN": "SYNTHETIC-LONG-SYNC-TOKEN-PLACEHOLDER",
        }
        bridge.verify_credentials(env, platform="marktplaats")
        with self.assertRaisesRegex(RuntimeError, "APIFY_TOKEN"):
            bridge.verify_credentials(env, platform="facebook")

    def test_platform_selection_isolated_from_other_scan(self):
        env = {
            "AET_MODAL_LIVE_ENABLED": "I_ACCEPT_LIVE_SCANS",
            "AET_GITHUB_TOKEN": "placeholder-token",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
            "AET_HUNTER_SYNC_TOKEN": "SYNTHETIC-LONG-SYNC-TOKEN-PLACEHOLDER",
            "APIFY_TOKEN": "synthetic-only",
        }
        with patch.object(bridge, "prepare_clone", return_value=Path("/tmp/synthetic")), \
                patch.object(bridge, "scan_marktplaats") as mp, \
                patch.object(bridge, "scan_facebook") as fb:
            bridge.run_original_hunter(env, platform="marktplaats")
            mp.assert_called_once()
            fb.assert_not_called()
            mp.reset_mock()
            bridge.run_original_hunter(env, platform="facebook")
            mp.assert_not_called()
            fb.assert_called_once()

    def test_facebook_monthly_limit_skips_without_api_or_sync(self):
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with patch.object(bridge, "get_status", return_value={"month": month, "calls": 100}), \
                patch.object(bridge, "execute") as execute, \
                patch.object(bridge, "publish_snapshots") as publish:
            bridge.scan_facebook(Path("/tmp/unused"), {})
            execute.assert_not_called()
            publish.assert_not_called()

    def test_modal_facebook_runs_twice_not_every_two_hours(self):
        # The single Modal job avoids cross-platform snapshot push races.
        source = (Path(__file__).parent / "ops/modal/hunter_modal.py").read_text(
            encoding="utf-8")
        self.assertIn('modal.Cron("17 */2 * * *", timezone="UTC")', source)
        self.assertIn('now.hour in (2, 14)', source)
        self.assertIn('else "marktplaats"', source)
        self.assertIn('run_original_hunter(platform=platform)', source)

    def test_non_github_runner_generates_unique_scan_id(self):
        env = {
            "AET_MODAL_LIVE_ENABLED": "I_ACCEPT_LIVE_SCANS",
            "AET_GITHUB_TOKEN": "fake-github",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
            "AET_HUNTER_SYNC_TOKEN": "long-enough-dummy-sync-secret-1234",
        }
        observed = []
        with patch.object(bridge, "prepare_clone", return_value=Path("/tmp/dummy-hunter")), \
                patch.object(bridge, "scan_marktplaats",
                             side_effect=lambda _root, e: observed.append(
                                 (e["GITHUB_RUN_ID"], e["GITHUB_RUN_ATTEMPT"]))), \
                patch.object(bridge.time, "time_ns", side_effect=(1700000000000000001,
                                                                  1700000000000000002)):
            bridge.run_original_hunter(env, platform="marktplaats")
            bridge.run_original_hunter(env, platform="marktplaats")
        self.assertEqual(observed, [("1700000000000000001", "1"),
                                    ("1700000000000000002", "1")])

    def test_github_run_id_kept_when_available(self):
        env = {
            "AET_MODAL_LIVE_ENABLED": "I_ACCEPT_LIVE_SCANS",
            "AET_GITHUB_TOKEN": "fake-github",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
            "AET_HUNTER_SYNC_TOKEN": "long-enough-dummy-sync-secret-1234",
            "GITHUB_RUN_ID": "9876543", "GITHUB_RUN_ATTEMPT": "2",
        }
        with patch.object(bridge, "prepare_clone", return_value=Path("/tmp/dummy")), \
                patch.object(bridge, "scan_marktplaats") as scanner, \
                patch.object(bridge.time, "time_ns") as clock:
            bridge.run_original_hunter(env, platform="marktplaats")
            clock.assert_not_called()
            self.assertEqual(scanner.call_args.args[1]["GITHUB_RUN_ID"], "9876543")
            self.assertEqual(scanner.call_args.args[1]["GITHUB_RUN_ATTEMPT"], "2")

    def test_provider_crash_persists_reserved_quota_without_sync(self):
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            budget = path / "apify_budget.json"
            budget.write_text('{"month":"' + month + '","calls":31}', encoding="utf-8")
            run_commands = []
            def simulate(args, *_args, **_kwargs):
                run_commands.append(args)
                if "apify_test.py" in args:
                    budget.write_text('{"month":"' + month + '","calls":32}', encoding="utf-8")
                    raise RuntimeError("simulated provider interruption")
                return 0
            with patch.object(bridge, "execute", side_effect=simulate), \
                    patch.object(bridge, "publish_snapshots") as published:
                with self.assertRaisesRegex(RuntimeError, "simulated provider"):
                    bridge.scan_facebook(path, {})
            published.assert_called_once_with(
                path, {}, ("apify_budget.json",),
                "Preserve Facebook provider call reservation after failure")
            self.assertEqual(__import__("json").loads(budget.read_text())["calls"], 32)
            self.assertFalse(any("hunter_sync.py" in x for x in run_commands))

    def test_export_failure_after_provider_reserves_budget_without_sync(self):
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget = root / "apify_budget.json"
            budget.write_text('{"month":"' + month + '","calls":31}', encoding="utf-8")
            commands = []
            def simulate(args, *_args, **_kwargs):
                commands.append(args)
                if "apify_test.py" in args:
                    budget.write_text('{"month":"' + month + '","calls":32}', encoding="utf-8")
                if "hunter_pro_combined.py" in args:
                    raise RuntimeError("simulated combined export error")
                return 0
            with patch.object(bridge, "execute", side_effect=simulate), \
                    patch.object(bridge, "publish_snapshots") as publish:
                with self.assertRaisesRegex(RuntimeError, "combined export"):
                    bridge.scan_facebook(root, {})
            publish.assert_called_once_with(
                root, {}, ("apify_budget.json",),
                "Preserve Facebook provider call reservation after failure")
            self.assertEqual(__import__("json").loads(budget.read_text())["calls"], 32)
            self.assertFalse(any("hunter_sync.py" in args for args in commands))

    def test_facebook_state_push_failure_recovers_budget_without_rescan(self):
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget = root / "apify_budget.json"
            budget.write_text('{"month":"' + month + '","calls":4}', encoding="utf-8")
            commands = []
            def simulate(args, *_args, **_kwargs):
                commands.append(args)
                if "apify_test.py" in args:
                    budget.write_text('{"month":"' + month + '","calls":5}', encoding="utf-8")
                return 0
            with patch.object(bridge, "execute", side_effect=simulate), \
                    patch.object(bridge, "publish_snapshots",
                                 side_effect=[RuntimeError("simulated state push failure"), None]) as publish:
                with self.assertRaisesRegex(RuntimeError, "state push failure"):
                    bridge.scan_facebook(root, {})
            self.assertEqual(publish.call_count, 2)
            self.assertEqual(publish.call_args_list[-1].args[2], ("apify_budget.json",))
            self.assertEqual(sum("apify_test.py" in args for args in commands), 1)
            self.assertFalse(any("hunter_sync.py" in args for args in commands))

    def test_invalid_monthly_counter_never_calls_provider(self):
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with patch.object(bridge, "get_status", return_value={"month": month, "calls": "broken"}), \
                patch.object(bridge, "execute") as execute:
            with self.assertRaisesRegex(RuntimeError, "invalid monthly call counter"):
                bridge.scan_facebook(Path("/tmp/missing"), {})
            execute.assert_not_called()

    def test_corrupt_budget_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "apify_budget.json").write_text("not-json", encoding="utf-8")
            with patch.object(bridge, "execute") as execute:
                with self.assertRaisesRegex(RuntimeError, "invalid budget"):
                    bridge.scan_facebook(Path(tmp), {})
                execute.assert_not_called()

    def test_disabled_platform_does_not_clone(self):
        with patch.object(bridge, "prepare_clone") as clone:
            bridge.run_original_hunter({"AET_MODAL_LIVE_ENABLED": "false"}, platform="facebook")
            clone.assert_not_called()



if __name__ == "__main__":
    unittest.main()
