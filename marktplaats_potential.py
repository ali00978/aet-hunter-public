"""Separate, broad Marktplaats research shortlist; no extra HTTP requests."""
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from car_triage import HIGH_RISK, vehicle_identity, estimated_trade_costs, extra_checks
from marktplaats_data import seller_evidence, vehicle_facts
from marktplaats_market import extra_repair_reserve, listing_id, technical_risk_signals, minimum_profit

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

def _profit_evidence(row, comparison, price):
    """Conservative scenario from local already-checked data; never guaranteed
    proceeds. No market requests, no fabricated sale prices or cost certainty."""
    sample = comparison.get('sample_size')
    median = comparison.get('median_asking_price_eur')
    if (row.get('price_type') != 'FIXED' or not _amount(price) or price <= 0
            or comparison.get('status') != 'indicatieve_vraagprijzen'
            or not isinstance(sample, int) or isinstance(sample, bool) or sample < 3
            or not _amount(median) or median <= 0
            or comparison.get('high_price_dispersion')):
        return None
    # Never assume the seller achieves the median asking price at resale.
    conservative_resale = round(median * .85)
    stored_resale = row.get('indicative_resale_eur')
    if _amount(stored_resale) and stored_resale > 0:
        conservative_resale = min(conservative_resale, stored_resale)
    details = {**extra_checks(row), **vehicle_facts(row)}
    if row.get('apk_status'):
        details['apk_status'] = row['apk_status']
    base_reserve, _ = estimated_trade_costs(row, details)
    airco_reserve, _ = extra_repair_reserve(row)
    apk_soon_reserve = 250 if details.get('apk_status') == 'verloopt_binnen_30_dagen' else 0
    derived_costs = base_reserve + airco_reserve + apk_soon_reserve
    stored_costs = row.get('estimated_costs_allowance_eur')
    if _amount(stored_costs):
        derived_costs = max(derived_costs, stored_costs)
    # At higher purchase prices A.E.T CARS needs more risk/reward than EUR 500.
    target = max(minimum_profit(), round(price * .30))
    margin = round(conservative_resale - price - derived_costs)
    # Research offers may never exceed the owner's EUR 3000 purchase budget.
    buy_limit = min(3000, max(0, round(conservative_resale - derived_costs - target)))
    return {'net_eur': margin, 'target_eur': round(target),
            'costs_eur': round(derived_costs), 'resale_eur': round(conservative_resale),
            'max_buy_eur': buy_limit}

