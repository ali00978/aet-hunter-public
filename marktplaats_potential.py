"""Separate, broad Marktplaats research shortlist; no extra HTTP requests."""
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from car_triage import HIGH_RISK, vehicle_identity
from marktplaats_data import seller_evidence, vehicle_facts
from marktplaats_market import extra_repair_reserve, listing_id, technical_risk_signals

CATEGORIES = {
    'vaste_prijs': 'Interessante vraagprijs',
    'klein_herstel': 'Herstelpunt / diagnose',
    'prijs_opvragen': 'Bieden / prijs opvragen',
    'zoekkaart': 'Zoekkaart controleren',
}
TARGET_MIX = (('vaste_prijs', 18), ('klein_herstel', 6),
              ('prijs_opvragen', 4), ('zoekkaart', 2))
MINOR_RE = re.compile(
    r'(?i)\b(?:lakschade|krassen|krasjes|deukjes?|kleine deuk|'
    r'bekleding beschadigd|radio werkt niet|raam werkt niet|'
    r'raammechanisme (?:defect|kapot)|centrale vergrendeling (?:defect|werkt niet))\b')

def _amount(n):
    return isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and 0 <= n <= 1_000_000

def _candidate(row, now, max_price):
    from scanner import approved_listing_brand, over_purchase_mileage_limit, is_financing_offer
    if not isinstance(row, dict):
        return None
    url = row.get('url')
    if not approved_listing_brand(url or '') or row.get('reserved') or row.get('unavailable') or row.get('severe_technical_risk') or over_purchase_mileage_limit(row) or is_financing_offer(row) or row.get('buyer_queue') in ('uitsluiten', 'buiten_budget', 'reparatieproject'):
        return None
    search = row.get('source') == 'marktplaats_search_card'
    try:
        stamp = datetime.fromisoformat(str(row.get('discovered_at') if search else row.get('checked_at')).replace('Z','+00:00'))
    except (ValueError, TypeError, OverflowError):
        return None
    if stamp.tzinfo is None or not timedelta(0) <= now - stamp <= timedelta(hours=24 if search else 48):
        return None
    text = (str(row.get('title') or '') + '\n' + str(row.get('description') or '')).lower()
    if any(term in text for term in HIGH_RISK) or technical_risk_signals(row):
        return None
    if vehicle_facts(row).get('vehicle_data_warnings'):
        return None
    seller = seller_evidence(row)['seller_type']
    if seller == 'handelaar' or (seller != 'particulier' and not search):
        return None
    kind, price = row.get('price_type'), row.get('price')
    if search:
        if not _amount(price) or not 350 <= price <= max_price:
            return None
        category, price_label = 'zoekkaart', f'€{price:,.0f} (onbevestigd)'
    elif kind == 'FIXED':
        if not _amount(price) or not 350 <= price <= max_price:
            return None
        category, price_label = 'vaste_prijs', f'€{price:,.0f}'
    elif kind in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION'):
        floor = row.get('minimum_bid_eur')
        if floor is not None and (not _amount(floor) or floor > max_price):
            return None
        category = 'prijs_opvragen'
        price_label = f'Bieden vanaf €{floor:,.0f}' if floor else 'Vraagprijs onbekend'
    else:
        return None
    notes = []
    if not search:
        reserve, _ = extra_repair_reserve(row)
        if reserve:
            notes.append('Airco werkt mogelijk niet: diagnose en herstelkosten eerst controleren')
        if MINOR_RE.search(text):
            notes.append('Cosmetisch/elektrisch herstelpunt: controleer omvang en kosten')
        if row.get('apk_status') == 'verloopt_binnen_30_dagen':
            notes.append('APK loopt af: mogelijke herstelkosten controleren')
        if row.get('repair_review_required') and not notes:
            notes.append('Door scanner gemeld herstelpunt: inspectie nodig')
        if notes:
            category = 'klein_herstel'
    comparison = row.get('market_comparison')
    comparison = comparison if isinstance(comparison, dict) else {}
    sample, median = comparison.get('sample_size'), comparison.get('median_asking_price_eur')
    compared = (not search and comparison.get('status') == 'indicatieve_vraagprijzen'
                and isinstance(sample,int) and not isinstance(sample,bool) and sample >= 3
                and _amount(median) and median > 0 and not comparison.get('high_price_dispersion'))
    if search:
        notes.append('Alleen zoekkaart: vraagprijs, verkoper en technische staat onbevestigd')
    elif kind != 'FIXED':
        notes.append('Biedadvertentie: definitieve vraagprijs onbekend')
    if not search and (row.get('description_source') not in ('jsonld','pagina_tekst') or len(str(row.get('description') or '')) < 180):
        notes.append('Volledige advertentiebeschrijving controleren')
    if not compared:
        notes.append('Marktwaarde en winst niet betrouwbaar vastgesteld')
    if row.get('not_rechecked_this_run'):
        notes.append('Niet opnieuw gecontroleerd tijdens laatste scan')
    try:
        score = int(row.get('score') or 0)
    except (TypeError, ValueError, OverflowError):
        score = 0
    ident = vehicle_identity(row)
    model_key = (ident.get('brand'),ident.get('model')) if ident.get('model') else (listing_id(url),None)
    discount = (median-price)/median if compared and kind == 'FIXED' else 0
    rank = (0 if discount >= .15 else 1, 0 if compared else 1,
            0 if kind == 'FIXED' and price <= 2000 else 1,
            -max(0,min(100,score)), -stamp.timestamp())
    return {**row, 'seller_type':seller, 'potential_category':category,
            'potential_category_label':CATEGORIES[category],
            'potential_display_price':price_label, 'potential_notes':notes,
            'potential_model_key':model_key, 'potential_sort':rank,
            'potential_checked_at':stamp.isoformat(),
            'potential_fixed_asking_price':bool(kind=='FIXED' and not search),
            'potential_asking_price_eur':price if kind=='FIXED' and not search else None}

