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
