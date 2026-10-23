"""Public, fake-process tests for isolated private development validation."""
import contextlib
import io
import os
import subprocess
import unittest
from unittest.mock import patch

from ops.github import private_pr_offline_validation as job


class PrivatePrOfflineValidationTests(unittest.TestCase):
    def test_only_known_private_branch_and_test_modules(self):
        self.assertEqual(job.BRANCH, "fix/facebook-state-commit-recovery")
        self.assertTrue(job.PRIVATE_REMOTE.endswith("/ali00978/aet-cars-ai-hunter.git"))
        self.assertIn("test_apify_budget_guard", job.TEST_NAMES)
        self.assertIn("test_hunter_sync", job.TEST_NAMES)
        self.assertIn("test_workflow_state_recovery", job.TEST_NAMES)
        self.assertIn("socket.getaddrinfo = no_network", job.OFFLINE_TEST_RUNNER)
        self.assertNotIn("scanner.py", job.TEST_NAMES)
        self.assertNotIn("apify_test.py", job.TEST_NAMES)

    def test_foreign_repository_refused_before_clone(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "fork/unsafe",
                                      "GITHUB_REF": "refs/heads/main",
                                      "AET_GITHUB_TOKEN": "FAKE"}, clear=True), \
                patch.object(job.subprocess, "run") as runner, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(job.main(), 1)
            runner.assert_not_called()

    def test_missing_token_refused_before_clone(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": job.OWNER,
                                      "GITHUB_REF": "refs/heads/main"},
                        clear=True), \
                patch.object(job.subprocess, "run") as runner, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(job.main(), 1)
            runner.assert_not_called()

    def test_success_still_no_provider_or_cloud_credentials(self):
        calls = []
        def fake_run(*args, **kw):
            calls.append((args[0], kw))
            if len(calls) == 1:
                return subprocess.CompletedProcess(args[0], 0)
            return subprocess.CompletedProcess(
                args[0], 0, stdout="Private OFFLINE tests: 185 failures: 0 errors: 0 skipped: 0\n")
        env = {"GITHUB_REPOSITORY": job.OWNER,
               "GITHUB_REF": "refs/heads/main",
               "AET_GITHUB_TOKEN": "TOKEN_MUST_NEVER_BE_PRINTED",
               "APIFY_TOKEN": "FAKE_APIFY_SECRET",
               "AET_HUNTER_SYNC_TOKEN": "FAKE_SYNC_SECRET"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(job.subprocess, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(job.main(), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][0], "git")
        self.assertIn("clone", calls[0][0])
        self.assertIn("--single-branch", calls[0][0])
        self.assertEqual(calls[1][0][0], job.sys.executable)
        for name in ("AET_GITHUB_TOKEN", "APIFY_TOKEN", "AET_HUNTER_SYNC_TOKEN"):
            self.assertNotIn(name, calls[1][1]["env"])
            self.assertNotIn(env[name], out.getvalue())
        self.assertNotIn("git push", out.getvalue())

    def test_failed_private_test_is_not_false_green(self):
        def fake_run(*args, **kw):
            if args[0][0] == "git":
                return subprocess.CompletedProcess(args[0], 0)
            return subprocess.CompletedProcess(
                args[0], 1, stdout="Private OFFLINE tests: 180 failures: 1 errors: 0 skipped: 0\n")
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": job.OWNER,
                                      "GITHUB_REF": "refs/heads/main",
                                      "AET_GITHUB_TOKEN": "FAKE_TOKEN"}, clear=True), \
                patch.object(job.subprocess, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(job.main(), 1)


if __name__ == "__main__":
    unittest.main()
