"""Indexed, auditable comparisons used exclusively by the Marktplaats scanner."""
import json
import math
import re
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from car_triage import vehicle_identity, extra_checks, HIGH_RISK, assess
from marktplaats_data import vehicle_facts, seller_evidence

_CACHE = {}


def minimum_profit():
    try:
        value = float(os.environ.get('MARKTPLAATS_MIN_PROFIT_EUR', '500'))
        return value if math.isfinite(value) and 0 <= value <= 10000 else 500
    except (ValueError, TypeError, OverflowError):
        return 500


def extra_repair_reserve(item):
    text = str(item.get('title') or '') + '\n' + str(item.get('description') or '')
    if re.search(r'(?i)\b(?:airco|airconditioning)\s+(?:(?:is|werkt|functioneert|koelt)\s+)?(?:leeg|defect|kapot|onvoldoende|slecht)\b|\b(?:airco|airconditioning)\s+(?:werkt|functioneert|koelt|doet het)\s+niet\b', text):
        return 400, ['EUR 400 extra onderzoeks-/herstelreserve voor niet-werkende airco; oorzaak en werkelijke kosten onbekend']
    return 0, []


def technical_risk_signals(item):
    text = (str(item.get('title') or '') + '\n' + str(item.get('description') or '')).lower()
    patterns = {
        'versnellingsbakgeluid': r'versnellingsbak[^.!\n]{0,70}(?:tikk|ratel|gier|slip)|(?:tikk|ratel|gier)[^.!\n]{0,70}versnellingsbak',
        'remgeluid': r'(?:tijdens|bij)\s+(?:het\s+)?remmen[^.!\n]{0,65}(?:geluid|piep|schuur|tril)|remmen\s+(?:piepen|schuren|trillen)',
        'botsingschade': r'(?<!nooit )(?<!geen )\been\s+(?:botsing|aanrijding)[^.!\n]{0,60}gehad|bumper[^.!\n]{0,60}steekt[^.!\n]{0,20}uit',
        'stuur_of_fuseekogel_slijtage': r'(?:stuur[^.!\n]{0,20}kogel|fus+ee?kogel)[^.!\n]{0,45}(?:slijtage|speling|versleten)',
        'koppeling_defect': r'koppeling\s+(?:is\s+)?(?:matig|slecht|slipt|defect|versleten|kapot)',
        'onderdelen_of_project': r'(?:voor|alleen als)\s+(?:de\s+)?onderdelen|repareren is mij te duur|reparatie te duur',
        'motor_onregelmatig': r'motor[^.!\n]{0,25}(?:tikt|ratelt|rook|stottert)|loopt op (?:3|drie) cilinders',
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text)]


