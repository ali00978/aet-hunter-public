"""All auth-probe regression tests are offline; use fake HTTP responses only."""
import contextlib
import io
import json
import unittest
from unittest.mock import Mock

from urllib.error import HTTPError, URLError

from ops.github.provider_auth_probe import TARGETS, NoRedirects, check, main


class StubResponse:
    status = 200

    def __init__(self, url, payload):
        self._url = url
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self._url

    def read(self, maximum):
        return self._payload[:maximum]


class ProviderAuthProbeTests(unittest.TestCase):
    def test_valid_private_github_read(self):
        url = TARGETS["github"][1]
        api = Mock()
        api.open.return_value = StubResponse(url, {
            "type": "file", "path": "hunter_sync.py", "content": "SENSITIVE",
        })
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(check("github", {"AET_GITHUB_TOKEN": "FAKE-TOKEN-SECRET"}, api))
        request = api.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, url)
        self.assertEqual(request.get_header("Authorization"), "Bearer FAKE-TOKEN-SECRET")
        self.assertNotIn("FAKE-TOKEN-SECRET", out.getvalue())
        self.assertNotIn("SENSITIVE", out.getvalue())

    def test_valid_apify_account_read(self):
        url = TARGETS["apify"][1]
        api = Mock()
        api.open.return_value = StubResponse(url, {
            "data": {"id": "EXAMPLE", "email": "private@example.invalid",
                     "proxy": {"password": "DO-NOT-PRINT"}},
        })
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(check("apify", {"APIFY_TOKEN": "FAKE-APIFY-SECRET"}, api))
        self.assertEqual(api.open.call_args.args[0].get_method(), "GET")
        self.assertNotIn("private@example.invalid", out.getvalue())
        self.assertNotIn("DO-NOT-PRINT", out.getvalue())

    def test_missing_secret_makes_no_request(self):
        api = Mock()
        self.assertFalse(check("github", {}, api))
        api.open.assert_not_called()

    def test_bad_github_auth_logs_only_http_status(self):
        api = Mock()
        api.open.side_effect = HTTPError(TARGETS["github"][1], 401,
                                         "SENSITIVE-TOKEN-IN-ERROR", {}, None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(check("github", {"AET_GITHUB_TOKEN": "FAKE-TOKEN"}, api))
        self.assertIn("HTTP 401", out.getvalue())
        self.assertNotIn("SENSITIVE-TOKEN", out.getvalue())
        self.assertNotIn("FAKE-TOKEN", out.getvalue())

    def test_network_errors_are_redacted(self):
        api = Mock()
        api.open.side_effect = URLError("FAKE-TOKEN-IN-ERROR")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(check("apify", {"APIFY_TOKEN": "FAKE-TOKEN"}, api))
        self.assertNotIn("FAKE-TOKEN", out.getvalue())

    def test_redirect_is_rejected(self):
        api = Mock()
        api.open.return_value = StubResponse("https://other.invalid/path", {"data": {"id": "test"}})
        self.assertFalse(check("apify", {"APIFY_TOKEN": "FAKE-TOKEN"}, api))
        self.assertIsNone(NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_unexpected_resource_rejected(self):
        api = Mock()
        api.open.return_value = StubResponse(TARGETS["github"][1], {"type": "dir"})
        self.assertFalse(check("github", {"AET_GITHUB_TOKEN": "FAKE-TOKEN"}, api))

    def test_missing_api_identity_rejected(self):
        api = Mock()
        api.open.return_value = StubResponse(TARGETS["apify"][1], {"data": {}})
        self.assertFalse(check("apify", {"APIFY_TOKEN": "FAKE-TOKEN"}, api))

    def test_cli_unknown_does_not_open_network(self):
        self.assertEqual(main(["unknown"]), 2)


if __name__ == "__main__":
    unittest.main()
