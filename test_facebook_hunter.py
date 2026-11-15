"""Facebook Hunter -> AET PRO regression tests; entirely offline."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import facebook_hunter as fb
class FacebookHunterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.good={"url":"https://www.facebook.com/marketplace/item/11111111111/",
          "title":"Toyota Aygo 2012 airco","price":1200,"listedAt":self.now.isoformat(),
          "first_seen_at":self.now.isoformat(),"source":"apify_hydedata","score":77}
    def test_word_boundaries_prevent_embedded_trade_matches(self):
        self.assertTrue(fb.has_word("VW Polo 2009","polo"))
        self.assertFalse(fb.has_word("VW Poloshirt","polo"))
        self.assertFalse(fb.has_word("alfa Romeo","rome"))
        self.assertTrue(fb.is_trade_model("Volkswagen Polo 2009"))
        self.assertFalse(fb.is_trade_model("Honda Civica Audiotape 2013"))
        self.assertTrue(fb.has_word("Almere Buiten","almere"))

    def test_dealer_aliases_and_conflicting_seller_fields_are_rejected(self):
        for label in ("handelaar","dealer","DEALER","business","BUSINESS_SELLER",
                      "dealership","professional","trader","company","zakelijk"):
            with self.subTest(seller=label):
                self.assertIsNone(fb.normalize(
                    {**self.good,"seller_type":label},"Apify",self.now))
        self.assertIsNone(fb.normalize(
            {**self.good,"seller_type":"particulier",
             "seller_platform_type":"dealer"},"Apify",self.now))
        valid=fb.normalize({**self.good,"seller_type":"private_seller"},
                           "Apify",self.now)
        self.assertEqual(valid["seller_classification"],"particulier")

    def test_business_name_and_nested_seller_flags_override_private_claim(self):
        hidden_dealers = (
            {"seller_type": "particulier", "sellerName": "Garage de Markt"},
            {"sellerType": "PRIVATE_SELLER", "seller_name": "Autobedrijf West B.V."},
            {"seller_type": "particulier",
             "seller": {"name": "Occasion Center Almere"}},
            {"seller_type": "particulier", "seller": {"type": "company"}},
            {"seller_type": "particulier", "seller_platform_type": "business_seller"},
        )
        for fields in hidden_dealers:
            with self.subTest(fields=fields):
                self.assertTrue(fb.business_seller_evidence(fields))
                self.assertIsNone(fb.normalize(
                    {**self.good, **fields}, "Apify", self.now))
        private = {**self.good, "seller_type": "particulier",
                   "sellerName": "Jan de Vries"}
        self.assertFalse(fb.business_seller_evidence(private))
        self.assertIsNotNone(fb.normalize(private, "Apify", self.now))

    def test_conflicting_odometer_readings_cannot_be_hidden(self):
        for fields in ({"mileage_km":80000,"mileage":340000},
                       {"mileage_km":340000,"mileage":80000},
                       {"mileage_km":155000,"mileage":215000}):
            with self.subTest(readings=fields):
                self.assertIsNone(fb.normalize(
                    {**self.good,**fields},"Apify",self.now))
        close=fb.normalize({**self.good,"mileage_km":160000,
                            "mileage":160500},"Apify",self.now)
        self.assertEqual(close["mileage_km"],160000)
        self.assertIsNotNone(fb.normalize(
            {**self.good,"mileage_km":None,"mileage":190000},
            "Apify",self.now))

    def test_explicit_bidding_labels_not_misread_as_price(self):
        # A provider may return 1200 as the current bid rather than a
        # purchase asking price. Neither can be sent as a priced vehicle.
        cases=({"priceType":"MIN_BID"},
               {"price_type":"BIDDING"},
               {"pricingType":"OFFER"},
               {"priceFormatted":"Bieden vanaf € 1.200"},
               {"priceLabel":"Prijs op aanvraag"},
               {"priceText":"Make an offer"})
        for fields in cases:
            with self.subTest(fields=fields):
                self.assertIsNone(fb.normalize(
                    {**self.good, **fields},"Apify",self.now))
        self.assertIsNotNone(fb.normalize(
            {**self.good,"priceType":"FIXED","priceFormatted":"€ 1.200"},
            "Apify",self.now))

    def test_facebook_numeric_price_cannot_disagree_with_visible_price(self):
        for value in ("€ 2.500", "EUR 2500", "€ 1.200 of € 1.900",
                      "€ 1.200 per maand", "vanaf € 1.200",
                      "€ 1.200 aanbetaling"):
            with self.subTest(price_label=value):
                self.assertIsNone(fb.normalize(
                    {**self.good, "priceFormatted": value},
                    "Apify", self.now))
        for field in ("priceText", "priceLabel"):
            self.assertIsNone(fb.normalize(
                {**self.good, field: "€ 9.500"},
                "Apify", self.now))
        for value in ("€ 1.200", "€ 1.200,-", "EUR 1200",
                      "€1.200,00", "€ 1.200 vaste prijs"):
            with self.subTest(matching_price=value):
                self.assertIsNotNone(fb.normalize(
                    {**self.good, "priceFormatted": value},
                    "Apify", self.now))
        self.assertIsNotNone(fb.normalize(self.good, "Apify", self.now))

    def test_facebook_foreign_currency_and_suffix_price_labels_fail_closed(self):
        for label in ("$ 1,200", "£ 1,200", "1200 USD",
                      "GBP 1200", "1.200 CHF", "€ 1.200 /maand",
                      "€ 1.200 per month", "2.500 EUR", "€ 1200; $ 950",
                      "2.500"):
            with self.subTest(label=label):
                for field in ("priceFormatted", "priceText", "priceLabel"):
                    self.assertIsNone(fb.normalize(
                        {**self.good, field: label}, "Apify", self.now))
        for label in ("1200 EUR", "1.200 EUR", "1.200 €",
                      "1.200", "EUR 1.200", "€ 1.200"):
            with self.subTest(agreed_price=label):
                self.assertIsNotNone(fb.normalize(
                    {**self.good, "priceFormatted": label},
                    "Apify", self.now))

    def test_price_mileage_seller_unknown_flagged(self):
        r=fb.normalize(self.good,"Apify",self.now)
        self.assertEqual(r["id"],"facebook:11111111111")
        self.assertIsNone(r["asking_price_eur"])
        self.assertEqual(r["search_card_hint_eur"],1200)
        self.assertIsNone(r["confirmed_profit_eur"])
        self.assertTrue(any("Privéverkoper" in x for x in r["issues"]))
        self.assertTrue(any("Kilometerstand" in x for x in r["issues"]))
    def test_severe_damage_overbudget_and_bad_url_excluded(self):
        cases=[{**self.good,"title":"Fiat Punto met motor schade"},
               {**self.good,"mileage_km":330001},
               {**self.good,"price":4000},
               {**self.good,"seller_type":"handelaar"},
               {**self.good,"url":"https://evil.test/marketplace/item/11111111111/"},
               {**self.good,"listedAt":(self.now-timedelta(days=70)).isoformat()}]
        self.assertTrue(all(fb.normalize(r,"Apify",self.now) is None for r in cases))
    def test_facebook_year_strings_and_local_city_filter_are_consistent(self):
        example = {**self.good, "title": "Toyota Aygo", "year": "2014",
                   "city": "Emmeloord"}
        correct = fb.normalize(example, "Apify", self.now)
        self.assertIsNotNone(correct)
        self.assertEqual(correct["year"], 2014)
        self.assertTrue(any(fb.has_word("Emmeloord", place)
                            for place in fb.LOCAL_HINTS))
        self.assertIsNone(fb.normalize(
            {**example, "year_detected": "2012"}, "Apify", self.now))
        for remote in ("Groningen", "Groningen (GR)", "Maastricht (LI)"):
            with self.subTest(location=remote):
                self.assertIsNone(fb.normalize(
                    {**example, "city": remote}, "Apify", self.now))
        self.assertIsNotNone(fb.normalize(
            {**example, "city": "Almere"}, "Apify", self.now))

    def test_unfamiliar_date_permitted_with_note(self):
        row={**self.good,"listedAt":None}
        result=fb.normalize(row,"Apify",self.now)
        self.assertIn("Advertentiedatum onbekend",result["issues"])
    def test_repeat_listing_uses_real_check_not_first_discovery(self):
        first=(self.now-timedelta(days=15)).isoformat()
        posted=(self.now-timedelta(days=20)).isoformat()
        row={**self.good,"first_seen_at":first,"checked_at":self.now.isoformat(),
             "listedAt":posted}
        fresh=fb.normalize(row,"Apify",self.now)
        self.assertEqual(fresh["checked_at"],self.now.isoformat())
        self.assertEqual(fresh["posted_at"],posted)
        old=fb.normalize({**row,"checked_at":first},"Apify",self.now,
                         provider_at=self.now)
        self.assertEqual(old["checked_at"],first)
        self.assertEqual(fb.for_pro([old],archived=True)[0]["availability"],
                         "historical_unverified")

    def test_provider_price_dict_is_not_confirmed(self):
        row={**self.good,"price":{"amount":850}}
        result=fb.normalize(row,"Social Fetch",self.now)
        self.assertEqual(result["search_card_hint_eur"],850)
        self.assertEqual(result["price_type"],"provider_price_unverified")
    def test_new_delivery_facebook_ads_get_first_thirty_slots_from_saved_pool(self):
        # Previous top-ranked, acknowledged ads must not conceal unseen cars
        # at positions 31..65. No provider or network request occurs.
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rows=[]
            for number in range(70):
                car={**self.good,
                     "url":"https://www.facebook.com/marketplace/item/"
                           +str(880000+number)+"/",
                     "title":"Toyota Aygo 2012",
                     "seller_type":"particulier"}
                if number>=30:
                    car["price"]=1350
                    car["mileage_km"]=240000
                else:
                    car["price"]=650
                    car["mileage_km"]=95000
                rows.append(car)
            (root/"apify_kandidaten.json").write_text(
                json.dumps(rows),encoding="utf-8")
            (root/"apify_status.json").write_text(
                json.dumps({"status":"ok","checked_at":self.now.isoformat()}),
                encoding="utf-8")
            known={"facebook:"+str(880000+i) for i in range(30)}
            with patch.object(fb,"ROOT",root):
                original=fb.find_candidates(now=self.now)
                first=fb.find_candidates(
                    now=self.now,known_delivery_ids=known)
                next_round=fb.find_candidates(
                    now=self.now,known_delivery_ids=known |
                    {row["id"] for row in first["candidates"]})
            self.assertEqual(len(original["candidates"]),30)
            self.assertEqual(set(row["id"] for row in original["candidates"]),known)
            self.assertEqual(len(first["candidates"]),30)
            self.assertFalse(known & {row["id"] for row in first["candidates"]})
            self.assertEqual(len(next_round["candidates"]),30)
            self.assertFalse((known | {row["id"] for row in first["candidates"]})
                             & {row["id"] for row in next_round["candidates"][:10]})
            self.assertEqual(len({row["id"] for row in first["candidates"]}),30)

    def test_facebook_unsent_priority_rejects_invalid_history_and_cap(self):
        with self.assertRaises(ValueError):
            fb.find_candidates(now=self.now,limit=31)
        with self.assertRaises(ValueError):
            fb.find_candidates(now=self.now,known_delivery_ids=["facebook:123"])

    def test_provider_scan_time_not_replaced_by_previous_listing_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_check = (self.now-timedelta(days=14)).isoformat()
            first = {**self.good, "checked_at": old_check,
                     "first_seen_at": old_check}
            second = {**self.good,
                      "url": "https://www.facebook.com/marketplace/item/22222222222/"}
            second.pop("first_seen_at", None)
            (root/"apify_kandidaten.json").write_text(json.dumps([first, second]))
            (root/"apify_status.json").write_text(json.dumps({
                "status":"ok", "checked_at":self.now.isoformat()}))
            with patch.object(fb, "ROOT", root):
                result = fb.find_candidates(now=self.now)
            rows = {row["id"]: row for row in result["candidates"]}
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows["facebook:11111111111"]["checked_at"], old_check)
            self.assertEqual(rows["facebook:22222222222"]["checked_at"],
                             self.now.isoformat())

    def test_provider_sold_snapshot_without_own_timestamp_still_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recently_checked = (self.now-timedelta(days=1)).isoformat()
            earlier_check = (self.now-timedelta(days=20)).isoformat()
            available = {**self.good, "checked_at": recently_checked}
            different_older_car = {
                **self.good,
                "url": "https://www.facebook.com/marketplace/item/33333333333/",
                "checked_at": earlier_check, "first_seen_at": earlier_check
            }
            sold = {"url":self.good["url"], "isSold":True}
            (root/"apify_kandidaten.json").write_text(
                json.dumps([available, different_older_car, sold]))
            (root/"apify_status.json").write_text(json.dumps({
                "status":"ok", "checked_at":self.now.isoformat()}))
            with patch.object(fb, "ROOT", root):
                result = fb.find_candidates(now=self.now)
            ids = {row["id"] for row in result["candidates"]}
            self.assertNotIn("facebook:11111111111", ids)
            self.assertIn("facebook:33333333333", ids)

    def test_newer_facebook_source_overrules_old_rich_price_and_photo(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old={**self.good, "price":650, "mileage_km":90000,
                 "year":2012, "checked_at":(self.now-timedelta(days=2)).isoformat(),
                 "photoUrl":"https://scontent.xx.fbcdn.net/v/test/old-car.jpg"}
            new={**self.good, "price":1950,"mileage_km":None,
                 "checked_at":self.now.isoformat(),"year":None}
            (root/"apify_kandidaten.json").write_text(json.dumps([new]))
            (root/"socialfetch_kandidaten.json").write_text(json.dumps([old]))
            for name in ("apify_status.json","socialfetch_status.json"):
                (root/name).write_text(json.dumps(
                    {"status":"ok","checked_at":self.now.isoformat()}))
            with patch.object(fb,"ROOT",root):
                result=fb.find_candidates(now=self.now)
            self.assertEqual(result["count"],1)
            self.assertEqual(result["candidates"][0]["search_card_hint_eur"],1950)
            self.assertEqual(result["candidates"][0]["photo_urls"],[])
            self.assertEqual(result["candidates"][0]["mileage_km"],None)

    def test_cross_timezone_facebook_source_ranking_uses_actual_utc_time(self):
        # 16:00+02:00 is 14:00 UTC, older than 14:30+00:00, despite
        # the lexicographically larger clock hour.
        moment=datetime(2026,10,29,15,0,tzinfo=timezone.utc)
        older={**self.good,"price":650,
               "listedAt":moment.isoformat(),"first_seen_at":moment.isoformat(),
               "checked_at":"2026-10-29T16:00:00+02:00"}
        fresher={**self.good,"price":1950,
                 "listedAt":moment.isoformat(),"first_seen_at":moment.isoformat(),
                 "checked_at":"2026-10-29T14:30:00+00:00"}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,rows in (("apify_kandidaten.json",[older]),
                              ("socialfetch_kandidaten.json",[fresher])):
                (root/name).write_text(json.dumps(rows))
            for name in ("apify_status.json","socialfetch_status.json"):
                (root/name).write_text(json.dumps(
                    {"status":"ok","checked_at":moment.isoformat()}))
            with patch.object(fb,"ROOT",root):
                result=fb.find_candidates(now=moment)
            self.assertEqual(result["candidates"][0]["search_card_hint_eur"],1950)

    def test_sold_and_nonstandard_facebook_ports_are_not_exported(self):
        for modification in (
            {"isSold":True}, {"isPending":True},
            {"url":"https://www.facebook.com:444/marketplace/item/11111111111/"},
            {"url":"https://www.facebook.com:invalid/marketplace/item/11111111111/"},
        ):
            self.assertIsNone(fb.normalize({**self.good,**modification},
                                           "Apify",self.now))
        self.assertIsNotNone(fb.normalize(
            {**self.good,"url":"https://www.facebook.com:443/marketplace/item/11111111111/"},
            "Apify",self.now))

    def test_newer_sold_or_pending_snapshot_suppresses_older_available_car(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old=(self.now-timedelta(days=2)).isoformat()
            available={**self.good,"checked_at":old}
            sold={**self.good,"checked_at":self.now.isoformat(),"isSold":True}
            for filename,records in (("apify_kandidaten.json",[available]),
                                     ("socialfetch_kandidaten.json",[sold])):
                (root/filename).write_text(json.dumps(records))
            for filename in ("apify_status.json","socialfetch_status.json"):
                (root/filename).write_text(json.dumps(
                    {"status":"ok","checked_at":self.now.isoformat()}))
            with patch.object(fb,"ROOT",root):
                result=fb.find_candidates(now=self.now)
            self.assertEqual(result["count"],0)
            (root/"socialfetch_kandidaten.json").write_text(json.dumps([
                {**sold,"isSold":False,"isPending":True}]))
            with patch.object(fb,"ROOT",root):
                self.assertEqual(fb.find_candidates(now=self.now)["count"],0)

    def test_old_sold_snapshot_cannot_hide_newer_available_car(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old=(self.now-timedelta(days=2)).isoformat()
            newer={**self.good,"checked_at":self.now.isoformat()}
            sold={**self.good,"checked_at":old,"isSold":True}
            (root/"apify_kandidaten.json").write_text(json.dumps([newer]))
            (root/"socialfetch_kandidaten.json").write_text(json.dumps([sold]))
            for filename in ("apify_status.json","socialfetch_status.json"):
                (root/filename).write_text(json.dumps(
                    {"status":"ok","checked_at":self.now.isoformat()}))
            with patch.object(fb,"ROOT",root):
                result=fb.find_candidates(now=self.now)
            self.assertEqual(result["count"],1)

    def test_sources_and_duplicates(self):
        with tempfile.TemporaryDirectory() as path:
            root=Path(path)
            for file in ("apify_kandidaten.json","socialfetch_kandidaten.json"):
                (root/file).write_text(json.dumps([self.good]))
            for file in ("apify_status.json","socialfetch_status.json"):
                (root/file).write_text(json.dumps({"status":"ok","checked_at":self.now.isoformat()}))
            with patch.object(fb,"ROOT",root):
                found=fb.find_candidates(now=self.now)
            self.assertEqual(found["count"],1)
            self.assertEqual(len(found["sources"]),2)
    def test_private_data_not_exported(self):
        r=fb.normalize({**self.good,"seller_name":"Private Person","description":"call 0612345678"},"Apify",self.now)
        p=fb.for_pro([r])
        self.assertNotIn("Private Person",json.dumps(p))
        self.assertNotIn("0612345678",json.dumps(p))
        self.assertEqual(len(p[0]["content_hash"]),64)
    def test_minor_bumper_damage_remains_research_not_verified_profit(self):
        car={**self.good,"title":"Opel Corsa 2009 APK bumper schade",
             "mileage_km":180000,"seller_type":"particulier"}
        normalized=fb.normalize(car,"Apify",self.now)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["title"],car["title"])
        self.assertIsNone(normalized["confirmed_profit_eur"])
        self.assertTrue(normalized["needs_inspection"])
        self.assertIsNone(fb.normalize(
            {**car,"title":"Opel Corsa 2009 achterbumper met kras",
             "mileage_km":180000},"Apify",self.now))

    def test_exclude_parts_from_actual_stored_provider_results(self):
        from facebook_vehicle_filter import is_vehicle_title
        with patch.object(fb, "ROOT", Path(__file__).resolve().parent):
            output=fb.find_candidates(now=self.now)
        self.assertTrue(all(is_vehicle_title(c["title"]) for c in output["candidates"]))
        banned={"Audi S-Line 8V Stuurwiel met Airbag en Bediening",
                "2006 bmw k1200gt 155pk", "Kia Ceed 2017 – 4 complete wielen te koop"}
        self.assertFalse(any(c["title"] in banned for c in output["candidates"]))

    def test_photo_flows_to_normalized_record_and_pro_export(self):
        car={**self.good,"photos":[{"url":"https://scontent.xx.fbcdn.net/v/t39/car.jpg"}]}
        item=fb.normalize(car,"Apify",self.now)
        self.assertEqual(item["photo_urls"],["https://scontent.xx.fbcdn.net/v/t39/car.jpg"])
        exported=fb.for_pro([item])[0]
        self.assertEqual(exported["photo_urls"],item["photo_urls"])

    def test_low_quality_older_expensive_cars_rank_below_trade_worthy_cars(self):
        old={**self.good, "title":"Volkswagen Polo 2002",
             "price":2700, "mileage_km":310000, "seller_type":"onbekend",
             "score":99}
        good={**self.good, "url":"https://www.facebook.com/marketplace/item/22222222/",
              "title":"Renault Clio 2013", "price":850, "mileage_km":125000,
              "seller_type":"particulier", "score":1,
              "photoUrl":"https://scontent.xx.fbcdn.net/v/car.jpg"}
        old_result=fb.normalize(old,"Apify",self.now)
        good_result=fb.normalize(good,"Apify",self.now)
        self.assertLess(old_result["score"],good_result["score"])
        self.assertGreater(good_result["score"],75)
        self.assertLess(old_result["score"],35)
        self.assertTrue(any("Ouder bouwjaar" in t for t in old_result["issues"]))

    def test_trade_brands_only_and_foreign_currency_rejected(self):
        self.assertIsNone(fb.normalize({**self.good,"title":"Honda Civic 2012"},"Apify",self.now))
        self.assertIsNone(fb.normalize({**self.good,"title":"Nissan Micra 2012"},"Apify",self.now))
        self.assertIsNone(fb.normalize({**self.good,"currency":"USD"},"Apify",self.now))
        self.assertIsNone(fb.normalize({**self.good,"priceFormatted":"$1,200"},"Apify",self.now))
        self.assertIsNotNone(fb.normalize({**self.good,"currency":"EUR"},"Apify",self.now))

    def test_minor_repair_gets_a_cautious_penalty_not_major_engine_failure(self):
        good={**self.good,"title":"Renault Clio 2011","price":1100,
              "mileage_km":165000,"seller_type":"particulier"}
        healthy=fb.normalize({**good,"description":"De auto rijdt goed"},
                             "Apify",self.now)
        airco=fb.normalize({**good,"description":"Airco kapot; verder rijdt de auto goed"},
                           "Apify",self.now)
        window=fb.normalize({**good,"title":"Renault Clio 2011 lakschade"},
                            "Apify",self.now)
        self.assertIsNotNone(airco)
        self.assertGreater(airco["score"],24)
        self.assertLess(airco["score"],healthy["score"])
        self.assertEqual(airco["score"],window["score"])
        self.assertTrue(any("Klein herstelpunt" in warning
                            for warning in airco["issues"]))
        self.assertTrue(airco["needs_inspection"])
        self.assertIsNone(airco["confirmed_profit_eur"])
        severe=fb.normalize({**good,
                             "title":"Renault Clio 2011 motorstoring"},
                            "Apify",self.now)
        self.assertLessEqual(severe["score"],24)
        self.assertLess(severe["score"],airco["score"])
        self.assertTrue(any("ernstig technisch risico" in warning
                            for warning in severe["issues"]))

    def test_description_mechanical_warning_reduces_priority(self):
        healthy=fb.normalize({**self.good,"seller_type":"particulier",
                             "mileage_km":110000},"Apify",self.now)
        damaged=fb.normalize({**self.good,"seller_type":"particulier",
                             "mileage_km":110000,
                             "description":"Auto rijdt, maar motorstoring blijft aan"},"Apify",self.now)
        self.assertLess(damaged["score"],healthy["score"])
        self.assertLessEqual(damaged["score"],24)
        self.assertTrue(any("technisch risico" in x for x in damaged["issues"]))

    def test_synthetic_saved_facebook_sources(self):
        """Public CI must not rely on or publish private provider snapshots."""
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            second = {**self.good,
                      "url": "https://www.facebook.com/marketplace/item/22222222222/",
                      "title": "Opel Corsa 2011 airco",
                      "price": 950}
            (root / "apify_kandidaten.json").write_text(json.dumps([self.good]), encoding="utf-8")
            (root / "socialfetch_kandidaten.json").write_text(json.dumps([second]), encoding="utf-8")
            for name in ("apify_status.json", "socialfetch_status.json"):
                (root / name).write_text(
                    json.dumps({"status":"ok","checked_at":self.now.isoformat()}),
                    encoding="utf-8",
                )
            with patch.object(fb, "ROOT", root):
                output = fb.find_candidates(now=self.now)
            self.assertEqual(output["count"], 2)
            self.assertEqual(len({r["id"] for r in output["candidates"]}), 2)
if __name__=="__main__":unittest.main()
