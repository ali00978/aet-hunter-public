"""Synthetic, offline tests of the private Hunter snapshot integrity preflight."""
import base64
import contextlib
from datetime import datetime, timezone
import io
import json
import os
import unittest
from unittest.mock import patch

from ops.github import private_snapshot_preflight as check

NOW = datetime(2026, 10, 22, 8, tzinfo=timezone.utc)


def snapshot():
    mp = {"id": "marktplaats:123", "source": "marktplaats", "content_hash": "a" * 64}
    fb = {"id": "facebook:555", "source": "facebook", "content_hash": "b" * 64}
    return {
        "hunter_pro_combined.json": {
            "schema": "aet-pro.hunter.v2", "candidates": [mp, fb], "count": 2,
            "platform_counts": {"marktplaats": 1, "facebook": 1}
        },
        "marktplaats_status.json": {"scan_health": "ok", "checked_at": NOW.isoformat()},
        "apify_status.json": {"status": "ok", "checked_at": NOW.isoformat()},
        "apify_budget.json": {"calls": 31, "month": "2026-10"},
    }


def encoded(data):
    return {"type": "file", "encoding": "base64",
            "content": base64.b64encode(json.dumps(data).encode()).decode()}


class FakeResponse:
    status = 200

    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def read(self, n):
        return self.data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append((req, timeout))
        return FakeResponse(next(self.responses))


class PrivateStatePreflightTests(unittest.TestCase):
    def test_only_five_get_requests_with_private_token(self):
        data = snapshot()
        fake = FakeOpener([{"permissions": {"push": True}}] +
                          [encoded(data[k]) for k in check.FILES])
        env = {"GITHUB_REPOSITORY": check.PUBLIC_REPO,
               "GITHUB_REF": "refs/heads/main",
               "AET_GITHUB_TOKEN": "TEST_SECRET_MUST_NOT_BE_LOGGED"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(check, "build_opener", return_value=fake), \
                contextlib.redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(check.main(), 0)
        self.assertEqual(len(fake.requests), 5)
        for req, timeout in fake.requests:
            self.assertEqual(req.get_method(), "GET")
            self.assertEqual(req.get_header("Authorization"), "Bearer " + env["AET_GITHUB_TOKEN"])
            self.assertEqual(timeout, 15)
        self.assertNotIn(env["AET_GITHUB_TOKEN"], captured.getvalue())
        self.assertNotIn("marktplaats:123", captured.getvalue())

    def test_valid_batch_passes_with_explicit_no_write_caveat(self):
        ok, messages = check.validate(snapshot(), {"permissions": {"push": True}}, NOW)
        self.assertTrue(ok)
        self.assertTrue(any("not a write test" in s for s in messages))

    def test_stale_data_reports_historical_not_live(self):
        from datetime import timedelta
        ok, messages = check.validate(snapshot(), {}, NOW + timedelta(days=2))
        self.assertTrue(ok)
        self.assertEqual(sum("HISTORICAL ONLY" in m for m in messages), 2)

    def test_duplicate_candidates_fail_closed(self):
        data = snapshot()
        data["hunter_pro_combined.json"]["candidates"][1] = dict(
            data["hunter_pro_combined.json"]["candidates"][0]
        )
        ok, _ = check.validate(data, {}, NOW)
        self.assertFalse(ok)

    def test_bad_hash_and_counts_fail_closed(self):
        for edit in ("hash", "count"):
            data = snapshot()
            if edit == "hash":
                data["hunter_pro_combined.json"]["candidates"][0]["content_hash"] = "bad"
            else:
                data["hunter_pro_combined.json"]["platform_counts"]["facebook"] = 5
            ok, _ = check.validate(data, {}, NOW)
            self.assertFalse(ok)

    def test_provider_cap_not_ignored(self):
        data = snapshot()
        data["apify_budget.json"]["calls"] = 101
        ok, _ = check.validate(data, {}, NOW)
        self.assertFalse(ok)

    def test_decode_snapshot_and_deny_unknown_endpoint(self):
        self.assertEqual(check.decode_snapshot(encoded({"hello": 7})), {"hello": 7})
        with self.assertRaises(ValueError):
            check.read_json("https://evil.invalid", "testing", FakeOpener([]))

    def test_refuse_foreign_repo_without_network(self):
        env = {"GITHUB_REPOSITORY": "fork/aet-hunter-public",
               "GITHUB_REF": "refs/heads/main", "AET_GITHUB_TOKEN": "testing"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(check, "build_opener") as make, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check.main(), 1)
            make.assert_not_called()

    def test_block_auth_redirect(self):
        self.assertIsNone(check.NoRedirect().redirect_request(
            None, None, 302, "Moved", {}, "https://evil.invalid/"))


if __name__ == "__main__":
    unittest.main()
