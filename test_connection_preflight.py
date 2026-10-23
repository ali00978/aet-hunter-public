"""Verify preflight cannot inadvertently run scans or reveal token values."""
import contextlib
import io
import os
import unittest
from unittest.mock import patch

from ops.github.connection_preflight import NAMES, check, main


class ConnectionPreflightTests(unittest.TestCase):
    def test_missing_everything(self):
        ok, absent = check({})
        self.assertFalse(ok)
        self.assertEqual(tuple(absent), NAMES)

    def test_test_values_do_not_appear_in_report(self):
        env = {name: "PRETEND-DO-NOT-PRINT-SECRET-1" for name in NAMES}
        with patch.dict(os.environ, env, clear=True):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(), 0)
            self.assertNotIn("PRETEND-DO-NOT-PRINT-SECRET-1", output.getvalue())

    def test_one_missing_field_fails_closed(self):
        values = {name: "pretend" for name in NAMES}
        del values["APIFY_TOKEN"]
        ok, missing = check(values)
        self.assertFalse(ok)
        self.assertEqual(missing, ["APIFY_TOKEN"])

    def test_requires_nonblank_values(self):
        values = {name: "stub" for name in NAMES}
        values["AET_PRO_SYNC_URL"] = " \t "
        ok, missing = check(values)
        self.assertFalse(ok)
        self.assertEqual(missing, ["AET_PRO_SYNC_URL"])


if __name__ == "__main__":
    unittest.main()