def _candidate(row, now, max_price, diagnostics=None):
    from scanner import approved_listing_brand, over_purchase_mileage_limit, is_financing_offer
    def reject(reason):
        if diagnostics is not None:
            diagnostics[reason] = diagnostics.get(reason, 0) + 1
        return None
    if not isinstance(row, dict):
        return reject('invalid_or_excluded')
    url = row.get('url')
    if not approved_listing_brand(url or '') or row.get('reserved') or row.get('unavailable') or row.get('severe_technical_risk') or over_purchase_mileage_limit(row) or is_financing_offer(row) or row.get('buyer_queue') in ('uitsluiten', 'buiten_budget', 'reparatieproject'):
        return reject('invalid_or_excluded')
    search = row.get('source') == 'marktplaats_search_card'
    try:
        stamp = datetime.fromisoformat(str(row.get('discovered_at') if search else row.get('checked_at')).replace('Z','+00:00'))
    except (ValueError, TypeError, OverflowError):
        return reject('stale')
    if stamp.tzinfo is None or not timedelta(0) <= now - stamp <= timedelta(hours=24 if search else 48):
        return reject('stale')
    text = (str(row.get('title') or '') + '\n' + str(row.get('description') or '')).lower()
    if any(term in text for term in HIGH_RISK) or technical_risk_signals(row):
        return reject('technical_risk')
    if vehicle_facts(row).get('vehicle_data_warnings'):
        return reject('vehicle_warnings')
    seller = seller_evidence(row)['seller_type']
    if seller == 'handelaar':
        return reject('dealer')
    kind, price = row.get('price_type'), row.get('price')
    # A missing private-seller signal does NOT prove a business seller.
    # Keep a bounded fallback of complete, fixed-price vehicle detail pages
    # for manual research, never bid-only pages with unknown seller identity.
    # Dealer names/descriptions are still rejected by seller_evidence().
    if not search and seller == 'onbekend' and kind != 'FIXED':
        return reject('unverified_seller_bid')
    if search:
        if not _amount(price) or not 350 <= price <= max_price:
            return reject('price_ineligible')
        category, price_label = 'zoekkaart', f'€{price:,.0f} (onbevestigd)'
    elif kind == 'FIXED':
        if not _amount(price) or not 350 <= price <= max_price:
            return reject('price_ineligible')
        category, price_label = 'vaste_prijs', f'€{price:,.0f}'
    elif kind in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION'):
        floor = row.get('minimum_bid_eur')
        if floor is not None and (not _amount(floor) or floor > max_price):
            return reject('price_ineligible')
        category = 'prijs_opvragen'
        price_label = f'Bieden vanaf €{floor:,.0f}' if floor else 'Vraagprijs onbekend'
    else:
        return reject('price_ineligible')
    # Seller verification and mechanical repairs are independent dimensions:
    # an unknown private-seller classification is NOT a defect on the car.
    notes = []
    seller_review_required = not search and seller == 'onbekend'
    if seller_review_required:
        notes.append('Verkoperstype niet vastgesteld: controleer of het een particulier betreft voordat je contact opneemt')
    if not search:
        repair_notes = []
        reserve, _ = extra_repair_reserve(row)
        if reserve:
            repair_notes.append('Airco werkt mogelijk niet: diagnose en herstelkosten eerst controleren')
        if MINOR_RE.search(text):
            repair_notes.append('Cosmetisch/elektrisch herstelpunt: controleer omvang en kosten')
        if row.get('apk_status') == 'verloopt_binnen_30_dagen':
            repair_notes.append('APK loopt af: mogelijke herstelkosten controleren')
        if row.get('repair_review_required') and not repair_notes:
            repair_notes.append('Door scanner gemeld herstelpunt: inspectie nodig')
        if repair_notes:
            notes.extend(repair_notes)
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
    evidence = _profit_evidence(row, comparison, price) if compared and not search else None
    enough_details = (not search and kind == 'FIXED'
                      and row.get('description_source') in ('jsonld', 'pagina_tekst')
                      and len(str(row.get('description') or '')) >= 180
                      and vehicle_facts(row).get('year_detected') is not None
                      and vehicle_facts(row).get('mileage_km') is not None
                      and not row.get('not_rechecked_this_run'))
    # A superficial 15% asking-price discount often disappears after
    # resale haircut + transport + repairs. Only calculated scenarios may
    # enter the first tier; repairs and weak evidence remain manual research.
    # Three similar asking prices may all come from one seller or weak variants.
    # Keep the estimated margin visible for research, but reserve the strongest
    # label for the comparison engine's independently corroborated confidence.
    strong = (seller == 'particulier' and evidence is not None and enough_details
              and comparison.get('confidence') == 'goed_onderbouwde_vraagprijzen'
              and category == 'vaste_prijs'
              and evidence['net_eur'] >= evidence['target_eur'])
    tier = ('sterke_marge_na_controle' if strong else
            'marge_onvoldoende' if evidence is not None
            and evidence['net_eur'] < evidence['target_eur'] else
            'nadere_controle')
    if evidence is not None:
        notes.append(
            f"Rekenscenario: EUR {evidence['net_eur']} ruimte na kostenreserve "
            f"(vóór belasting), doel EUR {evidence['target_eur']}; "
            "geen bevestigde verkoopprijs of winst")
        if tier == 'marge_onvoldoende':
            notes.append(
                f"Vraagprijs boven berekende biedgrens EUR {evidence['max_buy_eur']}; "
                "alleen na lagere inkoopprijs mogelijk interessant")
    # Unverified but cost-supported candidates deserve review before
    # unknown-market entries; entries with nonpositive margin rank last.
    # None of these tiers is proof of achievable resale proceeds.
    # Private listings remain preferred. Unknown sellers are research only,
    # deliberately ranked after every confirmed non-business private offer;
    # an attractive price alone never promotes an unverified seller as a deal.
    priority = (0 if strong else
                1 if seller == 'particulier' and evidence is not None
                     and comparison.get('confidence') == 'goed_onderbouwde_vraagprijzen'
                     and evidence['net_eur'] >= evidence['target_eur'] else
                2 if seller == 'particulier' and evidence is not None
                     and evidence['net_eur'] > 0 else
                3 if seller == 'particulier' and evidence is None else
                4 if seller == 'particulier' else
                5 if evidence is None or evidence['net_eur'] > 0 else 6)
    rank = (priority, 0 if compared else 1,
            0 if kind == 'FIXED' and price <= 2000 else 1,
            0 if discount >= .15 else 1,
            -max(0,min(100,score)), -stamp.timestamp())
    if diagnostics is not None:
        diagnostics['eligible'] = diagnostics.get('eligible', 0) + 1
    return {**row, 'seller_type':seller, 'potential_category':category,
            'potential_category_label':CATEGORIES[category],
            'potential_display_price':price_label, 'potential_notes':notes,
            'potential_model_key':model_key, 'potential_sort':rank,
            'potential_trade_tier':tier,
            'potential_seller_review_required':seller_review_required,
            'potential_net_margin_eur':evidence['net_eur'] if evidence is not None else None,
            'potential_target_margin_eur':evidence['target_eur'] if evidence is not None else None,
            'potential_checked_at':stamp.isoformat(),
            'potential_fixed_asking_price':bool(kind=='FIXED' and not search),
            'potential_asking_price_eur':price if kind=='FIXED' and not search else None}

