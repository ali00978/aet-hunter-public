"""Offline regression: repeated scans are NOT thirty new cars each run."""
import json
import tempfile
import unittest
from pathlib import Path

from hunter_delivery_delta import SCHEMA, FILE, fingerprint, load, plan, acknowledge


def candidate(id_, price=1500, checked="2026-10-25T00:00:00+00:00",
              photos=None, rank=1):
    return {
        "id": "marktplaats:m"+str(id_), "source": "marktplaats",
        "title": "TEST Opel Corsa 2008", "year": 2008,
        "mileage_km": 201000, "seller_classification": "particulier",
        "location_label": "TEST Almere", "price_type": "advertised_fixed",
        "asking_price_eur": price, "minimum_bid_eur": None,
        "search_card_hint_eur": None, "checked_at": checked,
        "photo_urls": photos or [], "rank": rank,
        "availability": "not_confirmed",
        "content_hash": "a"*64,
    }


def combined(items):
    return {
        "schema": "aet-pro.hunter.v2",
        "candidates": items, "count": len(items),
        "platform_counts": {"marktplaats": len(items), "facebook": 0},
        "revision": "b"*64,
    }


class AcknowledgedDeltaTests(unittest.TestCase):
    def test_identical_repeat_is_empty_but_scan_still_accounted_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load(tmp)
            first, signatures, totals = plan(
                combined([candidate(101), candidate(102)]), "marktplaats", state)
            self.assertEqual((first["count"], totals["new_to_delivery"]), (2, 2))
            self.assertFalse((Path(tmp)/FILE).exists(), "Planning cannot acknowledge")
            acknowledge(tmp, "marktplaats", signatures)
            revised = combined([
                candidate(101, checked="2026-10-25T02:00:00+00:00",
                          photos=["https://images.marktplaats.com/api/v1/signed"],
                          rank=7),
                candidate(102, checked="2026-10-25T02:00:00+00:00", rank=3),
            ])
            second, sigs, stats = plan(revised, "marktplaats", load(tmp))
            self.assertEqual(second["candidates"], [])
            self.assertEqual(second["platform_counts"], {"marktplaats":0,"facebook":0})
            self.assertEqual(stats, {"observed":2,"new_to_delivery":0,
                                     "changed":0,"repeated":2})
            self.assertEqual(set(sigs), {"marktplaats:m101", "marktplaats:m102"})

    def test_price_change_or_truly_new_listing_is_delivered(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, signs, _ = plan(combined([candidate(101)]), "marktplaats", load(tmp))
            acknowledge(tmp,"marktplaats",signs)
            current = combined([candidate(101,price=1200),candidate(102,price=1700)])
            delta, _, counters = plan(current,"marktplaats",load(tmp))
            self.assertEqual({x["id"] for x in delta["candidates"]},
                             {"marktplaats:m101","marktplaats:m102"})
            self.assertEqual(counters["changed"],1)
            self.assertEqual(counters["new_to_delivery"],1)
            self.assertEqual(delta["count"],2)
            self.assertEqual(len(delta["revision"]),64)

    def test_failed_delivery_does_not_advance_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, signs, _ = plan(combined([candidate(101)]),
                                   "marktplaats", load(tmp))
            self.assertEqual(first["count"],1)
            # Simulate AET Pro rejection: do NOT call acknowledge.
            again, _, _ = plan(combined([candidate(101)]),
                                "marktplaats", load(tmp))
            self.assertEqual(again["count"],1)
            calls=[]
            acknowledge(tmp,"marktplaats",signs,lambda:calls.append("saved"))
            self.assertEqual(calls,["saved"])
            self.assertEqual(load(tmp)["marktplaats"]["marktplaats:m101"],
                             fingerprint(candidate(101)))

    def test_cross_platform_independent_and_corrupt_checkpoint_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fb={"id":"facebook:234567890","source":"facebook",
                "title":"TEST Renault Clio","price_type":"provider_price_unverified",
                "search_card_hint_eur":950,"content_hash":"d"*64}
            allitems=combined([candidate(101),fb])
            allitems["platform_counts"]={"marktplaats":1,"facebook":1}
            _, p, _=plan(allitems,"marktplaats",load(tmp))
            acknowledge(tmp,"marktplaats",p)
            selected, f, counters=plan(allitems,"facebook",load(tmp))
            self.assertEqual(selected["count"],1)
            self.assertEqual(selected["platform_counts"],{"marktplaats":0,"facebook":1})
            acknowledge(tmp,"facebook",f)
            self.assertEqual(load(tmp)["marktplaats"]["marktplaats:m101"],
                             fingerprint(candidate(101)))
            self.assertEqual(load(tmp)["facebook"]["facebook:234567890"],
                             fingerprint(fb))
            (Path(tmp)/FILE).write_text('{"schema":"WRONG"}')
            with self.assertRaises(ValueError):
                load(tmp)

    def test_missing_or_duplicate_id_rejected_and_no_candidate_leak(self):
        with self.assertRaises(ValueError):
            fingerprint({"source":"marktplaats"})
        with self.assertRaises(ValueError):
            plan(combined([candidate(101),candidate(101)]),"marktplaats",
                 {"marktplaats":{},"facebook":{}})


if __name__ == "__main__":
    unittest.main()
