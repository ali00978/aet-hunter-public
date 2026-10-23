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
    def test_marktplaats_progress_reports_only_aggregates_and_no_network(self):
        from unittest.mock import patch
        messages=[]
        def scanner():
            messages.append("scanned")
            return "ok"
        self.assertEqual(runtime.run_scanner_with_progress(
            scanner, interval_seconds=120,
            emit=lambda text: messages.append(text)), "ok")
        self.assertEqual(messages[1], "scanned")
        self.assertEqual(len(messages), 2)
        self.assertTrue(messages[0].startswith("marktplaats: scan gestart"))
        self.assertNotIn("http", messages[0].lower())

    def test_marktplaats_progress_cleans_up_even_on_error(self):
        from unittest.mock import patch
        with self.assertRaisesRegex(ValueError,"scanner failed"):
            runtime.run_scanner_with_progress(
                lambda: (_ for _ in ()).throw(ValueError("scanner failed")),
                emit=lambda text: None)
        with self.assertRaises(ValueError):
            runtime.run_scanner_with_progress(
                lambda: None,interval_seconds=0,emit=lambda text: None)

    def test_scan_counters_do_not_expose_private_data(self):
        good={"search_pages_requested":28,"detail_pages_attempted":101,
              "requests_attempted_total":129,
              "seller_name":"private", "url":"https://example.test"}
        self.assertEqual(runtime.safe_scan_counters(good),(28,101,129))
        for invalid in ({**good,"detail_pages_attempted":True},
                        {**good,"search_pages_requested":146},
                        {**good,"requests_attempted_total":"129"},
                        {"seller_name":"private"}):
            self.assertIsNone(runtime.safe_scan_counters(invalid))

    def test_explicit_live_gate_precedes_all_network_and_sync(self):
        self.assertEqual(runtime.run_platform("marktplaats", env={}), "disabled")
        self.assertEqual(runtime.run_platform("facebook", env={}), "disabled")

    def test_scan_ids_stable_within_github_run_and_private_status_sanitized(self):
        cfg={"GITHUB_RUN_ID":"456789123", "GITHUB_RUN_ATTEMPT":"2"}
        self.assertEqual(runtime.durable_scan_identity(cfg),("456789123","2"))
        self.assertEqual(runtime.durable_scan_identity(cfg),("456789123","2"))
        invalid={"GITHUB_RUN_ID":"123abc","GITHUB_RUN_ATTEMPT":"1"}
        ident=runtime.durable_scan_identity(invalid)
        self.assertTrue(ident[0].isdigit())
        self.assertEqual(ident[1],"1")
        self.assertEqual(runtime.provider_status_label({"status":"provider_pending"}),
                         "provider_pending")
        self.assertEqual(runtime.provider_status_label({
            "status":"https://untrusted.example/secrets"}),"unknown")

    def test_aet_pro_sync_receipt_is_bounded_and_requires_true(self):
        import hunter_sync
        endpoint=("https://aet-cars-pro.eltakech100.workers.dev"
                  "/api/integrations/hunter/sync")
        token="x"*26
        responses=[
            (b'{"received":true,"count":2}',True),
            (b'{"received":false}',False),
            (b'{"received":"true"}',False),
            (b'[]',False),
            (b'not-json',False),
            (b'{' + b'x'*70_000 + b'}',False)
        ]
        class Reply:
            status=200
            def __init__(self,raw):self.raw=raw
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return self.raw[:n]
        class Opener:
            def __init__(self,raw):self.raw=raw
            def open(self,request,timeout=0):
                self_outer.assertEqual(request.get_method(),"POST")
                self_outer.assertEqual(timeout,20)
                return Reply(self.raw)
        self_outer=self
        for raw,valid in responses:
            with self.subTest(valid=valid,size=len(raw)):
                with patch.object(hunter_sync,"build_opener",
                                  return_value=Opener(raw)):
                    if valid:
                        result=hunter_sync.send({"test":True},endpoint,token)
                        self.assertIs(result["received"],True)
                    else:
                        with self.assertRaises(ValueError):
                            hunter_sync.send({"test":True},endpoint,token)

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
        for env in ({"AET_APIFY_MONTHLY_BUDGET_USD":"0"}, {"AET_APIFY_MONTHLY_BUDGET_USD":"NaN"},
                    {"AET_APIFY_MONTHLY_BUDGET_USD":"2","AET_APIFY_MAX_CHARGE_USD":"NaN"}):
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
            status = apify.collect(d, env, persist, request_fn=provider, now=now,
                                   account_check=lambda token, cap: True)
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["accepted"], 1)
            # Reserve and pending-Actor marker are BOTH durably saved
            # before the first billable provider POST.
            self.assertEqual(marks[:2],[("persist",),("persist",)])
            self.assertEqual(marks[2][0], "provider_after_reservation")
            self.assertEqual(marks[2][1], 1)
            stored = json.loads((Path(d)/"apify_kandidaten.json").read_text())
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["title"], "Opel Corsa 2012")
            budget = json.loads((Path(d)/"apify_budget.json").read_text())
            self.assertEqual(budget["calls"], 1)
            apify.reserve(d, env, persist, now=now)
            apify.reserve(d, env, persist, now=now)
            apify.reserve(d, env, persist, now=now)
            with self.assertRaises(ValueError):
                apify.reserve(d, env, persist, now=now)

    def test_provider_failed_request_is_still_counted(self):
        env = {"AET_FACEBOOK_ENABLED":"true", "APIFY_TOKEN":"safe-token-value-1234567",
               "AET_APIFY_MONTHLY_BUDGET_USD":"1.00", "AET_APIFY_MAX_CHARGE_USD":"0.10"}
        with tempfile.TemporaryDirectory() as d:
            counts = []
            status = apify.collect(d, env, lambda: counts.append(1),
                                   request_fn=lambda token, cap: (_ for _ in ()).throw(URLError("network")),
                                   account_check=lambda token, cap: True)
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
        self.assertIn("maxItems=80", seen["url"])
        self.assertNotIn("maxPaidDatasetItems", seen["url"])

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


    def test_real_public_export_pipeline_without_marketplace_network(self):
        import scanner
        import hunter_sync
        import hunter_feed
        import hunter_pro_combined
        import facebook_hunter
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            env = {
                "AET_MODAL_LIVE_ENABLED": "I_ACCEPT_LIVE_SCANS",
                "AET_PRO_SYNC_URL":
                  "https://aet-cars-pro.eltakech100.workers.dev/api/integrations/hunter/sync",
                "AET_HUNTER_SYNC_TOKEN": "x" * 26,
                "AET_HUNTER_STATE_DIR": d,
            }
            previous_roots = (hunter_feed.ROOT, hunter_pro_combined.OUTPUT,
                              hunter_sync.ROOT, facebook_hunter.ROOT)
            stages, payloads = [], []
            def fake_scanner():
                (root / "marktplaats_status.json").write_text(json.dumps(
                    {"checked_at": now.isoformat(), "scan_health": "ok",
                     "candidate_count": 0}), encoding="utf-8")
                (root / "marktplaats_proefresultaten.json").write_text("[]",
                    encoding="utf-8")
                (root / "marktplaats_handmatig_controleren.json").write_text("[]",
                    encoding="utf-8")
                stages.append("scanner")
            def fake_sync(payload, endpoint, token):
                self.assertEqual(stages[:3], ["reload", "scanner", "persist"])
                self.assertEqual(endpoint, env["AET_PRO_SYNC_URL"])
                self.assertEqual(token, "x" * 26)
                payloads.append(payload)
                return {"received": True}
            try:
                with patch.object(scanner, "main", side_effect=fake_scanner):
                    with patch.object(hunter_sync, "send", side_effect=fake_sync):
                        outcome = runtime.run_platform(
                            "marktplaats", env=env,
                            persist=lambda: stages.append("persist"),
                            reload=lambda: stages.append("reload"))
                self.assertEqual(outcome, "synced")
                self.assertEqual((hunter_feed.ROOT, hunter_pro_combined.OUTPUT,
                                  hunter_sync.ROOT, facebook_hunter.ROOT),previous_roots)
                self.assertEqual(len(payloads), 1)
                for filename in ("hunter_feed.json", "hunter_pro_export.json",
                                 "hunter_pro_combined.json"):
                    self.assertTrue((root / filename).is_file(), filename)
                self.assertEqual(
                    json.loads((root / "hunter_feed.json").read_text())["version"], 1)
                self.assertEqual(
                    json.loads((root / "hunter_pro_export.json").read_text())["schema"],
                    "aet-pro.hunter.v1")
                self.assertEqual(
                    json.loads((root / "hunter_pro_combined.json").read_text())["schema"],
                    "aet-pro.hunter.v2")
                self.assertEqual(len(payloads), 1)
                self.assertEqual(payloads[0]["schema"], "aet-pro.hunter.sync.v1")
                self.assertEqual(payloads[0]["batch"]["schema"], "aet-pro.hunter.v2")
                self.assertEqual(payloads[0]["scan"]["platform"], "marktplaats")
                self.assertEqual(payloads[0]["scan"]["candidate_ids"], [])
                self.assertGreaterEqual(stages.count("persist"), 2)
            finally:
                (hunter_feed.ROOT, hunter_pro_combined.OUTPUT,
                 hunter_sync.ROOT, facebook_hunter.ROOT) = previous_roots



    def test_facebook_provider_snapshot_to_aet_pro_batch_without_network(self):
        import hunter_sync
        import hunter_feed
        import hunter_pro_combined
        import facebook_hunter
        import public_apify_collector
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            env = {
                "AET_MODAL_LIVE_ENABLED": "I_ACCEPT_LIVE_SCANS",
                "AET_FACEBOOK_ENABLED": "true",
                "AET_PRO_SYNC_URL":
                  "https://aet-cars-pro.eltakech100.workers.dev/api/integrations/hunter/sync",
                "AET_HUNTER_SYNC_TOKEN": "x" * 26,
                "AET_HUNTER_STATE_DIR": d,
            }
            previous = (hunter_feed.ROOT, hunter_pro_combined.OUTPUT,
                        hunter_sync.ROOT, facebook_hunter.ROOT)
            outputs = []
            def fake_collect(folder, supplied_env, persist):
                self.assertEqual(Path(folder), root)
                record = {"url":"https://www.facebook.com/marketplace/item/123456789/",
                          "title":"Opel Corsa 2012", "price":950, "mileage_km":145000,
                          "listedAt":now.isoformat(), "first_seen_at":now.isoformat(),
                          "seller_type":"particulier"}
                (root / "apify_status.json").write_text(json.dumps(
                    {"checked_at":now.isoformat(),"status":"ok"}), encoding="utf-8")
                (root / "apify_kandidaten.json").write_text(json.dumps([record]),
                    encoding="utf-8")
                persist()
                return {"checked_at":now.isoformat(),"status":"ok"}
            def fake_sync(payload, *_args):
                outputs.append(payload)
                return {"received":True}
            try:
                with patch.object(public_apify_collector, "collect", side_effect=fake_collect):
                    with patch.object(hunter_sync, "send", side_effect=fake_sync):
                        result = runtime.run_platform("facebook", env=env)
                self.assertEqual(result, "synced")
                self.assertEqual((hunter_feed.ROOT, hunter_pro_combined.OUTPUT,
                                  hunter_sync.ROOT, facebook_hunter.ROOT),previous)
                self.assertEqual(outputs[0]["schema"], "aet-pro.hunter.sync.v1")
                self.assertEqual(outputs[0]["scan"]["platform"], "facebook")
                self.assertEqual(outputs[0]["scan"]["candidate_ids"], ["facebook:123456789"])
                self.assertEqual(outputs[0]["batch"]["platform_counts"]["facebook"], 1)
                self.assertTrue((root / "hunter_pro_combined.json").is_file())
                self.assertTrue(outputs[0]["batch"]["candidates"][0]["needs_inspection"])
            finally:
                (hunter_feed.ROOT, hunter_pro_combined.OUTPUT,
                 hunter_sync.ROOT, facebook_hunter.ROOT) = previous



    def test_targeted_facebook_queries_rotate_and_fetch_vehicle_specs(self):
        import urllib.parse
        from datetime import datetime,timezone
        self.assertEqual(len(apify.SEARCH_GROUPS), 4)
        self.assertEqual(len({x for g in apify.SEARCH_GROUPS for x in g}), 16)
        scheduled = [datetime(2026,10,23+slot//3,(2,10,18)[slot%3],
                              tzinfo=timezone.utc) for slot in range(12)]
        for at in scheduled:
            self.assertEqual(len(apify.query_group(at)),4)
        self.assertEqual(len(set(tuple(apify.query_group(at))
                                 for at in scheduled[:4])),4)
        caught={}
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def read(self,n):return b"[]"
        class Opener:
            def open(self, req,timeout=0):
                caught["payload"]=json.loads(req.data.decode())
                caught["url"]=req.full_url
                return Response()
        apify.fetch_actor("safe-token-value-1234567",.05,opener=Opener())
        payload=caught["payload"]
        self.assertTrue(payload["vehiclesOnly"])
        self.assertTrue(payload["strictKeywordMatch"])
        self.assertTrue(payload["includeDetails"])
        self.assertEqual(payload["daysSinceListed"],"30")
        self.assertEqual(payload["maxItemsPerSearch"],4)
        self.assertEqual(payload["maxPrice"],3000)
        self.assertEqual(payload["minPrice"],350)
        self.assertEqual(len(payload["searchQueries"]),4)
        self.assertIn("maxTotalChargeUsd=0.05",caught["url"])

    def test_nested_vehicle_details_km_conversion_and_seller(self):
        self.assertEqual(
            apify.provider_vehicle_fields({"vehicle":{
                "mileage":120000, "mileageUnit":"MILES",
                "year":2014, "sellerType":"PRIVATE_SELLER"}}),
            {"year":2014, "mileage_km":193121,
             "seller_type":"particulier"})
        self.assertEqual(
            apify.provider_vehicle_fields({"vehicle":{
                "mileage":210000, "mileageUnit":"KM",
                "sellerType":"DEALER"}}),
            {"mileage_km":210000, "seller_type":"handelaar"})
        self.assertNotIn("mileage_km", apify.provider_vehicle_fields(
            {"vehicle":{"mileage":300000,"mileageUnit":"UNKNOWN"}}))

    def test_free_budget_clamps_old_high_configuration(self):
        monthly, run = apify.bounded_budget({
            "AET_APIFY_MONTHLY_BUDGET_USD": "25.00",
            "AET_APIFY_MAX_CHARGE_USD": "0.40"})
        self.assertEqual((monthly, run), (4.65, 0.05))
        self.assertEqual(round(93 * run, 2), monthly)
        self.assertEqual(apify.bounded_budget({}), (4.65, 0.05))
        self.assertEqual(apify.bounded_budget({
            "AET_APIFY_MONTHLY_BUDGET_USD": "4.00",
            "AET_APIFY_MAX_CHARGE_USD": "0.04"}), (4.00,0.04))
        self.assertEqual(apify.MAX_ITEMS_PER_RUN, 80)
        self.assertEqual(apify.ONE_TIME_RETRY_FILE, "apify_retry_once.json")
        self.assertEqual(apify.MAX_MONTHLY_STARTS, 93)
        self.assertAlmostEqual(apify.FREE_MONTHLY_CREDIT_USD-monthly,0.35)

    def test_full_31_day_budget_cannot_exceed_4_65(self):
        now=datetime(2026,10,1,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            count=[]
            for attempt in range(93):
                self.assertEqual(apify.reserve(folder,{},lambda:count.append(1),now),0.05)
            record=json.loads((Path(folder)/"apify_budget.json").read_text())
            self.assertEqual(record,{"month":"2026-10","calls":93,"reserved_usd":4.65})
            self.assertEqual(len(count),93)
            with self.assertRaises(ValueError):
                apify.reserve(folder,{},lambda:None,now)

    def test_existing_six_cent_history_carries_forward(self):
        now=datetime(2026,10,23,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"apify_budget.json"
            p.write_text(json.dumps({"month":"2026-10",
                                     "calls":2,"reserved_usd":0.12}))
            apify.reserve(folder,{},lambda:None,now)
            data=json.loads(p.read_text())
            self.assertEqual((data["calls"],data["reserved_usd"]),(3,0.17))

    def test_free_account_credit_gate_is_read_only_and_fail_closed(self):
        def fake_open(maximum=5, spent=2, included=5, price=0):
            requests = []
            class Reply:
                status = 200
                def __init__(self, payload): self.payload = payload
                def __enter__(self): return self
                def __exit__(self, *_args): return False
                def read(self, _count):
                    return json.dumps({"data":self.payload}).encode()
            class Opener:
                def open(self, req, timeout):
                    requests.append((req.get_method(), req.full_url,
                                     req.get_header("Authorization"), timeout))
                    if req.full_url == apify.ACCOUNT_LIMITS:
                        return Reply({"limits":{"maxMonthlyUsageUsd":maximum},
                                      "current":{"monthlyUsageUsd":spent}})
                    if req.full_url == apify.ACCOUNT_PLAN:
                        return Reply({"plan":{"monthlyUsageCreditsUsd":included,
                                              "monthlyBasePriceUsd":price}})
                    raise RuntimeError("Unexpected API GET")
            return Opener(), requests
        opener, records = fake_open()
        self.assertTrue(apify.verify_free_headroom(
            "safe-token-value-1234567", 0.05, opener))
        self.assertEqual([r[:2] for r in records],
                         [("GET", apify.ACCOUNT_LIMITS),
                          ("GET", apify.ACCOUNT_PLAN)])
        for record in records:
            self.assertNotIn("safe-token", record[1])
        # A high spending ceiling is allowed only when separate prepaid
        # credits are verified. A low included balance must stop the call.
        opener, _ = fake_open(maximum=100, spent=1, included=5, price=0)
        self.assertTrue(apify.verify_free_headroom(
            "safe-token-value-1234567", 0.05, opener))
        # Paid plans are also held to the owner's $4.65 monthly ceiling.
        opener, _ = fake_open(maximum=100, spent=2, included=19, price=19)
        self.assertTrue(apify.verify_free_headroom(
            "safe-token-value-1234567", 0.05, opener))
        opener, _ = fake_open(maximum=5, spent=4.60, included=5, price=0)
        self.assertTrue(apify.verify_free_headroom(
            "safe-token-value-1234567",0.05,opener))
        for maximum, spent, included, price in (
                (5, 4.61, 5, 0), (5, 5.0, 5, 0),
                (100, 10, 5, 0), (5, -1, 5, 0),
                (100, 4.9, 5, 19), (100, 1, 0, 0),
                (100, 1, None, 0), (100, 1, 19, None)):
            with self.subTest(maximum=maximum, spent=spent,
                              included=included, price=price):
                opener, _ = fake_open(maximum, spent, included, price)
                with self.assertRaises(ValueError):
                    apify.verify_free_headroom(
                        "safe-token-value-1234567", 0.05, opener)

    def test_unverifiable_credits_never_start_paid_actor(self):
        env = {"AET_FACEBOOK_ENABLED":"true",
               "APIFY_TOKEN":"safe-token-value-1234567"}
        with tempfile.TemporaryDirectory() as d:
            with patch.object(apify, "fetch_actor") as provider:
                status = apify.collect(d, env, lambda: None,
                                       account_check=lambda *_: (_ for _ in ()).throw(
                                           ValueError("unknown free credits")))
            self.assertEqual(status["status"], "preflight_failed")
            self.assertEqual(status["preflight_reason"], "provider_gate_unknown")
            provider.assert_not_called()
            self.assertFalse((Path(d)/"apify_budget.json").exists())

    def test_duplicate_matching_actor_runs_fail_closed(self):
        from datetime import timedelta
        from urllib.parse import urlsplit
        started=datetime.now(timezone.utc)-timedelta(minutes=25)
        marker={"actor":apify.ACTOR,"started_at":started.isoformat()}
        def mock_get(url,token):
            if url==apify.ACTOR_INFO:
                return {"data":{"id":"testactorID123456"}}
            if urlsplit(url).path=="/v2/actor-runs":
                return {"data":{"items":[
                    {"id":"runAlpha12345","actId":"testactorID123456",
                     "startedAt":started.isoformat()},
                    {"id":"runBeta123456","actId":"testactorID123456",
                     "startedAt":started.isoformat()}]}}
            self.fail("Ambiguous runs must NOT be queried for dataset")
        state,rows=apify.recover_uncertain_run(
            "safe-token-value-1234567",marker,getter=mock_get)
        self.assertEqual((state,rows),("unresolved",None))

    def test_corrupt_pending_marker_stops_before_any_paid_request(self):
        env={"AET_FACEBOOK_ENABLED":"true",
             "APIFY_TOKEN":"safe-token-value-1234567"}
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/apify.PENDING_ACTOR_FILE).write_text("not-json")
            def forbidden(*_):
                self.fail("Must not call Apify if pending state is corrupt")
            result=apify.collect(d,env,lambda:None,
                 request_fn=forbidden,account_check=forbidden,
                 recovery_fn=forbidden)
            self.assertEqual(result["status"],"provider_pending")
            self.assertEqual(result["preflight_reason"],"pending_state_corrupt")
            self.assertFalse((Path(d)/"apify_budget.json").exists())

    def test_provider_http_429_clears_nonstarted_actor_marker(self):
        from urllib.error import HTTPError
        env={"AET_FACEBOOK_ENABLED":"true",
             "APIFY_TOKEN":"safe-token-value-1234567"}
        with tempfile.TemporaryDirectory() as d:
            result=apify.collect(d,env,lambda:None,
                request_fn=lambda *_: (_ for _ in ()).throw(
                    HTTPError("https://api.apify.com",429,"rate limited",None,None)),
                account_check=lambda *_:True)
            self.assertEqual(result["status"],"http_error")
            self.assertFalse((Path(d)/apify.PENDING_ACTOR_FILE).exists())
            self.assertTrue((Path(d)/apify.COOLDOWN_FILE).exists())
            self.assertEqual(json.loads((Path(d)/"apify_budget.json").read_text())["calls"],1)

    def test_async_start_uses_same_filters_and_hard_charge_cap(self):
        from urllib.parse import parse_qs,urlsplit
        captured={}
        class Reply:
            status=201
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):
                return json.dumps({"data":{
                    "id":"abcXYZ123456", "actId":"validactor12345",
                    "options":{"maxTotalChargeUsd":0.05}}}).encode("utf-8")
        class Opener:
            def open(self,request,timeout=0):
                captured["method"]=request.get_method()
                captured["url"]=request.full_url
                captured["payload"]=json.loads(request.data.decode("utf-8"))
                captured["token"]=request.get_header("Authorization")
                return Reply()
        result=apify.start_actor_async("safe-token-value-1234567",0.05,
                                       opener=Opener())
        self.assertEqual(result["id"],"abcXYZ123456")
        self.assertEqual(captured["method"],"POST")
        self.assertEqual(urlsplit(captured["url"]).path,
                         "/v2/acts/hydedata~facebook-marketplace-scraper/runs")
        self.assertEqual(parse_qs(urlsplit(captured["url"]).query)
                         ["maxTotalChargeUsd"],["0.05"])
        self.assertEqual(captured["token"],"Bearer safe-token-value-1234567")
        self.assertTrue(captured["payload"]["vehiclesOnly"])
        self.assertTrue(captured["payload"]["includeDetails"])
        self.assertEqual(captured["payload"]["maxPrice"],3000)
        self.assertEqual(len(captured["payload"]["searchQueries"]),4)
        self.assertNotIn("safe-token-value-1234567",captured["url"])

    def test_async_start_refuses_unconfirmed_charge_limit(self):
        class Reply:
            status=201
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):
                return json.dumps({"data":{"id":"abcXYZ123456",
                    "actId":"validactor12345",
                    "options":{"maxTotalChargeUsd":0.50}}}).encode()
        class Opener:
            def open(self,*args,**kw):return Reply()
        with self.assertRaisesRegex(ValueError,"safe run charge limit"):
            apify.start_actor_async("safe-token-value-1234567",0.05,
                                    opener=Opener())

    def test_async_start_restores_previous_run_without_duplicate_post(self):
        now=datetime.now(timezone.utc)
        env={"AET_FACEBOOK_ENABLED":"true",
             "APIFY_TOKEN":"safe-token-value-1234567"}
        car={"url":"https://www.facebook.com/marketplace/item/123456789/",
             "title":"Opel Corsa 2012","price":950,"mileage_km":130000,
             "seller_type":"particulier","listedAt":now.isoformat()}
        with tempfile.TemporaryDirectory() as d:
            saved=[]
            def persist():
                saved.append(json.loads((Path(d)/apify.PENDING_ACTOR_FILE).read_text())
                             if (Path(d)/apify.PENDING_ACTOR_FILE).exists()
                             else {})
            def starter(token,cap,now):
                self.assertEqual(cap,0.05)
                self.assertEqual(len(saved),2)
                return {"id":"abcXYZ123456","actId":"validactor12345"}
            def waiter(token,marker):
                self.assertEqual(marker["run_id"],"abcXYZ123456")
                self.assertEqual(marker["actor_id"],"validactor12345")
                return "recovered",[car]
            with patch.object(apify,"start_actor_async",side_effect=starter):
                with patch.object(apify,"await_started_actor",side_effect=waiter):
                    result=apify.collect(d,env,persist,now=now,
                        account_check=lambda *_:True)
            self.assertEqual(result["status"],"ok")
            self.assertEqual(result["accepted"],1)
            self.assertFalse((Path(d)/apify.PENDING_ACTOR_FILE).exists())
            self.assertEqual(len(saved),4)
            self.assertEqual(json.loads((Path(d)/"apify_budget.json").read_text())["calls"],1)

    def test_async_pending_preserves_id_and_never_starts_second_actor(self):
        now=datetime.now(timezone.utc)
        env={"AET_FACEBOOK_ENABLED":"true",
             "APIFY_TOKEN":"safe-token-value-1234567"}
        with tempfile.TemporaryDirectory() as d:
            with patch.object(apify,"start_actor_async",return_value={
                    "id":"abcXYZ123456","actId":"validactor12345"}) as start:
                with patch.object(apify,"await_started_actor",
                                  return_value=("pending",None)):
                    first=apify.collect(d,env,lambda:None,now=now,
                                        account_check=lambda *_:True)
            self.assertEqual(first["status"],"provider_pending")
            self.assertEqual(start.call_count,1)
            marker=json.loads((Path(d)/apify.PENDING_ACTOR_FILE).read_text())
            self.assertEqual(marker["run_id"],"abcXYZ123456")
            with patch.object(apify,"start_actor_async") as blocked:
                second=apify.collect(d,env,lambda:None,now=now,
                    recovery_fn=lambda *_:("pending",None),
                    account_check=lambda *_:self.fail("No extra preflight"))
                self.assertEqual(second["status"],"provider_pending")
                blocked.assert_not_called()
            self.assertEqual(json.loads((Path(d)/"apify_budget.json").read_text())["calls"],1)

    def test_direct_known_run_recovery_skips_actor_run_search(self):
        started=datetime.now(timezone.utc)
        expected="abcXYZ123456"
        requested=[]
        def getter(url,token):
            requested.append(url)
            if url==apify.ACTOR_INFO:
                return {"data":{"id":"validactor12345"}}
            if url==apify.ACTOR_RUNS+"/"+expected:
                return {"data":{"id":expected,"actId":"validactor12345",
                        "status":"SUCCEEDED","defaultDatasetId":"abCd12345678"}}
            if url.startswith("https://api.apify.com/v2/datasets/"):
                return [{"url":"https://www.facebook.com/marketplace/item/123/"}]
            self.fail("No run-list search for known Apify run")
        state,rows=apify.recover_uncertain_run(
            "safe-token-value-1234567",
            {"actor":apify.ACTOR,"started_at":started.isoformat(),
             "run_id":expected,"actor_id":"validactor12345"},getter=getter)
        self.assertEqual(state,"recovered")
        self.assertEqual(len(rows),1)
        self.assertEqual(len(requested),2)
        self.assertNotIn(apify.ACTOR_INFO,requested)
        self.assertFalse(any("/actor-runs?" in url for url in requested))

    def test_status_http_429_after_paid_start_keeps_run_and_enforces_cooldown(self):
        from urllib.error import HTTPError
        now=datetime.now(timezone.utc)
        env={"AET_FACEBOOK_ENABLED":"true",
             "APIFY_TOKEN":"safe-token-value-1234567"}
        with tempfile.TemporaryDirectory() as d:
            with patch.object(apify,"start_actor_async",return_value={
                    "id":"abcXYZ123456","actId":"validactor12345"}) as starter:
                with patch.object(apify,"await_started_actor",
                     side_effect=HTTPError("https://api.apify.com",429,
                                           "rate limited",None,None)):
                    first=apify.collect(d,env,lambda:None,now=now,
                                         account_check=lambda *_:True)
            self.assertEqual(first["status"],"provider_pending")
            self.assertEqual(starter.call_count,1)
            self.assertTrue((Path(d)/apify.PENDING_ACTOR_FILE).is_file())
            self.assertTrue(apify.active_backoff(d,now))
            budget=json.loads((Path(d)/"apify_budget.json").read_text())
            self.assertEqual(budget["calls"],1)
            with patch.object(apify,"start_actor_async") as no_post:
                with patch.object(apify,"read_only_provider_get") as no_get:
                    second=apify.collect(d,env,lambda:None,now=now,
                                         account_check=lambda *_:
                                         self.fail("No billable preflight"))
            self.assertEqual(second["status"],"provider_cooldown")
            self.assertTrue((Path(d)/apify.PENDING_ACTOR_FILE).is_file())
            no_post.assert_not_called()
            no_get.assert_not_called()

    def test_network_poll_failure_after_paid_start_preserves_marker(self):
        from urllib.error import URLError
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            marker={"actor":apify.ACTOR,"started_at":now.isoformat()}
            apify.atomic_json(Path(d)/apify.PENDING_ACTOR_FILE,marker)
            def starter(token,cap,now):
                return {"id":"abcXYZ123456","actId":"validactor12345"}
            def fail_waiter(token,marker):
                raise URLError("disconnected")
            with self.assertRaises(apify.PendingActorError):
                apify.launch_and_collect("safe-token-value-1234567",0.05,d,
                                          lambda:None,now=now,
                                          starter=starter,
                                          wait_for_result=fail_waiter)
            saved=json.loads((Path(d)/apify.PENDING_ACTOR_FILE).read_text())
            self.assertEqual(saved["run_id"],"abcXYZ123456")
            self.assertTrue(apify.active_backoff(d,now))

    def test_known_run_identity_mismatch_never_accepts_dataset(self):
        now=datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            apify.recover_uncertain_run(
                "safe-token-value-1234567",
                {"actor":apify.ACTOR, "started_at":now.isoformat(),
                 "run_id":"abcXYZ123456","actor_id":"validactor12345"},
                getter=lambda url,token: {"data":{"id":"abcXYZ123456",
                           "actId":"differentactor98765","status":"SUCCEEDED",
                           "defaultDatasetId":"abCd12345678"}})

    def test_provider_http_429_creates_real_cooldown_without_second_request(self):
        from urllib.error import HTTPError
        env = {"AET_FACEBOOK_ENABLED":"true",
               "APIFY_TOKEN":"safe-token-value-1234567"}
        now = datetime.now(timezone.utc)
        requests = []
        with tempfile.TemporaryDirectory() as d:
            def restricted(token, cap):
                requests.append(True)
                raise HTTPError("https://api.apify.com", 429, "restricted", None, None)
            first = apify.collect(d, env, lambda: None,
                                  request_fn=restricted, now=now,
                                  account_check=lambda *_: True)
            self.assertEqual(first["status"], "http_error")
            self.assertEqual(first["http_status"], 429)
            self.assertTrue((Path(d)/apify.COOLDOWN_FILE).exists())
            second = apify.collect(d, env, lambda: None,
                                   request_fn=restricted, now=now,
                                   account_check=lambda *_: True)
            self.assertEqual(second["status"], "provider_cooldown")
            self.assertEqual(len(requests), 1)
            self.assertEqual(json.loads((Path(d)/"apify_budget.json").read_text())["calls"], 1)

    def test_empty_or_steering_wheel_only_response_never_erases_valid_cars(self):
        env = {"AET_FACEBOOK_ENABLED":"true",
               "APIFY_TOKEN":"safe-token-value-1234567"}
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            old = [{"url":"https://www.facebook.com/marketplace/item/111/",
                    "title":"Opel Corsa 2012", "price":900}]
            (Path(d)/"apify_kandidaten.json").write_text(json.dumps(old))
            for rows, expected in (([], "empty_response"),
                                   ([{"url":"https://www.facebook.com/marketplace/item/222/",
                                      "title":"Stuurwiel Opel Corsa",
                                      "price":450}], "no_vehicle_candidates")):
                outcome = apify.collect(d, env, lambda: None,
                                        request_fn=lambda *_: rows,
                                        now=now, account_check=lambda *_: True)
                self.assertEqual(outcome["status"], expected)
                self.assertEqual(json.loads((Path(d)/"apify_kandidaten.json").read_text()),old)

    def test_same_dutch_price_parser_in_facebook_and_marktplaats(self):
        from facebook_hunter import money
        self.assertEqual(money("€ 1.250,00"), 1250)
        self.assertEqual(money("€ 1.250"), 1250)
        self.assertEqual(money({"amount":"€ 2.999"}), 2999)
        self.assertIsNone(money("€ 99"))
        self.assertIsNone(money("€ 20.000"))
        self.assertIsNone(money("Onbekend"))

    def test_provider_budget_is_persisted_before_chargeable_request(self):
        env={"AET_FACEBOOK_ENABLED":"true",
             "APIFY_TOKEN":"safe-token-value-1234567"}
        with tempfile.TemporaryDirectory() as d:
            order=[]
            def persist():
                order.append("persistent")
            def provider(token, cap):
                budget=json.loads((Path(d)/"apify_budget.json").read_text())
                self.assertEqual(budget["calls"], 1)
                self.assertEqual(budget["reserved_usd"], 0.05)
                self.assertEqual(order[0], "persistent")
                return []
            status=apify.collect(d, env, persist, request_fn=provider,
                                 account_check=lambda *_: True)
            self.assertEqual(status["status"], "empty_response")
            self.assertGreaterEqual(len(order), 2)

    def test_real_hydedata_output_includes_photo_and_private_seller(self):
        env = {"AET_FACEBOOK_ENABLED": "true",
               "APIFY_TOKEN": "safe-token-value-1234567"}
        photo = "https://scontent-ams2-1.xx.fbcdn.net/v/t1/opel.jpg"
        now = datetime.now(timezone.utc)
        samples = [
            {"id": "1234567",
             "url": "https://www.facebook.com/marketplace/item/1234567/",
             "title": "Opel Corsa 2012", "price": 1250,
             "listedAt": now.isoformat(), "photoUrl": photo,
             "sellerType": "PRIVATE_SELLER"},
            {"id": "2222222",
             "url": "https://www.facebook.com/marketplace/item/2222222/",
             "title": "Fiat Punto 2009", "price": 600,
             "listedAt": now.isoformat(), "sellerType": "DEALER"},
            {"id": "3333333",
             "url": "https://www.facebook.com/marketplace/item/3333333/",
             "title": "Toyota Aygo 2013", "price": 1800,
             "listedAt": now.isoformat(), "isSold": True},
        ]
        with tempfile.TemporaryDirectory() as d:
            status = apify.collect(d, env, lambda: None,
                request_fn=lambda *_: samples, now=now,
                account_check=lambda *_: True)
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["accepted"], 1)
            cars = json.loads((Path(d) / "apify_kandidaten.json").read_text())
            self.assertEqual(len(cars), 1)
            self.assertEqual(cars[0]["seller_type"], "particulier")
            self.assertEqual(cars[0]["photo_urls"], [photo])




if __name__ == "__main__":
    unittest.main()