def quality_gate(item, base, details, comparison):
    from car_triage import quality_gate as original
    bid_only = item.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION')
    calculation = {**item, 'price': item.get('minimum_bid_eur') or 0} if bid_only else item
    result = original(calculation, assess(calculation) if bid_only else base, details, comparison)
    target = minimum_profit()
    result['target_net_profit_eur'] = result['target_profit_eur'] = target
    resale, costs = result.get('indicative_resale_eur'), result.get('estimated_costs_allowance_eur')
    if resale is not None and costs is not None:
        extra, warnings = extra_repair_reserve(item)
        apk_soon = details.get('apk_status') == 'verloopt_binnen_30_dagen'
        costs += extra + (250 if apk_soon else 0)
        if apk_soon:
            warnings.append('EUR 250 reserve voor APK die binnen 30 dagen verloopt; keuring en herstel nog controleren')
        breakdown = {'transport': 100, 'verkoop_en_advertentie': 50, 'voorbereiding_en_kleine_gebreken': 100}
        if details.get('apk_status') == 'ongunstig':
            breakdown['apk_en_mogelijk_herstel'] = 400
        elif details.get('apk_status') == 'onbekend':
            breakdown['apk_onzekerheid'] = 150
        if isinstance(details.get('mileage_km'), (int, float)) and details['mileage_km'] >= 250000:
            breakdown['slijtagebuffer'] = 150
        if apk_soon:
            breakdown['apk_binnen_30_dagen'] = 250
        remainder = costs - extra - sum(breakdown.values())
        if remainder > 0:
            breakdown['onderhoud_onzekerheid'] = remainder
        if extra:
            breakdown['airco_diagnose_en_herstelreserve'] = extra
        result['estimated_cost_breakdown_eur'] = breakdown
        result['estimated_costs_allowance_eur'] = costs
        result['repair_review_required'] = bool(extra or apk_soon)
        result['reasons'].extend(warnings)
        if apk_soon:
            result['actionable_lead'] = False
            result['missing_information'].append('APK verloopt binnen 30 dagen; keuring en eventuele reparaties controleren')
        if extra:
            result['actionable_lead'] = False
            result['missing_information'].append('Diagnose en kosten airco')
        result['indicative_margin_eur'] = None if bid_only else resale - calculation['price'] - costs
        result['max_purchase_price_eur'] = max(0, resale - costs - target)
        result['gross_margin_eur'] = None if bid_only else resale - calculation['price']
        result['net_margin_before_tax_eur'] = None if bid_only else resale - calculation['price'] - costs
        result['margin_basis'] = 'Schatting na kostenreserve, vóór belastingen en vaste bedrijfskosten'
        if result['net_margin_before_tax_eur'] is not None and result['net_margin_before_tax_eur'] < target:
            result['actionable_lead'] = False
        result['reasons'] = [r for r in result['reasons'] if 'minimaal EUR 500 winst' not in r]
        result['reasons'].append(f'Indicatieve verkoopprijs: 85% van vergelijkbare vraagprijzen; margedoel EUR {target:g} vóór belasting')
    from scanner import MAX_BUY_PRICE, MAX_PURCHASE_MILEAGE_KM, over_purchase_mileage_limit
    if result.get('max_purchase_price_eur') is not None:
        result['max_purchase_price_eur'] = min(MAX_BUY_PRICE, result['max_purchase_price_eur'])
        result['required_negotiation_eur'] = max(0, calculation['price'] - result['max_purchase_price_eur'])
        result['asking_price_within_buy_limit'] = calculation['price'] <= result['max_purchase_price_eur']
    if bid_only:
        result.update(actionable_lead=False, indicative_margin_eur=None, required_negotiation_eur=None,
                      asking_price_within_buy_limit=None, minimum_resale_before_costs_eur=None)
        result['reasons'].append('Geen vaste vraagprijs: biedlimiet is alleen een onderzoeksindicatie')
    # The purchase threshold is stricter than the mileage parser and price
    # archive: cars above 330k km can remain market references, never buys.
    # Conflicting cached odometers must not retain an actionable buy price.
    # Compare all available sources, not only vehicle_facts()' first reading.
    if over_purchase_mileage_limit(item):
        result.update(actionable_lead=False, max_purchase_price_eur=None,
                      indicative_margin_eur=None, net_margin_before_tax_eur=None,
                      gross_margin_eur=None, required_negotiation_eur=None,
                      asking_price_within_buy_limit=False,
                      purchase_mileage_over_limit=True)
        result['missing_information'].append('Tellerstand controleren: bron boven inkoopgrens')
        result['reasons'].append(
            f'Meer dan {MAX_PURCHASE_MILEAGE_KM:,} km volgens minstens één bron; geen inkoopadvies'.replace(',', '.'))
    signals = technical_risk_signals(item)
    if details.get('vehicle_data_warnings'):
        # A conflicting odometer/year makes the computed margin unreliable.
        # Do not leave a cached positive margin or negotiation amount visible
        # after removing the purchase recommendation.
        result.update(actionable_lead=False, max_purchase_price_eur=None,
                      indicative_margin_eur=None, net_margin_before_tax_eur=None,
                      gross_margin_eur=None, required_negotiation_eur=None,
                      asking_price_within_buy_limit=None)
        result['missing_information'] += details['vehicle_data_warnings']
    if signals:
        # Active mechanical faults have unknown repair costs. Do not retain
        # optimistic gross/net margins after suppressing the purchase advice.
        result.update(severe_technical_risk=True, actionable_lead=False,
                      priority='technisch_risico', score=min(20, result['score']),
                      max_purchase_price_eur=None, indicative_margin_eur=None,
                      net_margin_before_tax_eur=None, gross_margin_eur=None,
                      asking_price_within_buy_limit=None, required_negotiation_eur=None,
                      technical_risk_signals=signals)
        result['reasons'].append('Technisch risico uit volledige tekst: ' + ', '.join(signals))
    result['risk_category'] = ('hoog' if signals else 'nader_onderzoek' if details.get('vehicle_data_warnings') or result.get('repair_review_required') or result.get('purchase_mileage_over_limit') or comparison.get('status') != 'indicatieve_vraagprijzen' else 'normale_inspectie_nodig')
    return result