def select_potential_candidates(research, search_cards, max_price=3000, limit=30,
                                now=None, excluded_listing_ids=None,
                                diagnostics=None):
    """Rank fresh research before the 300-card cap hides unsent vehicles.

    Only the outbound delivery feed uses excluded IDs; the normal stored
    research snapshot still includes historic ads for comparison and review.
    No extra public searches and no historic offer is mislabelled as new.
    """
    now = now or datetime.now(timezone.utc)
    if diagnostics is not None:
        if not isinstance(diagnostics, dict):
            raise ValueError('Invalid potential research diagnostics')
        diagnostics.clear()
    if not _amount(max_price) or max_price < 350 or isinstance(limit,bool) or not isinstance(limit,int) or limit <= 0:
        return []
    limit = min(limit,300)
    excluded_listing_ids = set(excluded_listing_ids or ())
    raw_rows = list(research or []) + list(search_cards or [])
    if diagnostics is not None:
        diagnostics['source_records'] = len(raw_rows)
    unique = {}
    for raw in raw_rows:
        candidate = _candidate(raw,now,max_price,diagnostics)
        if candidate is None:
            continue
        key = listing_id(candidate['url'])
        if key in excluded_listing_ids:
            if diagnostics is not None:
                diagnostics['previously_delivered'] = diagnostics.get('previously_delivered',0)+1
            continue
        old = unique.get(key)
        if old is not None and diagnostics is not None:
            diagnostics['duplicate'] = diagnostics.get('duplicate', 0) + 1
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
    # Quotas retain a mix, but the final display should not bury the strongest
    # actual margin scenarios behind 18 ordinary fixed-price research cards.
    selected.sort(key=lambda row: row['potential_sort'])
    if diagnostics is not None:
        diagnostics['unique_after_filter'] = len(unique)
        diagnostics['selected'] = len(selected)
        diagnostics['selected_unknown_seller'] = sum(
            row['seller_type'] == 'onbekend' and row['potential_category'] != 'zoekkaart'
            for row in selected)
        diagnostics['selected_search_cards'] = sum(
            row['potential_category'] == 'zoekkaart' for row in selected)
    return [{**row,'potential_rank':i+1} for i,row in enumerate(selected[:limit])]
