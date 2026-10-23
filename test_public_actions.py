"""Public GitHub wrapper tests: no provider, private repo, or token calls."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.github.public_actions_runner import main


class PublicGithubRunnerTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {"AET_PUBLIC_HUNTER_ENABLED": "false",
                                      "AET_GITHUB_TERMS_APPROVED": "true"}):
            with patch("ops.modal.legacy_bridge.run_original_hunter") as run:
                main()
                run.assert_not_called()

    def test_not_authorized_for_commercial_github_usage(self):
        # A valid enabled flag alone is not enough to launch a live scan.
        with patch.dict(os.environ, {
                "AET_PUBLIC_HUNTER_ENABLED": "true",
                "AET_GITHUB_TERMS_APPROVED": "false",
                "GITHUB_REPOSITORY": "ali00978/aet-hunter-public",
                "GITHUB_REF": "refs/heads/main"}, clear=True):
            with patch("ops.modal.legacy_bridge.run_original_hunter") as run:
                main()
                run.assert_not_called()

    def test_unknown_repo_refused(self):
        with patch.dict(os.environ, {
                "AET_PUBLIC_HUNTER_ENABLED": "true",
                "AET_GITHUB_TERMS_APPROVED": "true",
                "GITHUB_REPOSITORY": "another-user/fork",
                "GITHUB_REF": "refs/heads/main"}):
            with self.assertRaisesRegex(RuntimeError, "unknown GitHub repository"):
                main()

    def test_nonmain_ref_refused(self):
        with patch.dict(os.environ, {
                "AET_PUBLIC_HUNTER_ENABLED": "true",
                "AET_GITHUB_TERMS_APPROVED": "true",
                "GITHUB_REPOSITORY": "ali00978/aet-hunter-public",
                "GITHUB_REF": "refs/heads/feature"}):
            with self.assertRaisesRegex(RuntimeError, "non-main"):
                main()

    def test_missing_secrets_fail_closed(self):
        env = {
            "AET_PUBLIC_HUNTER_ENABLED": "true",
            "AET_GITHUB_TERMS_APPROVED": "true",
            "GITHUB_REPOSITORY": "ali00978/aet-hunter-public",
            "GITHUB_REF": "refs/heads/main",
            "AET_HUNTER_PLATFORM": "marktplaats",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("ops.modal.legacy_bridge.run_original_hunter") as run:
                with self.assertRaisesRegex(RuntimeError, "AET_GITHUB_TOKEN"):
                    main()
                run.assert_not_called()

    def test_correct_setup_calls_same_original_bridge(self):
        env = {
            "AET_PUBLIC_HUNTER_ENABLED": "true",
            "AET_GITHUB_TERMS_APPROVED": "true",
            "GITHUB_REPOSITORY": "ali00978/aet-hunter-public",
            "GITHUB_REF": "refs/heads/main",
            "AET_GITHUB_TOKEN": "example-private-token-25-chars",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
            "AET_HUNTER_SYNC_TOKEN": "fake-synchronization-secret-26",
            "APIFY_TOKEN": "example-private-apify-token",
            "AET_HUNTER_PLATFORM": "marktplaats",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("ops.modal.legacy_bridge.run_original_hunter") as run:
                main()
                run.assert_called_once()
                passed = run.call_args.args[0]
                self.assertEqual(passed["AET_MODAL_LIVE_ENABLED"], "I_ACCEPT_LIVE_SCANS")
                self.assertEqual(passed["AET_GITHUB_TOKEN"], env["AET_GITHUB_TOKEN"])
                self.assertEqual(run.call_args.kwargs["platform"], "marktplaats")

    def test_unselected_platform_is_blocked_even_with_valid_credentials(self):
        env = {
            "AET_PUBLIC_HUNTER_ENABLED": "true",
            "AET_GITHUB_TERMS_APPROVED": "true",
            "GITHUB_REPOSITORY": "ali00978/aet-hunter-public",
            "GITHUB_REF": "refs/heads/main",
            "AET_HUNTER_PLATFORM": "both",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                main()

    def test_marktplaats_does_not_require_apify_secret(self):
        env = {
            "AET_PUBLIC_HUNTER_ENABLED": "true",
            "AET_GITHUB_TERMS_APPROVED": "true",
            "GITHUB_REPOSITORY": "ali00978/aet-hunter-public",
            "GITHUB_REF": "refs/heads/main",
            "AET_HUNTER_PLATFORM": "marktplaats",
            "AET_GITHUB_TOKEN": "fake-write-access-placeholder",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
            "AET_HUNTER_SYNC_TOKEN": "fake-synchronization-secret-26",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("ops.modal.legacy_bridge.run_original_hunter") as run:
                main()
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs["platform"], "marktplaats")

    def test_facebook_still_requires_apify_secret(self):
        env = {
            "AET_PUBLIC_HUNTER_ENABLED": "true",
            "AET_GITHUB_TERMS_APPROVED": "true",
            "GITHUB_REPOSITORY": "ali00978/aet-hunter-public",
            "GITHUB_REF": "refs/heads/main",
            "AET_HUNTER_PLATFORM": "facebook",
            "AET_GITHUB_TOKEN": "fake-write-access-placeholder",
            "AET_PRO_SYNC_URL": "https://example.test/api/integrations/hunter/sync",
            "AET_HUNTER_SYNC_TOKEN": "fake-synchronization-secret-26",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("ops.modal.legacy_bridge.run_original_hunter") as run:
                with self.assertRaisesRegex(RuntimeError, "APIFY_TOKEN"):
                    main()
                run.assert_not_called()

    def test_public_workflow_is_gated_and_uses_encrypted_public_runner(self):
        path = Path(__file__).parent / ".github/workflows/public-original-hunter.yml"
        content = path.read_text(encoding="utf-8")
        self.assertIn("cron: '17 */2 * * *'", content)
        self.assertIn("cron: '47 2,10,18 * * *'", content)
        self.assertIn("AET_APIFY_MONTHLY_BUDGET_USD: '4.65'", content)
        self.assertIn("AET_APIFY_MAX_CHARGE_USD: '0.05'", content)
        self.assertNotIn("vars.AET_APIFY_MONTHLY_BUDGET_USD", content)
        self.assertNotIn("vars.AET_APIFY_MAX_CHARGE_USD", content)
        self.assertIn("inputs.platform == 'facebook'", content)
        self.assertIn("inputs.platform == 'marktplaats'", content)
        mp, fb = content.split("  facebook:", 1)
        self.assertNotIn("secrets.APIFY_TOKEN", mp)
        self.assertIn("secrets.APIFY_TOKEN", fb)
        self.assertIn("AET_HUNTER_PLATFORM: marktplaats", mp)
        self.assertIn("AET_HUNTER_PLATFORM: facebook", fb)
        self.assertIn("group: aet-public-original-hunter", content)
        self.assertIn("vars.AET_PUBLIC_HUNTER_ENABLED == 'true'", content)
        self.assertIn("vars.AET_GITHUB_TERMS_APPROVED == 'true'", content)
        self.assertNotIn("  push:", content)
        self.assertEqual(content.count("python public_guard.py"),2)
        self.assertEqual(content.count("python run_offline_tests.py"),2)
        self.assertIn("actions/cache/restore@v4", content)
        self.assertIn("actions/cache/save@v4", content)
        self.assertIn("python -m ops.github.standalone_hunter_runner", content)
        self.assertNotIn("AET_GITHUB_TOKEN: ", content)
        self.assertNotIn("ops/github/public_actions_runner.py", content)
        self.assertNotIn("  pull_request:", content)
        self.assertNotIn("git push", content)
        self.assertNotIn("python scanner.py", content)

if __name__ == "__main__":
    unittest.main()
