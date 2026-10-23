"""Offline tests: smoke probe is bounded, one GET, and no private data."""
import contextlib
import io
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from ops.github import live_marktplaats_smoke as smoke


class LiveSmokeTests(unittest.TestCase):
    def test_one_fetch_and_no_detail_requests(self):
        calls = []
        def fake(url):
            calls.append(url)
            html = '<a href="/v/auto-s/opel/m123456789-opel-corsa-2012">auto</a>'
            return html, "https://www.marktplaats.nl/q/opel%20corsa/", 200
        ok, msg = smoke.smoke(fake)
        self.assertTrue(ok, msg)
        self.assertIn("1 unique car listing", msg)
        self.assertEqual(calls, ["https://www.marktplaats.nl/q/opel%20corsa/"])

    def test_403_stops_without_retry(self):
        calls = []
        def fake(url):
            calls.append(url)
            raise HTTPError(url, 403, "do-not-show-private-response", {}, None)
        ok, msg = smoke.smoke(fake)
        self.assertFalse(ok)
        self.assertIn("403", msg)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("do-not-show-private-response", msg)

    def test_empty_or_login_page_not_proof_of_valid_scan(self):
        ok, msg = smoke.smoke(
            lambda url: ("<html>Sign in</html>", url, 200))
        self.assertFalse(ok)

    def test_external_redirect_is_rejected(self):
        ok, msg = smoke.smoke(
            lambda url: ('<a href="/v/auto-s/opel/m123456789-car">x</a>',
                         "https://untrusted.example/landing", 200))
        self.assertFalse(ok)

    def test_not_owner_main_never_fetches(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/fork",
                                      "GITHUB_REF": smoke.REF}, clear=True), \
                patch.object(smoke.scanner, "fetch") as fetch, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(smoke.main(), 1)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
