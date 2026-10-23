"""Marktplaats isolation and full scan regressions; no network calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import scanner

URL = 'https://www.marktplaats.nl/v/auto-s/opel/m123-corsa'
OTHER = 'https://www.marktplaats.nl/v/auto-s/ford/m456-fiesta'

def page(objects):
    return '<script type="application/ld+json">' + json.dumps(objects) + '</script>'

def vehicle(url=URL, price=1250, **fields):
    return {'@type': 'Car', 'url': url, 'name': 'Opel Corsa 2011',
            'offers': {'price': price, 'priceCurrency': 'EUR'}, **fields}

class MarktplaatsTests(unittest.TestCase):
    def test_recommendation_cannot_overwrite_vehicle(self):
        data = scanner.extract_fields(scanner.parse(page([
            vehicle(description='Motor defect, loopt niet.', vehicleModelDate=2011),
            vehicle(OTHER, 8995, description='Perfect onderhouden auto', vehicleModelDate=2020)
        ])), URL)
        self.assertEqual(data['price'], 1250)
        self.assertEqual(data['year'], 2011)
        self.assertIn('defect', data['description'])

    def test_preserve_detailed_listing_title(self):
        html = '<meta property="og:title" content="Opel Corsa 1.2 Automaat 2011">' + page(vehicle())
        self.assertEqual(scanner.extract_fields(scanner.parse(html), URL)['title'], 'Opel Corsa 1.2 Automaat 2011')

    def test_ambiguous_vehicles_do_not_mix(self):
        data = scanner.extract_fields(scanner.parse(page([vehicle(), vehicle(OTHER)])))
        self.assertIsNone(data['price'])

    def test_monthly_price_with_dutch_suffix_and_decimals(self):
        for text in ['€ 199,- per maand', '€ 199,00 /mnd', '€ 199 p/m']:
            self.assertIsNone(scanner.search_card_signals(text)['price'])
            self.assertTrue(scanner.is_financing_offer({'price': 199, 'description': text}))
        self.assertFalse(scanner.is_financing_offer({'price': 199, 'description': '1199 per maand'}))

    def test_offer_list_and_conflict(self):
        obj = vehicle()
        obj['offers'] = [obj['offers']]
        self.assertEqual(scanner.extract_fields(scanner.parse(page(obj)), URL)['price'], 1250)
        obj['offers'].append({'price': 2500, 'priceCurrency': 'EUR'})
        self.assertIsNone(scanner.extract_fields(scanner.parse(page(obj)), URL)['price'])

    def test_foreign_currency_and_nonfinite_values(self):
        obj = vehicle()
        obj['offers']['priceCurrency'] = 'USD'
        self.assertIsNone(scanner.extract_fields(scanner.parse(page(obj)), URL)['price'])
        for price in [float('nan'), float('inf'), -1, True]:
            self.assertIsNone(scanner.parse_price_eur(price))

    def test_miles_are_not_kilometers(self):
        obj = vehicle(mileageFromOdometer={'value': 100000, 'unitCode': 'SMI'})
        self.assertIsNone(scanner.extract_fields(scanner.parse(page(obj)), URL)['mileage'])

    def test_full_scan_assesses_one_vehicle_without_inventing_profit(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                def fetch(url):
                    if '/q/' in url:
                        return '<article><a href="' + URL + '">Opel Corsa € 1250</a></article>', url, 200
                    obj = vehicle(vehicleModelDate=2011,
                                  mileageFromOdometer={'value': 160000, 'unitCode': 'KMT'},
                                  description='Goed onderhouden auto. ' * 12)
                    return page(obj), url, 200
                with patch.object(scanner, 'fetch', side_effect=fetch), patch.object(scanner.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                    scanner.main()
                rows = json.loads(Path('marktplaats_proefresultaten.json').read_text())
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['price'], 1250)
                self.assertEqual(rows[0]['year'], 2011)
                self.assertEqual(rows[0]['mileage_km'], 160000)
                self.assertIsNone(rows[0]['estimated_profit'])
                self.assertFalse(rows[0]['not_rechecked_this_run'])
                self.assertEqual(json.loads(Path('marktplaats_koopkansen.json').read_text()), [])
            finally:
                os.chdir(old_cwd)

    def test_restriction_stops_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with patch.object(scanner, 'fetch', side_effect=HTTPError(URL, 429, 'Limited', {}, None)) as fetch, patch.object(scanner.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                    scanner.main()
                self.assertEqual(fetch.call_count, 1)
                status = json.loads(Path('marktplaats_status.json').read_text())
                self.assertEqual(status['scan_health'], 'blocked')
                self.assertEqual(status['blocked_http_status'], 429)
            finally:
                os.chdir(old_cwd)

    def test_rejected_or_removed_ad_is_evicted_from_cache(self):
        for response in ['expensive', 'removed', 'redirect']:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as tmp:
                old_cwd = os.getcwd()
                try:
                    os.chdir(tmp)
                    old = {'url': URL, 'title': 'Opel Corsa 2011', 'price': 1250}
                    for name in ['marktplaats_proefresultaten.json', 'marktplaats_vergelijkingen.json']:
                        Path(name).write_text(json.dumps([old]))
                    def fetch(url):
                        if '/q/' in url:
                            return '<article><a href="' + URL + '">Opel Corsa € 1250</a></article>', url, 200
                        if response == 'removed':
                            raise HTTPError(url, 404, 'Gone', {}, None)
                        return page(vehicle(price=15000)), OTHER if response == 'redirect' else url, 200
                    with patch.object(scanner, 'fetch', side_effect=fetch), patch.object(scanner.time, 'sleep') as sleep, contextlib.redirect_stdout(io.StringIO()):
                        scanner.main()
                    for name in ['marktplaats_proefresultaten.json', 'marktplaats_vergelijkingen.json']:
                        self.assertEqual(json.loads(Path(name).read_text()), [])
                    # Rejections must also respect the detail request interval.
                    self.assertGreaterEqual(sleep.call_count, 19)
                finally:
                    os.chdir(old_cwd)


class PublicSearchPriceRegressions(unittest.TestCase):
    def _scan_with_structured_card(self, price_type, cents, card):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            try:
                os.chdir(tmp)
                structured = {'props': {'pageProps': {'searchRequestAndResponse': {
                    'listings': [{'vipUrl': URL, 'priceInfo': {
                        'priceType': price_type, 'priceCents': cents},
                        'sellerInformation': {'sellerName': 'Jan', 'sellerType': 'CONSUMER'},
                        'attributes': [{'key': 'constructionYear', 'value': '2011'},
                                       {'key': 'mileage', 'value': '160000'}]}]
                }}}}
                search = ('<script type="application/json">'+json.dumps(structured)+'</script>'
                          '<article><a href="'+URL+'">Opel Corsa</a>'+card+'</article>')
                listing = page(vehicle(vehicleModelDate=2011,
                    mileageFromOdometer={'value':160000,'unitCode':'KMT'},
                    description='Goed onderhouden auto. '*12))
                def fetch(url):
                    return (search if '/q/' in url else listing), url, 200
                with patch.object(scanner, 'fetch', side_effect=fetch), patch.object(scanner.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                    scanner.main()
                return (json.loads(Path('marktplaats_status.json').read_text()),
                        json.loads(Path('marktplaats_handmatig_controleren.json').read_text()))
            finally:
                os.chdir(previous)

    def test_ambiguous_card_uses_clean_anchor_only_for_fixed_asking_prices(self):
        for kind in ('FIXED', 'MIN_BID'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                previous = os.getcwd()
                try:
                    os.chdir(tmp)
                    structured = {'props': {'pageProps': {'searchRequestAndResponse': {
                        'listings': [{'vipUrl': URL,
                                      'priceInfo': {'priceType': kind, 'priceCents': None},
                                      'sellerInformation': {'sellerType': 'CONSUMER',
                                                            'sellerName': 'Jan'},
                                      'attributes': []}]}}}}
                    search = ('<script type="application/json">'
                              +json.dumps(structured)+'</script>'
                              +'<article><a href="'+URL+'">Opel Corsa € 1.250</a>'
                              +'<span>Extra velgen € 200</span></article>')
                    listing = page(vehicle(vehicleModelDate=2011,
                        mileageFromOdometer={'value':160000,'unitCode':'KMT'},
                        description='Goed onderhouden auto. '*12))
                    def fetch(url):
                        return (search if '/q/' in url else listing), url, 200
                    with patch.object(scanner,'fetch',side_effect=fetch), \
                            patch.object(scanner.time,'sleep'), \
                            contextlib.redirect_stdout(io.StringIO()):
                        scanner.main()
                    status = json.loads(Path('marktplaats_status.json').read_text())
                    review = json.loads(Path('marktplaats_handmatig_controleren.json').read_text())
                    if kind == 'FIXED':
                        self.assertEqual(status['price_diagnostics']['price_from_anchor_fallback'], 1)
                        self.assertEqual(review[0]['price'], 1250)
                    else:
                        self.assertEqual(status['price_diagnostics']['price_from_anchor_fallback'], 0)
                        self.assertFalse(review)
                finally:
                    os.chdir(previous)

    def test_partial_fixed_price_record_preserves_unambiguous_card_price(self):
        status, review = self._scan_with_structured_card('FIXED', None, '€ 1.250,-')
        self.assertGreater(status['search_cards_budget_eligible'], 0)
        self.assertEqual(status['search_cards_missing_fixed_price'], 0)
        self.assertEqual(review[0]['price'], 1250)

    def test_bid_amount_never_becomes_fixed_asking_price(self):
        status, review = self._scan_with_structured_card('MIN_BID', 125000, '€ 1.250,-')
        self.assertEqual(status['search_cards_budget_eligible'], 0)
        self.assertEqual(status['search_cards_missing_bid_or_request'], 1)
        self.assertEqual(review, [])

    def test_financing_mentioned_as_option_does_not_hide_fixed_sale(self):
        status, review = self._scan_with_structured_card('FIXED', 125000,
                                                        '€ 1.250,- Ook lease mogelijk.')
        self.assertEqual(status['search_cards_budget_eligible'], 1)
        self.assertEqual(len(review), 1)


class DeepMarktplaatsTests(unittest.TestCase):
    def test_full_description_scope_and_location(self):
        obj = vehicle(description='Korte samenvatting van de auto')
        html = (page(obj) + '<div class="Description-module-description"><div>'
                'Goed onderhouden.<br />LET OP: versnellingsbak maakt een tikkend geluid.'
                '<ul><li>Airco</li></ul></div></div>'
                '<aside>Een andere auto is perfect</aside>'
                '<div class="SellerLocationSection-module-locationName">Almere</div>'
                '<p class="QualityMark-module-title">APK tot 8 november 2026</p>')
        data = scanner.extract_fields(scanner.parse(html), URL)
        self.assertEqual(data['description_source'], 'pagina_tekst')
        self.assertIn('tikkend geluid', data['description'])
        self.assertNotIn('andere auto', data['description'])
        self.assertEqual(data['location'], 'Almere')
        self.assertEqual(data['apk_text'], 'APK tot 8 november 2026')
        from marktplaats_market import technical_risk_signals
        self.assertIn('versnellingsbakgeluid', technical_risk_signals(data))

    def test_offer_url_binds_full_description(self):
        obj = vehicle()
        obj.pop('url')
        obj['offers']['url'] = OTHER
        html = page(obj) + '<div class="Description-module-description">Perfecte auto, lange beschrijving</div>'
        self.assertIsNone(scanner.extract_fields(scanner.parse(html), URL)['price'])
        self.assertNotEqual(scanner.extract_fields(scanner.parse(html), URL)['description_source'], 'pagina_tekst')

    def test_business_signature_overrides_schema_person(self):
        obj = vehicle(seller={'@type': 'Person', 'name': 'Jan'})
        html = page(obj) + '<div class="Description-module-description">Mooie auto.<br>Autobedrijf Voorbeeld<br>Inkoop en verkoop</div>'
        self.assertEqual(scanner.extract_fields(scanner.parse(html), URL)['seller_type'], 'business')

    def test_repaired_clutch_not_active_fault(self):
        from marktplaats_market import technical_risk_signals
        self.assertFalse(technical_risk_signals({'description': 'Koppeling vervangen in 2025, werkt goed.'}))
        self.assertIn('koppeling_defect', technical_risk_signals({'description': 'Koppeling matig'}))

    def test_market_references_are_auditable_and_deduplicated(self):
        from marktplaats_market import market_comparison
        from datetime import datetime, timezone
        candidate = {'url': URL, 'title': 'Opel Corsa 1.2 2011', 'year': 2011, 'mileage': 150000, 'price': 1200}
        rows = [{**candidate, 'url': f'https://www.marktplaats.nl/v/auto-s/opel/m{n}-corsa', 'price': 2500+n,
                 'checked_at': datetime.now(timezone.utc).isoformat(), 'price_type': 'FIXED', 'description_source': 'pagina_tekst',
                 'description': 'Goed onderhouden en correct werkende auto.'} for n in (456, 457, 458)]
        rows.append({**rows[0], 'url': rows[0]['url']+'-andere-titel'})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'references.json'
            path.write_text(json.dumps(rows))
            comp = market_comparison(candidate, path)
            self.assertEqual(comp['sample_size'], 3)
            self.assertEqual(len(comp['comparable_urls']), 3)
            self.assertEqual(comp['full_description_samples'], 3)
            rows[1]['description'] = 'Versnellingsbak maakt een tikkend geluid'
            path.write_text(json.dumps(rows))
            self.assertEqual(market_comparison(candidate, path)['sample_size'], 2)

    def test_expired_apk_not_positive_claim(self):
        details = scanner.marktplaats_checks({'title': 'Opel Corsa', 'apk_text': 'APK tot 7 juni 2000'})
        self.assertEqual(details['apk_status'], 'ongunstig')
        self.assertEqual(details['apk_expiry_date'], '2000-06-07')

    def test_archive_upgrade_never_bypasses_cooldown(self):
        from datetime import datetime, timezone
        old = {'url': URL, 'price': 1000, 'title': 'Opel Corsa 2011', 'year': 2011, 'mileage': 150000,
               'checked_at': datetime.now(timezone.utc).isoformat()}
        rating = {'indicative_margin_eur': 900, 'severe_technical_risk': False}
        with patch.object(scanner, 'market_comparison', return_value={}), patch.object(scanner, 'quality_gate', return_value=rating):
            now = datetime.now(timezone.utc).timestamp()
            self.assertEqual(scanner.archive_research_links([old], {}, {URL: now+3600}, now), [])
            self.assertEqual(scanner.archive_research_links([old], {}, {}, now), [URL])
            old['extraction_version'] = 3
            self.assertEqual(scanner.archive_research_links([old], {}, {}, now), [])


    def test_dealer_status_survives_rescoring(self):
        row = {'seller_type': 'business', 'seller_name': 'Example Cars'}
        row.update(scanner.seller_priority(row, row))
        self.assertEqual(row['seller_type'], 'handelaar')
        row.update(scanner.seller_priority(row, row))
        self.assertEqual(row['seller_type'], 'handelaar')
        self.assertFalse(row['seller_type_verified'])

class VariantRegressionTests(unittest.TestCase):
    def test_hidden_engine_and_robot_transmission(self):
        from marktplaats_market import identity
        self.assertEqual(identity({'title': 'Suzuki Swift 2005', 'description': 'Swift 1.3 benzine'})['engine_l'], '1.3')
        self.assertEqual(identity({'title': 'Toyota Aygo 1.0 MMT'})['transmission'], 'automaat')
        self.assertEqual(identity({'title': 'Fiat 500 Abarth'})['performance_variant'], 'abarth')

    def test_unknown_engine_must_not_mix_different_variants(self):
        from marktplaats_market import market_comparison
        from datetime import datetime, timezone
        item = {'url': 'https://www.marktplaats.nl/v/auto-s/fiat/m123-500', 'title': 'Fiat 500', 'price': 2000, 'year': 2010, 'mileage': 150000}
        rows = [{**item, 'url': f'https://www.marktplaats.nl/v/auto-s/fiat/m{n}-500',
                 'title': title, 'price': 4000, 'price_type': 'FIXED', 'checked_at': datetime.now(timezone.utc).isoformat()}
                for n, title in [(456, 'Fiat 500 1.2'), (457, 'Fiat 500 0.9 Twinair'), (458, 'Fiat 500 1.2')]]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'refs.json'
            path.write_text(json.dumps(rows))
            self.assertEqual(market_comparison(item, path)['status'], 'onvoldoende_variantgegevens')
            rows[1]['title'] = 'Fiat 500 1.4 Abarth'
            path.write_text(json.dumps(rows))
            self.assertEqual(market_comparison(item, path)['sample_size'], 2)

if __name__ == '__main__':
    unittest.main()
