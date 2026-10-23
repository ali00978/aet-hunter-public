"""Offline tests for the manual invalid-payload Cloudflare handshake."""
import contextlib
import io
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from ops.github import aet_pro_handshake as handshake


class FakeOpener:
    def __init__(self, codes):
        self.codes = iter(codes)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        code = next(self.codes)
        raise HTTPError(request.full_url, code, "TEST-SENSITIVE-TEXT", {}, None)


class AetProHandshakeTests(unittest.TestCase):
    def env(self):
        return {
            "GITHUB_REPOSITORY": handshake.REPO,
            "GITHUB_REF": handshake.REF,
            "AET_PRO_SYNC_URL": "https://" + handshake.EXPECTED_HOST + handshake.EXPECTED_PATH,
            "AET_HUNTER_SYNC_TOKEN": "SAMPLE-TEST-ONLY-LONG-TOKEN-000000",
        }

    def test_expected_host_path_https_only(self):
        good = self.env()["AET_PRO_SYNC_URL"]
        self.assertTrue(handshake.valid_endpoint(good))
        for value in ("http://" + handshake.EXPECTED_HOST + handshake.EXPECTED_PATH,
                      "https://malicious.example" + handshake.EXPECTED_PATH,
                      good + "?token=sensitive",
                      good + "#fragment",
                      good + "/",
                      "https://user:pass@" + handshake.EXPECTED_HOST + handshake.EXPECTED_PATH,
                      "https://" + handshake.EXPECTED_HOST + ":444" + handshake.EXPECTED_PATH):
            with self.subTest(url=value):
                self.assertFalse(handshake.valid_endpoint(value))

    def test_redirects_refused(self):
        self.assertIsNone(handshake.NoRedirects().redirect_request(
            None, None, 302, "Redirect", {}, "https://elsewhere.example/"
        ))

    def test_good_handshake_uses_only_invalid_post_payload(self):
        opener = FakeOpener([422, 401])
        output = io.StringIO()
        env = self.env()
        with patch.dict(os.environ, env, clear=True), \
                patch.object(handshake, "build_opener", return_value=opener), \
                contextlib.redirect_stdout(output):
            self.assertEqual(handshake.main(), 0)
        self.assertEqual(len(opener.requests), 2)
        first, second = [row[0] for row in opener.requests]
        for request, timeout in opener.requests:
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.data, handshake.INVALID_BODY)
            self.assertEqual(request.full_url, env["AET_PRO_SYNC_URL"])
            self.assertEqual(timeout, 12)
        self.assertEqual(first.get_header("X-hunter-token"), env["AET_HUNTER_SYNC_TOKEN"])
        self.assertIsNone(second.get_header("X-hunter-token"))
        report = output.getvalue()
        self.assertNotIn(env["AET_HUNTER_SYNC_TOKEN"], report)
        self.assertNotIn("TEST-SENSITIVE-TEXT", report)
        self.assertIn("PASS:", report)

    def test_same_422_with_no_token_is_failure(self):
        opener = FakeOpener([422, 422])
        with patch.dict(os.environ, self.env(), clear=True), \
                patch.object(handshake, "build_opener", return_value=opener), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handshake.main(), 1)

    def test_invalid_token_never_claims_success(self):
        opener = FakeOpener([401])
        with patch.dict(os.environ, self.env(), clear=True), \
                patch.object(handshake, "build_opener", return_value=opener), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handshake.main(), 1)
        self.assertEqual(len(opener.requests), 1)

    def test_wrong_repo_blocks_all_network(self):
        env = {**self.env(), "GITHUB_REPOSITORY": "attacker/fork"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(handshake, "build_opener") as make_opener, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handshake.main(), 1)
            make_opener.assert_not_called()

    def test_missing_secret_blocks_all_network(self):
        env = self.env()
        del env["AET_HUNTER_SYNC_TOKEN"]
        with patch.dict(os.environ, env, clear=True), \
                patch.object(handshake, "build_opener") as make_opener, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handshake.main(), 1)
            make_opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
