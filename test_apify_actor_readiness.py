"""Offline tests of manually dispatched Apify read-only readiness logic."""
import contextlib
import io
import json
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from ops.github import apify_actor_readiness as check


def actor():
    return {"data": {"username": "hydedata", "name": "facebook-marketplace-scraper",
                      "isPublic": True}}


def limits(spent=1.0, maximum=5.0):
    return {"data": {"limits": {"maxMonthlyUsageUsd": maximum},
                     "current": {"monthlyUsageUsd": spent}}}


class FakeResponse:
    status = 200

    def __init__(self, content):
        self.content = content

    def read(self, size):
        return self.content[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append((req, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(json.dumps(response).encode("utf-8"))


class ApifyActorReadinessTests(unittest.TestCase):
    def env(self):
        return {"GITHUB_REPOSITORY": check.REPO, "GITHUB_REF": check.REF,
                "APIFY_TOKEN": "SYNTHETIC-DO-NOT-PRINT-SECRET"}

    def test_only_get_fixed_provider_endpoints(self):
        opener = FakeOpener([actor(), limits()])
        for url in (check.ACTOR, check.LIMITS):
            code, data = check.read_json(url, self.env()["APIFY_TOKEN"], opener)
            self.assertEqual(code, 200)
            self.assertIsInstance(data, dict)
        self.assertEqual(len(opener.requests), 2)
        for (req, timeout), target in zip(opener.requests, (check.ACTOR, check.LIMITS)):
            self.assertEqual(req.full_url, target)
            self.assertEqual(req.get_method(), "GET")
            self.assertIsNone(req.data)
            self.assertEqual(timeout, 12)
            self.assertEqual(req.get_header("Authorization"),
                             "Bearer SYNTHETIC-DO-NOT-PRINT-SECRET")
        self.assertNotIn("/runs", "".join(x[0].full_url for x in opener.requests))

    def test_reject_unknown_endpoints(self):
        with self.assertRaises(ValueError):
            check.read_json("https://malicious.invalid", "SYNTHETIC", FakeOpener([]))

    def test_redirect_is_forbidden(self):
        self.assertIsNone(check.RejectRedirect().redirect_request(
            None, None, 302, "Moved", {}, "https://attacker.invalid"))

    def test_sanitized_success_and_no_amounts_logged(self):
        opener = FakeOpener([actor(), limits(spent=1.13, maximum=5.0)])
        output = io.StringIO()
        with patch.dict(os.environ, self.env(), clear=True), \
                patch.object(check, "build_opener", return_value=opener), \
                contextlib.redirect_stdout(output):
            self.assertEqual(check.main(), 0)
        self.assertNotIn(self.env()["APIFY_TOKEN"], output.getvalue())
        self.assertNotIn("1.13", output.getvalue())
        self.assertNotIn("hydedata", output.getvalue())
        self.assertIn("Facebook Actor: FOUND", output.getvalue())
        self.assertIn("WITHIN LIMIT", output.getvalue())

    def test_near_limit_fails_closed(self):
        valid, messages = check.summarize((200, actor()), (200, limits(spent=4.5)))
        self.assertFalse(valid)
        self.assertTrue(any("STOP" in m for m in messages))

    def test_unknown_budget_is_not_assumed_free(self):
        valid, messages = check.summarize((200, actor()), (200, limits(maximum=0.0)))
        self.assertFalse(valid)
        self.assertTrue(any("NOT VERIFIED" in m for m in messages))

    def test_actor_not_available_fails(self):
        for payload in ({"data": {"name": "different", "username": "hydedata"}},
                        {"data": {"name": "facebook-marketplace-scraper",
                                  "username": "hydedata", "isPublic": False}}):
            with self.subTest(payload=payload):
                valid, messages = check.summarize((200, payload),
                                                  (200, limits()))
                self.assertFalse(valid)

    def test_account_error_is_sanitized(self):
        opener = FakeOpener([HTTPError(check.ACTOR, 401, "SECRET-ECHO", None, None)])
        status, body = check.read_json(check.ACTOR, "SECRET-ECHO", opener)
        self.assertEqual(status, 401)
        self.assertIsNone(body)

    def test_oversized_response_rejected(self):
        class BigResponse(FakeResponse):
            def read(self, size):
                return b"A" * size
        opener = FakeOpener([])
        opener.open = lambda *_args, **_kwargs: BigResponse(b"")
        status, body = check.read_json(check.ACTOR, "DO-NOT-PRINT", opener)
        self.assertEqual(status, 200)
        self.assertIsNone(body)

    def test_wrong_branch_no_network(self):
        env = {**self.env(), "GITHUB_REF": "refs/heads/other"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(check, "build_opener") as opener, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check.main(), 1)
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
