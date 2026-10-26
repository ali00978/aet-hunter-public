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

    def test_title_formatting_ignored_after_safe_checkpoint_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = candidate(301)
            original["title"] = "Opel Corsa 1.2 - 2008"
            # This is the signature format already stored by production before
            # canonicalization shipped. It must NOT trigger a mass redelivery.
            legacy_state = load(tmp)
            legacy_sig = fingerprint(original, legacy=True)
            self.assertNotEqual(legacy_sig, fingerprint(original))
            legacy_state["marktplaats"][original["id"]] = legacy_sig
            (Path(tmp) / FILE).write_text(json.dumps(legacy_state))
            first, signatures, counters = plan(
                combined([original]), "marktplaats", load(tmp))
            self.assertEqual(first["count"], 0)
            self.assertEqual(counters["repeated"], 1)
            acknowledge(tmp, "marktplaats", signatures)
            self.assertEqual(load(tmp)["marktplaats"][original["id"]],
                             fingerprint(original))
            reworded = {
                **original,
                "title": "  OPEL  CORSA  1.2 – 2008!!! ",
                "location_label": "test   ALMERE  ",
                "checked_at": "2026-10-25T02:00:00+00:00",
            }
            quiet, _, counters = plan(
                combined([reworded]), "marktplaats", load(tmp))
            self.assertEqual(quiet["count"], 0)
            self.assertEqual(counters["repeated"], 1)
            # Real new content must still reach AET Pro, not be normalized.
            material = {**reworded, "title": "Opel Corsa 1.4 - 2008"}
            changed, _, stats = plan(
                combined([material]), "marktplaats", load(tmp))
            self.assertEqual(changed["count"], 1)
            self.assertEqual(stats["changed"], 1)
            lower_price = {**reworded, "asking_price_eur": 990}
            changed, _, stats = plan(
                combined([lower_price]), "marktplaats", load(tmp))
            self.assertEqual(changed["count"], 1)
            self.assertEqual(stats["changed"], 1)


    def test_new_only_ignores_price_edits_and_facebook_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            earlier = candidate(100,price=1400)
            _, accepted, _ = plan(combined([earlier]),"marktplaats",load(folder))
            acknowledge(folder,"marktplaats",accepted)
            updated = candidate(100,price=750)
            fresh = candidate(101,price=1000)
            batch, signs, stats = plan(
                combined([updated,fresh]),"marktplaats",load(folder),new_only=True)
            self.assertEqual([x["id"] for x in batch["candidates"]],[fresh["id"]])
            self.assertEqual(stats["new_to_delivery"],1)
            self.assertEqual(stats["changed"],0)
            self.assertEqual(stats["repeated"],1)
            self.assertEqual(set(signs),{fresh["id"]})

    def test_facebook_formatting_is_not_silently_normalized(self):
        facebook = {"id": "facebook:111222333", "source": "facebook",
                    "title": "Opel Corsa 1.2", "content_hash": "a"*64}
        changed = {**facebook, "title": "OPEL CORSA 1.2"}
        self.assertNotEqual(fingerprint(facebook), fingerprint(changed))
        self.assertEqual(fingerprint(facebook, legacy=True),
                         fingerprint(facebook))

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

    def test_recently_rechecked_old_car_stays_known_after_3000_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load(tmp)
            state["marktplaats"] = {
                candidate(i)["id"]: fingerprint(candidate(i))
                for i in range(3000)
            }
            (Path(tmp) / FILE).write_text(json.dumps(state))
            early_id = candidate(1)["id"]
            # Car #1 is still for sale and was just observed again; adding
            # another car must evict the oldest *unseen* ID, not the active one.
            acknowledge(tmp, "marktplaats", {
                early_id: fingerprint(candidate(1)),
                candidate(3000)["id"]: fingerprint(candidate(3000)),
            })
            refreshed = load(tmp)
            self.assertEqual(len(refreshed["marktplaats"]), 3000)
            self.assertIn(early_id, refreshed["marktplaats"])
            self.assertIn(candidate(3000)["id"], refreshed["marktplaats"])
            self.assertNotIn(candidate(0)["id"], refreshed["marktplaats"])
            unchanged, _, stats = plan(
                combined([candidate(1)]), "marktplaats", refreshed)
            self.assertEqual(unchanged["count"], 0)
            self.assertEqual(stats["repeated"], 1)


    def test_compact_old_id_survives_fingerprint_eviction_and_price_change(self):
        from hunter_delivery_delta import delivered_ids, known_delivery_keys
        with tempfile.TemporaryDirectory() as root:
            state=load(root)
            previous=candidate(101)
            state["marktplaats"]={previous["id"]: fingerprint(previous)}
            (Path(root)/FILE).write_text(json.dumps(state))
            # Batch succeeded: remember confirmed IDs as compact encrypted state.
            acknowledge(root,"marktplaats",
                        {candidate(4000)["id"]: fingerprint(candidate(4000))})
            saved=load(root)
            self.assertIn(previous["id"],delivered_ids(saved))
            # Simulate a much later rolling fingerprint checkpoint eviction.
            saved["marktplaats"]={candidate(4000+i)["id"]:
                                 fingerprint(candidate(4000+i))
                                 for i in range(3000)}
            (Path(root)/FILE).write_text(json.dumps(saved))
            older=load(root)
            self.assertNotIn(previous["id"],older["marktplaats"])
            self.assertIn(previous["id"],known_delivery_keys(older))
            refreshed=combined([candidate(101,price=350),
                                candidate(9001,price=500)])
            only_new,signatures,counts=plan(refreshed,"marktplaats",
                                             older,new_only=True)
            self.assertEqual([row["id"] for row in only_new["candidates"]],
                             [candidate(9001)["id"]])
            self.assertEqual(counts["new_to_delivery"],1)
            self.assertNotIn(previous["id"],signatures)

    def test_compact_registry_only_changes_after_confirmed_receipt(self):
        from hunter_delivery_delta import delivered_ids
        with tempfile.TemporaryDirectory() as root:
            state=load(root)
            batch,sigs,_=plan(combined([candidate(9009)]),
                               "marktplaats",state,new_only=True)
            self.assertEqual(batch["count"],1)
            self.assertFalse(delivered_ids(load(root)))
            acknowledge(root,"marktplaats",sigs)
            self.assertIn(candidate(9009)["id"],delivered_ids(load(root)))
            bad=load(root)
            bad["marktplaats_delivered_ids"]=["marktplaats:m9009"]*2
            (Path(root)/FILE).write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                load(root)

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

    def test_market_median_drift_does_not_relist_unchanged_cars(self):
        with tempfile.TemporaryDirectory() as tmp:
            initial = candidate(102)
            _, saved, _ = plan(combined([initial]), "marktplaats", load(tmp))
            acknowledge(tmp, "marktplaats", saved)
            # Repeated scans may recompute entirely different comparisons.
            # None of these observations changes a seller's actual offer.
            for median in (2500, 2520, 2710, 2200, 3500, None):
                valued = {**initial,
                          "median_comparable_asking_price_eur": median,
                          "indicative_resale_eur": 2200 if median else None,
                          "comparable_count": 5}
                quiet, _, stats = plan(
                    combined([valued]), "marktplaats", load(tmp))
                self.assertEqual(quiet["count"], 0)
                self.assertEqual(stats["repeated"], 1)
            # Real seller price drops and technical warning changes still
            # trigger a new delivered update.
            price_drop = {**initial, "asking_price_eur": 990,
                          "median_comparable_asking_price_eur": 2710}
            changed, _, stats = plan(
                combined([price_drop]), "marktplaats", load(tmp))
            self.assertEqual(changed["count"], 1)
            self.assertEqual(stats["changed"], 1)

    def test_legacy_market_hashes_rebase_only_after_ack_and_when_seen(self):
        from hunter_delivery_delta import FINGERPRINT_REVISION, PENDING_REBASE
        with tempfile.TemporaryDirectory() as tmp:
            earlier = load(tmp)
            earlier.pop("marktplaats_fingerprint_revision")
            # Existing encrypted state may include ads outside this scan's
            # top 100. They must not spam as updated when they resurface.
            earlier["marktplaats"] = {
                candidate(401)["id"]: "a"*64,
                candidate(402)["id"]: "b"*64,
                candidate(403)["id"]: "c"*64,
            }
            (Path(tmp) / FILE).write_text(json.dumps(earlier))
            prior = load(tmp)
            self.assertNotIn("marktplaats_fingerprint_revision", prior)
            # Computing a delta must not mutate or permanently advance state.
            packets, new_sigs, stats = plan(
                combined([candidate(401), candidate(404)]),
                "marktplaats", prior)
            self.assertEqual(stats["new_to_delivery"], 1)
            self.assertEqual(stats["changed"], 0)
            self.assertEqual({x["id"] for x in packets["candidates"]},
                             {candidate(404)["id"]})
            self.assertEqual(load(tmp), earlier)
            # Only AET Pro's accepted delivery authorizes migration.
            acknowledge(tmp, "marktplaats", new_sigs)
            updated = load(tmp)
            self.assertEqual(updated["marktplaats_fingerprint_revision"],
                             FINGERPRINT_REVISION)
            self.assertEqual(updated["marktplaats"][candidate(402)["id"]],
                             PENDING_REBASE)
            self.assertEqual(updated["marktplaats"][candidate(403)["id"]],
                             PENDING_REBASE)
            self.assertEqual(updated["marktplaats"][candidate(401)["id"]],
                             fingerprint(candidate(401)))
            # Newly seen after migration, it is silently rebased once.
            delayed, sign, stats = plan(
                combined([candidate(402)]), "marktplaats", updated)
            self.assertEqual(delayed["count"], 0)
            self.assertEqual(stats["repeated"], 1)
            acknowledge(tmp, "marktplaats", sign)
            lower, _, stats = plan(
                combined([candidate(402, price=900)]),
                "marktplaats", load(tmp))
            self.assertEqual(lower["count"], 1)
            self.assertEqual(stats["changed"], 1)

    def test_invalid_fingerprint_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load(tmp)
            state["marktplaats_fingerprint_revision"] = 99
            (Path(tmp) / FILE).write_text(json.dumps(state))
            with self.assertRaises(ValueError):
                load(tmp)

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
                               "marktplaats",{"marktplaats":known,"facebook":{},
                                                "marktplaats_fingerprint_revision":2})
        self.assertEqual(counts["new_to_delivery"],15)
        self.assertEqual(counts["repeated"],0)
        self.assertEqual(picked["count"],15)

    def test_unseen_cars_not_hidden_by_legacy_market_hashes(self):
        from hunter_pro_export import build_payload
        rows = [self.raw(i) for i in range(80)]
        feed = {"version": 1, "cars": rows}
        # 60 acknowledged ads still have old market-sensitive fingerprints.
        # Those must not fill the 30 outbound slots ahead of 20 new cars.
        old = {"marktplaats:m"+str(10000+i): "a"*64 for i in range(60)}
        export = build_payload(feed, known_fingerprints=old, limit=30)
        unknown = {"marktplaats:m"+str(10000+i) for i in range(60, 80)}
        self.assertTrue(unknown.issubset(
            {row["id"] for row in export["candidates"]}))
        self.assertEqual(export["count"],20)
        # A pending legacy rebase must also never mask brand-new candidates.
        from hunter_delivery_delta import PENDING_REBASE
        pending = {key: PENDING_REBASE for key in old}
        after = build_payload(feed, known_fingerprints=pending, limit=30)
        self.assertTrue(unknown.issubset(
            {row["id"] for row in after["candidates"]}))


    def test_new_only_export_skips_all_previous_ids_before_cap(self):
        from hunter_pro_export import build_payload
        rows=[self.raw(i) for i in range(85)]
        known={"marktplaats:m"+str(10000+i):"a"*64 for i in range(40)}
        fresh=build_payload({"version":1,"cars":rows},
                            known_fingerprints=known,limit=50)
        self.assertEqual(fresh["count"],45)
        self.assertFalse(set(known) & {x["id"] for x in fresh["candidates"]})
        all_previously_sent={"marktplaats:m"+str(10000+i):"b"*64 for i in range(85)}
        empty=build_payload({"version":1,"cars":rows},
                            known_fingerprints=all_previously_sent,limit=50)
        self.assertEqual(empty["count"],0)

    def test_updated_price_is_kept_for_research_but_never_sent_as_new(self):
        from hunter_pro_export import build_payload
        rows=[self.raw(i) for i in range(30)]
        feed={"version":1,"cars":rows}
        known={item["id"]:fingerprint(item)
               for item in build_payload(feed)["candidates"]}
        baseline=build_payload(feed,known_fingerprints=known)
        self.assertEqual(baseline["count"],0)
        rows[29]={**rows[29],"price":1200,
                  "checked_at":"2026-10-25T02:00:00+00:00"}
        self.assertEqual(build_payload(feed,known_fingerprints=known)["count"],0)
        original_research=build_payload(feed)
        self.assertEqual(original_research["count"],30)
        self.assertEqual(original_research["candidates"][29]["asking_price_eur"],1200)
        # Even an outdated archive export cannot re-deliver acknowledged IDs
        # when runtime uses strict new-only delivery planning.
        picked,_,counts=plan({"schema":"aet-pro.hunter.v2",
                              "candidates":original_research["candidates"]},
                             "marktplaats",{"marktplaats":known,"facebook":{},
                                                "marktplaats_fingerprint_revision":2},
                             new_only=True)
        self.assertEqual(picked["count"],0)
        self.assertEqual(counts["changed"],0)
        self.assertEqual(counts["repeated"],30)


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