def identity(row):
    result = vehicle_identity(row)
    if re.fullmatch(r'[0-6][.,]\d', str(row.get('engine_l') or '')):
        result['engine_l'] = str(row['engine_l']).replace(',', '.')
    title = str(row.get('title') or '').lower()
    brand_match = re.search(r'/v/auto-s/([^/]+)/', str(row.get('url') or ''))
    brands = ('toyota', 'suzuki', 'kia', 'hyundai', 'renault', 'opel', 'peugeot', 'citroen', 'fiat', 'volkswagen', 'ford', 'skoda', 'seat', 'bmw', 'audi', 'mercedes-benz')
    result['brand'] = brand_match.group(1) if brand_match else next((b for b in brands if re.search(r'(?<!\w)'+re.escape(b)+r'(?!\w)', title)), None)
    # Models from the active search pool were missing from the shared legacy parser.
    extra_models = {'peugeot': ('307',), 'seat': ('leon',), 'toyota': ('corolla',),
                    'audi': ('a3', 'a4'), 'bmw': ('1 serie', '3 serie'),
                    'mercedes-benz': ('a klasse', 'b klasse')}
    normalized_title = re.sub(r'[-_]', ' ', title)
    for model in extra_models.get(result['brand'], ()):
        if re.search(r'(?<!\w)'+re.escape(model)+r'(?!\w)', normalized_title):
            result['model'] = model
            break
    if result['brand'] == 'fiat' and result['model'] == 'punto' and re.search(r'\bgrande\b', title):
        result['model'] = 'grande punto'
    engine = re.search(r'(?<![\d.])([0-6][.,]\d{1,2})(?!\d)', title)
    if engine and not re.fullmatch(r'[0-6][.,]\d', str(row.get('engine_l') or '')):
        result['engine_l'] = engine.group(1).replace(',', '.')
    body = str(row.get('body_type') or '').lower()
    if re.search(r'\b(cabrio|cabriolet|cc)\b', title) or 'cabrio' in body:
        result['body'] = 'cabrio'
    elif 'station' in body or 'wagon' in body:
        result['body'] = 'wagon'
    elif 'hatchback' in body:
        result['body'] = 'hatchback'
    elif 'sedan' in body:
        result['body'] = 'sedan'
    description = str(row.get('description') or '').lower()
    result['performance_variant'] = 'abarth' if 'abarth' in title else 'standard'
    if not result['engine_l']:
        match = re.search(r'(?<!\d)([0-3][.,]\d)\s*(?:liter|benzine|diesel|16v|vvt|tsi|tce)\b', description)
        if match:
            result['engine_l'] = match.group(1).replace(',', '.')
        else:
            cc = re.search(r'motorinhoud\s*:\s*(\d{3,4})\s*cc', description)
            if cc:
                result['engine_l'] = f'{round(int(cc.group(1))/1000, 1):.1f}'
    if re.search(r'\b(mmt|quickshift|easytronic|sensodrive)\b', title):
        result['transmission'] = 'automaat'
    if re.search(r'\bcombi\b', title):
        result['body'] = 'wagon'
    fuel = str(row.get('fuel_type') or '').lower()
    if fuel in ('benzine', 'diesel', 'elektrisch', 'hybride', 'lpg'):
        result['fuel'] = fuel
    trans = str(row.get('transmission') or '').lower()
    if 'handgeschakeld' in trans:
        result['transmission'] = 'handgeschakeld'
    elif 'automaat' in trans or 'automatic' in trans:
        result['transmission'] = 'automaat'
    return result


