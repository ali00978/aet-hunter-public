"""Offline tests for the manual two-token read-only connection verifier."""
import contextlib
import io
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from ops.github import readonly_connection_check as check


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, response=None):
        self.requests = []
        self.response = response

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response or Response()


class ReadOnlyConnectionTests(unittest.TestCase):
    def test_both_checks_use_only_static_get_endpoints(self):
        opener = FakeOpener()
        for label, secret, url in check.CHECKS:
            ok, status = check.status_for(url, "PLACEHOLDER_DO_NOT_PRINT", opener)
            self.assertTrue(ok, label)
            self.assertEqual(status, "HTTP 200")
        self.assertEqual(len(opener.requests), 2)
        for (req, timeout), (label, secret, url) in zip(opener.requests, check.CHECKS):
            self.assertEqual(req.get_method(), "GET")
            self.assertIsNone(req.data)
            self.assertEqual(req.full_url, url)
            self.assertEqual(timeout, 10)
            self.assertEqual(req.get_header("Authorization"),
                             "Bearer PLACEHOLDER_DO_NOT_PRINT")
            self.assertTrue(url.startswith("https://"))
        self.assertNotIn("run-sync", "\n".join(x[2] for x in check.CHECKS))

    def test_http_auth_error_is_sanitized(self):
        opener = FakeOpener(HTTPError(
            check.CHECKS[1][2], 401, "SECRET-NEVER-LOG", None, None
        ))
        result = check.status_for(check.CHECKS[1][2], "SECRET-NEVER-LOG", opener)
        self.assertEqual(result, (False, "HTTP 401"))
        self.assertNotIn("SECRET-NEVER-LOG", str(result))

    def test_no_redirects_to_forward_authorization(self):
        handler = check.RejectRedirect()
        self.assertIsNone(
            handler.redirect_request(None, None, 302, "Redirect", {},
                                     "https://redirect.invalid/steal")
        )

    def test_main_checks_two_secrets_with_no_sensitive_logging(self):
        env = {
            "GITHUB_REPOSITORY": check.REPO,
            "GITHUB_REF": check.MAIN,
            "AET_GITHUB_TOKEN": "GITHUB-VERY-PRIVATE-VALUE",
            "APIFY_TOKEN": "APIFY-VERY-PRIVATE-VALUE",
        }
        opener = FakeOpener()
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), \
                patch.object(check, "build_opener", return_value=opener), \
                contextlib.redirect_stdout(output):
            self.assertEqual(check.main(), 0)
        report = output.getvalue()
        self.assertNotIn(env["AET_GITHUB_TOKEN"], report)
        self.assertNotIn(env["APIFY_TOKEN"], report)
        self.assertIn("GitHub private repository read access: OK", report)
        self.assertIn("Apify account API authentication: OK", report)
        self.assertEqual(len(opener.requests), 2)

    def test_missing_apify_does_not_call_apify_endpoint(self):
        env = {
            "GITHUB_REPOSITORY": check.REPO,
            "GITHUB_REF": check.MAIN,
            "AET_GITHUB_TOKEN": "placeholder",
        }
        opener = FakeOpener()
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), \
                patch.object(check, "build_opener", return_value=opener), \
                contextlib.redirect_stdout(output):
            self.assertEqual(check.main(), 1)
        self.assertEqual(len(opener.requests), 1)
        self.assertIn("missing APIFY_TOKEN", output.getvalue())

    def test_refuses_another_repository_without_network(self):
        env = {
            "GITHUB_REPOSITORY": "another-owner/fork",
            "GITHUB_REF": check.MAIN,
            "AET_GITHUB_TOKEN": "irrelevant",
            "APIFY_TOKEN": "irrelevant",
        }
        with patch.dict(os.environ, env, clear=True), \
                patch.object(check, "build_opener") as build:
            self.assertEqual(check.main(), 1)
            build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
