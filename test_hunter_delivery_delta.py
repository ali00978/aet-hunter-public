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


    def test_material_repair_updates_without_noisy_scan_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            initial = candidate(101)
            _, saved, _ = plan(combined([initial]), "marktplaats", load(tmp))
            acknowledge(tmp, "marktplaats", saved)
            scanned = {**initial, "checked_at": "2026-10-25T02:00:00+00:00",
                       "issues": ["Niet opnieuw gecontroleerd tijdens laatste scan"],
                       "rank": 14}
            quiet, _, stats = plan(combined([scanned]), "marktplaats", load(tmp))
            self.assertEqual(quiet["count"], 0)
            self.assertEqual(stats["repeated"], 1)
            repair = {**scanned, "issues": [
                "Niet opnieuw gecontroleerd tijdens laatste scan",
                "APK loopt af: mogelijke herstelkosten controleren"]}
            changed, signatures, stats = plan(
                combined([repair]), "marktplaats", load(tmp))
            self.assertEqual(changed["count"], 1)
            self.assertEqual(stats["changed"], 1)
            acknowledge(tmp, "marktplaats", signatures)
            repeat, _, _ = plan(combined([repair]), "marktplaats", load(tmp))
            self.assertEqual(repeat["count"], 0)
            repaired, _, stats = plan(combined([initial]), "marktplaats", load(tmp))
            self.assertEqual(repaired["count"], 1)
            self.assertEqual(stats["changed"], 1)

    def test_material_market_value_change_not_comparison_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            initial = candidate(102)
            first, signatures, _ = plan(
                combined([initial]), "marktplaats", load(tmp))
            self.assertEqual(first["count"], 1)
            acknowledge(tmp, "marktplaats", signatures)
            valued = {**initial, "median_comparable_asking_price_eur": 2500,
                      "indicative_resale_eur": 2200}
            valued_delta, signatures, stats = plan(
                combined([valued]), "marktplaats", load(tmp))
            self.assertEqual((valued_delta["count"], stats["changed"]), (1, 1))
            acknowledge(tmp, "marktplaats", signatures)
            small_sample_shift = {**valued,
                                  "median_comparable_asking_price_eur": 2520,
                                  "comparable_count": 5}
            no_noise, _, _ = plan(
                combined([small_sample_shift]), "marktplaats", load(tmp))
            self.assertEqual(no_noise["count"], 0)
            meaningful_shift = {**small_sample_shift,
                                "median_comparable_asking_price_eur": 2710}
            alert, signatures, _ = plan(
                combined([meaningful_shift]), "marktplaats", load(tmp))
            self.assertEqual(alert["count"], 1)
            acknowledge(tmp, "marktplaats", signatures)
            confidence_lost, _, _ = plan(
                combined([initial]), "marktplaats", load(tmp))
            self.assertEqual(confidence_lost["count"], 1)

    def test_facebook_fingerprint_unchanged_by_marktplaats_rule(self):
        facebook = {"id": "facebook:123456789", "source": "facebook",
                    "title": "TEST Opel Corsa", "asking_price_eur": 1000}
        with_metadata = {**facebook,
                         "median_comparable_asking_price_eur": 2400,
                         "issues": ["APK loopt af: mogelijke herstelkosten controleren"]}
        self.assertEqual(fingerprint(facebook), fingerprint(with_metadata))


