"""Offline regression checks: no API requests or costs."""
import unittest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from car_triage import assess, extra_checks, market_comparison, quality_gate, vehicle_identity, deal_watchlist
from seller_filter import seller_type
from scanner import search_structured_prices, search_card_signals, search_card_priority, parse, extract_fields, choose_detail_links, select_diverse_detail_links, purchase_review_tier, extract_vehicle_year, interleave_search_results, parse_price_eur, is_financing_offer, buying_opportunity, listing_requires_manual_description_check

class HunterTests(unittest.TestCase):
    def test_conflicting_odometer_fields_cannot_bypass_purchase_limit(self):
        from scanner import over_purchase_mileage_limit
        self.assertTrue(over_purchase_mileage_limit({
            'mileage': 220000, 'mileage_km': 345000}))
        self.assertTrue(over_purchase_mileage_limit({
            'mileage': 220000, 'search_card_mileage_hint_km': 345000}))
        self.assertFalse(over_purchase_mileage_limit({
            'mileage': 220000, 'mileage_km': 330000}))

    def test_quality_gate_uses_assessed_profit_target_in_bid(self):
        from unittest.mock import patch
        base = {"score": 80, "reasons": [], "target_profit_eur": 700}
        details = {"year_detected": 2011, "mileage_km": 120000,
                   "apk_status": "gunstig", "distance_from_dronten": "binnen_100km"}
        item = {"title": "Peugeot 107 2011", "description": "Nette auto. " * 30,
                "description_source": "pagina_tekst", "price": 1000}
        comparison = {"status": "indicatieve_vraagprijzen", "median_asking_price_eur": 3000}
        with patch("car_triage.estimated_trade_costs", return_value=(250, [])):
            result = quality_gate(item, base, details, comparison)
        self.assertEqual(result["indicative_resale_eur"], 2550)
        self.assertEqual(result["max_purchase_price_eur"], 1600)
        self.assertEqual(result["target_net_profit_eur"], 700)
        self.assertTrue(result["asking_price_within_buy_limit"])

    def test_price_survives_unrelated_dealer_financing_text(self):
        text = 'Seat Ibiza 2011 117.865 km € 4.950,- Vandaag. ' + 'Goed onderhouden. ' * 8 + 'Ook lease mogelijk.'
        self.assertEqual(search_card_signals(text)['price'], 4950)

    def test_monthly_payment_is_not_purchase_price(self):
        self.assertIsNone(search_card_signals('Seat Ibiza lease € 199 per maand')['price'])

    def test_conflicting_card_prices_stay_unknown(self):
        self.assertIsNone(search_card_signals('Seat Ibiza € 995 of € 1295')['price'])

    def test_price_in_sibling_microdata(self):
        page = parse('<article><a href="/v/auto-s/opel/m123">Opel Corsa</a>'
                     '<meta itemprop="price" content="1250"></article>')
        url = 'https://www.marktplaats.nl/v/auto-s/opel/m123'
        self.assertEqual(search_card_signals(page.card_texts[url])['price'], 1250)

    def test_price_inside_card_with_void_meta_and_img(self):
        page = parse('<article><a href="/v/auto-s/opel/m123">Opel</a>'
                     '<img src="a.jpg"><meta itemprop="price" content="995">'
                     '<span>2006</span></article>')
        url = 'https://www.marktplaats.nl/v/auto-s/opel/m123'
        self.assertEqual(search_card_signals(page.card_texts[url])['price'], 995)

    def test_price_outside_anchor_inside_article(self):
        page = parse('<article><a href="/v/auto-s/opel/m123">Opel Corsa</a>'
                     '<div><span>€ 1.250</span><img src="car.jpg"></div></article>')
        url = 'https://www.marktplaats.nl/v/auto-s/opel/m123'
        self.assertEqual(search_card_signals(page.card_texts[url])['price'], 1250)

    def test_card_does_not_mix_multiple_listing_prices(self):
        page = parse('<li><a href="/v/auto-s/opel/m123">Opel</a>€ 900'
                     '<a href="/v/auto-s/ford/m456">Ford</a>€ 1900</li>')
        self.assertEqual(page.card_texts, {})

    def test_search_jsonld_listing_price(self):
        html = '''<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"Product","url":"https://www.marktplaats.nl/v/auto-s/opel/m12345","offers":{"price":"1.250","priceCurrency":"EUR"}}]}</script>'''
        prices = search_structured_prices(parse(html))
        self.assertEqual(prices['https://www.marktplaats.nl/v/auto-s/opel/m12345'], 1250)

    def test_search_jsonld_offer_list_price(self):
        html = '''<script type="application/ld+json">{"url":"https://www.marktplaats.nl/v/auto-s/opel/m12345","offers":[{"price":"995","priceCurrency":"EUR"}]}</script>'''
        self.assertEqual(search_structured_prices(parse(html))['https://www.marktplaats.nl/v/auto-s/opel/m12345'], 995)

    def test_search_jsonld_conflicting_offer_list_is_rejected(self):
        html = '''<script type="application/ld+json">{"url":"https://www.marktplaats.nl/v/auto-s/opel/m12345","offers":[{"price":995},{"price":1995}]}</script>'''
        self.assertEqual(search_structured_prices(parse(html)), {})

    def test_search_jsonld_conflicting_prices_not_used(self):
        html = '''<script type="application/ld+json">[{"url":"https://www.marktplaats.nl/v/auto-s/opel/m12345","offers":{"price":1200}},{"url":"https://www.marktplaats.nl/v/auto-s/opel/m12345","offers":{"price":1900}}]</script>'''
        self.assertEqual(search_structured_prices(parse(html)), {})

    def test_cached_revisit_after_48_hours(self):
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        url = 'https://www.marktplaats.nl/v/auto-s/opel/m123'
        previous = (now - timedelta(hours=49)).isoformat()
        self.assertEqual(choose_detail_links([url], {url: previous}, {}, now.timestamp(),
                                             cached_urls={url}), [url])

    def test_cached_recent_24_hours_is_skipped(self):
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        url = 'https://www.marktplaats.nl/v/auto-s/opel/m123'
        previous = (now - timedelta(hours=24)).isoformat()
        self.assertEqual(choose_detail_links([url], {url: previous}, {}, now.timestamp(),
                                             cached_urls={url}), [])

    def test_uncached_revisit_after_six_hours(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        previous = (now - timedelta(hours=7)).isoformat()
        self.assertEqual(choose_detail_links(['a'], {'a': previous}, {}, now.timestamp()), ['a'])

    def test_uncached_recent_four_hours_is_skipped(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        previous = (now - timedelta(hours=4)).isoformat()
        self.assertEqual(choose_detail_links(['a'], {'a': previous}, {}, now.timestamp()), [])


    def test_mid_budget_card_priority(self):
        self.assertLess(search_card_priority('€ 1100 Toyota Aygo'),
                        search_card_priority('€ 350 Toyota Aygo'))

    def test_huurkoop_is_not_purchase_price(self):
        self.assertIsNone(search_card_signals('Fiat Panda huurkoop EUR 200')['price'])

    def test_card_attribute_tracking(self):
        page = parse('<a href="/v/auto-s/fiat/m123" data-price="€ 1100">Fiat Panda</a>')
        self.assertEqual(page.link_attributes['/v/auto-s/fiat/m123']['data-price'], '€ 1100')


    def test_uncached_ad_can_be_revisited_after_twelve_hours(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=13)).isoformat()
        self.assertEqual(choose_detail_links(['old'], {'old': old}, {}, now.timestamp()), ['old'])

    def test_cached_comparable_refreshes_after_48_hours(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=2, minutes=1)).isoformat()
        self.assertEqual(choose_detail_links(['old'], {'old': old}, {}, now.timestamp(),
                                             cached_urls={'old'}), ['old'])


    def test_card_attribute_price_recognition(self):
        page = parse('<a href="/v/auto-s/opel/m123" aria-label="Opel Corsa € 950">'
                     '<span>Opel Corsa</span></a>')
        self.assertEqual(search_card_signals(page.link_texts['/v/auto-s/opel/m123'])['price'], 950)

    def test_nested_image_alt_price_recognition(self):
        page = parse('<a href="/v/auto-s/kia/m123"><img alt="Kia Picanto EUR 1200"/>'
                     '<span>Picanto</span></a>')
        self.assertEqual(search_card_signals(page.link_texts['/v/auto-s/kia/m123'])['price'], 1200)

    def test_termijnbedrag_not_treated_as_car_price(self):
        self.assertIsNone(search_card_signals('€ 150 termijnbedrag')['price'])

    def test_diverse_detail_selection_uses_best_card_not_first(self):
        now = datetime.now(timezone.utc).timestamp()
        groups = [['first', 'better'], ['another']]
        ranked = {'first': (3, 9000), 'better': (0, 850), 'another': (1, 2400)}
        chosen = select_diverse_detail_links(groups, {}, {}, now, limit=2,
                                              priorities=ranked)
        self.assertEqual(chosen, ['better', 'another'])

    def test_diverse_detail_selection_does_not_lose_first_entry(self):
        now = datetime.now(timezone.utc).timestamp()
        groups = [['first', 'better']]
        ranked = {'first': (1, 2500), 'better': (0, 850)}
        chosen = select_diverse_detail_links(groups, {}, {}, now, limit=2,
                                              priorities=ranked)
        self.assertEqual(set(chosen), {'first', 'better'})


    def test_search_card_price_recognition(self):
        self.assertEqual(search_card_signals('Opel Corsa € 1.250')['price'], 1250)
        self.assertEqual(search_card_signals('Opel Corsa EUR 1250')['price'], 1250)

    def test_search_card_apk_expiry_is_not_the_model_year(self):
        row = search_card_signals('Peugeot 107 bouwjaar 2007 159.690 km € 1.700 APK tot november 2027')
        self.assertEqual(row['year'], 2007)
        self.assertEqual(row['mileage_km'], 159690)
        self.assertEqual(row['price'], 1700)

    def test_search_card_apk_expiry_without_build_year_stays_unknown(self):
        self.assertIsNone(search_card_signals('Peugeot 107 € 1.500 APK tot 02-2027')['year'])

    def test_search_card_year_with_distinct_unrelated_years_stays_unknown(self):
        self.assertIsNone(search_card_signals('Opel Corsa 2010 en 2012 € 1.000')['year'])

    def test_repeated_same_price_is_unambiguous(self):
        self.assertEqual(search_card_signals('€ 1250 Opel Corsa € 1250')['price'], 1250)

    def test_conflicting_prices_are_not_used_for_prefilter(self):
        self.assertIsNone(search_card_signals('€ 1250 of € 2500')['price'])

    def test_lease_card_has_no_trusted_buying_price(self):
        self.assertIsNone(search_card_signals('€ 250 per maand lease')['price'])

    def test_low_price_gets_priority_over_unknown(self):
        self.assertLess(search_card_priority('EUR 700 Fiat Punto'),
                        search_card_priority('Fiat Punto vraagprijs onbekend'))


    def test_deal_watchlist_prioritizes_price_gap_without_profit_claim(self):
        row = {'title': 'Toyota Aygo 2012', 'price': 1100, 'year': 2012, 'mileage': 140000}
        rating = {'severe_technical_risk': False}
        comparison = {'sample_size': 4, 'median_asking_price_eur': 2000}
        result = deal_watchlist(row, rating, comparison)
        self.assertEqual(result['watchlist_tier'], 'sterke_prijsafwijking_controleren')
        self.assertEqual(result['asking_price_gap_eur'], 900)
        self.assertNotIn('profit_estimate_eur', result)
        self.assertEqual(deal_watchlist(row, {'severe_technical_risk': True}, comparison)['watchlist_tier'], 'uitsluiten')
        self.assertEqual(deal_watchlist(row, rating, {'sample_size': 1})['watchlist_tier'], 'handmatig_prijsonderzoek')

    def test_cheap_older_car_is_visible_without_market_comparables(self):
        item = {'title': 'Fiat Punto 2005', 'price': 700, 'year': 2005, 'mileage': 260000}
        comparison = {'status': 'onvoldoende_vergelijkingen', 'sample_size': 1}
        result = deal_watchlist(item, {'severe_technical_risk': False}, comparison)
        self.assertEqual(result['watchlist_tier'], 'goedkope_oudere_auto_controleren')
        self.assertNotIn('profit_estimate_eur', result)

    def test_buyer_queue_includes_cheap_high_mileage_private_cars(self):
        item = {'title': 'Fiat Punto 2005', 'price': 700, 'year': 2005,
                'mileage': 260000, 'seller_type': 'particulier',
                'watchlist_tier': 'handmatig_prijsonderzoek',
                'severe_technical_risk': False}
        self.assertEqual(buying_opportunity(item)['buyer_queue'], 'goedkoop_onderhandelen')
        item['severe_technical_risk'] = True
        self.assertEqual(buying_opportunity(item)['buyer_queue'], 'reparatieproject')

    def test_cheap_unknown_seller_stays_on_negotiation_list(self):
        item = {'title': 'Fiat Punto 2005', 'price': 700,
                'seller_type': 'onbekend', 'watchlist_tier': 'handmatig_prijsonderzoek',
                'severe_technical_risk': False}
        self.assertEqual(buying_opportunity(item)['buyer_queue'], 'goedkoop_onderhandelen')

    def test_meta_summary_is_not_full_description(self):
        item = {'title': 'Toyota Aygo 2012 nieuwe apk', 'price': 900,
                'year': 2012, 'mileage': 140000, 'city': 'Dronten',
                'description': 'Nette auto, rijdt prima', 'description_source': 'meta_samenvatting'}
        details = extra_checks(item)
        comparison = {'status': 'indicatieve_vraagprijzen', 'sample_size': 5,
                      'median_asking_price_eur': 3500}
        result = quality_gate(item, assess(item), details, comparison)
        self.assertFalse(result['actionable_lead'])
        self.assertIn('volledige advertentiebeschrijving', result['missing_information'])

    def test_fixed_profit_target_and_maximum_bid(self):
        item = {'title': 'Toyota Aygo 2012 nieuwe apk', 'price': 1600,
                'year': 2012, 'mileage': 140000, 'city': 'Dronten',
                'description': 'Goed onderhouden, nieuwe apk',
                'description_source': 'jsonld'}
        comparison = {'status': 'indicatieve_vraagprijzen', 'sample_size': 4,
                      'median_asking_price_eur': 2000}
        rating = quality_gate(item, assess(item), extra_checks(item), comparison)
        self.assertEqual(rating['target_net_profit_eur'], 500)
        self.assertEqual(rating['max_purchase_price_eur'], 950)
        self.assertEqual(rating['required_negotiation_eur'], 650)
        self.assertEqual(rating['estimated_costs_allowance_eur'], 250)
        self.assertFalse(rating['asking_price_within_buy_limit'])
        self.assertEqual(buying_opportunity({**item, **rating})['buyer_queue'], 'grote_prijskloof')

    def test_incomplete_description_does_not_hide_manual_buying_queue(self):
        item = {'title': 'Toyota Aygo 2012', 'price': 900,
                'description_source': 'jsonld', 'description': 'Rijdt goed',
                'seller_type': 'particulier', 'severe_technical_risk': False}
        self.assertTrue(listing_requires_manual_description_check(item))
        self.assertEqual(buying_opportunity(item)['buyer_queue'], 'goedkoop_onderhandelen')
        item['severe_technical_risk'] = True
        self.assertEqual(buying_opportunity(item)['buyer_queue'], 'reparatieproject')

    def test_new_delivery_cars_replace_old_detail_rechecks_without_extra_requests(self):
        from scanner import prioritize_undelivered_purchase_details
        from datetime import datetime, timezone
        now=datetime(2026,10,26,tzinfo=timezone.utc).timestamp()
        def link(i):
            return ("https://www.marktplaats.nl/v/auto-s/opel/m"
                    +str(90000+i)+"-test")
        previous=[link(i) for i in range(4)]
        newly_seen=[link(i) for i in range(4,9)]
        selected=previous+[newly_seen[0]]
        groups=[previous+newly_seen]
        facts={u:{"price_type":"FIXED","price":850,
                  "seller_type":"particulier"} for u in selected+newly_seen}
        result, added=prioritize_undelivered_purchase_details(
            selected,groups,{}, {}, now, facts,
            {"marktplaats:" + u.rsplit("/",1)[-1].split("-")[0]
             for u in previous},
            5,priorities={u:(0,850) for u in facts})
        self.assertEqual(len(result),5)
        self.assertEqual(len({x.rsplit("/",1)[-1].split("-")[0] for x in result}),5)
        self.assertEqual(result[:4],newly_seen[:4])
        self.assertGreaterEqual(added,3)
        self.assertEqual(prioritize_undelivered_purchase_details(
            selected,groups,{}, {}, now, facts,set(),5)[0],selected)


    def test_confirmed_delivery_ids_use_production_prefix_not_bare_listing_id(self):
        from scanner import prioritize_undelivered_purchase_details
        from datetime import datetime, timezone
        ts=datetime(2026,10,26,tzinfo=timezone.utc).timestamp()
        def listing(i,slug="test"):
            return f"https://www.marktplaats.nl/v/auto-s/opel/m{82000+i}-{slug}"
        previous=[listing(i) for i in range(4)]
        unseen=[listing(i) for i in range(4,10)]
        # This exact prefix is what the encrypted Hunter delivery checkpoint
        # actually stores; bare m-numbers masked the original production bug.
        remembered={"marktplaats:m"+str(82000+i) for i in range(4)}
        facts={x:{"price_type":"FIXED","price":900,
                  "seller_type":"particulier"} for x in previous+unseen}
        selected=previous+[unseen[0]]
        output,added=prioritize_undelivered_purchase_details(
            selected,[previous+unseen],{}, {}, ts, facts,remembered,5)
        self.assertEqual(output[:4],unseen[:4])
        self.assertGreaterEqual(added,3)
        self.assertEqual(len(set(output)),5)
        # A changed URL slug must still be recognized by its stable ID.
        renamed=[listing(i,"updated") for i in range(4)]
        renamed_facts={**facts,**{x:{"price_type":"FIXED","price":900,
                                    "seller_type":"particulier"} for x in renamed}}
        result,_=prioritize_undelivered_purchase_details(
            renamed+[unseen[0]],[renamed+unseen],{}, {}, ts,
            renamed_facts,remembered,5)
        self.assertEqual(result[:4],unseen[:4])
        self.assertEqual(len(set(result)),5)

    def test_detail_refill_respects_cooldown_of_new_ads(self):
        from scanner import prioritize_undelivered_purchase_details
        from datetime import datetime, timezone
        ts=datetime(2026,10,26,tzinfo=timezone.utc).timestamp()
        def listing(i):
            return f"https://www.marktplaats.nl/v/auto-s/opel/m{83000+i}-test"
        old=[listing(1),listing(2),listing(3)]
        blocked=listing(4)
        ready=listing(5)
        facts={url:{"price_type":"FIXED","price":900,
                    "seller_type":"particulier"} for url in old+[blocked,ready]}
        known={"marktplaats:m83001","marktplaats:m83002","marktplaats:m83003"}
        result,added=prioritize_undelivered_purchase_details(
            old,[old+[blocked,ready]],{}, {blocked:ts+5000},ts,
            facts,known,3)
        self.assertEqual(result[0],ready)
        self.assertNotIn(blocked,result)
        self.assertEqual(len(result),3)

    def test_detail_budget_is_fairly_spread_across_models(self):
        groups = [['a1', 'a2', 'a3', 'a4', 'a5'],
                  ['b1', 'b2', 'b3'], ['c1', 'c2', 'c3']]
        chosen = select_diverse_detail_links(groups, {}, {}, 0, limit=6)
        self.assertEqual(chosen, ['a1', 'b1', 'c1', 'a2', 'b2', 'c2'])
        self.assertEqual(len(chosen), len(set(chosen)))

    def test_short_jsonld_description_is_not_validated(self):
        item = {'title': 'Renault Modus 2006', 'price': 600,
                'description': 'Zie beschrijving', 'description_source': 'jsonld'}
        self.assertTrue(listing_requires_manual_description_check(item))
        rating = quality_gate(item, assess(item), extra_checks(item),
                              {'status': 'indicatieve_vraagprijzen', 'sample_size': 4,
                               'median_asking_price_eur': 2400})
        self.assertFalse(rating['actionable_lead'])
        self.assertIn('volledige advertentiebeschrijving', rating['missing_information'])

    def test_short_description_cannot_be_trusted_as_full_inspection(self):
        self.assertTrue(listing_requires_manual_description_check(
            {'description_source': 'jsonld', 'description': 'Motorprobleem, geschikt voor reparatie'}))
        self.assertTrue(listing_requires_manual_description_check(
            {'description_source': 'meta_samenvatting', 'description': 'a' * 300}))
        self.assertFalse(listing_requires_manual_description_check(
            {'description_source': 'jsonld', 'description': 'a' * 200}))

    def test_explicit_nonrunning_and_gearbox_defects_are_rejected(self):
        for defect in ('motor kapot', 'versnellingsbak stuk', 'start niet', 'water in olie'):
            with self.subTest(defect=defect):
                item = {'title': 'Opel Corsa 2011', 'price': 800, 'year': 2011,
                        'description': defect, 'description_source': 'jsonld'}
                details = extra_checks(item)
                rating = quality_gate(item, assess(item), details,
                                      {'status': 'onvoldoende_vergelijkingen'})
                self.assertTrue(rating['severe_technical_risk'])
                self.assertEqual(buying_opportunity({**item, **rating})['buyer_queue'], 'reparatieproject')

    def test_motorprobleem_in_description_is_severe_risk(self):
        item = {'title': 'Hyundai i10 1.0 2013', 'price': 1250,
                'year': 2013, 'description': 'Motorprobleem, mogelijk koppakking',
                'description_source': 'jsonld'}
        details = extra_checks(item)
        rating = quality_gate(item, assess(item), details, {'status': 'onvoldoende_vergelijkingen'})
        self.assertTrue(rating['severe_technical_risk'])
        self.assertFalse(rating['actionable_lead'])
        self.assertEqual(buying_opportunity({**item, **rating})['buyer_queue'], 'reparatieproject')

    def test_apk_and_mileage_reserves_increase_costs(self):
        from car_triage import estimated_trade_costs
        normal, _ = estimated_trade_costs({}, {'apk_status': 'positieve_claim_onbevestigd', 'mileage_km': 120000})
        unknown, _ = estimated_trade_costs({}, {'apk_status': 'onbekend', 'mileage_km': 120000})
        old_apk, _ = estimated_trade_costs({}, {'apk_status': 'ongunstig', 'mileage_km': 260000})
        self.assertEqual(normal, 250)
        self.assertEqual(unknown, 400)
        self.assertEqual(old_apk, 800)

    def test_no_market_data_means_no_invented_max_bid(self):
        item = {'title': 'Fiat Punto 2005', 'price': 700, 'year': 2005}
        rating = quality_gate(item, assess(item), extra_checks(item),
                              {'status': 'onvoldoende_vergelijkingen'})
        self.assertIsNone(rating['max_purchase_price_eur'])
        self.assertIsNone(rating['required_negotiation_eur'])

    def test_buyer_queue_rejects_monthly_payment(self):
        item = {'title': 'Toyota Corolla Hybrid', 'price': 369,
                'description': 'Financial lease voor €369 p/m of kopen voor €22.400'}
        self.assertEqual(buying_opportunity(item)['buyer_queue'], 'uitsluiten')

    def test_monthly_lease_price_is_not_a_car_deal(self):
        item = {'title': 'Toyota Corolla 2.0 Hybrid', 'price': 369,
                'description': 'Financial lease deze Toyota Corolla voor €369 p/m of koop direct voor €22.400'}
        self.assertTrue(is_financing_offer(item))
        self.assertFalse(is_financing_offer({'title': 'Toyota Aygo 2008', 'price': 900,
            'description': 'Inruil mogelijk en financiering bespreekbaar'}))

    def test_price_normalization(self):
        self.assertEqual(parse_price_eur('1.950'), 1950)
        self.assertEqual(parse_price_eur('1.950,00'), 1950)
        self.assertEqual(parse_price_eur('1950.00'), 1950)
        self.assertIsNone(parse_price_eur('op aanvraag'))

    def test_description_extraction_from_listing_jsonld(self):
        html = ('<meta property="og:title" content="Peugeot 107 2007">'
                '<script type="application/ld+json">'
                '{"@type":"Product","description":"Loopt niet goed vermoedelijk koppakking",'
                '"offers":{"price":"600","priceCurrency":"EUR"}}'
                '</script>')
        item = extract_fields(parse(html))
        self.assertEqual(item['description_source'], 'jsonld')
        self.assertIn('koppakking', item['description'])
        item.update({'year': 2007, 'mileage': 231852})
        details = extra_checks(item)
        comparison = {'status': 'indicatieve_vraagprijzen', 'sample_size': 4,
                      'median_asking_price_eur': 1774}
        rating = quality_gate(item, assess(item), details, comparison)
        self.assertTrue(rating['severe_technical_risk'])
        self.assertFalse(rating['actionable_lead'])

    def test_missing_description_prevents_actionable_deal(self):
        item = {'title': 'Toyota Aygo 2012 nieuwe apk', 'price': 900,
                'year': 2012, 'mileage': 140000, 'city': 'Dronten'}
        details = extra_checks(item)
        comparison = {'status': 'indicatieve_vraagprijzen', 'sample_size': 5,
                      'median_asking_price_eur': 3500}
        rating = quality_gate(item, assess(item), details, comparison)
        self.assertIn('advertentiebeschrijving', rating['missing_information'])
        self.assertFalse(rating['actionable_lead'])

    def test_hidden_head_gasket_fault_is_not_a_deal(self):
        item = {'title': 'Peugeot 107 2007', 'description': 'Loopt niet goed vermoedelijk koppakking',
                'price': 600, 'year': 2007, 'mileage': 231852}
        comparison = {'status': 'indicatieve_vraagprijzen', 'sample_size': 4,
                      'median_asking_price_eur': 1774}
        details = extra_checks(item)
        rating = quality_gate(item, assess(item), details, comparison)
        self.assertTrue(rating['severe_technical_risk'])
        self.assertFalse(rating['actionable_lead'])
        self.assertEqual(deal_watchlist(item, rating, comparison)['watchlist_tier'], 'uitsluiten')

    def test_high_mileage_does_not_reduce_deal_score(self):
        item = {'title': 'Toyota Aygo 2012', 'price': 900}
        base = assess(item)
        comparison = {'status': 'indicatieve_vraagprijzen', 'sample_size': 4,
                      'median_asking_price_eur': 2500}
        details = {'year_detected': 2012, 'mileage_km': 350000,
                   'apk_status': 'positieve_claim_onbevestigd',
                   'distance_from_dronten': 'waarschijnlijk_binnen_100km_controleren'}
        high = quality_gate(item, base, details, comparison)
        details['mileage_km'] = 150000
        low = quality_gate(item, base, details, comparison)
        self.assertEqual(high['score'], low['score'])
        self.assertEqual(high['actionable_lead'], low['actionable_lead'])

    def test_watchlist_needs_vehicle_identity_and_old_car_limit(self):
        comparison = {'sample_size': 4, 'median_asking_price_eur': 2200}
        safe = {'severe_technical_risk': False}
        missing = deal_watchlist({'price': 800, 'title': 'Aygo'}, safe, comparison)
        self.assertEqual(missing['watchlist_tier'], 'voertuiggegevens_controleren')
        old = deal_watchlist({'price': 1800, 'year': '2007', 'mileage': '180.000'}, safe, comparison)
        self.assertEqual(old['watchlist_tier'], 'prijsafwijking_controleren')
        normal = deal_watchlist({'price': 900, 'year': '2012', 'mileage': '140.000'}, safe, comparison)
        self.assertEqual(normal['watchlist_tier'], 'sterke_prijsafwijking_controleren')

    def test_interleave_searches_before_detail_budget(self):
        groups = [['a1', 'a2', 'a3'], ['b1', 'b2'], ['c1']]
        self.assertEqual(interleave_search_results(groups), ['a1', 'b1', 'c1', 'a2', 'b2', 'a3'])
        self.assertEqual(interleave_search_results([]), [])

    def test_extra_checks_does_not_use_apk_expiry_as_model_year(self):
        result = extra_checks({'title': 'Ford Ka APK tot 2027', 'price': 1100})
        self.assertIsNone(result['year_detected'])
        with_year = extra_checks({'title': 'Ford Ka 2012, APK tot 2027', 'price': 1100})
        self.assertEqual(with_year['year_detected'], 2012)

    def test_apk_year_is_not_vehicle_year(self):
        self.assertIsNone(extract_vehicle_year(None, 'Ford Ka APK tot 2027'))
        self.assertEqual(extract_vehicle_year('2012', 'Ford Ka APK tot 2027'), 2012)
        self.assertEqual(extract_vehicle_year(None, 'Ford Ka 2011, APK tot 2027'), 2011)

    def test_scoring_uses_structured_year_not_apk_expiry(self):
        scored = assess({'title': 'Ford Ka APK tot 2027', 'year': 2011, 'price': 1200})
        self.assertEqual(scored['year_from_title'], 2011)
        self.assertIn('Bouwjaar vanaf 2010 (advertentiegegevens of titel)', scored['reasons'])
        without_year = assess({'title': 'Ford Ka APK tot 2027', 'price': 1200})
        self.assertIsNone(without_year['year_from_title'])

    def test_outside_budget_dealer_tier(self):
        row = {'year': 2018, 'price': 4500, 'seller_type': 'handelaar'}
        self.assertEqual(purchase_review_tier(row), 'buiten_budget')

    def test_year_strings_in_cached_ads(self):
        self.assertEqual(purchase_review_tier({'year': '2014', 'price': 1500, 'seller_type': 'particulier'}), 'particulier_eerst_bekijken')
        self.assertEqual(purchase_review_tier({'year': 'unknown', 'price': 1500, 'seller_type': 'particulier'}), 'bouwjaar_controleren')

    def test_private_newer_car_gets_review_priority(self):
        row = {'year': 2012, 'price': 1700, 'seller_type': 'particulier', 'apk_status': 'onbekend'}
        self.assertEqual(purchase_review_tier(row), 'particulier_eerst_bekijken')

    def test_old_expensive_car_deprioritized(self):
        row = {'year': 2005, 'price': 1800, 'seller_type': 'particulier'}
        self.assertEqual(purchase_review_tier(row), 'particulier_eerst_bekijken')

    def test_faulty_car_not_review_priority(self):
        row = {'year': 2015, 'price': 1000, 'seller_type': 'particulier', 'severe_technical_risk': True}
        self.assertEqual(purchase_review_tier(row), 'technisch_risico')

    def test_detail_selection_skips_cooldown_and_recent_history_before_budget(self):
        now = datetime.now(timezone.utc)
        ts = now.timestamp()
        urls = ["blocked", "recent", "fresh", "old"]
        history = {"recent": now.isoformat(), "old": "2026-01-01T00:00:00+00:00"}
        chosen = choose_detail_links(urls, history, {"blocked": ts + 3600}, ts, limit=2)
        self.assertEqual(chosen, ["fresh", "old"])

    def test_oldest_revisit_is_first(self):
        now = datetime.now(timezone.utc)
        history = {'newer': '2026-10-01T00:00:00+00:00', 'older': '2026-01-01T00:00:00+00:00'}
        self.assertEqual(choose_detail_links(['newer', 'older'], history, {}, now.timestamp(), limit=1), ['older'])

    def test_detail_selection_does_not_revisit_recent_ad(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(choose_detail_links(["recent"], {"recent": now.isoformat()}, {}, now.timestamp()), [])

    def test_english_transmission_fault_is_not_actionable(self):
        item = {'title': 'Skoda Roomster automatic transmission problem 2012', 'price': 1900, 'year': 2012, 'mileage': 150000, 'city': 'Dronten'}
        details = extra_checks(item)
        comparison = {'status': 'indicatieve_vraagprijzen', 'median_asking_price_eur': 5000}
        result = quality_gate(item, assess(item), details, comparison)
        self.assertTrue(result['severe_technical_risk'])
        self.assertFalse(result['actionable_lead'])
        self.assertEqual(result['priority'], 'technisch_risico')

    def test_missing_details_not_a_top_deal(self):
        item = {"title": "Peugeot 206 Gentry", "price": 1100, "city": "Veenendaal"}
        details = extra_checks(item)
        result = quality_gate(item, assess(item), details, {"status": "bouwjaar_onbekend"})
        self.assertFalse(result["actionable_lead"])
        self.assertNotEqual(result["priority"], "bekijken")
        self.assertIn("kilometerstand", result["missing_information"])

    def test_bad_apk_reduces_priority(self):
        item = {"title": "Opel Corsa 2011 geen APK", "price": 900, "city": "Dronten"}
        details = extra_checks(item)
        result = quality_gate(item, assess(item), details, {"status": "onvoldoende_vergelijkingen"})
        self.assertFalse(result["actionable_lead"])
        self.assertEqual(details["apk_status"], "ongunstig")

    def test_no_comparables_no_fake_profit(self):
        item = {"title": "Toyota Aygo 2012 150000 km nieuwe apk", "price": 950, "city": "Dronten"}
        details = extra_checks(item)
        result = quality_gate(item, assess(item), details, {"status": "onvoldoende_vergelijkingen"})
        self.assertIsNone(result["profit_estimate_eur"])
        self.assertIsNone(result["indicative_margin_eur"])
        self.assertFalse(result["actionable_lead"])

    def test_structured_seller_extraction(self):
        html = '<script type="application/ld+json">{"@type":"Product","name":"Skoda Roomster 2014","offers":{"price":"2250","seller":{"@type":"Organization","name":"Voorbeeld Autobedrijf"}}}</script>'
        data = extract_fields(parse(html))
        self.assertEqual(data["seller_type"], "business")
        self.assertEqual(seller_type(data), "handelaar")

    def test_roomster_recognition(self):
        self.assertEqual(vehicle_identity({"title": "Skoda Roomster 1.6 TDI"})["model"], "roomster")

    def test_dealer_name_detection(self):
        self.assertEqual(seller_type({"seller_name": "Jansen Autobedrijf BV"}), "handelaar")
        self.assertEqual(seller_type({"seller_name": "Piet"}), "onbekend")
        self.assertEqual(seller_type({"seller_type": "private"}), "particulier")

    def test_vehicle_identity_extracts_model_and_engine(self):
        identity = vehicle_identity({'title': 'Peugeot 107 1.0 12V 2010'})
        self.assertEqual(identity['model'], '107')
        self.assertEqual(identity['engine_l'], '1.0')

    def test_comparison_uses_extracted_numeric_fields(self):
        now = datetime.now(timezone.utc).isoformat()
        target = {'title': 'Toyota Aygo 1.0 2012', 'year_detected': 2012, 'mileage_km': 160000, 'url': 'target'}
        rows = [{'title': 'Toyota Aygo 1.0 2012', 'year_detected': 2012,
                 'mileage_km': '165.000', 'price': 2800, 'url': f'ad{i}',
                 'checked_at': now} for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'references.json'
            path.write_text(json.dumps(rows), encoding='utf-8')
            comparison = market_comparison(target, str(path))
        self.assertEqual(comparison['sample_size'], 3)
        self.assertEqual(comparison['status'], 'indicatieve_vraagprijzen')

    def test_lease_payment_never_corrupts_comparable_median(self):
        now = datetime.now(timezone.utc).isoformat()
        rows = [{'title': 'Toyota Aygo 1.0 2012', 'year': 2012,
                 'mileage': 160000, 'price': 2000 + i * 100,
                 'url': 'normal' + str(i), 'checked_at': now,
                 'description_source': 'jsonld'} for i in range(3)]
        rows.append({'title': 'Toyota Aygo 1.0 2012', 'year': 2012,
                     'mileage': 160000, 'price': 199,
                     'description': 'Financial lease voor €199 p/m',
                     'url': 'lease', 'checked_at': now,
                     'description_source': 'jsonld'})
        target = {'title': 'Toyota Aygo 1.0 2012', 'year': 2012,
                  'mileage': 160000, 'url': 'target'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'prices.json'
            path.write_text(json.dumps(rows), encoding='utf-8')
            result = market_comparison(target, str(path))
        self.assertEqual(result['sample_size'], 3)
        self.assertEqual(result['median_asking_price_eur'], 2100)

    def test_comparison_rejects_other_engine_and_high_mileage(self):
        now = datetime.now(timezone.utc).isoformat()
        target = {"title": "Ford Focus 1.0 EcoBoost 2013", "year_detected": 2013,
                  "mileage_km": 160000, "url": "target"}
        rows = [
            {"title": "Ford Focus 1.0 EcoBoost 2013", "year": 2013, "mileage": 165000,
             "price": 3000, "url": "good1", "checked_at": now},
            {"title": "Ford Focus 1.0 EcoBoost 2014", "year": 2014, "mileage": 170000,
             "price": 3100, "url": "good2", "checked_at": now},
            {"title": "Ford Focus 1.6 2013", "year": 2013, "mileage": 165000,
             "price": 6000, "url": "bad-engine", "checked_at": now},
            {"title": "Ford Focus 1.0 EcoBoost 2013", "year": 2013, "mileage": 300000,
             "price": 1000, "url": "bad-km", "checked_at": now},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            result = market_comparison(target, str(path))
        self.assertEqual(result["sample_size"], 2)
        self.assertEqual(result["status"], "onvoldoende_vergelijkingen")

class ArchiveRetentionTests(unittest.TestCase):
    def test_refreshed_old_listing_survives_full_archive(self):
        from scanner import retain_recent_archive_records
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        rows = [
            {'url': f'https://www.marktplaats.nl/v/auto-s/opel/m{1000+i}-auto',
             'checked_at': (now - timedelta(hours=120-i)).isoformat()}
            for i in range(4)
        ]
        # Updating the oldest dict key never moves its insertion position.
        rows[0]['checked_at'] = (now + timedelta(minutes=1)).isoformat()
        kept = retain_recent_archive_records(rows, limit=3)
        self.assertEqual({r['url'] for r in kept},
                         {rows[i]['url'] for i in (0, 2, 3)})

    def test_invalid_archive_timestamps_are_evicted_first(self):
        from scanner import retain_recent_archive_records
        rows = [{'url':'https://www.marktplaats.nl/v/auto-s/opel/m100', 'checked_at':'not-a-date'},
                {'url':'https://www.marktplaats.nl/v/auto-s/opel/m101', 'checked_at':'2026-10-21T10:00:00+00:00'}]
        self.assertEqual(retain_recent_archive_records(rows, limit=1)[0]['url'], rows[1]['url'])


if __name__ == "__main__":
    unittest.main()


class PriceRegressionTests(unittest.TestCase):
    def test_search_card_euro_amount(self):
        from scanner import search_card_signals
        self.assertEqual(search_card_signals("Opel Corsa € 1.250 2010 180.000 km")["price"], 1250)

    def test_search_card_monthly_payment_not_purchase_price(self):
        from scanner import search_card_signals
        self.assertIsNone(search_card_signals("Lease € 199 per maand")["price"])

    def test_apify_price_normalization(self):
        from scanner import parse_price_eur
        self.assertEqual(parse_price_eur("€ 2.450"), 2450)
        self.assertEqual(parse_price_eur("1.750,00"), 1750)