def select_potential_candidates(research, search_cards, max_price=3000, limit=30, now=None):
    """Pick up to 30 unique recent research cars; soft category quotas."""
    now = now or datetime.now(timezone.utc)
    if not _amount(max_price) or max_price < 350 or isinstance(limit,bool) or not isinstance(limit,int) or limit <= 0:
        return []
    limit = min(limit,100)
    unique = {}
    for raw in list(research or []) + list(search_cards or []):
        candidate = _candidate(raw,now,max_price)
        if candidate is None:
            continue
        key = listing_id(candidate['url'])
        old = unique.get(key)
        # A full detail beats a search hint for the same advert ID.
        if old is None or (old['potential_category']=='zoekkaart' and candidate['potential_category']!='zoekkaart') or (
                (old['potential_category']=='zoekkaart') == (candidate['potential_category']=='zoekkaart')
                and candidate['potential_checked_at']>old['potential_checked_at']):
            unique[key] = candidate
    buckets = {name:[] for name in CATEGORIES}
    for row in unique.values():
        buckets[row['potential_category']].append(row)
    for rows in buckets.values():
        rows.sort(key=lambda r:r['potential_sort'])
    selected, used, models = [], set(), Counter()
    def fill(category, cap, quota=None):
        for row in buckets[category]:
            if len(selected)>=limit or (quota is not None and sum(x['potential_category']==category for x in selected)>=quota):
                break
            key=listing_id(row['url'])
            model=row['potential_model_key']
            if key in used or models[model]>=cap:
                continue
            used.add(key)
            models[model]+=1
            selected.append(row)
    quotas={category:max(1,round(limit*target/30))
            for category,target in TARGET_MIX}
    # Try model diversity first. Then honour each category's quota even when
    # most ads are of the same model; otherwise fixed-price rows can fill
    # all 30 and silently crowd out repair or price-request research.
    for category,_ in TARGET_MIX:
        fill(category,3,quotas[category])
    for category,_ in TARGET_MIX:
        fill(category,limit,quotas[category])
    for cap in (3,6,limit):
        for category,_ in TARGET_MIX:
            fill(category,cap)
    return [{**row,'potential_rank':i+1} for i,row in enumerate(selected[:limit])]