class BroaderOfflineCandidateSelectionTests(unittest.TestCase):
    """Synthetic cars only; no HTTP or added marketplace requests."""

    @staticmethod
    def raw(i, price=1500):
        return {"url":"https://www.marktplaats.nl/v/auto-s/opel/m"
                + str(10000+i) + "-test-corsa",
                "title":"TEST Opel Corsa 2012 #" + str(i), "year":2012,
                "mileage_km":110000, "seller_type":"particulier",
                "price_type":"FIXED", "price":price,
                "checked_at":"2026-10-25T00:00:00+00:00",
                "potential_category":"vaste_vraagprijs"}

    def test_new_items_beyond_old_top_30_reach_aet_pro(self):
        from hunter_pro_export import build_payload
        feed={"version":1,"cars":[self.raw(i) for i in range(45)]}
        original=build_payload(feed)
        self.assertEqual(original["count"],30)
        known={item["id"]:fingerprint(item) for item in original["candidates"]}
        updated=build_payload(feed,known_fingerprints=known)
        expected={"marktplaats:m"+str(10000+i) for i in range(30,45)}
        self.assertTrue(expected.issubset({x["id"] for x in updated["candidates"]}))
        picked, _, counts=plan({"schema":"aet-pro.hunter.v2",
                                "candidates":updated["candidates"]},
                               "marktplaats",{"marktplaats":known,"facebook":{}})
        self.assertEqual(counts["new_to_delivery"],15)
        self.assertEqual(counts["repeated"],15)
        self.assertEqual(picked["count"],15)

    def test_price_drop_prioritized_and_unchanged_skipped(self):
        from hunter_pro_export import build_payload
        rows=[self.raw(i) for i in range(30)]
        feed={"version":1,"cars":rows}
        known={item["id"]:fingerprint(item)
               for item in build_payload(feed)["candidates"]}
        baseline=build_payload(feed,known_fingerprints=known)
        selected,_,counts=plan({"schema":"aet-pro.hunter.v2",
                                 "candidates":baseline["candidates"]},
                                "marktplaats",{"marktplaats":known,"facebook":{}})
        self.assertEqual(selected["count"],0)
        self.assertEqual(counts["repeated"],30)
        rows[29]={**rows[29],"price":1200,
                  "checked_at":"2026-10-25T02:00:00+00:00"}
        changed=build_payload(feed,known_fingerprints=known)
        self.assertEqual(changed["candidates"][0]["id"],"marktplaats:m10029")
        picked,_,counts=plan({"schema":"aet-pro.hunter.v2",
                              "candidates":changed["candidates"]},
                             "marktplaats",{"marktplaats":known,"facebook":{}})
        self.assertEqual(picked["count"],1)
        self.assertEqual(counts["changed"],1)


class HundredCandidateHandoffTests(unittest.TestCase):
    def test_hundred_distinct_cars_deliver_exactly_once(self):
        from hunter_pro_export import build_payload
        from hunter_sync import envelope
        from datetime import datetime, timezone
        now=datetime.now(timezone.utc)
        rows=[BroaderOfflineCandidateSelectionTests.raw(i) for i in range(100)]
        for row in rows:
            row["checked_at"]=now.isoformat()
        mp=build_payload({"version":1,"cars":rows},limit=100)
        self.assertEqual(mp["count"],100)
        self.assertEqual(len({r["id"] for r in mp["candidates"]}),100)
        packet={"schema":"aet-pro.hunter.v2","count":100,"candidates":mp["candidates"],
                "platform_counts":{"marktplaats":100,"facebook":0},
                "revision":"a"*64}
        scanned={"checked_at":now.isoformat(),"scan_health":"ok"}
        package=envelope("marktplaats",54321,1,now=now,combined=packet,
                         source_status=scanned)
        self.assertEqual(len(package["scan"]["candidate_ids"]),100)
        with tempfile.TemporaryDirectory() as tmp:
            result, signatures, counts=plan(packet,"marktplaats",load(tmp))
            self.assertEqual(result["count"],100)
            self.assertEqual(counts["new_to_delivery"],100)
            acknowledge(tmp,"marktplaats",signatures)
            repeated,_,counts2=plan(packet,"marktplaats",load(tmp))
            self.assertEqual(repeated["count"],0)
            self.assertEqual(counts2["repeated"],100)

    def test_over_capacity_rejected_and_default_remains_thirty(self):
        from hunter_pro_export import build_payload
        rows=[BroaderOfflineCandidateSelectionTests.raw(i) for i in range(101)]
        feed={"version":1,"cars":rows}
        self.assertEqual(build_payload(feed)["count"],30)
        self.assertEqual(build_payload(feed,limit=100)["count"],100)
        with self.assertRaises(ValueError):
            build_payload(feed,limit=101)
        packet={"schema":"aet-pro.hunter.v2","candidates":[
            candidate(10000+i) for i in range(101)]}
        with self.assertRaisesRegex(ValueError,"Too many"):
            plan(packet,"marktplaats",{"marktplaats":{},"facebook":{}})


if __name__ == "__main__":
    unittest.main()
