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

    def test_live_saved_facebook_sources(self):
        output=fb.find_candidates()
        self.assertGreater(output["count"],0,"Stored Facebook provider candidates should be readable")
        self.assertEqual(len({r["id"] for r in output["candidates"]}),output["count"])
        print("FACEBOOK_HUNTER_CANDIDATES:",output["count"])
if __name__=="__main__":unittest.main()