def listing_id(url):
    match = re.search(r'/([ma]\d+)(?:-|$)', str(url))
    return match.group(1) if match else str(url).split('?', 1)[0].rstrip('/')


def reference_index(path):
    from scanner import approved_listing_brand
    path = Path(path)
    try:
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        if key in _CACHE:
            return _CACHE[key]
        rows = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    index = {}
    for row in rows if isinstance(rows, list) else []:
        if (not isinstance(row, dict) or
                not approved_listing_brand(row.get('url'))):
            continue
        info = identity(row)
        if not info['model']:
            continue
        details = {**extra_checks(row), **vehicle_facts(row)}
        try:
            checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
            if checked.tzinfo is None:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        index.setdefault(info['model'], []).append((row, info, details, checked))
    _CACHE.clear()
    _CACHE[key] = index
    return index


def market_comparison(item, reference_path='marktplaats_vergelijkingen.json', allow_legacy=False):
    # Import lazily to keep the finance predicate identical to detail acceptance.
    from scanner import is_financing_offer
    ident = identity(item)
    model = ident['model']
    details = {**extra_checks(item), **vehicle_facts(item)}
    year, km = details['year_detected'], details['mileage_km']
    if details.get('vehicle_data_warnings'):
        return {'status': 'tegenstrijdige_voertuiggegevens', 'sample_size': 0,
                'reason': '; '.join(details['vehicle_data_warnings'])}
    if not model:
        return {'status': 'model_onbekend', 'sample_size': 0}
    if not isinstance(year, int) or not isinstance(km, (int, float)):
        return {'status': 'onvoldoende_voertuiggegevens', 'sample_size': 0, 'model': model}
    now = datetime.now(timezone.utc)
    matches = {}
    excluded_variant = excluded_stale = 0
    excluded = {'vehicle_data': 0, 'price_type_or_reserved': 0, 'price': 0, 'finance': 0, 'condition': 0, 'year_or_mileage': 0, 'brand': 0}
    for row, other, rd, checked in reference_index(reference_path).get(model, []):
        if rd.get('vehicle_data_warnings'):
            excluded['vehicle_data'] += 1
            continue
        if ident.get('brand') and other.get('brand') and ident['brand'] != other['brand']:
            excluded['brand'] += 1
            continue
        if checked < now - timedelta(days=21) or checked > now + timedelta(minutes=5):
            excluded_stale += 1
            continue
        if row.get('reserved') or (row.get('price_type') != 'FIXED' and not (allow_legacy and not row.get('price_type'))):
            excluded['price_type_or_reserved'] += 1
            continue
        price = row.get('price')
        # Bound the value before a float conversion: persisted JSON can contain
        # very large integers for which math.isfinite itself raises OverflowError.
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not 350 <= price <= 12000:
            excluded['price'] += 1
            continue
        if str(row.get('currency') or 'EUR').upper() != 'EUR' or is_financing_offer(row):
            excluded['finance'] += 1
            continue
        text = (str(row.get('title') or '') + ' ' + str(row.get('description') or '')).lower()
        if any(term in text for term in HIGH_RISK) or technical_risk_signals(row) or extra_repair_reserve(row)[0] or row.get('description_source') == 'meta_samenvatting':
            excluded['condition'] += 1
            continue
        if any(ident[k] and other[k] and ident[k] != other[k] for k in ('engine_l', 'fuel', 'body', 'transmission', 'performance_variant')):
            excluded_variant += 1
            continue
        y, k = rd['year_detected'], rd['mileage_km']
        if not isinstance(y, int) or abs(y-year) > 2 or not isinstance(k, (int, float)) or abs(k-km) > 50000:
            excluded['year_or_mileage'] += 1
            continue
        url = row.get('url')
        key = listing_id(url)
        if not url or key == listing_id(item.get('url')):
            continue
        candidate = (abs(y-year)*20000 + abs(k-km), price, url, checked, row, other)
        if key not in matches or checked > matches[key][3]:
            matches[key] = candidate
    # A dealership with many near-identical listings must not dominate
    # indicative asking-price statistics. Keep unnamed/private sellers distinct.
    closest = []
    per_dealer = {}
    excluded_same_dealer = 0
    for candidate in sorted(matches.values(), key=lambda x: (x[0], x[2])):
        row = candidate[4]
        name = str(row.get('seller_name') or '').casefold().strip()
        dealer_key = None
        if len(name) >= 4 and seller_evidence(row)['seller_type'] == 'handelaar':
            dealer_key = re.sub(r'[^\w]+', ' ', name).strip()
        if dealer_key and per_dealer.get(dealer_key, 0) >= 2:
            excluded_same_dealer += 1
            continue
        closest.append(candidate)
        if dealer_key:
            per_dealer[dealer_key] = per_dealer.get(dealer_key, 0) + 1
        if len(closest) >= 12:
            break
    outliers = []
    if len(closest) >= 5:
        centre = median(x[1] for x in closest)
        outliers = [x for x in closest if x[1] < centre * .55 or x[1] > centre * 1.8]
        closest = [x for x in closest if x not in outliers]
    base = {'sample_size': len(closest), 'model': model,
            'reference_pool_size': len(reference_index(reference_path).get(model, [])),
            'exclusions': excluded, 'confidence': 'onvoldoende',
            'comparable_urls': [x[2] for x in closest],
            'criteria': 'bouwjaar ±2; kilometerstand ±50.000; geen bekende variantconflicten',
            'excluded_variant_conflicts': excluded_variant, 'excluded_stale': excluded_stale,
            'excluded_same_dealer': excluded_same_dealer,
            'reference_named_seller_count': len({
                str(x[4].get('seller_name')).casefold().strip() for x in closest
                if x[4].get('seller_name')}),
            'excluded_price_outliers': len(outliers), 'outlier_urls': [x[2] for x in outliers]}
    if len(closest) < 3:
        return {**base, 'status': 'onvoldoende_vergelijkingen'}
    # Never infer a market value from unlike variants when the candidate
    # omits a distinguishing attribute (automatic vs manual, petrol vs diesel,
    # body style, or engine). Keep the ad visible for manual research instead.
    for field, label in (('engine_l', 'motor'), ('fuel', 'brandstof'),
                         ('transmission', 'transmissie'), ('body', 'carrosserie')):
        known_variants = {x[5].get(field) for x in closest if x[5].get(field)}
        if not ident.get(field) and len(known_variants) > 1:
            return {**base, 'status': 'onvoldoende_variantgegevens',
                    'reason': f'{label.capitalize()} onbekend; vergelijkingen bevatten verschillende uitvoeringen',
                    'ambiguous_variant_field': field}
    prices = sorted(x[1] for x in closest)
    spread = round(prices[-1]/prices[0], 2)
    strong = (len(closest) >= 5 and spread < 1.8
              and base['reference_named_seller_count'] >= 3
              and all(ident.get(k) and all(x[5].get(k) == ident[k]
                      for x in closest)
                      for k in ('engine_l', 'fuel', 'transmission')))
    return {**base, 'status': 'indicatieve_vraagprijzen', 'vehicle_identity': ident,
            'confidence': 'goed_onderbouwde_vraagprijzen' if strong else 'indicatief',
            'price_spread_ratio': spread, 'high_price_dispersion': spread >= 2.5,
            'median_asking_price_eur': round(median(prices)),
            'lowest_asking_price_eur': prices[0], 'highest_asking_price_eur': prices[-1],
            'full_description_samples': sum(x[4].get('description_source') == 'pagina_tekst' for x in closest),
            'verified_sale_prices': False}
