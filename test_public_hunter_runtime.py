"""No-provider-call tests for standalone public production runtime."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import public_hunter_runtime as runtime
import public_apify_collector as apify


class StandaloneRuntimeTests(unittest.TestCase):
    def test_explicit_live_gate_precedes_all_network_and_sync(self):
        self.assertEqual(runtime.run_platform("marktplaats", env={}), "disabled")
        self.assertEqual(runtime.run_platform("facebook", env={}), "disabled")

    def test_pro_endpoint_rejects_other_origins_and_token_in_url(self):
        token = "x" * 26
        good = "https://aet-cars-pro.eltakech100.workers.dev/api/integrations/hunter/sync"
        self.assertEqual(runtime.require_sync({"AET_PRO_SYNC_URL":good,
                            "AET_HUNTER_SYNC_TOKEN":token})[0], good)
        for url in ("http://" + good.removeprefix("https://"),
                    "https://evil.example/api/integrations/hunter/sync",
                    good + "?token=leak",
                    "https://user:pass@aet-cars-pro.eltakech100.workers.dev/api/integrations/hunter/sync"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                runtime.require_sync({"AET_PRO_SYNC_URL":url, "AET_HUNTER_SYNC_TOKEN":token})

    def test_live_gate_requires_sync_even_before_scanning(self):
        for source in ("marktplaats", "facebook"):
            cfg = {"AET_MODAL_LIVE_ENABLED":"I_ACCEPT_LIVE_SCANS",
                   "AET_FACEBOOK_ENABLED":"true"}
            with self.subTest(source=source), self.assertRaises(ValueError):
                runtime.run_platform(source, env=cfg)

    def test_provider_spending_fails_closed(self):
        for env in ({}, {"AET_APIFY_MONTHLY_BUDGET_USD":"NaN"},
                    {"AET_APIFY_MONTHLY_BUDGET_USD":"2","AET_APIFY_MAX_CHARGE_USD":"2"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                apify.bounded_budget(env)

    def test_provider_quota_is_reserved_and_committed_before_request(self):
        now = datetime.now(timezone.utc)
        env = {"AET_FACEBOOK_ENABLED":"true", "APIFY_TOKEN":"safe-token-value-1234567",
               "AET_APIFY_MONTHLY_BUDGET_USD":"0.20", "AET_APIFY_MAX_CHARGE_USD":"0.10"}
        with tempfile.TemporaryDirectory() as d:
            marks = []
            def provider(token, cap):
                data = json.loads((Path(d)/"apify_budget.json").read_text())
                marks.append(("provider_after_reservation", data["calls"], cap))
                return [
                    {"url":"https://www.facebook.com/marketplace/item/123456789/",
                     "title":"Opel Corsa 2012", "price":950,
                     "listedAt":now.isoformat(), "mileage_km":165000,
                     "seller_type":"particulier"},
                    {"url":"https://www.facebook.com/marketplace/item/987654321/",
                     "title":"Los stuurwiel Opel Corsa", "price":50,
                     "listedAt":now.isoformat()},
                ]
            def persist():marks.append(("persist",))
            status = apify.collect(d, env, persist, request_fn=provider, now=now)
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["accepted"], 1)
            self.assertEqual(marks[0], ("persist",))
            self.assertEqual(marks[1][0], "provider_after_reservation")
            self.assertEqual(marks[1][1], 1)
            stored = json.loads((Path(d)/"apify_kandidaten.json").read_text())
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["title"], "Opel Corsa 2012")
            budget = json.loads((Path(d)/"apify_budget.json").read_text())
            self.assertEqual(budget["calls"], 1)
            apify.reserve(d, env, persist, now=now)
            with self.assertRaises(ValueError):
                apify.reserve(d, env, persist, now=now)

    def test_provider_failed_request_is_still_counted(self):
        env = {"AET_FACEBOOK_ENABLED":"true", "APIFY_TOKEN":"safe-token-value-1234567",
               "AET_APIFY_MONTHLY_BUDGET_USD":"1.00", "AET_APIFY_MAX_CHARGE_USD":"0.10"}
        with tempfile.TemporaryDirectory() as d:
            counts = []
            status = apify.collect(d, env, lambda: counts.append(1),
                                   request_fn=lambda token, cap: (_ for _ in ()).throw(URLError("network")))
            self.assertEqual(status["status"], "request_failed")
            self.assertEqual(json.loads((Path(d)/"apify_budget.json").read_text())["calls"], 1)
            self.assertGreaterEqual(len(counts), 2)

    def test_apify_token_goes_in_header_not_url(self):
        seen = {}
        class Reply:
            status = 200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b"[]"
        class Opener:
            def open(self, request, timeout=0):
                seen["url"] = request.full_url
                seen["authorization"] = request.get_header("Authorization")
                return Reply()
        self.assertEqual(apify.fetch_actor("safe-token-value-1234567", 0.10, Opener()), [])
        self.assertTrue(seen["authorization"].startswith("Bearer "))
        self.assertNotIn("safe-token", seen["url"])
        self.assertIn("maxTotalChargeUsd=0.1", seen["url"])

    def test_hunter_envelope_compatible_with_original_aet_pro(self):
        from hunter_sync import envelope
        now = datetime.now(timezone.utc)
        batch = {"schema": "aet-pro.hunter.v2", "candidates": [
            {"id": "marktplaats:m1234", "source":"marktplaats"}]}
        status = {"checked_at":now.isoformat(), "scan_health":"ok"}
        package = envelope("marktplaats", 1234, 1, now=now,
                           combined=batch, source_status=status)
        self.assertEqual(package["schema"], "aet-pro.hunter.sync.v1")
        self.assertEqual(package["scan"]["candidate_ids"], ["marktplaats:m1234"])
        self.assertEqual(package["batch"], batch)


if __name__ == "__main__":
    unittest.main()
