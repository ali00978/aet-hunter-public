"""Conservative Facebook automotive filter and provider image regression."""
import unittest
from facebook_vehicle_filter import is_vehicle_title,provider_photos
class FacebookAutomotiveClassificationTests(unittest.TestCase):
    def test_complete_cars_with_minor_bumper_damage_not_misclassified(self):
        cars=[
            "Volkswagen Golf 2008 APK bumper schade",
            "Opel Corsa 2009 178.000 km voorbumper beschadigd",
            "Renault Clio 2011 achterbumper krasjes 165000 km",
            "Toyota Aygo 2010 rijdt goed, schade aan bumper",
        ]
        for title in cars:
            with self.subTest(car=title):
                self.assertTrue(is_vehicle_title(title))
        loose_parts=[
            "Golf 7 achterbumper met kras",
            "Opel Corsa 2009 voorbumper beschadigd",
            "Audi A3 bumper schade voor onderdelen",
            "VW Polo 2011 APK voorbumper schade en velgen",
            "Kia Picanto 2015 losse achterbumper beschadigd",
        ]
        for title in loose_parts:
            with self.subTest(part=title):
                self.assertFalse(is_vehicle_title(title))

    def test_real_false_positives_are_rejected(self):
        titles=[
          "Audi S-Line 8V Stuurwiel met Airbag en Bediening",
          "BMW E46 sportstuur compleet",
          "Volkswagen Golf m stuur",
          "Opel Corsa steering wheel",
          "Audi A3 Lenkrad",
          "Toyota Yaris wieldoppen",
          "Kia Picanto achterbumper",
          "Kia Ceed 2017 – 4 complete wielen te koop",
          "2006 bmw k1200gt 155pk",
          "BMW E46 M stuurwiel",
          "Toyota Aygo velgen en winterbanden",
          "VW Golf 7 navigatiesysteem",
          "Volvo 340 1.7",
          "Alfa Romeo 147 2008",
          "Citroen C1 motorblok los",
          "Renault Clio modelauto 1:18",
          "Opel Corsa onderdelen / sloop",
          "BMW M sport bumper",
          "Audi A3 lichtmetalen velgen 18 inch",
          "Suzuki Swift voor onderdelen",
          "Mercedes A170 achterlicht",
          "Fiat Punto carplay adapter",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(is_vehicle_title(title))
    def test_genuine_vehicle_titles_stay_visible(self):
        examples=[
            "Renault Clio 2005", "Ford KA", "Nissan micra",
            "Toyota Aygo 1.0 VVT-i 5-deurs – 2014 – Bruin",
            "Volswagen polo 1.4 pmi Nieuwe motor/apk", # typo compensated by model
            "BMW e46 323ci 1999 apk verlopen","Mercedes-Benz A170 Automaat Nieuwe APK 137.900",
            "Opel Corsa 2008 1.2 Airco","Fiat [hidden information] AIRCO",
            "Volkswagen Golf 2009", "Peugeot 206 2001 nieuwe apk",
        ]
        for title in examples:
            with self.subTest(title=title):
                self.assertTrue(is_vehicle_title(title))
    def test_only_safe_provider_photos(self):
        raw={"photos":[{"url":"https://scontent-ams2-1.xx.fbcdn.net/v/t39/abc.jpg?stp=123"},
                       {"url":"https://scontent-ams2-1.xx.fbcdn.net/v/t39/abc.jpg?stp=123"},
                       {"url":"http://www.facebook.com/not-https"},
                       {"imageUrl":"https://notfbcdn.net/a.jpg"}],
             "thumbnailUrl":"https://example.com/evil.jpg"}
        self.assertEqual(provider_photos(raw),["https://scontent-ams2-1.xx.fbcdn.net/v/t39/abc.jpg?stp=123"])
    def test_real_hydedata_photo_url_is_recognized(self):
        url = "https://scontent-ams2-1.xx.fbcdn.net/v/t1/auto.jpg"
        self.assertEqual(provider_photos({"photoUrl": url}), [url])
        self.assertEqual(provider_photos({"photoUrl": "https://attacker.invalid/a.jpg"}), [])

    def test_empty_when_provider_does_not_supply_photo(self):
        self.assertEqual(provider_photos({"image":None,"photos":[]}),[])
if __name__=="__main__":unittest.main()