class RotatingThreeHundredPoolTests(unittest.TestCase):
    """No provider requests: previously saved cars ranked 101-300 get a turn."""

    def test_acknowledged_hundred_does_not_starve_next_two_hundred(self):
        from hunter_pro_export import build_payload
        rows = [BroaderOfflineCandidateSelectionTests.raw(i) for i in range(250)]
        feed = {"version": 1, "cars": rows}
        delivered_ids = set()
        with tempfile.TemporaryDirectory() as root:
            for expected in (100, 100, 50, 0):
                checkpoint = load(root)
                # 100 remains the maximum outbound capacity of A.E.T Pro;
                # the separate offline input pool may contain 300.
                export = build_payload(
                    feed, known_fingerprints=checkpoint["marktplaats"],
                    limit=100)
                self.assertLessEqual(export["count"], 100)
                scoped, signatures, counters = plan(
                    combined(export["candidates"]), "marktplaats", checkpoint)
                self.assertEqual(scoped["count"], expected)
                self.assertEqual(counters["new_to_delivery"], expected)
                ids = {row["id"] for row in scoped["candidates"]}
                self.assertFalse(delivered_ids & ids,
                                 "Old cars must not be presented as new")
                delivered_ids.update(ids)
                # Acknowledge only after the Worker would have confirmed.
                acknowledge(root, "marktplaats", signatures)
        self.assertEqual(len(delivered_ids), 250)

    def test_default_thirty_and_export_hard_maximum_remain_unchanged(self):
        from hunter_pro_export import build_payload
        feed = {"version": 1, "cars": [
            BroaderOfflineCandidateSelectionTests.raw(i) for i in range(360)]}
        self.assertEqual(build_payload(feed)["count"], 30)
        self.assertEqual(build_payload(feed, limit=100)["count"], 100)
        with self.assertRaises(ValueError):
            build_payload(feed, limit=101)
        # Only the already collected first 300 can enter selection; no
        # extra requests or unbounded memory/deep backlog scans.
        first300 = {"marktplaats:m" + str(10000+i) for i in range(300)}
        known = {key: "a"*64 for key in first300}
        later = build_payload(feed, known_fingerprints=known, limit=100)
        self.assertTrue({row["id"] for row in later["candidates"]}
                        .issubset(first300))


if __name__ == "__main__":
    unittest.main()
