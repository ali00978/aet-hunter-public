"""Public Marktplaats page facts: no private endpoints or executable scripts."""
import json
import re
import math
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin

BUSINESS_NAME = re.compile(r"(?i)(?:handels?onderneming|handelsbedrijf|trading|\bgarage\b|\.\s*(?:com|nl|be)\b|autobedrijf|autohandel|automotive|autoservice|garagebedrijf|occasion|car\s*service|(?:^|\W)auto(?:['’]?s)?(?:\W|$)|cars(?:\W|$)|carcenter|carcentre|(?:^|\W)b\.?v\.?(?:\W|$))")


def parse_mileage(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        # Bound integers before any float conversion: corrupt persisted values
        # may contain thousands of digits and must not crash the scanner.
        return value if 0 <= value <= 1000000 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and 0 <= value <= 1000000 and value.is_integer() else None
    text = re.sub(r'(?i)\s*(?:kilometers?|km)\s*$', '', str(value).strip())
    if re.fullmatch(r'\d{1,3}(?:[.,]\d{1,2})?\s*[kK]', text):
        return parse_mileage(float(text[:-1].strip().replace(',', '.')) * 1000)
    if not re.fullmatch(r'\d{1,7}|\d{1,3}(?:[.,\s]\d{3})+', text):
        return None
    return parse_mileage(int(re.sub(r'\D', '', text)))


def vehicle_facts(item):
    stored_warnings = item.get('vehicle_data_conflicts')
    warnings = ([w for w in stored_warnings if isinstance(w, str) and w.strip()]
                if isinstance(stored_warnings, (list, tuple)) else
                [stored_warnings] if isinstance(stored_warnings, str) and stored_warnings.strip() else [])
    # A lower cached "mileage" value must not silently overrule another valid
    # odometer field. Allow minor display/rounding differences, but never
    # price or recommend a car against materially contradictory readings.
    first_km = parse_mileage(item.get('mileage'))
    second_km = parse_mileage(item.get('mileage_km'))
    if (first_km is not None and second_km is not None and
            abs(first_km - second_km) > max(1000, round(max(first_km, second_km) * 0.01))):
        warnings.append('Tegenstrijdige opgeslagen kilometerstanden; tellerstand controleren')
    raw = item.get('mileage') if item.get('mileage') is not None else item.get('mileage_km')
    km = parse_mileage(raw)
    if raw is not None and km is None:
        warnings.append('Ongeldige kilometerstand in advertentiegegevens')
    if raw is None:
        text = str(item.get('title') or '') + '\n' + str(item.get('description') or '')
        labeled = {parse_mileage(m.group(1)) for m in re.finditer(r'(?i)\b(?:kilometerstand|km[ -]?stand)\s*[:=]?\s*(\d{1,3}(?:[.,\s]\d{3})+|\d{4,7})', text)}
        labeled.discard(None)
        values = {parse_mileage(m.group(1)) for m in re.finditer(r'(?i)(?<![\d.,])(\d{1,3}(?:[.,\s]\d{3})+|\d{1,3}(?:[.,]\d{1,2})?k|\d{4,7})\s*(?:km|kilometers?)\b', text)}
        values.discard(None)
        if labeled:
            values = labeled
        if len(values) == 1:
            km = values.pop()
        elif len(values) > 1:
            warnings.append('Meerdere kilometerstanden in tekst; handmatig controleren')
    year = item.get('year')
    if year is None:
        years = set(re.findall(r'(?<!\d)(?:19\d{2}|20\d{2})(?!\d)', str(item.get('title') or '')))
        year = years.pop() if len(years) == 1 else None
    if isinstance(year, str) and year.isdigit():
        year = int(year)
    if isinstance(year, bool) or not isinstance(year, int) or not 1950 <= year <= datetime.now(timezone.utc).year + 1:
        if year is not None:
            warnings.append('Ongeldig bouwjaar')
        year = None
    if year is not None:
        # Only explicitly labelled conflicting model/build years count. Other
        # bare years in descriptions may be APK dates or maintenance history.
        text = str(item.get('title') or '') + '\n' + str(item.get('description') or '')
        labelled = {int(m.group(1)) for m in re.finditer(
            r'(?i)\b(?:bouwjaar|modeljaar)\s*[:=]?\s*((?:19|20)\d{2})\b', text)}
        if any(other != year for other in labelled):
            warnings.append('Tegenstrijdig expliciet bouwjaar in advertentietekst')
    if year is not None and datetime.now(timezone.utc).year-year >= 10 and km is not None and km < 5000:
        warnings.append('Onwaarschijnlijk lage kilometerstand voor deze leeftijd; tellerstand controleren')
    return {'mileage_km': km, 'year_detected': year, 'vehicle_data_warnings': warnings}


def price_from_cents(cents):
    """Only turn unambiguous public priceCents into euros, never a bid into an ask."""
    if isinstance(cents, str):
        raw = cents.strip()
        if not re.fullmatch(r'\d{1,11}', raw):
            return None
        cents = int(raw)
    if (isinstance(cents, bool) or not isinstance(cents, (int, float))
            or not 0 < cents <= 10_000_000_000):
        return None
    if isinstance(cents, float) and (not math.isfinite(cents) or not cents.is_integer()):
        return None
    return cents / 100


def seller_evidence(item):
    name = str(item.get('seller_name') or '').strip()
    explicit = str(item.get('seller_platform_type') or item.get('seller_type') or '').lower()
    reasons = []
    if explicit in ('trader', 'business', 'dealer', 'professional', 'company', 'zakelijk', 'handelaar'):
        reasons.append('Zakelijke verkoper volgens advertentiegegevens')
    if BUSINESS_NAME.search(name):
        reasons.append('Bedrijfsnaam: ' + name)
    description = str(item.get('description') or '')
    if re.search(r'(?im)^\s*(?:autobedrijf|autohandel|garagebedrijf)\s+\S', description):
        reasons.append('Bedrijfssignatuur in advertentie')
    if re.search(r'(?i)onze\s+(?:voorraad|occasions)|wij\s+(?:verkopen|leveren)\s+(?:alle\s+)?auto', description):
        reasons.append('Zakelijke verkooptekst')
    if re.search(r'(?i)(?:door|via|uit)\s+inruil\s+verkregen|inruilauto\s+voor\s+(?:de\s+)?handel', description):
        reasons.append('Inruilvoorraad volgens verkooptekst')
    kind = 'handelaar' if reasons else 'particulier' if explicit in ('consumer', 'private', 'individual', 'particulier') else 'onbekend'
    return {'seller_type': kind, 'seller_classification_reasons': reasons or ['Particulier volgens advertentiegegevens' if kind == 'particulier' else 'Verkoperstype niet vastgesteld'],
            'seller_type_verified': False, 'seller_type_source': 'platform_en_verkopersnaam'}


def photo_urls(values, size='85'):
    photos, seen = [], set()
    for value in values if isinstance(values, list) else [values]:
        if isinstance(value, dict):
            value = value.get('contentUrl') or value.get('url')
        if not isinstance(value, str):
            continue
        try:
            value = urljoin('https://www.marktplaats.nl', value).replace('$_#', '$_'+size)
            parsed = urlsplit(value)
            valid_host = parsed.hostname in ('images.marktplaats.com', 'images.marktplaats.nl')
        except ValueError:
            continue
        if parsed.scheme != 'https' or not valid_host or not parsed.path.startswith('/api/v1/hz-mp-pro-listing/images/'):
            continue
        key = parsed.hostname + parsed.path
        if key not in seen:
            seen.add(key)
            photos.append(value)
    return photos[:50]


def public_listing_config(scripts, expected_id=None):
    for text in scripts:
        match = re.search(r'window\.__CONFIG__\s*=\s*', text)
        if not match:
            continue
        try:
            config, _ = json.JSONDecoder().raw_decode(text[match.end():])
            listing = config.get('listing', {})
            if isinstance(listing, dict) and listing.get('itemId') == expected_id:
                return listing
        except (ValueError, AttributeError):
            pass
    return {}


def search_page_records(scripts):
    records = {}
    for text in scripts:
        try:
            data = json.loads(text)
            rows = data['props']['pageProps']['searchRequestAndResponse']['listings']
        except (ValueError, KeyError, TypeError):
            continue
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            # Search JSON may attach tracking/query parameters to vipUrl,
            # while HTML anchors are normalized to bare listing paths.
            # Canonicalize both to the same exact key before joining prices.
            try:
                raw_url = urljoin('https://www.marktplaats.nl', str(row.get('vipUrl') or ''))
                parsed_url = urlsplit(raw_url)
                valid_host = parsed_url.netloc in ('www.marktplaats.nl', 'marktplaats.nl')
            except ValueError:
                continue
            if (parsed_url.scheme != 'https' or not valid_host or
                    not re.fullmatch(r'/v/auto-s/[^/]+/[ma][0-9]+(?:-[^/]*)?', parsed_url.path.rstrip('/'))):
                continue
            url = 'https://www.marktplaats.nl' + parsed_url.path.rstrip('/')
            seller = row.get('sellerInformation')
            seller = seller if isinstance(seller, dict) else {}
            info = row.get('priceInfo')
            info = info if isinstance(info, dict) else {}
            cents = info.get('priceCents')
            raw_attributes = row.get('attributes')
            raw_attributes = raw_attributes if isinstance(raw_attributes, list) else []
            # Optional public attributes are not always well formed. Ignore
            # invalid/unhashable keys instead of losing every listing on page.
            attrs = {a['key']: a.get('value') for a in raw_attributes
                     if isinstance(a, dict) and isinstance(a.get('key'), str)}
            pictures = row.get('pictures')
            pictures = pictures if isinstance(pictures, list) else []
            image_urls = row.get('imageUrls')
            image_urls = image_urls if isinstance(image_urls, list) else []
            euros = price_from_cents(cents)
            record = {'url': url, 'title': row.get('title'), 'seller_name': seller.get('sellerName'),
                      'seller_platform_type': seller.get('sellerType'),
                      'price_type': info.get('priceType'),
                      'reserved': row.get('reserved') is True or row.get('isReserved') is True,
                      'price': euros if info.get('priceType') == 'FIXED' else None,
                      'minimum_bid_eur': euros if info.get('priceType') == 'MIN_BID' else None,
                      'year': attrs.get('constructionYear'), 'mileage_km': attrs.get('mileage'),
                      'image_urls': photo_urls([p.get('extraExtraLargeUrl') or p.get('largeUrl') for p in pictures if isinstance(p,dict)] or image_urls)}
            record.update(seller_evidence(record))
            records[url] = record
    return records
